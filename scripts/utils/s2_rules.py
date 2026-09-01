# scripts/utils/s2_rules.py
# S2 失效模式参考案例匹配引擎
# 从 references/s2-lookup-table.json 加载历史案例，按相似度取 Top-3 作为 Agent 推理参考
# 不做硬匹配：所有功能仍由 Agent 推理，suggestions 仅作为参考

import json
from difflib import SequenceMatcher
from pathlib import Path

from utils.data_models import canonicalize_domain

# 常量：11 种失效模式（与 data_models.py 保持一致，避免循环导入）
FAILURE_MODES = [
    "丢失", "非预期", "间歇", "过多", "过少",
    "过早", "反向", "振荡", "部分", "过晚", "卡滞",
]

# 常见功能名后缀（匹配时去除）
_FUNC_SUFFIXES = ["功能", "控制", "管理", "系统", "装置", "设备"]

# 匹配层次
MATCH_EXACT = "exact"           # 功能名完全一致
MATCH_CORE_WORD = "core_word"   # 去后缀后核心词包含
MATCH_FUZZY = "fuzzy"           # difflib 序列相似度 >= 阈值

_FUZZY_THRESHOLD = 0.5          # 模糊匹配最低相似度
_TOP_N = 3                      # 返回前 N 条参考


def _load_lookup_table() -> list[dict]:
    """加载参考案例库 JSON"""
    # 相对于本文件：scripts/utils/s2_rules.py → references/s2-lookup-table.json
    skill_root = Path(__file__).resolve().parent.parent.parent
    table_path = skill_root / "references" / "s2-lookup-table.json"
    if not table_path.exists():
        return []
    with open(table_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _strip_suffix(name: str) -> str:
    """去除功能名常见后缀，提取核心词"""
    result = name
    for suffix in _FUNC_SUFFIXES:
        if result.endswith(suffix) and len(result) > len(suffix):
            result = result[:-len(suffix)]
    return result


def _compute_similarity(query: str, candidate: str) -> float:
    """计算两个功能名的相似度分数（0.0 ~ 1.0）

    三层策略：
    1. 精确匹配 → 1.0
    2. 核心词包含（去后缀后一方包含另一方）→ 0.85
    3. difflib 序列相似度 → 0.0 ~ 0.8
    """
    if query == candidate:
        return 1.0

    # 核心词匹配
    q_core = _strip_suffix(query)
    c_core = _strip_suffix(candidate)
    if q_core and c_core:
        if q_core in c_core or c_core in q_core:
            return 0.85

    # difflib 序列相似度
    ratio = SequenceMatcher(None, query, candidate).ratio()
    # 缩放到 0~0.8 区间，确保核心词匹配优先
    return min(ratio * 0.8, 0.8)


def _is_mode_selected(missing_modes: list[str], selected_modes: list[str], mode: str) -> bool:
    """判断某个失效模式是否被选中（综合 selected_modes 和 missing_modes）

    逻辑：
    - 如果 mode 在 selected_modes 中 → 选中
    - 如果 mode 在 missing_modes 中（模板缺该列）→ 未选中
    - 如果 mode 不在 missing_modes 且不在 selected_modes 中（模板有列但没打√）→ 未选中
    """
    if mode in selected_modes:
        return True
    return False


def get_s2_suggestions(func_name: str, domain: str = None,
                       sub_functions: list[dict] = None) -> list[dict]:
    """为指定功能获取 Top-N 参考案例

    参数:
        func_name: 功能名称（如 "转向助力功能"）
        domain: 域代码（如 "CS"），可选，同域优先
        sub_functions: 子功能列表（含 name/description），可选，用于丰富匹配上下文

    返回:
        排序后的参考案例列表，每条包含:
        - ref_func_name: 参考功能名
        - ref_domain: 参考域
        - selected_modes: 选中的模式
        - reason: 选择理由
        - groups: 来源数据组
        - similarity: 相似度分数
        - match_type: 匹配类型 (exact/core_word/fuzzy)
        - note: 特殊说明（如冲突提示）
    """
    table = _load_lookup_table()
    if not table:
        return []

    scored = []
    for case in table:
        ref_name = case.get("func_name", "")
        ref_domain = case.get("domain", "")
        sim = _compute_similarity(func_name, ref_name)

        # 域加分使用 canonical code；例如当前 PT 与历史 P 属同一域。
        if domain and canonicalize_domain(ref_domain) == canonicalize_domain(domain):
            sim = min(sim + 0.1, 1.0)

        if sim < _FUZZY_THRESHOLD:
            continue

        # 确定匹配类型
        if sim >= 1.0:
            match_type = MATCH_EXACT
        elif sim >= 0.85:
            match_type = MATCH_CORE_WORD
        else:
            match_type = MATCH_FUZZY

        scored.append({
            "ref_func_name": ref_name,
            "ref_domain": ref_domain,
            "selected_modes": case.get("selected_modes", []),
            "reason": case.get("reason", ""),
            "groups": case.get("groups", []),
            "similarity": round(sim, 2),
            "match_type": match_type,
        })

    # 按相似度降序排序
    scored.sort(key=lambda x: x["similarity"], reverse=True)

    # 取 Top-N
    result = scored[:_TOP_N]

    # 如果有多条同名但模式不同（冲突），加 note 提示 Agent
    if len(result) >= 2:
        exact_matches = [r for r in result if r["match_type"] == MATCH_EXACT]
        if len(exact_matches) >= 2:
            mode_sets = {tuple(r["selected_modes"]) for r in exact_matches}
            if len(mode_sets) > 1:
                for r in exact_matches:
                    r["note"] = "同名功能在不同项目中模式不同，需结合当前项目子功能列表判断"

    return result


def compute_s2_for_related_items(related_items: list[dict]) -> dict:
    """批量计算所有相关项的 S2 参考建议

    参数:
        related_items: intermediate.json 中的 related_items 列表

    返回:
        {func_id: [suggestion, ...]} 映射
    """
    result = {}
    for item in related_items:
        func_id = item.get("func_id", "")
        func_name = item.get("func_name", "")
        domain = item.get("domain", "")
        sub_funcs = item.get("sub_functions", [])

        suggestions = get_s2_suggestions(func_name, domain, sub_funcs)
        if suggestions:
            result[func_id] = suggestions

    return result


def format_suggestions_text(suggestions: list[dict]) -> str:
    """将建议列表格式化为人类可读文本（供 parse 输出日志使用）"""
    if not suggestions:
        return "  （无参考案例）"
    lines = []
    for s in suggestions:
        modes_str = ",".join(s["selected_modes"])
        note = f" ⚠️ {s['note']}" if s.get("note") else ""
        lines.append(
            f"  [{s['similarity']:.2f}] {s['ref_domain']}/{s['ref_func_name']} "
            f"→ {modes_str} (数据组{s['groups']}){note}"
        )
    return "\n".join(lines)
