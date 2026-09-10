# utils/chapter_matcher.py
# 章节匹配：将子功能精确/模糊匹配到第3章节点
# 纯函数，无 CLI
#
# 2026-08-17 重构（修复 P0 章节错配 + 提升效率）：
#   - 匹配升级为四层降级：L1 精确 → L2 核心词 → L3 子串 → L4 模糊
#   - 新增停用词核心词匹配（L2）：剔除"转向/控制"等公共词后做语义核心词包含
#     匹配，修复"转向补偿控制"被 difflib 误配到"转向模式控制"的问题
#   - L1 精确建立标题索引（O(1) 查找），避免全表扫描
#   - L4 模糊阈值从 0.55 收紧到 0.85，命中即标记 low_confidence 供 Agent 复核
#   - H2 分组模糊阈值 0.75 → 0.85，减少候选池污染
# 2026-08-17（数据组5-11 适配）：
#   - 新增 L1b 主干精确：匹配前剥离括号注释（处理"自动紧急制动系统（AEB）" vs
#     "自动紧急制动系统（L2）"类差异，修复数据组9 AD 域 12 未匹配）
#   - H2 组匹配失败时降级为全局候选（修复数据组10 ET 域候选章节为 0 → 182 未匹配）；
#     全局兜底命中（非精确）标 low_confidence 供 Agent 复核，避免跨 H2 误配
#   - 核心词匹配（L2）改为先剥离括号再去停用词

import re
import difflib
from utils.data_models import RelatedItem, SubFunction
from utils.document_context import build_function_description_map, split_heading_number


# ---- 匹配配置 ----

# 领域公共停用词：不承载功能语义（噪声词），核心词匹配时剔除。
# 注意：保留"补偿/回正/软止点/模式"等特征词，它们才是标题的语义核心。
STOPWORDS = ["转向", "助力", "控制", "功能", "系统", "管理", "检测", "整车", "车辆", "模块"]

# L4 模糊匹配阈值（收紧至 0.85，避免共享公共词导致中文标题误判）
FUZZY_THRESHOLD = 0.85
# H2 分组模糊阈值
H2_FUZZY_THRESHOLD = 0.85
# 核心词判定最小长度（防止"制动"等过短词在子串层误匹配多个标题）
SUBSTRING_MIN_CORE_LEN = 2

# 全角/半角/方括号注释（如（AEB）、(L2)、【低配】）：主干精确匹配时剥离
_BRACKET_RE = re.compile(r"[（(【\[].*?[）)】\]]")


