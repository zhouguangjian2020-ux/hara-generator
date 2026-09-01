# scripts/stages/stage_hara.py
# S4 HARA 阶段编排：prepare（从 HAZOP 生成骨架）和 validate（验证+编号+ASIL计算）

import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

# 确保 scripts/ 在 path 中
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.hara_rules import (
    compute_asil, infer_function_type, get_safe_state, get_ftti,
    infer_hazard_category, get_prefill_data, lookup_scenarios,
    assign_hazard_ids, assign_safety_goal_ids, clear_derived_hara_fields, validate_all,
    validate_s1_coverage, validate_s1_downstream_exclusion,
    validate_final_s1_against_intermediate,
    validate_s2_decisions_against_s1, validate_s3_entries_against_s1_s2,
    validate_s2_s3_mode_coverage,
    validate_failure_id_format, validate_description_length,
    build_hazard_semantic_key,
    lookup_sec_for_scenario,
    FOOTNOTE_CLAUSE,
)
from utils.case_library import get_case_library
from utils.domain_packs import (
    canonical_domain_pack_code,
    get_exact_risk_matrix_events,
    load_domain_pack,
)
from utils.compatible_baseline_contract import (
    validate_s2_compatible_baselines,
    validate_s3_compatible_baselines,
)
from utils.domain_pack_generation import (
    validate_compatible_s2_contract,
    validate_compatible_s3_scope_contract,
    validate_known_s2_contract,
    validate_known_s3_contract,
)


class S4ValidationError(ValueError):
    """S4 验证未通过，不能产出正式 final 文件。"""



