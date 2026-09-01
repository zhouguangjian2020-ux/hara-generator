# scripts/utils/safety_goal_rules.py
# S5 整车安全目标规则引擎：从 s4_hara_final.json 提取、去重、编号、聚合
# 纯函数库，无 CLI，无 print（被 stage_safety_goal.py 调用）
#
# 数据来源：
#   - ISO 26262-3:2018 第9章（Safety Goal）
#   - 11个数据组交叉验证的工程实践

import re

from utils.data_models import extract_domain


# ASIL 等级排序权重（用于排序和取最高）
ASIL_ORDER = {"-": -1, "QM": 0, "A": 1, "B": 2, "C": 3, "D": 4}

# SG 描述模板化短语模式
SG_TEMPLATE_PATTERNS = [
    r"防止.*导致危害",
    r"避免.*造成伤害",
    r"防止.*引发事故",
    r"确保.*安全",
]


def _max_asil(asil_set: set) -> str:
    """从ASIL集合中取最高等级"""
    if not asil_set:
        return "QM"
    max_level = max(ASIL_ORDER.get(a, 0) for a in asil_set)
    for k, v in ASIL_ORDER.items():
        if v == max_level:
            return k
    return "QM"


def extract_safety_goals(s4_final: dict, vehicle_safety_goal_catalog: dict | None = None) -> list[dict]:
    """从 s4_hara_final.json 提取所有有安全目标的非QM事件。

    遍历所有 hazard 的 events，筛选：
      - 非 skip 的 hazard
      - event 有 safety_goal 文本（非空、非None）
      - event 的 asil 非 QM 且非 None

    Args:
        s4_final: validate 后的 s4_hara_final.json 结构

    Returns:
        原始事件列表，每个元素包含：
        {
            "func_id", "func_name", "failure_id", "anomaly",
            "scenario", "safety_goal", "safe_state", "ftti", "asil",
            "safety_goal_id", "hazard_id", "vehicle_hazard_id", "domain"
        }
    """
    domain = s4_final.get("domain", "X")
    events = []

    for hazard in s4_final.get("hazards", []):
        if hazard.get("skip"):
            continue

        func_id = hazard.get("func_id", "")
        func_name = hazard.get("func_name", "")
        failure_id = hazard.get("failure_id", "")
        anomaly = hazard.get("anomaly", "")

        for event in hazard.get("events", []):
            sg_text = event.get("safety_goal")
            asil = event.get("asil")

            # 跳过无安全目标或QM的事件
            if not sg_text or sg_text == "None":
                continue
            if not asil or asil in ("-", "QM"):
                continue

            events.append({
                "func_id": func_id,
                "func_name": func_name,
                "failure_id": failure_id,
                "anomaly": anomaly,
                "scenario": event.get("scenario", ""),
                "safety_goal": sg_text.strip(),
                "safe_state": event.get("safe_state"),
                "ftti": event.get("ftti"),
                "asil": asil,
                "safety_goal_id": event.get("safety_goal_id"),
                "hazard_id": event.get("hazard_id"),
                "vehicle_hazard_id": event.get("vehicle_hazard_id") or hazard.get("vehicle_hazard_id"),
                "domain": domain,
                # 这些键是 S3→S4→S5 的完整工程追溯链，不以 Feature ID 或 Excel 行号代替。
                "analysis_unit_id": event.get("analysis_unit_id") or hazard.get("analysis_unit_id"),
                "hazard_family": event.get("hazard_family") or hazard.get("hazard_family"),
                "canonical_safety_goal": event.get("canonical_safety_goal") or sg_text.strip(),
                "risk_matrix_locked": bool(hazard.get("risk_matrix_locked")),
                "source_case_locked": bool(hazard.get("source_case_locked")),
                "source_case_id": event.get("source_case_id"),
                "vehicle_safety_goal_id": event.get("vehicle_safety_goal_id"),
                "vehicle_safety_goal_ids": event.get("vehicle_safety_goal_ids", []),
                "source_refs": event.get("source_refs") or hazard.get("source_refs") or [],
                "sec_source": event.get("sec_source"),
            })

    if vehicle_safety_goal_catalog is not None:
        for event in events:
            _resolve_reference_vehicle_safety_goal(event, vehicle_safety_goal_catalog)
    return events