def apply_exact_chapter_match(
    related_items: list[RelatedItem],
    chapters: list[dict],
    document_context: dict | None = None,
):
    """兼容优先的章节匹配。

    先执行原 H3/H4 四层匹配；只有真正未匹配或低置信结果才尝试
    document_context 中 H2～H8 的精确/别名精确补充。现有高置信匹配不重排。
    """
    h3_map, h4_map, h3_strip_map, h4_strip_map = _build_index(chapters)
    deep_context_index = _build_deep_context_index(document_context) if document_context else None
    unmatched = []
    duplicate_warnings = []

    for item in related_items:
        group = _find_h2_group(item.func_name, chapters)
        h2_ok = bool(group)
        match_group = group if group else chapters
        candidate_group = group if group else []
        matched_chapters: dict[str, list[dict]] = {}

        for sub in item.sub_functions:
            original_description = sub.description
            ok, method, conf = _match_sub(
                sub, match_group, h3_map, h4_map, h3_strip_map, h4_strip_map,
            )
            legacy_low_conf = ok and (method == "fuzzy" or (not h2_ok and method != "exact"))
            deep_candidates = []
            if (not ok or legacy_low_conf) and document_context:
                deep = _deep_context_match(item, sub, deep_context_index)
                deep_candidates = deep.get("candidates", [])
                if deep.get("matched"):
                    ok = True
                    method = deep["method"]
                    conf = deep["confidence"]
                    sub.chapter = deep["label"]
                    # 深层纠正章节时也替换旧章节描述；不能残留父章节摘要。
                    sub.description = original_description or deep.get("description") or ""
                    evidence_ref = deep.get("evidence_ref")
                    if evidence_ref and evidence_ref not in sub.evidence_refs:
                        sub.evidence_refs.append(evidence_ref)
                    legacy_low_conf = False

            description_missing = ok and not str(sub.description or "").strip()
            legacy_low_conf = legacy_low_conf or description_missing
            setattr(sub, "_match_method", method if ok else "none")
            candidate_chapters = _build_candidates(candidate_group)
            if ok:
                label = sub.chapter or ""
                if label:
                    matched_chapters.setdefault(label, []).append({
                        "name": sub.name,
                        "method": method,
                    })
                if legacy_low_conf:
                    unmatched.append({
                        "desc": _unmatched_desc(item, sub),
                        "func_id": item.func_id,
                        "func_name": item.func_name,
                        "feature_list_id": sub.feature_list_id,
                        "sub_name": sub.name,
                        "scenario": sub.scenario or "",
                        "h2_group": item.func_name,
                        "candidate_chapters": candidate_chapters,
                        "document_context_candidates": deep_candidates,
                        "status": "low_confidence",
                        "match_method": method,
                        "auto_matched_chapter": label,
                        "confidence": round(conf, 3) if conf is not None else None,
                        "h2_group_matched": h2_ok,
                        **({"reason": "matched_section_description_missing"} if description_missing else {}),
                    })
                continue

            unmatched.append({
                "desc": _unmatched_desc(item, sub),
                "func_id": item.func_id,
                "func_name": item.func_name,
                "feature_list_id": sub.feature_list_id,
                "sub_name": sub.name,
                "scenario": sub.scenario or "",
                "h2_group": item.func_name,
                "candidate_chapters": candidate_chapters,
                "document_context_candidates": deep_candidates,
                "status": "unmatched",
                "match_method": "none",
                "auto_matched_chapter": None,
                "confidence": None,
                "h2_group_matched": h2_ok,
            })

        # 一章多 Feature 是允许的。只有包含弱匹配时才保留为复核警告。
        for label, matches in matched_chapters.items():
            if len(matches) <= 1:
                continue
            weak = [m for m in matches if m.get("method") in {"keyword", "substring", "fuzzy"}]
            if not weak:
                continue
            sub_names = [match["name"] for match in matches]
            duplicate_warnings.append({
                "func_id": item.func_id,
                "func_name": item.func_name,
                "chapter_label": label,
                "sub_functions": sub_names,
                "relation_type": "one_to_many_needs_review",
                "message": (
                    f"[{item.func_id}] {item.func_name}: 子功能 {', '.join(sub_names)} "
                    f"共享章节 {label}，且包含弱匹配，请复核章节边界"
                ),
            })

    return unmatched, duplicate_warnings


_ALIAS_REPLACEMENTS = (
    ("换挡", "挡位"),
    ("判定", "判断"),
    ("交通标识", "交通标志"),
    ("后碰撞", "后向碰撞"),
    ("危险警报", "危险报警"),
)
_DEEP_GENERIC_HEADINGS = {
    "功能概述", "功能概要", "功能描述", "功能详细定义", "功能需求",
    "目的", "功能架构图", "功能初始架构", "功能分配", "外部接口", "内部接口",
}


def _alias_normalize(value: str) -> str:
    normalized = _strip_brackets(value)
    for source, target in _ALIAS_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    return normalized


def _build_deep_context_index(document_context: dict) -> dict:
    nodes = document_context.get("nodes", []) or []
    by_id = {node.get("node_id"): node for node in nodes if node.get("node_id")}
    path_cache: dict[str, list[str]] = {}

    def path_for(node_id: str) -> list[str]:
        if node_id in path_cache:
            return path_cache[node_id]
        node = by_id.get(node_id)
        if not node:
            return []
        parent_path = path_for(node.get("parent_id")) if node.get("parent_id") else []
        result = parent_path + [str(node.get("heading_text", ""))]
        path_cache[node_id] = result
        return result

    exact: dict[str, list[dict]] = {}
    alias: dict[str, list[dict]] = {}
    for node_id, node in by_id.items():
        level = int(node.get("level", 0) or 0)
        heading = str(node.get("heading_text", "")).strip()
        if not (2 <= level <= 8) or not heading or _clean_context_heading(heading) in _DEEP_GENERIC_HEADINGS:
            continue
        candidate = {
            "node_id": node_id,
            "label": node.get("label"),
            "heading_text": heading,
            "level": level,
            "heading_path": path_for(node_id),
            "evidence_ref": node.get("evidence_ref"),
        }
        exact.setdefault(_normalize(heading), []).append(candidate)
        alias.setdefault(_alias_normalize(heading), []).append(candidate)
    return {
        "exact": exact,
        "alias": alias,
        "summaries": build_function_description_map(document_context, max_chars=1200),
    }


