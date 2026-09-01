"""从已审核的 PT 案例库重建 PT Domain Pack。

本模块只把旧 PT Domain Pack 当作语义结构模板（function_catalog 和
analysis_units），不会读取旧 risk_catalog/safety_goal_catalog 的业务记录。
案例、场景、危害和运行时 canonical ID 全部从新的 PT reference inventory 生成。
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from utils.pt_case_source import import_case_table, normalize_text
from utils.case_library import CaseLibrary

FAMILY_TO_UNIT = {
    "pt_gear_state_control": "PT_GEAR_STATE_CONTROL_MAIN",
    "pt_traction_torque_control": "PT_TRACTION_TORQUE_CONTROL_MAIN",
    "pt_charge_discharge": "PT_CHARGE_DISCHARGE_MAIN",
    "pt_hv_safety": "PT_HV_SAFETY_MAIN",
    "pt_thermal_management": "PT_THERMAL_MANAGEMENT_BASELINE",
    "pt_hv_power_state_management": "PT_HV_POWER_STATE_MANAGEMENT_BASELINE",
}

NO_HAZARD_ID = "PT_HZ_NO_HAZARD"
NO_HAZARD_FAMILY = "pt_hazard_family_no_hazard"

FAILURE_MODE_ORDER = {"丢失": 0, "非预期": 1, "过多": 2, "过少": 3, "反向": 4, "卡滞": 5}


def _mode_sort_key(value: str) -> tuple[int, str]:
    return (FAILURE_MODE_ORDER.get(value, 99), value)


def _stable_id(prefix: str, *parts: Any, length: int = 12) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:length].upper()}"


def _record_key(record: dict[str, Any], hazard_id: str | None) -> tuple[str, ...] | None:
    unit_id = FAMILY_TO_UNIT.get(record.get("canonical_function_family"))
    if not unit_id or not record.get("scenario_id") or not hazard_id:
        return None
    return (
        unit_id,
        normalize_text(record.get("normalized_failure_mode")),
        normalize_text(record.get("anomaly")),
        hazard_id,
        normalize_text(record.get("scenario_id")),
    )


def _failure_key(record: dict[str, Any], hazard_id: str | None) -> tuple[str, ...] | None:
    unit_id = FAMILY_TO_UNIT.get(record.get("canonical_function_family"))
    if not unit_id or not hazard_id:
        return None
    return (
        unit_id,
        normalize_text(record.get("normalized_failure_mode")),
        normalize_text(record.get("anomaly")),
        hazard_id,
    )


def _substantive_signature(record: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(record.get(field) for field in (
        "description", "severity", "severity_reason", "exposure", "exposure_reason",
        "controllability", "controllability_reason", "computed_asil", "safety_goal",
        "safe_state", "ftti",
    ))


def _nonempty_first(records: list[dict[str, Any]], field: str) -> Any:
    for record in records:
        value = record.get(field)
        if value not in (None, ""):
            return value
    return None


def _source_ref(record: dict[str, Any]) -> dict[str, Any]:
    trace = record.get("trace", {})
    return {
        "source_file": trace.get("source_file"),
        "source_sheet": trace.get("source_sheet"),
        "source_row": trace.get("source_row"),
        "source_project": trace.get("source_project"),
        "raw_function_id": trace.get("raw_function_id"),
        "raw_failure_id": trace.get("raw_failure_id"),
        "raw_hazard_event_id": trace.get("raw_hazard_event_id"),
        "raw_safety_goal_id": trace.get("raw_safety_goal_id"),
        "raw_vehicle_safety_goal_id": trace.get("raw_vehicle_safety_goal_id"),
    }



def _unique_vehicle_safety_goal_id(records: list[dict[str, Any]]) -> str | None:
    ids = {
        str(record.get("vehicle_safety_goal_id")).strip()
        for record in records
        if str(record.get("vehicle_safety_goal_id") or "").strip()
    }
    return next(iter(ids)) if len(ids) == 1 else None


def _build_vehicle_safety_goal_runtime_catalog(
    inventory: dict[str, Any],
    embedded_cases: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Project the reviewed vehicle-level SG sheet into the PT runtime pack.

    Project IDs remain trace-only everywhere else. This catalog is the one
    explicit exception: its IDs are approved output identifiers used by S5
    prefill, with case/source mappings kept for auditable resolution.
    """
    source_catalog = inventory.get("vehicle_safety_goal_catalog")
    if not isinstance(source_catalog, dict) or not source_catalog.get("goals"):
        return None
    result = deepcopy(source_catalog)
    result["runtime_contract"] = (
        "reference vehicle safety goal IDs may be used only for PT S5 prefill; "
        "semantic matching remains separate"
    )
    case_to_goal: dict[str, str] = {}
    hazard_event_to_goal: dict[str, str] = {}
    analysis_key_to_goals: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
    goal_case_refs: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record, case in zip(inventory.get("records", []), embedded_cases):
        goal_id = str(record.get("vehicle_safety_goal_id") or "").strip()
        if not goal_id:
            continue
        if goal_id not in result["goals"]:
            raise ValueError(f"案例记录引用未定义的整车安全目标: {goal_id}")
        case_id = str(case.get("case_id") or "").strip()
        if case_id:
            case_to_goal[case_id] = goal_id
        raw_hazard_id = str((record.get("trace") or {}).get("raw_hazard_event_id") or "").strip()
        if raw_hazard_id:
            previous = hazard_event_to_goal.get(raw_hazard_id)
            if previous and previous != goal_id:
                raise ValueError(
                    f"危害事件 {raw_hazard_id} 对应多个整车安全目标: {previous}, {goal_id}"
                )
            hazard_event_to_goal[raw_hazard_id] = goal_id
        unit_id = FAMILY_TO_UNIT.get(record.get("canonical_function_family"))
        semantic_key = (
            unit_id or "",
            normalize_text(record.get("safety_goal")),
            normalize_text(record.get("safe_state")),
            normalize_text(record.get("ftti")),
        )
        analysis_key_to_goals[semantic_key].add(goal_id)
        goal_case_refs[goal_id].append(_source_ref(record))

    for goal_id, goal in result["goals"].items():
        goal["case_source_refs"] = goal_case_refs.get(goal_id, [])
    result["case_to_goal"] = dict(sorted(case_to_goal.items()))
    result["hazard_event_to_goal"] = dict(sorted(hazard_event_to_goal.items()))
    result["semantic_candidates"] = {
        "|".join(key): sorted(values)
        for key, values in sorted(analysis_key_to_goals.items())
    }
    return result

