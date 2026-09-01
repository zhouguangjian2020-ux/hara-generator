# stages/stage_document.py
# Agent 文档结构 validate / compose

from __future__ import annotations

import json
from pathlib import Path

from stages.stage_parse import _compile_domain_contexts
from utils.document_agent import (
    compose_agent_document_structure,
    validate_agent_document_structure,
)
from utils.s2_rules import compute_s2_for_related_items
from utils.domain_pack_generation import apply_effective_pt_case_routes


def _load(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def run(args):
    subcommand = getattr(args, "document_subcommand", None)
    intermediate = _load(args.intermediate)
    agent = _load(args.agent_document)
    request = _load(args.request) if getattr(args, "request", None) else None
    if request is None and intermediate.get("document_parse_request"):
        request = {
            "request_id": intermediate["document_parse_request"].get("request_id"),
            "mode": intermediate["document_parse_request"].get("mode"),
        }

    validate_agent_document_structure(
        agent,
        intermediate=intermediate,
        request=request,
    )
    if subcommand == "validate":
        print("Agent 文档结构校验通过。")
        return

    result = compose_agent_document_structure(
        intermediate,
        agent,
        request=request,
    )
    if agent.get("mode") == "full_structure":
        s2_suggestions = compute_s2_for_related_items(result.get("related_items", []))
        for item in result.get("related_items", []):
            func_id = item.get("func_id", "")
            if func_id in s2_suggestions:
                item["s2_suggestions"] = s2_suggestions[func_id]
        _compile_domain_contexts(result)
    apply_effective_pt_case_routes(result)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    resolution = result.get("document_parse_resolution", {})
    quality = result.get("parse_quality", {})
    print(
        f"document compose: {resolution.get('status', 'completed')}, "
        f"prefill_ready={quality.get('prefill_ready')}, "
        f"analysis_context_ready={quality.get('analysis_context_ready')}"
    )
    print(f"已输出: {output_path.resolve()}")
