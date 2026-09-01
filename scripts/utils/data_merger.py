#!/usr/bin/env python3
"""
数据合并器 - 合并历史预填数据和Agent新生成的数据

功能:
- 合并预填的历史数据和Agent新分析的数据
- 去重、排序、验证
- 确保数据一致性（如failure_id连续性）
"""

from typing import Any
from collections import defaultdict


def merge_function_data(
    prefilled_data: dict[str, Any],
    agent_data: dict[str, Any],
    function_name: str
) -> dict[str, Any]:
    """
    合并预填数据和Agent生成的数据

    参数:
        prefilled_data: 从PT速查表提取的历史数据
        agent_data: Agent新分析生成的数据（结构与prefilled_data相同）
        function_name: 当前功能名

    返回:
        合并后的完整数据，结构与extract_complete_function_data返回值相同
    """
    merged = {
        "function_name": function_name,
        "failure_modes": [],
        "hazop_entries": [],
        "hara_events": [],
        "safety_goals": []
    }

    # 1. 合并失效模式（简单列表合并去重）
    merged["failure_modes"] = _merge_failure_modes(
        prefilled_data.get("failure_modes", []),
        agent_data.get("failure_modes", [])
    )

    # 2. 合并HAZOP条目
    merged["hazop_entries"] = _merge_hazop_entries(
        prefilled_data.get("hazop_entries", []),
        agent_data.get("hazop_entries", [])
    )

    # 3. 合并HARA事件
    merged["hara_events"] = _merge_hara_events(
        prefilled_data.get("hara_events", []),
        agent_data.get("hara_events", [])
    )

    # 4. 合并安全目标
    merged["safety_goals"] = _merge_safety_goals(
        prefilled_data.get("safety_goals", []),
        agent_data.get("safety_goals", [])
    )

    return merged


def _merge_failure_modes(
    prefilled: list[str],
    agent_generated: list[str]
) -> list[str]:
    """合并失效模式列表，去重保序"""
    seen = set()
    result = []

    # 先添加预填的
    for fm in prefilled:
        if fm not in seen:
            seen.add(fm)
            result.append(fm)

    # 再添加新生成的
    for fm in agent_generated:
        if fm not in seen:
            seen.add(fm)
            result.append(fm)

    return result