def _resolve_reference_vehicle_safety_goal(event: dict, catalog: dict) -> None:
    """Resolve one event to an explicit reviewed vehicle SG, fail-closed.

    Direct event/case/source-reference mappings are authoritative. Semantic
    lookup is only a last resort and is accepted only for exactly one candidate;
    a text match is never used by itself.
    """
    goals = catalog.get("goals") if isinstance(catalog, dict) else None
    if not isinstance(goals, dict):
        raise ValueError("vehicle_safety_goal_catalog.goals 必须为对象")

    candidates: set[str] = set()
    direct = str(event.get("vehicle_safety_goal_id") or "").strip()
    if direct:
        candidates.add(direct)
    listed = event.get("vehicle_safety_goal_ids")
    if isinstance(listed, list):
        candidates.update(str(value).strip() for value in listed if str(value or "").strip())
    elif listed not in (None, ""):
        raise ValueError("事件 vehicle_safety_goal_ids 必须为数组")

    case_id = str(event.get("source_case_id") or "").strip()
    case_map = catalog.get("case_to_goal") or {}
    if case_id and isinstance(case_map, dict) and case_id in case_map:
        candidates.add(str(case_map[case_id]).strip())

    refs = event.get("source_refs") if isinstance(event.get("source_refs"), list) else []
    hazard_map = catalog.get("hazard_event_to_goal") or {}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        raw_id = str(ref.get("raw_hazard_event_id") or "").strip()
        if raw_id and isinstance(hazard_map, dict) and raw_id in hazard_map:
            candidates.add(str(hazard_map[raw_id]).strip())

    if len(candidates) > 1:
        raise ValueError(
            "同一S4事件对应多个整车安全目标，禁止猜测: "
            + ", ".join(sorted(candidates))
        )

    reference_id = next(iter(candidates), None)
    if reference_id is None:
        # Semantic candidates are keyed by unit + SG + safe-state + FTTI,
        # never by safety-goal text alone.
        semantic_map = catalog.get("semantic_candidates") or {}
        key = "|".join(str(value or "").strip() for value in (
            event.get("analysis_unit_id"),
            event.get("safety_goal"),
            event.get("safe_state"),
            event.get("ftti"),
        ))
        values = semantic_map.get(key, []) if isinstance(semantic_map, dict) else []
        if not isinstance(values, list):
            raise ValueError(f"整车安全目标语义候选必须为数组: {key}")
        normalized = {str(value).strip() for value in values if str(value or "").strip()}
        if len(normalized) > 1:
            raise ValueError(
                "整车安全目标语义匹配到多个候选，禁止静默选择: "
                + ", ".join(sorted(normalized))
            )
        reference_id = next(iter(normalized), None)

    if reference_id is None:
        event["reference_vehicle_safety_goal_id"] = None
        return
    if reference_id not in goals:
        raise ValueError(f"事件引用未定义的整车安全目标: {reference_id}")
    goal = goals[reference_id]
    if not isinstance(goal, dict):
        raise ValueError(f"整车安全目标目录项无效: {reference_id}")
    event["reference_vehicle_safety_goal_id"] = reference_id
    event["vehicle_safety_goal_id"] = reference_id
    # Reviewed vehicle-level text/state/FTTI is the runtime prefill source.
    for field in ("safety_goal", "safe_state", "ftti"):
        if field in goal:
            event[field] = goal.get(field)
    event["canonical_safety_goal"] = goal.get("safety_goal") or event.get("canonical_safety_goal")


def _deduplication_key(event: dict) -> tuple:
    """返回整车级 SG 的受控语义合并键。

    不能只按安全目标文本合并：相同文案在不同危害族、安全状态或 FTTI
    下是不同的工程约束。ASIL 是同一整车级安全目标关联事件的风险属性，
    必须在合并后取最高值，而不是用于拆分整车级安全目标。Pack 提供
    canonical_safety_goal 时可抵抗轻微文案差异；未沉淀的旧数据仍保留文本
    作为 canonical 值，但同样不能跨危害族/状态/FTTI 合并。
    """
    domain = event.get("domain", "")
    reference_id = event.get("reference_vehicle_safety_goal_id")
    if reference_id:
        return (domain, "__authority__", str(reference_id))
    hazard_family = event.get("hazard_family") or "__legacy_unclassified__"
    canonical_sg = event.get("canonical_safety_goal") or event.get("safety_goal") or ""
    return (
        domain,
        hazard_family,
        canonical_sg,
        event.get("safe_state"),
        event.get("ftti"),
    )


