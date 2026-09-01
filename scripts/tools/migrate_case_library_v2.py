"""Migrate the legacy PT case library to semantic case-library v2.

The generated v2 asset deliberately does not contain project-level func_id,
analysis_unit_id, or failure_id fields. Those identifiers are written only to
the offline migration report so the migration can be audited without making
the runtime matcher depend on one source project's numbering.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "references" / "case_library" / "PT_cases.json"
DEFAULT_OUTPUT = ROOT / "references" / "case_library" / "PT_cases_v2.json"
DEFAULT_REPORT = ROOT / "references" / "case_library" / "PT_migration_report.json"

MIGRATION_BATCH = "20260826_case_library_v2"

LEGACY_FAMILY_FALLBACK = {
    "P_func_0001": "pt_gear_state_control",
    "P_func_0002": "pt_hv_power_state_management",
    "P_func_0003": "pt_thermal_management",
    "P_func_0004": "pt_traction_torque_control",
    "P_func_0005": "pt_charge_discharge",
    "P_func_0006": "pt_hv_safety",
}

CANONICAL_MODES = {
    "丢失": "丢失",
    "非预期": "非预期",
    "间歇": "间歇",
    "过多": "过多",
    "过大": "过多",
    "过少": "过少",
    "过小": "过少",
    "过早": "过早",
    "反向": "反向",
    "振荡": "振荡",
    "部分": "部分",
    "过晚": "过晚",
    "卡滞": "卡滞",
}

ROLE_RULES = [
    ("显示", ["gear_information_display"]),
    ("DCDC", ["dcdc_control"]),
    ("高压上电", ["hv_power_on_management"]),
    ("上电", ["hv_power_on_management"]),
    ("高压下电", ["hv_power_off_management"]),
    ("下电", ["hv_power_off_management"]),
    ("互锁", ["hv_interlock_monitoring"]),
    ("绝缘", ["hv_insulation_monitoring"]),
    ("碰撞", ["collision_hv_shutdown"]),
    ("热失效", ["thermal_runaway_detection"]),
    ("电池热", ["battery_thermal_safety_control"]),
    ("电池包加热", ["battery_thermal_safety_control"]),
    ("电池包冷却", ["battery_thermal_safety_control"]),
    ("电机冷却", ["edrive_thermal_protection"]),
    ("除霜", ["cabin_visibility_control"]),
    ("制动能量回收", ["braking_regeneration"]),
    ("滑行能量回收", ["coasting_regeneration"]),
    ("驱动扭矩", ["traction_torque_calculation"]),
    ("充电", ["charging_configuration"]),
    ("放电", ["v2l_energy_output"]),
    ("档", ["gear_shift_control"]),
    ("挡", ["gear_shift_control"]),
]

HAZARD_RULES = [
    ("车辆无扭矩输出", "vehicle_no_drive_torque"),
    ("车辆驱动力丢失", "vehicle_drive_force_loss"),
    ("车辆驱动力不足", "vehicle_drive_force_insufficient"),
    ("加速不足", "vehicle_drive_force_insufficient"),
    ("车辆无动力", "vehicle_no_drive_power"),
    ("车辆无法移动", "vehicle_cannot_move"),
    ("车辆无法启动", "vehicle_start_failure"),
    ("非预期向前移动", "unintended_forward_motion"),
    ("非预期向后移动", "unintended_backward_motion"),
    ("车辆反向移动", "unintended_reverse_motion"),
    ("加速度方向相反", "reversed_acceleration_direction"),
    ("非预期倒车", "unintended_reverse_motion"),
    ("非预期加速", "unintended_acceleration"),
    ("非预期纵向移动", "unintended_longitudinal_motion"),
    ("非预期车辆纵向减速", "unintended_longitudinal_deceleration"),
    ("车辆非预期减速或停止", "unintended_deceleration_or_stop"),
    ("车辆非预期减速", "unintended_deceleration"),
    ("非预期减速", "unintended_deceleration"),
    ("车辆纵向减速非预期减少", "reduced_longitudinal_deceleration"),
    ("减速度过大", "excessive_deceleration"),
    ("制动力降低", "reduced_braking_force"),
    ("溜车", "vehicle_rollaway"),
    ("冒烟、起火、爆炸", "thermal_event_or_fire"),
    ("高压泄漏、触电", "hv_leakage_or_electric_shock"),
    ("续航下降", "reduced_driving_range"),
    ("SOC减少", "battery_soc_loss"),
    ("动力电池能量无法蓄能", "battery_energy_storage_failure"),
    ("非预期运行", "unintended_vehicle_operation"),
    ("车辆意外驻车激活", "unintended_parking_activation"),
    ("无法正确显示车辆D挡", "gear_d_display_failure"),
    ("无法正确显示车辆N挡", "gear_n_display_failure"),
    ("无法正确显示车辆R挡", "gear_r_display_failure"),
    ("无法正确显示车辆P挡", "gear_p_display_failure"),
    ("挡风雾无法消除", "windshield_visibility_failure"),
    ("整车控制器失效", "vehicle_controller_failure"),
    ("无法及时离开车辆", "occupant_exit_failure"),
]


def text(value: Any) -> str:
    return str(value or "").strip()


def compact(value: str) -> str:
    return re.sub(r"[\s，。；、：:（）()\[\]【】,;]+", "", text(value)).lower()


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = text(value)
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def family_for_name(func_name: str, legacy_func_id: str) -> tuple[str, str, float]:
    name = text(func_name)
    # Check safety before thermal: 电池热失效告警 belongs to HV safety.
    if any(token in name for token in ("互锁", "绝缘", "碰撞下电", "碰撞监测", "碰撞检测", "热失效告警")):
        return "pt_hv_safety", "function_name_rule", 1.0
    if any(token in name for token in ("档", "挡", "换挡", "挡位")):
        return "pt_gear_state_control", "function_name_rule", 1.0
    if any(token in name for token in ("高压上电", "高压下电", "DCDC")):
        return "pt_hv_power_state_management", "function_name_rule", 1.0
    if any(token in name for token in ("电池包", "电机冷却", "除霜")):
        return "pt_thermal_management", "function_name_rule", 1.0
    if any(token in name for token in ("扭矩", "能量回收")):
        return "pt_traction_torque_control", "function_name_rule", 1.0
    if any(token in name for token in ("充电", "放电")):
        return "pt_charge_discharge", "function_name_rule", 1.0
    if legacy_func_id in LEGACY_FAMILY_FALLBACK:
        return LEGACY_FAMILY_FALLBACK[legacy_func_id], "legacy_group_fallback", 0.65
    return "pt_unclassified_function", "unresolved", 0.0


def roles_for_name(func_name: str, family: str) -> list[str]:
    name = text(func_name)
    roles: list[str] = []
    for token, matched_roles in ROLE_RULES:
        if token in name:
            roles.extend(matched_roles)
    if not roles:
        roles.append(family.removeprefix("pt_") + "_general")
    return unique(roles)


def controlled_objects_for_family(family: str) -> list[str]:
    return {
        "pt_gear_state_control": ["gear_state"],
        "pt_hv_power_state_management": ["high_voltage_power_state"],
        "pt_thermal_management": ["thermal_management_state"],
        "pt_traction_torque_control": ["vehicle_drive_torque"],
        "pt_charge_discharge": ["vehicle_energy_transfer"],
        "pt_hv_safety": ["high_voltage_safety_state"],
    }.get(family, ["unclassified_vehicle_function"])


def anomaly_class_for_case(func_name: str, mode: str, anomaly: str) -> tuple[str, str, float]:
    value = compact(anomaly)
    name = compact(func_name)
    if any(token in value for token in ("切入", "切换", "切换到")) and any(token in value for token in ("档", "挡")):
        if "反向" in value or "进入r" in value or "进入d" in value:
            return "gear_transition_wrong_state", "keyword_rule", 1.0
        if mode == "非预期":
            return "unintended_gear_transition", "keyword_rule", 1.0
        return "gear_transition_not_executed", "keyword_rule", 1.0
    if "挡位" in anomaly or "档位" in anomaly or "挡" in anomaly or "档" in anomaly:
        if "显示" in anomaly or "不显示" in anomaly:
            return "gear_state_display_failure", "keyword_rule", 1.0
        return "gear_state_transition_failure", "keyword_rule", 0.95
    if "dcdc" in value:
        if "短路" in value:
            return "dcdc_unintended_short_circuit", "keyword_rule", 1.0
        return "dcdc_output_regulation_anomaly", "keyword_rule", 0.9
    if "驱动扭矩" in anomaly or "驱动转矩" in anomaly or "驱动力" in anomaly:
        if "反向" in anomaly:
            return "drive_torque_reversed", "keyword_rule", 1.0
        if "过大" in anomaly:
            return "drive_torque_excessive", "keyword_rule", 1.0
        if "过小" in anomaly:
            return "drive_torque_insufficient", "keyword_rule", 1.0
        if "非预期" in anomaly:
            return "drive_torque_unintended", "keyword_rule", 1.0
        return "required_drive_torque_not_provided", "keyword_rule", 1.0
    if "制动能量回收" in anomaly:
        return "braking_regeneration_anomaly", "keyword_rule", 1.0
    if "滑行能量回收" in anomaly:
        return "coasting_regeneration_anomaly", "keyword_rule", 1.0
    if "充电" in anomaly:
        return "charging_control_anomaly", "keyword_rule", 0.95
    if "放电" in anomaly:
        return "discharging_control_anomaly", "keyword_rule", 0.95
    if "高压互锁" in anomaly or "互锁" in anomaly:
        return "hv_interlock_detection_anomaly", "keyword_rule", 1.0
    if "高压绝缘" in anomaly or "绝缘" in anomaly:
        return "hv_insulation_detection_anomaly", "keyword_rule", 1.0
    if "碰撞下电" in anomaly or "碰撞" in anomaly:
        return "collision_hv_shutdown_anomaly", "keyword_rule", 1.0
    if "上电" in anomaly:
        return "hv_power_on_anomaly", "keyword_rule", 0.95
    if "下电" in anomaly:
        return "hv_power_off_anomaly", "keyword_rule", 0.95
    if "加热" in anomaly:
        return "battery_heating_anomaly", "keyword_rule", 0.95
    if "冷却" in anomaly:
        return "thermal_cooling_anomaly", "keyword_rule", 0.95
    if "热失效" in anomaly or "热失控" in anomaly:
        return "thermal_runaway_detection_anomaly", "keyword_rule", 1.0
    if "除霜" in anomaly:
        return "defrost_function_anomaly", "keyword_rule", 1.0
    if "制动转矩" in anomaly and ("反向" in anomaly or "相反" in anomaly):
        return "braking_torque_reversed", "keyword_rule", 1.0
    if name:
        return f"{family_slug(name)}_{mode_slug(mode)}", "function_mode_fallback", 0.55
    digest = hashlib.sha256(text(anomaly).encode("utf-8")).hexdigest()[:12]
    return f"unclassified_anomaly_{digest}", "unresolved_hash_fallback", 0.1


def family_slug(value: str) -> str:
    if "档" in value or "挡" in value:
        return "gear_state"
    if "扭矩" in value:
        return "drive_torque"
    if "回收" in value:
        return "energy_regeneration"
    if "充电" in value:
        return "charging"
    if "放电" in value:
        return "discharging"
    if "高压" in value:
        return "high_voltage"
    return "vehicle_function"


def mode_slug(mode: str) -> str:
    return {
        "丢失": "loss",
        "非预期": "unintended",
        "过多": "excessive",
        "过少": "insufficient",
        "反向": "reversed",
        "间歇": "intermittent",
        "过早": "too_early",
        "过晚": "too_late",
        "振荡": "oscillation",
        "部分": "partial",
        "卡滞": "stuck",
    }.get(mode, "other")


def hazard_profile(vehicle_hazard: str) -> tuple[str, str, str, float]:
    value = text(vehicle_hazard)
    for phrase, hazard_class in HAZARD_RULES:
        if phrase in value:
            return (
                "PT_" + hazard_class.upper(),
                hazard_class,
                "vehicle_hazard_rule",
                1.0,
            )
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return (
        "PT_UNCLASSIFIED_HAZARD_" + digest.upper(),
        "unclassified_hazard_" + digest,
        "unresolved_hash_fallback",
        0.1,
    )


def scenario_dimensions(scenario: str) -> dict[str, Any]:
    value = text(scenario)
    compact_value = compact(value)
    if any(token in value for token in ("倒车", "倒车过程")):
        maneuver = "reverse"
    elif any(token in value for token in ("泊车", "停车")):
        maneuver = "parking"
    elif any(token in value for token in ("充电",)):
        maneuver = "charging"
    elif any(token in value for token in ("放电",)):
        maneuver = "discharging"
    elif any(token in value for token in ("超车", "并道", "借道")):
        maneuver = "overtaking_or_merging"
    elif any(token in value for token in ("下坡", "下长坡")):
        maneuver = "downhill_driving"
    elif any(token in value for token in ("起步", "启动", "启动车辆")):
        maneuver = "vehicle_start"
    elif any(token in value for token in ("等待", "走走停停")):
        maneuver = "waiting_or_stop_go"
    elif "行驶" in value:
        maneuver = "driving"
    else:
        maneuver = "unspecified"

    if "高速" in value or "超高速" in value:
        road_type = "highway"
    elif "乡村" in value:
        road_type = "rural_road"
    elif any(token in value for token in ("城市", "十字路口", "小区")):
        road_type = "urban_road"
    elif "车库" in value:
        road_type = "garage"
    elif "停车场" in value:
        road_type = "parking_area"
    elif "路边" in value:
        road_type = "roadside"
    elif any(token in value for token in ("山路", "坡", "匝道")):
        road_type = "slope_or_ramp"
    elif "充电站" in value:
        road_type = "charging_station"
    elif "维修厂" in value:
        road_type = "workshop"
    else:
        road_type = "unspecified"

    if "超高速" in value or "高速" in value:
        speed_band = "high"
    elif "中速" in value:
        speed_band = "medium"
    elif "低速" in value or "小于" in value and "km/h" in value:
        speed_band = "low"
    elif "起步" in value or "0 < v < 10" in value:
        speed_band = "0_10_kmh"
    else:
        speed_band = "unspecified"

    if any(token in value for token in ("行人", "易受伤害", "婴儿")):
        traffic_condition = "vulnerable_road_user"
    elif any(token in value for token in ("后方有", "跟车", "后方较", "后方适中")):
        traffic_condition = "following_vehicle"
    elif any(token in value for token in ("对向", "来车", "迎面")):
        traffic_condition = "oncoming_vehicle"
    elif any(token in value for token in ("前方车辆", "前车")):
        traffic_condition = "front_vehicle"
    elif "车上有人" in value or "车内有人" in value:
        traffic_condition = "occupant_present"
    elif "没有交通" in value:
        traffic_condition = "no_relevant_traffic"
    else:
        traffic_condition = "unspecified"

    if any(token in value for token in ("较近", "近距离", "距离较近", "短")):
        following_distance = "short"
    elif any(token in value for token in ("适中", "中等距离")):
        following_distance = "medium"
    elif any(token in value for token in ("较远", "远距离")):
        following_distance = "long"
    else:
        following_distance = "unspecified"

    if "平坦" in value or "平路" in value:
        slope = "flat"
    elif any(token in value for token in ("下坡", "下长坡")):
        slope = "downhill"
    elif any(token in value for token in ("上坡", "爬坡")):
        slope = "uphill"
    else:
        slope = "unspecified"

    if "寒冷" in value or "低温" in value:
        environment = "cold_weather"
    elif any(token in value for token in ("热失控", "温度达到", "电池温度", "电机温度")):
        environment = "thermal_limit_condition"
    else:
        environment = "unspecified"

    return {
        "maneuver": maneuver,
        "road_type": road_type,
        "speed_band": speed_band,
        "traffic_condition": traffic_condition,
        "following_distance": following_distance,
        "slope": slope,
        "environment": environment,
        "vulnerable_road_user": any(token in value for token in ("行人", "易受伤害", "婴儿")),
        "source_text_compact": compact_value,
    }


def fingerprint(dimensions: dict[str, Any]) -> str:
    # source_text_compact is intentionally excluded so equivalent semantic
    # dimensions can share a fingerprint across projects and phrasings.
    stable = {k: dimensions[k] for k in sorted(dimensions) if k != "source_text_compact"}
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def migrate_case(case: dict[str, Any], index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    old_func_id = text(case.get("func_id"))
    func_name = text(case.get("func_name"))
    old_mode = text(case.get("failure_mode"))
    canonical_mode = CANONICAL_MODES.get(old_mode, old_mode or "未分类")
    family, family_method, family_confidence = family_for_name(func_name, old_func_id)
    roles = roles_for_name(func_name, family)
    anomaly = text(case.get("anomaly"))
    anomaly_class, anomaly_method, anomaly_confidence = anomaly_class_for_case(
        func_name, canonical_mode, anomaly
    )
    hazard_text = text(case.get("vehicle_hazard"))
    hazard_family, hazard_class, hazard_method, hazard_confidence = hazard_profile(hazard_text)
    scenario_text = text(case.get("scenario")) or "未提供场景"
    dimensions = scenario_dimensions(scenario_text)
    scenario_fingerprint = fingerprint(dimensions)
    dimensions.pop("source_text_compact", None)

    new_case_id = f"PT_CASE_{index:06d}"
    migrated = {
        "schema_version": "2.0",
        "case_id": new_case_id,
        "domain": "PT",
        "function_profile": {
            "canonical_function_family": family,
            "semantic_roles": roles,
            "controlled_objects": controlled_objects_for_family(family),
            "capability_tokens": unique([func_name, *roles]),
        },
        "failure_profile": {
            "canonical_mode": canonical_mode,
            "anomaly_class": anomaly_class,
            "anomaly_aliases": unique([anomaly]),
            **({"legacy_mode_labels": [old_mode]} if old_mode and old_mode != canonical_mode else {}),
        },
        "hazard_profile": {
            "hazard_family": hazard_family,
            "vehicle_hazard_class": hazard_class,
            "hazard_aliases": unique([hazard_text]),
        },
        "scenario_profile": {
            "semantic_fingerprint": scenario_fingerprint,
            "dimensions": dimensions,
            "scenario_text": scenario_text,
        },
        "assessment": {
            "severity": case.get("severity"),
            "severity_reason": case.get("severity_reason"),
            "exposure": case.get("exposure"),
            "exposure_reason": case.get("exposure_reason"),
            "controllability": case.get("controllability"),
            "controllability_reason": case.get("controllability_reason"),
            "asil": case.get("asil"),
            "safety_goal": case.get("safety_goal"),
            "safe_state": case.get("safe_state"),
            "ftti": case.get("ftti"),
        },
        "provenance": {
            "source_assets": [{
                "asset_type": "legacy_case_library",
                "asset_name": "PT_cases.json",
                "sheet": None,
                "row": None,
            }],
            "migration_batch": MIGRATION_BATCH,
            "notes": "Migrated from legacy project-bound asset; project IDs are intentionally omitted from runtime asset.",
        },
    }
    audit = {
        "new_case_id": new_case_id,
        "legacy_case_id": text(case.get("case_id")),
        "legacy_func_id": old_func_id,
        "legacy_failure_id": text(case.get("failure_id")),
        "legacy_func_name": func_name,
        "legacy_failure_mode": old_mode,
        "canonical_failure_mode": canonical_mode,
        "legacy_anomaly": anomaly,
        "legacy_vehicle_hazard": hazard_text,
        "function_mapping": {"method": family_method, "confidence": family_confidence, "family": family},
        "anomaly_mapping": {"method": anomaly_method, "confidence": anomaly_confidence, "class": anomaly_class},
        "hazard_mapping": {"method": hazard_method, "confidence": hazard_confidence, "class": hazard_class, "family": hazard_family},
        "scenario_mapping": {"fingerprint": scenario_fingerprint, "dimensions": dimensions},
        "needs_review": min(family_confidence, anomaly_confidence, hazard_confidence) < 0.75,
    }
    return migrated, audit


def validate_v2_case(case: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "case_id", "domain", "function_profile",
        "failure_profile", "hazard_profile", "scenario_profile",
        "assessment", "provenance",
    }
    missing = sorted(required - set(case))
    if missing:
        errors.append(f"case[{index}] missing required fields: {missing}")
    forbidden = {"func_id", "analysis_unit_id", "failure_id"}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden:
                    errors.append(f"case[{index}] forbidden runtime field at {path}/{key}")
                walk(child, f"{path}/{key}")
        elif isinstance(value, list):
            for child_index, child in enumerate(value):
                walk(child, f"{path}/{child_index}")

    walk(case, "")
    if case.get("schema_version") != "2.0":
        errors.append(f"case[{index}] schema_version must be 2.0")
    if case.get("domain") != "PT":
        errors.append(f"case[{index}] domain must be canonical PT")
    function_profile = case.get("function_profile")
    if not isinstance(function_profile, dict) or not function_profile.get("canonical_function_family"):
        errors.append(f"case[{index}] missing canonical function family")
    failure_profile = case.get("failure_profile")
    if not isinstance(failure_profile, dict) or not failure_profile.get("canonical_mode") or not failure_profile.get("anomaly_class"):
        errors.append(f"case[{index}] missing canonical failure profile")
    hazard_profile = case.get("hazard_profile")
    if not isinstance(hazard_profile, dict) or not hazard_profile.get("hazard_family") or not hazard_profile.get("vehicle_hazard_class"):
        errors.append(f"case[{index}] missing hazard profile")
    scenario_profile = case.get("scenario_profile")
    if not isinstance(scenario_profile, dict) or not scenario_profile.get("semantic_fingerprint") or not scenario_profile.get("scenario_text"):
        errors.append(f"case[{index}] missing scenario profile")
    return errors


def semantic_identity(case: dict[str, Any]) -> tuple[Any, ...]:
    function = case.get("function_profile", {})
    failure = case.get("failure_profile", {})
    hazard = case.get("hazard_profile", {})
    scenario = case.get("scenario_profile", {})
    return (
        case.get("domain"),
        function.get("canonical_function_family"),
        failure.get("canonical_mode"),
        failure.get("anomaly_class"),
        hazard.get("hazard_family"),
        hazard.get("vehicle_hazard_class"),
        scenario.get("semantic_fingerprint"),
    )


def assessment_signature(case: dict[str, Any]) -> tuple[Any, ...]:
    assessment = case.get("assessment", {})
    return tuple(assessment.get(field) for field in (
        "severity", "severity_reason", "exposure", "exposure_reason",
        "controllability", "controllability_reason", "asil",
        "safety_goal", "safe_state", "ftti",
    ))


def semantic_collision_audit(cases: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        groups[semantic_identity(case)].append(case)

    duplicates = [group for group in groups.values() if len(group) > 1]
    conflicts = [
        group for group in duplicates
        if len({assessment_signature(case) for case in group}) > 1
    ]
    conflict_case_ids = {
        case.get("case_id") for group in conflicts for case in group
    }
    return {
        "semantic_identity_group_count": len(groups),
        "duplicate_identity_group_count": len(duplicates),
        "assessment_conflict_group_count": len(conflicts),
        "assessment_conflict_case_count": len(conflict_case_ids),
        "maximum_group_size": max((len(group) for group in duplicates), default=1),
        "runtime_policy": (
            "duplicate semantic identities with identical assessments may remain exact; "
            "assessment conflicts must be returned as editable candidates and never lock the first row"
        ),
        "conflict_groups": [
            {
                "case_ids": [case.get("case_id") for case in group],
                "semantic_identity": {
                    "domain": semantic_identity(group[0])[0],
                    "canonical_function_family": semantic_identity(group[0])[1],
                    "canonical_mode": semantic_identity(group[0])[2],
                    "anomaly_class": semantic_identity(group[0])[3],
                    "hazard_family": semantic_identity(group[0])[4],
                    "vehicle_hazard_class": semantic_identity(group[0])[5],
                    "scenario_semantic_fingerprint": semantic_identity(group[0])[6],
                },
                "assessment_variant_count": len({assessment_signature(case) for case in group}),
            }
            for group in conflicts
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy PT case assets to semantic case-library v2")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    cases = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("legacy case library must be a JSON list")

    migrated: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"case[{index}] must be an object")
        row, audit = migrate_case(case, index)
        migrated.append(row)
        audit_rows.append(audit)

    def counts(path: str) -> dict[str, int]:
        return dict(Counter(row[path] for row in audit_rows))

    schema_errors = []
    for index, case in enumerate(migrated, start=1):
        schema_errors.extend(validate_v2_case(case, index))

    collision_audit = semantic_collision_audit(migrated)

    report = {
        "schema_version": "2.0",
        "migration_batch": MIGRATION_BATCH,
        "source": str(args.input),
        "output": str(args.output),
        "source_case_count": len(cases),
        "migrated_case_count": len(migrated),
        "runtime_forbidden_fields": ["func_id", "analysis_unit_id", "failure_id"],
        "function_family_distribution": dict(Counter(row["function_mapping"]["family"] for row in audit_rows)),
        "canonical_mode_distribution": dict(Counter(row["canonical_failure_mode"] for row in audit_rows)),
        "anomaly_mapping_methods": dict(Counter(row["anomaly_mapping"]["method"] for row in audit_rows)),
        "hazard_mapping_methods": dict(Counter(row["hazard_mapping"]["method"] for row in audit_rows)),
        "scenario_fingerprint_count": len({row["scenario_mapping"]["fingerprint"] for row in audit_rows}),
        "needs_review_count": sum(1 for row in audit_rows if row["needs_review"]),
        "semantic_collision_audit": collision_audit,
        "schema_validation": {
            "passed": not schema_errors,
            "error_count": len(schema_errors),
            "errors": schema_errors[:100],
        },
        "runtime_ready": not schema_errors and not any(row["needs_review"] for row in audit_rows),
        "legacy_func_id_count": len({row["legacy_func_id"] for row in audit_rows}),
        "legacy_failure_id_count": len({row["legacy_failure_id"] for row in audit_rows}),
        "rows": audit_rows,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[case-library v2] source cases: {len(cases)}")
    print(f"[case-library v2] migrated cases: {len(migrated)}")
    print(f"[case-library v2] legacy func_id groups: {report['legacy_func_id_count']}")
    print(f"[case-library v2] semantic scenarios: {report['scenario_fingerprint_count']}")
    print(f"[case-library v2] needs review: {report['needs_review_count']}")
    print(
        "[case-library v2] semantic duplicate/conflict groups: "
        f"{collision_audit['duplicate_identity_group_count']}/"
        f"{collision_audit['assessment_conflict_group_count']}"
    )
    print(f"[case-library v2] schema validation: {report['schema_validation']['passed']}")
    print(f"[case-library v2] runtime ready: {report['runtime_ready']}")
    print(f"[case-library v2] output: {args.output}")
    print(f"[case-library v2] report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
