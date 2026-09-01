#!/usr/bin/env python3
"""
功能匹配引擎 - 在PT域速查表中匹配整车功能

匹配策略:
1. 精确匹配 (>90%): 功能名完全一致或高度相似
2. 相似匹配 (50%-90%): 功能相关但有差异
3. 无匹配 (<50%): 在速查表中找不到相似功能
"""

import re
from difflib import SequenceMatcher
from typing import Any


def normalize_function_name(name: str) -> str:
    """标准化功能名称，去除多余空格和标点"""
    if not name:
        return ""
    # 去除多余空格
    name = " ".join(name.strip().split())
    # 去除常见的后缀
    name = re.sub(r'功能$', '', name)
    return name.strip()


def extract_keywords(name: str) -> set[str]:
    """从功能名中提取关键词"""
    # 常见的功能相关词汇
    keywords = set()

    # 分词（简单按常见词汇切分）
    common_words = [
        "档位", "挡位", "控制", "显示", "管理", "功能",
        "高压", "上下电", "热管理", "驱动", "充放电",
        "能量", "回收", "制动", "安全", "能耗", "续航"
    ]

    for word in common_words:
        if word in name:
            keywords.add(word)

    return keywords


def calculate_similarity(name1: str, name2: str) -> float:
    """
    计算两个功能名的相似度

    综合考虑:
    1. 字符串编辑距离
    2. 关键词重叠度
    """
    # 标准化
    n1 = normalize_function_name(name1)
    n2 = normalize_function_name(name2)

    # 完全相同
    if n1 == n2:
        return 1.0

    # 字符串相似度 (SequenceMatcher)
    seq_sim = SequenceMatcher(None, n1, n2).ratio()

    # 关键词重叠度
    kw1 = extract_keywords(n1)
    kw2 = extract_keywords(n2)

    if not kw1 and not kw2:
        keyword_sim = 0.0
    elif not kw1 or not kw2:
        keyword_sim = 0.0
    else:
        intersection = len(kw1 & kw2)
        union = len(kw1 | kw2)
        keyword_sim = intersection / union if union > 0 else 0.0

    # 综合得分：字符串相似度70%，关键词相似度30%
    total_sim = seq_sim * 0.7 + keyword_sim * 0.3

    return total_sim


