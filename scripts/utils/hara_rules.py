# scripts/utils/hara_rules.py
# S4 HARA 确定性规则引擎：ASIL 查表、功能类型配置、ID 分配、验证
# 纯函数库，无 CLI，无 print（被 stage_hara.py 调用）
#
# 数据来源：
#   - ISO 26262-3:2018 Table 4（ASIL determination）
#   - 旧项目（山子高科）+ 新项目交叉验证的工程实践

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from utils.data_models import extract_domain
from utils.domain_packs import load_domain_pack
from utils.project_ids import allocate_project_function_numbers


# ============================================================
# ASIL 判定表（ISO 26262-3:2018 Table 4）
# 键: (S, E, C)，值: ASIL 等级
# C=0 不进表 → QM；S=0 的 ASIL 留空（不适用ASIL）
# ============================================================

ASIL_LOOKUP: dict[tuple[int, int, int], str] = {
    # S1
    (1, 1, 1): "QM", (1, 1, 2): "QM", (1, 1, 3): "QM",
    (1, 2, 1): "QM", (1, 2, 2): "QM", (1, 2, 3): "QM",
    (1, 3, 1): "QM", (1, 3, 2): "QM", (1, 3, 3): "A",
    (1, 4, 1): "QM", (1, 4, 2): "A",  (1, 4, 3): "B",
    # S2
    (2, 1, 1): "QM", (2, 1, 2): "QM", (2, 1, 3): "QM",
    (2, 2, 1): "QM", (2, 2, 2): "QM", (2, 2, 3): "A",
    (2, 3, 1): "QM", (2, 3, 2): "A",  (2, 3, 3): "B",
    (2, 4, 1): "A",  (2, 4, 2): "B",  (2, 4, 3): "C",
    # S3
    (3, 1, 1): "QM", (3, 1, 2): "QM", (3, 1, 3): "A",
    (3, 2, 1): "QM", (3, 2, 2): "A",  (3, 2, 3): "B",
    (3, 3, 1): "A",  (3, 3, 2): "B",  (3, 3, 3): "C",
    (3, 4, 1): "B",  (3, 4, 2): "C",  (3, 4, 3): "D",
}

# ISO 26262-3 clause 6.4.3.11 脚注：S3/E1/C3 = A 但需人工审核
FOOTNOTE_CLAUSE = "6.4.3.11"
FOOTNOTE_KEY = (3, 1, 3)


# ============================================================
# S3 → S4 语义追溯主键
# ============================================================

def _normalize_trace_text(value: object) -> str:
    """稳定归一化用于追溯的工程文本，不依赖 Excel 行号或临时编号。"""
    if not isinstance(value, str):
        return ""
    return "".join(value.strip().lower().translate(str.maketrans({
        "（": "(", "）": ")", "，": ",", "：": ":", "；": ";", "　": " ",
    })).split())


def build_hazard_semantic_key(
    analysis_unit_id: object,
    failure_mode: object,
    anomaly_description: object,
    vehicle_hazard: object,
) -> str:
    """返回稳定的异常→整车危害语义键。

    ``failure_id`` 是运行期追溯编号，不能单独代表工程语义。本键由标准分析单元、
    失效模式、异常表现和整车危害共同生成，用来校验 S3→S4 链路没有在保留 ID 的
    情况下悄悄换义。它不是跨项目强行对齐参考 Excel ID 的机制。
    """
    parts = (
        _normalize_trace_text(analysis_unit_id),
        _normalize_trace_text(failure_mode),
        _normalize_trace_text(anomaly_description),
        _normalize_trace_text(vehicle_hazard),
    )
    payload = "\x1f".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    unit_prefix = parts[0] or "UNRESOLVED"
    return f"{unit_prefix}::HZ::{digest}"


# ============================================================
# 参考数据库：从11个数据组参考Excel提取的S/E/C值
# ============================================================

_SEC_DB_CACHE: dict | None = None


def load_sec_database() -> dict | None:
    """加载 sec_reference_database.json（惰性加载，只加载一次）"""
    global _SEC_DB_CACHE
    if _SEC_DB_CACHE is not None:
        return _SEC_DB_CACHE
    path = Path(__file__).parent.parent.parent / "references" / "sec_reference_database.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        _SEC_DB_CACHE = json.load(f)
    return _SEC_DB_CACHE


def _match_anomaly_type(anomaly_desc: str) -> str:
    """从异常描述中提取失效模式类型"""
    if not anomaly_desc:
        return "unknown"
    a = anomaly_desc
    if any(kw in a for kw in ["丢失", "丧失", "失去", "不足", "无助力", "缺失"]):
        return "loss"
    if "非预期" in a or "意外" in a:
        return "unexpected"
    if any(kw in a for kw in ["过多", "过度", "过大", "过载"]):
        return "over"
    if any(kw in a for kw in ["反向", "相反", "方向错误"]):
        return "reverse"
    if any(kw in a for kw in ["锁定", "卡滞", "卡死", "冻结"]):
        return "lock"
    if any(kw in a for kw in ["错误", "异常", "故障", "失效"]):
        return "fault"
    return "other"


def lookup_sec_for_scenario(scenario_text: str, anomaly_desc: str) -> dict | None:
    """从参考数据库精确查找场景的 S/E/C + 理由 + description。

    匹配策略：
    1. 场景文本完全匹配
    2. 在匹配的场景中按异常类型过滤（丢失/非预期/过度/反向/卡滞）
    3. 取第一个匹配的记录

    返回: {"s", "e", "c", "s_reason", "e_reason", "c_reason", "description", "asil", "safety_goal", ...} 或 None
    """
    db = load_sec_database()
    if not db:
        return None

    scenario_index = db.get("scenario_index", {})
    refs = scenario_index.get(scenario_text)
    if not refs:
        return None

    # 按异常类型过滤
    target_type = _match_anomaly_type(anomaly_desc)

    # 先尝试精确匹配异常类型
    for ref in refs:
        ref_type = _match_anomaly_type(ref.get("anomaly", ""))
        if ref_type == target_type:
            return ref

    # 退化：取第一个
    return refs[0] if refs else None


# ============================================================
# 功能类型配置：安全状态 / FTTI 预填
# ============================================================

FUNCTION_TYPE_CONFIG: dict[str, dict] = {
    "steering_assist": {
        "keywords": ["转向助力", "转向辅助", "EPS", "电动助力转向", "助力转向"],
        "safe_state": "关闭转向辅助",
        # 转向助力丢失通常 C=0（高速下所需力矩远小于驾驶员能力），无 SG
        "ftti": {"非预期": "200ms(TBD)", "过多": "200ms(TBD)", "反向": "200ms(TBD)",
                 "卡滞": "100ms(TBD)", "锁定": "100ms(TBD)",
                 "丢失": None, "default": "200ms(TBD)"},
    },
    "ibs_braking": {
        "keywords": ["行车制动", "IBS", "线控制动", "制动助力", "液压制动",
                      "再生制动", "制动功能", "车辆制动"],
        "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
        "ftti": {"default": "500ms(TBD)"},
    },
    "epb": {
        "keywords": ["驻车制动", "EPB", "电子驻车", "停车制动"],
        # EPB 安全状态按危害方向区分
        "safe_state_by_hazard": {
            "unexpected_apply": "释放驻车制动器",
            "unexpected_release": "驻车制动器不得释放",
            "dynamic_unexpected": "动态驻车制动不使能",
            "insufficient": "驻车制动器不得释放",
            "loss_of_apply": "驻车制动器不得释放",
        },
        "safe_state_default": "按危害方向选择",
        "ftti": {"default": "500ms(TBD)"},
    },
    "vehicle_hold": {
        "keywords": ["车辆保持", "自动驻车", "AVH", "坡道驻车", "上坡辅助",
                      "自动保持"],
        "safe_state_by_hazard": {
            "loss_of_hold": "保持驻车保持力",
            "unexpected_hold": "不提供车辆保持力",
        },
        "safe_state_default": "按危害方向选择",
        "ftti": {"default": "1s(TBD)"},
    },
    "warning": {
        "keywords": ["报警", "提示", "警告灯", "指示灯", "警示", "提醒"],
        "safe_state": "发送对应提示",
        "ftti": {"default": "2s(TBD)"},
    },
    "esc": {
        "keywords": ["ESC", "车身稳定", "VSA", "电子稳定", "车辆稳定性"],
        "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
        "ftti": {"default": "500ms(TBD)"},
    },
}


# ============================================================
# ASIL 计算
# ============================================================

def compute_asil(severity, exposure, controllability):
    """计算 ASIL 等级。

    Args:
        severity: 0-3（0 表示 S0，无伤害）
        exposure: 1-4 或 None（S=0 时为 None）
        controllability: 0-3 或 None（S=0 时为 None）

    Returns:
        (asil_str, footnote_flag):
            asil_str: None（S=0）/"QM"（C=0）/"A"/"B"/"C"/"D"
            footnote_flag: True 表示命中 ISO 脚注需人工审核
    """
    # S=0 → ASIL 留空；E/C 不评定并保持 null。
    if severity == 0:
        return None, False

    # C=0 → QM（总体可控）
    if controllability == 0:
        return "QM", False

    # 工程判断例外由调用方处理（engineering_override），此处只做标准查表

    key = (severity, exposure, controllability)
    asil = ASIL_LOOKUP.get(key)
    if asil is None:
        raise ValueError(f"无效的 S/E/C 组合: S{severity}/E{exposure}/C{controllability}")

    footnote = key == FOOTNOTE_KEY
    return asil, footnote


# ============================================================
# 功能类型推断
# ============================================================

def infer_function_type(func_name: str):
    """按功能名称关键词推断功能类型。

    Returns:
        功能类型字符串（如 "steering_assist"），未匹配返回 None。
    """
    for ftype, config in FUNCTION_TYPE_CONFIG.items():
        for kw in config.get("keywords", []):
            if kw in func_name:
                return ftype
    return None


def get_safe_state(function_type: str, hazard_category: str = None):
    """获取安全状态预填值。

    Args:
        function_type: 功能类型（如 "epb"）
        hazard_category: 危害类别（EPB/车辆保持等按方向区分时需要）

    Returns:
        安全状态字符串，或 None（未知类型）
    """
    config = FUNCTION_TYPE_CONFIG.get(function_type)
    if config is None:
        return None

    if "safe_state" in config:
        return config["safe_state"]

    if "safe_state_by_hazard" in config:
        if hazard_category and hazard_category in config["safe_state_by_hazard"]:
            return config["safe_state_by_hazard"][hazard_category]
        return config.get("safe_state_default")

    return None


def get_ftti(function_type: str, failure_mode: str = None):
    """获取 FTTI 预填值。

    Args:
        function_type: 功能类型
        failure_mode: 失效模式（转向助力按模式区分 FTTI）

    Returns:
        FTTI 字符串（如 "200ms"），或 None（无 SG 时）
    """
    config = FUNCTION_TYPE_CONFIG.get(function_type)
    if config is None:
        return None

    ftti_map = config.get("ftti", {})
    if failure_mode and failure_mode in ftti_map:
        return ftti_map[failure_mode]
    return ftti_map.get("default")


def infer_hazard_category(function_type: str, anomaly_desc: str,
                          hazard_desc: str = "") -> str | None:
    """根据异常表现和危害描述推断危害方向（用于 EPB/车辆保持等按方向区分安全状态的功能）。

    Args:
        function_type: 功能类型
        anomaly_desc: 异常表现描述
        hazard_desc: 整车危害描述（可选）

    Returns:
        危害方向字符串（如 "unexpected_apply"），无法推断返回 None
    """
    if function_type == "epb":
        if "动态制动" in anomaly_desc:
            return "dynamic_unexpected"
        if "非预期接合" in anomaly_desc or "意外接合" in anomaly_desc:
            return "unexpected_apply"
        if "非预期释放" in anomaly_desc or "意外释放" in anomaly_desc:
            return "unexpected_release"
        if "制动不足" in anomaly_desc or "制动力不足" in anomaly_desc:
            return "insufficient"
        if "接合" in anomaly_desc and ("丧失" in anomaly_desc or "失效" in anomaly_desc):
            return "loss_of_apply"
        return None

    if function_type == "vehicle_hold":
        if "丢失" in anomaly_desc or "丧失" in anomaly_desc or "失效" in anomaly_desc:
            return "loss_of_hold"
        if "非预期" in anomaly_desc:
            return "unexpected_hold"
        return None

    return None


# ============================================================
# 场景资产加载：按域文件加载，兼容旧版总表
# ============================================================

_SCENARIO_MANIFEST_CACHE: dict | None = None
_SCENARIO_DOMAIN_CACHE: dict[str, dict] = {}
_SCENARIO_LOOKUP_CACHE: dict | None = None
_HARDCODED_SCEN_CACHE: dict | None = None

_DOMAIN_ALIASES = {
    "P": ["PT", "POWERTRAIN"], "PT": ["P", "POWERTRAIN"],
    "A": ["AD", "ADAS"], "AD": ["A", "ADAS"],
    "B": ["BD", "BODY"], "BD": ["B", "BODY"],
    "Info": ["ET", "INFOTAINMENT"], "ET": ["Info", "INFOTAINMENT"],
    "CB": ["CHASSIS_BRAKE"], "CS": ["CHASSIS_STEERING"],
}


def _scenario_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "references" / "scenarios"


def _legacy_scenario_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "references" / "scenario_lookup_table.json"


