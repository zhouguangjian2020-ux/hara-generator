# scripts/stages/stage_merge.py
# S1 决策合并与验证：将规则预判结果与 Agent 推理结果合并，输出完整的 s1_decisions.json

import json
from pathlib import Path
from typing import Any


class S1ValidationError(ValueError):
    """S1 Agent 输出无法与 intermediate 严格对齐。"""


_AGENT_SCOPE_DISPOSITIONS = frozenset({"analyze", "transfer", "exclude"})


def _validate_agent_scope_metadata(sub: dict[str, Any], prefix: str, errors: list[str]) -> None:
    """校验 Agent 对 pending 子功能提出的可选域范围结论。

    已由 Domain Pack 锁定的 covered_by/transfer/exclude 永远不会交给 Agent。
    Agent 只可对 pending 项声明：当前域分析、受控移交或当前域排除。
    """
    disposition = sub.get("disposition")
    target_domain = sub.get("target_domain")
    reason_code = sub.get("reason_code")
    remark = sub.get("remark")
    is_hara = sub.get("is_hara")

    if disposition is None:
        if target_domain is not None:
            errors.append(f"{prefix}: 未声明 disposition 时不得填写 target_domain")
        if reason_code is not None:
            errors.append(f"{prefix}: 未声明 disposition 时不得填写 reason_code")
        return

    if not isinstance(disposition, str) or disposition not in _AGENT_SCOPE_DISPOSITIONS:
        errors.append(
            f"{prefix}: disposition 必须为 {sorted(_AGENT_SCOPE_DISPOSITIONS)} 之一，实际为 {disposition!r}"
        )
        return

    if not isinstance(reason_code, str) or not reason_code.strip():
        errors.append(f"{prefix}: 声明 disposition 时必须提供非空 reason_code")
    if not isinstance(remark, str) or not remark.strip() or remark.strip() == "/":
        errors.append(f"{prefix}: 声明 disposition 时必须提供可读的 remark")

    if disposition == "analyze":
        if is_hara is not True:
            errors.append(f"{prefix}: disposition=analyze 时 is_hara 必须为 true")
        if target_domain is not None:
            errors.append(f"{prefix}: disposition=analyze 时不得填写 target_domain")
    elif disposition == "transfer":
        if is_hara is not False:
            errors.append(f"{prefix}: disposition=transfer 时 is_hara 必须为 false")
        if not isinstance(target_domain, str) or not target_domain.strip():
            errors.append(f"{prefix}: disposition=transfer 时必须提供非空 target_domain")
    elif disposition == "exclude":
        if is_hara is not False:
            errors.append(f"{prefix}: disposition=exclude 时 is_hara 必须为 false")
        if target_domain is not None:
            errors.append(f"{prefix}: disposition=exclude 时不得填写 target_domain")


