#!/usr/bin/env python3
"""
完整提取PT域两个Excel的所有关键信息，构建类似CS域的完整Domain Pack
参考CS域结构：
1. function_catalog - 功能目录和语义角色
2. analysis_catalog - 分析单元、失效模式规则、HAZOP模式
3. risk_catalog - 场景集合、事件矩阵
4. safety_goal_catalog - 危害族、安全目标、危害目录
"""

import json
import openpyxl
from pathlib import Path
from collections import defaultdict
import sys
import re

def get_cell_value(ws, row, col):
    """获取单元格值，处理合并单元格"""
    cell = ws.cell(row, col)
    for merged_range in ws.merged_cells.ranges:
        if cell.coordinate in merged_range:
            top_left_cell = ws.cell(merged_range.min_row, merged_range.min_col)
            return top_left_cell.value
    return cell.value

def normalize_text(text):
    """标准化文本"""
    if not text or not isinstance(text, str):
        return ""
    return " ".join(text.strip().split())

def find_header_row(ws, max_search_rows=10):
    """查找表头行"""
    key_headers = ["功能", "失效", "危害", "场景", "严重", "暴露", "可控", "ASIL"]

    for row in range(1, max_search_rows + 1):
        found_count = 0
        for col in range(1, min(ws.max_column + 1, 30)):
            cell_value = get_cell_value(ws, row, col)
            if cell_value:
                cell_text = str(cell_value)
                for key in key_headers:
                    if key in cell_text:
                        found_count += 1
                        break
        if found_count >= 4:
            return row
    return 2

def extract_failure_mode_sheet(ws):
    """
    提取失效模式表
    表头示例：整车功能ID | 整车功能 | 丢失 | 非预期 | ... | 选择理由
    """
    print(f"\n--- 提取失效模式表 ---")
    print(f"总行数: {ws.max_row}, 总列数: {ws.max_column}")

    header_row = find_header_row(ws)
    print(f"表头行: 第{header_row}行")

    # 读取表头
    headers = {}
    for col in range(1, ws.max_column + 1):
        val = get_cell_value(ws, header_row, col)
        if val:
            headers[col] = normalize_text(str(val))

    print(f"表头: {headers}")

    # 识别列
    col_func_id = None
    col_func_name = None
    col_reason = None
    failure_mode_cols = {}  # {col: mode_name}

    for col, header in headers.items():
        if "功能" in header and "ID" in header:
            col_func_id = col
        elif "功能" in header and "ID" not in header:
            col_func_name = col
        elif "理由" in header or "原因" in header:
            col_reason = col
        elif header in ["丢失", "非预期", "间歇", "过早", "过晚", "过多", "过少", "反向", "振荡", "部分", "卡滞"]:
            failure_mode_cols[col] = header

    print(f"功能ID列: {col_func_id}, 功能名列: {col_func_name}, 理由列: {col_reason}")
    print(f"失效模式列: {failure_mode_cols}")

    # 提取数据
    records = []
    for row in range(header_row + 1, ws.max_row + 1):
        func_id = get_cell_value(ws, row, col_func_id) if col_func_id else None
        func_name = get_cell_value(ws, row, col_func_name) if col_func_name else None

        if not func_name:
            continue

        func_id = str(func_id).strip() if func_id else ""
        func_name = normalize_text(func_name)
        reason = normalize_text(get_cell_value(ws, row, col_reason)) if col_reason else ""

        # 检查哪些失效模式被选中
        selected_modes = []
        for col, mode_name in failure_mode_cols.items():
            val = get_cell_value(ws, row, col)
            # 判断是否选中：√、✓、是、Y、y、或非空
            if val and str(val).strip() in ["√", "✓", "是", "Y", "y", "1", "有"]:
                selected_modes.append(mode_name)

        if selected_modes:
            records.append({
                "function_id": func_id,
                "function_name": func_name,
                "failure_modes": selected_modes,
                "reason": reason
            })

    print(f"提取到 {len(records)} 个功能的失效模式")
    for i, rec in enumerate(records[:3], 1):
        print(f"  {i}. {rec['function_name']}: {rec['failure_modes']}")

    return records