def _event_trace(event: dict) -> dict:
    """将 S4 事件压缩为 S5 可审计的关联记录。"""
    return {
        "event_safety_goal_id": event.get("safety_goal_id"),
        "hazard_id": event.get("hazard_id"),
        "func_id": event.get("func_id"),
        "failure_id": event.get("failure_id"),
        "analysis_unit_id": event.get("analysis_unit_id"),
        "vehicle_hazard_id": event.get("vehicle_hazard_id"),
        "hazard_family": event.get("hazard_family"),
        "scenario": event.get("scenario"),
        "asil": event.get("asil"),
        "safe_state": event.get("safe_state"),
        "ftti": event.get("ftti"),
    }


def deduplicate_safety_goals(events: list[dict]) -> list[dict]:
    """以危害族、受控 SG、安全状态及 FTTI 合并事件级安全目标。

    这项合并是保守的：
      * 同文案但不同危害族、安全状态或 FTTI 必须拆分；
      * 同一受控语义键下不同 ASIL 的事件必须合并，整车级 ASIL 取最高；
      * 只有 Pack 显式给出 canonical_safety_goal 时，才可以将轻微文案差异归并；
      * 输出保留每条事件级 SG 到整车级 SG 的完整追溯记录。
    """
    sg_map: dict[tuple, dict] = {}

    for event in events:
        key = _deduplication_key(event)
        if key not in sg_map:
            sg_map[key] = {
                "safety_goal": event["safety_goal"],
                "canonical_safety_goal": event.get("canonical_safety_goal") or event["safety_goal"],
                "reference_vehicle_safety_goal_id": event.get("reference_vehicle_safety_goal_id"),
                "hazard_family": event.get("hazard_family"),
                "deduplication_key": list(key),
                "asil_set": set(),
                "domain": event.get("domain", ""),
                "functions": set(),
                "anomalies": set(),
                "safe_state": event.get("safe_state"),
                "ftti": event.get("ftti"),
                "event_count": 0,
                "hazard_ids": set(),
                "vehicle_hazard_ids": set(),
                "event_safety_goal_ids": set(),
                "event_links": [],
            }

        entry = sg_map[key]
        entry["asil_set"].add(event["asil"])
        entry["event_count"] += 1
        if event.get("func_name"):
            entry["functions"].add(event["func_name"])
        if event.get("anomaly"):
            entry["anomalies"].add(event["anomaly"])
        if event.get("hazard_id"):
            entry["hazard_ids"].add(event["hazard_id"])
        if event.get("vehicle_hazard_id"):
            entry["vehicle_hazard_ids"].add(event["vehicle_hazard_id"])
        if event.get("safety_goal_id"):
            entry["event_safety_goal_ids"].add(event["safety_goal_id"])
        entry["event_links"].append(_event_trace(event))

    result = []
    for entry in sg_map.values():
        result.append({
            "safety_goal": entry["safety_goal"],
            "canonical_safety_goal": entry["canonical_safety_goal"],
            "reference_vehicle_safety_goal_id": entry.get("reference_vehicle_safety_goal_id"),
            "hazard_family": entry.get("hazard_family"),
            "deduplication_key": entry["deduplication_key"],
            "asil": _max_asil(entry["asil_set"]),
            "domain": entry["domain"],
            "functions": sorted(entry["functions"]),
            "anomalies": sorted(entry["anomalies"]),
            "safe_state": entry["safe_state"],
            "ftti": entry["ftti"],
            "event_count": entry["event_count"],
            "hazard_ids": sorted(entry["hazard_ids"]),
            "vehicle_hazard_ids": sorted(entry["vehicle_hazard_ids"]),
            "event_safety_goal_ids": sorted(entry["event_safety_goal_ids"]),
            "event_links": sorted(
                entry["event_links"],
                key=lambda item: (item.get("event_safety_goal_id") or "", item.get("hazard_id") or ""),
            ),
        })
    return result