def _load_json(path: str, label: str) -> dict:
    """加载 JSON 文件，含错误提示。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: {label} 文件不存在: {path}")
        raise
    except json.JSONDecodeError as e:
        print(f"错误: {label} JSON 格式无效: {e}")
        raise
    if not isinstance(data, dict):
        raise ValueError(f"错误: {label} 顶层必须是 JSON 对象")
    return data


def _validate_agent_s1_data(data: Any) -> list[str]:
    """验证 Agent S1 JSON 的基础结构，不依赖 intermediate。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["Agent S1 顶层必须是 JSON 对象"]
    if "decisions" not in data:
        return ["缺少顶层 'decisions' 字段"]
    if not isinstance(data["decisions"], list):
        return ["顶层 'decisions' 必须是列表"]

    for i, decision in enumerate(data["decisions"]):
        prefix = f"decisions[{i}]"
        if not isinstance(decision, dict):
            errors.append(f"{prefix}: 必须是对象")
            continue
        if ("func_id" not in decision
                or not isinstance(decision["func_id"], str)
                or not decision["func_id"].strip()):
            errors.append(f"{prefix}: 'func_id' 缺失或为空")
        if "sub_functions" not in decision:
            errors.append(f"{prefix}: 缺少 'sub_functions'")
            continue
        if not isinstance(decision["sub_functions"], list):
            errors.append(f"{prefix}: 'sub_functions' 必须是列表")
            continue
        if "reason" in decision and not isinstance(decision["reason"], str):
            errors.append(f"{prefix}: 'reason' 必须是字符串")
        for j, sub in enumerate(decision["sub_functions"]):
            sp = f"{prefix}.sub_functions[{j}]"
            if not isinstance(sub, dict):
                errors.append(f"{sp}: 必须是对象")
                continue
            if ("feature_list_id" not in sub
                    or not isinstance(sub["feature_list_id"], str)
                    or not sub["feature_list_id"].strip()):
                errors.append(f"{sp}: 'feature_list_id' 缺失或为空")
            if "is_hara" not in sub:
                errors.append(f"{sp}: 缺少 'is_hara'")
            elif not isinstance(sub["is_hara"], bool):
                errors.append(
                    f"{sp}: 'is_hara' 必须是布尔值 true/false，实际为 {sub['is_hara']!r}"
                )
            if "remark" in sub and not isinstance(sub["remark"], str):
                errors.append(f"{sp}: 'remark' 必须是字符串，实际为 {sub['remark']!r}")
            _validate_agent_scope_metadata(sub, sp, errors)
    return errors


