#!/usr/bin/env python3
"""
PT域数据提取器 - 从速查表中提取功能的完整历史数据

功能:
- 提取指定功能的失效模式、HAZOP、HARA、安全目标等完整数据
- 支持按功能ID或功能名提取
- 自动去重和结构化整理
"""

from typing import Any
from collections import defaultdict


def extract_complete_function_data(
    function_id: str,
    pt_pack: dict[str, Any]
) -> dict[str, Any]:
    """
    提取一个功能的完整历史数据

    参数:
        function_id: 功能ID，如"P_func_0001"
        pt_pack: PT域Domain Pack

    返回:
        {
            "function_id": "P_func_0001",
            "function_name": "档位控制及显示功能",
            "failure_modes": ["丢失", "非预期", "反向"],
            "hazop_entries": [
                {
                    "failure_mode": "丢失",
                    "failure_id": "P_MF_0001_01",
                    "anomaly": "D挡位不显示",
                    "vehicle_hazard": "无法正确显示车辆D挡信息",
                    "vehicle_hazard_id": "P_hzrd_01001",
                    "hazard_family": "information_display"
                },
                ...
            ],
            "hara_events": [
                {
                    "failure_id": "P_MF_0001_01",
                    "scenario": "车辆在平坦路面起步...",
                    "scenario_id": "PT_SC_001",
                    "description": "驾驶员无法通过仪表盘...",
                    "severity": 0,
                    "severity_reason": "无安全影响",
                    "exposure": null,
                    "exposure_reason": null,
                    "controllability": null,
                    "controllability_reason": null,
                    "expected_asil": null,
                    "safety_goal": null,
                    "safe_state": null,
                    "ftti": null,
                    "vehicle_hazard_id": "P_hzrd_01001"
                },
                ...
            ],
            "safety_goals": [
                {
                    "safety_goal": "防止非预期的输出制动转矩",
                    "safe_state": "关闭制动扭矩",
                    "ftti": "200ms",
                    "asil": "B",
                    "applicable_hazards": ["P_hzrd_01002", "P_hzrd_01003"]
                },
                ...
            ]
        }
    """
    # 1. 提取失效模式
    failure_modes = _extract_failure_modes(function_id, pt_pack)

    # 2. 提取HAZOP条目
    hazop_entries = _extract_hazop_entries(function_id, pt_pack)

    # 3. 提取HARA事件
    hara_events = _extract_hara_events(function_id, pt_pack)

    # 4. 提取并去重安全目标
    safety_goals = _extract_safety_goals(hara_events)

    # 5. 查找功能名
    function_name = _find_function_name_from_data(hazop_entries, hara_events)

    return {
        "function_id": function_id,
        "function_name": function_name or function_id,
        "failure_modes": failure_modes,
        "hazop_entries": hazop_entries,
        "hara_events": hara_events,
        "safety_goals": safety_goals
    }


def _extract_failure_modes(function_id: str, pt_pack: dict[str, Any]) -> list[str]:
    """从failure_mode_rules中提取失效模式列表"""
    failure_mode_rules = pt_pack.get("analysis_catalog", {}).get("failure_mode_rules", {})
    return failure_mode_rules.get(function_id, [])