def assign_sg_ids(sg_list: list[dict], domain: str = "X") -> list[dict]:
    """分配整车级安全目标编号 {域}_SG_VH_{4位流水}（如 P_SG_VH_0001）。

    排序规则：
      1. 按ASIL从高到低（D→C→B→A）
      2. 同ASIL按在HARA表中首次出现顺序（即 deduplicate_safety_goals 的输出顺序）

    Args:
        sg_list: deduplicate_safety_goals() 返回的列表（已按首次出现顺序排列）
        domain: 域代码（P/A/B/CB/CS/Info），用于编号前缀

    Returns:
        带 sg_id 字段的列表，按排序规则排列
    """
    # 排序：ASIL降序，同ASIL按HARA表首次出现顺序（保留输入列表顺序）
    indexed = list(enumerate(sg_list))
    sorted_indexed = sorted(
        indexed,
        key=lambda pair: (-ASIL_ORDER.get(pair[1]["asil"], 0), pair[0])
    )
    sorted_list = [sg for _idx, sg in sorted_indexed]

    reserved = {
        str(sg.get("reference_vehicle_safety_goal_id")).strip()
        for sg in sorted_list
        if str(sg.get("reference_vehicle_safety_goal_id") or "").strip()
    }
    assigned: set[str] = set()
    next_number = 1
    for sg in sorted_list:
        reference_id = str(sg.get("reference_vehicle_safety_goal_id") or "").strip()
        if reference_id:
            allowed_prefixes = {domain}
            if domain == "PT":
                # Excel 中的 PT 权威目标沿用项目历史前缀 P；旧自动编号仍使用 PT。
                allowed_prefixes.add("P")
            if not any(re.fullmatch(rf"{re.escape(prefix)}_SG_VH_\d{{4}}", reference_id) for prefix in allowed_prefixes):
                raise ValueError(f"权威整车安全目标 ID 格式错误: {reference_id}")
            sg["sg_id"] = reference_id
            assigned.add(reference_id)
            continue
        while True:
            candidate = f"{domain}_SG_VH_{next_number:04d}"
            next_number += 1
            if candidate not in reserved and candidate not in assigned:
                sg["sg_id"] = candidate
                assigned.add(candidate)
                break

    return sorted_list


def _count_chinese_chars(text: str) -> int:
    """统计文本中的汉字数量"""
    return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')


def _sg_is_template_only(sg_text: str, func_name: str = "") -> bool:
    """判断SG描述是否只包含模板化短语（去掉功能名后全部匹配模板模式）。

    Args:
        sg_text: 安全目标描述文本
        func_name: 功能名称（用于从SG文本中剥离）

    Returns:
        True 表示SG描述只有模板化表达，缺乏具体功能/场景信息
    """
    if not sg_text:
        return False

    remaining = sg_text.strip()

    # 去掉功能名
    if func_name and func_name in remaining:
        remaining = remaining.replace(func_name, "")

    # 去掉所有模板化短语
    for pattern in SG_TEMPLATE_PATTERNS:
        remaining = re.sub(pattern, "", remaining)

    # 去掉标点和空白
    remaining = re.sub(r"[，。、；：！？\s,.;:!?]", "", remaining)

    # 如果剩余文本中汉字少于3个，认为主要是模板化表达
    chinese_remaining = _count_chinese_chars(remaining)
    return chinese_remaining < 3


def _extract_anomaly_keywords(anomaly: str) -> list[str]:
    """从 anomaly 文本中提取关键词（去掉常见动词，保留名词核心）。"""
    if not anomaly:
        return []
    # 简单提取：按常见词分割，保留2字以上的片段
    keywords = []
    # 去掉常见动词前缀
    cleaned = re.sub(r"^(非预期|无法|不能|丢失|失效|故障|错误|异常|偏差|过高|过低|过大|过小|不足)", "", anomaly)
    # 去掉常见后缀
    cleaned = re.sub(r"(功能|现象|状态|情况|问题|故障)$", "", cleaned)
    cleaned = cleaned.strip()
    if cleaned and len(cleaned) >= 2:
        keywords.append(cleaned)
    # 也加入原 anomaly 中2字以上的名词片段
    for i in range(len(anomaly) - 1):
        chunk = anomaly[i:i+2]
        if _count_chinese_chars(chunk) == 2 and chunk not in keywords:
            keywords.append(chunk)
    return keywords


