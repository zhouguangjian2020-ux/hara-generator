"""统一域知识包加载与基础结构校验。

Domain Pack 是运行时的业务知识入口：它描述功能语义、分析单元、风险矩阵和
安全目标规则。它不包含、也不解释参考 Excel 的格式、列号或非目标 Sheet。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


_PACK_CACHE: dict[str, dict[str, Any]] = {}
_MANIFEST_CACHE: dict[str, Any] | None = None

_REQUIRED_ROOT_FIELDS = (
    "schema_version", "domain", "domain_name", "status", "provenance",
    "function_catalog", "analysis_catalog", "risk_catalog",
    "safety_goal_catalog", "quality_contract",
)
_REQUIRED_SECTIONS = {
    "function_catalog": ("function_matchers", "semantic_roles", "scope_rules", "known_input_aliases"),
    "analysis_catalog": ("analysis_units", "failure_mode_rules", "hazop_patterns"),
    "risk_catalog": ("scenario_sets", "scenario_lookup", "event_matrix"),
    "safety_goal_catalog": ("hazard_catalog", "hazard_families", "rules"),
    "quality_contract": ("known_exact_requirements", "unknown_policy"),
}


def _pack_directory() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "references" / "domain_packs"


def reset_domain_pack_cache() -> None:
    """清空进程内缓存，供测试和热重载调用。"""
    global _MANIFEST_CACHE
    _PACK_CACHE.clear()
    _MANIFEST_CACHE = None


def load_domain_pack_manifest() -> dict[str, Any]:
    """加载统一域知识包清单。"""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE

    path = _pack_directory() / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法加载 Domain Pack manifest: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("Domain Pack manifest 顶层必须是对象")
    _MANIFEST_CACHE = value
    return value


def canonical_domain_pack_code(domain: str) -> str:
    """将历史域前缀归一化为 Domain Pack 标准代码。"""
    raw = (domain or "").strip()
    if not raw:
        return ""
    aliases = load_domain_pack_manifest().get("domain_aliases", {})
    if raw in aliases:
        return aliases[raw]
    for alias, canonical in aliases.items():
        if alias.lower() == raw.lower():
            return canonical
    return raw


def load_domain_pack(domain: str, *, validate: bool = True) -> dict[str, Any]:
    """按域加载一个知识包；不会加载其他域的数据。"""
    canonical = canonical_domain_pack_code(domain)
    if canonical in _PACK_CACHE:
        return _PACK_CACHE[canonical]

    manifest = load_domain_pack_manifest()
    filename = manifest.get("domain_files", {}).get(canonical)
    if not filename:
        raise ValueError(f"未配置域 '{domain}' 的 Domain Pack")

    path = _pack_directory() / filename
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法加载 {canonical} Domain Pack: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{canonical} Domain Pack 顶层必须是对象")
    if validate:
        errors = validate_domain_pack(value, expected_domain=canonical)
        if errors:
            raise ValueError(f"{canonical} Domain Pack 无效：" + "; ".join(errors))

    _PACK_CACHE[canonical] = value
    return value


def _scenario_identity(value: Any) -> str:
    """单源场景目录的精确身份键：仅忽略 Unicode/排版空白差异。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value)).replace("\u00a0", " ")
    return re.sub(r"\s+", "", text).strip()