def _load_json(path: str, label: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: {label} 文件不存在: {path}")
        raise
    except json.JSONDecodeError as e:
        print(f"错误: {label} JSON 格式无效: {e}")
        raise


def _save_json(data: dict, path: str):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[S4] 已保存: {out}")


def _validation_failure_marker_path(output_path: str) -> Path:
    """返回与正式 final 绑定的验证失败标记文件路径。"""
    return Path(f"{output_path}.validation_failed")


def _mark_validation_failure(output_path: str, message: str) -> None:
    """阻止旧 final 在本轮 S4 失败后被下游误用。"""
    marker = _validation_failure_marker_path(output_path)
    _save_json_atomic({
        "stage": "S4",
        "validation": {"passed": False},
        "message": message,
    }, str(marker))
    print(f"[S4 validate] 已写入失败标记，下游 S5/write 将拒绝使用该 final: {marker}")


def _clear_validation_failure_marker(output_path: str) -> None:
    """成功生成 final 后清除同路径的失败标记。"""
    marker = _validation_failure_marker_path(output_path)
    if marker.exists():
        marker.unlink()


def _save_json_atomic(data: dict, path: str):
    """原子写入正式 JSON，避免中途失败留下半成品。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp",
            prefix=f".{out.stem}.", dir=out.parent, delete=False
        ) as temp:
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        os.replace(temp_path, out)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    print(f"[S4] 已保存: {out}")


# ============================================================
# prepare: 从 s3_hazop.json 生成 HARA 骨架
# ============================================================

# 只允许明确的“不涉及/无整车层面危害”处置；禁止宽泛的“无”关键词，
# 防止把“无扭矩”“无法行驶”等真实危险后果静默跳过。
_SKIP_KEYWORDS = ("不涉及", "无整车层面危害", "无整车危害", "无危害", "N/A", "n/a")


def _is_skip_hazard(hazard_desc: str) -> bool:
    """判断 S3 危害是否明确属于受控 skip；空描述不是 skip。"""
    if not isinstance(hazard_desc, str) or not hazard_desc.strip():
        return False
    return any(keyword in hazard_desc for keyword in _SKIP_KEYWORDS)


def _skip_metadata(hazard_desc: str, associated_hara: str, source_hazard: dict) -> dict | None:
    """将明确的 S3 '不涉及' 结论转换为受控 S4 skip 元数据。"""
    explicit_no_vehicle_hazard = _is_skip_hazard(hazard_desc)
    explicit_no_hara = isinstance(associated_hara, str) and associated_hara.strip() == "不涉及"
    structured_no_vehicle_hazard = (
        source_hazard.get("hara_applicability") == "not_applicable"
        and source_hazard.get("reason_code") == "NO_VEHICLE_LEVEL_HAZARD"
    )
    if not (explicit_no_vehicle_hazard or explicit_no_hara or structured_no_vehicle_hazard):
        return None
    source = hazard_desc.strip() if isinstance(hazard_desc, str) else ""
    return {
        "skip_reason_code": "NO_VEHICLE_LEVEL_HAZARD",
        "skip_reason": (
            f"S3 已明确标注「{source or '不涉及'}」：该异常不构成整车层面危害；"
            "若仅为 QM/S=0，必须保留为 HARA 事件而非 skip。"
        ),
        "covered_by": source_hazard.get("covered_by"),
        "transfer_to": source_hazard.get("transfer_to"),
    }


def _domain_pack_compatible_prefill(domain: str, entry: dict, anomaly: dict):
    """Return non-locking PT reference baseline events for one exact S3 anomaly."""
    unit, failure = entry.get("analysis_unit_id"), anomaly.get("failure_id")
    if not isinstance(unit, str) or not isinstance(failure, str):
        return None
    pack = load_domain_pack(domain)
    risk = pack.get("risk_catalog", {})
    records = [item for item in risk.get("compatible_event_baselines", [])
               if isinstance(item, dict) and item.get("analysis_unit_id") == unit and item.get("failure_id") == failure]
    if not records:
        return None
    scenario_catalog = risk.get("scenario_catalog", {})
    fields = ("description", "severity", "severity_reason", "exposure", "exposure_reason", "controllability", "controllability_reason", "asil", "safety_goal", "safe_state", "ftti")
    events = []
    for item in records:
        scenario_id = item.get("scenario_id")
        catalog_item = scenario_catalog.get(scenario_id, {}) if isinstance(scenario_catalog, dict) else {}
        scenario = catalog_item.get("text") if isinstance(catalog_item, dict) else None
        # 兼容尚未迁移的其他 Domain Pack；PT 单源 Pack 不允许退回文本副本。
        if not isinstance(scenario, str) or not scenario.strip():
            scenario = item.get("scenario")
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError(
                f"{domain} compatible baseline 缺少可解析场景: {unit}/{failure}/{scenario_id}"
            )
        events.append({
            "scenario_id": scenario_id,
            "scenario": scenario,
            **{field: item.get(field) for field in fields},
            "sec_source": "domain_pack_compatible",
            "note": "PT reference compatible baseline; review project differences.",
            "engineering_override": None,
        })
    return {"analysis_unit_id": unit, "failure_id": failure, "events": events}


def _domain_pack_matrix_prefill(domain: str, entry: dict, anomaly: dict):
    """为声明标准分析单元的 HAZOP 异常加载精确风险矩阵。

    返回 None 代表该域/分析单元没有精确矩阵合同；一旦合同存在，缺少任一
    场景会由 Domain Pack API 抛错，禁止退化为旧的文本匹配或 Agent 推理。
    """
    analysis_unit_id = entry.get("analysis_unit_id")
    failure_id = anomaly.get("failure_id")
    if not isinstance(analysis_unit_id, str) or not isinstance(failure_id, str):
        return None
    events = get_exact_risk_matrix_events(domain, analysis_unit_id, failure_id)
    if events is None:
        return None
    pack = load_domain_pack(domain)
    vehicle_hazard_id = anomaly.get("vehicle_hazard_id")
    catalog = pack.get("safety_goal_catalog", {}).get("hazard_catalog", {})
    hazard_definition = catalog.get(vehicle_hazard_id, {}) if isinstance(catalog, dict) and isinstance(vehicle_hazard_id, str) else {}
    family_id = anomaly.get("hazard_family")
    family = pack.get("safety_goal_catalog", {}).get("hazard_families", {}).get(family_id, {})
    if not isinstance(family, dict):
        family = {}
    result = []
    for item in events:
        matrix_event = {
            "scenario_id": item.get("scenario_id"),
            "scenario": item.get("scenario", ""),
            "description": item.get("description") or "",
            "severity": item.get("severity"),
            "severity_reason": item.get("severity_reason"),
            "exposure": item.get("exposure"),
            "exposure_reason": item.get("exposure_reason"),
            "controllability": item.get("controllability"),
            "controllability_reason": item.get("controllability_reason"),
            "safety_goal": item.get("safety_goal"),
            "safe_state": item.get("safe_state"),
            "ftti": item.get("ftti"),
            "expected_asil": item.get("expected_asil"),
            "sec_source": "domain_pack_exact",
            "note": None,
            "engineering_override": None,
        }
        if "vehicle_safety_goal_id" in item:
            matrix_event["vehicle_safety_goal_id"] = item.get("vehicle_safety_goal_id")
        if "vehicle_safety_goal_ids" in item:
            matrix_event["vehicle_safety_goal_ids"] = item.get("vehicle_safety_goal_ids")
        result.append(matrix_event)
    return {
        "analysis_unit_id": analysis_unit_id,
        "failure_id": failure_id,
        "vehicle_hazard_id": vehicle_hazard_id,
        "vehicle_hazard": hazard_definition.get("vehicle_hazard"),
        "hazard_family": family_id,
        "safe_state": family.get("safe_state"),
        "ftti": family.get("ftti"),
        "events": result,
    }


def _resolve_source_case_events(
    case_lib,
    *,
    domain: str,
    case_ids: list[str],
    function_profile: dict,
    failure_profile: dict,
    hazard_profile: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Resolve and normalize one controlled S3 source-case group."""
    source_cases = case_lib.resolve_source_cases(
        domain_profile=domain,
        case_ids=case_ids,
        function_profile=function_profile,
        failure_profile=failure_profile,
        hazard_profile=hazard_profile,
    )
    source_events = [case_lib.project_source_case_event(case) for case in source_cases]
    # 权威 Excel 可能把同一 source failure/hazard 组的工程字段只填在
    # 第一条非 QM 事件。仅在组内值唯一时确定性展开；冲突时失败关闭。
    for inherited_field in ("safety_goal", "safe_state", "ftti"):
        values = {
            event.get(inherited_field) for event in source_events
            if event.get(inherited_field) not in (None, "")
        }
        missing_required = [
            event for event in source_events
            if event.get("expected_asil") in {"A", "B", "C", "D"}
            and event.get(inherited_field) in (None, "")
        ]
        if missing_required and len(values) != 1:
            raise ValueError(
                f"source cases 的 {inherited_field} 无法唯一展开；"
                f"非空候选={sorted(values)}"
            )
        if missing_required:
            inherited_value = next(iter(values))
            for event in missing_required:
                event[inherited_field] = inherited_value
                event["note"] += f"；{inherited_field} 由同一 source failure 组唯一值展开"
    return source_events, source_cases


def _validate_source_case_lock(domain: str, hazards: list[dict]) -> list[tuple[str, str]]:
    """Re-resolve S3 source cases and reject edits to locked S4 events."""
    issues: list[tuple[str, str]] = []
    case_lib = get_case_library()
    locked_fields = (
        "scenario_id", "scenario", "description", "severity", "severity_reason",
        "exposure", "exposure_reason", "controllability",
        "controllability_reason", "safety_goal", "safe_state", "ftti",
        "vehicle_safety_goal_id", "vehicle_safety_goal_ids",
        "expected_asil", "sec_source", "source_refs",
    )
    for index, hazard in enumerate(hazards):
        if not hazard.get("source_case_locked"):
            continue
        try:
            failure_profile = {
                "canonical_mode": hazard.get("failure_mode"),
                "anomaly_class": case_lib.canonical_anomaly_class(
                    hazard.get("anomaly", ""), hazard.get("failure_mode", "")
                ),
            }
            hazard_profile = {
                key: hazard.get(key)
                for key in ("hazard_family", "vehicle_hazard_class")
                if hazard.get(key)
            }
            expected, _ = _resolve_source_case_events(
                case_lib,
                domain=domain,
                case_ids=hazard.get("source_case_ids") or [],
                function_profile=hazard.get("function_profile") or {},
                failure_profile=failure_profile,
                hazard_profile=hazard_profile or None,
            )
        except Exception as error:
            issues.append(("error", f"hazard[{index}] 来源案例无法重新解引用: {error}"))
            continue
        actual = hazard.get("events")
        if not isinstance(actual, list) or len(actual) != len(expected):
            issues.append((
                "error",
                f"hazard[{index}] 来源案例事件数错误：应为 {len(expected)}，"
                f"实际为 {len(actual) if isinstance(actual, list) else 0}",
            ))
            continue
        expected_ids = [event.get("source_case_id") for event in expected]
        actual_ids = [event.get("source_case_id") for event in actual if isinstance(event, dict)]
        if actual_ids != expected_ids:
            issues.append(("error", f"hazard[{index}] 来源案例事件顺序或 case_id 被修改"))
        actual_by_id = {
            event.get("source_case_id"): event for event in actual
            if isinstance(event, dict) and event.get("source_case_id")
        }
        if len(actual_by_id) != len(actual):
            issues.append(("error", f"hazard[{index}] source_case_id 缺失或重复"))
            continue
        for expected_event in expected:
            case_id = expected_event.get("source_case_id")
            event = actual_by_id.get(case_id)
            if event is None:
                issues.append(("error", f"hazard[{index}] 缺少来源案例事件 {case_id}"))
                continue
            for field in locked_fields:
                if event.get(field) != expected_event.get(field):
                    issues.append(("error", f"hazard[{index}] {case_id} 的 {field} 被修改"))
            if event.get("asil") != expected_event.get("expected_asil"):
                issues.append(("error", f"hazard[{index}] {case_id} 的 ASIL 与来源案例不一致"))
            if event.get("engineering_override") is not None:
                issues.append(("error", f"hazard[{index}] {case_id} 不允许 engineering_override"))
    return issues


def _validate_domain_pack_matrix_lock(domain: str, hazards: list[dict]) -> list[tuple[str, str]]:
    """验证精确 Pack 事件未被 Agent 修改。"""
    issues: list[tuple[str, str]] = []
    fields = (
        "scenario", "description", "severity", "severity_reason",
        "exposure", "exposure_reason", "controllability",
        "controllability_reason", "safety_goal", "safe_state", "ftti",
        "vehicle_safety_goal_id", "vehicle_safety_goal_ids",
    )
    for index, hazard in enumerate(hazards):
        if not hazard.get("risk_matrix_locked"):
            continue
        analysis_unit_id = hazard.get("analysis_unit_id")
        failure_id = hazard.get("failure_id")
        expected = get_exact_risk_matrix_events(domain, analysis_unit_id, failure_id)
        pack = load_domain_pack(domain)
        hazard_catalog = pack.get("safety_goal_catalog", {}).get("hazard_catalog", {})
        vehicle_hazard_id = hazard.get("vehicle_hazard_id")
        definition = hazard_catalog.get(vehicle_hazard_id, {}) if isinstance(hazard_catalog, dict) else {}
        if not isinstance(vehicle_hazard_id, str) or not isinstance(definition, dict):
            issues.append(("error", f"hazard[{index}] 缺少可解引用的 vehicle_hazard_id"))
        else:
            if hazard.get("hazard") != definition.get("vehicle_hazard"):
                issues.append(("error", f"hazard[{index}] 的整车危害未与 vehicle_hazard_id 目录一致"))
            if hazard.get("hazard_family") != definition.get("hazard_family"):
                issues.append(("error", f"hazard[{index}] 的 hazard_family 未与 hazard_id 目录一致"))
        if expected is None:
            issues.append(("error", f"hazard[{index}] 标记为 risk_matrix_locked 但没有 Pack 合同"))
            continue
        actual = hazard.get("events", [])
        if not isinstance(actual, list) or len(actual) != len(expected):
            issues.append(("error", f"hazard[{index}] 精确矩阵事件数错误：应为 {len(expected)}，实际为 {len(actual) if isinstance(actual, list) else 0}"))
            continue
        actual_by_id = {event.get("scenario_id"): event for event in actual if isinstance(event, dict)}
        if len(actual_by_id) != len(actual):
            issues.append(("error", f"hazard[{index}] 精确矩阵 scenario_id 缺失或重复"))
            continue
        for expected_event in expected:
            scenario_id = expected_event.get("scenario_id")
            event = actual_by_id.get(scenario_id)
            if event is None:
                issues.append(("error", f"hazard[{index}] 缺少受控场景 {scenario_id}"))
                continue
            if event.get("vehicle_hazard_id") != expected_event.get("vehicle_hazard_id") or event.get("vehicle_hazard_id") != vehicle_hazard_id:
                issues.append(("error", f"hazard[{index}] {scenario_id} 的 vehicle_hazard_id 被修改"))
            for field in fields:
                # 新版 PT Pack 可能包含整车安全目标字段；旧域 Pack 没有
                # 这些字段时保持跨域兼容，不把“缺少字段”误判为被修改。
                if field not in expected_event:
                    continue
                if event.get(field) != expected_event.get(field):
                    issues.append(("error", f"hazard[{index}] {scenario_id} 的 {field} 被修改"))
            if event.get("engineering_override") is not None:
                issues.append(("error", f"hazard[{index}] {scenario_id} 不允许 engineering_override"))
            expected_asil = expected_event.get("expected_asil")
            actual_asil = event.get("asil")
            if actual_asil != expected_asil:
                issues.append(("error", f"hazard[{index}] {scenario_id} 的 ASIL 与 Pack 不一致"))
    return issues


_COMPATIBLE_BASELINE_FIELDS = (
    "scenario", "description", "severity", "severity_reason", "exposure",
    "exposure_reason", "controllability", "controllability_reason", "safety_goal",
    "safe_state", "ftti",
)


# 车辆危害映射是工程追溯合同，而不是为了通过 validate 的可选文本字段。
# Pack 可通过 release_policy.candidate_mapping_requires_engineering_approval
# 将 candidate/unmapped 危害组限制在审核草稿阶段，禁止生成正式 S4/S5/Excel。
_MAPPING_STATUS = frozenset({
    "not_applicable",
    "exact_locked",
    "compatible_verified",
    "candidate_review",
    "unmapped",
    "engineering_approved",
})
_RELEASE_MAPPING_STATUS = frozenset({
    "exact_locked",
    "compatible_verified",
    "engineering_approved",
})


def _mapping_policy(domain: str) -> dict:
    """返回域 Pack 的正式发布映射策略；未声明的域保持既有行为。"""
    try:
        pack = load_domain_pack(domain)
    except ValueError:
        return {}
    policy = pack.get("release_policy", {})
    return policy if isinstance(policy, dict) else {}


def _initial_hazard_mapping(
    *,
    skip: bool,
    matrix_prefill: dict | None,
    compatible_prefill: dict | None,
    source_case_prefill: dict | None,
    vehicle_hazard_id: object,
    hazard_family: object,
) -> dict:
    """为 S4 骨架标明车辆危害映射的证据等级。

    该字段不会把 candidate 自动升级为正式映射；工程师批准的新映射必须
    显式写为 engineering_approved，并附可审计的 approval 信息。
    """
    if skip:
        return {"status": "not_applicable", "source": "controlled_skip", "approval": None}
    if matrix_prefill is not None:
        return {"status": "exact_locked", "source": "domain_pack_event_matrix", "approval": None}
    if compatible_prefill is not None:
        return {"status": "compatible_verified", "source": "domain_pack_compatible_baseline", "approval": None}
    if source_case_prefill is not None:
        return {"status": "exact_locked", "source": "s3_source_case_ids", "approval": None}
    if isinstance(vehicle_hazard_id, str) and vehicle_hazard_id.strip() and isinstance(hazard_family, str) and hazard_family.strip():
        return {"status": "candidate_review", "source": "s3_candidate", "approval": None}
    return {"status": "unmapped", "source": "s3_candidate", "approval": None}


def _validate_hazard_mapping_contract(domain: str, hazards: list[dict]) -> list[tuple[str, str]]:
    """校验正式 S4 对车辆危害映射的发布边界。

    仅在 Domain Pack 显式启用 candidate 映射审核闸门时阻断 candidate/unmapped；
    因此该能力可由其他域逐步启用，不会把 PT 的政策硬编码成全域特例。
    """
    policy = _mapping_policy(domain)
    if not policy.get("candidate_mapping_requires_engineering_approval"):
        return []

    issues: list[tuple[str, str]] = []
    for index, hazard in enumerate(hazards):
        if not isinstance(hazard, dict) or hazard.get("skip"):
            continue
        prefix = f"hazard[{index}] ({hazard.get('failure_id', '?')})"
        mapping = hazard.get("hazard_mapping")
        if not isinstance(mapping, dict):
            issues.append(("error", f"{prefix} 缺少 hazard_mapping，不能确认 vehicle_hazard_id 的工程来源"))
            continue
        status = mapping.get("status")
        source = mapping.get("source")
        if status not in _MAPPING_STATUS:
            issues.append(("error", f"{prefix}.hazard_mapping.status 非法: {status!r}"))
            continue
        if not isinstance(source, str) or not source.strip():
            issues.append(("error", f"{prefix}.hazard_mapping.source 必须是非空字符串"))

        action = hazard.get("agent_action")
        if action == "locked" and status != "exact_locked":
            issues.append(("error", f"{prefix} 为 exact locked 项，mapping status 必须为 exact_locked"))
        elif action == "review_compatible_baseline" and status != "compatible_verified":
            issues.append(("error", f"{prefix} 为 compatible baseline 项，mapping status 必须为 compatible_verified"))
        elif action == "create_needs_review_events" and status not in {"candidate_review", "unmapped", "engineering_approved"}:
            issues.append(("error", f"{prefix} 为 needs_review 项，禁止伪装为 exact/compatible 映射"))

        if status == "engineering_approved":
            approval = mapping.get("approval")
            if not isinstance(approval, dict):
                issues.append(("error", f"{prefix} engineering_approved 必须提供 approval 审核记录"))
            else:
                for field in ("reviewer", "approved_at", "reference"):
                    value = approval.get(field)
                    if not isinstance(value, str) or not value.strip():
                        issues.append(("error", f"{prefix}.hazard_mapping.approval 缺少 {field}"))
                if source != "engineering_review":
                    issues.append(("error", f"{prefix} engineering_approved 的 source 必须为 engineering_review"))
        elif status in {"candidate_review", "unmapped"}:
            issues.append((
                "error",
                f"{prefix} 的 vehicle_hazard 映射状态为 {status}；必须经工程审核批准后才能生成正式 S4/S5/Excel",
            ))

        events = hazard.get("events", [])
        if not isinstance(events, list):
            continue
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            event_status = event.get("mapping_status")
            event_source = event.get("mapping_source")
            if event_status != status:
                issues.append(("error", f"{prefix}.events[{event_index}] mapping_status 必须与 hazard_mapping.status 一致"))
            if event_source != source:
                issues.append(("error", f"{prefix}.events[{event_index}] mapping_source 必须与 hazard_mapping.source 一致"))
    return issues


def _needs_review_agent_task() -> dict:
    """返回 PT 未覆盖危害组的受控 Agent 任务合同。

    该合同描述 Agent 需要完成的工程判断及不可触碰的追溯边界。它不是
    自动生成事件或评定 S/E/C 的脚本：事件、场景和风险评定仍必须由当前项目
    的工程证据支撑，并由后续 validate 审核。
    """
    return {
        "task_type": "candidate_hara_event",
        "required_event_fields": [
            "scenario",
            "description",
            "severity",
            "severity_reason",
            "exposure",
            "exposure_reason",
            "controllability",
            "controllability_reason",
        ],
        "conditional_rules": {
            "severity_zero": (
                "severity=0 时 exposure/exposure_reason/controllability/"
                "controllability_reason/ASIL/安全目标/安全状态/FTTI 必须留空"
            ),
            "asil_ge_a": (
                "ASIL=A/B/C/D 时必须基于当前事件填写 safety_goal、safe_state 和 ftti"
            ),
        },
        "allowed_actions": [
            "select_current_project_scenario",
            "propose_project_specific_scenario",
            "evaluate_sec_with_rationale",
            "propose_safety_goal_for_review",
        ],
        "forbidden_actions": [
            "invent_vehicle_hazard_id",
            "change_hazard_mapping_status",
            "modify_failure_id_or_semantic_key",
            "modify_exact_or_compatible_baseline",
            "reuse_other_domain_or_legacy_project_as_fact",
            "write_runtime_repair_script",
        ],
    }


def _validate_needs_review_agent_task(hazards: list[dict]) -> list[tuple[str, str]]:
    """保证 PT needs_review 仍以受控表单交给 Agent，而非开放式 JSON 修复。"""
    issues: list[tuple[str, str]] = []
    expected = _needs_review_agent_task()
    for index, hazard in enumerate(hazards):
        if not isinstance(hazard, dict) or hazard.get("skip"):
            continue
        if hazard.get("agent_action") != "create_needs_review_events":
            continue
        prefix = f"hazard[{index}] ({hazard.get('failure_id', '?')})"
        if hazard.get("agent_task") != expected:
            issues.append((
                "error",
                f"{prefix} 缺少或改写了 create_needs_review_events 的受控 agent_task；"
                "必须使用 hara prepare 生成的任务合同，不得以临时脚本替代",
            ))
    return issues


def _validate_agent_review_contract(hazards: list[dict]) -> list[tuple[str, str]]:
    """验证 PT compatible 基线和 needs_review 项确实由 Agent 显式处理。

    compatible 不是 exact lock：允许按项目差异改动或删除基线场景，但每一处差异
    都必须有受控动作和理由。Pack 未覆盖项则必须由 Agent 生成事件并留下完成说明。
    """
    issues: list[tuple[str, str]] = []
    for index, hazard in enumerate(hazards):
        if not isinstance(hazard, dict) or hazard.get("skip"):
            continue
        action = hazard.get("agent_action")
        review = hazard.get("agent_review")
        prefix = f"hazard[{index}] ({hazard.get('failure_id', '?')})"

        if action == "review_compatible_baseline":
            if not isinstance(review, dict):
                issues.append(("error", f"{prefix} 缺少 compatible 基线的 agent_review"))
                continue
            review_status = review.get("status")
            summary = review.get("summary")
            if review_status not in {"confirmed", "adjusted"}:
                issues.append(("error", f"{prefix} compatible 基线必须将 agent_review.status 设为 confirmed 或 adjusted"))
            if not isinstance(summary, str) or not summary.strip():
                issues.append(("error", f"{prefix} compatible 基线必须填写 agent_review.summary，说明与当前项目是否一致"))

            baseline = hazard.get("compatible_baseline")
            baseline_events = baseline.get("events") if isinstance(baseline, dict) else None
            if not isinstance(baseline_events, list) or not baseline_events:
                issues.append(("error", f"{prefix} 缺少兼容基线快照，不能验证 Agent 差异"))
                continue
            baseline_by_id = {
                event.get("baseline_event_id"): event
                for event in baseline_events
                if isinstance(event, dict) and isinstance(event.get("baseline_event_id"), str)
            }
            if len(baseline_by_id) != len(baseline_events):
                issues.append(("error", f"{prefix} compatible 基线 baseline_event_id 缺失或重复"))
                continue

            changes = hazard.get("compatible_baseline_changes", [])
            if not isinstance(changes, list):
                issues.append(("error", f"{prefix}.compatible_baseline_changes 必须是数组"))
                changes = []
            changes_by_id: dict[str, dict] = {}
            has_add = False
            for change_index, change in enumerate(changes):
                change_prefix = f"{prefix}.compatible_baseline_changes[{change_index}]"
                if not isinstance(change, dict):
                    issues.append(("error", f"{change_prefix} 必须是对象"))
                    continue
                change_action = change.get("action")
                reason = change.get("difference_reason")
                if change_action not in {"modify", "remove", "add"}:
                    issues.append(("error", f"{change_prefix}.action 仅允许 modify/remove/add"))
                    continue
                if not isinstance(reason, str) or not reason.strip():
                    issues.append(("error", f"{change_prefix} 必须填写 difference_reason"))
                baseline_event_id = change.get("baseline_event_id")
                if change_action == "add":
                    has_add = True
                    if baseline_event_id not in (None, ""):
                        issues.append(("error", f"{change_prefix}: add 不得引用 baseline_event_id"))
                else:
                    if not isinstance(baseline_event_id, str) or baseline_event_id not in baseline_by_id:
                        issues.append(("error", f"{change_prefix} 引用了未知 baseline_event_id"))
                        continue
                    if baseline_event_id in changes_by_id:
                        issues.append(("error", f"{change_prefix} 与同一 baseline_event_id 的处置重复"))
                    changes_by_id[baseline_event_id] = change

            actual_events = hazard.get("events", [])
            if not isinstance(actual_events, list):
                issues.append(("error", f"{prefix}.events 必须是数组"))
                continue
            actual_by_id: dict[str, dict] = {}
            added_events = 0
            for event_index, event in enumerate(actual_events):
                event_prefix = f"{prefix}.events[{event_index}]"
                if not isinstance(event, dict):
                    issues.append(("error", f"{event_prefix} 必须是对象"))
                    continue
                baseline_event_id = event.get("baseline_event_id")
                if isinstance(baseline_event_id, str) and baseline_event_id:
                    if baseline_event_id not in baseline_by_id:
                        issues.append(("error", f"{event_prefix} 使用未知 baseline_event_id"))
                    elif baseline_event_id in actual_by_id:
                        issues.append(("error", f"{event_prefix} baseline_event_id 重复"))
                    else:
                        actual_by_id[baseline_event_id] = event
                        if event.get("evidence_status") != "compatible":
                            issues.append((
                                "error",
                                f"{event_prefix} 保留 baseline_event_id 时必须标记 evidence_status=compatible",
                            ))
                else:
                    added_events += 1
                    if event.get("evidence_status") != "project_specific":
                        issues.append(("error", f"{event_prefix} 新增事件必须标记 evidence_status=project_specific"))
                    if not isinstance(event.get("difference_reason"), str) or not event.get("difference_reason").strip():
                        issues.append(("error", f"{event_prefix} 新增事件必须填写 difference_reason"))

            changed_or_removed = False
            for baseline_event_id, baseline_event in baseline_by_id.items():
                actual = actual_by_id.get(baseline_event_id)
                change = changes_by_id.get(baseline_event_id)
                if actual is None:
                    changed_or_removed = True
                    if not change or change.get("action") != "remove":
                        issues.append(("error", f"{prefix} 删除了基线事件 {baseline_event_id}，必须声明 remove 差异和理由"))
                    continue
                if change and change.get("action") == "remove":
                    issues.append(("error", f"{prefix} {baseline_event_id} 标记 remove 但事件仍存在"))
                    continue
                changed_fields = [
                    field for field in _COMPATIBLE_BASELINE_FIELDS
                    if actual.get(field) != baseline_event.get(field)
                ]
                if changed_fields:
                    changed_or_removed = True
                    if not change or change.get("action") != "modify":
                        issues.append(("error", f"{prefix} 修改了基线事件 {baseline_event_id} 的 {changed_fields}，必须声明 modify 差异和理由"))
                elif change and change.get("action") == "modify":
                    issues.append(("error", f"{prefix} {baseline_event_id} 声明 modify 但受控字段未发生变化"))

            has_adjustment = changed_or_removed or added_events > 0 or has_add
            if review_status == "confirmed" and has_adjustment:
                issues.append(("error", f"{prefix} 有差异处置时 agent_review.status 必须为 adjusted"))
            if review_status == "adjusted" and not has_adjustment:
                issues.append(("error", f"{prefix} agent_review.status=adjusted 但未提供实际差异"))

        elif action == "create_needs_review_events":
            if not isinstance(review, dict):
                issues.append(("error", f"{prefix} needs_review 项缺少 agent_review"))
                continue
            if review.get("status") != "completed":
                issues.append(("error", f"{prefix} needs_review 项必须将 agent_review.status 设为 completed"))
            if not isinstance(review.get("summary"), str) or not review.get("summary").strip():
                issues.append(("error", f"{prefix} needs_review 项必须填写 agent_review.summary，说明推理依据"))
            events = hazard.get("events")
            if not isinstance(events, list) or not events:
                issues.append(("error", f"{prefix} needs_review 项必须至少创建 1 条 event，不能以空数组绕过分析"))
                continue
            for event_index, event in enumerate(events):
                if not isinstance(event, dict):
                    issues.append(("error", f"{prefix}.events[{event_index}] 必须是对象"))
                    continue
                if event.get("evidence_status") != "needs_review":
                    issues.append(("error", f"{prefix}.events[{event_index}] 必须标记 evidence_status=needs_review"))
    return issues


def _validate_s4_traceability(domain: str, hazards: list[dict]) -> list[tuple[str, str]]:
    """验证 S4 组/事件均可追溯到 S3 和当前域的整车危害目录。

    该检查不使用 Excel 行号或显示文本匹配：S4 必须保留分析单元、failure_id、
    vehicle_hazard_id、hazard_family 四个业务键。域 Pack 存在时，还会解引用目录
    以阻断跨域 ID 或文本被篡改。
    """
    issues: list[tuple[str, str]] = []
    catalog: dict = {}
    try:
        pack = load_domain_pack(domain)
        maybe_catalog = pack.get("safety_goal_catalog", {}).get("hazard_catalog", {})
        catalog = maybe_catalog if isinstance(maybe_catalog, dict) else {}
    except ValueError:
        # 旧域可能尚未迁入 Pack；仍强制完整字段，但无法做目录解引用。
        issues.append(("warning", f"域 {domain} 尚无可用 Domain Pack，无法解引用 vehicle_hazard_id"))

    for index, hazard in enumerate(hazards):
        if not isinstance(hazard, dict):
            issues.append(("error", f"hazard[{index}] 必须是对象"))
            continue
        prefix = f"hazard[{index}]"
        failure_id = hazard.get("failure_id")
        if not isinstance(failure_id, str) or not failure_id.strip():
            issues.append(("error", f"{prefix} 缺少 failure_id"))
        if hazard.get("skip"):
            continue

        required = ("analysis_unit_id", "vehicle_hazard_id", "hazard_family")
        for field in required:
            value = hazard.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(("error", f"{prefix} 缺少 {field}，不能建立 S3→S4→S5 追溯"))

        expected_semantic_key = build_hazard_semantic_key(
            hazard.get("analysis_unit_id"), hazard.get("failure_mode"),
            hazard.get("anomaly"), hazard.get("hazard"),
        )
        semantic_key = hazard.get("semantic_key")
        if not isinstance(semantic_key, str) or not semantic_key.strip():
            issues.append(("error", f"{prefix} 缺少 semantic_key，不能证明 S3→S4 未发生语义漂移"))
        elif semantic_key != expected_semantic_key:
            issues.append(("error", f"{prefix} semantic_key 与当前分析单元/失效模式/异常/危害不一致"))

        vehicle_hazard_id = hazard.get("vehicle_hazard_id")
        # 方案A修复：只有exact域（有预定义目录）才验证vehicle_hazard_id
        # compatible域（如PT）的vehicle_hazard_id由Agent在S3创建，无需验证
        if catalog and vehicle_hazard_id:
            definition = catalog.get(vehicle_hazard_id) if isinstance(vehicle_hazard_id, str) else None
            if not isinstance(definition, dict):
                issues.append(("error", f"{prefix} 的 vehicle_hazard_id 未在 {domain} Pack 危害目录中定义"))
            else:
                if _hazard_text := hazard.get("hazard"):
                    if _hazard_text != definition.get("vehicle_hazard"):
                        issues.append(("error", f"{prefix} 的整车危害文本未与 vehicle_hazard_id 目录一致"))
                if hazard.get("hazard_family") != definition.get("hazard_family"):
                    issues.append(("error", f"{prefix} 的 hazard_family 未与 vehicle_hazard_id 目录一致"))

        events = hazard.get("events", [])
        if not isinstance(events, list):
            continue
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                issues.append(("error", f"{prefix}.events[{event_index}] 必须是对象"))
                continue
            event_prefix = f"{prefix}.events[{event_index}]"
            for field, expected in (
                ("analysis_unit_id", hazard.get("analysis_unit_id")),
                ("failure_id", failure_id),
                ("vehicle_hazard_id", vehicle_hazard_id),
                ("hazard_family", hazard.get("hazard_family")),
            ):
                value = event.get(field)
                if not isinstance(value, str) or not value.strip():
                    issues.append(("error", f"{event_prefix} 缺少 {field}"))
                elif value != expected:
                    issues.append(("error", f"{event_prefix} 的 {field} 与所属 hazard 组不一致"))
            if event.get("semantic_key") != semantic_key:
                issues.append(("error", f"{event_prefix} semantic_key 必须与 hazard 组一致"))
    return issues




def _validate_s4_source_traceability(
    domain: str, hazards: list[dict], s3_entries: list | None,
) -> list[tuple[str, str]]:
    """Ensure formal S4 failure IDs and semantics are traceable to this run's S3."""
    policy = _mapping_policy(domain)
    requires_s3 = bool(policy.get("formal_s4_requires_s3_traceability"))
    if s3_entries is None:
        if requires_s3:
            return [("error", f"{domain} 正式 S4 发布必须提供 --s3 以校验 S3→S4 追溯")]
        return []
    if not isinstance(s3_entries, list):
        return [("error", "--s3 中 entries 必须是列表")]

    expected: set[tuple[str, str, str]] = set()
    expected_source: dict[tuple[str, str, str], dict] = {}
    for entry in s3_entries:
        if not isinstance(entry, dict):
            continue
        analysis_unit_id = entry.get("analysis_unit_id")
        failure_mode = entry.get("failure_mode")
        for anomaly in entry.get("anomalies", []):
            if not isinstance(anomaly, dict):
                continue
            failure_id = anomaly.get("failure_id")
            if not isinstance(failure_id, str) or not failure_id.strip():
                continue
            for source_hazard in anomaly.get("hazards", []):
                if not isinstance(source_hazard, dict):
                    continue
                semantic_key = build_hazard_semantic_key(
                    analysis_unit_id, failure_mode, anomaly.get("description"),
                    source_hazard.get("description"),
                )
                source_key = (str(analysis_unit_id or ""), failure_id, semantic_key)
                expected.add(source_key)
                expected_source[source_key] = {
                    "case_ids": anomaly.get("case_ids", []),
                    "source_refs": anomaly.get("source_refs", []),
                }

    issues: list[tuple[str, str]] = []
    for index, hazard in enumerate(hazards):
        if not isinstance(hazard, dict) or hazard.get("skip"):
            continue
        source_key = (
            str(hazard.get("analysis_unit_id") or ""),
            hazard.get("failure_id"),
            hazard.get("semantic_key"),
        )
        if source_key not in expected:
            issues.append((
                "error",
                f"hazard[{index}] ({hazard.get('failure_id', '?')}) 未在本轮 S3 找到相同 failure_id + semantic_key；"
                "禁止保留编号但替换异常或整车危害语义",
            ))
            continue
        if hazard.get("source_case_locked"):
            source = expected_source.get(source_key, {})
            if hazard.get("source_case_ids") != source.get("case_ids"):
                issues.append(("error", f"hazard[{index}] source_case_ids 与本轮 S3 不一致"))
            if hazard.get("source_refs") != source.get("source_refs"):
                issues.append(("error", f"hazard[{index}] source_refs 与本轮 S3 不一致"))
    return issues

def prepare(s3_path: str, output_path: str = "s4_hara_skeleton.json",
            s1_path: str = None, s2_path: str = None, intermediate_path: str = None):
    """从 s3_hazop.json 生成 HARA 骨架。

    - 读取 HAZOP entries，按 failure_id + hazard 分组
    - "不涉及"的危害标记 skip=true
    - 按 func_id 推断功能类型，预填安全状态/FTTI
    - events 留空，由 Agent 填写
    - CLI 调用必须提供 intermediate_path，验证 Domain Pack 锁定的最终 S1 合同
    - 如果提供 s1_path，检查 S1→S3 功能覆盖
    - 如果提供 s2_path，检查 S2→S3 失效模式覆盖
    """
    s3 = _load_json(s3_path, "s3_hazop")
    domain = canonical_domain_pack_code(s3.get("domain", ""))
    entries = s3.get("entries", [])
    if not domain or domain == "MULTI":
        raise ValueError("S3 缺少单一有效 canonical domain；hara prepare 暂不支持无 entry 域的 MULTI 输入")

    # S1→S2/S3 硬边界、S2 完整性及 S1→S3 覆盖检查。
    # 必须先做该检查再判断 entries 是否为空，否则 Agent 可用空 S3 绕过
    # 已锁定的 S1=是功能。
    s1 = None
    intermediate = None
    if s1_path:
        s1 = _load_json(s1_path, "s1_decisions")
        if intermediate_path:
            intermediate = _load_json(intermediate_path, "intermediate")
        if not s2_path:
            raise ValueError(
                "hara prepare 必须传入 --s2 s2_decisions.json，才能验证 S2 输出范围和完整性"
            )
        s2_for_boundary = _load_json(s2_path, "s2_decisions")
        s1_integrity_errors = validate_final_s1_against_intermediate(s1, intermediate)
        for message in s1_integrity_errors:
            print(f"[S4 prepare] [ERROR] [S1合同] {message}")
        if s1_integrity_errors:
            raise ValueError("最终 S1 合同无效，禁止生成 S4 骨架；请重新执行 merge 并修正 S1 JSON")
        s2_errors = validate_s2_decisions_against_s1(s1, s2_for_boundary, intermediate)
        s2_compatible_errors = validate_s2_compatible_baselines(s2_for_boundary.get("decisions", []))
        # Domain Pack 的 exact/compatible 合同依赖 intermediate 的 domain_context。
        # CLI 已强制提供 --intermediate；保留 Python API 的历史调用兼容性，
        # 未提供时仅执行通用 S1/S2/S3 边界检查，不能推导 Pack 合同。
        s2_pack_errors = []
        if intermediate is not None:
            s2_pack_errors = (
                validate_known_s2_contract(intermediate, s1, s2_for_boundary)
                + validate_compatible_s2_contract(intermediate, s1, s2_for_boundary)
            )
        for message in s2_errors:
            print(f"[S4 prepare] [ERROR] [S2范围] {message}")
        for message in s2_compatible_errors:
            print(f"[S4 prepare] [ERROR] [S2 compatible合同] {message}")
        for message in s2_pack_errors:
            print(f"[S4 prepare] [ERROR] [S2 Pack合同] {message}")
        if s2_errors or s2_compatible_errors or s2_pack_errors:
            raise ValueError(
                "S2 输出未通过范围/完整性、exact 锁定或 compatible 差异合同校验，禁止生成 S4 骨架；请先确认基线或登记差异"
            )
        s3_scope_errors = validate_s3_entries_against_s1_s2(
            s1, s2_for_boundary, s3, intermediate=intermediate
        )
        for message in s3_scope_errors:
            print(f"[S4 prepare] [ERROR] [S3范围] {message}")
        s3_compatible_errors = validate_s3_compatible_baselines(s3.get("entries", []))
        s3_pack_errors = []
        if intermediate is not None:
            s3_pack_errors = (
                validate_known_s3_contract(intermediate, s1, s2_for_boundary, s3)
                + validate_compatible_s3_scope_contract(intermediate, s1, s2_for_boundary, s3)
            )
        for message in s3_compatible_errors:
            print(f"[S4 prepare] [ERROR] [S3 compatible合同] {message}")
        for message in s3_pack_errors:
            print(f"[S4 prepare] [ERROR] [S3 Pack合同] {message}")
        if s3_scope_errors or s3_compatible_errors or s3_pack_errors:
            raise ValueError(
                "S3 输出未通过功能范围、模式覆盖、结构、exact 锁定或 compatible 追溯合同校验，禁止生成 S4 骨架；请先确认基线或登记差异"
            )

        boundary_errors = validate_s1_downstream_exclusion(
            s1, s2_for_boundary, entries
        )
        for msg in boundary_errors:
            print(f"[S4 prepare] [ERROR] {msg}")
        if boundary_errors:
            raise ValueError(
                "S1=否 功能已进入 S2/S3，禁止生成 S4 骨架；请先修正下游 JSON"
            )

        coverage_issues = validate_s1_coverage(s1, entries)
        for level, msg in coverage_issues:
            tag = "ERROR" if level == "error" else "WARN"
            print(f"[S4 prepare] [{tag}] {msg}")
        if any(level == "error" for level, _ in coverage_issues):
            raise ValueError(
                "S1→S3 覆盖检查失败，不能生成 S4 骨架；请补全 s3_hazop.json"
            )
    else:
        raise ValueError(
            "hara prepare 必须传入 --s1 s1_decisions.json，才能执行 S1 下游硬边界检查"
        )

    if not entries:
        print("[S4] 警告: s3_hazop.json 中无 entries")
        return

    # S2→S3 失效模式覆盖检查
    if s2_path:
        s2 = _load_json(s2_path, "s2_decisions")
        mode_issues = validate_s2_s3_mode_coverage(
            s2, entries, include_generic=False
        )
        for level, msg in mode_issues:
            tag = "ERROR" if level == "error" else "WARN"
            print(f"[S4 prepare] [{tag}] {msg}")

    # failure_id 格式校验
    fid_issues = validate_failure_id_format(entries)
    for level, msg in fid_issues:
        tag = "ERROR" if level == "error" else "WARN"
        print(f"[S4 prepare] [{tag}] {msg}")

    # 异常/危害描述字数校验
    desc_issues = validate_description_length(entries)
    for level, msg in desc_issues:
        tag = "ERROR" if level == "error" else "WARN"
        print(f"[S4 prepare] [{tag}] {msg}")

    # 收集功能配置
    func_configs = {}  # {func_id: {...}}
    hazards = []
    hazard_counter = {}  # {failure_id: 序号}
    lookup_hits = 0  # 场景速查表/案例库命中计数
    case_lib = get_case_library()

    for entry in entries:
        func_id = entry.get("func_id", "")
        func_name = entry.get("func_name", "")
        failure_mode = entry.get("failure_mode", "")

        # 功能配置（每个 func_id 只处理一次）
        if func_id not in func_configs:
            ftype = infer_function_type(func_name)
            function_profile = entry.get("function_profile")
            if not isinstance(function_profile, dict) or not function_profile.get("canonical_function_family"):
                function_profile = case_lib.build_function_profile(
                    domain,
                    function_name=func_name,
                    semantic_texts=entry.get("function_semantic_texts", []),
                    analysis_unit_id=entry.get("analysis_unit_id", ""),
                )
            config = {
                "func_id": func_id,
                "func_name": func_name,
                "function_type": ftype,
                "function_profile": function_profile,
                "safe_state_default": get_safe_state(ftype) if ftype else None,
                "ftti_by_mode": {},
            }
            # 预填该功能所有可能失效模式的 FTTI
            if ftype:
                # 从 entries 收集该功能的所有失效模式
                for e2 in entries:
                    if e2.get("func_id") == func_id:
                        fm = e2.get("failure_mode", "")
                        if fm and fm not in config["ftti_by_mode"]:
                            config["ftti_by_mode"][fm] = get_ftti(ftype, fm)
            func_configs[func_id] = config

        # 遍历异常和危害
        for anom in entry.get("anomalies", []):
            failure_id = anom.get("failure_id", "")
            anom_desc = anom.get("description", "")

            # 支持 hazards 列表和旧格式 hazard 字符串
            hazards_list = anom.get("hazards")
            if hazards_list is None:
                h = anom.get("hazard", "")
                ah = anom.get("associated_hara", "")
                hazards_list = [{"description": h, "associated_hara": ah}]

            for hz in hazards_list:
                hz_desc = hz.get("description", "")
                ah = hz.get("associated_hara", "")

                # 生成 hazard_key
                if failure_id not in hazard_counter:
                    hazard_counter[failure_id] = 0
                hazard_counter[failure_id] += 1
                hazard_key = f"{failure_id}__{hazard_counter[failure_id]}"

                # 只有 S3 已明确声明“不涉及/无整车层面危害”时才允许 skip。
                # 空文本、模糊“无”以及 QM/S=0 不是 skip。
                skip_meta = _skip_metadata(hz_desc, ah, hz)
                skip = skip_meta is not None

                # 推断危害方向，预填安全状态和 FTTI
                ftype = func_configs[func_id]["function_type"]
                hazard_category = infer_hazard_category(ftype, anom_desc, hz_desc) if ftype else None
                pre_safe_state = get_safe_state(ftype, hazard_category) if ftype else None
                pre_ftti = get_ftti(ftype, failure_mode) if ftype else None

                # PT 优先使用 Domain Pack 精确/compatible 合同；未命中时只允许
                # 案例库 v2 的跨项目语义候选，不得退化到旧场景总表、全局 SEC
                # 数据库或其他域硬编码。similar/template 始终保持可编辑。
                pt_pack_controlled = canonical_domain_pack_code(domain) == "PT"

                # Domain Pack 精确矩阵优先级最高。命中时完全跳过跨域场景表、
                # SEC 参考库和硬编码规则，避免不必要的加载与近似匹配。
                matrix_prefill = None if skip else _domain_pack_matrix_prefill(domain, entry, anom)
                compatible_prefill = None if (skip or matrix_prefill is not None) else _domain_pack_compatible_prefill(domain, entry, anom)

                # ---- PT/其他域统一使用案例库 v2 语义预填 ----
                # Domain Pack 的 exact/compatible 合同已经在上方优先处理。
                # 未命中时，所有域都只通过各自的 v2 案例库做语义候选匹配；
                # 不再使用旧 PT.json 的功能名/来源 ID 作为匹配键。
                case_library_prefill = None
                source_case_prefill = None
                if not skip and matrix_prefill is None and compatible_prefill is None:
                    try:
                        function_profile = func_configs[func_id].get("function_profile", {})
                        failure_profile = anom.get("failure_profile")
                        if not isinstance(failure_profile, dict):
                            failure_profile = {}
                        failure_profile = {
                            "canonical_mode": failure_profile.get("canonical_mode") or failure_mode,
                            "anomaly_class": failure_profile.get("anomaly_class")
                            or anom.get("anomaly_class")
                            or case_lib.canonical_anomaly_class(anom_desc, failure_mode),
                        }
                        hazard_profile = hz.get("hazard_profile")
                        if not isinstance(hazard_profile, dict):
                            hazard_profile = {}
                        if not hazard_profile:
                            for key in ("hazard_family", "vehicle_hazard_class"):
                                value = hz.get(key) or anom.get(key)
                                if value:
                                    hazard_profile[key] = value

                        # S3 controlled draft 已经从同一运行时案例库选定 case_ids 时，
                        # S4 只做来源解引用与语义复核，不得丢弃来源后重新进行无场景
                        # 搜索。后者按匹配合同最多只能得到 similar，会错误要求 Agent
                        # 重做案例库已有的场景与 S/E/C。
                        source_case_ids = anom.get("case_ids")
                        if entry.get("source") == "case_library" and isinstance(source_case_ids, list) and source_case_ids:
                            source_events, source_cases = _resolve_source_case_events(
                                case_lib,
                                domain=domain,
                                case_ids=source_case_ids,
                                function_profile=function_profile,
                                failure_profile=failure_profile,
                                hazard_profile=hazard_profile or None,
                            )
                            source_case_prefill = {
                                "scenarios": [
                                    {**event, "case_library_v2": True}
                                    for event in source_events
                                ],
                                "guidance": (
                                    "S3 已从同一 PT 运行时案例库锁定 source_case_ids；"
                                    "S4 已完整解引用案例事件，Agent 不得改写。"
                                ),
                                "match_type": "exact",
                                "match_confidence": 1.0,
                                "matched_by": ["s3_source_case_ids", "semantic_profile_recheck"],
                                "prefill_candidates": [
                                    case_lib.project_case_candidate(case) for case in source_cases
                                ],
                                "prefill_reason": "受控 S3→S4 来源案例交接",
                            }
                            lookup_hits += 1
                        else:
                            scenario_profile = (
                                hz.get("scenario_profile")
                                or anom.get("scenario_profile")
                                or entry.get("scenario_profile")
                            )
                            if not isinstance(scenario_profile, dict):
                                scenario_text = hz.get("scenario") or anom.get("scenario") or entry.get("scenario")
                                scenario_profile = {"scenario_text": scenario_text} if scenario_text else None

                            match = case_lib.match_cases(
                                domain_profile=domain,
                                function_profile=function_profile,
                                failure_profile=failure_profile,
                                hazard_profile=hazard_profile or None,
                                scenario_profile=scenario_profile,
                            )
                            if match.match_type in ("exact", "similar", "template"):
                                prefill_data = case_lib.get_prefill_data(match)
                                if match.match_type == "exact":
                                    scenario = prefill_data.get("scenario") or ""
                                    severity = prefill_data.get("severity")
                                    severity_reason = prefill_data.get("severity_reason")
                                    exposure = prefill_data.get("exposure")
                                    exposure_reason = prefill_data.get("exposure_reason")
                                    controllability = prefill_data.get("controllability")
                                    controllability_reason = prefill_data.get("controllability_reason")
                                    safety_goal = prefill_data.get("safety_goal")
                                elif match.match_type == "similar":
                                    scenario = prefill_data.get("reference_scenario") or ""
                                    severity = prefill_data.get("severity")
                                    severity_reason = prefill_data.get("severity_reason")
                                    exposure = prefill_data.get("exposure")
                                    exposure_reason = prefill_data.get("exposure_reason")
                                    controllability = prefill_data.get("controllability")
                                    controllability_reason = prefill_data.get("controllability_reason")
                                    safety_goal = prefill_data.get("safety_goal")
                                else:
                                    scenario = ""
                                    severity = exposure = controllability = None
                                    severity_reason = exposure_reason = controllability_reason = ""
                                    safety_goal = prefill_data.get("safety_goal_template")

                                case_library_prefill = {
                                    "safe_state": prefill_data.get("safe_state") or prefill_data.get("safe_state_template"),
                                    "ftti": prefill_data.get("ftti") or prefill_data.get("ftti_template"),
                                    "scenarios": [{
                                        "scenario": scenario,
                                        "description": (
                                            prefill_data.get("description") or ""
                                            if match.match_type == "exact"
                                            else (
                                                f"参考场景: {prefill_data.get('reference_scenario', '')}"
                                                if match.match_type == "template" else ""
                                            )
                                        ),
                                        "severity": severity,
                                        "severity_reason": severity_reason,
                                        "exposure": exposure,
                                        "exposure_reason": exposure_reason,
                                        "controllability": controllability,
                                        "controllability_reason": controllability_reason,
                                        "safety_goal": safety_goal,
                                        "case_library_v2": True,
                                    }],
                                    "guidance": f"案例库 v2 {match.match_type} 匹配: {match.reason}",
                                    "match_type": match.match_type,
                                    "match_confidence": match.confidence,
                                    "matched_by": list(match.matched_by),
                                    "prefill_candidates": prefill_data.get("prefill_candidates", []),
                                    "prefill_reason": prefill_data.get("prefill_reason", match.reason),
                                }
                                lookup_hits += 1
                    except Exception as error:
                        # S3 声明了来源 case_ids 时必须失败关闭，不能静默降级到
                        # similar/Agent 推理，否则会掩盖案例资产损坏或链路篡改。
                        if entry.get("source") == "case_library" and anom.get("case_ids"):
                            raise ValueError(
                                f"S3→S4 来源案例交接失败 {func_id}/{failure_id}: {error}"
                            ) from error
                        print(f"[S4 prepare] 案例库 v2 匹配失败: {error}")
                        case_library_prefill = None

                # ---- 第一级：查当前旧场景速查表（仅旧域或尚未迁入 Pack 的 PT 项）----
                prefill = source_case_prefill or case_library_prefill or (
                    None if (skip or pt_pack_controlled or matrix_prefill is not None or compatible_prefill is not None) else lookup_scenarios(
                        domain, func_name, failure_mode, anom_desc
                    )
                )
                if prefill and not case_library_prefill:
                    lookup_hits += 1

                # ---- 第二级：查硬编码功能类型（仅无精确矩阵合同的域）----
                if not prefill and not skip and not pt_pack_controlled and matrix_prefill is None and compatible_prefill is None and ftype:
                    prefill = get_prefill_data(ftype, anom_desc, failure_mode, hz_desc)

                prefill_scenarios = []
                prefill_locked = False
                prefill_guidance = None
                prefilled_events = []

                if prefill:
                    prefill_locked = not (
                        prefill.get("match_type") in {"similar", "template"}
                    )
                    prefill_guidance = prefill.get("guidance")
                    # 预填值优先，None 也是有效值（如 S=0 场景无安全状态）
                    if "safe_state" in prefill:
                        pre_safe_state = prefill["safe_state"]
                    if "ftti" in prefill:
                        pre_ftti = prefill["ftti"]

                    # 预填事件（S/E/C + 理由 + description 从参考数据库精确匹配）
                    if prefill.get("scenarios"):
                        sec_suggestions = prefill.get("sec_suggestions")
                        db_hit_count = 0
                        for idx, sc in enumerate(prefill.get("scenarios", [])):
                            # v2 案例场景携带已审计的语义匹配值；不得再被旧 SEC
                            # 文本表覆盖。旧路径仍支持字符串/tuple 场景。
                            direct_case = isinstance(sc, dict) and sc.get("case_library_v2") is True
                            if direct_case:
                                sc_text = sc.get("scenario") or ""
                                sg_text = sc.get("safety_goal")
                                sug_s = sc.get("severity")
                                sug_e = sc.get("exposure")
                                sug_c = sc.get("controllability")
                                sug_s_reason = sc.get("severity_reason")
                                sug_e_reason = sc.get("exposure_reason")
                                sug_c_reason = sc.get("controllability_reason")
                                sug_desc = sc.get("description") or ""
                                sec_source = f"case_library_{prefill.get('match_type', 'semantic')}"
                            elif isinstance(sc, tuple):
                                sc_text, sg_text = sc
                                sug_s = sug_e = sug_c = None
                                sug_s_reason = sug_e_reason = sug_c_reason = None
                                sug_desc = None
                                sec_source = "agent_evaluated"
                            else:
                                sc_text = sc
                                sgs = prefill.get("scenario_safety_goals")
                                if sgs and idx < len(sgs):
                                    sg_text = sgs[idx]
                                else:
                                    sg_text = prefill.get("safety_goal")
                                sug_s = sug_e = sug_c = None
                                sug_s_reason = sug_e_reason = sug_c_reason = None
                                sug_desc = None
                                sec_source = "agent_evaluated"

                            prefill_scenarios.append(sc_text)

                            if not direct_case:
                                # 旧路径：优先从参考数据库精确匹配，再退化为模式建议。
                                sec_ref = lookup_sec_for_scenario(sc_text, anom_desc)
                                if sec_ref:
                                    db_hit_count += 1
                                    sug_s = sec_ref.get("s")
                                    sug_e = sec_ref.get("e")
                                    sug_c = sec_ref.get("c")
                                    sug_s_reason = sec_ref.get("s_reason")
                                    sug_e_reason = sec_ref.get("e_reason")
                                    sug_c_reason = sec_ref.get("c_reason")
                                    sug_desc = sec_ref.get("description")
                                    sec_source = "reference_exact"
                                else:
                                    if sec_suggestions and idx < len(sec_suggestions):
                                        sug_s, sug_e, sug_c = sec_suggestions[idx]
                                    sec_source = "reference_pattern" if sec_suggestions else "agent_evaluated"

                            prefilled_events.append({
                                "scenario": sc_text,
                                "description": sug_desc or "",
                                "severity": sug_s,
                                "severity_reason": sug_s_reason,
                                "exposure": sug_e,
                                "exposure_reason": sug_e_reason,
                                "controllability": sug_c,
                                "controllability_reason": sug_c_reason,
                                "safety_goal": sg_text,
                                "safe_state": sc.get("safe_state") if direct_case else prefill.get("safe_state"),
                                "ftti": sc.get("ftti") if direct_case else prefill.get("ftti"),
                                "sec_source": sec_source,
                                "note": prefill.get("prefill_reason"),
                                "engineering_override": None,
                                "source_case_id": sc.get("source_case_id") if direct_case else None,
                                "vehicle_safety_goal_id": sc.get("vehicle_safety_goal_id") if direct_case else None,
                                "source_refs": deepcopy(sc.get("source_refs") or []) if direct_case else [],
                                "scenario_id": sc.get("scenario_id") if direct_case else None,
                                "expected_asil": sc.get("expected_asil") if direct_case else None,
                            })

                        if db_hit_count > 0:
                            print(f"[S4 prepare]   {db_hit_count}/{len(prefill.get('scenarios', []))} 场景从参考数据库精确匹配")

                # compatible 基线优先于旧大表/硬编码，但 Agent 可以按项目差异修改。
                if compatible_prefill is not None:
                    prefill_guidance = "由 PT Domain Pack 的 compatible 参考基线预填；只允许按当前输入与工程项目差异修改，并在备注说明。"
                    prefilled_events = compatible_prefill["events"]
                    prefill_scenarios = [event["scenario"] for event in prefilled_events]
                elif pt_pack_controlled and not skip and not case_library_prefill:
                    prefill_guidance = (
                        "PT Domain Pack 尚无该 failure_id 的 compatible 场景/S/E/C 基线；"
                        "必须由 Agent 按当前 PT 输入推理并标注 needs_review，禁止套用旧场景表或其他域资产。"
                    )

                # 精确矩阵在旧查询之前已完成解析；此处只将其规范化为 S4 受控字段。
                if matrix_prefill is not None:
                    prefill_locked = True
                    prefill_guidance = "由 Domain Pack 精确风险矩阵锁定；Agent 不得修改 S/E/C、理由、SG、安全状态或 FTTI。"
                    pre_safe_state = matrix_prefill["safe_state"]
                    pre_ftti = matrix_prefill["ftti"]
                    prefilled_events = matrix_prefill["events"]
                    prefill_scenarios = [event["scenario"] for event in prefilled_events]

                resolved_analysis_unit_id = (
                    matrix_prefill["analysis_unit_id"] if matrix_prefill else entry.get("analysis_unit_id")
                )
                resolved_vehicle_hazard_id = (
                    matrix_prefill.get("vehicle_hazard_id")
                    if matrix_prefill else (anom.get("vehicle_hazard_id") or hz.get("vehicle_hazard_id"))
                )
                resolved_hazard_family = (
                    matrix_prefill["hazard_family"] if matrix_prefill else anom.get("hazard_family")
                )
                semantic_key = build_hazard_semantic_key(
                    resolved_analysis_unit_id, failure_mode, anom_desc, hz_desc,
                )
                hazard_mapping = _initial_hazard_mapping(
                    skip=skip,
                    matrix_prefill=matrix_prefill,
                    compatible_prefill=compatible_prefill,
                    source_case_prefill=source_case_prefill,
                    vehicle_hazard_id=resolved_vehicle_hazard_id,
                    hazard_family=resolved_hazard_family,
                )

                hazard_group = {
                    "hazard_key": hazard_key,
                    "func_id": func_id,
                    "func_name": func_name,
                    "failure_id": failure_id,
                    "failure_mode": failure_mode,
                    "anomaly": anom_desc,
                    "domain": domain,
                    "function_profile": func_configs[func_id].get("function_profile", {}),
                    "hazard": hz_desc,
                    "semantic_key": semantic_key,
                    "vehicle_hazard_id": resolved_vehicle_hazard_id,
                    "hazard_mapping": hazard_mapping,
                    "skip": skip,
                    "skip_reason_code": skip_meta.get("skip_reason_code") if skip_meta else None,
                    "skip_reason": skip_meta.get("skip_reason") if skip_meta else None,
                    "covered_by": skip_meta.get("covered_by") if skip_meta else None,
                    "transfer_to": skip_meta.get("transfer_to") if skip_meta else None,
                    "hara_applicability": hz.get("hara_applicability") or (
                        "not_applicable" if skip else "applicable"
                    ),
                    "reason_code": hz.get("reason_code") or (
                        "NO_VEHICLE_LEVEL_HAZARD" if skip else None
                    ),
                    "case_set_id": entry.get("case_set_id"),
                    "source_function_key": entry.get("source_function_key"),
                    "source_failure_id": anom.get("source_failure_id"),
                    "source_case_ids": anom.get("case_ids", []),
                    "source_refs": anom.get("source_refs", []),
                    "safe_state_prefill": pre_safe_state,
                    "ftti_prefill": pre_ftti,
                    "prefill_locked": prefill_locked,
                    "prefill_scenarios": prefill_scenarios,
                    "prefill_guidance": prefill_guidance,
                    "prefill_source": (
                        "case_library_exact" if source_case_prefill else (
                            f"case_library_{case_library_prefill.get('match_type')}"
                            if case_library_prefill else ("domain_pack_exact" if matrix_prefill else None)
                        )
                    ),
                    "prefill_confidence": (
                        source_case_prefill.get("match_confidence") if source_case_prefill
                        else (case_library_prefill.get("match_confidence") if case_library_prefill else None)
                    ),
                    "matched_by": (
                        source_case_prefill.get("matched_by", []) if source_case_prefill
                        else (case_library_prefill.get("matched_by", []) if case_library_prefill else [])
                    ),
                    "prefill_candidates": (
                        source_case_prefill.get("prefill_candidates", []) if source_case_prefill
                        else (case_library_prefill.get("prefill_candidates", []) if case_library_prefill else [])
                    ),
                    "source_case_locked": source_case_prefill is not None,
                    "analysis_unit_id": resolved_analysis_unit_id,
                    "risk_matrix_locked": matrix_prefill is not None,
                    "hazard_family": resolved_hazard_family,
                    # 所有活跃 event 都继承可追溯键；skip 组强制为空，防止
                    # “skip=true 但实际仍携带事件”在 Excel 展示层被静默丢弃。
                    "events": [] if skip else [
                        {
                            **event,
                            "analysis_unit_id": resolved_analysis_unit_id,
                            "failure_id": failure_id,
                            "semantic_key": semantic_key,
                            "vehicle_hazard_id": resolved_vehicle_hazard_id,
                            "hazard_family": resolved_hazard_family,
                            "mapping_status": hazard_mapping["status"],
                            "mapping_source": hazard_mapping["source"],
                        }
                        for event in prefilled_events
                    ],
                }

                if skip:
                    hazard_group["evidence_status"] = "not_applicable"
                    hazard_group["agent_action"] = "none"
                elif matrix_prefill is not None:
                    hazard_group["evidence_status"] = "exact_known"
                    hazard_group["agent_action"] = "locked"
                elif source_case_prefill is not None:
                    hazard_group["evidence_status"] = "exact_known"
                    hazard_group["agent_action"] = "locked"
                    hazard_group["source_case_baseline"] = {
                        "case_ids": [event.get("source_case_id") for event in hazard_group["events"]],
                        "events": deepcopy(hazard_group["events"]),
                    }
                elif compatible_prefill is not None:
                    # compatible 是可用基线而非锁定结论。给每条基线一个稳定 ID，
                    # 将初始快照随骨架保存，validate 才能识别无理由的修改/删除。
                    for event_index, event in enumerate(hazard_group["events"], start=1):
                        event["baseline_event_id"] = f"{hazard_key}__B{event_index:02d}"
                        event["evidence_status"] = "compatible"
                        event["difference_reason"] = None
                    hazard_group.update({
                        "evidence_status": "compatible",
                        "agent_action": "review_compatible_baseline",
                        "agent_review": {
                            "required": True,
                            "status": "pending",
                            "summary": None,
                        },
                        "compatible_baseline": {"events": deepcopy(hazard_group["events"])},
                        "compatible_baseline_changes": [],
                    })
                elif pt_pack_controlled:
                    # Pack 未覆盖时绝不套用旧表。Agent 必须创建 PT 项目专属事件，
                    # 并显式留下 needs_review 的完成说明。
                    hazard_group.update({
                        "evidence_status": "needs_review",
                        "agent_action": "create_needs_review_events",
                        "agent_task": _needs_review_agent_task(),
                        "agent_review": {
                            "required": True,
                            "status": "pending",
                            "summary": None,
                        },
                    })
                else:
                    hazard_group["evidence_status"] = "unknown"
                    hazard_group["agent_action"] = "complete_unlocked_events"
                hazards.append(hazard_group)

    skeleton = {
        "domain": domain,
        "functions": list(func_configs.values()),
        "hazards": hazards,
    }

    _save_json(skeleton, output_path)

    # 统计
    total = len(hazards)
    skipped = sum(1 for h in hazards if h["skip"])
    active = total - skipped
    n_funcs = len(func_configs)
    unknown_types = [f["func_id"] for f in func_configs.values() if not f["function_type"]]
    locked_groups = sum(
        1 for hazard in hazards
        if not hazard.get("skip") and hazard.get("agent_action") == "locked"
    )
    functions_requiring_agent_defaults = [
        func_id for func_id in unknown_types
        if any(
            not hazard.get("skip")
            and hazard.get("func_id") == func_id
            and hazard.get("agent_action") != "locked"
            for hazard in hazards
        )
    ]
    prefilled = sum(1 for h in hazards if h.get("events"))
    prefilled_events = sum(len(h.get("events", [])) for h in hazards if h.get("events"))
    needs_review_groups = [
        h for h in hazards
        if not h.get("skip") and h.get("agent_action") == "create_needs_review_events"
    ]
    case_candidate_groups = [
        h for h in needs_review_groups
        if str(h.get("prefill_source") or "").startswith("case_library_")
    ]
    no_prefill = [
        h["failure_id"] for h in needs_review_groups
        if h not in case_candidate_groups
    ]

    print(f"[S4 prepare] 域: {domain}")
    print(f"[S4 prepare] 功能: {n_funcs} 个, 危害组: {total} 个（跳过 {skipped}, 活跃 {active}）")
    matrix_locked = sum(1 for hazard in hazards if hazard.get("risk_matrix_locked"))
    compatible_groups = sum(1 for hazard in hazards if hazard.get("agent_action") == "review_compatible_baseline")
    compatible_events = sum(
        len(hazard.get("events", []))
        for hazard in hazards
        if hazard.get("agent_action") == "review_compatible_baseline"
    )
    is_pt_pack = canonical_domain_pack_code(domain) == "PT"
    if is_pt_pack:
        print(
            f"[S4 prepare] PT Pack 基线: {compatible_groups} 个 compatible 危害组, "
            f"{compatible_events} 条基线事件"
            + ("；Agent 必须确认一致性或逐项登记差异。" if compatible_groups else "。")
        )
        print(
            f"[S4 prepare] PT Pack 未覆盖: {len(needs_review_groups)} 个危害组；"
            f"其中案例库 v2 候选 {len(case_candidate_groups)} 个，空白待创建 {len(no_prefill)} 个。"
        )
        if matrix_locked:
            print(f"[S4 prepare] PT Pack 精确锁定: {matrix_locked} 个危害组，Agent 不得编辑。")
    else:
        print(
            f"[S4 prepare] 代码预填: {prefilled} 个危害组, {prefilled_events} 个标准场景已锁定"
            f"（旧速查表命中 {lookup_hits} 个，精确矩阵危害组 {matrix_locked} 个）"
        )

    # 统计参考数据库命中
    db_exact = sum(
        1 for h in hazards if h.get("events")
        for e in h["events"] if e.get("sec_source") == "reference_exact"
    )
    db_pattern = sum(
        1 for h in hazards if h.get("events")
        for e in h["events"] if e.get("sec_source") == "reference_pattern"
    )
    db_agent = sum(
        1 for h in hazards if h.get("events")
        for e in h["events"] if e.get("sec_source") == "agent_evaluated"
    )
    case_exact = sum(
        1 for h in hazards if h.get("events")
        for e in h["events"] if e.get("sec_source") == "case_library_exact"
    )
    case_similar = sum(
        1 for h in hazards if h.get("events")
        for e in h["events"] if e.get("sec_source") == "case_library_similar"
    )
    case_template = sum(
        1 for h in hazards if h.get("events")
        for e in h["events"] if e.get("sec_source") == "case_library_template"
    )
    total_events = db_exact + db_pattern + db_agent + case_exact + case_similar + case_template
    if total_events > 0:
        print(
            "[S4 prepare] S/E/C/候选预填: "
            f"案例库 exact/similar/template={case_exact}/{case_similar}/{case_template}, "
            f"旧参考 exact/pattern={db_exact}/{db_pattern}, Agent评定={db_agent} "
            f"(共 {total_events} 事件)"
        )
    if functions_requiring_agent_defaults:
        print(
            f"[S4 prepare] 警告: {len(functions_requiring_agent_defaults)} 个功能未识别类型，"
            f"且仍有未锁定事件，安全状态/FTTI 需 Agent 评定: {functions_requiring_agent_defaults}"
        )
    if no_prefill and not is_pt_pack:
        print(f"[S4 prepare] 警告: {len(no_prefill)} 个危害组未匹配标准场景集，Agent 需自行展开: {no_prefill[:5]}")
    if active and locked_groups == active:
        print(
            "[S4 prepare] 所有活跃危害均由 Domain Pack 精确矩阵或 S3 来源案例锁定；"
            "Agent 不得编辑，可直接执行 hara validate。"
        )
    elif is_pt_pack:
        print(
            "[S4 prepare] PT 处理规则：compatible 基线不得静默改写；"
            "案例库 similar/template 只能作为可编辑候选；无候选项才需新建事件，"
            "所有 needs_review 项均须完成工程说明。"
        )
    else:
        print("[S4 prepare] 非精确矩阵覆盖的危害仍需 Agent 依据规则填写；已锁定字段不得修改。")


# ============================================================
# autofill: deterministic S4 trace-field completion (does not make engineering decisions)
# ============================================================

def autofill(s4_path: str, output_path: str = "s4_hara_autofilled.json") -> dict:
    """Fill only deterministic S4 inheritance fields into an Agent-edited draft.

    This command deliberately does *not* create events, choose scenarios, assign
    S/E/C, map a candidate hazard to a formal catalogue ID, or invent SG/FTTI.
    Those remain engineering decisions. It replaces unsafe ad-hoc Agent scripts
    that previously copied trace fields inconsistently across S4 events.
    """
    s4 = _load_json(s4_path, "s4_hara")
    domain = s4.get("domain", "X")
    hazards = s4.get("hazards")
    if not isinstance(hazards, list):
        raise ValueError("s4_hara.json 缺少 hazards 列表")

    filled_events = 0
    initialized_mappings = 0
    for hazard in hazards:
        if not isinstance(hazard, dict):
            continue
        action = hazard.get("agent_action")
        mapping = hazard.get("hazard_mapping")
        if not isinstance(mapping, dict):
            has_mapping_fields = all(
                isinstance(hazard.get(field), str) and hazard[field].strip()
                for field in ("vehicle_hazard_id", "hazard_family")
            )
            if hazard.get("skip"):
                status, source = "not_applicable", "controlled_skip"
            elif action == "locked":
                status, source = "exact_locked", "domain_pack_event_matrix"
            elif action == "review_compatible_baseline":
                status, source = "compatible_verified", "domain_pack_compatible_baseline"
            elif has_mapping_fields:
                status, source = "candidate_review", "s3_candidate"
            else:
                status, source = "unmapped", "s3_candidate"
            mapping = {"status": status, "source": source, "approval": None}
            hazard["hazard_mapping"] = mapping
            initialized_mappings += 1

        semantic_key = build_hazard_semantic_key(
            hazard.get("analysis_unit_id"), hazard.get("failure_mode"),
            hazard.get("anomaly"), hazard.get("hazard"),
        )
        if not isinstance(hazard.get("semantic_key"), str) or not hazard["semantic_key"].strip():
            hazard["semantic_key"] = semantic_key

        events = hazard.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            for field in ("analysis_unit_id", "failure_id", "vehicle_hazard_id", "hazard_family", "semantic_key"):
                if event.get(field) in (None, ""):
                    event[field] = hazard.get(field)
                    filled_events += 1
            if event.get("mapping_status") in (None, ""):
                event["mapping_status"] = mapping.get("status")
                filled_events += 1
            if event.get("mapping_source") in (None, ""):
                event["mapping_source"] = mapping.get("source")
                filled_events += 1
            if action == "create_needs_review_events" and event.get("evidence_status") in (None, ""):
                event["evidence_status"] = "needs_review"
                filled_events += 1

    s4["autofill"] = {
        "version": "1.0",
        "scope": "deterministic_trace_fields_only",
        "filled_event_fields": filled_events,
        "initialized_hazard_mappings": initialized_mappings,
        "note": (
            "未创建事件，未修改场景/S/E/C/工程安全目标，未将 candidate/unmapped 映射升级为正式危害目录。"
        ),
    }
    _save_json_atomic(s4, output_path)
    print(
        f"[S4 autofill] 域: {domain}; 补齐 event 追溯字段 {filled_events} 个，"
        f"初始化 hazard_mapping {initialized_mappings} 组。"
    )
    return s4


# ============================================================
# validate: 验证 Agent 填写的 s4_hara.json，分配 ID 和 ASIL
# ============================================================

def validate(s4_path: str, output_path: str = "s4_hara_final.json",
             s3_path: str = None):
    """验证 Agent 填写的 HARA 数据，分配 ID/ASIL/SG ID。

    1. 为所有事件分配 hazard_id（A列连续编号）
    2. 计算 ASIL（S=0→ASIL 留空；C=0→QM；其余查表）
    3. 为 ASIL≥A 的事件分配 safety_goal_id
    4. 执行完整性/一致性验证
    5. 输出最终 JSON 和验证报告
    """
    s4 = _load_json(s4_path, "s4_hara")
    domain = s4.get("domain", "X")
    hazards = s4.get("hazards", [])

    s3_entries = None
    if s3_path:
        s3 = _load_json(s3_path, "s3_hazop")
        s3_entries = s3.get("entries", [])

    if not hazards:
        message = "s4_hara.json 中无 hazards，不能生成正式 final 文件"
        _mark_validation_failure(output_path, message)
        raise S4ValidationError(message)

    # ---- 0. 清除 Agent/旧版本可能注入的派生字段 ----
    # ASIL、hazard_id、safety_goal_id 和 id_range 必须由本次 S4 重新生成，
    # 不能信任草稿或历史 final 中残留的值。
    clear_derived_hara_fields(hazards)

    # ---- 1. 计算 ASIL ----
    footnote_events = []
    asil_errors = []
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        for event in hazard.get("events", []):
            s = event.get("severity")
            e = event.get("exposure")
            c = event.get("controllability")
            override = event.get("engineering_override")

            if s is None:
                continue

            try:
                asil, footnote = compute_asil(s, e, c)
            except ValueError as exc:
                # 将非法 S/E/C 组合纳入统一质量闸门，而不是让 CLI 直接
                # traceback；validate_event 随后还会给出具体字段范围错误。
                asil = None
                footnote = False
                asil_errors.append((
                    "error",
                    f"hazard({hazard.get('failure_id', '?')}): event 的 ASIL 计算失败：{exc}",
                ))

            # 工程判断覆盖
            if override == "QM" and s != 0:
                asil = "QM"

            event["asil"] = asil

            if footnote:
                footnote_events.append(event.get("hazard_id", "?"))

    # ---- 2. 分配 hazard_id ----
    id_map, range_map = assign_hazard_ids(hazards, domain)

    # ---- 3. 分配 safety_goal_id ----
    assign_safety_goal_ids(hazards, domain)

    # ---- 4. 验证 ----
    issues = asil_errors + validate_all(hazards, s3_entries)
    # 方案A修复：删除复杂审核机制验证
    # issues.extend(_validate_agent_review_contract(hazards))
    # issues.extend(_validate_needs_review_agent_task(hazards))
    # issues.extend(_validate_hazard_mapping_contract(domain, hazards))
    issues.extend(_validate_s4_traceability(domain, hazards))
    issues.extend(_validate_s4_source_traceability(domain, hazards, s3_entries))
    issues.extend(_validate_domain_pack_matrix_lock(domain, hazards))
    issues.extend(_validate_source_case_lock(domain, hazards))
    errors = [i for i in issues if i[0] == "error"]
    warnings = [i for i in issues if i[0] == "warning"]

    # ---- 5. 统计 ----
    total_events = sum(
        len(h.get("events", []))
        for h in hazards
        if not h.get("skip")
    )
    asil_dist = {}
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        for event in hazard.get("events", []):
            a = event.get("asil") or "<missing>"
            asil_dist[a] = asil_dist.get(a, 0) + 1

    sg_count = sum(
        1 for h in hazards
        for ev in h.get("events", [])
        if ev.get("safety_goal_id")
    )
    skip_count = sum(1 for h in hazards if h.get("skip"))

    # ---- 6. 输出 ----
    # 只有验证通过才写正式 final 文件；失败时不覆盖旧文件，也不留下“看似成功”的 final。
    validation_meta = {
        "passed": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    s4["validation"] = validation_meta

    print(f"\n{'='*60}")
    print(f"[S4 validate] 验证报告")
    print(f"{'='*60}")
    print(f"危害组总数: {len(hazards)}（跳过 {skip_count}, 正常 {len(hazards)-skip_count}）")
    print(f"危害事件总数: {total_events}")
    print(f"ASIL 分布: {dict(sorted(asil_dist.items()))}")
    print(f"安全目标数: {sg_count}")
    print(f"错误: {len(errors)}, 警告: {len(warnings)}")

    if footnote_events:
        print(f"\n[!] ISO 脚注 {FOOTNOTE_CLAUSE}（S3/E1/C3=A 需人工审核）: {len(footnote_events)} 条")

    if errors:
        print(f"\n错误:")
        for level, msg in errors:
            print(f"  [ERROR] {msg}")

    if warnings:
        print(f"\n警告:")
        for level, msg in warnings:
            print(f"  [WARN] {msg}")

    if not errors:
        _save_json_atomic(s4, output_path)
        _clear_validation_failure_marker(output_path)
        print(f"\n[S4 validate] 验证通过，输出: {Path(output_path).resolve()}")
        return s4

    print(f"\n[S4 validate] 存在 {len(errors)} 个错误，请修正后重新验证")
    output = Path(output_path)
    output_notice = (
        "未生成正式 final 文件"
        if not output.exists()
        else f"未覆盖现有正式 final 文件: {output.resolve()}"
    )
    message = f"S4 验证失败：{len(errors)} 个错误；{output_notice}"
    _mark_validation_failure(output_path, message)
    raise S4ValidationError(message)


# ============================================================
# CLI 入口
# ============================================================

def run(args):
    """被 run_hara.py 调用的入口"""
    subcmd = getattr(args, "hara_subcommand", None)

    if subcmd == "prepare":
        s1_path = getattr(args, "s1", None)
        s2_path = getattr(args, "s2", None)
        intermediate_path = getattr(args, "intermediate", None)
        try:
            prepare(args.s3_hazop, args.output or "s4_hara_skeleton.json",
                    s1_path=s1_path, s2_path=s2_path, intermediate_path=intermediate_path)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S4 prepare] 操作失败：{error}")
            raise SystemExit(1) from error
    elif subcmd == "autofill":
        try:
            autofill(args.s4_hara, args.output or "s4_hara_autofilled.json")
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S4 autofill] 执行失败：{error}")
            raise SystemExit(1) from error
    elif subcmd == "validate":
        s3_path = getattr(args, "s3", None)
        try:
            validate(args.s4_hara, args.output or "s4_hara_final.json", s3_path=s3_path)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S4 validate] 操作失败：{error}")
            raise SystemExit(1) from error
    else:
        print("用法: run_hara.py hara prepare <s3_hazop.json> --intermediate intermediate.json --s1 s1_decisions.json --s2 s2_decisions.json [-o skeleton.json]")
        print("      run_hara.py hara autofill <s4_hara.json> [-o autofilled.json]")
        print("      run_hara.py hara validate <s4_hara.json> [-o final.json] [--s3 s3_hazop.json]")
