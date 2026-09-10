# scripts/utils/data_models.py
# 数据模型定义（扁平结构，Skill scripts/ 内直接 import）
from dataclasses import dataclass, field
import re
from typing import Any, Iterable


# 失效模式固定的11种（顺序即 Excel 列顺序）
FAILURE_MODES = [
    "丢失", "非预期", "间歇", "过多", "过少",
    "过早", "反向", "振荡", "部分", "过晚", "卡滞",
]

# 域代码 → 中文名（运行时统一使用 canonical Domain Pack code）
DOMAIN_NAMES = {
    "PT": "动力域",
    "CB": "底盘制动域",
    "CS": "底盘转向域",
    "AD": "智驾域",
    "BD": "车身域",
    "ET": "娱乐域",
    # 兼容旧规则/旧数据中的别名；新输出应写 canonical code。
    "P": "动力域",
    "A": "智驾域",
    "B": "车身域",
    "Info": "娱乐域",
}


_CANONICAL_DOMAIN_ALIASES = {
    "P": "PT",
    "POWERTRAIN": "PT",
    "PT": "PT",
    "动力": "PT",
    "动力域": "PT",
    "动力总成": "PT",
    "动力总成域": "PT",
    "A": "AD",
    "ADAS": "AD",
    "AD": "AD",
    "智驾": "AD",
    "智驾域": "AD",
    "自动驾驶": "AD",
    "B": "BD",
    "BODY": "BD",
    "BD": "BD",
    "车身": "BD",
    "车身域": "BD",
    "Info": "ET",
    "INFOTAINMENT": "ET",
    "ET": "ET",
    "娱乐": "ET",
    "娱乐域": "ET",
    "信息娱乐": "ET",
    "CB": "CB",
    "CHASSIS_BRAKE": "CB",
    "制动": "CB",
    "制动域": "CB",
    "底盘制动": "CB",
    "底盘制动域": "CB",
    "CS": "CS",
    "CHASSIS_STEERING": "CS",
    "转向": "CS",
    "转向域": "CS",
    "底盘转向": "CS",
    "底盘转向域": "CS",
}

_SUPPORTED_CANONICAL_DOMAINS = ("CS", "CB", "PT", "AD", "ET", "BD")


class DomainResolutionError(ValueError):
    """域无法由输入元数据/语义稳定确定时抛出的显式错误。"""


def canonicalize_domain(domain: Any) -> str:
    """将项目/模板/历史域别名归一化为 Domain Pack canonical code。"""
    raw = str(domain or "").strip()
    if not raw:
        return ""
    direct = _CANONICAL_DOMAIN_ALIASES.get(raw)
    if direct:
        return direct
    upper = raw.upper()
    for alias, canonical in _CANONICAL_DOMAIN_ALIASES.items():
        if alias.upper() == upper:
            return canonical
    return upper


def _domain_from_id(func_id: str) -> str:
    value = str(func_id or "").strip()
    if "_func_" not in value:
        return ""
    return canonicalize_domain(value.split("_func_", 1)[0])


def _source_domain_hint(source_path: Any) -> str:
    """从文档/模板路径提取显式域提示；不会把任意单字母当作域。"""
    if not source_path:
        return ""
    text = str(source_path)
    lowered = text.lower()
    keyword_map = (
        (("动力域", "动力总成", "powertrain", "pt domain"), "PT"),
        (("底盘转向", "转向域", "ch steering", "steering", "cs domain"), "CS"),
        (("底盘制动", "制动域", "ch brake", "brake", "cb domain"), "CB"),
        (("智驾域", "自动驾驶", "adas", "ad domain"), "AD"),
        (("信息娱乐", "娱乐域", "infotainment", "et domain"), "ET"),
        (("车身域", "body domain", "bd domain"), "BD"),
    )
    for keywords, domain in keyword_map:
        if any(keyword in lowered for keyword in keywords):
            return domain

    # 英文短代码只接受独立 token，避免把普通路径中的 B/AD 等误识别为域。
    for token, domain in (("PT", "PT"), ("CS", "CS"), ("CB", "CB"), ("AD", "AD"), ("ET", "ET"), ("BD", "BD")):
        if re.search(rf"(?<![A-Za-z0-9]){token}(?![A-Za-z0-9])", text, flags=re.IGNORECASE):
            return domain
    return ""


