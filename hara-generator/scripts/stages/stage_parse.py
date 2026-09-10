# stages/stage_parse.py
# 阶段一编排：文档接入 → 功能清单快解析 → 章节证据 → S1规则 → intermediate

from __future__ import annotations

import json
from pathlib import Path

from utils.chapter_matcher import apply_exact_chapter_match
from utils.data_models import DOMAIN_NAMES, canonicalize_domain
from utils.document_context import (
    DOCUMENT_CONTEXT_SCHEMA,
    build_document_parse_request,
    build_parse_quality,
)
from utils.document_ingest import DocumentConversionError, prepare_document
from utils.docx_parser import (
    FunctionTableNotFoundError,
    inspect_docx_context,
    parse_docx_with_context,
)
from utils.domain_packs import compile_domain_context
from utils.domain_pack_generation import apply_effective_case_routes
from utils.s1_rules import apply_s1_rules, to_intermediate_json
from utils.s2_rules import compute_s2_for_related_items


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def _request_output_path(args, output_path: Path) -> Path:
    explicit = getattr(args, "document_request_output", None)
    if explicit:
        return Path(explicit)
    return output_path.with_name(f"{output_path.stem}_document_parse_request.json")


def _empty_intermediate(
    *,
    doc_name: str,
    domain: str,
    document_context: dict,
    source_document: dict,
    parser_name: str,
    diagnostics: list[dict] | None = None,
) -> dict:
    quality = build_parse_quality(
        [],
        document_context,
        source_document=source_document,
        parser_name=parser_name,
    )
    if diagnostics:
        quality["issues"].extend(diagnostics)
    return {
        "doc_name": doc_name,
        "domain": domain,
        "domain_name": DOMAIN_NAMES.get(domain, domain),
        "total_related_items": 0,
        "total_sub_functions": 0,
        "s1_rules_applied": False,
        "related_items": [],
        "chapters": [],
        "unmatched_chapters": [],
        "document_context": document_context,
        "source_document": source_document,
        "parse_quality": quality,
        "agent_work_required": {
            "s1_pending_items": 0,
            "s1_pending_sub_count": 0,
            "s1_pending_feature_ids": [],
            "s1_locked_feature_ids": [],
            "s1_locked_decisions": [],
            "chapter_unmatched_count": 0,
            "document_parse_required": True,
        },
        "domain_contexts": {},
    }


def _compile_domain_contexts(intermediate: dict) -> None:
    items_by_domain: dict[str, list[dict]] = {}
    for item_dict in intermediate.get("related_items", []):
        domain = item_dict.get("domain", "")
        if domain:
            items_by_domain.setdefault(domain, []).append(item_dict)

    domain_contexts = {}
    for domain, domain_items in items_by_domain.items():
        try:
            context = compile_domain_context(domain, domain_items)
        except ValueError as error:
            print(f"[Domain Pack] {domain} 上下文未生成: {error}")
            continue
        domain_contexts[context["domain"]] = context
        if context["domain"] == "PT":
            print("[Domain Pack] PT: 已完成语义编译；PT功能级案例路由将在当前中间结果中生效。")
        else:
            print(
                f"[Domain Pack] {context['domain']}: {context['resolution_status']}, "
                f"未解析={context['unresolved_feature_count']}, "
                f"歧义={context['ambiguous_feature_count']}"
            )
        by_func = {item["func_id"]: item for item in context["related_items"]}
        for item_dict in domain_items:
            if item_dict.get("func_id") in by_func:
                item_dict["domain_context"] = by_func[item_dict["func_id"]]
    intermediate["domain_contexts"] = domain_contexts


def _emit_document_request(args, output_path: Path, intermediate: dict) -> Path | None:
    quality = intermediate.get("parse_quality", {}) or {}
    if not quality.get("requires_agent_document_parse"):
        return None
    request = build_document_parse_request(intermediate)
    request_path = _request_output_path(args, output_path)
    _write_json(request_path, request)
    intermediate["document_parse_request"] = {
        "request_id": request["request_id"],
        "mode": request["mode"],
        "path": str(request_path.resolve()),
        "required_output_schema": request["required_output_schema"],
    }
    intermediate["agent_work_required"]["document_parse_request_id"] = request["request_id"]
    return request_path