def _path_matches_function(path: list[str], func_name: str) -> bool:
    target = _alias_normalize(func_name)
    if not target:
        return False
    for heading in path:
        candidate = _alias_normalize(heading)
        if candidate and (candidate == target or candidate in target or target in candidate):
            return True
    return False


def _context_candidates(item: RelatedItem, sub: SubFunction, deep_index: dict | None) -> list[dict]:
    if not deep_index:
        return []
    subjects = [sub.name] + ([sub.scenario] if sub.scenario else [])
    pool = []
    match_kind = "exact"
    for subject in subjects:
        pool.extend(deep_index["exact"].get(_normalize(subject), []))
    if not pool:
        match_kind = "alias_exact"
        for subject in subjects:
            pool.extend(deep_index["alias"].get(_alias_normalize(subject), []))
    if not pool:
        return []
    deduped = []
    seen = set()
    for candidate in pool:
        if candidate["node_id"] in seen:
            continue
        seen.add(candidate["node_id"])
        deduped.append(candidate)
    same_function = [
        candidate for candidate in deduped
        if _path_matches_function(candidate["heading_path"], item.func_name)
    ]
    if same_function:
        deduped = same_function
    return [{**candidate, "match_kind": match_kind} for candidate in deduped[:20]]


def _clean_context_heading(value: str) -> str:
    return split_heading_number(value)[1]


def _deep_context_match(item: RelatedItem, sub: SubFunction, deep_index: dict | None) -> dict:
    candidates = _context_candidates(item, sub, deep_index)
    if len(candidates) != 1:
        return {"matched": False, "candidates": candidates}
    candidate = candidates[0]
    node_id = candidate["node_id"]
    return {
        "matched": True,
        "method": "deep_exact" if candidate["match_kind"] == "exact" else "alias_exact",
        "confidence": 1.0 if candidate["match_kind"] == "exact" else 0.98,
        "label": candidate.get("label") or candidate.get("heading_text"),
        "description": deep_index.get("summaries", {}).get(node_id, "") if deep_index else "",
        "evidence_ref": candidate.get("evidence_ref"),
        "candidates": candidates,
    }


# ========== 索引 ==========

def _build_index(chapters: list[dict]) -> tuple:
    """一次遍历建立归一化标题索引：
    - h3_map / h4_map：原样标题（保留括号），供 L1 精确匹配
    - h3_strip_map / h4_strip_map：剥离括号后的主干，供 L1b 主干精确匹配
    """
    h3_map: dict[str, list[dict]] = {}
    h4_map: dict[str, list[tuple]] = {}
    h3_strip_map: dict[str, list[dict]] = {}
    h4_strip_map: dict[str, list[tuple]] = {}
    for c in chapters:
        h3_map.setdefault(_normalize(c["h3"]), []).append(c)
        h3_strip_map.setdefault(_strip_brackets(c["h3"]), []).append(c)
        for h4 in c.get("h4s", []):
            h4_map.setdefault(_normalize(h4["h4"]), []).append((c, h4))
            h4_strip_map.setdefault(_strip_brackets(h4["h4"]), []).append((c, h4))
    return h3_map, h4_map, h3_strip_map, h4_strip_map


# ========== 四层降级匹配 ==========