def extract_hazop_sheet(ws):
    """
    提取HAZOP分析表
    表头示例：整车功能ID | 整车功能 | 功能失效模式 | 功能异常表现 | 功能失效ID | 整车危害 | 关联HARA
    """
    print(f"\n--- 提取HAZOP表 ---")
    print(f"总行数: {ws.max_row}, 总列数: {ws.max_column}")

    header_row = find_header_row(ws)
    print(f"表头行: 第{header_row}行")

    headers = {}
    for col in range(1, ws.max_column + 1):
        val = get_cell_value(ws, header_row, col)
        if val:
            headers[col] = normalize_text(str(val))

    print(f"表头: {headers}")

    # 识别列
    col_map = {}
    for col, header in headers.items():
        if "功能" in header and "ID" in header and "失效" not in header:
            col_map["function_id"] = col
        elif "整车功能" in header and "ID" not in header:
            col_map["function"] = col
        elif "失效模式" in header:
            col_map["failure_mode"] = col
        elif "异常表现" in header or "功能异常" in header:
            col_map["anomaly"] = col
        elif "失效" in header and "ID" in header:
            col_map["failure_id"] = col
        elif "整车危害" in header:
            col_map["vehicle_hazard"] = col
        elif "关联" in header and "HARA" in header:
            col_map["associated_hara"] = col

    print(f"列映射: {col_map}")

    # 提取数据
    records = []
    current_func_id = None
    current_func = None

    for row in range(header_row + 1, ws.max_row + 1):
        func_id = get_cell_value(ws, row, col_map.get("function_id", 1))
        func = get_cell_value(ws, row, col_map.get("function", 2))

        # 处理合并单元格
        if func_id:
            current_func_id = str(func_id).strip()
        if func:
            current_func = normalize_text(func)

        failure_mode = get_cell_value(ws, row, col_map.get("failure_mode", 3))
        anomaly = get_cell_value(ws, row, col_map.get("anomaly", 4))

        if not failure_mode and not anomaly:
            continue

        record = {
            "function_id": current_func_id if current_func_id else "",
            "function": current_func if current_func else "",
            "failure_mode": normalize_text(failure_mode) if failure_mode else "",
            "anomaly": normalize_text(anomaly) if anomaly else "",
            "failure_id": str(get_cell_value(ws, row, col_map.get("failure_id", 5)) or "").strip(),
            "vehicle_hazard": normalize_text(get_cell_value(ws, row, col_map.get("vehicle_hazard", 6))),
            "associated_hara": str(get_cell_value(ws, row, col_map.get("associated_hara", 7)) or "").strip(),
        }
        records.append(record)

    print(f"提取到 {len(records)} 条HAZOP记录")
    for i, rec in enumerate(records[:3], 1):
        print(f"  {i}. {rec['function'][:30]} | {rec['failure_mode']} | {rec['failure_id']}")

    return records