def validate_safety_goal_quality(s4_final: dict) -> list:
    """安全目标描述质量验证。

    从 s4_final 中提取所有有 safety_goal 的事件，检查：
    1. SG 描述为空或过短（< 10 个汉字）→ error
    2. SG 描述模板化（只含通用模板短语，无具体功能/场景信息）→ warning
    3. SG 与 anomaly 对应性（SG应包含anomaly中的关键词）→ warning
    4. QM 事件不应有 SG → warning
    5. ASIL 事件缺失 SG（A/B/C/D但SG为空）→ error

    Args:
        s4_final: s4_hara_final.json 结构

    Returns:
        list of (level, message)，level 为 "error" 或 "warning"
    """
    issues = []

    for hazard in s4_final.get("hazards", []):
        if hazard.get("skip"):
            continue
        # 精确 Pack 已在 S4 以矩阵字段逐项验证；不再用仅适用于 Agent 自由文本的
        # 关键词相关性启发式制造假阳性。
        if hazard.get("risk_matrix_locked") or hazard.get("source_case_locked"):
            continue

        func_name = hazard.get("func_name", "")
        anomaly = hazard.get("anomaly", "")
        anomaly_keywords = _extract_anomaly_keywords(anomaly)

        for event in hazard.get("events", []):
            sg_text = event.get("safety_goal") or ""
            sg_text = sg_text.strip()
            asil = event.get("asil") or ""
            asil = asil.strip() if asil else ""

            # 1. SG 描述为空或过短（只针对有ASIL等级的事件）
            if asil in ["A", "B", "C", "D"]:
                chinese_count = _count_chinese_chars(sg_text)
                if chinese_count < 10:
                    issues.append((
                        "error",
                        f"SG描述过短（{chinese_count}字）: '{sg_text[:30]}' "
                        f"(anomaly={anomaly}, asil={asil})"
                    ))

            # 2. SG 描述模板化
            if sg_text and _sg_is_template_only(sg_text, func_name):
                issues.append((
                    "warning",
                    f"SG描述模板化风险: '{sg_text[:40]}' "
                    f"(func={func_name})"
                ))

            # 3. SG 与 anomaly 对应性
            if sg_text and anomaly and anomaly_keywords:
                has_keyword = any(kw in sg_text for kw in anomaly_keywords)
                if not has_keyword:
                    issues.append((
                        "warning",
                        f"SG与anomaly关联性弱: SG='{sg_text[:30]}' "
                        f"不包含anomaly='{anomaly}'的关键词"
                    ))

            # 4. QM 事件不应有 SG
            if asil == "QM" and sg_text:
                issues.append((
                    "warning",
                    f"QM事件有安全目标: '{sg_text[:30]}' "
                    f"(anomaly={anomaly})"
                ))

            # 5. ASIL 事件缺失 SG
            if asil in ["A", "B", "C", "D"] and not sg_text:
                issues.append((
                    "error",
                    f"ASIL {asil} 事件缺失安全目标 "
                    f"(anomaly={anomaly}, scenario={event.get('scenario', '')[:20]})"
                ))

    return issues


