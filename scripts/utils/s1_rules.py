# utils/s1_rules.py
# S1 规则引擎：关键词匹配 + intermediate.json 构建
# 纯函数，无 CLI

from utils.data_models import DOMAIN_NAMES, canonicalize_domain
from utils.domain_packs import compile_domain_context
from utils.pt_subfunction_source import (
    RUNTIME_FILENAME,
    load_pt_subfunction_authority,
    match_pt_subfunctions,
)


# ========== S1 内嵌规则 ==========

HARA_RULES = {
    "excluded_items": [
        {"keywords": ["安全监控", "故障报警"], "domains": ["*"],
         "remark": "该功能属于安全机制，根据ISO 26262标准不对此类功能进行HARA分析。"},
        {"keywords": ["电耗显示", "续航显示"], "domains": ["*"],
         "remark": "纯信息显示类功能，不影响车辆运动控制，不进行HARA分析。"},
        {"keywords": ["增程控制"], "domains": ["P"],
         "remark": "增程器仅为辅助动力单元，不直接驱动车轮，其失效不影响车辆依靠电池正常行驶，不进行HARA分析。"},
    ],
    "excluded_subfunctions": [
        {"keywords": ["车辆特殊模式"], "domains": ["*"],
         "remark": "特殊测试/工作模式，不进行HARA分析。"},
        {"keywords": ["仪表提示"], "domains": ["*"],
         "remark": "仪表信息提示功能，不直接影响车辆运动控制，不进行HARA分析。"},
        {"keywords": ["排放测试", "排放控制"], "domains": ["P"],
         "remark": "排放相关功能为法规测试/法规要求，不涉及车辆安全，不进行HARA分析。"},
        {"keywords": ["故障存储", "故障报警"], "domains": ["*"],
         # 修复（2026-08-17）：排除词同时含"监测"的报警（如 TPMS故障报警/胎压监测报警）
         # 属安全相关监测功能，参考 Excel 判定为"是"（需 HARA），加否定词避免误排。
         "exclude_keywords": ["监测"],
         "remark": "通用故障存储及报警属于诊断/安全机制，不进行HARA分析。"},
        # 2026-08-17 新增（依据数据组6/8 参考 Excel "否" 判定）：
        {"keywords": ["车辆模式"], "domains": ["*"],
         "remark": "工厂/拖车/测功等特殊测试模式，不进行HARA分析。"},
        {"keywords": ["齿条对中"], "domains": ["CS"],
         "remark": "转向机械校准功能，不涉及车辆运动控制，不进行HARA分析。"},
        {"keywords": ["恢复车辆设置"], "domains": ["*"],
         "remark": "车辆设置恢复功能，不涉及车辆运动控制，不进行HARA分析。"},
    ],
}


# ========== 公开接口 ==========