def load_scenario_manifest() -> dict:
    """加载拆分场景资产 manifest；缺失时返回空字典。"""
    global _SCENARIO_MANIFEST_CACHE
    if _SCENARIO_MANIFEST_CACHE is not None:
        return _SCENARIO_MANIFEST_CACHE
    try:
        _SCENARIO_MANIFEST_CACHE = json.loads((_scenario_dir() / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _SCENARIO_MANIFEST_CACHE = {}
    return _SCENARIO_MANIFEST_CACHE


def canonical_scenario_domain(domain: str) -> str:
    """将项目域别名归一化为拆分场景表的标准域代码。"""
    raw = (domain or "").strip()
    if not raw:
        return raw
    aliases = load_scenario_manifest().get("domain_aliases", {})
    if raw in aliases:
        return aliases[raw]
    for alias, canonical in aliases.items():
        if alias.lower() == raw.lower():
            return canonical
    return raw


def _expand_domain_pack_lookup(value: object, catalog: dict) -> object:
    """将 PT Pack 中的 scenario_ids 展开为运行时只读场景文本视图。"""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if key == "scenario_ids" and isinstance(child, list):
                scenarios = []
                for scenario_id in child:
                    item = catalog.get(scenario_id, {}) if isinstance(catalog, dict) else {}
                    text = item.get("text") if isinstance(item, dict) else None
                    if isinstance(text, str) and text.strip():
                        scenarios.append(text)
                result["scenarios"] = scenarios
            else:
                result[key] = _expand_domain_pack_lookup(child, catalog)
        return result
    if isinstance(value, list):
        return [_expand_domain_pack_lookup(child, catalog) for child in value]
    return value


def _load_domain_pack_scenarios(domain: str) -> dict:
    """从指定域 Domain Pack 构造运行时场景视图。"""
    canonical = canonical_scenario_domain(domain)
    pack = load_domain_pack(canonical)
    risk = pack.get("risk_catalog", {})
    catalog = risk.get("scenario_catalog", {}) if isinstance(risk, dict) else {}
    lookup = risk.get("scenario_lookup", {}) if isinstance(risk, dict) else {}
    return {
        "schema_version": "domain_pack_single_source",
        "domain": canonical,
        "domain_name": pack.get("domain_name", ""),
        "function_types": _expand_domain_pack_lookup(lookup, catalog),
        "scenario_sets": risk.get("scenario_sets", {}) if isinstance(risk, dict) else {},
        "scenario_asset": f"references/domain_packs/{canonical}.json",
    }


def reset_scenario_asset_caches() -> None:
    """清空场景资产缓存，供 PT 单源迁移回归测试和热重载调用。"""
    global _SCENARIO_MANIFEST_CACHE, _SCENARIO_LOOKUP_CACHE, _HARDCODED_SCEN_CACHE
    _SCENARIO_MANIFEST_CACHE = None
    _SCENARIO_LOOKUP_CACHE = None
    _HARDCODED_SCEN_CACHE = None
    _SCENARIO_DOMAIN_CACHE.clear()


def _load_legacy_scenario_lookup() -> dict:
    path = _legacy_scenario_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_domain_scenario(domain: str, allow_legacy: bool = True) -> dict:
    """加载指定域场景；启用单源合同的域从 Domain Pack 读取，否则兼容旧场景资产。"""
    canonical = canonical_scenario_domain(domain)
    if canonical in _SCENARIO_DOMAIN_CACHE:
        return _SCENARIO_DOMAIN_CACHE[canonical]
    try:
        pack = load_domain_pack(canonical)
    except ValueError:
        pack = None
    risk = pack.get("risk_catalog", {}) if isinstance(pack, dict) else {}
    if isinstance(risk, dict) and risk.get("scenario_single_source_contract") and isinstance(risk.get("scenario_catalog"), dict):
        _SCENARIO_DOMAIN_CACHE[canonical] = _load_domain_pack_scenarios(canonical)
        return _SCENARIO_DOMAIN_CACHE[canonical]
    manifest = load_scenario_manifest()
    filename = manifest.get("domain_files", {}).get(canonical, f"{canonical}.json")
    path = _scenario_dir() / filename
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if not data and allow_legacy:
        legacy = _load_legacy_scenario_lookup()
        old = legacy.get("domains", {}).get(canonical, {})
        if old:
            data = {"schema_version": "legacy", "domain": canonical,
                    "domain_name": old.get("domain_name", ""),
                    "function_types": old.get("function_types", {}),
                    "scenario_sets": {}}
        elif canonical in ("CS", "CB"):
            sets = legacy.get("hardcoded_scenarios", {}).get("scenarios", {})
            allowed = (["steering_assist"] if canonical == "CS" else [
                "epb_apply_loss", "epb_release_loss", "epb_unexpected_apply",
                "epb_unexpected_release", "epb_dynamic", "epb_dynamic_loss",
                "brake_loss", "brake_unexpected"])
            data = {"schema_version": "legacy", "domain": canonical,
                    "domain_name": "底盘转向域" if canonical == "CS" else "底盘制动域",
                    "function_types": {},
                    "scenario_sets": {k: sets[k] for k in allowed if k in sets}}
    _SCENARIO_DOMAIN_CACHE[canonical] = data or {}
    return _SCENARIO_DOMAIN_CACHE[canonical]


def load_scenario_profile(profile_id: str) -> dict:
    """加载域级分析 Profile（如 CS_steering_assist）。"""
    path = Path(__file__).resolve().parent.parent.parent / "references" / "profiles" / f"{profile_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_feature_aliases(domain: str) -> dict:
    """加载域级功能语义别名表。"""
    canonical = canonical_scenario_domain(domain)
    path = Path(__file__).resolve().parent.parent.parent / "references" / "aliases" / f"{canonical}_feature_aliases.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_scenario_set(key: str) -> dict:
    """按场景集名称懒加载所属域文件。"""
    owner = load_scenario_manifest().get("scenario_set_domains", {}).get(key)
    if owner:
        return load_domain_scenario(owner).get("scenario_sets", {}).get(key, {})
    for domain in ("CS", "CB", "PT", "AD", "ET", "BD"):
        value = load_domain_scenario(domain).get("scenario_sets", {}).get(key)
        if value:
            return value
    return {}


def load_all_scenario_sets() -> dict:
    """返回兼容旧调用方的扁平场景集索引（显式调用时才加载全部域）。"""
    global _HARDCODED_SCEN_CACHE
    if _HARDCODED_SCEN_CACHE is not None:
        return _HARDCODED_SCEN_CACHE
    merged = {}
    for domain in ("CS", "CB", "PT", "AD", "ET", "BD"):
        merged.update(load_domain_scenario(domain).get("scenario_sets", {}))
    if not merged:
        merged = _load_legacy_scenario_lookup().get("hardcoded_scenarios", {}).get("scenarios", {})
    _HARDCODED_SCEN_CACHE = merged
    return merged


def _load_hardcoded_scenarios(domain: str | None = None) -> dict:
    """加载 CS/CB 等域的场景集；保留旧函数名供现有分支调用。"""
    return (load_domain_scenario(domain).get("scenario_sets", {}) if domain
            else load_all_scenario_sets())


def load_scenario_lookup() -> dict:
    """兼容旧调用方的聚合视图；实际查询按域懒加载。"""
    global _SCENARIO_LOOKUP_CACHE
    if _SCENARIO_LOOKUP_CACHE is not None:
        return _SCENARIO_LOOKUP_CACHE
    manifest = load_scenario_manifest()
    domains = {}
    domain_codes = list(manifest.get("domain_files", {}))
    for domain in manifest.get("domain_pack_domains", []):
        if domain not in domain_codes:
            domain_codes.append(domain)
    for domain in domain_codes:
        data = load_domain_scenario(domain)
        domains[domain] = {"domain_name": data.get("domain_name", ""),
                           "function_types": data.get("function_types", {})}
    _SCENARIO_LOOKUP_CACHE = {"version": manifest.get("schema_version", "3.0"),
                              "source": "split-domain scenario assets",
                              "domains": domains,
                              "hardcoded_scenarios": {"scenarios": load_all_scenario_sets()}}
    return _SCENARIO_LOOKUP_CACHE


def lookup_scenarios(domain: str, func_name: str,
                     failure_mode: str = "", anomaly_desc: str = "") -> dict | None:
    """按域懒加载并按功能/失效模式查找场景。"""
    domain_data = load_domain_scenario(domain)
    if not domain_data:
        return None
    for ftype_key, ftype_data in domain_data.get("function_types", {}).items():
        if not any(kw in func_name for kw in ftype_data.get("keywords", [])):
            continue
        failure_modes = ftype_data.get("failure_modes", {})
        fm_data = failure_modes.get(failure_mode) if failure_mode else None
        if fm_data is None and anomaly_desc:
            for fm_val in failure_modes.values():
                if any(kw in anomaly_desc for kw in fm_val.get("anomaly_keywords", [])):
                    fm_data = fm_val
                    break
        if fm_data is None or not fm_data.get("scenarios", []):
            continue
        canonical = canonical_scenario_domain(domain)
        return {"scenarios": fm_data["scenarios"],
                "safety_goal": fm_data.get("safety_goal"),
                "safe_state": fm_data.get("safe_state"),
                "ftti": fm_data.get("ftti"),
                "guidance": fm_data.get("guidance", ""),
                "is_reference": False, "reference_to": None,
                "source": f"scenario_lookup:{canonical}",
                "scenario_asset": ("references/domain_packs/PT.json" if canonical == "PT" else f"{canonical}.json"),
                "function_type": ftype_key}
    return None


# ============================================================
# 预填数据：根据功能类型和异常描述返回标准场景集
# ============================================================

def _scenario_domain_for_function_type(function_type: str) -> str | None:
    if function_type == "steering_assist":
        return "CS"
    if function_type in {"epb", "brake", "esc", "vehicle_hold"}:
        return "CB"
    return None


def get_prefill_data(function_type: str, anomaly_desc: str,
                     failure_mode: str = "", hazard_desc: str = "") -> dict | None:
    """根据功能类型和异常描述返回预填数据（场景、安全目标、安全状态、FTTI）。

    Returns:
        dict with keys:
            scenarios: list of scenario strings
            scenario_safety_goals: list[str] or None (场景级安全目标；非None时按索引对应 scenarios，
                用于承载原硬编码元组 (场景, SG) 语义，如 IBS 非预期制动后3个不对称场景)
            safety_goal: str or None (默认安全目标文本)
            safe_state: str or None
            ftti: str or None
            guidance: str (Agent 填写指引)
            is_reference: bool (是否为引用行)
            reference_to: str or None (引用的 failure_id 关键词)
        无法匹配时返回 None。
    """
    if not function_type or not anomaly_desc:
        return None

    a = anomaly_desc

    # 硬编码场景集（从速查表加载，保留智能分支逻辑）
    hc = _load_hardcoded_scenarios(_scenario_domain_for_function_type(function_type))

    # ---- 转向助力 ----
    if function_type == "steering_assist":
        is_lock = any(kw in a for kw in ["锁定", "卡滞"])
        sg = "防止车辆横向运动失控" if is_lock else "防止车辆非预期横向运动"
        ftti = "100ms(TBD)" if is_lock else "200ms(TBD)"
        # 丢失助力时 C=0（QM）；非预期时高速直线 S=3/D；过度/反向/卡滞时高速直线 S=0
        is_loss = any(kw in a for kw in ["丢失", "丧失", "失去", "不足"])
        is_unexpected = "非预期" in a
        if is_loss:
            guidance = ("转向助力丢失时，机械转向备份始终可用，驾驶员可保持方向控制，"
                        "所有车速场景 C=0（QM），仅急弯低速可 C=1（仍 QM）；"
                        "静止/高速直线场景 S=0。"
                        "S/E/C 已预填建议值，请确认并填写理由。")
        elif is_unexpected:
            guidance = ("转向非预期激活时，系统自主提供助力导致突然转向，C 偏高（1-3）；"
                        "静止场景 S=0，高速直线场景 S3/E4/C3=D。"
                        "S/E/C 已预填建议值，请确认并填写理由。")
        else:
            # 过度/反向/卡滞：故障仅在驾驶员有转向输入时才表现
            guidance = ("转向过度/反向/卡滞时，非预期转向导致 C 偏高（1-3）；"
                        "静止场景 S=0，高速直线场景 S=0（驾驶员直线行驶无转向输入，故障不表现）。"
                        "S/E/C 已预填建议值，请确认并填写理由。")
        return {
            "scenarios": hc.get("steering_assist", {}).get("scenarios", []),
            "safety_goal": sg,
            "safe_state": "关闭转向辅助",
            "ftti": ftti,
            "guidance": guidance,
            "is_reference": False,
            "reference_to": None,
        }

    # ---- EPB ----
    if function_type == "epb":
        h = hazard_desc or ""

        # 动态制动非预期激活（最具体，含"动态制动"+"非预期"）
        if "动态制动" in a and "非预期" in a:
            return {
                "scenarios": hc.get("epb_dynamic", {}).get("scenarios", []),
                "safety_goal": "防止车辆非预期减速由于动态制动非预期激活",
                "safe_state": "动态驻车制动不使能",
                "ftti": "500ms(TBD)",
                "guidance": "动态制动非预期激活导致纵向减速，请评定每个场景的 S/E/C。低速场景可工程判断 QM。",
                "is_reference": False,
                "reference_to": None,
            }
        # 动态制动丧失（含"动态制动"+"丧失/失效/功能丧失"）
        if "动态制动" in a and any(kw in a for kw in ["丧失", "失效", "功能丧失", "丢失"]):
            return {
                "scenarios": hc.get("epb_dynamic_loss", {}).get("scenarios", []),
                "safety_goal": "防止因动态制动功能丧失导致制动距离延长",
                "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                "ftti": "500ms(TBD)",
                "guidance": "动态制动丧失导致制动力不足，制动距离延长，请评定每个场景的 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        # 非预期接合/夹紧（含"非预期"+"接合/夹紧/驻车夹紧"）
        if "非预期" in a and any(kw in a for kw in ["接合", "夹紧", "驻车夹紧"]):
            return {
                "scenarios": hc.get("epb_unexpected_apply", {}).get("scenarios", []),
                "safety_goal": "防止非预期接合驻车制动当车速大于TBD",
                "safe_state": "释放驻车制动器",
                "ftti": "500ms(TBD)",
                "guidance": "非预期接合导致横向运动和纵向减速，请评定每个场景的 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        # 非预期释放
        if "非预期" in a and "释放" in a:
            return {
                "scenarios": hc.get("epb_unexpected_release", {}).get("scenarios", []),
                "safety_goal": "防止非预期释放驻车制动器导致的车辆意外移动",
                "safe_state": "驻车制动器不得释放",
                "ftti": "500ms(TBD)",
                "guidance": "非预期释放导致溜车，驾驶员在车内时 C=1-2，离开时 C=3。C1+低速+驾驶员在车内可工程判断 QM。",
                "is_reference": False,
                "reference_to": None,
            }
        # 接合丧失/无法夹紧（含"接合"+"丧失类"，或直接含"无法夹紧/夹紧失效"等）
        if (any(kw in a for kw in ["接合", "无法夹紧", "夹紧失效", "夹紧丧失", "无法接合", "无法驻车"])
                and any(kw in a for kw in ["丧失", "失效", "不能", "无法", "丢失"])):
            return {
                "scenarios": hc.get("epb_apply_loss", {}).get("scenarios", []),
                "safety_goal": "防止因驻车制动器未接合导致的车辆意外移动",
                "safe_state": "驻车制动器不得释放",
                "ftti": "500ms(TBD)",
                "guidance": "接合丧失导致溜车，驾驶员在车内时 C=1-2，离开时 C=3。C1+低速+驾驶员在车内可工程判断 QM。",
                "is_reference": False,
                "reference_to": None,
            }
        # "无法夹紧"本身即接合丧失（无"丧失"关键词时也命中）
        if any(kw in a for kw in ["无法夹紧", "夹紧失效", "夹紧丧失", "无法接合"]):
            return {
                "scenarios": hc.get("epb_apply_loss", {}).get("scenarios", []),
                "safety_goal": "防止因驻车制动器未接合导致的车辆意外移动",
                "safe_state": "驻车制动器不得释放",
                "ftti": "500ms(TBD)",
                "guidance": "接合丧失导致溜车，驾驶员在车内时 C=1-2，离开时 C=3。C1+低速+驾驶员在车内可工程判断 QM。",
                "is_reference": False,
                "reference_to": None,
            }
        # 释放丧失/失效
        if "释放" in a and any(kw in a for kw in ["丧失", "失效", "不能", "无法", "丢失"]):
            return {
                "scenarios": hc.get("epb_release_loss", {}).get("scenarios", []),
                "safety_goal": None,
                "safe_state": None,
                "ftti": None,
                "guidance": "释放丧失导致车辆无法移动，城市道路起步场景 S=0（无事故），ASIL 留空。",
                "is_reference": False,
                "reference_to": None,
            }
        # 不足类：根据危害描述判断复用哪个场景（不再用 is_reference 引用）
        if "不足" in a:
            if "无法起步" in h or "无法移动" in h:
                return {
                    "scenarios": hc.get("epb_release_loss", {}).get("scenarios", []),
                    "safety_goal": None,
                    "safe_state": None,
                    "ftti": None,
                    "guidance": "释放力不足导致车辆无法起步，城市道路起步场景 S=0（无事故），ASIL 留空。",
                    "is_reference": False,
                    "reference_to": None,
                }
            if "制动距离" in h:
                return {
                    "scenarios": hc.get("epb_dynamic_loss", {}).get("scenarios", []),
                    "safety_goal": "防止因动态制动功能丧失导致制动距离延长",
                    "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                    "ftti": "500ms(TBD)",
                    "guidance": "动态制动力不足导致制动距离延长，请评定每个场景的 S/E/C。",
                    "is_reference": False,
                    "reference_to": None,
                }
            # 默认：夹紧力不足导致溜车，复用接合丧失场景
            return {
                "scenarios": hc.get("epb_apply_loss", {}).get("scenarios", []),
                "safety_goal": "防止因驻车制动器未接合导致的车辆意外移动",
                "safe_state": "驻车制动器不得释放",
                "ftti": "500ms(TBD)",
                "guidance": "夹紧力不足导致溜车，危害事件与接合丧失相同，请评定每个场景的 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        # 过大类：根据危害描述判断复用哪个场景
        if any(kw in a for kw in ["过大", "过度"]):
            if "非预期移动" in h:
                return {
                    "scenarios": hc.get("epb_unexpected_release", {}).get("scenarios", []),
                    "safety_goal": "防止非预期释放驻车制动器导致的车辆意外移动",
                    "safe_state": "驻车制动器不得释放",
                    "ftti": "500ms(TBD)",
                    "guidance": "释放力过大导致溜车，危害事件与非预期释放相同，请评定每个场景的 S/E/C。",
                    "is_reference": False,
                    "reference_to": None,
                }
            if "非预期减速" in h:
                return {
                    "scenarios": hc.get("epb_dynamic", {}).get("scenarios", []),
                    "safety_goal": "防止车辆非预期减速由于动态制动非预期激活",
                    "safe_state": "动态驻车制动不使能",
                    "ftti": "500ms(TBD)",
                    "guidance": "动态制动力过大导致非预期减速，危害事件与动态制动非预期相同，请评定每个场景的 S/E/C。",
                    "is_reference": False,
                    "reference_to": None,
                }
            # 过热类（制动器过热/卡钳损坏）：不预填场景，Agent 自行评定
            return None
        return None

    # ---- IBS 行车制动 ----
    if function_type == "ibs_braking":
        # 制动过度 → 复用非预期激活场景（不再用 is_reference 引用）
        if "过度" in a:
            return {
                "scenarios": hc.get("brake_unexpected", {}).get("scenarios", []),
                "scenario_safety_goals": hc.get("brake_unexpected", {}).get("scenario_safety_goals"),
                "safety_goal": "避免提供非预期的制动扭矩",
                "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                "ftti": "500ms(TBD)",
                "guidance": "制动过度的危害事件与非预期激活相同，请评定每个场景的 S/E/C。低速场景可 QM。",
                "is_reference": False,
                "reference_to": None,
            }
        # 制动不足 → 复用制动失效场景（不再用 is_reference 引用）
        if "不足" in a:
            return {
                "scenarios": hc.get("brake_loss", {}).get("scenarios", []),
                "safety_goal": "避免丢失制动扭矩或制动扭矩小于请求值",
                "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                "ftti": "500ms(TBD)",
                "guidance": "制动不足的危害事件与制动功能失效相同，请评定每个场景的 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        # 制动非预期激活
        if "非预期" in a and any(kw in a for kw in ["激活", "制动", "启动", "提供"]):
            return {
                "scenarios": hc.get("brake_unexpected", {}).get("scenarios", []),
                "scenario_safety_goals": hc.get("brake_unexpected", {}).get("scenario_safety_goals"),
                "safety_goal": "避免提供非预期的制动扭矩",
                "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                "ftti": "500ms(TBD)",
                "guidance": "非预期制动导致纵向减速和不对称横向运动，后3个场景安全目标不同（不对称制动扭矩）。低速场景可 QM。",
                "is_reference": False,
                "reference_to": None,
            }
        # 制动失效/丧失
        if any(kw in a for kw in ["失效", "丧失", "丢失"]) and any(kw in a for kw in ["制动", "减速"]):
            return {
                "scenarios": hc.get("brake_loss", {}).get("scenarios", []),
                "safety_goal": "避免丢失制动扭矩或制动扭矩小于请求值",
                "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                "ftti": "500ms(TBD)",
                "guidance": "制动失效时 C 通常为3（驾驶员无法有效制动），请按场景评定 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        return None

    # ---- ESC ----
    if function_type == "esc":
        if "非预期" in a:
            return {
                "scenarios": hc.get("epb_unexpected_apply", {}).get("scenarios", []),  # 横向运动场景
                "safety_goal": "防止车辆非预期横向运动由于ESC非预期激活",
                "safe_state": "1.IBS不使能; 2.制动力矩由机械液压部分驱动",
                "ftti": "500ms(TBD)",
                "guidance": "ESC非预期激活导致横向运动，请评定每个场景的 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        return None

    # ---- 车辆保持 ----
    if function_type == "vehicle_hold":
        if any(kw in a for kw in ["丢失", "丧失", "失效"]):
            return {
                "scenarios": hc.get("epb_apply_loss", {}).get("scenarios", []),  # 溜车场景
                "safety_goal": "防止车辆保持力丢失导致车辆意外移动",
                "safe_state": "保持驻车保持力",
                "ftti": "1s(TBD)",
                "guidance": "车辆保持丢失导致溜车，请参考溜车场景评定 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        if "非预期" in a:
            return {
                "scenarios": hc.get("epb_dynamic", {}).get("scenarios", []),  # 纵向减速场景
                "safety_goal": "防止非预期车辆保持导致车辆非预期减速",
                "safe_state": "不提供车辆保持力",
                "ftti": "1s(TBD)",
                "guidance": "非预期车辆保持导致纵向减速，请评定每个场景的 S/E/C。",
                "is_reference": False,
                "reference_to": None,
            }
        return None

    return None


# ============================================================
# 预填合规性检查（安全状态/FTTI 加锁）
# ============================================================

def validate_prefill_compliance(hazard: dict) -> list:
    """检查 Agent 是否覆盖了锁定的预填值（安全状态、FTTI、场景数）。

    允许 QM 或 ASIL 留空 的 S=0 事件清空安全状态和 FTTI（这是正确的）。
    仅当 ASIL>=A 时才强制要求安全状态/FTTI 与预填值一致。

    Returns:
        list of (level, message)
    """
    issues = []

    if hazard.get("skip"):
        return issues

    prefill_safe_state = hazard.get("safe_state_prefill")
    prefill_ftti = hazard.get("ftti_prefill")
    prefill_scenarios = hazard.get("prefill_scenarios", [])
    prefill_locked = hazard.get("prefill_locked", False)

    for i, event in enumerate(hazard.get("events", [])):
        # 判断该事件是否为 QM、S=0 或 C=0（安全状态/FTTI 应为空）
        asil = event.get("asil")
        is_qm = (
            asil in ("QM", "-") or asil is None
            or event.get("severity") == 0
            or event.get("controllability") == 0
            or event.get("engineering_override") == "QM"
        )

        # 安全状态加锁检查
        if prefill_locked and prefill_safe_state:
            ev_ss = event.get("safe_state")
            if is_qm:
                # QM 或 ASIL 留空 的 S=0 事件：安全状态应为空，允许清空
                if ev_ss and ev_ss != prefill_safe_state:
                    issues.append(("error",
                        f"event[{i}]: safe_state 被覆盖为 '{ev_ss}'，"
                        f"预填值为 '{prefill_safe_state}'，禁止修改安全状态"))
                # ev_ss 为 None 或等于预填值都可以
            else:
                # ASIL>=A 事件：安全状态必须与预填值一致
                if ev_ss != prefill_safe_state:
                    issues.append(("error",
                        f"event[{i}]: safe_state 为 '{ev_ss}'，"
                        f"预填值为 '{prefill_safe_state}'，ASIL>=A 时禁止修改安全状态"))

        # FTTI 加锁检查
        if prefill_locked and prefill_ftti:
            ev_ftti = event.get("ftti")
            if is_qm:
                if ev_ftti and ev_ftti != prefill_ftti:
                    issues.append(("error",
                        f"event[{i}]: ftti 被覆盖为 '{ev_ftti}'，"
                        f"预填值为 '{prefill_ftti}'，禁止修改 FTTI"))
            else:
                if ev_ftti != prefill_ftti:
                    issues.append(("error",
                        f"event[{i}]: ftti 为 '{ev_ftti}'，"
                        f"预填值为 '{prefill_ftti}'，ASIL>=A 时禁止修改 FTTI"))

    # 场景数检查（预填了场景但 Agent 删空了）
    if prefill_locked and prefill_scenarios:
        actual_scenarios = [
            e.get("scenario", "").strip()
            for e in hazard.get("events", [])
            if e.get("scenario", "").strip()
        ]
        if len(actual_scenarios) < len(prefill_scenarios):
            issues.append(("warning",
                f"预填了 {len(prefill_scenarios)} 个标准场景，"
                f"当前只有 {len(actual_scenarios)} 个，请勿随意删除标准场景。"
                f"如确需调整请在 note 中说明理由"))

    return issues


# ============================================================
# ID 分配
# ============================================================


def _function_number_map(hazards: list[dict]) -> dict[str, str]:
    """为当前 S4 中的项目功能分配不冲突的编号。

    兼容 ``P_func_0001``，也支持 ``F16`` 等项目自定义编号。无法提取数字
    或数字与其他功能冲突时，按首次出现顺序分配尚未使用的编号，禁止统一
    回退为 ``00`` 导致跨功能 hazard/SG ID 重复。
    """
    ordered_ids: list[str] = []
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        func_id = str(hazard.get("func_id") or "").strip()
        if func_id and func_id not in ordered_ids:
            ordered_ids.append(func_id)

    return allocate_project_function_numbers(ordered_ids, width=2)

def assign_hazard_ids(hazards: list, domain: str):
    """为所有事件分配 hazard_id，为每个 hazard 组分配 id_range。

    编号规则：{域}_hzrd_{功能2位编号}{3位流水号}
    流水号在同一功能内从 001 开始连续递增，跨失效模式不重置。
    功能编号从 func_id 提取（如 CS_func_0001 → 01）。

    跳过 skip=true 的 hazard 组。

    Returns:
        (id_map, range_map):
            id_map: {failure_id: [hazard_id, ...]} 用于回填 HAZOP G 列
            range_map: {failure_id: "CS_hzrd_01001-CS_hzrd_01019"}
    """
    # 按 func_id 分组，保持顺序
    func_counters = {}  # {func_id: next_seq}
    func_nums = _function_number_map(hazards)
    id_map = {}         # {failure_id: [hazard_id, ...]}
    range_map = {}      # {failure_id: "id_start-id_end"}

    for hazard in hazards:
        if hazard.get("skip"):
            continue

        func_id = hazard["func_id"]
        failure_id = hazard["failure_id"]

        if func_id not in func_counters:
            func_counters[func_id] = 1

        func_num = func_nums[func_id]

        events = hazard.get("events", [])
        if not events:
            continue

        first_id = None
        last_id = None

        for event in events:
            seq = func_counters[func_id]
            hazard_id = f"{domain}_hzrd_{func_num}{seq:03d}"
            event["hazard_id"] = hazard_id

            if failure_id not in id_map:
                id_map[failure_id] = []
            id_map[failure_id].append(hazard_id)

            if first_id is None:
                first_id = hazard_id
            last_id = hazard_id

            func_counters[func_id] += 1

        # 记录该 hazard 组的 ID 范围
        if first_id and last_id:
            hazard["id_range"] = f"{first_id}-{last_id}" if first_id != last_id else first_id
            # range_map 按 failure_id 聚合（一个 failure_id 可能有多个 hazard 组）
            if failure_id not in range_map:
                range_map[failure_id] = hazard["id_range"]
            else:
                # 扩展范围
                old_start = range_map[failure_id].replace("~", "-").split("-")[0]
                range_map[failure_id] = f"{old_start}-{last_id}"

    return id_map, range_map


def assign_safety_goal_ids(hazards: list, domain: str):
    """为 ASIL≥A 的事件分配 safety_goal_id。

    编号规则：{域}_SG_{功能2位编号}{3位流水号}
    流水号在同一功能内对非 QM 事件从 001 开始连续递增（QM 事件不编号，跳过）。
    每个非 QM 事件分配独立 SG ID（与参考 Excel 一致，即使 SG 文本相同）。
    """
    sg_counters = {}   # {func_id: {"seq": next_seq, "func_num": "01"}}
    func_nums = _function_number_map(hazards)

    for hazard in hazards:
        if hazard.get("skip"):
            continue

        func_id = hazard["func_id"]
        if func_id not in sg_counters:
            sg_counters[func_id] = {"seq": 1, "func_num": func_nums[func_id]}

        counter = sg_counters[func_id]

        for event in hazard.get("events", []):
            asil = event.get("asil")
            sg_text = event.get("safety_goal")

            # S=0（ASIL 留空）或其他 QM 事件均不分配 SG ID。
            if not asil or asil in ("QM", "-") or not sg_text:
                event["safety_goal_id"] = None
                continue

            # 每个非 QM 事件分配独立 SG ID（参考 Excel 模式）
            sg_id = f"{domain}_SG_{counter['func_num']}{counter['seq']:03d}"
            event["safety_goal_id"] = sg_id
            counter["seq"] += 1


def clear_derived_hara_fields(hazards: list[dict]) -> None:
    """清除 Agent/旧版本可能携带的 S4 派生字段。

    ``asil``、``hazard_id``、``safety_goal_id`` 和 hazard 级 ``id_range``
    不是 Agent 的输入事实，而是 S4 校验阶段根据当前项目数据重新生成的
    派生结果。验证前先清除它们，避免重复验证或旧 JSON 中的值被误认为可信。

    不清除工程输入（S/E/C、理由、安全目标、安全状态、FTTI），也不清除
    来源追溯字段。对 ``skip=true`` 组同样只清除派生字段，不吞掉其携带的
    event；携带 event 仍由 ``validate_hazard_group`` 报错。
    """
    for hazard in hazards:
        if not isinstance(hazard, dict):
            continue
        hazard.pop("id_range", None)
        events = hazard.get("events", [])
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            event.pop("asil", None)
            event.pop("hazard_id", None)
            event.pop("safety_goal_id", None)


# ============================================================
# 验证
# ============================================================

# S4 中的 skip 只能使用受控的工程处置，不能用自由文本或模糊的“无”字眼
# 把真实危害事件静默吞掉。正常的 QM/S=0 场景仍必须保留为事件，不能 skip。
CONTROLLED_SKIP_REASON_CODES = frozenset({
    "NO_VEHICLE_LEVEL_HAZARD",
    "OUT_OF_DOMAIN_TRANSFER",
    "COVERED_BY_OTHER_ANALYSIS_UNIT",
})


def _hazard_text(hazard: dict) -> str:
    """兼容早期 S4 夹具中的 description，同时以正式 hazard 字段为准。"""
    value = hazard.get("hazard")
    if not isinstance(value, str) or not value.strip():
        value = hazard.get("description")
    return value.strip() if isinstance(value, str) else ""


def validate_event(event: dict, hazard: dict):
    """验证单个 HARA 事件。

    Returns:
        list of (level, message)，level 为 "error" 或 "warning"
    """
    issues = []

    severity = event.get("severity")
    exposure = event.get("exposure")
    controllability = event.get("controllability")
    severity_reason = event.get("severity_reason")
    exposure_reason = event.get("exposure_reason")
    controllability_reason = event.get("controllability_reason")
    asil = event.get("asil")
    sg = event.get("safety_goal")
    safe_state = event.get("safe_state")
    ftti = event.get("ftti")
    override = event.get("engineering_override")

    # S 值校验
    if severity is None:
        issues.append(("error", "缺少 severity（严重度）"))
    elif severity not in (0, 1, 2, 3):
        issues.append(("error", f"severity={severity} 不在 0-3 范围内"))

    # S=0 的统一项目规则：E/C 及其理由以及 ASIL 均留空，且不得生成安全目标。
    if severity == 0:
        if exposure is not None:
            issues.append(("error", "S=0 时 E 必须为 null（不评定）"))
        if controllability is not None:
            issues.append(("error", "S=0 时 C 必须为 null（不评定）"))
        if exposure_reason is not None:
            issues.append(("error", "S=0 时 E 理由必须为 null"))
        if controllability_reason is not None:
            issues.append(("error", "S=0 时 C 理由必须为 null"))
        if asil not in (None, ""):
            issues.append(("error", "S=0 时 ASIL 必须留空"))
        for field, value, label in (
            ("safety_goal", sg, "安全目标"),
            ("safe_state", safe_state, "安全状态"),
            ("ftti", ftti, "FTTI"),
            ("safety_goal_id", event.get("safety_goal_id"), "安全目标 ID"),
        ):
            if value is not None and value != "":
                issues.append(("error", f"S=0 时不得填写{label}"))
        if override is not None:
            issues.append(("error", "S=0 时不得使用 engineering_override"))
    elif severity is not None and severity > 0:
        # S>0 时 E/C 必须有值
        if exposure is None:
            issues.append(("error", "S>0 时必须评定 E"))
        elif exposure not in (1, 2, 3, 4):
            issues.append(("error", f"exposure={exposure} 不在 1-4 范围内"))
        if controllability is None:
            issues.append(("error", "S>0 时必须评定 C"))
        elif controllability not in (0, 1, 2, 3):
            issues.append(("error", f"controllability={controllability} 不在 0-3 范围内"))

    # E 和 E_reason 一致性
    if (exposure is None) != (exposure_reason is None):
        issues.append(("error", "E 和 E 理由必须同时有值或同时为空"))

    # C 和 C_reason 一致性
    if (controllability is None) != (controllability_reason is None):
        issues.append(("error", "C 和 C 理由必须同时有值或同时为空"))

    # S 理由
    if severity is not None and severity > 0 and not severity_reason:
        issues.append(("warning", "S>0 时建议填写 S 理由"))

    # ASIL 校验
    if asil and asil not in ("QM", "A", "B", "C", "D"):
        issues.append(("error", f"ASIL={asil} 不是合法值"))

    # ASIL≥A 时安全目标完整性
    if asil in ("A", "B", "C", "D"):
        if not sg:
            issues.append(("error", f"ASIL={asil} 时必须填写安全目标"))
        if not safe_state:
            issues.append(("error", f"ASIL={asil} 时必须填写安全状态"))
        if not ftti:
            issues.append(("error", f"ASIL={asil} 时必须填写 FTTI"))

    # ASIL=QM 时安全目标应为空
    if asil == "QM" and sg:
        issues.append(("warning", "ASIL=QM 时安全目标应为空（除非有特殊备注）"))

    # engineering_override 校验
    if override is not None:
        if override != "QM":
            issues.append(("error", f"engineering_override={override}，仅支持 QM"))
        if not event.get("note"):
            issues.append(("warning", "engineering_override 应在 note 中说明理由"))

    # 场景和描述非空
    if not event.get("scenario"):
        issues.append(("error", "缺少 scenario（运行场景）"))
    if not event.get("description"):
        issues.append(("error", "缺少 description（危害事件描述）"))

    return issues


def validate_hazard_group(hazard: dict):
    """验证一个 hazard 组的完整性。"""
    issues = []

    if hazard.get("skip"):
        events = hazard.get("events")
        if not isinstance(events, list):
            issues.append(("error", "skip=true 时 events 必须是空数组"))
        elif events:
            issues.append(("error", "skip=true 的 hazard 组不得携带任何 event；QM/S=0 也必须作为非 skip 事件保留"))

        reason_code = hazard.get("skip_reason_code")
        if reason_code not in CONTROLLED_SKIP_REASON_CODES:
            issues.append((
                "error",
                "skip=true 时 skip_reason_code 必须为受控值："
                + "/".join(sorted(CONTROLLED_SKIP_REASON_CODES)),
            ))
        reason = hazard.get("skip_reason")
        if not isinstance(reason, str) or not reason.strip():
            issues.append(("error", "skip=true 时必须填写可读的 skip_reason"))

        if reason_code == "OUT_OF_DOMAIN_TRANSFER":
            target = hazard.get("transfer_to")
            if not isinstance(target, str) or not target.strip():
                issues.append(("error", "OUT_OF_DOMAIN_TRANSFER 必须填写 transfer_to"))
        if reason_code == "COVERED_BY_OTHER_ANALYSIS_UNIT":
            covered_by = hazard.get("covered_by")
            if not isinstance(covered_by, str) or not covered_by.strip():
                issues.append(("error", "COVERED_BY_OTHER_ANALYSIS_UNIT 必须填写 covered_by"))

        # 检查是否误将真实危害或 QM/S=0 危害跳过。不得使用泛化的“无”匹配，
        # 例如“无扭矩”“无法行驶”是可能需要 HARA 的真实失效后果。
        hz_desc = _hazard_text(hazard)
        _skip_kws = ("不涉及", "无整车层面危害", "无整车危害", "无危害", "N/A", "n/a")
        if reason_code == "NO_VEHICLE_LEVEL_HAZARD" and (
            not hz_desc or not any(kw in hz_desc for kw in _skip_kws)
        ):
            issues.append((
                "error",
                "NO_VEHICLE_LEVEL_HAZARD 只能用于明确标注「不涉及/无整车层面危害」的 S3 危害；"
                "QM/S=0 不得 skip。",
            ))
        return issues

    events = hazard.get("events", [])
    if not events:
        issues.append(("error", "非 skip 的 hazard 组至少需要 1 个 event"))
        return issues

    for i, event in enumerate(events):
        for level, msg in validate_event(event, hazard):
            issues.append((level, f"event[{i}]: {msg}"))

    return issues


def validate_all(hazards: list, s3_hazards: list = None):
    """验证所有 hazard 组，返回完整问题列表。

    Args:
        hazards: s4_hara.json 中的 hazards 列表
        s3_hazards: 原始 HAZOP entries（用于 hazard_key 一致性校验）
    """
    all_issues = []

    for i, hazard in enumerate(hazards):
        prefix = f"hazard[{i}] ({hazard.get('failure_id', '?')})"
        for level, msg in validate_hazard_group(hazard):
            all_issues.append((level, f"{prefix}: {msg}"))

        # 预填合规性检查（安全状态/FTTI 加锁）
        for level, msg in validate_prefill_compliance(hazard):
            all_issues.append((level, f"{prefix}: {msg}"))

    # HAZOP 一致性校验：S4 不仅要覆盖每个 failure_id，也必须逐条承接
    # 一个异常下面的多条整车危害。这里使用 failure_id + 危害描述的计数，
    # 不依赖 Excel 行号、合并单元格或展示编号。
    if s3_hazards:
        expected_hazards: Counter[tuple[str, str]] = Counter()
        for entry in s3_hazards:
            if not isinstance(entry, dict):
                continue
            for anomaly in entry.get("anomalies", []):
                if not isinstance(anomaly, dict):
                    continue
                failure_id = anomaly.get("failure_id")
                if not isinstance(failure_id, str) or not failure_id:
                    continue
                source_hazards = anomaly.get("hazards")
                if source_hazards is None:
                    source_hazards = [{"description": anomaly.get("hazard", "")}]
                for source_hazard in source_hazards if isinstance(source_hazards, list) else []:
                    if not isinstance(source_hazard, dict):
                        continue
                    text = source_hazard.get("description")
                    if isinstance(text, str) and text.strip():
                        expected_hazards[(failure_id, text.strip())] += 1

        actual_hazards: Counter[tuple[str, str]] = Counter()
        for hazard in hazards:
            if not isinstance(hazard, dict):
                continue
            failure_id = hazard.get("failure_id")
            text = _hazard_text(hazard)
            if isinstance(failure_id, str) and failure_id and text:
                actual_hazards[(failure_id, text)] += 1

        missing = expected_hazards - actual_hazards
        unexpected = actual_hazards - expected_hazards
        if missing:
            examples = [f"{failure_id}/{text[:24]}" for (failure_id, text), _ in list(missing.items())[:5]]
            all_issues.append((
                "error",
                f"S3 中有 {sum(missing.values())} 条 failure_id+危害 未被 S4 承接（含受控 skip）: {examples}",
            ))
        if unexpected:
            examples = [f"{failure_id}/{text[:24]}" for (failure_id, text), _ in list(unexpected.items())[:5]]
            all_issues.append((
                "error",
                f"S4 有 {sum(unexpected.values())} 条危害无法追溯到 S3: {examples}",
            ))

    # 精确风险矩阵为受控场景集，跨失效模式复用同一场景是其合同的一部分；
    # 不应再触发面向 Agent 自由编写内容的“照搬/分布异常”启发式警告。
    generic_hazards = [hazard for hazard in hazards if not hazard.get("risk_matrix_locked")]

    # 场景重复检测（反照搬）
    all_issues.extend(check_duplicate_scenarios(generic_hazards))

    # S/E/C 一致性检查（场景级规则验证）
    all_issues.extend(validate_sec_consistency(generic_hazards))

    # ASIL 分布质量检查
    all_issues.extend(validate_asil_distribution(generic_hazards))

    # 暴露度多样性检查
    all_issues.extend(validate_exposure_diversity(generic_hazards))

    # 可控性合理性检查
    all_issues.extend(validate_controllability_reasonableness(generic_hazards))

    # 场景质量检查
    all_issues.extend(validate_scenario_quality(generic_hazards))

    return all_issues


# ============================================================
# S/E/C 一致性检查（场景级规则验证）
# ============================================================

def validate_sec_consistency(hazards: list) -> list:
    """检查 S/E/C 值是否符合场景级规则。

    基于 s4-hara-rules.md 中的关键规则：
    - 转向助力丢失：C 应为 0（机械备份可用）
    - 转向功能在“车辆保持静止且故障不表现”场景：S 应为 0
    - 高速直线+过度/反向/卡滞：S 应为 0（无转向输入，故障不表现）
    - 同一失效模式所有事件 S/E/C 完全相同：警告可能未差异化评定

    Returns:
        list of (level, message)
    """
    issues = []

    for hazard in hazards:
        if hazard.get("skip"):
            continue
        # 已由精确 Domain Pack 锁定的事件必须以矩阵为准；旧的通用启发式
        # （如“助力丢失必为 C0”）不能覆盖经批准的场景级评定。
        if hazard.get("risk_matrix_locked") or hazard.get("source_case_locked"):
            continue

        failure_id = hazard.get("failure_id", "?")
        anomaly = hazard.get("anomaly", "") or ""
        events = hazard.get("events", [])

        if not events:
            continue

        # 规则1: 转向助力丢失 → C 应为 0（S=0 的 E/C 为 None 也算对）
        is_steering_loss = (
            hazard.get("prefill_locked") and
            any(kw in anomaly for kw in ["丢失", "丧失", "失去", "不足", "无助力"])
        )
        if is_steering_loss:
            for i, ev in enumerate(events):
                s = ev.get("severity")
                c = ev.get("controllability")
                if s is not None and s > 0 and c is not None and c > 0:
                    issues.append(("error",
                        f"{failure_id} event[{i}]: 转向助力丢失模式但 S={s}/C={c}，"
                        f"应 C=0（机械转向备份始终可用，ASIL=QM）"))

        # 规则2: 仅转向功能在“车辆始终保持静止、故障不表现”的场景要求 S=0。
        # “减速至静止”“静止后非预期移动/溜车/反向转矩”等包含“静止”的动态危害
        # 仍可能造成伤害，不能仅凭子串命中就强制降为 S0。
        profile = hazard.get("function_profile") or {}
        steering_context = " ".join(str(value or "") for value in (
            profile.get("canonical_function_family"),
            hazard.get("analysis_unit_id"),
            hazard.get("func_name"),
        )).lower()
        is_steering_context = "steering" in steering_context or "转向" in steering_context
        stationary_phrases = ("原地静止", "车辆静止", "静止状态", "停车静止", "保持静止")
        dynamic_phrases = (
            "减速至静止", "起步", "启动", "溜车", "移动", "行驶", "倒车",
            "加速", "转矩", "切入", "驶离", "滑移",
        )
        if is_steering_context:
            for i, ev in enumerate(events):
                scenario = ev.get("scenario", "") or ""
                s = ev.get("severity")
                is_stationary_without_manifestation = (
                    any(phrase in scenario for phrase in stationary_phrases)
                    and not any(phrase in scenario for phrase in dynamic_phrases)
                )
                if is_stationary_without_manifestation and s is not None and s > 0:
                    issues.append(("error",
                        f"{failure_id} event[{i}]: 转向功能在车辆保持静止且故障不表现的场景 S={s}，应 S=0"))

        # 规则3: 高速直线 + 过度/反向/卡滞 → S 应为 0
        is_over_reverse_lock = (
            hazard.get("prefill_locked") and
            any(kw in anomaly for kw in ["过多", "过度", "过大", "反向", "相反", "锁定", "卡滞", "卡死"])
        )
        if is_over_reverse_lock:
            for i, ev in enumerate(events):
                scenario = ev.get("scenario", "") or ""
                s = ev.get("severity")
                if "高速" in scenario and "直线" in scenario and s is not None and s > 0:
                    issues.append(("error",
                        f"{failure_id} event[{i}]: 高速直线+过度/反向/卡滞模式但 S={s}，"
                        f"应 S=0（驾驶员直线行驶无转向输入，故障不表现）"))

        # 规则4: 同一失效模式所有事件 S/E/C 完全相同 → 警告
        sec_values = set()
        for ev in events:
            s = ev.get("severity")
            e = ev.get("exposure")
            c = ev.get("controllability")
            sec_values.add((s, e, c))
        if len(events) >= 5 and len(sec_values) == 1:
            s, e, c = next(iter(sec_values))
            if s is not None:  # 不是全 None
                issues.append(("warning",
                    f"{failure_id}: 所有 {len(events)} 个事件的 S/E/C 完全相同"
                    f"(S={s}/E={e}/C={c})，可能未按场景差异化评定"))

    return issues


# ============================================================
# S1 下游边界检查
# ============================================================

def collect_s1_no_hara_funcs(s1_decisions: dict) -> set[str]:
    """收集 S1 明确判定为不做 HARA 的相关项。

    新版合并结果有相关项级 `is_hara`；为兼容旧结果，若该字段缺失，
    则回退到“所有子功能均为 False”的判定。
    """
    result: set[str] = set()
    for decision in s1_decisions.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        func_id = decision.get("func_id")
        if not isinstance(func_id, str) or not func_id:
            continue
        sub_functions = decision.get("sub_functions", [])
        # 同样只由子功能聚合判断 S1=否，避免父级假值掩盖“子功能=是”。
        if isinstance(sub_functions, list) and sub_functions and not _derive_s1_item_is_hara(sub_functions):
            result.add(func_id)
    return result


def validate_s1_downstream_exclusion(
    s1_decisions: dict,
    s2_decisions: dict | None = None,
    s3_entries: list[dict] | None = None,
) -> list[str]:
    """阻断 S1=否功能进入 S2 或 S3。

    这是硬边界，不是 warning；调用方不应通过 `--force` 绕过。
    """
    errors: list[str] = []
    s1_no_funcs = collect_s1_no_hara_funcs(s1_decisions)

    if s2_decisions is not None:
        for index, decision in enumerate(s2_decisions.get("decisions", [])):
            if not isinstance(decision, dict):
                continue
            func_id = decision.get("func_id")
            if func_id in s1_no_funcs:
                errors.append(
                    f"[S1=否→S2] 相关项 {func_id} 被纳入 S2 失效模式，"
                    "S1 判定为不做 HARA，禁止继续分析"
                )

    if s3_entries is not None:
        for index, entry in enumerate(s3_entries):
            if not isinstance(entry, dict):
                continue
            func_id = entry.get("func_id")
            if func_id in s1_no_funcs:
                errors.append(
                    f"[S1=否→S3] s3_hazop.entries[{index}] 的相关项 {func_id} "
                    "来自 S1=否功能，禁止进入 HAZOP 分析"
                )

    return errors


# ============================================================
# S1 → S3 覆盖检查
# ============================================================

def validate_s1_coverage(s1_decisions: dict, s3_entries: list) -> list:
    """检查 S1 中判定为 HARA="是" 的功能是否都出现在 S3 HAZOP 中。

    Args:
        s1_decisions: s1_decisions.json 解析后的 dict
        s3_entries: s3_hazop.json 的 entries 列表

    Returns:
        list of (level, message)
    """
    issues = []

    # 收集 S1 中 is_hara=true 的 func_id
    s1_hara_funcs = {}  # {func_id: func_name}
    for decision in s1_decisions.get("decisions", []):
        func_id = decision.get("func_id", "")
        sub_funcs = decision.get("sub_functions", [])
        has_hara = any(sf.get("is_hara") for sf in sub_funcs)
        if has_hara and func_id:
            s1_hara_funcs[func_id] = decision.get("func_name", func_id)

    # 收集 S3 中出现的 func_id
    s3_funcs = set()
    for entry in s3_entries:
        fid = entry.get("func_id", "")
        if fid:
            s3_funcs.add(fid)

    # 检查缺失
    for func_id, func_name in s1_hara_funcs.items():
        if func_id not in s3_funcs:
            issues.append(("error",
                f"S1 判定需要 HARA 的功能 {func_id}（{func_name}）未出现在 S3 HAZOP 中，"
                f"请检查 s3_hazop.json 是否遗漏该功能"))

    return issues


# ============================================================
# S2 Agent 输出范围与完整性检查
# ============================================================

S2_FAILURE_MODES = (
    "丢失", "非预期", "间歇", "过多",
    "过少", "过早", "反向", "振荡", "部分", "过晚", "卡滞",
)


def _derive_s1_item_is_hara(sub_functions: list) -> bool:
    """S1 相关项级结论只能由子功能结论派生，不能作为独立事实来源。"""
    return any(
        isinstance(sub, dict) and sub.get("is_hara") is True
        for sub in sub_functions
    )


def validate_final_s1_against_intermediate(
    s1_decisions: dict,
    intermediate: dict | None = None,
) -> list[str]:
    """验证下游可用的最终 S1 合同。

    Agent 原始 S1 只能用于 merge；S2/S3/S4/write 使用的最终 S1 必须满足：
      1. item.is_hara == any(sub_functions[].is_hara)；
      2. 若传入 intermediate，决策集合/Feature 集合精确对齐；
      3. intermediate 已锁定的 s1_rule_is_hara 不得被改写。

    这使 Domain Pack/速查表的锁定结论成为不可绕过的阶段边界。
    """
    errors: list[str] = []
    if not isinstance(s1_decisions, dict):
        return ["S1 顶层必须是 JSON 对象"]
    decisions = s1_decisions.get("decisions")
    if not isinstance(decisions, list):
        return ["S1 缺少有效的 decisions 列表"]

    expected_items: dict[str, dict] = {}
    if intermediate is not None:
        if not isinstance(intermediate, dict) or not isinstance(intermediate.get("related_items"), list):
            errors.append("intermediate 缺少有效的 related_items 列表")
        else:
            for index, item in enumerate(intermediate["related_items"]):
                if not isinstance(item, dict):
                    errors.append(f"intermediate.related_items[{index}] 必须是对象")
                    continue
                func_id = item.get("func_id")
                if not isinstance(func_id, str) or not func_id.strip():
                    errors.append(f"intermediate.related_items[{index}] 缺少有效 func_id")
                    continue
                if func_id in expected_items:
                    errors.append(f"intermediate 重复的 func_id '{func_id}'")
                    continue
                subs = item.get("sub_functions")
                if not isinstance(subs, list):
                    errors.append(f"intermediate {func_id} 的 sub_functions 必须是列表")
                    subs = []
                expected_items[func_id] = {
                    "func_name": item.get("func_name", ""),
                    "features": {
                        sub.get("feature_list_id"): sub
                        for sub in subs
                        if isinstance(sub, dict)
                        and isinstance(sub.get("feature_list_id"), str)
                        and sub.get("feature_list_id")
                    },
                }

    actual_funcs: set[str] = set()
    for index, decision in enumerate(decisions):
        prefix = f"S1 decisions[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        func_id = decision.get("func_id")
        if not isinstance(func_id, str) or not func_id.strip():
            errors.append(f"{prefix} 缺少有效 func_id")
            continue
        if func_id in actual_funcs:
            errors.append(f"S1 重复提交 func_id '{func_id}'")
            continue
        actual_funcs.add(func_id)
        if expected_items and func_id not in expected_items:
            errors.append(f"S1 相关项 {func_id} 不存在于 intermediate")

        sub_functions = decision.get("sub_functions")
        if not isinstance(sub_functions, list):
            errors.append(f"S1 {func_id} 的 sub_functions 必须是列表")
            continue
        actual_subs: dict[str, dict] = {}
        for sub_index, sub in enumerate(sub_functions):
            sub_prefix = f"S1 {func_id}.sub_functions[{sub_index}]"
            if not isinstance(sub, dict):
                errors.append(f"{sub_prefix} 必须是对象")
                continue
            feature_id = sub.get("feature_list_id")
            if not isinstance(feature_id, str) or not feature_id.strip():
                errors.append(f"{sub_prefix} 缺少有效 feature_list_id")
                continue
            if feature_id in actual_subs:
                errors.append(f"S1 {func_id} 重复提交 feature_list_id '{feature_id}'")
                continue
            if not isinstance(sub.get("is_hara"), bool):
                errors.append(f"{sub_prefix}.is_hara 必须是布尔值")

            disposition = sub.get("disposition")
            if disposition is not None:
                if disposition not in {"analyze", "covered_by", "transfer", "exclude"}:
                    errors.append(f"{sub_prefix}.disposition 不合法: {disposition!r}")
                elif disposition in {"analyze", "covered_by"} and sub.get("is_hara") is not True:
                    errors.append(f"{sub_prefix}.disposition={disposition} 时 is_hara 必须为 true")
                elif disposition in {"transfer", "exclude"} and sub.get("is_hara") is not False:
                    errors.append(f"{sub_prefix}.disposition={disposition} 时 is_hara 必须为 false")

                if disposition == "covered_by":
                    if not isinstance(sub.get("analysis_unit_id"), str) or not sub.get("analysis_unit_id").strip():
                        errors.append(f"{sub_prefix}.disposition=covered_by 时必须有 analysis_unit_id")
                if disposition == "transfer":
                    if not isinstance(sub.get("target_domain"), str) or not sub.get("target_domain").strip():
                        errors.append(f"{sub_prefix}.disposition=transfer 时必须有 target_domain")
                elif sub.get("target_domain") is not None:
                    errors.append(f"{sub_prefix}.disposition={disposition} 时不得有 target_domain")
                if not isinstance(sub.get("reason_code"), str) or not sub.get("reason_code").strip():
                    errors.append(f"{sub_prefix}.disposition={disposition} 时必须有 reason_code")
            actual_subs[feature_id] = sub

        derived_is_hara = _derive_s1_item_is_hara(sub_functions)
        parent_is_hara = decision.get("is_hara")
        if not isinstance(parent_is_hara, bool):
            errors.append(f"S1 {func_id}.is_hara 必须是布尔值，并由子功能结果派生")
        elif parent_is_hara != derived_is_hara:
            errors.append(
                f"S1 {func_id} 父级 is_hara={parent_is_hara} 与子功能聚合结果 "
                f"{derived_is_hara} 不一致"
            )

        expected = expected_items.get(func_id)
        if expected is not None:
            expected_ids = set(expected["features"])
            actual_ids = set(actual_subs)
            missing = sorted(expected_ids - actual_ids)
            extra = sorted(actual_ids - expected_ids)
            if missing:
                errors.append(f"S1 {func_id} 缺少 intermediate 子功能: {', '.join(missing)}")
            if extra:
                errors.append(f"S1 {func_id} 包含 intermediate 外子功能: {', '.join(extra)}")
            for feature_id in sorted(expected_ids & actual_ids):
                locked_value = expected["features"][feature_id].get("s1_rule_is_hara")
                actual_value = actual_subs[feature_id].get("is_hara")
                if isinstance(locked_value, bool) and actual_value != locked_value:
                    errors.append(
                        f"[S1锁定] {func_id}/{feature_id}: intermediate/Domain Pack 锁定为 "
                        f"{locked_value}，实际为 {actual_value}"
                    )
                locked_metadata = {
                    "disposition": expected["features"][feature_id].get("s1_disposition"),
                    "analysis_unit_id": expected["features"][feature_id].get("analysis_unit_id"),
                    "target_domain": expected["features"][feature_id].get("target_domain"),
                    "reason_code": expected["features"][feature_id].get("s1_reason_code"),
                }
                for field, locked_metadata_value in locked_metadata.items():
                    if locked_metadata_value is not None and actual_subs[feature_id].get(field) != locked_metadata_value:
                        errors.append(
                            f"[S1锁定] {func_id}/{feature_id}: {field} 锁定为 "
                            f"{locked_metadata_value!r}，实际为 {actual_subs[feature_id].get(field)!r}"
                        )

    if expected_items:
        missing_funcs = sorted(set(expected_items) - actual_funcs)
        if missing_funcs:
            errors.append(f"S1 缺少 intermediate 相关项: {', '.join(missing_funcs)}")
    return errors


def _s1_hara_func_map(s1_decisions: dict) -> tuple[dict[str, dict], list[str]]:
    """构建 S1 相关项索引，并兼容旧版缺少 item-level is_hara 的结果。"""
    result: dict[str, dict] = {}
    errors: list[str] = []
    decisions = s1_decisions.get("decisions")
    if not isinstance(decisions, list):
        return {}, ["S1 缺少有效的 decisions 列表"]

    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"S1 decisions[{index}] 必须是对象")
            continue
        func_id = decision.get("func_id")
        if not isinstance(func_id, str) or not func_id.strip():
            errors.append(f"S1 decisions[{index}] 缺少有效 func_id")
            continue
        if func_id in result:
            errors.append(f"S1 重复提交 func_id '{func_id}'")
            continue
        sub_functions = decision.get("sub_functions", [])
        if not isinstance(sub_functions, list):
            errors.append(f"S1 {func_id} 的 sub_functions 必须是列表")
            sub_functions = []
        # 下游范围始终由子功能事实派生，不能相信可被伪造的父级字段。
        derived_is_hara = _derive_s1_item_is_hara(sub_functions)
        parent_is_hara = decision.get("is_hara")
        if isinstance(parent_is_hara, bool) and parent_is_hara != derived_is_hara:
            errors.append(
                f"S1 {func_id} 父级 is_hara={parent_is_hara} 与子功能聚合结果 "
                f"{derived_is_hara} 不一致"
            )
        is_hara = derived_is_hara
        result[func_id] = {
            "func_name": decision.get("func_name", ""),
            "is_hara": is_hara,
        }
    return result, errors


def validate_s2_decisions_against_s1(
    s1_decisions: dict,
    s2_decisions: dict,
    intermediate: dict | None = None,
) -> list[str]:
    """严格校验 S2 Agent 输出的功能范围、完整性和失效模式值。

    S2 的可编辑集合必须精确等于 S1 合并结果中 `is_hara=true` 的相关项。
    因此 S2 不能漏功能、增加功能、重复功能，也不能为 S1=否功能输出模式。
    """
    errors: list[str] = []
    if not isinstance(s1_decisions, dict):
        return ["S1 顶层必须是 JSON 对象"]
    if not isinstance(s2_decisions, dict):
        return ["S2 顶层必须是 JSON 对象"]

    s1_map, s1_errors = _s1_hara_func_map(s1_decisions)
    errors.extend(s1_errors)
    expected_hara = {fid for fid, item in s1_map.items() if item["is_hara"] is True}
    s1_no_hara = set(s1_map) - expected_hara

    intermediate_map: dict[str, str] = {}
    if intermediate is not None:
        related_items = intermediate.get("related_items")
        if not isinstance(related_items, list):
            errors.append("intermediate 缺少有效的 related_items 列表")
        else:
            for index, item in enumerate(related_items):
                if not isinstance(item, dict):
                    errors.append(f"intermediate.related_items[{index}] 必须是对象")
                    continue
                func_id = item.get("func_id")
                if not isinstance(func_id, str) or not func_id.strip():
                    errors.append(f"intermediate.related_items[{index}] 缺少有效 func_id")
                    continue
                if func_id in intermediate_map:
                    errors.append(f"intermediate 重复的 func_id '{func_id}'")
                    continue
                intermediate_map[func_id] = item.get("func_name", "")

            for func_id in s1_map:
                if func_id not in intermediate_map:
                    errors.append(
                        f"S1 相关项 {func_id} 不存在于 intermediate，不能作为 S2 输入范围"
                    )

    decisions = s2_decisions.get("decisions")
    if not isinstance(decisions, list):
        errors.append("S2 缺少有效的 decisions 列表")
        return errors

    actual_ids: list[str] = []
    actual_set: set[str] = set()
    for index, decision in enumerate(decisions):
        prefix = f"S2 decisions[{index}]"
        if not isinstance(decision, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        func_id = decision.get("func_id")
        if not isinstance(func_id, str) or not func_id.strip():
            errors.append(f"{prefix} 缺少有效 func_id")
            continue
        if func_id in actual_set:
            errors.append(f"S2 重复提交 func_id '{func_id}'")
        actual_ids.append(func_id)
        actual_set.add(func_id)

        if func_id not in s1_map:
            errors.append(f"S2 提交了 S1 中不存在的 func_id '{func_id}'")
        elif func_id in s1_no_hara:
            errors.append(
                f"S2 {func_id}: S1 判定为不做 HARA，禁止提交 selected_modes"
            )

        expected_name = intermediate_map.get(func_id, s1_map.get(func_id, {}).get("func_name", ""))
        if "func_name" not in decision:
            errors.append(f"{prefix} 缺少 func_name")
        elif not isinstance(decision["func_name"], str) or not decision["func_name"].strip():
            errors.append(f"{prefix}.func_name 必须是非空字符串")
        elif expected_name and decision["func_name"] != expected_name:
            errors.append(
                f"S2 {func_id}: func_name 与输入不一致，期望 '{expected_name}'，"
                f"实际 '{decision['func_name']}'"
            )

        modes = decision.get("selected_modes")
        if not isinstance(modes, list):
            errors.append(f"{prefix}.selected_modes 必须是列表")
            modes = []
        if func_id in expected_hara and not modes:
            errors.append(f"S2 {func_id}: selected_modes 不能为空，需至少选择一种失效模式")
        seen_modes: set[str] = set()
        for mode_index, mode in enumerate(modes):
            mode_prefix = f"{prefix}.selected_modes[{mode_index}]"
            if not isinstance(mode, str) or not mode.strip():
                errors.append(f"{mode_prefix} 必须是非空字符串")
                continue
            if mode in seen_modes:
                errors.append(f"S2 {func_id}: 重复选择失效模式 '{mode}'")
            seen_modes.add(mode)
            if mode not in S2_FAILURE_MODES:
                errors.append(
                    f"S2 {func_id}: 未知失效模式 '{mode}'，允许值为："
                    f"{'/'.join(S2_FAILURE_MODES)}"
                )

        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"S2 {func_id}: reason 必须是非空字符串")

    missing = sorted(expected_hara - actual_set)
    extra = sorted(actual_set - expected_hara)
    if missing:
        errors.append(f"S2 缺少 S1=是相关项的完整决策: {', '.join(missing)}")
    if extra:
        errors.append(f"S2 包含非 S1=是相关项: {', '.join(extra)}")

    return errors


# ============================================================
# S3 Agent 输出范围、结构与覆盖检查
# ============================================================

def validate_s3_entries_against_s1_s2(
    s1_decisions: dict,
    s2_decisions: dict,
    s3_data: dict,
    intermediate: dict | None = None,
) -> list[str]:
    """严格校验 S3 HAZOP entries 是否完整且只覆盖 S1/S2 允许的范围。

    约束：
      * S3 entries 的功能集合必须来自 S2，间接保证 S1=是；
      * 每个 S2 selected_mode 必须恰好对应一个 entry；
      * 不允许未知功能、未知失效模式、重复功能+模式组合；
      * entry/anomaly/hazard 的最小结构必须完整，避免空壳结果进入 S4。
    """
    errors: list[str] = []
    if not isinstance(s3_data, dict):
        return ["S3 顶层必须是 JSON 对象"]
    entries = s3_data.get("entries")
    if not isinstance(entries, list):
        return ["S3 缺少有效的 entries 列表"]

    # S2 自身先校验，防止从非法 S2 推导出错误的 S3 允许集合。
    s2_errors = validate_s2_decisions_against_s1(s1_decisions, s2_decisions, intermediate)
    errors.extend(f"[S2前置] {error}" for error in s2_errors)
    if s2_errors:
        return errors

    expected: dict[str, dict[str, str]] = {}
    for decision in s2_decisions.get("decisions", []):
        func_id = decision["func_id"]
        func_name = decision["func_name"]
        for mode in decision["selected_modes"]:
            expected[f"{func_id}\x1f{mode}"] = {
                "func_id": func_id,
                "func_name": func_name,
                "failure_mode": mode,
            }

    actual: dict[str, int] = {}
    for index, entry in enumerate(entries):
        prefix = f"S3 entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        func_id = entry.get("func_id")
        mode = entry.get("failure_mode")
        func_name = entry.get("func_name")
        if not isinstance(func_id, str) or not func_id.strip():
            errors.append(f"{prefix}.func_id 必须是非空字符串")
            continue
        if not isinstance(mode, str) or not mode.strip():
            errors.append(f"{prefix}.failure_mode 必须是非空字符串")
            continue
        key = f"{func_id}\x1f{mode}"
        actual[key] = actual.get(key, 0) + 1
        if key not in expected:
            if func_id not in {item["func_id"] for item in expected.values()}:
                errors.append(f"{prefix}: 功能 {func_id} 不在 S2 允许范围内")
            elif mode not in S2_FAILURE_MODES:
                errors.append(
                    f"{prefix}: 未知失效模式 '{mode}'，允许值为："
                    f"{'/'.join(S2_FAILURE_MODES)}"
                )
            else:
                errors.append(
                    f"{prefix}: 功能 {func_id} 未在 S2 选择失效模式 '{mode}'，禁止扩展分析范围"
                )
        elif actual[key] > 1:
            errors.append(f"{prefix}: 重复的功能+失效模式 entry ({func_id}, {mode})")

        if key in expected and func_name != expected[key]["func_name"]:
            errors.append(
                f"{prefix}.func_name 与 S2 不一致，期望 '{expected[key]['func_name']}'，"
                f"实际 '{func_name}'"
            )

        anomalies = entry.get("anomalies")
        if not isinstance(anomalies, list) or not anomalies:
            errors.append(f"{prefix}.anomalies 必须是非空列表")
            continue
        for anomaly_index, anomaly in enumerate(anomalies):
            ap = f"{prefix}.anomalies[{anomaly_index}]"
            if not isinstance(anomaly, dict):
                errors.append(f"{ap} 必须是对象")
                continue
            description = anomaly.get("description")
            if not isinstance(description, str) or not description.strip():
                errors.append(f"{ap}.description 必须是非空字符串")
            failure_id = anomaly.get("failure_id")
            if not isinstance(failure_id, str) or not failure_id.strip():
                errors.append(f"{ap}.failure_id 必须是非空字符串")
            hazards = anomaly.get("hazards")
            if not isinstance(hazards, list) or not hazards:
                errors.append(f"{ap}.hazards 必须是非空列表")
                continue
            for hazard_index, hazard in enumerate(hazards):
                hp = f"{ap}.hazards[{hazard_index}]"
                if not isinstance(hazard, dict):
                    errors.append(f"{hp} 必须是对象")
                    continue
                hazard_desc = hazard.get("description")
                if not isinstance(hazard_desc, str) or not hazard_desc.strip():
                    errors.append(f"{hp}.description 必须是非空字符串")
                if "associated_hara" not in hazard:
                    errors.append(f"{hp} 缺少 associated_hara 字段")
                elif not isinstance(hazard["associated_hara"], str):
                    errors.append(f"{hp}.associated_hara 必须是字符串")
                elif isinstance(hazard_desc, str):
                    normalized_hazard = hazard_desc.strip().lower()
                    no_vehicle_level_hazard = any(token in normalized_hazard for token in (
                        "无整车层面危害", "无整车危害", "不涉及", "无危害", "n/a",
                    ))
                    if no_vehicle_level_hazard:
                        associated = hazard["associated_hara"].strip()
                        # “否”是旧版 S3 合同中的无关联写法；允许读取，Excel 写入时统一规范为“不涉及”。
                        if associated not in {"", "否", "不涉及", "N/A", "n/a"}:
                            errors.append(
                                f"{hp}: 整车危害为“无整车层面危害/不涉及”时，"
                                "associated_hara 只能留空、填写“否”或“不涉及”，不能关联 HARA"
                            )

    expected_missing = sorted(key for key in expected if actual.get(key, 0) == 0)
    for key in expected_missing:
        item = expected[key]
        errors.append(
            f"S3 缺少 S2 选中的功能+失效模式: {item['func_id']} / {item['failure_mode']}"
        )
    return errors


# ============================================================
# S2 → S3 失效模式覆盖检查
# ============================================================

def validate_s2_s3_mode_coverage(
    s2_decisions: dict, s3_entries: list, include_generic: bool = True
) -> list:
    """检查 S2 中选中的失效模式是否全部出现在 S3 HAZOP entries 中。

    Args:
        s2_decisions: s2_decisions.json 解析后的 dict
        s3_entries: s3_hazop.json 的 entries 列表

    Returns:
        list of (level, message) — 缺失模式报 warning（不阻断）
    """
    issues = []

    # 收集 S2 中每个功能的 selected_modes
    s2_modes = {}  # {func_id: set(modes)}
    for decision in s2_decisions.get("decisions", []):
        func_id = decision.get("func_id", "")
        modes = decision.get("selected_modes", [])
        if func_id and modes:
            s2_modes[func_id] = set(modes)

    # 收集 S3 中每个功能的 failure_mode
    s3_modes = {}  # {func_id: set(modes)}
    for entry in s3_entries:
        func_id = entry.get("func_id", "")
        mode = entry.get("failure_mode", "")
        if func_id and mode:
            if func_id not in s3_modes:
                s3_modes[func_id] = set()
            s3_modes[func_id].add(mode)

    # 检查缺失。P0-4 严格校验时由 validate_s3_entries_against_s1_s2 负责，
    # 这里保留默认行为兼容旧调用方。
    if include_generic:
        for func_id, expected_modes in s2_modes.items():
            actual_modes = s3_modes.get(func_id, set())
            missing = expected_modes - actual_modes
            if missing:
                issues.append(("warning",
                    f"S2 选中的失效模式 {sorted(missing)} 未出现在 S3 HAZOP 中（功能 {func_id}），"
                    f"请检查 s3_hazop.json 是否遗漏"))

    # ---- 域特定失效模式覆盖检查 ----
    # 提取域前缀（从第一个 func_id）
    domain_prefix = None
    for e in s3_entries:
        fid = e.get("func_id", "")
        if fid and "_func_" in fid:
            domain_prefix = fid.split("_func_")[0]
            break

    if domain_prefix == "Info":
        # ET域：信息显示类功能应覆盖"过大/过小"（数值显示偏差）
        display_keywords = ["仪表", "显示", "HUD", "导航", "车速", "电量"]
        for e in s3_entries:
            func_name = e.get("func_name", "")
            if any(k in func_name for k in display_keywords):
                modes = {a.get("mode", "") for a in e.get("anomalies", [])}
                # 检查是否有过大或过小类失效
                has_quantity = any("过大" in a.get("description", "") or "过小" in a.get("description", "")
                                   for a in e.get("anomalies", []))
                if not has_quantity and len(e.get("anomalies", [])) >= 3:
                    issues.append(("warning",
                        f"[ET域] 功能 {e.get('func_id')} {func_name} 未发现'过大/过小'类失效，"
                        f"显示类功能建议补充数值偏差类失效（如显示车速过大/过小）"))
    elif domain_prefix == "B":
        # BD域：灯光类功能应覆盖"过早/过晚"（点亮时机偏差）
        light_keywords = ["灯", "灯光", "照明"]
        for e in s3_entries:
            func_name = e.get("func_name", "")
            if any(k in func_name for k in light_keywords):
                has_timing = any("过早" in a.get("description", "") or "过晚" in a.get("description", "")
                                 for a in e.get("anomalies", []))
                if not has_timing and len(e.get("anomalies", [])) >= 4:
                    issues.append(("warning",
                        f"[BD域] 灯光功能 {e.get('func_id')} {func_name} 未发现'过早/过晚'类失效，"
                        f"建议补充点亮/熄灭时机偏差类失效（如位置灯点亮过晚）"))

    return issues


# ============================================================
# failure_id 格式校验
# ============================================================

# failure_id 格式：{域}_MF_{4位数字}_{2位数字}，如 CB_MF_0002_03、Info_MF_0002_01
# 域前缀：首字母大写，可跟小写字母（如 Info），长度1-8
_FAILURE_ID_PATTERN = re.compile(r"^[A-Z][A-Za-z]{0,7}_MF_\d{4}_\d{2}$")


def validate_failure_id_format(s3_entries: list, domain_prefix: str = None) -> list:
    """校验 s3_hazop.json 中所有 failure_id 的格式是否符合规范。

    格式要求：{域}_MF_{功能4位序号}_{2位流水号}，如 CB_MF_0002_03
    同时校验域前缀一致性（如果提供了 domain_prefix）。

    Args:
        s3_entries: s3_hazop.json 的 entries 列表
        domain_prefix: 期望的域前缀，如 "B"、"Info"、"CB"。
                      提供时会校验 failure_id 的前缀是否匹配。

    Returns:
        list of (level, message) — 格式不合规报 error（阻断写入）
    """
    issues = []
    for entry in s3_entries:
        func_id = entry.get("func_id", "")
        func_name = entry.get("func_name", "")
        for anom in entry.get("anomalies", []):
            fid = anom.get("failure_id", "")
            if not fid:
                issues.append(("error",
                    f"failure_id 为空（功能 {func_id} {func_name}），"
                    f"应为 {{域}}_MF_{{4位序号}}_{{2位流水号}} 格式，如 CB_MF_0002_03"))
                continue
            if not _FAILURE_ID_PATTERN.match(fid):
                issues.append(("error",
                    f"failure_id 格式不规范: '{fid}'（功能 {func_id} {func_name}），"
                    f"应为 {{域}}_MF_{{4位序号}}_{{2位流水号}} 格式，如 CB_MF_0002_03。"
                    f"禁止使用汉字后缀（如 _丢失）或缺少流水号（如 B_MF_0001）"))
                continue
            # 域前缀一致性校验
            if domain_prefix:
                prefix = fid.split("_MF_")[0]
                if prefix != domain_prefix:
                    issues.append(("error",
                        f"failure_id 域前缀不匹配: '{fid}' 的前缀是 '{prefix}'，"
                        f"应为 '{domain_prefix}'（功能 {func_id} {func_name}）。"
                        f"请确保与功能ID域前缀一致。"))
    return issues


# ============================================================
# 异常/危害描述字数校验
# ============================================================

def validate_description_length(s3_entries: list) -> list:
    """校验 s3_hazop.json 中异常描述和危害描述的字数。

    异常描述：5-15 字（超过 15 字报 warning）
    危害描述：≤ 20 字（特殊可放宽至 25，超过 25 报 warning）

    Returns:
        list of (level, message) — 超长报 warning（不阻断）
    """
    issues = []
    for entry in s3_entries:
        func_id = entry.get("func_id", "")
        failure_mode = entry.get("failure_mode", "")
        for anom in entry.get("anomalies", []):
            desc = anom.get("description", "")
            if desc:
                desc_len = len(desc)
                if desc_len > 15:
                    issues.append(("warning",
                        f"异常描述过长（{desc_len}字，建议≤15）: '{desc}'"
                        f"（功能 {func_id} {failure_mode}）"))
                elif desc_len < 5:
                    issues.append(("warning",
                        f"异常描述过短（{desc_len}字，建议≥5）: '{desc}'"
                        f"（功能 {func_id} {failure_mode}）"))

            # 支持 hazards 列表和旧格式
            hazards = anom.get("hazards")
            if hazards is None:
                h = anom.get("hazard", "")
                if h:
                    hazards = [{"description": h}]
            if hazards:
                for hz in hazards:
                    hz_desc = hz.get("description", "") if isinstance(hz, dict) else str(hz)
                    if hz_desc:
                        hz_len = len(hz_desc)
                        if hz_len > 25:
                            issues.append(("warning",
                                f"危害描述过长（{hz_len}字，建议≤20，特殊≤25）: '{hz_desc}'"
                                f"（功能 {func_id} {failure_mode}）"))
    return issues


# ============================================================
# 场景重复检测（反照搬）
# ============================================================

def check_duplicate_scenarios(hazards: list) -> list:
    """检测不同 failure_id 之间场景集完全相同的情况（可能照搬）。

    按 failure_id 收集所有非 skip/reference 事件的场景文本集合，
    如果两个 failure_id 的场景集完全一致且都非空，报 WARNING。

    Returns:
        list of (level, message)
    """
    issues = []

    # {failure_id: set(scenario_texts)}
    fm_scenarios = {}
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        failure_id = hazard.get("failure_id", "")
        if not failure_id:
            continue
        for event in hazard.get("events", []):
            scenario = event.get("scenario", "").strip()
            if scenario:
                fm_scenarios.setdefault(failure_id, set()).add(scenario)

    # 两两比较
    checked = set()
    for fid1, scenes1 in fm_scenarios.items():
        for fid2, scenes2 in fm_scenarios.items():
            if fid1 >= fid2 or len(scenes1) < 2:
                continue
            pair = (fid1, fid2)
            if pair in checked:
                continue
            checked.add(pair)

            if scenes1 == scenes2:
                issues.append(("warning",
                    f"失效模式 {fid1} 和 {fid2} 的场景集完全相同（{len(scenes1)} 个场景），"
                    f"可能存在照搬，请根据各自危害方向调整场景"))

    return issues


# ============================================================
# ASIL 分布质量检查（防 ASIL 膨胀）
# ============================================================

ASIL_RANK = {"QM": 0, "A": 1, "B": 2, "C": 3, "D": 4, None: -1}


def _highest_asil(hazard: dict) -> str | None:
    """获取 hazard 的最高 ASIL（取所有事件中最高的）。"""
    events = hazard.get("events", [])
    best = None
    best_rank = -1
    for ev in events:
        asil = ev.get("asil")
        rank = ASIL_RANK.get(asil, -1)
        if rank > best_rank:
            best = asil
            best_rank = rank
    return best


def _func_group(failure_id: str) -> str:
    """从 failure_id 提取功能组前缀，如 B_MF_0001_01 → B_MF_0001。"""
    if not failure_id:
        return ""
    parts = failure_id.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return failure_id


def validate_asil_distribution(hazards: list) -> list:
    """检查 ASIL 分布合理性，防止 ASIL 膨胀。

    逻辑：
    1. 统计所有非 skip hazard 的 ASIL 分布（QM/A/B/C/D/None）
    2. 如果 ASIL D 的比例 > 30% 且总 hazard 数 >= 5，报 warning
    3. 如果所有 hazard 都是同一个 ASIL 等级且数量 >= 5，报 warning
    4. 按功能分组，如果一个功能组内所有 hazard 的 ASIL 都相同且 >= 3 个，报 warning

    Returns:
        list of (level, message) — 全部是 warning 级别
    """
    issues = []

    # 收集所有非 skip hazard 的最高 ASIL
    hazard_asils = []
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        asil = _highest_asil(hazard)
        hazard_asils.append((hazard.get("failure_id", "?"), asil))

    total = len(hazard_asils)
    if total == 0:
        return issues

    # 统计分布
    from collections import Counter
    asil_counts = Counter(a for _, a in hazard_asils)

    # 规则2: ASIL D 比例 > 30%
    d_count = asil_counts.get("D", 0)
    if total >= 5 and d_count / total > 0.30:
        issues.append(("warning",
            f"ASIL 分布：ASIL D 占 {d_count}/{total} = {d_count/total*100:.1f}%，"
            f"超过 30% 阈值，存在 ASIL 膨胀风险，请复核高风险评定"))

    # 规则3: 所有 hazard 都是同一个 ASIL 等级
    if total >= 5 and len(asil_counts) == 1:
        only_asil = next(iter(asil_counts))
        issues.append(("warning",
            f"ASIL 分布：全部 {total} 个 hazard 的 ASIL 均为 {only_asil}，"
            f"未进行差异化评定，请确认是否合理"))

    # 规则4: 按功能分组，组内 ASIL 全部相同且 >= 3 个
    group_asils = {}
    for fid, asil in hazard_asils:
        grp = _func_group(fid)
        group_asils.setdefault(grp, []).append((fid, asil))

    for grp, items in group_asils.items():
        if len(items) < 3:
            continue
        asil_set = set(a for _, a in items)
        if len(asil_set) == 1:
            only_asil = next(iter(asil_set))
            issues.append(("warning",
                f"功能组 {grp} 内 {len(items)} 个 hazard 的 ASIL 均为 {only_asil}，"
                f"可能未按失效模式差异化评定"))

    return issues


# ============================================================
# 暴露度多样性检查
# ============================================================

def validate_exposure_diversity(hazards: list) -> list:
    """检查暴露度(E)的多样性，防止所有场景 E 值相同。

    逻辑：
    1. 按 failure_id 分组
    2. 对每个 hazard，如果事件数 >= 3 且所有事件的 exposure 值完全相同（且不为 None），报 warning
    3. 特别检查：如果同一功能组内所有 hazard 的 E 值分布都一样，报 warning

    Returns:
        list of (level, message) — warning 级别
    """
    issues = []

    # 规则1 & 2: 单个 hazard 内所有事件 E 值相同
    group_e_patterns = {}  # {func_group: set of frozenset of E-values patterns}

    for hazard in hazards:
        if hazard.get("skip"):
            continue
        failure_id = hazard.get("failure_id", "?")
        events = hazard.get("events", [])
        if not events:
            continue

        e_values = [ev.get("exposure") for ev in events]
        e_set = set(e_values)

        # 规则2: 事件数 >= 3 且所有 E 完全相同且不为 None
        if len(events) >= 3 and len(e_set) == 1:
            e_val = next(iter(e_set))
            if e_val is not None:
                issues.append(("warning",
                    f"{failure_id}: 所有 {len(events)} 个事件的 exposure 均为 E{e_val}，"
                    f"暴露度缺乏多样性，请确认是否所有场景暴露概率相同"))

        # 收集功能组的 E 分布模式（全部转字符串以便排序比较）
        grp = _func_group(failure_id)
        e_for_sort = [str(e) for e in e_values]
        e_sorted = tuple(sorted(e_for_sort))
        group_e_patterns.setdefault(grp, set()).add(e_sorted)

    # 规则3: 同一功能组内所有 hazard 的 E 值分布都一样
    for grp, patterns in group_e_patterns.items():
        if len(patterns) == 1 and len(next(iter(patterns))) >= 3:
            # 检查该组有多少个 hazard
            hazard_count = len([
                h for h in hazards
                if not h.get("skip") and _func_group(h.get("failure_id", "")) == grp
            ])
            if hazard_count >= 2:
                issues.append(("warning",
                    f"功能组 {grp} 内 {hazard_count} 个 hazard 的暴露度分布完全一致，"
                    f"可能未按失效方向差异化场景暴露度"))

    return issues


# ============================================================
# 可控性合理性检查
# ============================================================

def _domain_from_failure_id(failure_id: str) -> str:
    """从 failure_id 提取域前缀，如 B_MF_0001_01 → B。"""
    if "_MF_" in failure_id:
        return failure_id.split("_MF_")[0]
    return ""


def validate_controllability_reasonableness(hazards: list) -> list:
    """检查可控性(C)的合理性。

    逻辑：
    1. 提取域前缀（从 failure_id 的 _MF_ 前缀之前部分，如 B_、Info_、CB_）
    2. BD域（B_前缀）：灯光/显示类功能的可控性不应低于 C2。
       如果 C=3 且功能名含"灯"/"显示"等关键词，报 warning
    3. ET域（Info_前缀）：显示类功能的可控性不应低于 C2，如果 C=3 报 warning
    4. 通用规则：如果 hazard 的 anomaly 含"丢失"且 C=3，报 warning
       （功能完全丢失通常驾驶员容易察觉，可控性较高）
    5. 通用规则：如果 hazard 的 anomaly 含"非预期"且 C=0，报 warning
       （非预期激活通常需要驾驶员察觉并采取行动，可控性不应为0）

    Returns:
        list of (level, message) — warning 级别
    """
    issues = []

    bd_light_keywords = ["灯", "显示"]
    et_display_keywords = ["显示", "仪表", "HUD", "中控", "娱乐", "导航"]

    for hazard in hazards:
        if hazard.get("skip"):
            continue

        failure_id = hazard.get("failure_id", "?")
        anomaly = hazard.get("anomaly", "") or ""
        func_name = hazard.get("func_name", "") or ""
        domain = _domain_from_failure_id(failure_id)
        events = hazard.get("events", [])

        if not events:
            continue

        for i, ev in enumerate(events):
            c = ev.get("controllability")
            if c is None:
                continue

            # 规则2: BD域灯光/显示类功能 C=3 报 warning
            if domain == "B":
                is_light_or_display = any(kw in func_name or kw in anomaly for kw in bd_light_keywords)
                if is_light_or_display and c == 3:
                    issues.append(("warning",
                        f"{failure_id} event[{i}]: BD域灯光/显示类功能但 C=3（难以控制），"
                        f"驾驶员通常可察觉并采取措施，建议 C≤2"))

            # 规则3: ET域显示类功能 C=3 报 warning
            if domain == "Info":
                is_display = any(kw in func_name or kw in anomaly for kw in et_display_keywords)
                if is_display and c == 3:
                    issues.append(("warning",
                        f"{failure_id} event[{i}]: ET域显示类功能但 C=3（难以控制），"
                        f"信息显示异常通常驾驶员可察觉，建议 C≤2"))

            # 规则4: anomaly 含"丢失"且 C=3
            if "丢失" in anomaly and c == 3:
                issues.append(("warning",
                    f"{failure_id} event[{i}]: 异常为'丢失'类但 C=3，"
                    f"功能完全丢失通常驾驶员容易察觉，可控性应较高，请复核"))

            # 规则5: anomaly 含"非预期"且 C=0
            if "非预期" in anomaly and c == 0:
                issues.append(("warning",
                    f"{failure_id} event[{i}]: 异常为'非预期'类但 C=0，"
                    f"非预期激活通常需要驾驶员察觉并采取行动，可控性不应为0，请复核"))

    return issues


# ============================================================
# 场景描述质量检查（防模板化）
# ============================================================

# 场景信息分类关键词
SCENARIO_CATEGORY_KEYWORDS = {
    "道路类型": ["高速", "城市", "乡村", "山路", "高架", "隧道", "停车场", "十字路口"],
    "车速": ["高速", "低速", "中速", "km/h", "静止", "加速", "减速"],
    "交通状况": ["拥堵", "畅通", "车流密集", "车辆稀少", "跟车", "超车", "会车"],
    "天气": ["雨天", "雪天", "雾天", "晴天", "夜间", "白天"],
    "驾驶操作": ["转弯", "变道", "制动", "加速", "倒车", "泊车", "上坡", "下坡"],
}


def _count_scenario_categories(scenario: str) -> int:
    """统计场景描述包含几类信息（道路类型/车速/交通状况/天气/驾驶操作）。"""
    if not scenario:
        return 0
    count = 0
    for cat, keywords in SCENARIO_CATEGORY_KEYWORDS.items():
        if any(kw in scenario for kw in keywords):
            count += 1
    return count


def validate_scenario_quality(hazards: list) -> list:
    """检查场景描述质量，防止模板化场景。

    逻辑：
    1. 模板化检测：统计相同场景描述在不同 failure_id 间的复用率。
       如果某个场景描述被 >= 5 个不同 failure_id 复用，报 warning
    2. 情境丰富度：每个场景描述应包含至少两类信息。
       如果事件数 >= 3 且所有场景描述只含1类或更少信息，报 warning
    3. 场景描述为空或过短（< 8个汉字）报 warning

    Returns:
        list of (level, message) — warning 级别
    """
    issues = []

    # 规则1: 场景描述跨 failure_id 复用率
    scenario_fids = {}  # {scenario_text: set of failure_ids}
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        failure_id = hazard.get("failure_id", "")
        if not failure_id:
            continue
        for event in hazard.get("events", []):
            scenario = (event.get("scenario") or "").strip()
            if scenario:
                scenario_fids.setdefault(scenario, set()).add(failure_id)

    for scenario, fids in scenario_fids.items():
        if len(fids) >= 5:
            issues.append(("warning",
                f"场景描述'{scenario[:30]}...'被 {len(fids)} 个不同 failure_id 复用，"
                f"存在模板化风险，请各失效模式根据自身特点定制场景"))

    # 规则2 & 3: 情境丰富度 + 场景过短
    for hazard in hazards:
        if hazard.get("skip"):
            continue
        failure_id = hazard.get("failure_id", "?")
        events = hazard.get("events", [])
        if not events:
            continue

        low_quality_count = 0
        for i, ev in enumerate(events):
            scenario = (ev.get("scenario") or "").strip()

            # 规则3: 场景为空或过短
            if len(scenario) < 8:
                issues.append(("warning",
                    f"{failure_id} event[{i}]: 场景描述过短（{len(scenario)}字），"
                    f"请补充道路、车速、交通等情境信息"))
                low_quality_count += 1
                continue

            # 规则2: 情境丰富度检测
            cat_count = _count_scenario_categories(scenario)
            if cat_count <= 1:
                low_quality_count += 1

        # 规则2: 事件数 >= 3 且所有场景描述只含1类或更少信息
        if len(events) >= 3 and low_quality_count == len(events):
            issues.append(("warning",
                f"{failure_id}: 所有 {len(events)} 个场景描述情境信息不足（≤1类），"
                f"建议从道路类型、车速、交通状况、天气、驾驶操作中至少包含2类"))

    return issues


# ============================================================
# PT 域档位控制命名白名单校验（仅针对 P_func_0001）
# ============================================================

_PT_GEAR_WHITELIST_CACHE: dict | None = None


def _load_pt_gear_whitelist() -> dict | None:
    """加载 pt_gear_anomaly_whitelist.json（惰性加载）"""
    global _PT_GEAR_WHITELIST_CACHE
    if _PT_GEAR_WHITELIST_CACHE is not None:
        return _PT_GEAR_WHITELIST_CACHE
    path = Path(__file__).parent.parent.parent / "references" / "pt_gear_anomaly_whitelist.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        _PT_GEAR_WHITELIST_CACHE = json.load(f)
    return _PT_GEAR_WHITELIST_CACHE


def validate_pt_gear_naming(s3_entries: list) -> list:
    """校验 PT 域档位控制(P_func_0001)的"功能异常表现"命名是否在白名单内。

    仅对 func_id 含 P_func_0001 的 entry 生效，其他功能不受影响。
    不在白名单的命名报 warning（不阻断写入），引导 Agent 对齐参考命名。

    Args:
        s3_entries: s3_hazop.json 的 entries 列表

    Returns:
        list of (level, message) — warning 列表
    """
    issues = []

    wl = _load_pt_gear_whitelist()
    if not wl:
        return issues  # 白名单不存在，跳过

    # 收集白名单标准命名（含档/挡两种写法兼容）
    std_anomalies = set()
    for item in wl.get("anomaly_list", []):
        anom = item.get("anomaly", "").strip()
        if anom:
            std_anomalies.add(anom)
            # 兼容"档"和"挡"两种写法
            std_anomalies.add(anom.replace("档", "挡"))
            std_anomalies.add(anom.replace("挡", "档"))

    # 收集 P_func_0001 的所有 entry
    gear_entries = []
    for entry in s3_entries:
        func_id = entry.get("func_id", "")
        if func_id != "P_func_0001":
            continue
        # 按失效模式分组收集 anomalies
        for anom_obj in entry.get("anomalies", []):
            desc = anom_obj.get("description", "").strip()
            if desc:
                gear_entries.append({
                    "desc": desc,
                    "failure_id": anom_obj.get("failure_id", ""),
                    "failure_mode": anom_obj.get("failure_mode", ""),
                })

    if not gear_entries:
        return issues  # 没有 PT 档位控制 entry，跳过

    # 检查每条命名是否在白名单内
    min_required = 16  # 丢失类至少 12 个切入 + 4 个显示
    non_standard = []
    for ge in gear_entries:
        desc = ge["desc"]
        # 精确匹配
        if desc in std_anomalies:
            continue
        # 模糊匹配：检查是否为粗分类（如"非预期换挡请求"）
        coarse_keywords = ["换挡请求", "换挡控制", "挡位仲裁", "自动P挡逻辑", "EPB请求逻辑", "READY状态", "高压下电"]
        is_coarse = any(kw in desc for kw in coarse_keywords)
        if is_coarse:
            non_standard.append((ge, "粗分类"))
        else:
            non_standard.append((ge, "非标准命名"))

    # 命名不在白名单
    for ge, reason in non_standard:
        issues.append(("warning",
            f"PT档位控制 {ge['failure_id']} 的功能异常表现 '{ge['desc']}' 为{reason}，"
            f"请使用 pt_gear_anomaly_whitelist.json 中的标准命名（如'P档切入D档位失效'、'非预期D档切入P'等）"))

    # 条数检查：档位控制应有 16-39 条
    actual_count = len(gear_entries)
    if actual_count < min_required:
        issues.append(("warning",
            f"PT档位控制(P_func_0001)仅有 {actual_count} 条功能异常表现，"
            f"参考文件有 {wl.get('total_anomalies', 39)} 条，"
            f"应按切换方向枚举（P→D、N→D、R→D等），详见白名单"))

    return issues


# ============================================================
# BD 域车身功能命名白名单校验（仅针对 B_ 开头的 func_id）
# ============================================================

_BD_WHITELIST_CACHE: dict | None = None


def _load_bd_whitelist() -> dict | None:
    """加载 bd_anomaly_whitelist.json（惰性加载）"""
    global _BD_WHITELIST_CACHE
    if _BD_WHITELIST_CACHE is not None:
        return _BD_WHITELIST_CACHE
    path = Path(__file__).parent.parent.parent / "references" / "bd_anomaly_whitelist.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        _BD_WHITELIST_CACHE = json.load(f)
    return _BD_WHITELIST_CACHE


def validate_bd_naming(s3_entries: list) -> list:
    """校验 BD 域车身功能的"功能异常表现"命名是否在白名单内。

    仅对 func_id 以 B_ 开头的 entry 生效，其他域不受影响。
    不在白名单的命名报 error（阻断写入），强制 Agent 对齐参考命名。

    Args:
        s3_entries: s3_hazop.json 的 entries 列表

    Returns:
        list of (level, message) — error 列表
    """
    issues = []

    wl = _load_bd_whitelist()
    if not wl:
        return issues  # 白名单不存在，跳过

    # 收集白名单标准命名（精确匹配）
    std_anomalies = set()
    for item in wl.get("anomaly_list", []):
        anom = item.get("anomaly", "").strip()
        if anom:
            std_anomalies.add(anom)

    # 收集 BD 域的所有 entry（func_id 以 B_ 开头）
    bd_entries = []
    for entry in s3_entries:
        func_id = entry.get("func_id", "")
        if not func_id.startswith("B_"):
            continue
        for anom_obj in entry.get("anomalies", []):
            desc = anom_obj.get("description", "").strip()
            if desc:
                bd_entries.append({
                    "desc": desc,
                    "failure_id": anom_obj.get("failure_id", ""),
                    "func_id": func_id,
                    "func_name": entry.get("func_name", ""),
                })

    if not bd_entries:
        return issues  # 没有 BD 域 entry，跳过

    # 检查每条命名是否在白名单内
    coarse_keywords = set(wl.get("coarse_keywords", []))
    non_standard = []
    for ge in bd_entries:
        desc = ge["desc"]
        # 精确匹配标准命名
        if desc in std_anomalies:
            continue
        # 检查是否为粗分类
        if desc in coarse_keywords:
            non_standard.append((ge, "粗分类"))
        else:
            non_standard.append((ge, "非标准命名"))

    # 命名不在白名单 — 报error
    for ge, reason in non_standard:
        issues.append(("error",
            f"BD域 {ge['failure_id']} 的功能异常表现 '{ge['desc']}' 为{reason}，"
            f"必须使用 bd_anomaly_whitelist.json 中的标准命名"
            f"（如'位置灯无法点亮/位置灯点亮后熄灭'、'转向灯无法点亮/转向灯点亮后熄灭'等）。"
            f"功能: {ge['func_id']} {ge['func_name']}"))

    # 条数检查：参考白名单有 61 条，至少应覆盖 80%
    actual_count = len(bd_entries)
    min_required = int(wl.get('total_anomalies', 61) * 0.8)
    if actual_count < min_required:
        issues.append(("error",
            f"BD域仅有 {actual_count} 条功能异常表现，"
            f"参考白名单有 {wl.get('total_anomalies', 61)} 条，"
            f"至少应覆盖 {min_required} 条（80%）。"
            f"请按功能分类枚举：外部灯光/雨刮/后视镜/门窗天窗/座椅/门锁等，详见白名单"))

    return issues


# ============================================================
# ET 域信息娱乐功能命名白名单校验（仅针对 Info_ 开头的 func_id）
# ============================================================

_ET_WHITELIST_CACHE: dict | None = None


def _load_et_whitelist() -> dict | None:
    """加载 et_anomaly_whitelist.json（惰性加载）"""
    global _ET_WHITELIST_CACHE
    if _ET_WHITELIST_CACHE is not None:
        return _ET_WHITELIST_CACHE
    path = Path(__file__).parent.parent.parent / "references" / "et_anomaly_whitelist.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        _ET_WHITELIST_CACHE = json.load(f)
    return _ET_WHITELIST_CACHE


def validate_et_naming(s3_entries: list) -> list:
    """校验 ET 域信息娱乐功能的"功能异常表现"命名是否在白名单内。

    仅对 func_id 以 Info_ 开头的 entry 生效，其他域不受影响。
    不在白名单的命名报 error（阻断写入），强制 Agent 对齐参考命名。

    Args:
        s3_entries: s3_hazop.json 的 entries 列表

    Returns:
        list of (level, message) — error 列表
    """
    issues = []

    wl = _load_et_whitelist()
    if not wl:
        return issues  # 白名单不存在，跳过

    # 收集白名单标准命名（精确匹配）
    std_anomalies = set()
    for item in wl.get("anomaly_list", []):
        anom = item.get("anomaly", "").strip()
        if anom:
            std_anomalies.add(anom)

    # 收集 ET 域的所有 entry（func_id 以 Info_ 开头）
    et_entries = []
    for entry in s3_entries:
        func_id = entry.get("func_id", "")
        if not func_id.startswith("Info_"):
            continue
        for anom_obj in entry.get("anomalies", []):
            desc = anom_obj.get("description", "").strip()
            if desc:
                et_entries.append({
                    "desc": desc,
                    "failure_id": anom_obj.get("failure_id", ""),
                    "func_id": func_id,
                    "func_name": entry.get("func_name", ""),
                })

    if not et_entries:
        return issues  # 没有 ET 域 entry，跳过

    # 检查每条命名是否在白名单内
    coarse_keywords = set(wl.get("coarse_keywords", []))
    non_standard = []
    for ge in et_entries:
        desc = ge["desc"]
        # 精确匹配标准命名
        if desc in std_anomalies:
            continue
        # 检查是否为粗分类
        if desc in coarse_keywords:
            non_standard.append((ge, "粗分类"))
        else:
            non_standard.append((ge, "非标准命名"))

    # 命名不在白名单 — 报error
    for ge, reason in non_standard:
        issues.append(("error",
            f"ET域 {ge['failure_id']} 的功能异常表现 '{ge['desc']}' 为{reason}，"
            f"必须使用 et_anomaly_whitelist.json 中的标准命名"
            f"（如'Ready灯显示丢失'、'车速表显示车速过大'等）。"
            f"功能: {ge['func_id']} {ge['func_name']}"))

    # 条数检查：参考白名单有 N 条，至少应覆盖 80%
    actual_count = len(et_entries)
    total_std = wl.get('total_anomalies', 59)
    min_required = int(total_std * 0.8)
    if actual_count < min_required:
        issues.append(("error",
            f"ET域仅有 {actual_count} 条功能异常表现，"
            f"参考白名单有 {total_std} 条，"
            f"至少应覆盖 {min_required} 条（80%）。"
            f"请按功能分类枚举：仪表显示/HUD/娱乐系统/导航/手机互联等，详见白名单"))

    return issues
