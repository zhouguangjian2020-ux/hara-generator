# scripts/stages/stage_s3.py
# S3 HAZOP：生成受控草稿、合并 Agent HAZOP，并验证最终追溯合同。

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.hara_rules import validate_s3_entries_against_s1_s2, validate_final_s1_against_intermediate
from utils.domain_pack_generation import (
    compatible_s2_contracts,
    generate_known_s3,
    known_s2_contracts,
    validate_compatible_s2_contract,
    validate_compatible_s3_scope_contract,
    validate_known_s2_contract,
    validate_known_s3_contract,
)
from utils.compatible_baseline_contract import validate_s2_compatible_baselines, validate_s3_compatible_baselines
from utils.domain_packs import canonical_domain_pack_code
from utils.project_ids import allocate_project_function_numbers


def _load_json(path: str, label: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"错误: {label} 文件不存在: {path}")
        raise
    except json.JSONDecodeError as error:
        print(f"错误: {label} JSON 格式无效: {error}")
        raise
    if not isinstance(data, dict):
        raise ValueError(f"错误: {label} 顶层必须是 JSON 对象")
    return data


def _save_json(data: dict, path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[S3] 已保存: {output.resolve()}")


def _s2_contract_errors(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any]) -> list[str]:
    from utils.hara_rules import validate_s2_decisions_against_s1

    errors = validate_s2_decisions_against_s1(s1, s2, intermediate)
    errors.extend(validate_s2_compatible_baselines(s2.get("decisions", [])))
    errors.extend(validate_known_s2_contract(intermediate, s1, s2))
    errors.extend(validate_compatible_s2_contract(intermediate, s1, s2))
    return errors


def _draft_payload(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any]) -> dict[str, Any]:
    entries, pending = generate_known_s3(intermediate, s1, s2)
    compatible = compatible_s2_contracts(intermediate, s1)
    compatible_ids = [func_id for func_id in pending if func_id in compatible]
    ordinary_ids = [func_id for func_id in pending if func_id not in compatible]
    domains = sorted({
        canonical_domain_pack_code(item.get("domain"))
        for item in intermediate.get("related_items", [])
        if isinstance(item, dict) and item.get("domain")
    })
    return {
        "domain": domains[0] if len(domains) == 1 else "MULTI",
        "entries": entries,
        "generation": {
            "source": "domain_pack",
            "artifact_kind": "s3_controlled_draft",
            "locked_entry_count": len(entries),
            "compatible_scope_review_func_ids": compatible_ids,
            "agent_required_func_ids": ordinary_ids,
        },
    }


def generate(intermediate_path: str, s1_path: str, s2_path: str, output_path: str) -> dict:
    intermediate = _load_json(intermediate_path, "intermediate")
    s1 = _load_json(s1_path, "s1_decisions")
    s2 = _load_json(s2_path, "s2_decisions")
    s1_errors = validate_final_s1_against_intermediate(s1, intermediate)
    s2_errors = _s2_contract_errors(intermediate, s1, s2)
    if s1_errors or s2_errors:
        raise ValueError("S1/S2 前置合同无效：" + "; ".join(s1_errors + s2_errors))
    result = _draft_payload(intermediate, s1, s2)
    _save_json(result, output_path)
    generation = result["generation"]
    print(f"[S3] 已生成受控草稿：{generation['locked_entry_count']} 条 exact 锁定 HAZOP entry。")
    if generation["compatible_scope_review_func_ids"]:
        print("[S3] 以下功能必须保留 compatible 范围追溯并由 Agent 完成 HAZOP: " + ", ".join(generation["compatible_scope_review_func_ids"]))
    if generation["agent_required_func_ids"]:
        print("[S3] 以下功能必须由 Agent 创建项目专属 HAZOP: " + ", ".join(generation["agent_required_func_ids"]))
    print("[S3] 草稿不是最终 s3_hazop.json；请执行 s3 compose。")
    return result