def _build_hazards(records: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    by_text: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        text = normalize_text(record.get("vehicle_hazard"))
        if text:
            by_text[text].append(record)

    hazard_catalog: dict[str, Any] = {}
    hazard_families: dict[str, Any] = {}
    text_to_id: dict[str, str] = {}
    for text in sorted(by_text):
        hazard_id = _stable_id("PT_HZ_C", text, length=10)
        family_id = _stable_id("pt_hazard_family_c", text, length=10).lower()
        text_to_id[text] = hazard_id
        variants = by_text[text]
        hazard_families[family_id] = {
            "canonical_hazard": text,
            "canonical_safety_goal": _nonempty_first(variants, "safety_goal"),
            "safe_state": _nonempty_first(variants, "safe_state"),
            "ftti": _nonempty_first(variants, "ftti"),
            "source_refs": [_source_ref(item) for item in variants],
        }
        hazard_catalog[hazard_id] = {
            "hazard_family": family_id,
            "vehicle_hazard": text,
            "source_refs": [_source_ref(item) for item in variants],
        }
    return hazard_catalog, hazard_families, text_to_id


def _canonical_scenario_id(text: str) -> str:
    normalized = re.sub(r"\s+", "", normalize_text(text))
    if not normalized:
        return ""
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12].upper()
    return f"PT_SC_{digest}"