def extract_hara_sheet(ws):
    """提取HARA分析表"""
    print(f"\n--- 提取HARA表 ---")
    print(f"总行数: {ws.max_row}, 总列数: {ws.max_column}")

    header_row = find_header_row(ws)
    print(f"表头行: 第{header_row}行")

    headers = {}
    for col in range(1, ws.max_column + 1):
        val = get_cell_value(ws, header_row, col)
        if val:
            headers[col] = normalize_text(str(val))

    # 识别列
    col_map = {}
    for col, header in headers.items():
        header_lower = header.lower()
        if "危害事件" in header and "id" in header_lower:
            col_map["hazard_id"] = col
        elif "整车功能" in header and "id" not in header_lower:
            col_map["function"] = col
        elif "功能失效" in header and "id" in header_lower:
            col_map["failure_id"] = col
        elif "异常表现" in header or "功能异常" in header:
            col_map["anomaly"] = col
        elif "整车危害" in header and "id" not in header_lower:
            col_map["vehicle_hazard"] = col
        elif "运行场景" in header or "驾驶" in header:
            col_map["scenario"] = col
        elif "危害事件描述" in header or ("描述" in header and "危害" in header):
            col_map["description"] = col
        elif "严重度" in header or (header.startswith("s") and len(header) <= 3):
            col_map["severity"] = col
        elif "s" in header_lower and "理由" in header:
            col_map["severity_reason"] = col
        elif "暴露" in header or ("e" in header_lower and "概率" in header):
            col_map["exposure"] = col
        elif "e" in header_lower and "理由" in header:
            col_map["exposure_reason"] = col
        elif "可控" in header or (header.startswith("c") and len(header) <= 5):
            col_map["controllability"] = col
        elif "c" in header_lower and "理由" in header:
            col_map["controllability_reason"] = col
        elif header.strip().upper() == "ASIL":
            col_map["asil"] = col
        elif "安全目标" in header and "id" in header_lower:
            col_map["safety_goal_id"] = col
        elif "安全目标" in header and "id" not in header_lower:
            col_map["safety_goal"] = col
        elif "安全状态" in header:
            col_map["safe_state"] = col
        elif "ftti" in header_lower:
            col_map["ftti"] = col

    print(f"列映射: {col_map}")

    # 提取数据
    records = []
    current_hazard_id = None
    current_function = None
    current_failure_id = None

    for row in range(header_row + 1, ws.max_row + 1):
        anomaly = get_cell_value(ws, row, col_map.get("anomaly", 4))
        vehicle_hazard = get_cell_value(ws, row, col_map.get("vehicle_hazard", 5))

        if not anomaly and not vehicle_hazard:
            continue

        hazard_id = get_cell_value(ws, row, col_map.get("hazard_id", 1))
        function = get_cell_value(ws, row, col_map.get("function", 2))
        failure_id = get_cell_value(ws, row, col_map.get("failure_id", 3))

        # 处理合并单元格
        if hazard_id:
            current_hazard_id = str(hazard_id).strip()
        if function:
            current_function = normalize_text(function)
        if failure_id:
            current_failure_id = str(failure_id).strip()

        def parse_sec(val):
            if val is None:
                return None
            val_str = str(val).strip()
            if val_str in ("", "-", "N/A", "n/a", "NA"):
                return None
            try:
                return int(float(val_str))
            except:
                return None

        severity = parse_sec(get_cell_value(ws, row, col_map.get("severity", 8)))
        exposure = parse_sec(get_cell_value(ws, row, col_map.get("exposure", 10)))
        controllability = parse_sec(get_cell_value(ws, row, col_map.get("controllability", 12)))

        asil_val = get_cell_value(ws, row, col_map.get("asil", 14))
        asil = str(asil_val).strip().upper() if asil_val and str(asil_val).strip() not in ("", "-", "N/A") else None

        record = {
            "hazard_id": current_hazard_id if current_hazard_id else "",
            "function": current_function if current_function else "",
            "failure_id": current_failure_id if current_failure_id else "",
            "anomaly": normalize_text(anomaly) if anomaly else "",
            "vehicle_hazard": normalize_text(vehicle_hazard) if vehicle_hazard else "",
            "scenario": normalize_text(get_cell_value(ws, row, col_map.get("scenario", 6))),
            "description": normalize_text(get_cell_value(ws, row, col_map.get("description", 7))),
            "severity": severity,
            "severity_reason": normalize_text(get_cell_value(ws, row, col_map.get("severity_reason", 9))),
            "exposure": exposure,
            "exposure_reason": normalize_text(get_cell_value(ws, row, col_map.get("exposure_reason", 11))),
            "controllability": controllability,
            "controllability_reason": normalize_text(get_cell_value(ws, row, col_map.get("controllability_reason", 13))),
            "asil": asil,
            "safety_goal_id": str(get_cell_value(ws, row, col_map.get("safety_goal_id", 15)) or "").strip(),
            "safety_goal": normalize_text(get_cell_value(ws, row, col_map.get("safety_goal", 16))),
            "safe_state": normalize_text(get_cell_value(ws, row, col_map.get("safe_state", 17))),
            "ftti": normalize_text(get_cell_value(ws, row, col_map.get("ftti", 18))),
        }
        records.append(record)

    print(f"提取到 {len(records)} 条HARA记录")
    return records