def validate_agent_s1_against_intermediate(
    intermediate: dict, agent_decisions: dict
) -> list[str]:
    """严格校验 Agent S1 输出与 intermediate 的可编辑范围是否完全一致。

    规则：
      * intermediate 中 s1_rule_is_hara 非 null 的子功能是锁定项，Agent 不得提交；
      * Agent 只能提交有 pending 子功能的相关项；
      * 每个相关项的 Agent 子功能集合必须精确等于 pending 集合；
      * 不允许重复 func_id、重复 feature_list_id、未知功能或未知子功能；
      * 有 pending 时不允许缺失相关项或只提交部分 pending。
    """
    errors = _validate_agent_s1_data(agent_decisions)
    if errors:
        return errors

    if not isinstance(intermediate, dict):
        return ["intermediate 顶层必须是 JSON 对象"]
    related_items = intermediate.get("related_items")
    if not isinstance(related_items, list):
        return ["intermediate 缺少有效的 'related_items' 列表"]

    expected: dict[str, dict[str, Any]] = {}
    for i, item in enumerate(related_items):
        prefix = f"intermediate.related_items[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: 必须是对象")
            continue
        func_id = item.get("func_id")
        if not isinstance(func_id, str) or not func_id.strip():
            errors.append(f"{prefix}: 'func_id' 缺失或为空")
            continue
        if func_id in expected:
            errors.append(f"intermediate: 重复的 func_id '{func_id}'")
            continue

        sub_functions = item.get("sub_functions")
        if not isinstance(sub_functions, list):
            errors.append(f"{func_id}: 'sub_functions' 必须是列表")
            sub_functions = []
        all_ids: list[str] = []
        pending_ids: list[str] = []
        locked_ids: list[str] = []
        locked_decisions: dict[str, Any] = {}
        for j, sub in enumerate(sub_functions):
            sp = f"{func_id}.sub_functions[{j}]"
            if not isinstance(sub, dict):
                errors.append(f"{sp}: 必须是对象")
                continue
            fid = sub.get("feature_list_id")
            if not isinstance(fid, str) or not fid.strip():
                errors.append(f"{sp}: 'feature_list_id' 缺失或为空")
                continue
            if fid in all_ids:
                errors.append(f"{func_id}: intermediate 中重复的 feature_list_id '{fid}'")
                continue
            all_ids.append(fid)
            rule_value = sub.get("s1_rule_is_hara")
            if rule_value is None:
                pending_ids.append(fid)
            else:
                if not isinstance(rule_value, bool):
                    errors.append(
                        f"{func_id}/{fid}: 's1_rule_is_hara' 必须是 null 或布尔值"
                    )
                locked_ids.append(fid)
                locked_decisions[fid] = rule_value

        expected[func_id] = {
            "all": set(all_ids),
            "pending": set(pending_ids),
            "locked": set(locked_ids),
            "locked_decisions": locked_decisions,
            "has_pending": bool(pending_ids),
        }

    seen_funcs: set[str] = set()
    agent_by_func: dict[str, dict[str, Any]] = {}
    for i, decision in enumerate(agent_decisions["decisions"]):
        if not isinstance(decision, dict):
            continue  # 基础 schema 错误已在上面报告
        func_id = decision.get("func_id")
        if not isinstance(func_id, str) or not func_id.strip():
            continue
        if func_id in seen_funcs:
            errors.append(f"Agent: 重复提交 func_id '{func_id}'")
            continue
        seen_funcs.add(func_id)

        if func_id not in expected:
            errors.append(f"Agent: 提交了 intermediate 中不存在的 func_id '{func_id}'")
            continue
        contract = expected[func_id]
        if not contract["has_pending"]:
            errors.append(
                f"Agent: '{func_id}' 没有 pending 子功能，不得提交决策（规则锁定项不可修改）"
            )

        actual_ids: list[str] = []
        actual_set: set[str] = set()
        for j, sub in enumerate(decision.get("sub_functions", [])):
            if not isinstance(sub, dict):
                continue
            fid = sub.get("feature_list_id")
            if not isinstance(fid, str) or not fid.strip():
                continue
            if fid in actual_set:
                errors.append(f"Agent {func_id}: 重复提交 feature_list_id '{fid}'")
            actual_ids.append(fid)
            actual_set.add(fid)
            if fid not in contract["all"]:
                errors.append(
                    f"Agent {func_id}: 提交了 intermediate 中不存在的 feature_list_id '{fid}'"
                )
            elif fid in contract["locked"]:
                errors.append(
                    f"Agent {func_id}/{fid}: 该项已由 S1 规则锁定，Agent 不得修改"
                )

        missing = sorted(contract["pending"] - actual_set)
        extra = sorted(actual_set - contract["pending"])
        if missing:
            errors.append(
                f"Agent {func_id}: 缺少 pending 决策: {', '.join(missing)}"
            )
        if extra:
            errors.append(
                f"Agent {func_id}: 只能提交 pending 子功能，额外提交: {', '.join(extra)}"
            )
        agent_by_func[func_id] = decision

    missing_funcs = sorted(
        func_id for func_id, contract in expected.items()
        if contract["has_pending"] and func_id not in seen_funcs
    )
    for func_id in missing_funcs:
        pending = sorted(expected[func_id]["pending"])
        errors.append(
            f"Agent: 缺少相关项 '{func_id}' 的全部 pending 决策: {', '.join(pending)}"
        )

    return errors