def _match_sub(sub: SubFunction, group: list[dict], h3_map: dict, h4_map: dict,
               h3_strip_map: dict, h4_strip_map: dict):
    """对一个子功能做四层降级匹配，返回 (matched, method, confidence)。

    层级（每层锁定后即返回）：
      L1 exact      标题精确相等（H4 优先 → H3；group 内优先，全局索引兜底）
      L1b strip     剥离括号注释后的主干精确相等（处理"（AEB）"vs"（L2）"差异）
      L2 keyword    核心词匹配（仅 H3 层，剥括号+剔除停用词后核心词互相包含）
      L3 substring  子串包含（H4 优先 → H3，要求存在共同核心词）
      L4 fuzzy      模糊匹配（阈值 0.85，命中视为低置信）
    """
    candidates = [sub.name]
    if sub.scenario:
        candidates.append(sub.scenario)

    for cand in candidates:
        if not _normalize(cand):
            continue

        # L1 精确
        hit = _exact_match(cand, group, h3_map, h4_map)
        if hit:
            _fill_sub(sub, hit[0], hit[1])
            return True, "exact", 1.0

        # L1b 主干精确（剥离括号，如"自动紧急制动系统（AEB）" vs "（L2）"）
        hit = _strip_exact_match(cand, group, h3_strip_map, h4_strip_map)
        if hit:
            _fill_sub(sub, hit[0], hit[1])
            return True, "exact", 1.0

        # L2 核心词（仅 H3 层，对齐参考 Excel 的 H3 级章节引用）
        hit = _core_match(cand, group)
        if hit:
            _fill_sub(sub, hit[0], hit[1])
            return True, "keyword", hit[2]

        # L3 子串包含
        hit = _substring_match(cand, group)
        if hit:
            _fill_sub(sub, hit[0], hit[1])
            return True, "substring", 0.9

        # L4 模糊（低置信）
        hit = _fuzzy_match(cand, group)
        if hit:
            _fill_sub(sub, hit[0], hit[1])
            return True, "fuzzy", hit[2]

    return False, None, 0.0


def _exact_match(subject: str, group: list[dict], h3_map: dict, h4_map: dict):
    """L1 精确匹配：H4 优先 → H3；group 内优先，再用全局索引兜底。"""
    nc = _normalize(subject)
    if not nc:
        return None
    # group 内优先（避免跨 H2 误配）
    for c in group:
        for h4 in c.get("h4s", []):
            if _normalize(h4["h4"]) == nc:
                return (c, h4)
    for c in group:
        if _normalize(c["h3"]) == nc:
            return (c, None)
    # 全局索引兜底（H2 组外精确命中，标题全局唯一）
    if nc in h3_map:
        return (h3_map[nc][0], None)
    if nc in h4_map:
        return (h4_map[nc][0][0], h4_map[nc][0][1])
    return None


def _strip_exact_match(subject: str, group: list[dict],
                       h3_strip_map: dict, h4_strip_map: dict):
    """L1b 主干精确匹配：剥离括号注释后主干相等（H4 优先 → H3）。

    处理子功能名与章节标题的括号注释差异，如：
      "自动紧急制动系统（AEB）"  vs  "自动紧急制动系统（L2）"
      "自动泊车APS"             vs  "自动泊车（APS）（L2）"
    多章节同主干时取文档顺序第一个（Agent 可在 unmatched 中复核）。
    """
    sc = _strip_brackets(subject)
    if not sc:
        return None
    for c in group:
        for h4 in c.get("h4s", []):
            if _strip_brackets(h4["h4"]) == sc:
                return (c, h4)
    for c in group:
        if _strip_brackets(c["h3"]) == sc:
            return (c, None)
    # 全局索引兜底（跨 H2 精确主干命中）
    if sc in h3_strip_map:
        return (h3_strip_map[sc][0], None)
    if sc in h4_strip_map:
        return (h4_strip_map[sc][0][0], h4_strip_map[sc][0][1])
    return None


def _core_match(subject: str, group: list[dict]):
    """L2 核心词匹配（仅 H3 层）。

    剔除停用词后，若子功能核心词与章节核心词相等或互相包含，则命中。
    为什么只做 H3：参考 Excel 的功能清单章节引用均为 H3 级
    （如 3.1.3 基础助力补偿），H4 由 L1/L3 覆盖。
    """
    sc = _core_keywords(subject)
    if not sc:
        return None
    best = None
    for c in group:
        tc = _core_keywords(c["h3"])
        if not tc:
            continue
        if sc == tc:
            # 完全相等：直接命中（最高置信）
            return (c, None, 1.0)
        if sc in tc or tc in sc:
            score = _core_score(sc, tc)
            if best is None or score > best[2]:
                best = (c, None, score)
    return best


def _core_score(sc: str, tc: str) -> float:
    """核心词匹配打分：子功能核心词被标题核心词完整包含 → 更精确。"""
    if sc in tc:
        return 0.5 + len(sc) / max(len(tc), 1)
    if tc in sc:
        return 0.3 + len(tc) / max(len(sc), 1)
    return 0.0