def extract_safety_goal_sheet(ws):
    """提取整车安全目标表"""
    print(f"\n--- 提取安全目标表 ---")
    print(f"总行数: {ws.max_row}, 总列数: {ws.max_column}")

    header_row = find_header_row(ws)
    print(f"表头行: 第{header_row}行")

    headers = {}
    for col in range(1, min(ws.max_column + 1, 20)):
        val = get_cell_value(ws, header_row, col)
        if val:
            headers[col] = normalize_text(str(val))

    print(f"表头: {headers}")

    # 识别列
    col_map = {}
    for col, header in headers.items():
        if "安全目标" in header and "ID" in header:
            col_map["sg_id"] = col
        elif "安全目标" in header and "ID" not in header and "描述" not in header:
            col_map["safety_goal"] = col
        elif "ASIL" in header:
            col_map["asil"] = col
        elif "安全状态" in header:
            col_map["safe_state"] = col
        elif "FTTI" in header.upper():
            col_map["ftti"] = col
        elif "危害事件" in header and "ID" in header:
            col_map["hazard_ids"] = col

    print(f"列映射: {col_map}")

    records = []
    for row in range(header_row + 1, ws.max_row + 1):
        sg_id = get_cell_value(ws, row, col_map.get("sg_id", 1))
        if not sg_id:
            continue

        record = {
            "sg_id": str(sg_id).strip(),
            "safety_goal": normalize_text(get_cell_value(ws, row, col_map.get("safety_goal", 2))),
            "asil": str(get_cell_value(ws, row, col_map.get("asil", 3)) or "").strip(),
            "safe_state": normalize_text(get_cell_value(ws, row, col_map.get("safe_state", 4))),
            "ftti": normalize_text(get_cell_value(ws, row, col_map.get("ftti", 5))),
            "hazard_ids": str(get_cell_value(ws, row, col_map.get("hazard_ids", 6)) or "").strip(),
        }
        records.append(record)

    print(f"提取到 {len(records)} 条安全目标记录")
    for i, rec in enumerate(records[:3], 1):
        print(f"  {i}. {rec['sg_id']}: {rec['safety_goal'][:50]}...")

    return records