def _semantic_domain_scores(semantic_texts: Iterable[str]) -> dict[str, tuple[int, list[str]]]:
    """以 Domain Pack 的功能族/语义角色模式计算域候选，不读取项目 ID。"""
    texts = [str(value).strip() for value in semantic_texts if str(value or "").strip()]
    if not texts:
        return {}

    # 延迟导入，避免 data_models 与 Domain Pack 工具之间形成模块初始化耦合。
    from utils.domain_packs import load_domain_pack

    scores: dict[str, tuple[int, list[str]]] = {}
    for domain in _SUPPORTED_CANONICAL_DOMAINS:
        try:
            pack = load_domain_pack(domain)
        except ValueError:
            continue
        score = 0
        evidence: list[str] = []
        catalog = pack.get("function_catalog", {})
        for matcher in catalog.get("function_matchers", []) or []:
            if not isinstance(matcher, dict):
                continue
            family = matcher.get("canonical_function_family", "")
            name_patterns = [
                pattern for pattern in matcher.get("name_patterns", [])
                if isinstance(pattern, str) and pattern.strip()
            ]
            keyword_patterns = [
                pattern for pattern in matcher.get("keywords", [])
                if isinstance(pattern, str) and pattern.strip()
            ]
            name_hit = next(
                (pattern for pattern in name_patterns if any(pattern in text for text in texts)),
                None,
            )
            keyword_hit = next(
                (pattern for pattern in keyword_patterns if any(pattern in text for text in texts)),
                None,
            )
            # 同一功能族的重叠别名只计一次，避免某个域因为别名更多而
            # 在跨域输入上“刷分”并掩盖真实歧义。
            if name_hit:
                score += 4
                evidence.append(f"{family}:{name_hit}")
            elif keyword_hit:
                score += 2
                evidence.append(f"{family}:{keyword_hit}")
        for role, patterns in (catalog.get("semantic_roles", {}) or {}).items():
            if not isinstance(patterns, list):
                continue
            for pattern in patterns:
                if not isinstance(pattern, str) or not pattern.strip():
                    continue
                for text in texts:
                    if pattern in text:
                        score += 1
                        evidence.append(f"role:{role}:{pattern}")
                        break
        if score:
            scores[domain] = (score, list(dict.fromkeys(evidence)))
    return scores


def resolve_domain(
    func_id: str = "",
    *,
    explicit_domain: str = "",
    semantic_texts: Iterable[str] | None = None,
    source_path: Any = None,
    strict: bool = True,
) -> str:
    """按合同解析域：显式域/模板域 > Domain Pack 语义 > ID 前缀。

    `func_id` 仅作为最后的兼容提示；它不能覆盖显式域或语义域。
    无法稳定确定时默认抛出 :class:`DomainResolutionError`，避免继续生成
    `unknown` Domain Pack 上下文和空案例库预填。
    """
    explicit = canonicalize_domain(explicit_domain)
    if explicit:
        if explicit not in _SUPPORTED_CANONICAL_DOMAINS:
            raise DomainResolutionError(f"显式域 '{explicit_domain}' 未配置 Domain Pack")
        return explicit

    source_hint = _source_domain_hint(source_path)
    if source_hint:
        return source_hint

    scores = _semantic_domain_scores(semantic_texts or [])
    if scores:
        best_score = max(score for score, _ in scores.values())
        best = [domain for domain, (score, _) in scores.items() if score == best_score]
        if len(best) == 1:
            return best[0]
        evidence = {domain: values[1][:3] for domain, values in scores.items()}
        raise DomainResolutionError(
            f"功能语义同时匹配多个域，无法自动确定: {evidence}；请传入显式 domain"
        )

    id_domain = _domain_from_id(func_id)
    if id_domain in _SUPPORTED_CANONICAL_DOMAINS:
        return id_domain

    message = (
        f"无法解析功能 '{func_id or '<empty>'}' 的域：未提供显式域，"
        "文档路径/模板未命中域提示，Domain Pack 语义也未命中"
    )
    if strict:
        raise DomainResolutionError(message)
    return "unknown"


def extract_domain(
    func_id: str,
    *,
    explicit_domain: str = "",
    semantic_texts: Iterable[str] | None = None,
    source_path: Any = None,
) -> str:
    """解析 canonical 域；ID 前缀只保留为最后的兼容性提示。"""
    return resolve_domain(
        func_id,
        explicit_domain=explicit_domain,
        semantic_texts=semantic_texts,
        source_path=source_path,
        strict=True,
    )


