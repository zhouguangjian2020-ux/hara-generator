"""从 Domain Pack 生成已知功能的受控 S2/S3 产物。

未知或语义不完整的输入不会猜测，也不会套用 CS 基线；调用方应将其留给 Agent
按现有流程处理。这里的“已知”由 parse 写入的 domain_context 和 S1 最终结论共同决定。
"""

from __future__ import annotations

from typing import Any
import re

from .domain_packs import load_domain_pack
from .function_matcher import resolve_pt_function_semantics
from .project_ids import allocate_project_function_numbers


_NO_VEHICLE_LEVEL_HAZARD_TOKENS = (
    "无整车层面危害", "无整车危害", "不涉及", "无危害", "n/a",
)


def _associated_hara_value(hazard_text: str) -> str:
    """把无整车层面危害规范为“不涉及”，其余危害才关联 HARA。"""
    normalized = hazard_text.strip().lower()
    return "不涉及" if any(token in normalized for token in _NO_VEHICLE_LEVEL_HAZARD_TOKENS) else "是"


def _failure_id_prefix(pattern: dict[str, Any], domain: str) -> str:
    """沿用域命名风格，但不沿用来源项目的功能序号。"""
    source_id = pattern.get("failure_id")
    if isinstance(source_id, str) and "_MF_" in source_id:
        prefix = source_id.split("_MF_", 1)[0].strip()
        if prefix:
            return prefix
    return domain


def _intermediate_items(intermediate: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("func_id"): item
        for item in intermediate.get("related_items", [])
        if isinstance(item, dict) and isinstance(item.get("func_id"), str) and item.get("func_id")
    }


def _pt_cases(pack: dict[str, Any], function_family: str) -> list[dict[str, Any]]:
    """返回 PT 运行时唯一权威案例集合中的某个整车功能案例。"""
    catalog = pack.get("case_library_catalog", {})
    cases = catalog.get("cases", []) if isinstance(catalog, dict) else []
    return [
        case for case in cases
        if isinstance(case, dict)
        and (case.get("function_profile") or {}).get("canonical_function_family") == function_family
    ]


def _case_source_ref(case: dict[str, Any]) -> dict[str, Any]:
    refs = case.get("source_refs")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict):
                return ref
    return {}


def _case_set_id(case: dict[str, Any]) -> str:
    """从案例来源追溯取得稳定 case set 键，不使用项目运行期 ID。"""
    ref = _case_source_ref(case)
    return str(ref.get("source_project") or ref.get("source_file") or "PT_RUNTIME_CASE_SET").strip()


def _case_source_function_key(case: dict[str, Any]) -> str:
    return str((case.get("function_profile") or {}).get("canonical_function_family") or "").strip()


def _case_source_failure_id(case: dict[str, Any]) -> str:
    return str(_case_source_ref(case).get("raw_failure_id") or "").strip()


def _case_mode(case: dict[str, Any]) -> str:
    return str((case.get("failure_profile") or {}).get("canonical_mode") or "").strip()


def _case_anomaly(case: dict[str, Any]) -> str:
    profile = case.get("failure_profile") or {}
    aliases = profile.get("anomaly_aliases")
    if isinstance(aliases, list):
        for value in aliases:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(profile.get("anomaly_class") or "").strip()


def _case_hazard(case: dict[str, Any], hazard_catalog: dict[str, Any]) -> dict[str, Any] | None:
    profile = case.get("hazard_profile") or {}
    hazard_text = str(profile.get("vehicle_hazard_class") or "").strip()
    family_id = profile.get("hazard_family")
    for hazard_id, item in hazard_catalog.items():
        if not isinstance(item, dict):
            continue
        if hazard_text and str(item.get("vehicle_hazard") or "").strip() == hazard_text:
            return {
                "vehicle_hazard_id": hazard_id,
                "description": hazard_text,
                "hazard_family": family_id or item.get("hazard_family"),
                "associated_hara": _associated_hara_value(hazard_text),
            }
    if hazard_text and any(token in hazard_text.lower() for token in _NO_VEHICLE_LEVEL_HAZARD_TOKENS):
        return {
            "vehicle_hazard_id": "PT_HZ_NO_HAZARD",
            "description": hazard_text,
            "hazard_family": "pt_hazard_family_no_hazard",
            "associated_hara": "不涉及",
        }
    if family_id == "pt_hazard_family_no_hazard":
        no_hazard = hazard_catalog.get("PT_HZ_NO_HAZARD", {})
        return {
            "vehicle_hazard_id": "PT_HZ_NO_HAZARD",
            "description": str(no_hazard.get("vehicle_hazard") or "无整车层面危害"),
            "hazard_family": family_id,
            "associated_hara": "不涉及",
        }
    return None


