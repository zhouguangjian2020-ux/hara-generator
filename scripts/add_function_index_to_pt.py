#!/usr/bin/env python3
"""
为PT_complete.json添加功能索引

从HAZOP和HARA数据中提取功能名，建立function_id到function_name的映射
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

# 项目根目录
ROOT = Path(__file__).parent.parent
PT_PACK_PATH = ROOT / "references" / "domain_packs" / "PT_complete.json"


def extract_function_names_from_hazop(hazop_patterns: list) -> dict[str, set]:
    """从HAZOP patterns中提取功能名"""
    func_to_names = defaultdict(set)

    for pattern in hazop_patterns:
        failure_id = pattern.get("failure_id", "")
        if not failure_id:
            continue

        # 从failure_id提取功能ID: P_MF_0001_01 -> P_func_0001
        parts = failure_id.split("_")
        if len(parts) >= 3 and parts[1] == "MF":
            func_id = f"P_func_{parts[2]}"

            # 尝试从anomaly或vehicle_hazard中推断功能名
            anomaly = pattern.get("anomaly", "")
            hazard = pattern.get("vehicle_hazard", "")

            # 简单启发式：从描述中提取关键词
            for text in [anomaly, hazard]:
                if "档位" in text:
                    func_to_names[func_id].add("档位控制及显示功能")
                elif "制动" in text:
                    func_to_names[func_id].add("制动功能")
                elif "驱动" in text:
                    func_to_names[func_id].add("驱动功能")
                elif "换挡" in text or "换档" in text:
                    func_to_names[func_id].add("换挡功能")

    return func_to_names


def extract_function_names_from_hara(events: list) -> dict[str, set]:
    """从HARA events中提取功能名"""
    func_to_names = defaultdict(set)

    for event in events:
        failure_id = event.get("failure_id", "")
        if not failure_id:
            continue

        parts = failure_id.split("_")
        if len(parts) >= 3 and parts[1] == "MF":
            func_id = f"P_func_{parts[2]}"

            # 从描述中推断
            description = event.get("description", "")

            if "档位" in description or "挂挡" in description:
                func_to_names[func_id].add("档位控制及显示功能")
            elif "制动" in description or "刹车" in description:
                func_to_names[func_id].add("制动功能")
            elif "驱动" in description:
                func_to_names[func_id].add("驱动功能")
            elif "换挡" in description or "换档" in description:
                func_to_names[func_id].add("换挡功能")

    return func_to_names


def build_function_index(pt_pack: dict) -> dict[str, str]:
    """
    构建功能索引

    返回: {function_id: function_name}
    """
    hazop_patterns = pt_pack.get("analysis_catalog", {}).get("hazop_patterns", [])
    events = pt_pack.get("risk_catalog", {}).get("event_matrix", [])
    failure_mode_rules = pt_pack.get("analysis_catalog", {}).get("failure_mode_rules", {})

    # 从HAZOP和HARA中提取
    func_names_hazop = extract_function_names_from_hazop(hazop_patterns)
    func_names_hara = extract_function_names_from_hara(events)

    # 合并两个来源
    func_index = {}

    for func_id in failure_mode_rules.keys():
        names = set()
        names.update(func_names_hazop.get(func_id, []))
        names.update(func_names_hara.get(func_id, []))

        if names:
            # 如果有多个候选名，选择最长的（通常最具体）
            func_index[func_id] = max(names, key=len)
        else:
            # 没有找到，使用默认名
            func_index[func_id] = f"未知功能{func_id}"

    return func_index


def add_function_index(pt_pack_path: Path):
    """为PT pack添加功能索引"""
    print(f"读取 {pt_pack_path.name}...")

    with open(pt_pack_path, 'r', encoding='utf-8') as f:
        pt_pack = json.load(f)

    # 构建索引
    print("构建功能索引...")
    func_index = build_function_index(pt_pack)

    print(f"\n找到 {len(func_index)} 个功能:")
    for func_id, func_name in sorted(func_index.items()):
        print(f"  {func_id}: {func_name}")

    # 添加到function_catalog
    if "function_catalog" not in pt_pack:
        pt_pack["function_catalog"] = {}

    pt_pack["function_catalog"]["function_index"] = func_index

    # 更新provenance
    if "provenance" not in pt_pack:
        pt_pack["provenance"] = {}

    pt_pack["provenance"]["function_index_added"] = "2026-08-26"
    pt_pack["provenance"]["function_index_method"] = "从HAZOP和HARA数据中提取功能名"

    # 写回文件
    print(f"\n保存到 {pt_pack_path.name}...")
    with open(pt_pack_path, 'w', encoding='utf-8') as f:
        json.dump(pt_pack, f, ensure_ascii=False, indent=2)

    print("✓ 完成")


if __name__ == "__main__":
    add_function_index(PT_PACK_PATH)