def apply_s1_rules(related_items: list, *, pt_authority_path=None) -> dict:
    """
    对相关项应用 S1 规则层（关键词匹配）。
    返回 {item_index: {"excluded": bool, "pending_subs": [idx, ...], "decisions": {sub_idx: {is_hara, remark}}}}
    以及全局统计。
    """
    result = {}
    for i, item in enumerate(related_items):
        item_result = {
            "excluded": False,
            "item_remark": "",
            "pending_subs": [],
            "decisions": {},
            "authority": {},
        }

        # 第1层：相关项级关键词规则
        for rule in HARA_RULES["excluded_items"]:
            if not _rule_applies(rule, item.domain):
                continue
            if any(kw in item.func_name for kw in rule["keywords"]):
                item_result["excluded"] = True
                item_result["item_remark"] = rule["remark"]
                for j, sub in enumerate(item.sub_functions):
                    item_result["decisions"][j] = {"is_hara": False, "remark": rule["remark"]}
                break

        if item_result["excluded"] and pt_authority_path is None:
            result[i] = item_result
            continue

        # 第2层：子功能级关键词规则
        for j, sub in enumerate(item.sub_functions):
            matched = False
            for rule in HARA_RULES["excluded_subfunctions"]:
                if not _rule_applies(rule, item.domain):
                    continue
                if any(kw in sub.name for kw in rule["keywords"]):
                    # 否定关键词：子功能名或场景含否定词时不排除（如"监测"报警类）
                    ex_ks = rule.get("exclude_keywords", [])
                    if any(ek in sub.name or ek in (sub.scenario or "") for ek in ex_ks):
                        continue
                    item_result["decisions"][j] = {"is_hara": False, "remark": rule["remark"]}
                    matched = True
                    break
            if not matched:
                item_result["pending_subs"].append(j)

        result[i] = item_result

    # Domain Pack 覆盖层：已精确命中的域知识必须成为脚本锁定结论，不能继续交给 Agent 猜测。
    # 未知/歧义功能保持原有通用关键词规则与 pending 路径，确保泛化场景不会被静默误判。
    items_by_domain = {}
    for item_index, item in enumerate(related_items):
        if item.domain:
            items_by_domain.setdefault(item.domain, []).append((item_index, item))

    for domain, indexed_items in items_by_domain.items():
        pack_items = []
        for _, item in indexed_items:
            pack_items.append({
                "func_id": item.func_id,
                "func_name": item.func_name,
                "sub_functions": [{
                    "feature_list_id": sub.feature_list_id,
                    "name": sub.name,
                    "description": sub.description,
                    "scenario": sub.scenario,
                    "components": sub.components,

                } for sub in item.sub_functions],
            })
        try:
            context = compile_domain_context(domain, pack_items)
        except ValueError:
            # Pack 不存在或仍处于无有效知识的 bootstrap 状态时，保留原有规则路径。
            continue
        # Domain Pack 的 scope_rules 是逐子功能合同：即使同一相关项仍有未匹配
        # 的 PT 子功能，也必须立即锁定已明确的 transfer/exclude/covered_by，剩余
        # pending 子功能才交给 Agent。否则混合 PT/ADAS/车身功能会退化成自由文本备注。
        context_by_func = {item["func_id"]: item for item in context.get("related_items", [])}
        for item_index, item in indexed_items:
            context_item = context_by_func.get(item.func_id)
            if not context_item:
                continue
            features_by_id = {
                feature.get("feature_list_id"): feature
                for feature in context_item.get("features", [])
            }
            item_result = result[item_index]
            for sub_index, sub in enumerate(item.sub_functions):
                feature = features_by_id.get(sub.feature_list_id)
                if not feature or feature.get("disposition") not in {
                    "analyze", "covered_by", "transfer", "exclude"
                }:
                    continue
                # compatible 仅向 Agent 提供受控基线/差异提示，不能静默锁定。
                # 只有 exact_known 才能直接写入 s1_rule_is_hara 并从 pending 移除。
                if feature.get("evidence_status") != "exact_known":
                    continue
                disposition = feature["disposition"]
                is_hara = disposition in {"analyze", "covered_by"}
                remark = feature.get("remark") or feature.get("reason_code") or "/"
                if disposition == "covered_by":
                    remark = f"由标准分析单元 {feature.get('covered_by')} 覆盖。"
                elif disposition == "transfer":
                    remark = (f"移交 {feature.get('target_domain')} 域分析。"
                              if feature.get("target_domain") else remark)
                item_result["decisions"][sub_index] = {
                    "is_hara": is_hara,
                    "remark": remark,
                    "source": "domain_pack",
                    "disposition": disposition,
                    "analysis_unit_id": feature.get("covered_by"),
                    "target_domain": feature.get("target_domain"),
                    "reason_code": feature.get("reason_code"),
                }
                if sub_index in item_result["pending_subs"]:
                    item_result["pending_subs"].remove(sub_index)

            # 一个相关项的所有 Feature 都被 Domain Pack 排除/移交时，保留 item 级排除语义。
            if item.sub_functions and all(
                item_result["decisions"].get(index, {}).get("is_hara") is False
                for index in range(len(item.sub_functions))
            ):
                item_result["excluded"] = True
                if not item_result["item_remark"]:
                    item_result["item_remark"] = "该相关项的全部功能已由 Domain Pack 排除或移交。"

    # PT 子功能表是 PT 域 S1 的最终工程师权威来源。它必须放在通用规则和
    # Domain Pack 之后应用：已命中的表格结论覆盖旧关键词/语义预判；未命中
    # 仍保留 pending，交给 Agent 分析，并明确标记 unknown/ambiguous。
    if pt_authority_path is not None:
        try:
            authority_records, authority_summary = load_pt_subfunction_authority(pt_authority_path)
        except (FileNotFoundError, ValueError) as error:
            authority_records = []
            authority_summary = {
                "schema_version": "pt_subfunction_authority.v1",
                "source_file": str(pt_authority_path),
                "load_error": str(error),
            }
        for item_index, item in enumerate(related_items):
            if item.domain not in {"P", "PT"}:
                continue
            item_result = result[item_index]
            matches = match_pt_subfunctions(
                item.func_name,
                [sub.name for sub in item.sub_functions],
                authority_records,
            ) if authority_records else [
                {
                    "hara_match_status": "unknown",
                    "hara": None,
                    "hara_source": RUNTIME_FILENAME,
                    "match_method": "authority_source_unavailable",
                    "source_row": None,
                    "source_record_id": None,
                    "source_function_name": item.func_name,
                    "source_feature_name": None,
                    "source_remark": authority_summary.get("load_error"),
                }
                for _ in item.sub_functions
            ]
            item_result["authority_summary"] = authority_summary
            for sub_index, match in enumerate(matches):
                item_result["authority"][sub_index] = match
                if match.get("hara_match_status") != "exact_known":
                    # 权威表未唯一命中时，不能让旧关键词/Domain Pack 结论
                    # 静默覆盖 unknown/ambiguous；必须重新交给 Agent 推理。
                    item_result["decisions"].pop(sub_index, None)
                    if sub_index not in item_result["pending_subs"]:
                        item_result["pending_subs"].append(sub_index)
                    item_result["excluded"] = False
                    continue
                item_result["decisions"][sub_index] = {
                    "is_hara": bool(match.get("hara")),
                    "remark": match.get("source_remark") or "依据 PT_subfunctions.json 权威判定。",
                    "source": "pt_subfunction_authority",
                    "disposition": "analyze" if match.get("hara") else "exclude",
                    "reason_code": "PT_SUBFUNCTION_AUTHORITY_HARA_YES" if match.get("hara") else "PT_SUBFUNCTION_AUTHORITY_HARA_NO",
                    "authority": match,
                }
                if sub_index in item_result["pending_subs"]:
                    item_result["pending_subs"].remove(sub_index)
            if item.sub_functions and all(
                item_result["decisions"].get(index, {}).get("is_hara") is False
                for index in range(len(item.sub_functions))
            ):
                item_result["excluded"] = True
                if not item_result["item_remark"]:
                    item_result["item_remark"] = "该相关项的全部子功能均由 PT_subfunctions.json 判定为不进行 HARA。"

    return result