def _source_hint_text(source_hint: dict[str, Any] | None) -> str:
    if not isinstance(source_hint, dict):
        return ""
    values = [source_hint.get("doc_name")]
    document = source_hint.get("source_document")
    if isinstance(document, dict):
        values.extend((document.get("original_name"), document.get("original_path")))
    return " ".join(str(value) for value in values if value)


def _pt_case_set_contract(
    pack: dict[str, Any], function_family: str, source_hint: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    cases = _pt_cases(pack, function_family)
    if not cases:
        return None
    by_set: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        case_set = _case_set_id(case)
        if case_set:
            by_set.setdefault(case_set, []).append(case)
    if not by_set:
        return None
    if len(by_set) > 1:
        hint_tokens = set(re.findall(r"[A-Za-z]+\d+", _source_hint_text(source_hint).upper()))
        if not hint_tokens:
            return None
        ranked = sorted(
            by_set.items(),
            key=lambda pair: (
                len(hint_tokens & set(re.findall(r"[A-Za-z]+\d+", pair[0].upper()))),
                len(pair[1]),
            ),
            reverse=True,
        )
        if not ranked or (len(ranked) > 1 and (
            len(hint_tokens & set(re.findall(r"[A-Za-z]+\d+", ranked[0][0].upper())))
            == len(hint_tokens & set(re.findall(r"[A-Za-z]+\d+", ranked[1][0].upper())))
        )):
            return None
        selected_set, selected_cases = ranked[0]
    else:
        selected_set, selected_cases = next(iter(by_set.items()))
    return {
        "case_set_id": selected_set,
        "source_function_key": function_family,
        "cases": selected_cases,
    }


def _group_pt_cases(cases: list[dict[str, Any]], selected_modes: list[str], hazard_catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """按 case_set/source_function/source_failure 分组，一个组只产生一个 failure。"""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for case in cases:
        mode = _case_mode(case)
        source_failure_id = _case_source_failure_id(case)
        if mode not in selected_modes or not source_failure_id:
            continue
        key = (_case_set_id(case), _case_source_function_key(case), source_failure_id)
        groups.setdefault(key, []).append(case)

    ordered = sorted(
        groups.items(),
        key=lambda pair: min(
            (int(_case_source_ref(case).get("source_row") or 10**9) for case in pair[1]),
            default=10**9,
        ),
    )
    result: list[dict[str, Any]] = []
    for key, members in ordered:
        modes = {_case_mode(case) for case in members}
        anomalies = {_case_anomaly(case) for case in members if _case_anomaly(case)}
        if len(modes) != 1 or len(anomalies) > 1:
            return [], "CASE_SET_FAILURE_GROUP_MISMATCH"
        hazard_map: dict[tuple[str, str], dict[str, Any]] = {}
        for case in members:
            hazard = _case_hazard(case, hazard_catalog)
            if hazard is None:
                return [], "CASE_SET_HAZARD_MISMATCH"
            hazard_map[(hazard["vehicle_hazard_id"], hazard["description"])] = hazard
        refs = []
        case_ids = []
        for case in sorted(members, key=lambda item: int(_case_source_ref(item).get("source_row") or 10**9)):
            case_ids.append(case.get("case_id"))
            refs.extend(case.get("source_refs") or [])
        result.append({
            "case_set_id": key[0],
            "source_function_key": key[1],
            "source_failure_id": key[2],
            "failure_mode": next(iter(modes)),
            "anomaly": next(iter(anomalies), ""),
            "hazards": list(hazard_map.values()),
            "case_ids": case_ids,
            "source_refs": refs,
            "source_order": min((int(_case_source_ref(case).get("source_row") or 10**9) for case in members), default=10**9),
        })
    return result, None


def _positive_feature_ids(s1_decision: dict[str, Any]) -> set[str]:
    return {
        item.get("feature_list_id")
        for item in s1_decision.get("sub_functions", [])
        if isinstance(item, dict) and item.get("is_hara") is True
        and isinstance(item.get("feature_list_id"), str) and item.get("feature_list_id")
    }


def _resolve_analysis_unit(
    intermediate: dict[str, Any], s1_decision: dict[str, Any], *, evidence_status: str,
) -> dict[str, str] | None:
    """解析一个由同一证据等级、同一 Pack 分析单元完整覆盖的 S1=是功能。

    不能只根据 func_id 或功能名称推断。所有最终 S1=是子功能都必须是
    ``covered_by/analyze``，证据等级一致，并且落在同一 ``analysis_unit_id``。
    """
    func_id = s1_decision.get("func_id")
    if not isinstance(func_id, str):
        return None
    item = _intermediate_items(intermediate).get(func_id)
    if not item:
        return None
    domain = item.get("domain")
    context = item.get("domain_context")
    if not isinstance(domain, str) or not isinstance(context, dict):
        return None

    positive_ids = _positive_feature_ids(s1_decision)
    if not positive_ids:
        return None
    by_feature = {
        feature.get("feature_list_id"): feature
        for feature in context.get("features", [])
        if isinstance(feature, dict) and isinstance(feature.get("feature_list_id"), str)
    }
    covered = [by_feature.get(feature_id) for feature_id in positive_ids]
    if any(
        feature is None
        or feature.get("disposition") not in {"covered_by", "analyze"}
        or feature.get("evidence_status") != evidence_status
        for feature in covered
    ):
        return None
    units = {feature.get("covered_by") for feature in covered if feature.get("covered_by")}
    if len(units) != 1:
        return None
    analysis_unit_id = next(iter(units))
    pack = load_domain_pack(domain)
    units_by_id = {
        unit.get("analysis_unit_id"): unit
        for unit in pack["analysis_catalog"].get("analysis_units", [])
        if isinstance(unit, dict)
    }
    if analysis_unit_id not in units_by_id:
        return None
    return {
        "domain": pack["domain"],
        "analysis_unit_id": analysis_unit_id,
        "func_id": func_id,
        "func_name": s1_decision.get("func_name", item.get("func_name", "")),
        "evidence_status": evidence_status,
    }



def apply_effective_pt_case_routes(intermediate: dict[str, Any]) -> dict[str, int]:
    """Project PT function-level exact routing into Agent-facing intermediate context.

    PT source-case coverage is selected by the unique vehicle-function case set, not by
    requiring every HARA-positive sub-function to resolve to a semantic role.  The
    latter remains useful diagnostic information, but it must not be exposed as an
    actionable ``unknown`` when the function-level exact contract is already locked.
    Raw feature diagnostics are preserved under ``raw_*`` fields.
    """
    related_items = intermediate.get("related_items", [])
    contexts = intermediate.get("domain_contexts", {})
    if not isinstance(related_items, list) or not isinstance(contexts, dict):
        return {"routed_count": 0, "eligible_count": 0}

    pt_items = [
        item for item in related_items
        if isinstance(item, dict)
        and item.get("domain") in {"P", "PT"}
        and any(sub.get("s1_rule_is_hara") is True for sub in item.get("sub_functions", []) if isinstance(sub, dict))
    ]
    routed_count = 0
    pt_context = contexts.get("PT")
    context_items = {
        item.get("func_id"): item
        for item in (pt_context or {}).get("related_items", [])
        if isinstance(item, dict) and isinstance(item.get("func_id"), str)
    } if isinstance(pt_context, dict) else {}

    for item in pt_items:
        decision = {
            "func_id": item.get("func_id"),
            "func_name": item.get("func_name", ""),
            "is_hara": True,
        }
        route = resolve_pt_function_level_analysis_unit(intermediate, decision)
        context_item = context_items.get(item.get("func_id"))
        if not route or not isinstance(context_item, dict):
            continue

        routed_count += 1
        context_item["raw_resolution_status"] = context_item.get("resolution_status")
        context_item["raw_unresolved_feature_count"] = context_item.get("unresolved_feature_count", 0)
        context_item["raw_ambiguous_feature_count"] = context_item.get("ambiguous_feature_count", 0)
        context_item["effective_routing_status"] = "exact_locked_by_function_case_set"
        context_item["case_set_id"] = route.get("case_set_id")
        context_item["source_function_key"] = route.get("source_function_key")
        context_item["analysis_unit_id"] = route.get("analysis_unit_id")
        context_item["resolution_status"] = "exact_known"
        context_item["unresolved_feature_count"] = 0
        context_item["ambiguous_feature_count"] = 0
        context_item["agent_action_required"] = False

        sub_by_id = {
            sub.get("feature_list_id"): sub
            for sub in item.get("sub_functions", [])
            if isinstance(sub, dict) and sub.get("feature_list_id")
        }
        for feature in context_item.get("features", []):
            if not isinstance(feature, dict):
                continue
            feature_id = feature.get("feature_list_id")
            sub = sub_by_id.get(feature_id, {})
            is_hara = sub.get("s1_rule_is_hara") is True
            feature["raw_evidence_status"] = feature.get("evidence_status")
            feature["raw_disposition"] = feature.get("disposition")
            feature["raw_covered_by"] = feature.get("covered_by")
            feature["raw_semantic_status"] = feature.get("semantic_status")
            feature["effective_routing_status"] = "exact_locked_by_function_case_set"
            feature["evidence_status"] = "exact_known"
            feature["effective_evidence_status"] = "exact_known"
            feature["disposition"] = "covered_by" if is_hara else "exclude"
            feature["covered_by"] = route.get("analysis_unit_id") if is_hara else None
            feature["effective_analysis_unit_id"] = route.get("analysis_unit_id") if is_hara else None
            feature["reason_code"] = "PT_FUNCTION_CASE_SET_EXACT"
            feature["agent_action_required"] = False

    # The aggregate PT context is exact for the HARA-positive functions only when
    # every such function has a unique, source-hinted case-set route.
    if isinstance(pt_context, dict) and pt_items and routed_count == len(pt_items):
        pt_context["raw_resolution_status"] = pt_context.get("resolution_status")
        pt_context["raw_unresolved_feature_count"] = pt_context.get("unresolved_feature_count", 0)
        pt_context["raw_ambiguous_feature_count"] = pt_context.get("ambiguous_feature_count", 0)
        pt_context["effective_routing_status"] = "exact_locked_by_function_case_set"
        pt_context["resolution_status"] = "exact_known"
        pt_context["unresolved_feature_count"] = 0
        pt_context["ambiguous_feature_count"] = 0
        pt_context["agent_action_required"] = False

    return {"routed_count": routed_count, "eligible_count": len(pt_items)}

def resolve_pt_function_level_analysis_unit(
    intermediate: dict[str, Any], s1_decision: dict[str, Any]
) -> dict[str, str] | None:
    """按唯一 PT 整车功能名称锁定整车功能级案例。

    子功能只负责 S1/HARA 范围和追溯；它们的语义覆盖状态不再阻断
    已由整车功能名称唯一命中的案例预填。
    """
    func_id = s1_decision.get("func_id")
    if not isinstance(func_id, str) or s1_decision.get("is_hara") is not True:
        return None
    item = _intermediate_items(intermediate).get(func_id)
    if not isinstance(item, dict) or item.get("domain") not in {"P", "PT"}:
        return None

    pack = load_domain_pack(item["domain"])
    semantic = resolve_pt_function_semantics(item.get("func_name", ""), pack, [])
    if semantic.get("status") != "resolved":
        return None
    family = semantic.get("canonical_function_family")
    if not isinstance(family, str) or not family:
        return None
    units = [
        unit for unit in pack.get("analysis_catalog", {}).get("analysis_units", [])
        if isinstance(unit, dict) and unit.get("canonical_function_family") == family
    ]
    case_contract = _pt_case_set_contract(pack, family, intermediate)
    if len(units) != 1 or not case_contract:
        return None
    return {
        "domain": pack["domain"],
        "analysis_unit_id": units[0].get("analysis_unit_id"),
        "func_id": func_id,
        "func_name": s1_decision.get("func_name", item.get("func_name", "")),
        "evidence_status": "exact_known",
        "canonical_function_family": family,
        "source_function_key": case_contract["source_function_key"],
        "case_set_id": case_contract["case_set_id"],
        "scope": "function_level",
    }


def resolve_known_analysis_unit(
    intermediate: dict[str, Any], s1_decision: dict[str, Any]
) -> dict[str, str] | None:
    """解析可由 Domain Pack 精确锁定的分析单元。"""
    return (
        resolve_pt_function_level_analysis_unit(intermediate, s1_decision)
        or _resolve_analysis_unit(intermediate, s1_decision, evidence_status="exact_known")
    )


def resolve_compatible_analysis_unit(
    intermediate: dict[str, Any], s1_decision: dict[str, Any]
) -> dict[str, str] | None:
    """解析必须走 compatible 确认合同的分析单元。"""
    return _resolve_analysis_unit(intermediate, s1_decision, evidence_status="compatible")

def known_s2_contracts(intermediate: dict[str, Any], s1: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回当前输入中能够由 Domain Pack/权威案例锁定的 S2 合同。"""
    contracts: dict[str, dict[str, Any]] = {}
    for decision in s1.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        resolved = resolve_known_analysis_unit(intermediate, decision)
        if not resolved:
            continue
        pack = load_domain_pack(resolved["domain"])
        if resolved.get("scope") == "function_level" and resolved["domain"] == "PT":
            cases = _pt_case_set_contract(pack, resolved["canonical_function_family"], intermediate)
            modes = sorted(
                {_case_mode(case) for case in (cases or {}).get("cases", []) if _case_mode(case)},
                key=lambda value: (
                    {"丢失": 0, "非预期": 1, "过多": 2, "过少": 3, "反向": 4, "卡滞": 5}.get(value, 99),
                    value,
                ),
            )
            if not modes or not cases:
                continue
        else:
            modes = pack["analysis_catalog"].get("failure_mode_rules", {}).get(resolved["analysis_unit_id"])
            if not isinstance(modes, list) or not all(isinstance(mode, str) and mode for mode in modes):
                continue
        contracts[resolved["func_id"]] = {**resolved, "selected_modes": list(modes)}
    return contracts


def compatible_s2_contracts(intermediate: dict[str, Any], s1: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回当前输入中必须确认或登记差异的 compatible S2 合同。"""
    contracts: dict[str, dict[str, Any]] = {}
    for decision in s1.get("decisions", []):
        if not isinstance(decision, dict) or decision.get("is_hara") is not True:
            continue
        # PT 已按整车功能级 exact 路由时，不再把其中若干子功能的
        # compatible 语义单元重复暴露给 Agent。
        if resolve_pt_function_level_analysis_unit(intermediate, decision):
            continue
        resolved = resolve_compatible_analysis_unit(intermediate, decision)
        if not resolved:
            continue
        pack = load_domain_pack(resolved["domain"])
        modes = pack["analysis_catalog"].get("failure_mode_rules", {}).get(resolved["analysis_unit_id"])
        if not isinstance(modes, list) or not all(isinstance(mode, str) and mode for mode in modes):
            continue
        contracts[resolved["func_id"]] = {**resolved, "selected_modes": list(modes)}
    return contracts


def generate_compatible_s2_candidates(intermediate: dict[str, Any], s1: dict[str, Any]) -> list[dict[str, Any]]:
    """生成 compatible S2 草稿；草稿不是最终结论，必须由 Agent 完成确认。"""
    candidates: list[dict[str, Any]] = []
    for contract in compatible_s2_contracts(intermediate, s1).values():
        candidates.append({
            "func_id": contract["func_id"],
            "func_name": contract["func_name"],
            "selected_modes": list(contract["selected_modes"]),
            "reason": (
                f"由 {contract['domain']} Domain Pack 的 compatible 分析单元 "
                f"{contract['analysis_unit_id']} 提供受控候选；"
                "必须结合当前项目执行器、运行边界和诊断策略确认或登记差异。"
            ),
            "source": "domain_pack_compatible_draft",
            "analysis_unit_id": contract["analysis_unit_id"],
            "evidence_status": "compatible",
            "agent_action": "review_compatible_baseline",
            "agent_review": {"required": True, "status": "pending", "summary": None},
            "compatible_baseline": {
                "analysis_unit_id": contract["analysis_unit_id"],
                "selected_modes": list(contract["selected_modes"]),
            },
            "compatible_baseline_changes": [],
        })
    return candidates


def generate_s2_draft(intermediate: dict[str, Any], s1: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """生成 S2 受控草稿，返回 ``(draft, ordinary_agent_required, compatible_review)``。"""
    exact, _ = generate_known_s2(intermediate, s1)
    compatible = generate_compatible_s2_candidates(intermediate, s1)
    covered = {item["func_id"] for item in exact + compatible}
    ordinary = [
        decision.get("func_id")
        for decision in s1.get("decisions", [])
        if isinstance(decision, dict)
        and decision.get("is_hara") is True
        and isinstance(decision.get("func_id"), str)
        and decision["func_id"] not in covered
    ]
    return exact + compatible, ordinary, [item["func_id"] for item in compatible]


def generate_known_s2(intermediate: dict[str, Any], s1: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """生成精确已知 S2 决策，并返回仍需 Agent 的 func_id。"""
    contracts = known_s2_contracts(intermediate, s1)
    generated: list[dict[str, Any]] = []
    pending: list[str] = []
    for decision in s1.get("decisions", []):
        if not isinstance(decision, dict) or decision.get("is_hara") is not True:
            continue
        func_id = decision.get("func_id", "")
        contract = contracts.get(func_id)
        if not contract:
            pending.append(func_id)
            continue
        generated.append({
            "func_id": func_id,
            "func_name": contract["func_name"],
            "selected_modes": contract["selected_modes"],
            "reason": (
                f"由 {contract['domain']} Domain Pack 的标准分析单元 "
                f"{contract['analysis_unit_id']} 锁定；"
                + ("按整车功能级案例路由，子功能仅用于S1追溯，不参与失效模式数量展开；"
                   if contract.get("scope") == "function_level"
                   else "已知语义完整覆盖；")
                + "不允许按原始子功能拆分或增删失效模式。"
            ),
            "source": "domain_pack",
            "analysis_unit_id": contract["analysis_unit_id"],
            "case_set_id": contract.get("case_set_id"),
            "source_function_key": contract.get("source_function_key"),
        })
    return generated, pending


def generate_known_s3(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """按同一 authoritative case set 的 source failure group 生成受控 S3。"""
    contracts = known_s2_contracts(intermediate, s1)
    s2_by_func = {
        decision.get("func_id"): decision
        for decision in s2.get("decisions", [])
        if isinstance(decision, dict) and isinstance(decision.get("func_id"), str)
    }
    entries: list[dict[str, Any]] = []
    pending: list[str] = []
    hara_func_ids = [
        decision.get("func_id")
        for decision in s1.get("decisions", [])
        if isinstance(decision, dict) and decision.get("is_hara") is True
    ]
    function_numbers = allocate_project_function_numbers(hara_func_ids, width=4)

    for func_id, contract in contracts.items():
        s2_decision = s2_by_func.get(func_id)
        if not s2_decision or s2_decision.get("selected_modes") != contract["selected_modes"]:
            pending.append(func_id)
            continue
        pack = load_domain_pack(contract["domain"])
        if contract.get("scope") == "function_level" and contract["domain"] == "PT":
            case_contract = _pt_case_set_contract(pack, contract["canonical_function_family"], intermediate)
            if not case_contract or case_contract["case_set_id"] != contract.get("case_set_id"):
                pending.append(func_id)
                continue
            groups, group_error = _group_pt_cases(
                case_contract["cases"], contract["selected_modes"],
                pack.get("safety_goal_catalog", {}).get("hazard_catalog", {}),
            )
            if group_error:
                pending.append(func_id)
                continue
            groups_by_mode: dict[str, list[dict[str, Any]]] = {mode: [] for mode in contract["selected_modes"]}
            for group in groups:
                groups_by_mode.setdefault(group["failure_mode"], []).append(group)
            failure_sequence = 1
            for mode in contract["selected_modes"]:
                mode_anomalies = []
                for group in groups_by_mode.get(mode, []):
                    failure_id = f"P_MF_{function_numbers[func_id]}_{failure_sequence:02d}"
                    failure_sequence += 1
                    hazards = group["hazards"]
                    not_applicable = bool(hazards) and all(
                        hazard.get("associated_hara") == "不涉及" for hazard in hazards
                    )
                    for hazard in hazards:
                        hazard["hara_applicability"] = "not_applicable" if not_applicable else "applicable"
                        hazard["reason_code"] = "NO_VEHICLE_LEVEL_HAZARD" if not_applicable else None
                    mode_anomalies.append({
                        "failure_id": failure_id,
                        "description": group["anomaly"],
                        "source_failure_id": group["source_failure_id"],
                        "case_ids": group["case_ids"],
                        "source_refs": group["source_refs"],
                        "vehicle_hazard_id": hazards[0].get("vehicle_hazard_id") if len(hazards) == 1 else None,
                        "hazard_family": hazards[0].get("hazard_family") if len(hazards) == 1 else None,
                        "hara_applicability": "not_applicable" if not_applicable else "applicable",
                        "reason_code": "NO_VEHICLE_LEVEL_HAZARD" if not_applicable else None,
                        "hazards": hazards,
                    })
                entries.append({
                    "func_id": func_id,
                    "func_name": contract["func_name"],
                    "failure_mode": mode,
                    "analysis_unit_id": contract["analysis_unit_id"],
                    "case_set_id": contract.get("case_set_id"),
                    "source_function_key": contract.get("source_function_key"),
                    "source": "case_library",
                    "anomalies": mode_anomalies,
                })
            continue

        # 非 PT/旧分析单元保留原有受控路径。
        patterns_by_mode: dict[str, list[dict[str, Any]]] = {}
        for item in pack["analysis_catalog"].get("hazop_patterns", []):
            if not isinstance(item, dict):
                continue
            if item.get("analysis_unit_id") not in {None, contract["analysis_unit_id"]}:
                continue
            mode = item.get("failure_mode")
            if mode in contract["selected_modes"]:
                patterns_by_mode.setdefault(mode, []).append(item)
        if set(patterns_by_mode) != set(contract["selected_modes"]):
            pending.append(func_id)
            continue
        unit_failed = False
        failure_sequence = 1
        for mode in contract["selected_modes"]:
            anomalies = []
            for pattern in patterns_by_mode[mode]:
                vehicle_hazard_id = pattern.get("vehicle_hazard_id")
                hazard_catalog = pack["safety_goal_catalog"].get("hazard_catalog", {})
                hazard = hazard_catalog.get(vehicle_hazard_id, {}) if isinstance(vehicle_hazard_id, str) else {}
                family_id = pattern.get("hazard_family")
                hazard_text = hazard.get("vehicle_hazard")
                if not isinstance(hazard_text, str) or not hazard_text or hazard.get("hazard_family") != family_id:
                    unit_failed = True
                    break
                failure_id = f"{_failure_id_prefix(pattern, contract['domain'])}_MF_{function_numbers[func_id]}_{failure_sequence:02d}"
                failure_sequence += 1
                anomalies.append({
                    "failure_id": failure_id,
                    "description": pattern["anomaly"],
                    "vehicle_hazard_id": vehicle_hazard_id,
                    "hazard_family": family_id,
                    "hazards": [{
                        "vehicle_hazard_id": vehicle_hazard_id,
                        "description": hazard_text,
                        "associated_hara": _associated_hara_value(hazard_text),
                    }],
                })
            if unit_failed:
                pending.append(func_id)
                break
            entries.append({
                "func_id": func_id,
                "func_name": contract["func_name"],
                "failure_mode": mode,
                "analysis_unit_id": contract["analysis_unit_id"],
                "source": "domain_pack",
                "anomalies": anomalies,
            })

    expected_hara_funcs = {
        decision.get("func_id") for decision in s1.get("decisions", [])
        if isinstance(decision, dict) and decision.get("is_hara") is True
    }
    known_func_ids = set(contracts)
    pending.extend(sorted(func_id for func_id in expected_hara_funcs - known_func_ids if func_id))
    return entries, list(dict.fromkeys(pending))


def validate_known_s2_contract(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any]) -> list[str]:
    """阻止 Agent 修改已知 Pack 的 S2 范围与失效模式集合。"""
    errors: list[str] = []
    contracts = known_s2_contracts(intermediate, s1)
    actual: dict[str, list[dict[str, Any]]] = {}
    for decision in s2.get("decisions", []):
        if isinstance(decision, dict) and isinstance(decision.get("func_id"), str):
            actual.setdefault(decision["func_id"], []).append(decision)
    for func_id, contract in contracts.items():
        decisions = actual.get(func_id, [])
        if len(decisions) != 1:
            errors.append(f"[Domain Pack S2] {func_id} 必须有且仅有一条受控决策")
            continue
        decision = decisions[0]
        if decision.get("func_name") != contract["func_name"]:
            errors.append(f"[Domain Pack S2] {func_id} func_name 必须保持为 '{contract['func_name']}'")
        if decision.get("selected_modes") != contract["selected_modes"]:
            errors.append(
                f"[Domain Pack S2] {func_id} 的 selected_modes 被修改；"
                f"必须为 {contract['selected_modes']}"
            )
        if contract.get("scope") == "function_level":
            for field in ("case_set_id", "source_function_key"):
                expected_value = contract.get(field)
                if expected_value and decision.get(field) not in {None, expected_value}:
                    errors.append(f"[Domain Pack S2] {func_id} 的 {field} 不匹配")
    return errors


def validate_compatible_s2_contract(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any]) -> list[str]:
    """强制 compatible 覆盖功能进入确认/差异合同，禁止静默降级为普通 Agent 决策。"""
    errors: list[str] = []
    contracts = compatible_s2_contracts(intermediate, s1)
    actual: dict[str, list[dict[str, Any]]] = {}
    for decision in s2.get("decisions", []):
        if isinstance(decision, dict) and isinstance(decision.get("func_id"), str):
            actual.setdefault(decision["func_id"], []).append(decision)
    for func_id, contract in contracts.items():
        prefix = f"[PT compatible S2] {func_id}"
        decisions = actual.get(func_id, [])
        if len(decisions) != 1:
            errors.append(f"{prefix} 必须有且仅有一条 compatible 确认决策")
            continue
        decision = decisions[0]
        if decision.get("func_name") != contract["func_name"]:
            errors.append(f"{prefix} func_name 必须保持为 '{contract['func_name']}'")
        if decision.get("analysis_unit_id") != contract["analysis_unit_id"]:
            errors.append(f"{prefix} analysis_unit_id 必须为 {contract['analysis_unit_id']}")
        if decision.get("evidence_status") != "compatible":
            errors.append(f"{prefix} 必须标记 evidence_status=compatible，不能降级为普通 Agent 决策")
        if decision.get("agent_action") != "review_compatible_baseline":
            errors.append(f"{prefix} 必须标记 agent_action=review_compatible_baseline")
        baseline = decision.get("compatible_baseline")
        if not isinstance(baseline, dict):
            errors.append(f"{prefix} 缺少 compatible_baseline 快照")
            continue
        if baseline.get("analysis_unit_id") != contract["analysis_unit_id"]:
            errors.append(f"{prefix}.compatible_baseline.analysis_unit_id 不匹配")
        if baseline.get("selected_modes") != contract["selected_modes"]:
            errors.append(f"{prefix}.compatible_baseline.selected_modes 必须保留 Pack 候选 {contract['selected_modes']}")
    return errors


def validate_compatible_s3_scope_contract(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any], s3: dict[str, Any]) -> list[str]:
    """保持 compatible 语义覆盖在 S3 的来源标记，避免静默降级为普通/无来源 HAZOP。"""
    errors: list[str] = []
    contracts = compatible_s2_contracts(intermediate, s1)
    entries_by_func: dict[str, list[dict[str, Any]]] = {}
    for entry in s3.get("entries", []):
        if isinstance(entry, dict) and isinstance(entry.get("func_id"), str):
            entries_by_func.setdefault(entry["func_id"], []).append(entry)
    s2_by_func = {
        decision.get("func_id"): decision
        for decision in s2.get("decisions", [])
        if isinstance(decision, dict) and isinstance(decision.get("func_id"), str)
    }
    for func_id, contract in contracts.items():
        entries = entries_by_func.get(func_id, [])
        if not entries:
            errors.append(f"[PT compatible S3] {func_id} 缺少 compatible 范围复核 entry")
            continue
        s2_decision = s2_by_func.get(func_id, {})
        for index, entry in enumerate(entries):
            prefix = f"[PT compatible S3] {func_id} entries[{index}]"
            if entry.get("analysis_unit_id") != contract["analysis_unit_id"]:
                errors.append(f"{prefix}.analysis_unit_id 必须为 {contract['analysis_unit_id']}")
            if entry.get("evidence_status") != "compatible":
                errors.append(f"{prefix} 必须标记 evidence_status=compatible")
            if entry.get("agent_action") != "review_compatible_scope":
                errors.append(f"{prefix} 必须标记 agent_action=review_compatible_scope")
            scope = entry.get("compatible_scope")
            if not isinstance(scope, dict):
                errors.append(f"{prefix} 缺少 compatible_scope 来源范围快照")
                continue
            if scope.get("analysis_unit_id") != contract["analysis_unit_id"]:
                errors.append(f"{prefix}.compatible_scope.analysis_unit_id 不匹配")
            if scope.get("baseline_selected_modes") != contract["selected_modes"]:
                errors.append(f"{prefix}.compatible_scope.baseline_selected_modes 必须保留 Pack 候选")
            review = scope.get("s2_agent_review")
            if review != s2_decision.get("agent_review"):
                errors.append(f"{prefix}.compatible_scope.s2_agent_review 必须保留 S2 compatible 确认记录")
    return errors


def validate_known_s3_contract(intermediate: dict[str, Any], s1: dict[str, Any], s2: dict[str, Any], s3: dict[str, Any]) -> list[str]:
    """阻止 Agent 修改已知 Pack 的五条受控 HAZOP 模式。"""
    errors: list[str] = []
    expected_entries, pending = generate_known_s3(intermediate, s1, s2)
    if pending:
        return errors
    expected = {
        (entry["func_id"], entry["failure_mode"]): entry
        for entry in expected_entries
    }
    actual = {
        (entry.get("func_id"), entry.get("failure_mode")): entry
        for entry in s3.get("entries", [])
        if isinstance(entry, dict) and (entry.get("func_id"), entry.get("failure_mode")) in expected
    }
    for key, expected_entry in expected.items():
        actual_entry = actual.get(key)
        if actual_entry is None:
            errors.append(f"[Domain Pack S3] 缺少受控 HAZOP: {key[0]} / {key[1]}")
            continue
        if actual_entry.get("analysis_unit_id") != expected_entry["analysis_unit_id"]:
            errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} analysis_unit_id 被修改")
        anomalies = actual_entry.get("anomalies")
        expected_anomalies = expected_entry["anomalies"]
        if not isinstance(anomalies, list) or len(anomalies) != len(expected_anomalies):
            errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} 的受控异常数量被修改")
            continue
        actual_by_failure = {item.get("failure_id"): item for item in anomalies if isinstance(item, dict)}
        if len(actual_by_failure) != len(anomalies):
            errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} 存在重复或缺失 failure_id")
            continue
        for expected_anomaly in expected_anomalies:
            anomaly = actual_by_failure.get(expected_anomaly["failure_id"])
            if anomaly is None:
                errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} 缺少 {expected_anomaly['failure_id']}")
                continue
            for field in ("failure_id", "description", "vehicle_hazard_id", "hazard_family"):
                if anomaly.get(field) != expected_anomaly[field]:
                    errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} / {expected_anomaly['failure_id']} 的 {field} 被修改")
            hazards = anomaly.get("hazards")
            expected_hazards = expected_anomaly.get("hazards", [])
            if not isinstance(hazards, list) or len(hazards) != len(expected_hazards) or not all(isinstance(item, dict) for item in hazards):
                errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} / {expected_anomaly['failure_id']} 的受控危害集合被修改")
            else:
                actual_hazard_keys = {(item.get("vehicle_hazard_id"), item.get("description"), item.get("associated_hara")) for item in hazards}
                expected_hazard_keys = {(item.get("vehicle_hazard_id"), item.get("description"), item.get("associated_hara")) for item in expected_hazards}
                if actual_hazard_keys != expected_hazard_keys:
                    errors.append(f"[Domain Pack S3] {key[0]} / {key[1]} / {expected_anomaly['failure_id']} 的危害描述或 associated_hara 被修改")
    return errors