def run(args):
    input_path = Path(args.input).resolve()
    output_path = Path(args.output) if args.output else Path("intermediate.json")
    if not input_path.exists():
        print(f"错误: 文件不存在: {input_path}")
        return

    explicit_domain = getattr(args, "domain", "") or ""
    try:
        with prepare_document(input_path) as prepared:
            try:
                doc_name, related_items, chapters, document_context, parser_name = parse_docx_with_context(
                    prepared.parse_path,
                    domain=explicit_domain,
                    source_path=input_path,
                )
            except FunctionTableNotFoundError as error:
                doc_name, document_context, table_headers = inspect_docx_context(
                    prepared.parse_path,
                    source_path=input_path,
                )
                document_context["table_evidence"] = table_headers
                domain = canonicalize_domain(explicit_domain)
                intermediate = _empty_intermediate(
                    doc_name=doc_name,
                    domain=domain,
                    document_context=document_context,
                    source_document=prepared.source_info,
                    parser_name="agent_full_structure_required",
                    diagnostics=[{
                        "code": "FUNCTION_TABLE_SCHEMA_UNRECOGNIZED",
                        "severity": "blocking",
                        "message": str(error),
                        "table_headers": error.table_headers,
                    }],
                )
                request_path = _emit_document_request(args, output_path, intermediate)
                _write_json(output_path, intermediate)
                print(f"[Document] 功能清单格式未识别，已保留目录证据并生成 Agent 请求。")
                print(f"已输出: {output_path.resolve()}")
                if request_path:
                    print(f"Agent 文档解析请求: {request_path.resolve()}")
                return
    except DocumentConversionError as error:
        domain = canonicalize_domain(explicit_domain)
        document_context = {
            "schema_version": DOCUMENT_CONTEXT_SCHEMA,
            "nodes": [],
            "metrics": {"heading_node_count": 0, "body_node_count": 0, "body_char_count": 0},
            "table_evidence": [],
            "parser": "conversion_failed",
        }
        intermediate = _empty_intermediate(
            # 即使旧 DOC 转换失败，也保留输入文件的完整原始文件名。
            doc_name=input_path.name,
            domain=domain,
            document_context=document_context,
            source_document=error.source_info,
            parser_name="conversion_failed",
            diagnostics=[{
                "code": "DOCUMENT_CONVERSION_FAILED",
                "severity": "blocking",
                "message": str(error),
                "attempts": error.attempts,
            }],
        )
        request_path = _emit_document_request(args, output_path, intermediate)
        _write_json(output_path, intermediate)
        print(f"[Document] {error}")
        print(f"已输出 Agent 回退骨架: {output_path.resolve()}")
        if request_path:
            print(f"Agent 文档解析请求: {request_path.resolve()}")
        return

    print(f"文档名: {doc_name}")
    print(f"文档解析器: {parser_name}")
    conversion = prepared.source_info.get("conversion", {})
    if conversion.get("required"):
        print(
            f"旧 DOC 转换: {conversion.get('status')} / {conversion.get('converter')} / "
            f"{conversion.get('elapsed_ms', 0)} ms"
        )
    print(f"相关项: {len(related_items)} 个")
    total_subs = sum(len(item.sub_functions) for item in related_items)
    print(f"子功能总数: {total_subs}")
    print(f"legacy 第3章节点: {len(chapters)} 个")
    print(f"document_context 节点: {document_context.get('metrics', {}).get('heading_node_count', 0)} 个")

    unmatched, duplicate_warnings = apply_exact_chapter_match(
        related_items,
        chapters,
        document_context,
    )
    low_conf = [item for item in unmatched if item.get("status") == "low_confidence"]
    truly_unmatched = [item for item in unmatched if item.get("status") == "unmatched"]
    matched_count = total_subs - len(truly_unmatched)
    print(
        f"章节匹配: {matched_count} 成功"
        + (f"（其中低置信 {len(low_conf)} 项需复核）" if low_conf else "")
        + f", {len(truly_unmatched)} 未自动匹配"
    )

    method_count: dict[str, int] = {}
    for item in related_items:
        for sub in item.sub_functions:
            method = getattr(sub, "_match_method", "none")
            method_count[method] = method_count.get(method, 0) + 1
    print("匹配方式分布: " + ", ".join(f"{key}={value}" for key, value in method_count.items()))

    if low_conf:
        print(f"\n[!] 低置信匹配（{len(low_conf)} 项，Agent 请复核）:")
        for item in low_conf:
            print(
                f"  [LOW] {item['desc']} → {item['auto_matched_chapter']} "
                f"(置信度 {item['confidence']})"
            )
    if duplicate_warnings:
        print(f"\n[!] 一章多 Feature 弱匹配复核（{len(duplicate_warnings)} 项）:")
        for warning in duplicate_warnings:
            print(f"  [WARN] {warning['message']}")

    s1_result = apply_s1_rules(related_items)
    total_pending = sum(len(value.get("pending_subs", [])) for value in s1_result.values())
    total_excluded = sum(1 for value in s1_result.values() if value.get("excluded"))
    total_sub_locked = sum(len(value.get("decisions", {})) for value in s1_result.values())
    total_sub_excluded = sum(
        sum(decision.get("is_hara") is False for decision in value.get("decisions", {}).values())
        for value in s1_result.values()
    )
    print(
        f"S1 规则: {total_excluded} 个相关项级排除, {total_sub_locked} 个子功能已锁定 "
        f"（其中不做HARA {total_sub_excluded} 个）, {total_pending} 个待 Agent 判断"
    )

    related_items_dicts = []
    for item in related_items:
        related_items_dicts.append({
            "func_id": item.func_id,
            "func_name": item.func_name,
            "domain": item.domain,
            "sub_functions": [{
                "feature_list_id": sub.feature_list_id,
                "name": sub.name,
                "description": sub.description,
                "scenario": sub.scenario,
                "components": sub.components,
            } for sub in item.sub_functions],
        })
    s2_suggestions = compute_s2_for_related_items(related_items_dicts)
    s2_matched = len(s2_suggestions)
    print(f"S2 参考案例: {s2_matched}/{len(related_items)} 个功能找到参考案例")

    parse_quality = build_parse_quality(
        related_items,
        document_context,
        unmatched=unmatched,
        duplicate_warnings=duplicate_warnings,
        source_document=prepared.source_info,
        parser_name=parser_name,
    )
    intermediate = to_intermediate_json(
        doc_name,
        related_items,
        chapters,
        s1_result,
        unmatched,
        document_context=document_context,
        parse_quality=parse_quality,
        source_document=prepared.source_info,
        duplicate_warnings=duplicate_warnings,
    )
    for item_dict in intermediate.get("related_items", []):
        func_id = item_dict.get("func_id", "")
        if func_id in s2_suggestions:
            item_dict["s2_suggestions"] = s2_suggestions[func_id]
    _compile_domain_contexts(intermediate)
    route_metrics = apply_effective_case_routes(intermediate)
    # When every HARA-positive PT function is covered by a unique function-level
    # case set, unresolved document chapters remain diagnostic only.  They no
    # longer create an Agent task for S1-S5, while unmatched_chapters is retained
    # for auditability.
    if route_metrics["eligible_count"] and route_metrics["routed_count"] == route_metrics["eligible_count"]:
        quality = intermediate.setdefault("parse_quality", {})
        quality["raw_analysis_context_ready"] = quality.get("analysis_context_ready", False)
        quality["raw_requires_agent_document_parse"] = quality.get("requires_agent_document_parse", False)
        quality["analysis_context_ready"] = True
        quality["requires_agent_document_parse"] = False
        quality["analysis_context_source"] = "function_case_set_exact"
        quality["chapter_evidence_complete"] = not bool(intermediate.get("unmatched_chapters"))
        quality["chapter_unmatched_non_blocking"] = bool(intermediate.get("unmatched_chapters"))
        quality["document_parse_bypass_reason"] = (
            "已迁移域的 HARA 正向整车功能均由唯一案例集精确锁定；未匹配章节保留为审计诊断，不阻断 S1-S5。"
        )
        intermediate.setdefault("agent_work_required", {})["document_parse_required"] = False
        intermediate["agent_work_required"]["document_parse_bypass"] = True
        intermediate["agent_work_required"]["document_parse_bypass_reason"] = quality["document_parse_bypass_reason"]

    quality = intermediate.get("parse_quality", {})
    print(
        f"文档质量门: prefill_ready={quality.get('prefill_ready')}, "
        f"analysis_context_ready={quality.get('analysis_context_ready')}"
        + (f"（来源: {quality.get('analysis_context_source')}）" if quality.get('analysis_context_source') else "")
    )
    if quality.get("chapter_unmatched_non_blocking"):
        print(
            f"章节未自动匹配 {quality.get('metrics', {}).get('chapter_unmatched_count', 0)} 项；"
            "域内案例集已精确覆盖 HARA 路径，未匹配章节仅保留为审计诊断，不触发 Agent 任务。"
        )

    request_path = _emit_document_request(args, output_path, intermediate)
    _write_json(output_path, intermediate)
    print(f"已输出: {output_path.resolve()}")
    if request_path:
        print(f"Agent 文档解析请求: {request_path.resolve()}")

    print(f"\n{'=' * 60}")
    print("Agent 下一步:")
    print("  1. S1 案例预填首先依据整车功能名称；不等待全部子功能章节完成匹配。")
    if request_path:
        print("  2. 先处理 document_parse_request，输出 agent_document_structure_v1 并执行 document compose。")
        print("  3. 使用 compose 后的 intermediate 进入 S1 merge；不得直接修改 S2/S3/S4。")
    else:
        print("  2. 读 intermediate 中当前域 domain_context，只处理未锁定的 S1 项。")
        print("  3. 输出 agent_s1.json 后执行 merge。")
    print(f"  当前 S1 pending: {total_pending}/{total_subs} 个子功能。")
    print(f"{'=' * 60}")