def validate_sg_ftti_reasonableness(s4_final: dict) -> list:
    """FTTI 合理性检查。

    1. ASIL D 但 FTTI 为空 → warning
    2. ASIL QM 但有 FTTI → warning
    3. FTTI 值合理性（范围检查）→ warning
    4. 安全状态为空但有 FTTI → warning

    Args:
        s4_final: s4_hara_final.json 结构

    Returns:
        list of (level, message)，全部 warning 级别
    """
    issues = []

    for hazard in s4_final.get("hazards", []):
        if hazard.get("skip"):
            continue
        if hazard.get("risk_matrix_locked"):
            continue

        anomaly = hazard.get("anomaly", "")

        for event in hazard.get("events", []):
            asil = event.get("asil") or ""
            asil = asil.strip() if asil else ""
            ftti = event.get("ftti")
            ftti_str = str(ftti).strip() if ftti else ""
            safe_state = event.get("safe_state") or ""
            safe_state = safe_state.strip() if safe_state else ""

            has_ftti = bool(ftti_str and ftti_str.lower() != "none")

            # 1. ASIL D 但 FTTI 为空
            if asil == "D" and not has_ftti:
                issues.append((
                    "warning",
                    f"ASIL D 事件FTTI为空 "
                    f"(anomaly={anomaly}, scenario={event.get('scenario', '')[:20]})"
                ))

            # 2. ASIL QM 但有 FTTI
            if asil == "QM" and has_ftti:
                issues.append((
                    "warning",
                    f"QM事件有FTTI值: {ftti_str} "
                    f"(anomaly={anomaly})"
                ))

            # 3. FTTI 值合理性
            if has_ftti:
                ftti_lower = ftti_str.lower()
                value = None
                unit = None

                # 提取数值和单位
                ms_match = re.search(r"(\d+(?:\.\d+)?)\s*ms", ftti_lower)
                s_match = re.search(r"(\d+(?:\.\d+)?)\s*s", ftti_lower)

                if ms_match:
                    value = float(ms_match.group(1))
                    unit = "ms"
                elif s_match:
                    value = float(s_match.group(1))
                    unit = "s"

                if value is not None and unit == "ms":
                    if value < 10 or value > 10000:
                        issues.append((
                            "warning",
                            f"FTTI值异常: {ftti_str} "
                            f"(ms范围应在10ms~10000ms, anomaly={anomaly})"
                        ))
                elif value is not None and unit == "s":
                    if value < 1 or value > 600:
                        issues.append((
                            "warning",
                            f"FTTI值异常: {ftti_str} "
                            f"(s范围应在1s~600s, anomaly={anomaly})"
                        ))

            # 4. 安全状态为空但有 FTTI
            if has_ftti and not safe_state:
                issues.append((
                    "warning",
                    f"有FTTI但安全状态为空: FTTI={ftti_str} "
                    f"(anomaly={anomaly})"
                ))

    return issues