def _canonicalize_scenario_ids(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = deepcopy(records)
    for record in result:
        profile = record.get("scenario_profile") or {}
        text = normalize_text(profile.get("scenario_text"))
        if text:
            record["scenario_id"] = _canonical_scenario_id(text)
    return result


def _scenario_catalog(records: list[dict[str, Any]]) -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    for record in records:
        scenario_id = record.get("scenario_id")
        text = normalize_text((record.get("scenario_profile") or {}).get("scenario_text"))
        if not scenario_id or not text:
            continue
        item = catalog.setdefault(scenario_id, {
            "text": text,
            "lifecycle_status": "active_reference",
            "source_kinds": ["PT.XLSX"],
            "source_refs": [],
        })
        item["source_refs"].append(_source_ref(record))
    for item in catalog.values():
        item["source_refs"] = sorted(item["source_refs"], key=lambda ref: (ref.get("source_row") or 0, ref.get("raw_hazard_event_id") or ""))
    return dict(sorted(catalog.items()))


def build_pt_domain_pack(
    inventory: dict[str, Any],
    *,
    semantic_template: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _canonicalize_scenario_ids(list(inventory.get("records", [])))
    hazard_catalog, hazard_families, text_to_hazard_id = _build_hazards(records)
    hazard_catalog[NO_HAZARD_ID] = {
        "hazard_family": NO_HAZARD_FAMILY,
        "vehicle_hazard": "无整车层面危害",
        "is_no_vehicle_level_hazard": True,
    }
    hazard_families[NO_HAZARD_FAMILY] = {
        "canonical_hazard": "无整车层面危害",
        "canonical_safety_goal": None,
        "safe_state": None,
        "ftti": None,
        "is_no_vehicle_level_hazard": True,
    }
    scenario_catalog = _scenario_catalog(records)
    inventory["records"] = records

    eligible = [
        record for record in records
        if record.get("scenario_id") and normalize_text(record.get("vehicle_hazard"))
        and FAMILY_TO_UNIT.get(record.get("canonical_function_family"))
    ]
    event_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    failure_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in eligible:
        hazard_id = text_to_hazard_id[normalize_text(record["vehicle_hazard"])]
        event_groups[_record_key(record, hazard_id)].append(record)

    failure_source_records = [
        record for record in records
        if FAMILY_TO_UNIT.get(record.get("canonical_function_family"))
        and normalize_text(record.get("normalized_failure_mode"))
        and (normalize_text(record.get("vehicle_hazard")) or record.get("hazard_mapping_status") == "not_applicable")
    ]
    for record in failure_source_records:
        hazard_text = normalize_text(record.get("vehicle_hazard"))
        hazard_id = text_to_hazard_id.get(hazard_text, NO_HAZARD_ID)
        failure_groups[_failure_key(record, hazard_id)].append(record)

    failure_ids = {
        key: _stable_id("P_MF_C", *key, length=10)
        for key in sorted(failure_groups)
    }
    for record in records:
        hazard_text = normalize_text(record.get("vehicle_hazard"))
        hazard_id = text_to_hazard_id.get(hazard_text, NO_HAZARD_ID) if (hazard_text or record.get("hazard_mapping_status") == "not_applicable") else None
        record["canonical_vehicle_hazard_id"] = hazard_id
        record["canonical_hazard_family"] = hazard_catalog.get(hazard_id, {}).get("hazard_family") if hazard_id else None
        failure_key = _failure_key(record, hazard_id) if hazard_id else None
        record["canonical_failure_id"] = failure_ids.get(failure_key) if failure_key else None
        record["library_status"] = "unresolved_semantics" if not record.get("canonical_function_family") else "trusted_reference"

    hazard_by_failure = {}
    hazop_patterns = []
    for key in sorted(failure_groups):
        unit_id, failure_mode, anomaly, hazard_id = key
        variants = failure_groups[key]
        failure_id = failure_ids[key]
        hazard_by_failure[failure_id] = hazard_id
        hazop_patterns.append({
            "analysis_unit_id": unit_id,
            "failure_mode": failure_mode,
            "failure_id": failure_id,
            "anomaly": anomaly,
            "vehicle_hazard_id": hazard_id,
            "hazard_family": hazard_catalog[hazard_id]["hazard_family"],
            "evidence_status": "trusted_reference",
            "source_refs": [_source_ref(item) for item in variants],
        })

    exact_events = []
    trusted_variants = []
    reference_events = []
    scenario_lookup: dict[str, Any] = defaultdict(lambda: {
        "analysis_unit_id": None,
        "failure_modes": defaultdict(lambda: {"failure_ids": [], "scenario_ids": []}),
    })
    for index, key in enumerate(sorted(event_groups), start=1):
        unit_id, failure_mode, anomaly, hazard_id, scenario_id = key
        variants = sorted(event_groups[key], key=lambda item: item.get("trace", {}).get("source_row", 0))
        failure_id = failure_ids[(unit_id, failure_mode, anomaly, hazard_id)]
        signatures = {_substantive_signature(item) for item in variants}
        canonical = variants[0]
        scenario_lookup_key = next(
            (family for family, candidate_unit in FAMILY_TO_UNIT.items() if candidate_unit == unit_id),
            unit_id,
        )
        lookup_modes = scenario_lookup[scenario_lookup_key]["failure_modes"]
        scenario_lookup[scenario_lookup_key]["analysis_unit_id"] = unit_id
        mode_entry = lookup_modes[failure_mode]
        if failure_id not in mode_entry["failure_ids"]:
            mode_entry["failure_ids"].append(failure_id)
        if scenario_id not in mode_entry["scenario_ids"]:
            mode_entry["scenario_ids"].append(scenario_id)

        event = {
            "analysis_unit_id": unit_id,
            "failure_id": failure_id,
            "scenario_id": scenario_id,
            "vehicle_hazard_id": hazard_id,
            "vehicle_safety_goal_id": _unique_vehicle_safety_goal_id(variants),
            "vehicle_safety_goal_ids": sorted({
                str(item.get("vehicle_safety_goal_id")).strip()
                for item in variants
                if str(item.get("vehicle_safety_goal_id") or "").strip()
            }),
            "description": canonical.get("description"),
            "severity": canonical.get("severity"),
            "severity_reason": canonical.get("severity_reason"),
            "exposure": canonical.get("exposure"),
            "exposure_reason": canonical.get("exposure_reason"),
            "controllability": canonical.get("controllability"),
            "controllability_reason": canonical.get("controllability_reason"),
            "expected_asil": canonical.get("computed_asil"),
            "safety_goal": canonical.get("safety_goal"),
            "safe_state": canonical.get("safe_state"),
            "ftti": canonical.get("ftti"),
            "evidence_status": "trusted_reference" if len(signatures) == 1 else "trusted_variant",
            "source_refs": [_source_ref(item) for item in variants],
        }
        if len(signatures) == 1:
            exact_events.append(event)
        else:
            trusted_variants.append({
                **event,
                "baseline_failure_id": failure_id,
                "variants": [
                    {"source_ref": _source_ref(item), **{field: item.get(field) for field in (
                        "description", "severity", "severity_reason", "exposure", "exposure_reason",
                        "controllability", "controllability_reason", "computed_asil", "safety_goal",
                        "safe_state", "ftti",
                    )}}
                    for item in variants
                ],
            })

        reference_events.append({
            "reference_source": "PT.XLSX",
            "reference_event_id": f"P_hzrd_{90000 + index:05d}",
            "source_refs": [_source_ref(item) for item in variants],
            "source_row": variants[0].get("trace", {}).get("source_row"),
            "function_name": canonical.get("function_name") or canonical.get("canonical_function_family") or "PT reference function",
            "reference_failure_id": failure_id,
            "anomaly": anomaly,
            "vehicle_hazard": hazard_catalog[hazard_id]["vehicle_hazard"],
            "vehicle_safety_goal_id": _unique_vehicle_safety_goal_id(variants),
            "vehicle_safety_goal_ids": sorted({
                str(item.get("vehicle_safety_goal_id")).strip()
                for item in variants
                if str(item.get("vehicle_safety_goal_id") or "").strip()
            }),
            "scenario_id": scenario_id,
            "description": canonical.get("description"),
            "severity": canonical.get("severity"),
            "severity_reason": canonical.get("severity_reason"),
            "exposure": canonical.get("exposure"),
            "exposure_reason": canonical.get("exposure_reason"),
            "controllability": canonical.get("controllability"),
            "controllability_reason": canonical.get("controllability_reason"),
            "asil": canonical.get("computed_asil"),
            "safety_goal_id": None,
            "safety_goal": canonical.get("safety_goal"),
            "safe_state": canonical.get("safe_state"),
            "ftti": canonical.get("ftti"),
            "evidence_status": "trusted_reference" if len(signatures) == 1 else "trusted_variant",
        })

    failure_mode_rules = defaultdict(set)
    for pattern in hazop_patterns:
        failure_mode_rules[pattern["analysis_unit_id"]].add(pattern["failure_mode"])
    failure_mode_rules = {key: sorted(value, key=_mode_sort_key) for key, value in sorted(failure_mode_rules.items())}
    scenario_lookup = json.loads(json.dumps(scenario_lookup, ensure_ascii=False))
    for family in scenario_lookup.values():
        for mode in family["failure_modes"].values():
            mode["failure_ids"].sort()
            mode["scenario_ids"].sort()

    scenario_sets = {
        "pt_reference_all": {
            "description": "新 PT 案例库生成的可信参考场景",
            "scenarios": [
                {"scenario_id": scenario_id, "text": item["text"]}
                for scenario_id, item in scenario_catalog.items()
            ],
        }
    }

    function_catalog = deepcopy(semantic_template["function_catalog"])
    analysis_units = deepcopy(semantic_template["analysis_catalog"].get("analysis_units", []))
    # 仅使用语义结构模板；所有 failure/hazard/event 业务记录均在本函数内重建。
    pack = {
        "schema_version": "1.0",
        "domain": "PT",
        "domain_name": semantic_template.get("domain_name", "动力传动域"),
        "status": "pt_case_library_rebuilt_v1",
        "release_policy": {
            "candidate_mapping_requires_engineering_approval": True,
            "formal_s4_requires_s3_traceability": True,
            "trusted_reference_source": "PT.XLSX",
            "policy_note": "新案例库的内容由工程师审核；来源 ID 只保留为 trace/source_refs，运行时使用 canonical 语义 ID。",
        },
        "provenance": {
            "migration_stage": "M4",
            "reference_sources": [inventory.get("source", {})],
            "case_library_schema": inventory.get("schema_version"),
            "source_record_count": len(records),
            "eligible_reference_event_count": len(reference_events),
            "canonical_failure_count": len(failure_ids),
            "canonical_hazard_count": len(hazard_catalog),
            "canonical_scenario_count": len(scenario_catalog),
            "deduplication_policy": "semantic_key_only; source IDs are trace-only",
            "trusted_variant_count": len(trusted_variants),
            "legacy_pt_pack_used_for": ["function_catalog", "analysis_units"],
            "legacy_pt_pack_not_used_for": ["hazard_catalog", "hazop_patterns", "event_matrix", "reference_event_matrix", "compatible_event_baselines", "scenario_catalog"],
            "scene_single_source_migration": {
                "reference_event_count": len(reference_events),
                "canonical_scenario_count": len(scenario_catalog),
                "legacy_scenario_count": 0,
                "guidance_conflict_policy": "new_case_library_wins",
                "guidance_conflict_paths": [],
                "external_pt_scenario_asset": "references/scenarios/PT.json",
                "external_pt_scenario_asset_status": "removed",
            },
        },
        "function_catalog": function_catalog,
        "analysis_catalog": {
            "analysis_units": analysis_units,
            "failure_mode_rules": failure_mode_rules,
            "hazop_patterns": hazop_patterns,
        },
        "risk_catalog": {
            "scenario_sets": scenario_sets,
            "scenario_lookup": scenario_lookup,
            "event_matrix": exact_events,
            "compatible_event_baselines": trusted_variants,
            "compatible_baseline_policy": "trusted variants preserve source differences; current project must choose applicability during S2-S4",
            "scenario_catalog": scenario_catalog,
            "reference_event_matrix": reference_events,
            "scenario_single_source_contract": {
                "enabled": True,
                "catalog_key": "risk_catalog.scenario_catalog",
                "lookup_uses": "scenario_ids",
                "reference_events_use": "scenario_id",
            },
        },
        "safety_goal_catalog": {
            "hazard_families": hazard_families,
            "rules": [{"deduplication_key": ["domain", "hazard_family", "canonical_safety_goal", "safe_state", "ftti"]}],
            "hazard_catalog": hazard_catalog,
        },
        "quality_contract": {
            "known_exact_requirements": ["trusted_reference 记录可作为 PT 参考预填；当前项目正式结论仍按 S1-S5 生成。"],
            "unknown_policy": "unresolved_semantics 不得静默绑定错误分析单元；但可信来源记录保留在案例库和 reference_event_matrix。",
        },
    }
    # 单套 PT 资产：将语义案例索引嵌入 PT.json，CaseLibrary 运行时不再
    # 读取独立的 PT_cases_v2.json。该投影由同一批 XLSX 记录生成，
    # 不能被人工单独编辑。
    embedded_cases = build_runtime_case_library_v2(inventory)
    vehicle_safety_goal_catalog = _build_vehicle_safety_goal_runtime_catalog(inventory, embedded_cases)
    if vehicle_safety_goal_catalog is not None:
        pack["vehicle_safety_goal_catalog"] = vehicle_safety_goal_catalog
    pack["case_library_catalog"] = {
        "schema_version": "2.0",
        "source": "PT.XLSX",
        "generated_by": "build_pt_domain_pack.py",
        "editable": False,
        "matching_contract": "semantic_profiles_only; project_ids_are_trace_only",
        "case_count": len(embedded_cases),
        "cases": embedded_cases,
    }
    pack["provenance"]["case_library_embedded"] = True
    pack["provenance"]["runtime_case_source"] = "case_library_catalog.cases"
    pack["provenance"]["runtime_case_asset"] = "same_file:references/domain_packs/PT.json"
    pack["provenance"]["single_runtime_asset"] = True
    pack["provenance"]["runtime_source_of_truth"] = "references/domain_packs/PT.json"
    summary = {
        "source_record_count": len(records),
        "reference_event_count": len(reference_events),
        "exact_event_count": len(exact_events),
        "trusted_variant_count": len(trusted_variants),
        "canonical_failure_count": len(failure_ids),
        "canonical_hazard_count": len(hazard_catalog),
        "canonical_scenario_count": len(scenario_catalog),
        "excluded_from_runtime_event_count": len(records) - len(eligible),
        "unknown_function_count": sum(not record.get("canonical_function_family") for record in records),
    }
    return pack, summary


def build_runtime_case_library_v2(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """把全量可信来源投影为旧 CaseLibrary v2 所需的纯语义资产。

    该投影故意不携带 func_id/analysis_unit_id/failure_id 等项目级匹配键；
    完整运行时语义记录直接嵌入 PT.json；来源追溯字段保存在每条案例的 source_refs 中。
    """
    cases: list[dict[str, Any]] = []
    anomaly_normalizer = CaseLibrary()
    for index, record in enumerate(inventory.get("records", []), start=1):
        scenario_profile = record.get("scenario_profile") or {}
        scenario_text = normalize_text(scenario_profile.get("scenario_text"))
        function_family = record.get("canonical_function_family")
        hazard_family = record.get("canonical_hazard_family")
        canonical_mode = record.get("normalized_failure_mode")
        anomaly = anomaly_normalizer.canonical_anomaly_class(record.get("anomaly", ""), canonical_mode or "")
        case_id = _stable_id("PT_CASE_", record.get("semantic_key"), index, length=12)
        cases.append({
            "schema_version": "2.0",
            "case_id": case_id,
            "domain": "PT",
            "vehicle_safety_goal_id": record.get("vehicle_safety_goal_id"),
            "function_profile": {
                "canonical_function_family": function_family,
                "semantic_roles": [record["semantic_role"]] if record.get("semantic_role") else [],
                "controlled_objects": [],
                "capability_tokens": [],
            },
            "failure_profile": {
                "canonical_mode": canonical_mode,
                "anomaly_class": anomaly,
                "anomaly_aliases": [record.get("anomaly")] if record.get("anomaly") else [],
            },
            "hazard_profile": {
                "hazard_family": hazard_family,
                "vehicle_hazard_class": record.get("vehicle_hazard"),
                "hazard_aliases": [record.get("vehicle_hazard")] if record.get("vehicle_hazard") else [],
            },
            "scenario_profile": {
                "semantic_fingerprint": record.get("scenario_id"),
                "scenario_text": scenario_text,
                "dimensions": scenario_profile.get("dimensions", {}),
            },
            # 危害事件描述属于案例本体，不是 Agent 在 S4 重写的空白字段。
            # S3 通过 case_id 选择同一运行时案例后，S4 必须能完整还原该事件。
            "event_description": normalize_text(record.get("description")) or None,
            "assessment": {
                "severity": record.get("severity"),
                "severity_reason": record.get("severity_reason"),
                "exposure": record.get("exposure"),
                "exposure_reason": record.get("exposure_reason"),
                "controllability": record.get("controllability"),
                "controllability_reason": record.get("controllability_reason"),
                "asil": record.get("computed_asil"),
                "safety_goal": record.get("safety_goal"),
                "safe_state": record.get("safe_state"),
                "ftti": record.get("ftti"),
            },
            "evidence_status": "trusted_reference" if function_family else "unresolved_semantics",
            "source_refs": [_source_ref(record)],
        })
    return cases


def build_pt_pack_from_xlsx(
    input_path: str | Path,
    *,
    semantic_template_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    template = json.loads(Path(semantic_template_path).read_text(encoding="utf-8"))
    inventory, audit = import_case_table(input_path)
    pack, summary = build_pt_domain_pack(inventory, semantic_template=template)
    return inventory, pack, {"audit": audit, "summary": summary}