def resolve_pt_function_semantics(
    function_name: str,
    pt_pack: dict[str, Any],
    semantic_texts: list[str] | None = None,
) -> dict[str, Any]:
    """按 PT Pack 的显式别名和语义角色解析功能。

    该接口与旧的 ``match_function_in_pt_pack`` 保持分离：旧接口返回历史
    function_id，供迁移期代码兼容；本接口返回 canonical family/role/unit，
    供新的 PT 运行时流程使用。来源 func_id/failure_id 永远不参与匹配。
    """
    catalog = pt_pack.get("function_catalog", {}) or {}
    aliases = catalog.get("known_input_aliases", {}) or {}
    families = aliases.get("families", {}) if isinstance(aliases, dict) else {}
    matchers = catalog.get("function_matchers", []) or []
    normalized = normalize_function_name(function_name)
    candidates: list[dict[str, Any]] = []

    for family, definition in families.items():
        if not isinstance(definition, dict):
            continue
        patterns = definition.get("name_patterns", []) or []
        for pattern in patterns:
            if normalize_function_name(pattern) == normalized:
                candidates.append({
                    "canonical_function_family": family,
                    "confidence": 1.0,
                    "match_type": "exact_alias",
                    "matched_pattern": pattern,
                    "default_semantic_role": definition.get("default_semantic_role"),
                })

    if not candidates:
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            family = matcher.get("canonical_function_family")
            patterns = list(matcher.get("name_patterns", []) or [])
            patterns.extend(matcher.get("keywords", []) or [])
            scores = [calculate_similarity(normalized, str(pattern)) for pattern in patterns if pattern]
            if family and scores:
                best = max(scores)
                if best >= 0.5:
                    candidates.append({
                        "canonical_function_family": family,
                        "confidence": best,
                        "match_type": "similar" if best < 0.9 else "exact",
                        "matched_pattern": patterns[scores.index(best)],
                    })

    by_family: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        family = candidate["canonical_function_family"]
        prior = by_family.get(family)
        if prior is None or candidate["confidence"] > prior["confidence"]:
            by_family[family] = candidate
    candidates = sorted(by_family.values(), key=lambda item: item["confidence"], reverse=True)

    if not candidates:
        return {
            "status": "unknown",
            "canonical_function_family": None,
            "semantic_role": None,
            "analysis_unit_id": None,
            "disposition": "needs_review",
            "confidence": 0.0,
            "candidates": [],
        }
    if len(candidates) > 1 and candidates[0]["confidence"] - candidates[1]["confidence"] < 0.1:
        return {
            "status": "ambiguous",
            "canonical_function_family": None,
            "semantic_role": None,
            "analysis_unit_id": None,
            "disposition": "needs_review",
            "confidence": candidates[0]["confidence"],
            "candidates": candidates,
        }

    best = candidates[0]
    family = best["canonical_function_family"]
    texts = [function_name] + (semantic_texts or [])
    role_matches: list[str] = []
    role_evidence: list[dict[str, str]] = []
    for role, patterns in (catalog.get("semantic_roles", {}) or {}).items():
        for pattern in patterns if isinstance(patterns, list) else []:
            if isinstance(pattern, str) and pattern and any(pattern in str(text) for text in texts):
                role_matches.append(role)
                role_evidence.append({"semantic_role": role, "pattern": pattern})
    aliases_definition = families.get(family, {}) if isinstance(families, dict) else {}
    default_role = aliases_definition.get("default_semantic_role") if isinstance(aliases_definition, dict) else None
    roles = list(dict.fromkeys(role_matches))
    if len(roles) > 1:
        return {
            "status": "ambiguous",
            "canonical_function_family": family,
            "semantic_role": None,
            "analysis_unit_id": None,
            "disposition": "needs_review",
            "confidence": best["confidence"],
            "candidates": candidates,
            "role_evidence": role_evidence,
        }
    role = roles[0] if roles else default_role
    analysis_catalog = pt_pack.get("analysis_catalog", {}) or {}
    units = [
        unit for unit in analysis_catalog.get("analysis_units", [])
        if isinstance(unit, dict)
        and unit.get("canonical_function_family") == family
        and (not role or role in (unit.get("covered_semantic_roles", []) or []))
    ]
    analysis_unit_id = units[0].get("analysis_unit_id") if len(units) == 1 else None
    if role:
        scope_rules = [
            rule for rule in catalog.get("scope_rules", []) or []
            if isinstance(rule, dict) and rule.get("semantic_role") == role
        ]
    else:
        scope_rules = []
    if len(scope_rules) == 1:
        disposition = scope_rules[0].get("disposition")
    else:
        disposition = "analyze" if analysis_unit_id else "needs_review"
    return {
        "status": "resolved" if analysis_unit_id or disposition == "exclude" else "needs_review",
        "canonical_function_family": family,
        "semantic_role": role,
        "analysis_unit_id": analysis_unit_id,
        "disposition": disposition,
        "confidence": best["confidence"],
        "candidates": candidates,
        "role_evidence": role_evidence,
    }