def _merge_hazop_entries(
    prefilled: list[dict[str, Any]],
    agent_generated: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    合并HAZOP条目

    去重规则：相同的 (failure_mode, anomaly, vehicle_hazard) 视为重复
    """
    seen_keys = set()
    result = []

    def get_key(entry: dict) -> tuple:
        return (
            entry.get("failure_mode", ""),
            entry.get("anomaly", ""),
            entry.get("vehicle_hazard", "")
        )

    # 先添加预填的
    for entry in prefilled:
        key = get_key(entry)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append(entry)

    # 再添加新生成的
    for entry in agent_generated:
        key = get_key(entry)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append(entry)

    return result


def _merge_hara_events(
    prefilled: list[dict[str, Any]],
    agent_generated: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    合并HARA事件

    去重规则：相同的 (failure_id, scenario_id) 视为重复
    注意：failure_id在agent_generated中可能是临时ID，需要重新分配
    """
    seen_keys = set()
    result = []

    def get_key(event: dict) -> tuple:
        # 使用description和scenario的组合来去重，而不是failure_id
        return (
            event.get("description", "")[:100],  # 取前100字符
            event.get("scenario", "")[:100]
        )

    # 先添加预填的
    for event in prefilled:
        key = get_key(event)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append(event)

    # 再添加新生成的
    for event in agent_generated:
        key = get_key(event)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append(event)

    return result


def _merge_safety_goals(
    prefilled: list[dict[str, Any]],
    agent_generated: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    合并安全目标

    去重规则：相同的 safety_goal 文本视为重复
    取较高的ASIL等级，合并applicable_hazards
    """
    asil_order = {"QM": 0, "A": 1, "B": 2, "C": 3, "D": 4}

    # 按safety_goal分组
    sg_map = {}

    def add_to_map(sg_list: list[dict[str, Any]]):
        for sg in sg_list:
            goal_text = sg.get("safety_goal", "")
            if not goal_text:
                continue

            if goal_text not in sg_map:
                sg_map[goal_text] = {
                    "safety_goal": goal_text,
                    "safe_state": sg.get("safe_state"),
                    "ftti": sg.get("ftti"),
                    "asil": sg.get("asil"),
                    "applicable_hazards": set(sg.get("applicable_hazards", []))
                }
            else:
                # 合并数据
                existing = sg_map[goal_text]

                # 取更高的ASIL
                new_asil = sg.get("asil")
                if new_asil:
                    old_asil = existing["asil"]
                    if not old_asil or asil_order.get(new_asil, 0) > asil_order.get(old_asil, 0):
                        existing["asil"] = new_asil

                # 合并hazards
                existing["applicable_hazards"].update(sg.get("applicable_hazards", []))

                # safe_state和ftti取非空的
                if not existing.get("safe_state") and sg.get("safe_state"):
                    existing["safe_state"] = sg["safe_state"]
                if not existing.get("ftti") and sg.get("ftti"):
                    existing["ftti"] = sg["ftti"]

    # 合并两个来源
    add_to_map(prefilled)
    add_to_map(agent_generated)

    # 转换为列表
    result = []
    for sg_data in sg_map.values():
        result.append({
            "safety_goal": sg_data["safety_goal"],
            "safe_state": sg_data["safe_state"],
            "ftti": sg_data["ftti"],
            "asil": sg_data["asil"],
            "applicable_hazards": sorted(sg_data["applicable_hazards"])
        })

    # 按ASIL降序排列
    result.sort(
        key=lambda x: asil_order.get(x.get("asil"), -1),
        reverse=True
    )

    return result


def reassign_failure_ids(
    merged_data: dict[str, Any],
    project_prefix: str = "PROJ"
) -> dict[str, Any]:
    """
    重新分配failure_id，确保ID连续且唯一

    参数:
        merged_data: 合并后的数据
        project_prefix: 项目前缀，如"PROJ"生成PROJ_MF_0001_01

    返回:
        重新分配ID后的数据（原地修改并返回）
    """
    # 建立失效模式到编号的映射
    failure_mode_to_num = {}
    for i, fm in enumerate(merged_data.get("failure_modes", []), 1):
        failure_mode_to_num[fm] = i

    # 为每个失效模式维护子编号计数器
    failure_counters = defaultdict(int)

    # 建立旧ID到新ID的映射
    old_to_new_id = {}

    # 1. 重新分配HAZOP条目的ID
    for entry in merged_data.get("hazop_entries", []):
        old_id = entry.get("failure_id", "")
        fm = entry.get("failure_mode", "")

        if fm in failure_mode_to_num:
            func_num = failure_mode_to_num[fm]
            failure_counters[fm] += 1
            sub_num = failure_counters[fm]

            new_id = f"{project_prefix}_MF_{func_num:04d}_{sub_num:02d}"
            entry["failure_id"] = new_id

            if old_id:
                old_to_new_id[old_id] = new_id

    # 2. 更新HARA事件的ID
    for event in merged_data.get("hara_events", []):
        old_id = event.get("failure_id", "")
        if old_id in old_to_new_id:
            event["failure_id"] = old_to_new_id[old_id]

    return merged_data


def validate_merged_data(merged_data: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    验证合并后的数据完整性

    返回:
        (是否通过, 错误信息列表)
    """
    errors = []

    # 1. 检查必需字段
    if not merged_data.get("function_name"):
        errors.append("缺少功能名")

    # 2. 检查失效模式不为空
    if not merged_data.get("failure_modes"):
        errors.append("失效模式列表为空")

    # 3. 检查HAZOP和HARA数据一致性
    hazop_failure_ids = {e.get("failure_id") for e in merged_data.get("hazop_entries", []) if e.get("failure_id")}
    hara_failure_ids = {e.get("failure_id") for e in merged_data.get("hara_events", []) if e.get("failure_id")}

    # HARA中的failure_id应该都能在HAZOP中找到
    orphan_ids = hara_failure_ids - hazop_failure_ids
    if orphan_ids:
        errors.append(f"HARA事件中存在孤立的failure_id（未在HAZOP中定义）: {orphan_ids}")

    # 4. 检查ASIL规则：S=0时，E/C/ASIL必须都为null
    for event in merged_data.get("hara_events", []):
        severity = event.get("severity")
        exposure = event.get("exposure")
        controllability = event.get("controllability")
        asil = event.get("expected_asil")

        if severity == 0:
            if exposure is not None or controllability is not None or asil is not None:
                errors.append(
                    f"ASIL规则违规: failure_id={event.get('failure_id')} "
                    f"S=0但E/C/ASIL不全为null (E={exposure}, C={controllability}, ASIL={asil})"
                )

    # 5. 检查安全目标的ASIL是否有效
    valid_asils = {"QM", "A", "B", "C", "D", None}
    for sg in merged_data.get("safety_goals", []):
        asil = sg.get("asil")
        if asil not in valid_asils:
            errors.append(f"安全目标ASIL无效: {asil}, 目标={sg.get('safety_goal')}")

    return (len(errors) == 0, errors)


def merge_all_functions(
    functions_with_data: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    合并所有功能的数据

    参数:
        functions_with_data: 列表，每个元素包含：
            {
                "function_name": "档位控制及显示功能",
                "match_type": "exact" | "similar" | "none",
                "prefilled_data": {...},  # 历史数据（可能为空）
                "agent_data": {...}       # Agent生成的数据（可能为空）
            }

    返回:
        合并后的功能数据列表
    """
    results = []

    for i, func_data in enumerate(functions_with_data, 1):
        function_name = func_data["function_name"]
        match_type = func_data["match_type"]
        prefilled = func_data.get("prefilled_data", {})
        agent = func_data.get("agent_data", {})

        # 合并数据
        if match_type == "exact":
            # 完全匹配：优先使用历史数据，补充Agent新发现的
            merged = merge_function_data(prefilled, agent, function_name)
        elif match_type == "similar":
            # 相似匹配：优先使用Agent数据，参考历史数据
            merged = merge_function_data(agent, prefilled, function_name)
        else:  # none
            # 无匹配：全部使用Agent数据
            merged = agent

        # 重新分配failure_id
        merged = reassign_failure_ids(merged, project_prefix=f"FUNC{i:02d}")

        # 验证
        is_valid, errors = validate_merged_data(merged)
        if not is_valid:
            print(f"⚠️  功能 '{function_name}' 数据验证失败:")
            for error in errors:
                print(f"   - {error}")

        results.append({
            "function_name": function_name,
            "match_type": match_type,
            "merged_data": merged,
            "validation_passed": is_valid,
            "validation_errors": errors
        })

    return results