def _substring_match(subject: str, group: list[dict]):
    """L3 子串包含匹配（H4 优先 → H3）。要求存在共同核心词，避免公共词误配。"""
    nc = _normalize(subject)
    if not nc:
        return None
    for c in group:
        for h4 in c.get("h4s", []):
            nh4 = _normalize(h4["h4"])
            if nh4 and (nc in nh4 or nh4 in nc) and _has_common_core(nc, nh4):
                return (c, h4)
    for c in group:
        nh3 = _normalize(c["h3"])
        if nh3 and (nc in nh3 or nh3 in nc) and _has_common_core(nc, nh3):
            return (c, None)
    return None


def _fuzzy_match(subject: str, group: list[dict]):
    """L4 模糊匹配（阈值 FUZZY_THRESHOLD，H4 优先 → H3）。命中视为低置信。"""
    best_score = 0.0
    best = None
    for c in group:
        for h4 in c.get("h4s", []):
            score = _fuzzy_ratio(subject, h4["h4"])
            if score > best_score:
                best_score, best = score, (c, h4)
    for c in group:
        score = _fuzzy_ratio(subject, c["h3"])
        if score > best_score:
            best_score, best = score, (c, None)
    if best_score >= FUZZY_THRESHOLD and best is not None:
        return (best[0], best[1], best_score)
    return None


# ========== 匹配辅助 ==========

def _normalize(s: str) -> str:
    return split_heading_number(s)[1].replace(" ", "").replace("\u3000", "")


def _strip_brackets(s: str) -> str:
    """剥离全角/半角/方括号注释（含括号内容）及空白，返回主干。
    如 "自动紧急制动系统（AEB）" → "自动紧急制动系统"。
    用于 L1b 主干精确匹配与 L2 核心词匹配（处理括号注释差异）。
    """
    return _BRACKET_RE.sub("", _normalize(s))


def _core_keywords(s: str) -> str:
    """剥离括号注释、剔除停用词后剩余的核心串。"""
    ns = _strip_brackets(s)
    for w in STOPWORDS:
        ns = ns.replace(w, "")
    return ns


def _has_common_core(a: str, b: str) -> bool:
    """两个字符串是否存在共同核心词（长度 >= SUBSTRING_MIN_CORE_LEN）。"""
    ca, cb = _core_keywords(a), _core_keywords(b)
    if not ca or not cb:
        return False
    return (ca in cb or cb in ca) and min(len(ca), len(cb)) >= SUBSTRING_MIN_CORE_LEN


def _fuzzy_ratio(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _find_h2_group(item_name: str, chapters: list[dict]) -> list[dict]:
    """H2 分组：精确 / 子串 / 模糊（阈值 0.85）。
    保留双向子串以覆盖"短 H2 标题"（如 H2=转向助力，功能名=转向助力功能）；
    候选池污染由后续 L1-L4 的严格匹配控制。
    """
    ni = _normalize(item_name)
    group = []
    for c in chapters:
        nh = _normalize(c["h2"])
        if ni and nh and (
            ni == nh
            or ni in nh
            or nh in ni
            or _fuzzy_ratio(item_name, c["h2"]) >= H2_FUZZY_THRESHOLD
        ):
            group.append(c)
    return group


def _fill_sub(sub: SubFunction, h3: dict, h4: dict | None) -> None:
    if h4 is not None:
        sub.chapter = h4["label"]
        description = h4["desc"] or ""
    else:
        sub.chapter = h3["label"]
        description = h3["desc"]
    if description and not sub.description:
        sub.description = description


# ========== unmatched 条目构造 ==========

def _unmatched_desc(item: RelatedItem, sub: SubFunction) -> str:
    return (f"[{item.func_id}] {item.func_name} / {sub.name}"
            + (f" ({sub.scenario})" if sub.scenario else ""))


def _build_candidates(group: list[dict]) -> list[dict]:
    """构造 Agent 兜底上下文：H2 组下所有章节全文（含 H4 子节）。"""
    candidate_chapters = []
    for c in (group or []):
        ch_info = {
            "label": c["label"],
            "h3": c["h3"],
            "desc": c["desc"] or "",
        }
        ch_info["h4s"] = [
            {"label": h["label"], "h4": h["h4"], "desc": h["desc"] or ""}
            for h in c.get("h4s", [])
        ]
        candidate_chapters.append(ch_info)
    return candidate_chapters
