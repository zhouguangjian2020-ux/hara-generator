"""Agent 文档结构输出的严格校验与合并。

Agent 只能补充文档结构和章节证据，不得写入 S2/S3/S4 的运行期字段。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from utils.data_models import RelatedItem, SubFunction, canonicalize_domain
from utils.document_context import (
    AGENT_DOCUMENT_STRUCTURE_SCHEMA,
    DOCUMENT_CONTEXT_SCHEMA,
    build_parse_quality,
    node_body_text,
)
from utils.s1_rules import apply_s1_rules, to_intermediate_json


FORBIDDEN_RUNTIME_FIELDS = {
    "failure_id", "hazard_id", "vehicle_hazard_id", "safety_goal_id",
    "asil", "id_range", "associated_hara",
}


class AgentDocumentStructureError(ValueError):
    pass


def _walk_forbidden(value: Any, path: str = "$") -> list[str]:
    errors = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_RUNTIME_FIELDS:
                errors.append(f"{child_path}: Agent 文档解析禁止提交运行期字段 {key}")
            errors.extend(_walk_forbidden(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_walk_forbidden(child, f"{path}[{index}]"))
    return errors


def _base_feature_map(intermediate: dict) -> dict[tuple[str, str], dict]:
    result = {}
    for item in intermediate.get("related_items", []) or []:
        for sub in item.get("sub_functions", []) or []:
            result[(str(item.get("func_id", "")), str(sub.get("feature_list_id", "")))] = sub
    return result


def validate_agent_document_structure(
    agent: dict,
    *,
    intermediate: dict | None = None,
    request: dict | None = None,
) -> None:
    errors = _walk_forbidden(agent)
    if not isinstance(agent, dict):
        raise AgentDocumentStructureError("Agent 文档结构必须是 JSON object")
    if agent.get("schema_version") != AGENT_DOCUMENT_STRUCTURE_SCHEMA:
        errors.append(
            f"schema_version 必须为 {AGENT_DOCUMENT_STRUCTURE_SCHEMA}"
        )
    if request and agent.get("request_id") != request.get("request_id"):
        errors.append("request_id 与 document_parse_request 不一致")
    mode = agent.get("mode") or (request or {}).get("mode")
    if mode not in {"supplement_context", "full_structure"}:
        errors.append("mode 必须为 supplement_context 或 full_structure")

    nodes = (agent.get("document_context") or {}).get("nodes", []) or []
    seen_nodes = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"document_context.nodes[{index}] 必须是 object")
            continue
        node_id = str(node.get("node_id", "")).strip()
        if not node_id:
            errors.append(f"document_context.nodes[{index}].node_id 不能为空")
        elif node_id in seen_nodes:
            errors.append(f"document_context.nodes[{index}].node_id 重复: {node_id}")
        seen_nodes.add(node_id)
        if not str(node.get("heading_text", "")).strip():
            errors.append(f"document_context.nodes[{index}].heading_text 不能为空")
        evidence = node.get("evidence_refs") or ([node.get("evidence_ref")] if node.get("evidence_ref") else [])
        if not any(str(value or "").strip() for value in evidence):
            errors.append(f"document_context.nodes[{index}] 必须提供 evidence_refs")

    if mode == "supplement_context":
        if not intermediate:
            errors.append("supplement_context 必须提供原 intermediate")
        base = _base_feature_map(intermediate or {})
        for index, assignment in enumerate(agent.get("section_assignments", []) or []):
            key = (
                str(assignment.get("func_id", "")),
                str(assignment.get("feature_list_id", "")),
            )
            if key not in base:
                errors.append(f"section_assignments[{index}] 引用了不存在的 Feature: {key}")
            evidence = assignment.get("evidence_refs", []) or []
            if not any(str(value or "").strip() for value in evidence):
                errors.append(f"section_assignments[{index}] 必须提供 evidence_refs")
            if not assignment.get("node_id") and not assignment.get("chapter_label"):
                errors.append(f"section_assignments[{index}] 必须提供 node_id 或 chapter_label")

        # Agent 可回传 related_items 做追溯，但不得更改来源 ID/名称。
        if agent.get("related_items"):
            base_items = {
                str(item.get("func_id", "")): item
                for item in (intermediate or {}).get("related_items", []) or []
            }
            for index, item in enumerate(agent.get("related_items", [])):
                func_id = str(item.get("func_id", ""))
                if func_id not in base_items:
                    errors.append(f"related_items[{index}].func_id 不在原 intermediate: {func_id}")
                    continue
                if item.get("func_name") != base_items[func_id].get("func_name"):
                    errors.append(f"related_items[{index}] 禁止修改 func_name")
    else:
        items = agent.get("related_items", []) or []
        if not items:
            errors.append("full_structure 必须提交 related_items")
        for item_index, item in enumerate(items):
            if not str(item.get("func_name", "")).strip():
                errors.append(f"related_items[{item_index}].func_name 不能为空")
            evidence = item.get("evidence_refs", []) or []
            if not evidence:
                errors.append(f"related_items[{item_index}] 必须提供 evidence_refs")
            subs = item.get("sub_functions", []) or []
            if not subs:
                errors.append(f"related_items[{item_index}].sub_functions 不能为空")
            for sub_index, sub in enumerate(subs):
                if not str(sub.get("name", "")).strip():
                    errors.append(
                        f"related_items[{item_index}].sub_functions[{sub_index}].name 不能为空"
                    )
                if not (sub.get("evidence_refs") or []):
                    errors.append(
                        f"related_items[{item_index}].sub_functions[{sub_index}] 必须提供 evidence_refs"
                    )

    if errors:
        raise AgentDocumentStructureError("Agent 文档结构校验失败:\n- " + "\n- ".join(errors))


def _normalize_agent_node(node: dict) -> dict:
    result = deepcopy(node)
    if "body" not in result and result.get("body_summary"):
        result["body"] = [{
            "style": "Agent Extracted",
            "text": str(result.pop("body_summary")),
            "evidence_ref": (result.get("evidence_refs") or [None])[0],
        }]
    if not result.get("evidence_ref") and result.get("evidence_refs"):
        result["evidence_ref"] = result["evidence_refs"][0]
    return result


def _find_item_sub(intermediate: dict, func_id: str, feature_id: str) -> tuple[dict, dict] | None:
    for item in intermediate.get("related_items", []) or []:
        if str(item.get("func_id", "")) != func_id:
            continue
        for sub in item.get("sub_functions", []) or []:
            if str(sub.get("feature_list_id", "")) == feature_id:
                return item, sub
    return None


def _refresh_quality(intermediate: dict, unresolved: list[dict]) -> None:
    quality = intermediate.setdefault("parse_quality", {})
    metrics = quality.setdefault("metrics", {})
    total = int(intermediate.get("total_sub_functions", 0) or 0)
    true_unmatched = sum(row.get("status") == "unmatched" for row in unresolved)
    low = sum(row.get("status") == "low_confidence" for row in unresolved)
    metrics["chapter_unmatched_count"] = true_unmatched
    metrics["chapter_low_confidence_count"] = low
    metrics["chapter_matched_count"] = max(total - true_unmatched, 0)
    metrics["chapter_match_ratio"] = round((total - true_unmatched) / total, 4) if total else 0.0
    context = intermediate.get("document_context", {}) or {}
    body_nodes = sum(1 for node in context.get("nodes", []) or [] if node_body_text(node))
    metrics["heading_node_count"] = len(context.get("nodes", []) or [])
    metrics["body_node_count"] = body_nodes
    ready = bool(quality.get("prefill_ready") and not true_unmatched and not low and body_nodes)
    quality["analysis_context_ready"] = ready
    quality["requires_agent_document_parse"] = not ready
    quality["parser"] = "agent_composed"
    quality["issues"] = [
        issue for issue in quality.get("issues", [])
        if issue.get("code") not in {
            "UNMATCHED_SECTIONS_PRESENT", "LOW_CONFIDENCE_MATCHES_PRESENT",
            "CHAPTER_MATCH_RATIO_LOW", "CONTEXT_BODY_MISSING",
        }
    ]
    if true_unmatched:
        quality["issues"].append({"code": "UNMATCHED_SECTIONS_PRESENT", "severity": "analysis", "count": true_unmatched})
    if low:
        quality["issues"].append({"code": "LOW_CONFIDENCE_MATCHES_PRESENT", "severity": "analysis", "count": low})


def _merge_supplement(intermediate: dict, agent: dict) -> dict:
    result = deepcopy(intermediate)
    context = result.setdefault("document_context", {"schema_version": DOCUMENT_CONTEXT_SCHEMA, "nodes": []})
    base_nodes = context.setdefault("nodes", [])
    by_id = {node.get("node_id"): node for node in base_nodes if node.get("node_id")}
    for raw_node in (agent.get("document_context") or {}).get("nodes", []) or []:
        node = _normalize_agent_node(raw_node)
        if node.get("node_id") in by_id:
            # Agent 只能为现有节点补正文/证据，不能改标题和父子关系。
            existing = by_id[node["node_id"]]
            for key in ("body", "evidence_ref", "evidence_refs", "section_type"):
                if node.get(key):
                    existing[key] = deepcopy(node[key])
        else:
            base_nodes.append(node)
            by_id[node.get("node_id")] = node

    resolved_keys = set()
    for assignment in agent.get("section_assignments", []) or []:
        func_id = str(assignment.get("func_id", ""))
        feature_id = str(assignment.get("feature_list_id", ""))
        found = _find_item_sub(result, func_id, feature_id)
        if not found:
            continue
        _item, sub = found
        node = by_id.get(assignment.get("node_id"))
        chapter = assignment.get("chapter_label") or (node or {}).get("label") or (node or {}).get("heading_text")
        description = str(assignment.get("description", "")).strip()
        if not description and node:
            description = node_body_text(node)
        if chapter:
            sub["chapter"] = chapter
        if description:
            sub["description"] = description
        evidence = list(sub.get("evidence_refs", []) or [])
        for value in assignment.get("evidence_refs", []) or []:
            if value not in evidence:
                evidence.append(value)
        sub["evidence_refs"] = evidence
        sub["document_match"] = {
            "method": "agent_confirmed",
            "reason": assignment.get("reason", ""),
            "node_id": assignment.get("node_id"),
            "evidence_refs": assignment.get("evidence_refs", []),
        }
        resolved_keys.add((func_id, feature_id))

    unresolved = []
    for row in result.get("unmatched_chapters", []) or []:
        key = (str(row.get("func_id", "")), str(row.get("feature_list_id", "")))
        if key not in resolved_keys:
            unresolved.append(row)
    unresolved.extend(deepcopy(agent.get("unresolved_items", []) or []))
    result["unmatched_chapters"] = unresolved
    result.setdefault("agent_work_required", {})["chapter_unmatched_count"] = len(unresolved)
    result["agent_work_required"]["document_parse_required"] = bool(unresolved)
    result["document_parse_resolution"] = {
        "request_id": agent.get("request_id"),
        "mode": "supplement_context",
        "status": "completed" if not unresolved else "partially_completed",
        "resolved_feature_count": len(resolved_keys),
        "remaining_unresolved_count": len(unresolved),
    }
    _refresh_quality(result, unresolved)
    return result


def _full_structure_items(agent: dict, base: dict) -> list[RelatedItem]:
    domain = canonicalize_domain(agent.get("domain") or base.get("domain") or "")
    items = []
    for item_index, item in enumerate(agent.get("related_items", []) or [], start=1):
        func_id = str(item.get("func_id", "")).strip() or f"{domain or 'DOC'}_func_{item_index:04d}"
        related = RelatedItem(
            func_id=func_id,
            func_name=str(item.get("func_name", "")).strip(),
            domain=domain,
        )
        for sub_index, sub in enumerate(item.get("sub_functions", []) or [], start=1):
            feature_id = str(sub.get("feature_list_id", "")).strip() or f"AUTO_FEAT_{item_index:04d}_{sub_index:03d}"
            related.sub_functions.append(SubFunction(
                feature_list_id=feature_id,
                name=str(sub.get("name", "")).strip(),
                description=str(sub.get("description", "")).strip(),
                chapter=str(sub.get("chapter", "")).strip(),
                scenario=str(sub.get("scenario", "")).strip(),
                components=deepcopy(sub.get("components", []) or []),
                source_table_index=sub.get("source_table_index"),
                source_row=sub.get("source_row"),
                evidence_refs=deepcopy(sub.get("evidence_refs", []) or []),
            ))
        items.append(related)
    return items


def _merge_full_structure(intermediate: dict, agent: dict) -> dict:
    related_items = _full_structure_items(agent, intermediate)
    context = deepcopy(agent.get("document_context") or {"schema_version": DOCUMENT_CONTEXT_SCHEMA, "nodes": []})
    context["nodes"] = [_normalize_agent_node(node) for node in context.get("nodes", []) or []]
    unresolved = deepcopy(agent.get("unresolved_items", []) or [])
    quality = build_parse_quality(
        related_items,
        context,
        unmatched=unresolved,
        source_document=intermediate.get("source_document", {}),
        parser_name="agent_full_structure",
    )
    s1_result = apply_s1_rules(related_items)
    result = to_intermediate_json(
        intermediate.get("doc_name", ""),
        related_items,
        [],
        s1_result,
        unresolved,
        document_context=context,
        parse_quality=quality,
        source_document=intermediate.get("source_document", {}),
        duplicate_warnings=[],
    )
    result["document_parse_resolution"] = {
        "request_id": agent.get("request_id"),
        "mode": "full_structure",
        "status": "completed" if quality.get("prefill_ready") else "partially_completed",
        "generated_func_ids": [
            item.func_id for item, raw in zip(related_items, agent.get("related_items", []))
            if not raw.get("func_id")
        ],
    }
    return result


def compose_agent_document_structure(
    intermediate: dict,
    agent: dict,
    *,
    request: dict | None = None,
) -> dict:
    validate_agent_document_structure(agent, intermediate=intermediate, request=request)
    mode = agent.get("mode") or (request or {}).get("mode")
    if mode == "supplement_context":
        return _merge_supplement(intermediate, agent)
    return _merge_full_structure(intermediate, agent)