def merge_decisions(intermediate_path: str, agent_s1_path: str,
                    output_path: str = "s1_decisions.json") -> dict:
    """严格合并 intermediate 中的规则结果与 Agent 的 pending 决策。"""
    intermediate = _load_json(intermediate_path, "intermediate")
    agent_decisions = _load_json(agent_s1_path, "Agent S1")

    errors = validate_agent_s1_against_intermediate(intermediate, agent_decisions)
    if errors:
        print("[S1] 合并阻断：Agent 输出未通过严格范围校验")
        for error in errors:
            print(f"  - {error}")
        raise S1ValidationError("; ".join(errors))

    agent_index: dict[str, dict[str, dict[str, Any]]] = {}
    agent_items: dict[str, dict[str, Any]] = {}
    for decision in agent_decisions["decisions"]:
        func_id = decision["func_id"]
        agent_items[func_id] = decision
        agent_index[func_id] = {
            sub["feature_list_id"]: {
                "is_hara": sub["is_hara"],
                "remark": sub.get("remark", "/"),
                "disposition": sub.get("disposition"),
                "target_domain": sub.get("target_domain"),
                "reason_code": sub.get("reason_code"),
            }
            for sub in decision["sub_functions"]
        }

    merged = {"decisions": []}
    for item in intermediate.get("related_items", []):
        func_id = item["func_id"]
        item_decision = {
            "func_id": func_id,
            "func_name": item.get("func_name", ""),
            "is_hara": False,
            "reason": item.get("s1_item_remark", "") if item.get("s1_excluded_by_rule") else "",
            "sub_functions": [],
        }

        for sub in item.get("sub_functions", []):
            fid = sub["feature_list_id"]
            rule_value = sub.get("s1_rule_is_hara")
            if rule_value is not None:
                # 锁定项完全采用脚本规则结果，不读取 Agent 值。
                locked_sub = {
                    "feature_list_id": fid,
                    "is_hara": rule_value,
                    "remark": sub.get("s1_rule_remark", "/"),
                    "source": sub.get("s1_rule_source", "rule"),
                }
                for field, intermediate_field in (
                    ("disposition", "s1_disposition"),
                    ("analysis_unit_id", "analysis_unit_id"),
                    ("target_domain", "target_domain"),
                    ("reason_code", "s1_reason_code"),
                ):
                    value = sub.get(intermediate_field)
                    if value is not None:
                        locked_sub[field] = value
                item_decision["sub_functions"].append(locked_sub)
            else:
                agent_sub = agent_index[func_id][fid]
                merged_sub = {
                    "feature_list_id": fid,
                    "is_hara": agent_sub["is_hara"],
                    "remark": agent_sub.get("remark", "/"),
                    "source": "agent",
                }
                for field in ("disposition", "target_domain", "reason_code"):
                    value = agent_sub.get(field)
                    if value is not None:
                        merged_sub[field] = value
                item_decision["sub_functions"].append(merged_sub)

        # 相关项级结果由最终子功能结果派生，避免 Agent 的子功能全为否时仍误标为是。
        item_decision["is_hara"] = any(
            sub["is_hara"] for sub in item_decision["sub_functions"]
        )
        agent_item = agent_items.get(func_id)
        if agent_item and agent_item.get("reason"):
            item_decision["reason"] = agent_item["reason"]
        merged["decisions"].append(item_decision)

    total_subs = sum(len(d["sub_functions"]) for d in merged["decisions"])
    hara_yes = sum(
        1 for d in merged["decisions"]
        for sub in d["sub_functions"] if sub["is_hara"]
    )
    merged["stats"] = {
        "total_sub_functions": total_subs,
        "hara_yes": hara_yes,
        "hara_no": total_subs - hara_yes,
        "warnings": [],
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"[S1] 已合并: {total_subs} 个子功能, {hara_yes} 个HARA=是")
    return merged


def validate_agent_s1(agent_s1_path: str, intermediate_path: str | None = None) -> list[str]:
    """验证 Agent S1 输出；传入 intermediate_path 时执行严格范围校验。"""
    data = _load_json(agent_s1_path, "Agent S1")
    if intermediate_path is None:
        return _validate_agent_s1_data(data)
    intermediate = _load_json(intermediate_path, "intermediate")
    return validate_agent_s1_against_intermediate(intermediate, data)


def run(args):
    """被 run_hara.py 调用的入口。校验失败返回非零退出码。"""
    try:
        errors = validate_agent_s1(args.agent_s1, args.intermediate)
        if errors:
            print("[S1] 验证失败:")
            for error in errors:
                print(f"  - {error}")
            raise SystemExit(1)
        if args.validate_only:
            print("[S1] 验证通过：Agent JSON 格式及 pending 范围正确")
            return
        merge_decisions(args.intermediate, args.agent_s1, args.output)
    except (S1ValidationError, ValueError) as error:
        print(f"[S1] 操作失败：{error}")
        raise SystemExit(1) from error