def _agent_entries(agent: dict) -> list[dict]:
    entries = agent.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Agent S3 输出缺少 entries 数组")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Agent S3 entries[{index}] 必须是对象")
        if not isinstance(entry.get("func_id"), str) or not entry["func_id"]:
            raise ValueError(f"Agent S3 entries[{index}] 缺少 func_id")
        anomalies = entry.get("anomalies")
        if isinstance(anomalies, list):
            for anomaly_index, anomaly in enumerate(anomalies):
                if isinstance(anomaly, dict) and "failure_id" in anomaly:
                    raise ValueError(
                        f"Agent S3 entries[{index}].anomalies[{anomaly_index}] 禁止提交 failure_id；"
                        "运行期 ID 必须由 s3 compose 在合并后统一生成"
                    )
    return entries


def _decorate_compatible_scope(entry: dict, contract: dict[str, Any], s2_decision: dict[str, Any]) -> dict:
    """S3 没有可靠的事件级 baseline 时，仍保留 compatible 的语义范围和 S2 确认记录。"""
    result = deepcopy(entry)
    result.update({
        "analysis_unit_id": contract["analysis_unit_id"],
        "evidence_status": "compatible",
        "agent_action": "review_compatible_scope",
        "compatible_scope": {
            "analysis_unit_id": contract["analysis_unit_id"],
            "baseline_selected_modes": list(contract["selected_modes"]),
            "s2_agent_review": deepcopy(s2_decision.get("agent_review")),
        },
    })
    return result


_DOMAIN_FAILURE_PREFIX = {"PT": "P", "BD": "B", "ET": "Info"}


def _failure_id_prefix_for_function(func_id: str, domain: str) -> str:
    """返回当前项目功能的运行期 failure_id 前缀，不读取案例来源项目 ID。"""
    if "_func_" in func_id:
        candidate = func_id.split("_func_", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Za-z]{0,7}", candidate):
            return candidate
    candidate = _DOMAIN_FAILURE_PREFIX.get(domain, domain)
    if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Za-z]{0,7}", candidate):
        return candidate
    raise ValueError(f"无法从功能 {func_id} / 域 {domain} 确定 failure_id 前缀")