def validate_sg_assignment_quality(safety_goals_result: dict) -> list:
    """安全目标汇总质量检查。

    在 generate_safety_goals 生成结果后调用。

    1. SG 数量合理性：unique_safety_goals < 2 且 total_events >= 10 → warning
    2. SG 数量合理性2：unique_safety_goals > total_events * 0.8 → warning
    3. SG 编号格式验证：每个 sg_id 应匹配 {domain}_SG_VH_\\d{4} 格式 → error
    4. ASIL 聚合验证：每个 SG 的 asil 应是其关联事件中的最高 ASIL → error
    5. SG 描述重复：不同 sg_id 但安全目标文本完全相同 → error

    Args:
        safety_goals_result: generate_safety_goals() 返回的 dict

    Returns:
        list of (level, message)
    """
    issues = []

    total_events = safety_goals_result.get("total_events", 0)
    unique_sgs = safety_goals_result.get("unique_safety_goals", 0)
    domain = safety_goals_result.get("domain", "X")
    sgs = safety_goals_result.get("safety_goals", [])

    # 1. SG 数量合理性：去重过度
    if unique_sgs < 2 and total_events >= 10:
        issues.append((
            "warning",
            f"SG去重过度: {unique_sgs} 个SG对应 {total_events} 个事件 "
            f"(可能所有事件都用了同一个SG模板)"
        ))

    # 2. SG 数量合理性2：去重不足
    if total_events > 0 and unique_sgs > total_events * 0.8:
        issues.append((
            "warning",
            f"SG去重不足: {unique_sgs} 个SG / {total_events} 个事件 "
            f"(>80%，几乎每个事件一个SG)"
        ))

    # SG ID 格式正则
    sg_prefixes = [domain, "P"] if domain == "PT" else [domain]
    sg_id_pattern = re.compile(rf"^(?:{'|'.join(re.escape(prefix) for prefix in sg_prefixes)})_SG_VH_\d{{4}}$")

    # 同文本可以因危害族/ASIL/安全状态/FTTI 不同而合法地形成多个 SG；
    # 因此只检测重复的受控合并键，不能再按文本报错。
    sg_key_map = {}  # tuple(deduplication_key) -> list of sg_id

    for sg in sgs:
        sg_id = sg.get("sg_id", "")
        sg_text = sg.get("safety_goal", "")
        sg_asil = sg.get("asil", "")

        # 3. SG 编号格式验证
        if not sg_id_pattern.match(sg_id):
            issues.append((
                "error",
                f"SG编号格式错误: '{sg_id}' "
                f"(应为 {domain}_SG_VH_XXXX 格式)"
            ))

        # 5. 受控合并键唯一性检测（相同文本允许不同工程语义的多条 SG）
        raw_key = sg.get("deduplication_key")
        if isinstance(raw_key, list) and raw_key:
            key = tuple(raw_key)
            sg_key_map.setdefault(key, []).append(sg_id)

        # 4. ASIL 聚合验证
        # 需要从 anomalies/functions 反推关联事件的 ASIL 比较复杂
        # 这里用 event_count 和 sg 的 asil 与原始事件数据做对比
        # 但 safety_goals_result 中没有原始事件的 asil 分布
        # 所以我们基于 sg 自身数据做合理性检查：
        # 如果 event_count > 1 但 asil 是 QM，可能有问题
        if sg.get("event_count", 0) > 1 and sg_asil == "QM":
            issues.append((
                "error",
                f"SG ASIL聚合异常: {sg_id} 有 {sg['event_count']} 个关联事件 "
                f"但ASIL为QM（应为非QM事件的最高ASIL）"
            ))

    # 5. 合并键重复意味着 S5 自己产生了重复的整车级 SG。
    for key, sg_ids in sg_key_map.items():
        if len(sg_ids) > 1:
            issues.append((
                "error",
                f"SG受控合并键重复: {list(key)} 被分配给多个SG编号: {', '.join(sg_ids)}",
            ))

    return issues


def generate_safety_goals(s4_final: dict, vehicle_safety_goal_catalog: dict | None = None) -> dict:
    """S5主入口：从 s4_hara_final.json 生成整车安全目标汇总。

    流程：extract → deduplicate → assign_ids → validate

    Args:
        s4_final: validate 后的 s4_hara_final.json 结构

    Returns:
        {
            "version": "1.0",
            "domain": str,
            "total_events": int,       # 原始有SG的非QM事件数
            "unique_safety_goals": int, # 去重后的安全目标数
            "safety_goals": [...],
            "validation_issues": [     # 新增：验证问题列表
                ("error"|"warning", "message"),
                ...
            ]
        }
    """
    domain = s4_final.get("domain", "X")

    # 1. 提取
    raw_events = extract_safety_goals(s4_final, vehicle_safety_goal_catalog)

    # 2. 去重
    deduped = deduplicate_safety_goals(raw_events)

    # 3. 编号
    with_ids = assign_sg_ids(deduped, domain)

    # 4. 建立事件级 SG → 整车级 SG 的显式映射。Excel 展示和后续导出
    # 必须使用该映射，而不能只按 safety_goal 文本匹配。
    event_safety_goal_mappings = []
    for safety_goal in with_ids:
        vehicle_sg_id = safety_goal.get("sg_id")
        for link in safety_goal.get("event_links", []):
            event_safety_goal_mappings.append({
                **link,
                "vehicle_safety_goal_id": vehicle_sg_id,
            })

    # 5. 验证
    result = {
        "version": "1.1",
        "domain": domain,
        "total_events": len(raw_events),
        "unique_safety_goals": len(with_ids),
        "safety_goals": with_ids,
        "event_safety_goal_mappings": event_safety_goal_mappings,
    }

    all_issues = []
    all_issues.extend(validate_safety_goal_quality(s4_final))
    all_issues.extend(validate_sg_ftti_reasonableness(s4_final))
    all_issues.extend(validate_sg_assignment_quality(result))

    result["validation_issues"] = all_issues

    return result
