"""文档上下文合同、质量门和 Agent 文档解析请求。

该模块只处理通用 JSON 数据，不依赖具体域或 HARA 业务规则。新增字段均为
intermediate 的可选增量字段，以保持旧调用方兼容。
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Iterable


DOCUMENT_CONTEXT_SCHEMA = "document_context_v1"
PARSE_QUALITY_SCHEMA = "document_parse_quality_v1"
DOCUMENT_PARSE_REQUEST_SCHEMA = "document_parse_request_v1"
AGENT_DOCUMENT_STRUCTURE_SCHEMA = "agent_document_structure_v1"

MAX_NODE_BODY_PARAGRAPHS = 3
MAX_NODE_BODY_CHARS = 600
MAX_AGENT_CONTEXT_CHARS = 120_000
MAX_AGENT_CONTEXT_NODES = 500

IMPORTANT_SECTION_KEYWORDS = (
    "功能概述", "功能概要", "功能描述", "功能详细定义", "功能需求",
    "触发", "使能", "运行条件", "运行模式", "工作模式",
    "输入", "输出", "接口", "功能分配", "控制对象", "执行器",
    "边界", "故障", "降级", "安全监控", "安全机制",
    "假设", "环境", "限制", "风险降低措施", "已知的安全信息",
)

_NORMALIZE_RE = re.compile(r"[\s\u3000，。；：、/\\·,.;:!！?？\-—_]+")


def normalize_text(value: Any) -> str:
    return _NORMALIZE_RE.sub("", str(value or "").strip()).lower()


def classify_section(heading_path: Iterable[str]) -> str:
    """把章节路径归一成少量对 HARA 有意义的类别。"""
    texts = [str(value or "").strip() for value in heading_path]
    leaf = texts[-1] if texts else ""
    rules = (
        (("故障", "降级", "安全监控", "安全机制"), "failure_and_degradation"),
        (("触发", "使能", "运行条件", "运行模式", "工作模式"), "operating_conditions"),
        (("输入", "输出", "接口", "功能分配"), "interfaces_and_allocation"),
        (("控制对象", "执行器"), "control_target"),
        (("假设",), "assumptions"),
        (("环境", "限制"), "environment_and_constraints"),
        (("风险降低措施",), "external_risk_reduction"),
        (("已知的安全信息",), "known_safety_information"),
        (("功能概述", "功能概要"), "function_overview"),
        (("功能描述", "功能详细定义", "功能需求", "整车功能定义"), "function_description"),
    )
    for keywords, section_type in rules:
        if any(keyword in leaf for keyword in keywords):
            return section_type
    return "other"


def node_body_text(node: dict) -> str:
    body = node.get("body", []) or []
    values = []
    for item in body:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
        else:
            text = str(item or "").strip()
        if text:
            values.append(text)
    return "；".join(values)


# 只拆分明确的标题编号，不误删 4WD、3D、360° 等功能名中的数字。
_HEADING_NUMBER_RE = re.compile(
    r"^(\d+(?:[.．]\d+)+)(?:[.．])?(?:\s+(.+)|(?=[^\d\s.．])(.+))$"
)
_SINGLE_HEADING_NUMBER_RE = re.compile(r"^(\d+)[.．、]?\s+(.+)$")


def split_heading_number(value: str) -> tuple[str, str]:
    """返回显式编号和纯标题；未识别编号时保留原文。"""
    text = str(value or "").strip()
    match = _HEADING_NUMBER_RE.match(text)
    if match:
        return match.group(1).replace("．", "."), (match.group(2) or match.group(3)).strip()
    match = _SINGLE_HEADING_NUMBER_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return "", text


_DESCRIPTION_HEADINGS = {"功能描述", "功能详细定义", "功能需求", "整车功能定义"}
_PURPOSE_HEADINGS = {"目的", "功能目的"}
_NON_DESCRIPTION_HEADINGS = {
    "功能概述", "功能概要", "功能架构图", "功能初始架构",
    "功能分配", "外部接口", "内部接口", "接口", "输入", "输出",
    "功能时序图", "功能时序图设计", "使能条件", "触发条件", "运行条件",
}

_SENTENCE_ENDINGS = tuple("。！？；.!?;")


def _direct_body_text(node: dict) -> str:
    """读取一个标题节点的直接正文，不读取任何子标题正文。"""
    parts = []
    for item in node.get("body", []) or []:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
        else:
            text = str(item or "").strip()
        if text:
            parts.append(text)
    if not parts:
        return ""
    # 目的或功能描述偶尔跨多个普通段落；按句子直接连接，避免输出人为分号。
    result = ""
    for part in parts:
        if result and not result.endswith(_SENTENCE_ENDINGS):
            result += "。"
        result += part
    return result.strip()


def _join_description_parts(parts: list[str]) -> str:
    """按“功能描述 + 目的”顺序连接，避免重复或缺失句末标点。"""
    result = ""
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        if result and not result.endswith(_SENTENCE_ENDINGS):
            result += "。"
        result += text
    return result[:1200]


def build_function_description_map(document_context: dict, *, max_chars: int = 1200) -> dict[str, str]:
    """为功能节点提取“一句话功能描述 + 目的”。

    只读取 ``目的``/``功能描述``标题下的直接正文；这些标题下面的
    ``IDCU需求``、``IBS需求``、使能条件、接口等下级内容不是一句话功能描述，
    不再递归纳入。若某个功能节点没有直接功能描述，则仅保留目的；反之亦然。
    没有这两个专门子标题时，保留节点自身的直接正文，并允许父级功能汇总
    直接正文子功能。该函数服务于功能描述字段，通用上下文摘要仍由
    ``build_node_summary_map`` 独立计算。
    """
    nodes = document_context.get("nodes", []) or []
    by_id = {node["node_id"]: node for node in nodes if node.get("node_id")}
    children: dict[str | None, list[str]] = {}
    for node_id, node in by_id.items():
        children.setdefault(node.get("parent_id"), []).append(node_id)
    cache: dict[str, str] = {}

    def title_of(node: dict) -> str:
        return split_heading_number(node.get("heading_text", ""))[1].rstrip("：:").strip()

    def visit(node_id: str) -> str:
        if node_id in cache:
            return cache[node_id]
        node = by_id[node_id]
        title = title_of(node)
        if title in _DESCRIPTION_HEADINGS or title in _PURPOSE_HEADINGS:
            # 关键边界：即使该标题下面有 IDCU/IBS 等子标题，也只取本节点直接正文。
            value = _direct_body_text(node)
            cache[node_id] = value[:max_chars]
            return cache[node_id]
        if title in _NON_DESCRIPTION_HEADINGS:
            cache[node_id] = ""
            return ""

        direct_description = ""
        direct_purpose = ""
        child_function_values: list[str] = []
        for child_id in children.get(node_id, []):
            child = by_id[child_id]
            child_title = title_of(child)
            if child_title in _DESCRIPTION_HEADINGS:
                value = _direct_body_text(child)
                if value:
                    direct_description = _join_description_parts([direct_description, value])
            elif child_title in _PURPOSE_HEADINGS:
                value = _direct_body_text(child)
                if value:
                    direct_purpose = _join_description_parts([direct_purpose, value])
            elif child_title not in _NON_DESCRIPTION_HEADINGS:
                value = visit(child_id)
                if value:
                    child_function_values.append(value)

        if direct_description or direct_purpose:
            value = _join_description_parts([direct_description, direct_purpose])
        else:
            own = _direct_body_text(node)
            value = own or _join_description_parts(child_function_values)
        cache[node_id] = value[:max_chars]
        return cache[node_id]

    return {node_id: visit(node_id) for node_id in by_id}


def build_node_summary_map(document_context: dict, *, max_chars: int = 1200) -> dict[str, str]:
    """一次性为所有节点计算受限摘要，避免逐节点重复扫描整棵树。"""
    nodes = document_context.get("nodes", []) or []
    by_id = {node.get("node_id"): node for node in nodes if node.get("node_id")}
    children: dict[str, list[str]] = {}
    roots = []
    for node_id, node in by_id.items():
        parent_id = node.get("parent_id")
        if parent_id in by_id:
            children.setdefault(parent_id, []).append(node_id)
        else:
            roots.append(node_id)
    priority = {
        "function_description": 0,
        "operating_conditions": 1,
        "interfaces_and_allocation": 2,
        "control_target": 3,
        "failure_and_degradation": 4,
    }
    subtree: dict[str, list[tuple[int, int, str]]] = {}

    def visit(node_id: str) -> list[tuple[int, int, str]]:
        if node_id in subtree:
            return subtree[node_id]
        node = by_id[node_id]
        entries = []
        own = node_body_text(node)
        if own:
            entries.append((
                priority.get(node.get("section_type"), 10),
                int(node.get("source_index", 0)),
                own,
            ))
        for child_id in children.get(node_id, []):
            entries.extend(visit(child_id))
        entries.sort()
        # 每个父节点最终只会使用最优两块证据，限制缓存规模。
        subtree[node_id] = entries[:2]
        return subtree[node_id]

    for root_id in roots:
        visit(root_id)
    result = {}
    for node_id, node in by_id.items():
        own = node_body_text(node)
        if own:
            result[node_id] = own[:max_chars]
        else:
            result[node_id] = "；".join(entry[2] for entry in subtree.get(node_id, [])[:2])[:max_chars]
    return result


def node_summary(document_context: dict, node_id: str, *, max_chars: int = 1200) -> str:
    """返回节点正文；节点无正文时优先取关键子章节正文。"""
    nodes = document_context.get("nodes", []) or []
    by_id = {node.get("node_id"): node for node in nodes if node.get("node_id")}
    node = by_id.get(node_id)
    if not node:
        return ""
    own = node_body_text(node)
    if own:
        return own[:max_chars]

    children: dict[str, list[dict]] = {}
    for candidate in nodes:
        parent_id = candidate.get("parent_id")
        if parent_id:
            children.setdefault(parent_id, []).append(candidate)

    priority = {
        "function_description": 0,
        "operating_conditions": 1,
        "interfaces_and_allocation": 2,
        "control_target": 3,
        "failure_and_degradation": 4,
    }
    found: list[tuple[int, int, str]] = []
    queue = list(children.get(node_id, []))
    while queue:
        candidate = queue.pop(0)
        text = node_body_text(candidate)
        if text:
            found.append((
                priority.get(candidate.get("section_type"), 10),
                int(candidate.get("source_index", 0)),
                text,
            ))
        queue.extend(children.get(candidate.get("node_id"), []))
    found.sort()
    return "；".join(item[2] for item in found[:2])[:max_chars]


def build_parse_quality(
    related_items: list,
    document_context: dict,
    *,
    unmatched: list[dict] | None = None,
    duplicate_warnings: list[dict] | None = None,
    source_document: dict | None = None,
    parser_name: str = "standard_docx",
) -> dict:
    """构造两级质量门：名称预填就绪、分析上下文就绪。"""
    unmatched = unmatched or []
    duplicate_warnings = duplicate_warnings or []
    nodes = document_context.get("nodes", []) or []
    body_nodes = sum(1 for node in nodes if node_body_text(node))
    total_subs = sum(len(getattr(item, "sub_functions", []) or []) for item in related_items)
    func_names = [str(getattr(item, "func_name", "") or "").strip() for item in related_items]
    sub_names = [
        str(getattr(sub, "name", "") or "").strip()
        for item in related_items
        for sub in (getattr(item, "sub_functions", []) or [])
    ]
    true_unmatched = sum(1 for item in unmatched if item.get("status") == "unmatched")
    low_confidence = sum(1 for item in unmatched if item.get("status") == "low_confidence")
    matched = max(total_subs - true_unmatched, 0)
    match_ratio = matched / total_subs if total_subs else 0.0

    issues: list[dict] = []
    if not related_items:
        issues.append({"code": "FUNCTION_LIST_MISSING", "severity": "blocking"})
    if any(not value for value in func_names):
        issues.append({"code": "FUNCTION_NAME_MISSING", "severity": "blocking"})
    if any(not value for value in sub_names):
        issues.append({"code": "SUB_FUNCTION_NAME_MISSING", "severity": "blocking"})
    if not nodes:
        issues.append({"code": "HEADING_TREE_MISSING", "severity": "analysis"})
    elif body_nodes == 0:
        issues.append({"code": "CONTEXT_BODY_MISSING", "severity": "analysis"})
    if total_subs and match_ratio < 0.65:
        issues.append({
            "code": "CHAPTER_MATCH_RATIO_LOW",
            "severity": "analysis",
            "value": round(match_ratio, 4),
            "threshold": 0.65,
        })
    if true_unmatched:
        issues.append({
            "code": "UNMATCHED_SECTIONS_PRESENT",
            "severity": "analysis",
            "count": true_unmatched,
        })
    if low_confidence:
        issues.append({
            "code": "LOW_CONFIDENCE_MATCHES_PRESENT",
            "severity": "analysis",
            "count": low_confidence,
        })

    prefill_ready = bool(related_items and func_names and all(func_names) and all(sub_names))
    analysis_context_ready = bool(
        prefill_ready
        and nodes
        and body_nodes
        and (not total_subs or match_ratio >= 0.65)
        and true_unmatched == 0
        and low_confidence == 0
    )
    return {
        "schema_version": PARSE_QUALITY_SCHEMA,
        "parser": parser_name,
        "prefill_ready": prefill_ready,
        "analysis_context_ready": analysis_context_ready,
        "requires_agent_document_parse": not analysis_context_ready,
        "metrics": {
            "related_item_count": len(related_items),
            "sub_function_count": total_subs,
            "heading_node_count": len(nodes),
            "body_node_count": body_nodes,
            "chapter_matched_count": matched,
            "chapter_unmatched_count": true_unmatched,
            "chapter_low_confidence_count": low_confidence,
            "chapter_match_ratio": round(match_ratio, 4),
            "duplicate_warning_count": len(duplicate_warnings),
        },
        "issues": issues,
        "source_document": deepcopy(source_document or {}),
    }


def _context_slice(intermediate: dict, unresolved: list[dict]) -> dict:
    """为 Agent 构造有边界的目录和正文切片，而不是发送全文。"""
    context = intermediate.get("document_context", {}) or {}
    nodes = context.get("nodes", []) or []
    terms = {
        normalize_text(value)
        for row in unresolved
        for value in (row.get("func_name"), row.get("sub_name"), row.get("scenario"))
        if normalize_text(value)
    }
    selected_ids: set[str] = set()
    by_id = {node.get("node_id"): node for node in nodes if node.get("node_id")}
    for node in nodes:
        heading = normalize_text(node.get("heading_text"))
        body = normalize_text(node_body_text(node))
        if any(term in heading or heading in term or term in body for term in terms if term):
            current = node
            while current:
                node_id = current.get("node_id")
                if node_id:
                    selected_ids.add(node_id)
                current = by_id.get(current.get("parent_id"))
    output = []
    char_count = 0
    for node in nodes:
        keep_body = (
            node.get("node_id") in selected_ids
            or node.get("section_type") != "other"
        )
        if node.get("level", 99) > 2 and node.get("node_id") not in selected_ids and not keep_body:
            continue
        copied = {
            key: deepcopy(node.get(key))
            for key in (
                "node_id", "parent_id", "source_index", "level", "label",
                "heading_text", "section_type", "evidence_ref",
            )
            if key in node
        }
        if keep_body:
            text = node_body_text(node)
            remain = MAX_AGENT_CONTEXT_CHARS - char_count
            if text and remain > 0:
                copied["body_summary"] = text[:remain]
                char_count += len(copied["body_summary"])
        output.append(copied)
        if len(output) >= MAX_AGENT_CONTEXT_NODES or char_count >= MAX_AGENT_CONTEXT_CHARS:
            break
    return {
        "schema_version": context.get("schema_version", DOCUMENT_CONTEXT_SCHEMA),
        "nodes": output,
        "truncated": len(output) < len(nodes),
        "body_char_count": char_count,
    }


def build_document_parse_request(intermediate: dict) -> dict:
    unresolved = [
        deepcopy(item)
        for item in intermediate.get("unmatched_chapters", []) or []
        if item.get("status") in {"unmatched", "low_confidence"}
    ]
    source = deepcopy(intermediate.get("source_document", {}) or {})
    stable_payload = json.dumps(
        {
            "sha256": source.get("sha256", ""),
            "doc_name": intermediate.get("doc_name", ""),
            "unresolved": [
                (row.get("func_id"), row.get("sub_name"), row.get("status"))
                for row in unresolved
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    request_id = "docreq_" + sha256(stable_payload.encode("utf-8")).hexdigest()[:16]
    fast_items = []
    for item in intermediate.get("related_items", []) or []:
        fast_items.append({
            "func_id": item.get("func_id"),
            "func_name": item.get("func_name"),
            "domain": item.get("domain"),
            "sub_functions": [
                {
                    "feature_list_id": sub.get("feature_list_id"),
                    "name": sub.get("name"),
                    "scenario": sub.get("scenario", ""),
                    "components": deepcopy(sub.get("components", [])),
                }
                for sub in item.get("sub_functions", []) or []
            ],
        })
    mode = "supplement_context" if fast_items else "full_structure"
    return {
        "schema_version": DOCUMENT_PARSE_REQUEST_SCHEMA,
        "request_id": request_id,
        "mode": mode,
        "source_document": source,
        "parse_quality": deepcopy(intermediate.get("parse_quality", {}) or {}),
        "required_output_schema": AGENT_DOCUMENT_STRUCTURE_SCHEMA,
        "constraints": {
            "must_preserve_source_ids": True,
            "must_include_evidence_refs": True,
            "forbidden_runtime_fields": [
                "failure_id", "hazard_id", "safety_goal_id", "asil", "id_range",
            ],
            "may_not_modify_s2_s3_s4": True,
        },
        "related_items_fast_parse": fast_items,
        "unresolved_items": unresolved,
        "document_context_slice": _context_slice(intermediate, unresolved),
    }