def match_function_in_pt_pack(
    function_name: str,
    pt_pack: dict[str, Any],
    exact_threshold: float = 0.9,
    similar_threshold: float = 0.5
) -> dict[str, Any]:
    """
    在PT速查表中匹配功能

    参数:
        function_name: 当前项目的功能名
        pt_pack: PT域Domain Pack
        exact_threshold: 精确匹配阈值（默认0.9）
        similar_threshold: 相似匹配阈值（默认0.5）

    返回:
        {
            "match_type": "exact" | "similar" | "none",
            "matched_function": "档位控制及显示功能",
            "function_id": "P_func_0001",
            "confidence": 0.95,
            "similar_functions": [...]  # match_type='similar'时提供多个候选
        }
    """
    failure_mode_rules = pt_pack.get("analysis_catalog", {}).get("failure_mode_rules", {})

    if not failure_mode_rules:
        return {
            "match_type": "none",
            "matched_function": None,
            "function_id": None,
            "confidence": 0.0,
            "similar_functions": []
        }

    # 计算与所有功能的相似度
    similarities = []

    for func_id, failure_modes in failure_mode_rules.items():
        # 从hazop_patterns或event_matrix中查找功能名
        func_name = _find_function_name(func_id, pt_pack)

        if func_name:
            sim = calculate_similarity(function_name, func_name)
            similarities.append({
                "function_id": func_id,
                "function_name": func_name,
                "similarity": sim,
                "failure_modes": failure_modes
            })

    # 按相似度排序
    similarities.sort(key=lambda x: x["similarity"], reverse=True)

    if not similarities:
        return {
            "match_type": "none",
            "matched_function": None,
            "function_id": None,
            "confidence": 0.0,
            "similar_functions": []
        }

    best_match = similarities[0]

    # 判断匹配类型
    if best_match["similarity"] >= exact_threshold:
        return {
            "match_type": "exact",
            "matched_function": best_match["function_name"],
            "function_id": best_match["function_id"],
            "confidence": best_match["similarity"],
            "similar_functions": []
        }
    elif best_match["similarity"] >= similar_threshold:
        # 返回相似度最高的3个作为候选
        candidates = similarities[:3]
        return {
            "match_type": "similar",
            "matched_function": best_match["function_name"],
            "function_id": best_match["function_id"],
            "confidence": best_match["similarity"],
            "similar_functions": [
                {
                    "function_id": c["function_id"],
                    "function_name": c["function_name"],
                    "similarity": c["similarity"]
                }
                for c in candidates
            ]
        }
    else:
        return {
            "match_type": "none",
            "matched_function": None,
            "function_id": None,
            "confidence": 0.0,
            "similar_functions": []
        }


def _find_function_name(function_id: str, pt_pack: dict[str, Any]) -> str | None:
    """从PT pack中查找function_id对应的功能名"""
    # 优先从function_index中获取
    function_index = pt_pack.get("function_catalog", {}).get("function_index", {})
    if function_id in function_index:
        return function_index[function_id]

    # 尝试从hazop_patterns中查找
    hazop_patterns = pt_pack.get("analysis_catalog", {}).get("hazop_patterns", [])
    for pattern in hazop_patterns:
        if pattern.get("failure_id", "").startswith(function_id.replace("func", "MF")):
            func_name = pattern.get("function")
            if func_name:
                return func_name

    # 尝试从event_matrix中查找
    events = pt_pack.get("risk_catalog", {}).get("event_matrix", [])
    for event in events:
        if event.get("failure_id", "").startswith(function_id.replace("func", "MF")):
            func_name = event.get("function")
            if func_name:
                return func_name

    # 如果都找不到，返回None
    return None


def get_all_functions_summary(pt_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """
    获取PT速查表中所有功能的摘要信息

    返回:
        [
            {
                "function_id": "P_func_0001",
                "function_name": "档位控制及显示功能",
                "failure_mode_count": 3,
                "hazop_count": 5,
                "hara_event_count": 8
            },
            ...
        ]
    """
    failure_mode_rules = pt_pack.get("analysis_catalog", {}).get("failure_mode_rules", {})
    hazop_patterns = pt_pack.get("analysis_catalog", {}).get("hazop_patterns", [])
    events = pt_pack.get("risk_catalog", {}).get("event_matrix", [])

    summary = []

    for func_id in failure_mode_rules.keys():
        func_name = _find_function_name(func_id, pt_pack)

        # 统计HAZOP条目数
        hazop_count = sum(
            1 for p in hazop_patterns
            if p.get("failure_id", "").startswith(func_id.replace("func", "MF"))
        )

        # 统计HARA事件数
        hara_count = sum(
            1 for e in events
            if e.get("failure_id", "").startswith(func_id.replace("func", "MF"))
        )

        summary.append({
            "function_id": func_id,
            "function_name": func_name or func_id,
            "failure_mode_count": len(failure_mode_rules[func_id]),
            "hazop_count": hazop_count,
            "hara_event_count": hara_count
        })

    return summary
