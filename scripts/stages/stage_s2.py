# scripts/stages/stage_s2.py
# S2 失效模式：生成受控草稿、合并 Agent 复核，并验证最终决策。

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.hara_rules import validate_s2_decisions_against_s1, validate_final_s1_against_intermediate
from utils.domain_pack_generation import (
    compatible_s2_contracts,
    generate_s2_draft,
    known_s2_contracts,
    validate_compatible_s2_contract,
    validate_known_s2_contract,
)
from utils.compatible_baseline_contract import validate_s2_compatible_baselines


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
    print(f"[S2] 已保存: {output.resolve()}")


def _draft_payload(intermediate: dict[str, Any], s1: dict[str, Any]) -> dict[str, Any]:
    decisions, agent_required, compatible_review = generate_s2_draft(intermediate, s1)
    exact_count = len(known_s2_contracts(intermediate, s1))
    return {
        "decisions": decisions,
        "generation": {
            "source": "domain_pack",
            "artifact_kind": "s2_controlled_draft",
            "locked_decision_count": exact_count,
            "compatible_review_func_ids": compatible_review,
            "agent_required_func_ids": agent_required,
        },
    }


def generate(intermediate_path: str, s1_path: str, output_path: str) -> dict:
    intermediate = _load_json(intermediate_path, "intermediate")
    s1 = _load_json(s1_path, "s1_decisions")
    s1_errors = validate_final_s1_against_intermediate(s1, intermediate)
    if s1_errors:
        raise ValueError("最终 S1 合同无效：" + "; ".join(s1_errors))
    result = _draft_payload(intermediate, s1)
    _save_json(result, output_path)
    generation = result["generation"]
    print(
        f"[S2] 已生成受控草稿：{generation['locked_decision_count']} 个 exact 锁定项，"
        f"{len(generation['compatible_review_func_ids'])} 个 compatible 待确认项。"
    )
    if generation["compatible_review_func_ids"]:
        print("[S2] compatible 基线必须由 Agent 确认或登记差异: " + ", ".join(generation["compatible_review_func_ids"]))
    if generation["agent_required_func_ids"]:
        print("[S2] 以下相关项必须由 Agent 完整补充，不能套用当前域基线: " + ", ".join(generation["agent_required_func_ids"]))
    print("[S2] 草稿不是最终 s2_decisions.json；请执行 s2 compose。")
    return result


def _agent_decisions(agent: dict) -> dict[str, dict]:
    raw = agent.get("decisions")
    if not isinstance(raw, list):
        raise ValueError("Agent S2 输出缺少 decisions 数组")
    by_func: dict[str, dict] = {}
    for index, decision in enumerate(raw):
        if not isinstance(decision, dict):
            raise ValueError(f"Agent S2 decisions[{index}] 必须是对象")
        func_id = decision.get("func_id")
        if not isinstance(func_id, str) or not func_id:
            raise ValueError(f"Agent S2 decisions[{index}] 缺少 func_id")
        if func_id in by_func:
            raise ValueError(f"Agent S2 重复提交 func_id: {func_id}")
        by_func[func_id] = decision
    return by_func