def _validate_single_source_scenario_contract(risk: dict[str, Any]) -> list[str]:
    """验证启用场景单源合同的 Pack：文本只存在 catalog，其他位置只引用 ID。"""
    errors: list[str] = []
    contract = risk.get("scenario_single_source_contract")
    if not contract:
        return errors
    if not isinstance(contract, dict):
        return ["risk_catalog.scenario_single_source_contract 必须是对象"]

    catalog = risk.get("scenario_catalog")
    if not isinstance(catalog, dict) or not catalog:
        return ["单源场景合同要求非空 risk_catalog.scenario_catalog"]
    seen_texts: dict[str, str] = {}
    for scenario_id, item in catalog.items():
        if not isinstance(scenario_id, str) or not scenario_id.startswith("PT_SC_"):
            errors.append(f"scenario_catalog 含无效 scenario_id: {scenario_id!r}")
            continue
        if not isinstance(item, dict):
            errors.append(f"scenario_catalog.{scenario_id} 必须是对象")
            continue
        identity = _scenario_identity(item.get("text"))
        if not identity:
            errors.append(f"scenario_catalog.{scenario_id} 缺少 text")
            continue
        prior = seen_texts.setdefault(identity, scenario_id)
        if prior != scenario_id:
            errors.append(f"scenario_catalog 场景文本重复: {prior}/{scenario_id}")

    def validate_ids(value: Any, path: str) -> None:
        if not isinstance(value, list):
            errors.append(f"{path} 必须是 scenario_ids 数组")
            return
        if len(value) != len(set(value)):
            errors.append(f"{path} 包含重复 scenario_id")
        for scenario_id in value:
            if scenario_id not in catalog:
                errors.append(f"{path} 引用未知 scenario_id: {scenario_id}")

    def walk_lookup(value: Any, path: str = "risk_catalog.scenario_lookup") -> None:
        if isinstance(value, dict):
            if "scenarios" in value:
                errors.append(f"{path} 不得存储重复 scenarios 文本")
            if "scenario_ids" in value:
                validate_ids(value.get("scenario_ids"), f"{path}.scenario_ids")
            for key, child in value.items():
                walk_lookup(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk_lookup(child, f"{path}[{index}]")

    walk_lookup(risk.get("scenario_lookup", {}))
    for index, event in enumerate(risk.get("compatible_event_baselines", [])):
        if not isinstance(event, dict):
            continue
        if "scenario" in event:
            errors.append(f"compatible_event_baselines[{index}] 不得存储重复 scenario 文本")
        if event.get("scenario_id") not in catalog:
            errors.append(f"compatible_event_baselines[{index}] 引用未知 scenario_id")

    reference_events = risk.get("reference_event_matrix")
    if not isinstance(reference_events, list):
        errors.append("单源场景合同要求 risk_catalog.reference_event_matrix 为数组")
        return errors
    source_ids: set[str] = set()
    for index, event in enumerate(reference_events):
        if not isinstance(event, dict):
            errors.append(f"reference_event_matrix[{index}] 必须是对象")
            continue
        if "scenario" in event:
            errors.append(f"reference_event_matrix[{index}] 不得存储重复 scenario 文本")
        event_id = event.get("reference_event_id")
        if not isinstance(event_id, str) or not re.fullmatch(r"P_hzrd_\d{5}", event_id):
            errors.append(f"reference_event_matrix[{index}] reference_event_id 无效")
        elif event_id in source_ids:
            errors.append(f"reference_event_matrix[{index}] reference_event_id 重复: {event_id}")
        else:
            source_ids.add(event_id)
        if event.get("scenario_id") not in catalog:
            errors.append(f"reference_event_matrix[{index}] 引用未知 scenario_id")
        for field in ("function_name", "reference_failure_id", "anomaly", "vehicle_hazard"):
            if not isinstance(event.get(field), str) or not event.get(field).strip():
                errors.append(f"reference_event_matrix[{index}] 缺少 {field}")
    return errors


def _validate_pt_case_library_catalog(pack: dict[str, Any]) -> list[str]:
    """验证 PT 单套运行时资产中的语义案例目录。

    该校验只对 PT 生效，避免改变其他域 Domain Pack 的合同；PT 的案例匹配
    与 Domain Pack 合并到同一个 JSON 后，目录损坏必须在加载阶段失败关闭。
    """
    catalog = pack.get("case_library_catalog")
    if not isinstance(catalog, dict):
        return ["PT.json 缺少 case_library_catalog"]
    if catalog.get("schema_version") != "2.0":
        return ["PT.json case_library_catalog.schema_version 必须为 2.0"]
    cases = catalog.get("cases")
    if not isinstance(cases, list):
        return ["PT.json case_library_catalog.cases 必须是数组"]
    case_count = catalog.get("case_count")
    if case_count != len(cases):
        return [f"PT.json case_library_catalog.case_count={case_count!r} 与实际案例数 {len(cases)} 不一致"]

    errors: list[str] = []
    required_profiles = ("function_profile", "failure_profile", "hazard_profile", "scenario_profile", "assessment")
    for index, case in enumerate(cases):
        prefix = f"case_library_catalog.cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        if case.get("schema_version") != "2.0":
            errors.append(f"{prefix}.schema_version 必须为 2.0")
        if case.get("domain") != "PT":
            errors.append(f"{prefix}.domain 必须为 PT")
        if not isinstance(case.get("case_id"), str) or not case.get("case_id"):
            errors.append(f"{prefix}.case_id 必须是非空字符串")
        for profile_name in required_profiles:
            if not isinstance(case.get(profile_name), dict):
                errors.append(f"{prefix}.{profile_name} 必须是对象")
        event_description = case.get("event_description")
        if event_description is not None and (
            not isinstance(event_description, str) or not event_description.strip()
        ):
            errors.append(f"{prefix}.event_description 必须为非空字符串或 null")
        source_refs = case.get("source_refs")
        if not isinstance(source_refs, list) or not source_refs:
            errors.append(f"{prefix}.source_refs 必须是非空数组")
    return errors


def validate_domain_pack(pack: dict[str, Any], *, expected_domain: str | None = None) -> list[str]:
    """验证 Pack 的结构契约，不验证具体工程知识的正确性。"""
    errors: list[str] = []
    if not isinstance(pack, dict):
        return ["Domain Pack 必须是对象"]

    for field in _REQUIRED_ROOT_FIELDS:
        if field not in pack:
            errors.append(f"缺少根字段 '{field}'")

    domain = pack.get("domain")
    if not isinstance(domain, str) or not domain.strip():
        errors.append("domain 必须是非空字符串")
    elif expected_domain and domain != expected_domain:
        errors.append(f"domain 应为 '{expected_domain}'，实际为 '{domain}'")

    if not isinstance(pack.get("domain_name"), str) or not pack.get("domain_name", "").strip():
        errors.append("domain_name 必须是非空字符串")
    if not isinstance(pack.get("status"), str) or not pack.get("status", "").strip():
        errors.append("status 必须是非空字符串")
    if not isinstance(pack.get("provenance"), dict):
        errors.append("provenance 必须是对象")
    if domain == "PT":
        errors.extend(_validate_pt_case_library_catalog(pack))

    for section, fields in _REQUIRED_SECTIONS.items():
        value = pack.get(section)
        if not isinstance(value, dict):
            errors.append(f"{section} 必须是对象")
            continue
        for field in fields:
            if field not in value:
                errors.append(f"{section} 缺少字段 '{field}'")

    function_catalog = pack.get("function_catalog")
    if isinstance(function_catalog, dict):
        semantic_roles = function_catalog.get("semantic_roles", {})
        if not isinstance(semantic_roles, dict):
            errors.append("function_catalog.semantic_roles 必须是对象")
            semantic_roles = {}
        else:
            for role, patterns in semantic_roles.items():
                if not isinstance(role, str) or not role.strip():
                    errors.append("function_catalog.semantic_roles 包含空角色名")
                elif not isinstance(patterns, list) or not all(
                    isinstance(pattern, str) and pattern.strip() for pattern in patterns
                ):
                    errors.append(f"semantic_roles.{role} 必须是非空字符串数组")

        scope_rules = function_catalog.get("scope_rules", [])
        if not isinstance(scope_rules, list):
            errors.append("function_catalog.scope_rules 必须是数组")
        else:
            allowed_dispositions = {"analyze", "covered_by", "transfer", "exclude"}
            for index, rule in enumerate(scope_rules):
                prefix = f"scope_rules[{index}]"
                if not isinstance(rule, dict):
                    errors.append(f"{prefix} 必须是对象")
                    continue
                role = rule.get("semantic_role")
                disposition = rule.get("disposition")
                if not isinstance(role, str) or role not in semantic_roles:
                    errors.append(f"{prefix} 引用未知 semantic_role")
                if disposition not in allowed_dispositions:
                    errors.append(f"{prefix}.disposition 必须为 {sorted(allowed_dispositions)} 之一")
                if not isinstance(rule.get("reason_code"), str) or not rule.get("reason_code", "").strip():
                    errors.append(f"{prefix} 缺少非空 reason_code")
                if not isinstance(rule.get("remark"), str) or not rule.get("remark", "").strip():
                    errors.append(f"{prefix} 缺少非空 remark")
                target_domain = rule.get("target_domain")
                if disposition == "transfer":
                    if not isinstance(target_domain, str) or not target_domain.strip():
                        errors.append(f"{prefix}.disposition=transfer 时必须有 target_domain")
                    elif canonical_domain_pack_code(target_domain) == canonical_domain_pack_code(domain):
                        errors.append(f"{prefix}.target_domain 不得等于当前域 {domain}")
                elif target_domain is not None:
                    errors.append(f"{prefix}.disposition={disposition} 时不得有 target_domain")

    # 风险矩阵是运行时的精确查询资产。空矩阵允许作为尚未沉淀的域骨架；一旦
    # 某个 Pack 写入矩阵，则场景 ID、键唯一性及受控完整性契约必须全部成立。
    risk = pack.get("risk_catalog")
    analysis = pack.get("analysis_catalog")
    if isinstance(risk, dict) and isinstance(analysis, dict):
        errors.extend(_validate_single_source_scenario_contract(risk))
        event_matrix_for_shape_check = risk.get("event_matrix", [])
        requires_structured_scenarios = bool(event_matrix_for_shape_check) or bool(
            pack.get("quality_contract", {}).get("exact_risk_matrix")
        )
        scenario_ids: set[str] = set()
        scenario_sets = risk.get("scenario_sets", {})
        if not isinstance(scenario_sets, dict):
            errors.append("risk_catalog.scenario_sets 必须是对象")
            scenario_sets = {}
        for set_name, scenario_set in scenario_sets.items():
            if not isinstance(scenario_set, dict):
                errors.append(f"scenario_sets.{set_name} 必须是对象")
                continue
            scenarios = scenario_set.get("scenarios", [])
            if not isinstance(scenarios, list):
                errors.append(f"scenario_sets.{set_name}.scenarios 必须是数组")
                continue
            for index, scenario in enumerate(scenarios):
                if not isinstance(scenario, dict):
                    # 旧域仍可保留字符串场景建议表；它们不是精确风险矩阵，
                    # 因此不强行把它们误判为 Domain Pack 结构错误。
                    if requires_structured_scenarios:
                        errors.append(f"scenario_sets.{set_name}.scenarios[{index}] 必须是带 scenario_id/text 的对象")
                    continue
                scenario_id = scenario.get("scenario_id")
                text = scenario.get("text")
                if not isinstance(scenario_id, str) or not scenario_id:
                    errors.append(f"scenario_sets.{set_name}.scenarios[{index}] 缺少 scenario_id")
                elif scenario_id in scenario_ids:
                    errors.append(f"scenario_id 重复: {scenario_id}")
                else:
                    scenario_ids.add(scenario_id)
                if not isinstance(text, str) or not text.strip():
                    errors.append(f"scenario_sets.{set_name}.scenarios[{index}] 缺少 text")

        units = {
            unit.get("analysis_unit_id") for unit in analysis.get("analysis_units", [])
            if isinstance(unit, dict) and isinstance(unit.get("analysis_unit_id"), str)
        }
        failure_ids = {
            item.get("failure_id") for item in analysis.get("hazop_patterns", [])
            if isinstance(item, dict) and isinstance(item.get("failure_id"), str)
        }

        # 整车危害是独立的、可复用的领域资产：S3/S4 必须以 vehicle_hazard_id
        # 引用，展示文字由目录解引用；hazard_family 仅保留作 S5 合并键。
        safety_goal_catalog = pack.get("safety_goal_catalog", {})
        hazard_catalog = safety_goal_catalog.get("hazard_catalog", {}) if isinstance(safety_goal_catalog, dict) else {}
        hazard_families = safety_goal_catalog.get("hazard_families", {}) if isinstance(safety_goal_catalog, dict) else {}
        if not isinstance(hazard_catalog, dict):
            errors.append("safety_goal_catalog.hazard_catalog 必须是对象")
            hazard_catalog = {}
        if not isinstance(hazard_families, dict):
            errors.append("safety_goal_catalog.hazard_families 必须是对象")
            hazard_families = {}
        for hazard_id, hazard in hazard_catalog.items():
            if not isinstance(hazard_id, str) or not hazard_id:
                errors.append("hazard_catalog 的键必须是非空 hazard_id")
                continue
            if not isinstance(hazard, dict):
                errors.append(f"hazard_catalog.{hazard_id} 必须是对象")
                continue
            statement = hazard.get("vehicle_hazard")
            family_id = hazard.get("hazard_family")
            if not isinstance(statement, str) or not statement.strip():
                errors.append(f"hazard_catalog.{hazard_id} 缺少 vehicle_hazard")
            if not isinstance(family_id, str) or not family_id:
                errors.append(f"hazard_catalog.{hazard_id} 缺少 hazard_family")
            elif family_id not in hazard_families:
                errors.append(f"hazard_catalog.{hazard_id} 引用未知 hazard_family: {family_id}")
        for index, pattern in enumerate(analysis.get("hazop_patterns", [])):
            if not isinstance(pattern, dict):
                continue
            vehicle_hazard_id = pattern.get("vehicle_hazard_id")
            if not isinstance(vehicle_hazard_id, str) or vehicle_hazard_id not in hazard_catalog:
                errors.append(f"hazop_patterns[{index}] 引用未知 vehicle_hazard_id")
                continue
            family_id = pattern.get("hazard_family")
            catalog_family = hazard_catalog[vehicle_hazard_id].get("hazard_family")
            if family_id != catalog_family:
                errors.append(f"hazop_patterns[{index}] 的 hazard_family 必须与 vehicle_hazard_id 目录一致")

        compatible_baselines = risk.get("compatible_event_baselines", [])
        if compatible_baselines is not None and not isinstance(compatible_baselines, list):
            errors.append("risk_catalog.compatible_event_baselines 必须是数组")
            compatible_baselines = []
        for index, event in enumerate(compatible_baselines or []):
            if not isinstance(event, dict):
                errors.append(f"compatible_event_baselines[{index}] 必须是对象")
                continue
            if event.get("severity") == 0:
                if event.get("asil") not in (None, ""):
                    errors.append(f"compatible_event_baselines[{index}] S=0 时 asil 必须留空")
                for field in (
                    "exposure", "exposure_reason", "controllability",
                    "controllability_reason", "safety_goal", "safe_state", "ftti",
                ):
                    if event.get(field) is not None:
                        errors.append(f"compatible_event_baselines[{index}] S=0 时 {field} 必须为 null")

        event_matrix = risk.get("event_matrix", [])
        if not isinstance(event_matrix, list):
            errors.append("risk_catalog.event_matrix 必须是数组")
            event_matrix = []
        seen_keys: set[tuple[str, str, str]] = set()
        for index, event in enumerate(event_matrix):
            if not isinstance(event, dict):
                errors.append(f"event_matrix[{index}] 必须是对象")
                continue
            key = tuple(event.get(field) for field in ("analysis_unit_id", "failure_id", "scenario_id"))
            if not all(isinstance(value, str) and value for value in key):
                errors.append(f"event_matrix[{index}] 缺少 analysis_unit_id/failure_id/scenario_id")
                continue
            if key in seen_keys:
                errors.append(f"风险矩阵键重复: {key}")
            seen_keys.add(key)
            if units and key[0] not in units:
                errors.append(f"event_matrix[{index}] 引用未知 analysis_unit_id: {key[0]}")
            if failure_ids and key[1] not in failure_ids:
                errors.append(f"event_matrix[{index}] 引用未知 failure_id: {key[1]}")
            if scenario_ids and key[2] not in scenario_ids:
                errors.append(f"event_matrix[{index}] 引用未知 scenario_id: {key[2]}")
            vehicle_hazard_id = event.get("vehicle_hazard_id")
            if not isinstance(vehicle_hazard_id, str) or vehicle_hazard_id not in hazard_catalog:
                errors.append(f"event_matrix[{index}] 引用未知 vehicle_hazard_id")
            else:
                expected_pattern = next((
                    item for item in analysis.get("hazop_patterns", [])
                    if isinstance(item, dict) and item.get("failure_id") == key[1]
                ), None)
                if isinstance(expected_pattern, dict) and vehicle_hazard_id != expected_pattern.get("vehicle_hazard_id"):
                    errors.append(f"event_matrix[{index}] 的 vehicle_hazard_id 必须与 failure_id 的 HAZOP 模板一致")
            if event.get("severity") == 0:
                if event.get("expected_asil") not in (None, ""):
                    errors.append(f"event_matrix[{index}] S=0 时 expected_asil 必须留空")
                for field in ("exposure", "exposure_reason", "controllability", "controllability_reason", "safety_goal", "safe_state", "ftti"):
                    if event.get(field) is not None:
                        errors.append(f"event_matrix[{index}] S=0 时 {field} 必须为 null")

        contract = pack.get("quality_contract", {}).get("exact_risk_matrix")
        if contract:
            contracts = contract if isinstance(contract, list) else [contract]
            for index, item in enumerate(contracts):
                if not isinstance(item, dict):
                    errors.append(f"quality_contract.exact_risk_matrix[{index}] 必须是对象")
                    continue
                unit_id = item.get("analysis_unit_id")
                set_name = item.get("scenario_set")
                contract_failure_ids = item.get("failure_ids")
                scenarios = scenario_sets.get(set_name, {}).get("scenarios", []) if isinstance(set_name, str) else []
                if not isinstance(unit_id, str) or unit_id not in units:
                    errors.append(f"exact_risk_matrix[{index}] 引用未知 analysis_unit_id")
                    continue
                if not isinstance(contract_failure_ids, list) or not all(isinstance(v, str) and v for v in contract_failure_ids):
                    errors.append(f"exact_risk_matrix[{index}] failure_ids 必须是非空字符串数组")
                    continue
                if not isinstance(scenarios, list) or not scenarios:
                    errors.append(f"exact_risk_matrix[{index}] 引用的 scenario_set 无有效场景")
                    continue
                expected = {
                    (unit_id, failure_id, scenario["scenario_id"])
                    for failure_id in contract_failure_ids
                    for scenario in scenarios
                    if isinstance(scenario, dict) and isinstance(scenario.get("scenario_id"), str)
                }
                actual = {key for key in seen_keys if key[0] == unit_id and key[1] in contract_failure_ids}
                missing = expected - actual
                unexpected = actual - expected
                if missing:
                    errors.append(f"exact_risk_matrix[{index}] 缺少 {len(missing)} 条受控风险事件")
                if unexpected:
                    errors.append(f"exact_risk_matrix[{index}] 存在 {len(unexpected)} 条未声明的受控风险事件")

    return errors


def lookup_risk_matrix_event(
    domain: str, analysis_unit_id: str, failure_id: str, scenario_id: str
) -> dict[str, Any] | None:
    """按域+分析单元+失效 ID+场景 ID 精确查询一条风险事件。

    绝不根据场景文本、异常描述或“第一条近似记录”推断。缺失时返回 None；重复
    键是 Pack 数据错误，直接抛异常，以防 Agent 得到不确定的 S/E/C/SG/FTTI。
    """
    pack = load_domain_pack(domain)
    matches = [
        event for event in pack["risk_catalog"].get("event_matrix", [])
        if isinstance(event, dict)
        and event.get("analysis_unit_id") == analysis_unit_id
        and event.get("failure_id") == failure_id
        and event.get("scenario_id") == scenario_id
    ]
    if len(matches) > 1:
        raise ValueError(
            "Domain Pack 风险矩阵存在重复精确键: "
            f"{analysis_unit_id}/{failure_id}/{scenario_id}"
        )
    return dict(matches[0]) if matches else None


def get_exact_risk_matrix_contract(
    domain: str, analysis_unit_id: str, failure_id: str
) -> dict[str, Any] | None:
    """查找声明某个分析单元/失效 ID 必须精确锁定的矩阵合同。"""
    pack = load_domain_pack(domain)
    contract = pack.get("quality_contract", {}).get("exact_risk_matrix")
    contracts = contract if isinstance(contract, list) else [contract]
    for item in contracts:
        if (isinstance(item, dict)
                and item.get("analysis_unit_id") == analysis_unit_id
                and failure_id in item.get("failure_ids", [])):
            return dict(item)
    return None


def get_exact_risk_matrix_events(
    domain: str, analysis_unit_id: str, failure_id: str
) -> list[dict[str, Any]] | None:
    """按合同顺序返回一整组精确事件；无合同则返回 None。

    合同存在但任一事件缺失属于 Pack 损坏，必须显式失败，不能退化为文本近似匹配。
    """
    contract = get_exact_risk_matrix_contract(domain, analysis_unit_id, failure_id)
    if contract is None:
        return None
    scenario_set = contract.get("scenario_set")
    scenarios = get_scenario_set(domain, scenario_set) if isinstance(scenario_set, str) else []
    if not scenarios:
        raise ValueError(
            f"Domain Pack 精确风险矩阵合同缺少场景集: {domain}/{analysis_unit_id}/{failure_id}"
        )
    result: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_id = scenario.get("scenario_id")
        event = lookup_risk_matrix_event(domain, analysis_unit_id, failure_id, scenario_id)
        if event is None:
            raise ValueError(
                "Domain Pack 精确风险矩阵缺少事件: "
                f"{domain}/{analysis_unit_id}/{failure_id}/{scenario_id}"
            )
        event["scenario"] = scenario.get("text", "")
        result.append(event)
    return result

def get_scenario_set(domain: str, scenario_set: str) -> list[dict[str, str]]:
    """返回当前域一个受控场景集的副本；未知场景集返回空数组。"""
    pack = load_domain_pack(domain)
    scenarios = pack["risk_catalog"].get("scenario_sets", {}).get(scenario_set, {}).get("scenarios", [])
    return [dict(item) for item in scenarios if isinstance(item, dict)]


def pack_inventory(domain: str) -> dict[str, int | str]:
    """返回当前域 Pack 的轻量库存，供 parse 日志与测试使用。"""
    pack = load_domain_pack(domain)
    functions = pack["function_catalog"]
    analysis = pack["analysis_catalog"]
    risk = pack["risk_catalog"]
    return {
        "domain": pack["domain"],
        "status": pack["status"],
        "semantic_roles": len(functions.get("semantic_roles", {})),
        "scope_rules": len(functions.get("scope_rules", [])),
        "analysis_units": len(analysis.get("analysis_units", [])),
        "hazop_patterns": len(analysis.get("hazop_patterns", [])),
        "scenario_sets": len(risk.get("scenario_sets", {})),
        "risk_matrix_rows": len(risk.get("event_matrix", [])),
    }


def _feature_text_candidates(feature: dict[str, Any]) -> list[str]:
    """提取 Feature 语义匹配文本，兼容 parse 产物及组件明细。"""
    values = [
        feature.get("name", ""),
        feature.get("description", ""),
        feature.get("scenario", ""),
    ]
    for component in feature.get("components", []) or []:
        if not isinstance(component, dict):
            continue
        values.extend([
            component.get("name", ""),
            component.get("description", ""),
            component.get("scenario", ""),
            component.get("sub_name", ""),
        ])
    return [str(value).strip() for value in values if isinstance(value, str) and value.strip()]


def resolve_semantic_role(pack: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any]:
    """按域 Pack 的受控语义角色表解析一个 Feature。

    仅使用显式模式的包含匹配；不会使用 Feature List ID 或模糊相似度自动下结论。
    """
    role_map = pack.get("function_catalog", {}).get("semantic_roles", {})
    candidates = _feature_text_candidates(feature)

    # 子功能名称是最直接的业务语义证据。若它与某一受控角色模式完全相同，
    # 应优先于章节/描述中同时出现的相邻功能词（例如“驾驶模式设置及显示”）
    # 判定，避免把“驾驶模式显示”错误标成“驾驶模式控制”。
    feature_name = feature.get("name", "")
    if isinstance(feature_name, str) and feature_name.strip():
        exact_matches = [
            {"semantic_role": role, "pattern": pattern, "evidence": feature_name}
            for role, patterns in role_map.items()
            if isinstance(patterns, list)
            for pattern in patterns
            if isinstance(pattern, str) and pattern == feature_name
        ]
        exact_roles = {item["semantic_role"] for item in exact_matches}
        if len(exact_roles) == 1:
            return {
                "semantic_role": exact_matches[0]["semantic_role"],
                "status": "resolved",
                "matches": exact_matches,
            }
        if len(exact_roles) > 1:
            return {"semantic_role": None, "status": "ambiguous", "matches": exact_matches}

    matches: list[dict[str, str]] = []
    for role, patterns in role_map.items():
        if not isinstance(patterns, list):
            continue
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            for text in candidates:
                if pattern in text:
                    matches.append({"semantic_role": role, "pattern": pattern, "evidence": text})

    if not matches:
        return {"semantic_role": None, "status": "unresolved", "matches": []}

    # 同义模式可能产生同角色的多条证据；多个不同角色则不擅自裁决。
    roles = {item["semantic_role"] for item in matches}
    if len(roles) != 1:
        return {
            "semantic_role": None,
            "status": "ambiguous",
            "matches": matches,
        }
    role = matches[0]["semantic_role"]
    return {"semantic_role": role, "status": "resolved", "matches": matches}


def _matches_function_family(pack: dict[str, Any], func_name: str) -> list[str]:
    result: list[str] = []
    for matcher in pack.get("function_catalog", {}).get("function_matchers", []):
        if not isinstance(matcher, dict):
            continue
        patterns = list(matcher.get("name_patterns", [])) + list(matcher.get("keywords", []))
        if any(isinstance(pattern, str) and pattern and pattern in (func_name or "") for pattern in patterns):
            family = matcher.get("canonical_function_family")
            if isinstance(family, str) and family:
                result.append(family)
    return list(dict.fromkeys(result))


def compile_domain_context(domain: str, related_items: list[dict[str, Any]]) -> dict[str, Any]:
    """编译给 Agent 和后续阶段使用的最小当前域上下文。

    该函数不修改 S1/S2/S3 输出，P0-2 仅提供可审计的解析结果。后续阶段将以其
    作为脚本锁定已知结论的唯一入口。
    """
    pack = load_domain_pack(domain)
    scope_by_role = {
        rule.get("semantic_role"): rule
        for rule in pack["function_catalog"].get("scope_rules", [])
        if isinstance(rule, dict) and isinstance(rule.get("semantic_role"), str)
    }
    units = pack["analysis_catalog"].get("analysis_units", [])

    resolved_items: list[dict[str, Any]] = []
    unresolved_count = 0
    ambiguous_count = 0
    for item in related_items:
        func_id = item.get("func_id", "")
        func_name = item.get("func_name", "")
        families = _matches_function_family(pack, func_name)
        feature_results: list[dict[str, Any]] = []

        for feature in item.get("sub_functions", []) or []:
            role_result = resolve_semantic_role(pack, feature)
            role = role_result["semantic_role"]
            result: dict[str, Any] = {
                "func_id": func_id,
                "feature_list_id": feature.get("feature_list_id", ""),
                "feature_name": feature.get("name", ""),
                "semantic_role": role,
                "semantic_status": role_result["status"],
                "evidence": role_result.get("matches", []),
                "disposition": None,
                "covered_by": None,
                "target_domain": None,
                "reason_code": None,
                "remark": None,
                "evidence_status": "unknown",
            }

            if role_result["status"] == "ambiguous":
                ambiguous_count += 1
            elif role_result["status"] == "unresolved":
                unresolved_count += 1
            elif role in scope_by_role:
                rule = scope_by_role[role]
                result.update({
                    "disposition": rule.get("disposition"),
                    "target_domain": rule.get("target_domain"),
                    "reason_code": rule.get("reason_code"),
                    "remark": rule.get("remark"),
                    "evidence_status": rule.get("evidence_status", "exact_known"),
                })
            else:
                matching_units = [
                    unit for unit in units
                    if role in unit.get("covered_semantic_roles", [])
                    and (not families or unit.get("canonical_function_family") in families)
                ]
                if len(matching_units) == 1:
                    unit = matching_units[0]
                    modes = pack["analysis_catalog"].get("failure_mode_rules", {}).get(unit.get("analysis_unit_id"), [])
                    result.update({
                        "disposition": "covered_by",
                        "covered_by": unit.get("analysis_unit_id"),
                        "reason_code": "COVERED_BY_STANDARD_ANALYSIS_UNIT",
                        "remark": f"由标准分析单元 {unit.get('analysis_unit_id')} 覆盖。",
                        "evidence_status": unit.get("evidence_status", "exact_known"),
                        "recommended_failure_modes": list(modes) if isinstance(modes, list) else [],
                    })
                else:
                    unresolved_count += 1

            feature_results.append(result)

        # 只有需要被标准分析单元覆盖的 Feature 才要求相关项名称命中功能族。
        # 纯排除或跨域移交的相关项（如安全监控及故障报警）同样可以是已知结论。
        requires_analysis_family = any(
            feature["disposition"] in {"analyze", "covered_by"}
            for feature in feature_results
        )
        item_status = "exact_known"
        if (any(feature["disposition"] is None for feature in feature_results)
                or (requires_analysis_family and not families)):
            item_status = "needs_review"
        resolved_items.append({
            "func_id": func_id,
            "func_name": func_name,
            "function_families": families,
            "resolution_status": item_status,
            "features": feature_results,
        })

    status = "exact_known" if resolved_items and not unresolved_count and not ambiguous_count and all(
        item["resolution_status"] == "exact_known" for item in resolved_items
    ) else "needs_review"
    return {
        "domain": pack["domain"],
        "pack_status": pack["status"],
        "resolution_status": status,
        "unresolved_feature_count": unresolved_count,
        "ambiguous_feature_count": ambiguous_count,
        "related_items": resolved_items,
        "analysis_units": pack["analysis_catalog"].get("analysis_units", []),
    }