def _anomaly_semantic_sort_key(
    entry: dict[str, Any],
    anomaly: dict[str, Any],
    mode_rank: dict[str, int],
) -> tuple[Any, ...]:
    """生成与 Agent 输出顺序无关的稳定语义排序键。"""
    semantic_payload = deepcopy(anomaly)
    semantic_payload.pop("failure_id", None)
    mode = str(entry.get("failure_mode") or "").strip()
    description = str(anomaly.get("description") or "").strip()
    return (
        mode_rank.get(mode, len(mode_rank)),
        mode,
        description,
        json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _assign_agent_failure_ids(
    entries: list[dict[str, Any]],
    s2: dict[str, Any],
    agent_func_ids: set[str],
    domain: str,
    project_func_ids: list[str] | None = None,
) -> dict[str, Any]:
    """合并完成后为 Agent anomaly 确定性生成 failure_id。

    Agent 对运行期 ID 没有写权限：只要 Agent 范围 anomaly 中出现 failure_id 字段，
    compose 必须失败。exact 锁定项不在 agent_func_ids 中，因此原有 ID 不受影响。
    """
    decisions = [
        item for item in s2.get("decisions", [])
        if isinstance(item, dict) and isinstance(item.get("func_id"), str) and item.get("func_id")
    ]
    function_number_source = project_func_ids or [item["func_id"] for item in decisions]
    function_numbers = allocate_project_function_numbers(function_number_source, width=4)
    entries_by_func: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        func_id = entry.get("func_id")
        if func_id in agent_func_ids:
            entries_by_func.setdefault(func_id, []).append(entry)

    assigned_count = 0
    assigned_by_func: dict[str, int] = {}
    for decision in decisions:
        func_id = decision["func_id"]
        if func_id not in agent_func_ids:
            continue
        refs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for entry in entries_by_func.get(func_id, []):
            anomalies = entry.get("anomalies")
            if not isinstance(anomalies, list):
                continue
            for anomaly in anomalies:
                if not isinstance(anomaly, dict):
                    continue
                if "failure_id" in anomaly:
                    raise ValueError(
                        f"功能 {func_id} 的 Agent anomaly 禁止提交 failure_id；"
                        "请删除该字段，由 s3 compose 统一生成"
                    )
                refs.append((entry, anomaly))
        if len(refs) > 99:
            raise ValueError(
                f"功能 {func_id} 合并后有 {len(refs)} 个异常，超过 failure_id 两位流水号容量 99；"
                "必须先完成语义分组/去重，禁止生成三位流水号"
            )

        mode_rank = {
            str(mode): index
            for index, mode in enumerate(decision.get("selected_modes", []))
        }
        refs.sort(key=lambda ref: _anomaly_semantic_sort_key(ref[0], ref[1], mode_rank))
        prefix = _failure_id_prefix_for_function(func_id, domain)
        function_number = function_numbers[func_id]
        for sequence, (_, anomaly) in enumerate(refs, start=1):
            anomaly["failure_id"] = f"{prefix}_MF_{function_number}_{sequence:02d}"
            assigned_count += 1
            assigned_by_func[func_id] = assigned_by_func.get(func_id, 0) + 1

    return {
        "strategy": "compose_strict_agent_id_forbidden_v3",
        "stable_sort": "s2_mode_order + anomaly_semantic_payload",
        "agent_failure_id_policy": "forbidden",
        "assigned_count": assigned_count,
        "assigned_by_func": assigned_by_func,
    }

def compose(
    intermediate_path: str,
    s1_path: str,
    s2_path: str,
    draft_path: str,
    agent_path: str,
    output_path: str,
) -> dict:
    """将 exact HAZOP 草稿与 Agent 的 compatible/项目专属 HAZOP 合成最终 S3。"""
    intermediate = _load_json(intermediate_path, "intermediate")
    s1 = _load_json(s1_path, "s1_decisions")
    s2 = _load_json(s2_path, "s2_decisions")
    draft = _load_json(draft_path, "s3_draft")
    agent = _load_json(agent_path, "agent_s3")
    s1_errors = validate_final_s1_against_intermediate(s1, intermediate)
    s2_errors = _s2_contract_errors(intermediate, s1, s2)
    if s1_errors or s2_errors:
        raise ValueError("S1/S2 前置合同无效：" + "; ".join(s1_errors + s2_errors))

    expected_draft = _draft_payload(intermediate, s1, s2)
    if draft.get("generation", {}).get("artifact_kind") != "s3_controlled_draft":
        raise ValueError("s3 compose 只接受由 s3 generate 产生的 s3_controlled_draft")
    if draft.get("domain") != expected_draft["domain"] or draft.get("entries") != expected_draft["entries"]:
        raise ValueError("s3 草稿与当前 intermediate/S1/S2/Domain Pack 不一致；请重新执行 s3 generate")

    exact = known_s2_contracts(intermediate, s1)
    compatible = compatible_s2_contracts(intermediate, s1)
    expected_hara = {
        item.get("func_id")
        for item in s1.get("decisions", [])
        if isinstance(item, dict) and item.get("is_hara") is True and isinstance(item.get("func_id"), str)
    }
    ordinary = expected_hara - set(exact) - set(compatible)
    agent_entries = _agent_entries(agent)
    illegal = sorted({entry["func_id"] for entry in agent_entries} - (set(compatible) | ordinary))
    if illegal:
        raise ValueError("Agent S3 不得提交 exact 锁定项或未知功能: " + ", ".join(illegal))

    s2_by_func = {
        item.get("func_id"): item
        for item in s2.get("decisions", [])
        if isinstance(item, dict) and isinstance(item.get("func_id"), str)
    }
    final_entries = [deepcopy(entry) for entry in expected_draft["entries"]]
    for entry in agent_entries:
        func_id = entry["func_id"]
        if func_id in compatible:
            final_entries.append(_decorate_compatible_scope(entry, compatible[func_id], s2_by_func[func_id]))
        else:
            final_entries.append(deepcopy(entry))

    failure_id_assignment = _assign_agent_failure_ids(
        final_entries,
        s2,
        {entry["func_id"] for entry in agent_entries},
        expected_draft["domain"],
        [
            item["func_id"]
            for item in s1.get("decisions", [])
            if isinstance(item, dict)
            and item.get("is_hara") is True
            and isinstance(item.get("func_id"), str)
        ],
    )

    result = {
        "domain": expected_draft["domain"],
        "entries": final_entries,
        "composition": {
            "source": "s3_controlled_draft + agent_s3",
            "draft": str(Path(draft_path)),
            "agent_input": str(Path(agent_path)),
            "exact_locked_func_ids": sorted(exact),
            "compatible_scope_review_func_ids": sorted(compatible),
            "agent_authored_func_ids": sorted(ordinary),
            "failure_id_assignment": failure_id_assignment,
        },
    }
    errors = validate(s1, s2, result, intermediate)
    if errors:
        raise ValueError("s3 compose 结果不合法：" + "; ".join(errors))
    _save_json(result, output_path)
    print("[S3] 已合成最终 HAZOP；exact 锁定项、compatible 范围追溯和 Agent 补充项均已校验。")
    return result


def validate(s1: dict, s2: dict, s3: dict, intermediate: dict | None) -> list[str]:
    errors = validate_final_s1_against_intermediate(s1, intermediate)
    errors.extend(validate_s2_compatible_baselines(s2.get("decisions", [])))
    errors.extend(validate_s3_entries_against_s1_s2(s1, s2, s3, intermediate))
    errors.extend(validate_s3_compatible_baselines(s3.get("entries", [])))
    if intermediate is not None:
        errors.extend(validate_known_s2_contract(intermediate, s1, s2))
        errors.extend(validate_compatible_s2_contract(intermediate, s1, s2))
        errors.extend(validate_compatible_s3_scope_contract(intermediate, s1, s2, s3))
        errors.extend(validate_known_s3_contract(intermediate, s1, s2, s3))
    return errors


def run(args):
    subcmd = getattr(args, "s3_subcommand", None)
    if subcmd == "generate":
        try:
            generate(args.intermediate, args.s1_decisions, args.s2_decisions, args.output)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S3] 生成失败: {error}")
            raise SystemExit(1) from error
        return
    if subcmd == "compose":
        try:
            compose(args.intermediate, args.s1_decisions, args.s2_decisions, args.draft, args.agent_s3, args.output)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S3] 合成失败: {error}")
            raise SystemExit(1) from error
        return
    if subcmd == "validate":
        s1 = _load_json(args.s1_decisions, "s1_decisions")
        s2 = _load_json(args.s2_decisions, "s2_decisions")
        s3 = _load_json(args.s3_hazop, "s3_hazop")
        intermediate = _load_json(args.intermediate, "intermediate") if args.intermediate else None
        errors = validate(s1, s2, s3, intermediate)
        if errors:
            print("[S3] 验证失败:")
            for error in errors:
                print(f"  - {error}")
            raise SystemExit(1)
        print("[S3] 验证通过：范围、模式、exact 锁定项及 compatible 追溯合同均正确")
        return
    print("用法: run_hara.py s3 generate <intermediate.json> <s1_decisions.json> <s2_decisions.json> [-o s3_draft.json]")
    print("      run_hara.py s3 compose <intermediate.json> <s1_decisions.json> <s2_decisions.json> <s3_draft.json> <agent_s3.json> [-o s3_hazop.json]")
    print("      run_hara.py s3 validate <s1_decisions.json> <s2_decisions.json> <s3_hazop.json> [--intermediate intermediate.json]")