def build_complete_domain_pack(failure_modes, hazop_records, hara_records, safety_goals):
    """
    构建完整的Domain Pack，参考CS域结构
    """
    print(f"\n{'='*60}")
    print("构建完整Domain Pack")
    print(f"{'='*60}")

    # 1. function_catalog
    functions = {}
    for fm in failure_modes:
        if fm["function_name"] not in functions:
            functions[fm["function_name"]] = {
                "function_id": fm["function_id"],
                "failure_modes": fm["failure_modes"]
            }

    print(f"功能数量: {len(functions)}")

    # 2. analysis_catalog - failure_mode_rules
    failure_mode_rules = {}
    for func_name, data in functions.items():
        key = data["function_id"] if data["function_id"] else func_name
        failure_mode_rules[key] = data["failure_modes"]

    # 3. analysis_catalog - hazop_patterns
    hazop_patterns = []
    for haz in hazop_records:
        if haz["failure_id"] and haz["failure_mode"]:
            # 提取hazard_family (从整车危害中归类)
            vehicle_hazard = haz["vehicle_hazard"]
            hazard_family = extract_hazard_family(vehicle_hazard)

            pattern = {
                "failure_mode": haz["failure_mode"],
                "failure_id": haz["failure_id"],
                "anomaly": haz["anomaly"],
                "hazard_family": hazard_family,
                "vehicle_hazard": vehicle_hazard
            }
            hazop_patterns.append(pattern)

    print(f"HAZOP模式数量: {len(hazop_patterns)}")

    # 4. risk_catalog - scenarios
    scenarios = {}
    scenario_counter = 1
    for rec in hara_records:
        if rec["scenario"] and rec["scenario"] not in scenarios:
            scenarios[rec["scenario"]] = f"PT_SC_{scenario_counter:02d}"
            scenario_counter += 1

    print(f"场景数量: {len(scenarios)}")

    # 5. risk_catalog - event_matrix
    event_matrix = []
    for rec in hara_records:
        scenario_id = scenarios.get(rec["scenario"], "PT_SC_99")
        event = {
            "analysis_unit_id": "PT_MAIN",
            "failure_id": rec["failure_id"],
            "scenario_id": scenario_id,
            "description": rec["description"],
            "severity": rec["severity"],
            "severity_reason": rec["severity_reason"],
            "exposure": rec["exposure"],
            "exposure_reason": rec["exposure_reason"],
            "controllability": rec["controllability"],
            "controllability_reason": rec["controllability_reason"],
            "expected_asil": rec["asil"],
            "safety_goal": rec["safety_goal"] if rec["safety_goal"] else None,
            "safe_state": rec["safe_state"] if rec["safe_state"] else None,
            "ftti": rec["ftti"] if rec["ftti"] else None,
            "vehicle_hazard_id": rec["hazard_id"],
        }
        event_matrix.append(event)

    print(f"事件矩阵记录数: {len(event_matrix)}")

    # 6. safety_goal_catalog
    hazard_families = {}
    hazard_catalog = {}

    # 从HARA记录中提取危害族
    for rec in hara_records:
        if rec["hazard_id"] and rec["vehicle_hazard"]:
            hazard_family = extract_hazard_family(rec["vehicle_hazard"])

            hazard_catalog[rec["hazard_id"]] = {
                "hazard_family": hazard_family,
                "vehicle_hazard": rec["vehicle_hazard"]
            }

            # 收集同一hazard_family的安全目标
            if hazard_family not in hazard_families and rec["safety_goal"]:
                hazard_families[hazard_family] = {
                    "canonical_safety_goal": rec["safety_goal"],
                    "safe_state": rec["safe_state"] if rec["safe_state"] else None,
                    "ftti": rec["ftti"] if rec["ftti"] else None,
                    "canonical_hazard": rec["vehicle_hazard"]
                }

    print(f"危害族数量: {len(hazard_families)}")
    print(f"危害目录记录数: {len(hazard_catalog)}")

    # 组装Domain Pack
    domain_pack = {
        "schema_version": "1.0",
        "domain": "PT",
        "domain_name": "动力域",
        "status": "pt_complete_extraction",
        "provenance": {
            "migration_stage": "P0-1",
            "risk_matrix_source": "数据组5 Excel文件",
            "extraction_policy": "完整提取失效模式、HAZOP、HARA、安全目标，正确处理合并单元格",
            "extraction_date": "2026-08-26"
        },
        "function_catalog": {
            "function_matchers": [],
            "semantic_roles": {},
            "scope_rules": []
        },
        "analysis_catalog": {
            "analysis_units": [],
            "failure_mode_rules": failure_mode_rules,
            "hazop_patterns": hazop_patterns
        },
        "risk_catalog": {
            "scenario_sets": {
                "pt_main": {
                    "scenarios": [
                        {"scenario_id": sid, "text": stext}
                        for stext, sid in sorted(scenarios.items(), key=lambda x: x[1])
                    ]
                }
            },
            "scenario_lookup": {},
            "event_matrix": event_matrix
        },
        "safety_goal_catalog": {
            "hazard_families": hazard_families,
            "rules": [
                {
                    "deduplication_key": [
                        "domain",
                        "hazard_family",
                        "canonical_safety_goal",
                        "safe_state",
                        "ftti"
                    ]
                }
            ],
            "hazard_catalog": hazard_catalog
        }
    }

    return domain_pack