@dataclass
class SubFunction:
    """子功能：对应"相关项功能清单"Sheet 中的一行"""
    feature_list_id: str
    name: str
    description: str
    chapter: str
    scenario: str = ""        # 表中"相关项中的子功能"列（动力域文档有，底盘域为空）
    components: list[dict] = field(default_factory=list)  # 同一 Feature ID 下的原始组件行
    source_table_index: int | None = None
    source_row: int | None = None
    evidence_refs: list[str] = field(default_factory=list)
    remark: str = "/"
    is_hara: bool = True      # 是否进行HARA分析，默认是


@dataclass
class RelatedItem:
    """整车功能（相关项）：包含多个子功能"""
    func_id: str                  # 整车功能ID，如 CS_func_0001
    func_name: str                # 整车层级功能名，如 转向助力功能
    domain: str = ""              # 域代码，从 func_id 前缀提取（P/CB/CS/A/B/Info）
    is_hara: bool = True          # 是否进行 HARA 分析（stage1 判定后填入）
    sub_functions: list[SubFunction] = field(default_factory=list)


@dataclass
class FailureModeRow:
    """失效模式 Sheet 的一行：一个整车功能对应一行"""
    func_id: str                  # 整车功能ID
    func_name: str                # 整车功能名
    selected_modes: set[str]      # 打√的失效模式名称集合
    reason: str                   # 选择理由


@dataclass
class HazopAnomaly:
    """HAZOP 分析中的一条功能异常表现"""
    description: str         # 功能异常表现描述
    failure_id: str          # 功能失效 ID（如 P_MF_0001_01）
    hazard: str              # 整车危害描述
    associated_hara: str = ""  # 关联 HARA 编号（S3 阶段留空，S4 阶段填入）


@dataclass
class HazopEntry:
    """HAZOP 分析中的一个功能-失效模式组合"""
    func_id: str                      # 整车功能 ID
    func_name: str                    # 整车功能名
    failure_mode: str                 # 失效模式（丢失/非预期/...）
    anomalies: list[HazopAnomaly]     # 该组合下的异常表现列表


# ============================================================
# S4 HARA 数据模型
# ============================================================

@dataclass
class HaraEvent:
    """HARA 分析中的一条危害事件（一个场景一行）"""
    scenario: str                          # F: 运行场景
    description: str                       # G: 危害事件描述
    severity: int                          # H: 严重度 0-3
    severity_reason: str = ""              # I: 对 S 的理由
    exposure: int = None                   # J: 暴露概率 1-4（S=0 时为 None）
    exposure_reason: str = None            # K: 对 E 的理由
    controllability: int = None            # L: 可控性 0-3（S=0 时为 None）
    controllability_reason: str = None     # M: 对 C 的理由
    safety_goal: str = None                # P: 安全目标（ASIL≥A 时填写）
    safe_state: str = None                 # Q: 安全状态
    ftti: str = None                       # R: FTTI
    note: str = None                       # S: 注释
    engineering_override: str = None       # 工程判断覆盖（仅 "QM"）
    sec_source: str = None                 # S/E/C 来源: reference_exact/reference_pattern/agent_evaluated
    # 以下字段由 validate 阶段脚本填写，Agent 不填
    hazard_id: str = None                  # A: 危害事件 ID（脚本分配）
    asil: str = None                       # N: ASIL（脚本计算）
    safety_goal_id: str = None             # O: 安全目标 ID（脚本分配）


@dataclass
class HaraHazardGroup:
    """HARA 中按 HAZOP 危害分组的一组事件"""
    hazard_key: str                        # 唯一标识：{failure_id}__{序号}
    func_id: str                           # 整车功能 ID
    func_name: str                         # 整车功能名
    failure_id: str                        # 功能失效 ID
    failure_mode: str                      # 失效模式
    anomaly: str                           # 功能异常表现
    hazard: str                            # 整车危害
    skip: bool = False                     # 是否跳过（"不涉及"）
    skip_reason: str = None                # 跳过原因
    is_reference: bool = False             # 是否为引用行
    reference_to: str = None               # 引用的 failure_id
    events: list[HaraEvent] = field(default_factory=list)
    # 以下字段由 validate 阶段脚本填写
    id_range: str = None                   # 该组事件的 ID 范围（回填 HAZOP G 列）


@dataclass
class HaraFunctionConfig:
    """S4 中每个功能的类型配置（安全状态/FTTI 预填）"""
    func_id: str
    func_name: str
    function_type: str = None              # 脚本推断的功能类型
    safe_state_default: str = None         # 默认安全状态
    ftti_by_mode: dict = field(default_factory=dict)  # {失效模式: FTTI}