def to_intermediate_json(doc_name: str, related_items: list,
                         chapters: list[dict], s1_result: dict,
                         unmatched_chapters: list[dict], *,
                         document_context: dict | None = None,
                         parse_quality: dict | None = None,
                         source_document: dict | None = None,
                         duplicate_warnings: list[dict] | None = None) -> dict:
    """构造 intermediate.json 数据结构"""
    items_json = []
    for i, item in enumerate(related_items):
        s1 = s1_result.get(i, {})
        subs_json = []
        for j, sub in enumerate(item.sub_functions):
            sub_dict = {
                "feature_list_id": sub.feature_list_id,
                "name": sub.name,
                "scenario": sub.scenario,
                "components": sub.components,
                "chapter": sub.chapter,
                "description": sub.description,
                "source_table_index": getattr(sub, "source_table_index", None),
                "source_row": getattr(sub, "source_row", None),
                "evidence_refs": getattr(sub, "evidence_refs", []),
            }
            authority = s1.get("authority", {}).get(j)
            if authority:
                sub_dict.update({
                    "hara_match_status": authority.get("hara_match_status"),
                    "hara_source": authority.get("hara_source"),
                    "hara_match_method": authority.get("match_method"),
                    "hara_source_row": authority.get("source_row"),
                    "hara_source_record_id": authority.get("source_record_id"),
                    "hara_source_function_name": authority.get("source_function_name"),
                    "hara_source_feature_name": authority.get("source_feature_name"),
                    "hara_source_remark": authority.get("source_remark"),
                    "hara_candidate_source_rows": authority.get("candidate_source_rows", []),
                })
            if j in s1.get("decisions", {}):
                sub_dict["s1_rule_is_hara"] = s1["decisions"][j]["is_hara"]
                sub_dict["s1_rule_remark"] = s1["decisions"][j]["remark"]
                sub_dict["s1_rule_source"] = s1["decisions"][j].get("source", "keyword_rule")
                sub_dict["s1_disposition"] = s1["decisions"][j].get("disposition")
                sub_dict["analysis_unit_id"] = s1["decisions"][j].get("analysis_unit_id")
                sub_dict["target_domain"] = s1["decisions"][j].get("target_domain")
                sub_dict["s1_reason_code"] = s1["decisions"][j].get("reason_code")
            elif j in s1.get("pending_subs", []):
                sub_dict["s1_rule_is_hara"] = None  # 待 Agent 判断
                sub_dict["s1_rule_remark"] = None
            subs_json.append(sub_dict)

        items_json.append({
            "func_id": item.func_id,
            "func_name": item.func_name,
            "domain": item.domain,
            "domain_name": DOMAIN_NAMES.get(item.domain, item.domain),
            "s1_excluded_by_rule": s1.get("excluded", False),
            "s1_item_remark": s1.get("item_remark", ""),
            "pending_sub_count": len(s1.get("pending_subs", [])),
            "sub_functions": subs_json,
        })

    chapters_json = []
    for c in chapters:
        ch = {
            "h2": c["h2"], "h3": c["h3"], "label": c["label"],
            "desc": c["desc"],
            "h4s": [{"h4": h["h4"], "label": h["label"], "desc": h["desc"]} for h in c["h4s"]],
        }
        chapters_json.append(ch)

    pending_feature_ids = []
    locked_feature_ids = []
    locked_decisions = []
    for item in items_json:
        func_id = item["func_id"]
        for sub in item["sub_functions"]:
            qualified_id = f"{func_id}/{sub['feature_list_id']}"
            if sub.get("s1_rule_is_hara") is None:
                pending_feature_ids.append(qualified_id)
            else:
                locked_feature_ids.append(qualified_id)
                locked_decisions.append({
                    "feature_id": qualified_id,
                    "is_hara": sub["s1_rule_is_hara"],
                    "remark": sub.get("s1_rule_remark", "/"),
                })

    result = {
        "doc_name": doc_name,
        "domain": related_items[0].domain if related_items else "",
        "domain_name": DOMAIN_NAMES.get(related_items[0].domain if related_items else "", ""),
        "total_related_items": len(related_items),
        "total_sub_functions": sum(len(it.sub_functions) for it in related_items),
        "s1_rules_applied": True,
        "related_items": items_json,
        "chapters": chapters_json,
        "unmatched_chapters": unmatched_chapters,
        "agent_work_required": {
            # 兼容旧字段：这里实际统计的是 pending 子功能数量。
            "s1_pending_items": len(pending_feature_ids),
            "s1_pending_sub_count": len(pending_feature_ids),
            "s1_pending_feature_ids": pending_feature_ids,
            "s1_locked_feature_ids": locked_feature_ids,
            "s1_locked_decisions": locked_decisions,
            "chapter_unmatched_count": len(unmatched_chapters),
        },
    }
    authority_summaries = [
        value.get("authority_summary")
        for value in s1_result.values()
        if isinstance(value, dict) and value.get("authority_summary")
    ]
    if authority_summaries:
        result["pt_subfunction_authority"] = authority_summaries[0]
    if document_context is not None:
        result["document_context"] = document_context
    if parse_quality is not None:
        result["parse_quality"] = parse_quality
        result["agent_work_required"]["document_parse_required"] = bool(
            parse_quality.get("requires_agent_document_parse")
        )
    if source_document is not None:
        result["source_document"] = source_document
    if duplicate_warnings is not None:
        result["chapter_relation_warnings"] = duplicate_warnings
    return result


# ========== 规则辅助 ==========

def _rule_applies(rule: dict, domain: str) -> bool:
    domains = rule.get("domains", ["*"])
    if "*" in domains:
        return True
    canonical = canonicalize_domain(domain)
    return canonical in {canonicalize_domain(value) for value in domains}