def compose(
    intermediate_path: str,
    s1_path: str,
    draft_path: str,
    agent_path: str,
    output_path: str,
) -> dict:
    """将不可改写的 Pack 草稿与 Agent 的最小补充合成为最终 S2。"""
    intermediate = _load_json(intermediate_path, "intermediate")
    s1 = _load_json(s1_path, "s1_decisions")
    draft = _load_json(draft_path, "s2_draft")
    agent = _load_json(agent_path, "agent_s2")
    s1_errors = validate_final_s1_against_intermediate(s1, intermediate)
    if s1_errors:
        raise ValueError("最终 S1 合同无效：" + "; ".join(s1_errors))

    expected_draft = _draft_payload(intermediate, s1)
    if draft.get("generation", {}).get("artifact_kind") != "s2_controlled_draft":
        raise ValueError("s2 compose 只接受由 s2 generate 产生的 s2_controlled_draft")
    if draft.get("decisions") != expected_draft["decisions"]:
        raise ValueError("s2 草稿与当前 intermediate/S1/Domain Pack 不一致；请重新执行 s2 generate")

    exact = known_s2_contracts(intermediate, s1)
    compatible = compatible_s2_contracts(intermediate, s1)
    draft_by_func = {item["func_id"]: item for item in expected_draft["decisions"]}
    agent_by_func = _agent_decisions(agent)
    hara_func_ids = {
        item.get("func_id")
        for item in s1.get("decisions", [])
        if isinstance(item, dict) and item.get("is_hara") is True and isinstance(item.get("func_id"), str)
    }
    ordinary = hara_func_ids - set(exact) - set(compatible)
    expected_agent = ordinary | set(compatible)
    extras = sorted(set(agent_by_func) - expected_agent)
    if extras:
        raise ValueError("Agent S2 不得提交 exact 锁定项或未知功能: " + ", ".join(extras))
    missing = sorted(expected_agent - set(agent_by_func))
    if missing:
        raise ValueError("Agent S2 缺少必须补充/确认的功能: " + ", ".join(missing))

    final_decisions = [deepcopy(draft_by_func[func_id]) for func_id in exact]
    for func_id, contract in compatible.items():
        review = agent_by_func[func_id]
        if review.get("func_name") not in {None, contract["func_name"]}:
            raise ValueError(f"Agent S2 {func_id} 的 func_name 不匹配")
        final = deepcopy(draft_by_func[func_id])
        for field in ("selected_modes", "reason", "agent_review", "compatible_baseline_changes"):
            if field in review:
                final[field] = deepcopy(review[field])
        final_decisions.append(final)
    for func_id in sorted(ordinary):
        final_decisions.append(deepcopy(agent_by_func[func_id]))

    result = {
        "decisions": final_decisions,
        "composition": {
            "source": "s2_controlled_draft + agent_s2",
            "draft": str(Path(draft_path)),
            "agent_input": str(Path(agent_path)),
            "exact_locked_func_ids": sorted(exact),
            "compatible_review_func_ids": sorted(compatible),
            "agent_authored_func_ids": sorted(ordinary),
        },
    }
    errors = validate(s1, result, intermediate)
    if errors:
        raise ValueError("s2 compose 结果不合法：" + "; ".join(errors))
    _save_json(result, output_path)
    print("[S2] 已合成最终决策；exact 锁定项、compatible 确认项和 Agent 补充项均已校验。")
    return result


def validate(s1: dict, s2: dict, intermediate: dict | None) -> list[str]:
    errors = validate_final_s1_against_intermediate(s1, intermediate)
    errors.extend(validate_s2_decisions_against_s1(s1, s2, intermediate))
    errors.extend(validate_s2_compatible_baselines(s2.get("decisions", [])))
    if intermediate is not None:
        errors.extend(validate_known_s2_contract(intermediate, s1, s2))
        errors.extend(validate_compatible_s2_contract(intermediate, s1, s2))
    return errors


def run(args):
    subcmd = getattr(args, "s2_subcommand", None)
    if subcmd == "generate":
        try:
            generate(args.intermediate, args.s1_decisions, args.output)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S2] 生成失败: {error}")
            raise SystemExit(1) from error
        return
    if subcmd == "compose":
        try:
            compose(args.intermediate, args.s1_decisions, args.draft, args.agent_s2, args.output)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S2] 合成失败: {error}")
            raise SystemExit(1) from error
        return
    if subcmd == "validate":
        s1 = _load_json(args.s1_decisions, "s1_decisions")
        s2 = _load_json(args.s2_decisions, "s2_decisions")
        intermediate = _load_json(args.intermediate, "intermediate") if args.intermediate else None
        errors = validate(s1, s2, intermediate)
        if errors:
            print("[S2] 验证失败:")
            for error in errors:
                print(f"  - {error}")
            raise SystemExit(1)
        print("[S2] 验证通过：功能范围、exact 锁定项及 compatible 确认合同均正确")
        return
    print("用法: run_hara.py s2 generate <intermediate.json> <s1_decisions.json> [-o s2_draft.json]")
    print("      run_hara.py s2 compose <intermediate.json> <s1_decisions.json> <s2_draft.json> <agent_s2.json> [-o s2_decisions.json]")
    print("      run_hara.py s2 validate <s1_decisions.json> <s2_decisions.json> [--intermediate intermediate.json]")