def _extract_hazop_entries(function_id: str, pt_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """从hazop_patterns中提取HAZOP条目"""
    hazop_patterns = pt_pack.get("analysis_catalog", {}).get("hazop_patterns", [])

    # function_id: P_func_0001
    # failure_id: P_MF_0001_01, P_MF_0001_02, ...
    func_prefix = function_id.replace("func", "MF")

    entries = []
    for pattern in hazop_patterns:
        failure_id = pattern.get("failure_id", "")
        if failure_id.startswith(func_prefix):
            entries.append({
                "failure_mode": pattern.get("failure_mode", ""),
                "failure_id": failure_id,
                "anomaly": pattern.get("anomaly", ""),
                "vehicle_hazard": pattern.get("vehicle_hazard", ""),
                "vehicle_hazard_id": pattern.get("vehicle_hazard_id", ""),
                "hazard_family": pattern.get("hazard_family", "")
            })

    return entries


def _extract_hara_events(function_id: str, pt_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """从event_matrix中提取HARA事件"""
    events = pt_pack.get("risk_catalog", {}).get("event_matrix", [])

    func_prefix = function_id.replace("func", "MF")

    hara_events = []
    for event in events:
        failure_id = event.get("failure_id", "")
        if failure_id.startswith(func_prefix):
            hara_events.append({
                "failure_id": failure_id,
                "scenario": event.get("scenario", ""),
                "scenario_id": event.get("scenario_id", ""),
                "description": event.get("description", ""),
                "severity": event.get("severity"),
                "severity_reason": event.get("severity_reason", ""),
                "exposure": event.get("exposure"),
                "exposure_reason": event.get("exposure_reason", ""),
                "controllability": event.get("controllability"),
                "controllability_reason": event.get("controllability_reason", ""),
                "expected_asil": event.get("expected_asil"),
                "safety_goal": event.get("safety_goal"),
                "safe_state": event.get("safe_state"),
                "ftti": event.get("ftti"),
                "vehicle_hazard_id": event.get("vehicle_hazard_id", "")
            })

    return hara_events


def _extract_safety_goals(hara_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    从HARA事件中提取并去重安全目标

    将相同的安全目标合并，收集所有关联的危害ID
    """
    # 按 (safety_goal, safe_state, ftti) 分组
    sg_map = defaultdict(lambda: {
        "safety_goal": None,
        "safe_state": None,
        "ftti": None,
        "asil": set(),
        "applicable_hazards": set()
    })

    for event in hara_events:
        sg = event.get("safety_goal")
        ss = event.get("safe_state")
        ftti = event.get("ftti")

        if not sg:  # 没有安全目标（如S=0的情况）
            continue

        key = (sg, ss, ftti)

        sg_map[key]["safety_goal"] = sg
        sg_map[key]["safe_state"] = ss
        sg_map[key]["ftti"] = ftti

        asil = event.get("expected_asil")
        if asil:
            sg_map[key]["asil"].add(asil)

        hazard_id = event.get("vehicle_hazard_id")
        if hazard_id:
            sg_map[key]["applicable_hazards"].add(hazard_id)

    # 转换为列表，取最高ASIL
    safety_goals = []
    asil_order = {"QM": 0, "A": 1, "B": 2, "C": 3, "D": 4}

    for sg_data in sg_map.values():
        # 取最高ASIL
        asils = sg_data["asil"]
        if asils:
            max_asil = max(asils, key=lambda a: asil_order.get(a, -1))
        else:
            max_asil = None

        safety_goals.append({
            "safety_goal": sg_data["safety_goal"],
            "safe_state": sg_data["safe_state"],
            "ftti": sg_data["ftti"],
            "asil": max_asil,
            "applicable_hazards": sorted(sg_data["applicable_hazards"])
        })

    # 按ASIL降序排列
    safety_goals.sort(
        key=lambda x: asil_order.get(x["asil"], -1),
        reverse=True
    )

    return safety_goals


def _find_function_name_from_data(
    hazop_entries: list[dict[str, Any]],
    hara_events: list[dict[str, Any]]
) -> str | None:
    """从提取的数据中查找功能名"""
    # 尝试从HAZOP条目中找
    for entry in hazop_entries:
        # 注意：当前PT pack的hazop_patterns可能没有function字段
        # 需要从原始Excel提取时补充
        pass

    # 尝试从HARA事件中找（同样可能没有function字段）
    for event in hara_events:
        pass

    return None


def extract_similar_function_summary(
    function_id: str,
    pt_pack: dict[str, Any]
) -> str:
    """
    生成相似功能的简要摘要，供Agent参考

    返回一段结构化的文本，描述该功能的：
    - 失效模式类型和数量
    - 主要危害类型
    - 风险分布（S/E/C/ASIL）
    - 典型场景
    """
    data = extract_complete_function_data(function_id, pt_pack)

    summary_lines = []

    summary_lines.append(f"## 功能: {data['function_name']}")
    summary_lines.append(f"**功能ID**: {data['function_id']}")
    summary_lines.append("")

    # 失效模式
    summary_lines.append(f"**失效模式** ({len(data['failure_modes'])} 种):")
    for fm in data['failure_modes']:
        summary_lines.append(f"  - {fm}")
    summary_lines.append("")

    # HAZOP统计
    summary_lines.append(f"**HAZOP分析** ({len(data['hazop_entries'])} 条):")
    hazard_families = set(e['hazard_family'] for e in data['hazop_entries'] if e.get('hazard_family'))
    summary_lines.append(f"  危害类型: {', '.join(hazard_families)}")
    summary_lines.append("")

    # HARA统计
    summary_lines.append(f"**HARA事件** ({len(data['hara_events'])} 条):")

    # 统计ASIL分布
    asil_dist = defaultdict(int)
    for event in data['hara_events']:
        asil = event.get('expected_asil') or 'null'
        asil_dist[asil] += 1

    summary_lines.append("  ASIL分布:")
    for asil, count in sorted(asil_dist.items()):
        summary_lines.append(f"    - {asil}: {count}条")
    summary_lines.append("")

    # 安全目标
    summary_lines.append(f"**安全目标** ({len(data['safety_goals'])} 个):")
    for sg in data['safety_goals'][:3]:  # 只显示前3个
        summary_lines.append(f"  - [{sg['asil']}] {sg['safety_goal']}")
    summary_lines.append("")

    # 典型场景示例（显示前3个）
    summary_lines.append("**典型场景示例**:")
    for i, event in enumerate(data['hara_events'][:3], 1):
        scenario = event['scenario'][:60] + "..." if len(event['scenario']) > 60 else event['scenario']
        summary_lines.append(f"  {i}. {scenario}")
        summary_lines.append(f"     S={event.get('severity')}, E={event.get('exposure')}, C={event.get('controllability')}, ASIL={event.get('expected_asil')}")

    return "\n".join(summary_lines)


def get_function_statistics(pt_pack: dict[str, Any]) -> dict[str, Any]:
    """
    获取PT速查表的整体统计信息

    返回:
        {
            "total_functions": 7,
            "total_hazop_entries": 100,
            "total_hara_events": 479,
            "total_safety_goals": 25,
            "asil_distribution": {"QM": 219, "A": 104, "B": 67, "C": 50},
            "functions_detail": [...]
        }
    """
    failure_mode_rules = pt_pack.get("analysis_catalog", {}).get("failure_mode_rules", {})
    hazop_patterns = pt_pack.get("analysis_catalog", {}).get("hazop_patterns", [])
    events = pt_pack.get("risk_catalog", {}).get("event_matrix", [])

    # ASIL分布
    asil_dist = defaultdict(int)
    all_safety_goals = set()

    for event in events:
        asil = event.get("expected_asil")
        asil_dist[asil if asil else "null"] += 1

        sg = event.get("safety_goal")
        if sg:
            all_safety_goals.add(sg)

    # 每个功能的详细信息
    functions_detail = []
    for func_id in failure_mode_rules.keys():
        data = extract_complete_function_data(func_id, pt_pack)
        functions_detail.append({
            "function_id": func_id,
            "function_name": data["function_name"],
            "failure_modes": len(data["failure_modes"]),
            "hazop_entries": len(data["hazop_entries"]),
            "hara_events": len(data["hara_events"]),
            "safety_goals": len(data["safety_goals"])
        })

    return {
        "total_functions": len(failure_mode_rules),
        "total_hazop_entries": len(hazop_patterns),
        "total_hara_events": len(events),
        "total_safety_goals": len(all_safety_goals),
        "asil_distribution": dict(asil_dist),
        "functions_detail": functions_detail
    }