def extract_hazard_family(vehicle_hazard):
    """
    从整车危害描述中提取危害族
    """
    if not vehicle_hazard:
        return "unknown"

    vh = vehicle_hazard.lower()

    # 纵向运动相关
    if any(word in vh for word in ["加速", "减速", "制动", "纵向", "速度", "扭矩"]):
        if "非预期" in vh or "意外" in vh:
            return "unexpected_longitudinal_motion"
        elif "丢失" in vh or "失效" in vh or "无法" in vh:
            return "loss_of_longitudinal_control"
        else:
            return "longitudinal_motion"

    # 横向运动相关
    if any(word in vh for word in ["转向", "横向", "方向"]):
        if "非预期" in vh:
            return "unexpected_lateral_motion"
        elif "失控" in vh:
            return "loss_of_lateral_control"
        else:
            return "lateral_motion"

    # 能量/电气相关
    if any(word in vh for word in ["能量", "电", "充电", "电池"]):
        return "energy_management"

    # 信息显示相关
    if any(word in vh for word in ["显示", "信息", "提示"]):
        return "information_display"

    return "other"

def main():
    sys.stdout.reconfigure(encoding='utf-8')

    # 两个Excel文件路径
    excel1_path = r"C:\Users\zhouguangjian\Downloads\FUSA HARA PT DOMAIN_V1.1_山子高科 - 副本.xlsx"
    excel2_path = r"E:\Git projects\hara-generator-skill\数据组\数据组5\ALL-HARA_FUSA-100-GPFA-H002______FUSA HARA PT DOMAIN.xlsx"

    # 先尝试读取第一个文件
    print(f"{'='*60}")
    print(f"读取Excel文件1: {excel1_path}")
    print(f"{'='*60}")

    wb1 = openpyxl.load_workbook(excel1_path, data_only=True)
    print(f"工作表: {wb1.sheetnames}")

    # 提取各个表
    failure_modes = []
    hazop_records = []
    hara_records = []
    safety_goals = []

    for name in wb1.sheetnames:
        if "失效模式" in name:
            failure_modes = extract_failure_mode_sheet(wb1[name])
        elif "HAZOP" in name and "分析" in name:
            hazop_records = extract_hazop_sheet(wb1[name])
        elif "HARA" in name and "分析" in name:
            hara_records = extract_hara_sheet(wb1[name])
        elif "安全目标" in name:
            safety_goals = extract_safety_goal_sheet(wb1[name])

    # 如果第二个文件存在，也提取
    if Path(excel2_path).exists():
        print(f"\n{'='*60}")
        print(f"读取Excel文件2: {excel2_path}")
        print(f"{'='*60}")

        wb2 = openpyxl.load_workbook(excel2_path, data_only=True)
        print(f"工作表: {wb2.sheetnames}")

        for name in wb2.sheetnames:
            if "失效模式" in name and not failure_modes:
                failure_modes = extract_failure_mode_sheet(wb2[name])
            elif "HAZOP" in name and "分析" in name and not hazop_records:
                hazop_records = extract_hazop_sheet(wb2[name])
            elif "HARA" in name and "分析" in name and not hara_records:
                hara_records = extract_hara_sheet(wb2[name])
            elif "安全目标" in name and not safety_goals:
                safety_goals = extract_safety_goal_sheet(wb2[name])

    # 构建Domain Pack
    domain_pack = build_complete_domain_pack(failure_modes, hazop_records, hara_records, safety_goals)

    # 保存
    output_path = Path("references/domain_packs/PT_complete.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(domain_pack, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"完整Domain Pack已保存到: {output_path}")
    print(f"{'='*60}")

    # 统计信息
    print(f"\n最终统计:")
    print(f"  失效模式规则: {len(domain_pack['analysis_catalog']['failure_mode_rules'])} 个功能")
    print(f"  HAZOP模式: {len(domain_pack['analysis_catalog']['hazop_patterns'])} 条")
    print(f"  场景: {len(domain_pack['risk_catalog']['scenario_sets']['pt_main']['scenarios'])} 个")
    print(f"  事件矩阵: {len(domain_pack['risk_catalog']['event_matrix'])} 条")
    print(f"  危害族: {len(domain_pack['safety_goal_catalog']['hazard_families'])} 个")
    print(f"  危害目录: {len(domain_pack['safety_goal_catalog']['hazard_catalog'])} 条")

if __name__ == "__main__":
    main()
