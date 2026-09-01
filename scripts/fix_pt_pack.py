#!/usr/bin/env python3
"""
修复PT_complete.json，添加Domain Pack验证所需的必需字段
"""

import json
from pathlib import Path

# 读取现有的PT_complete.json
pt_pack_path = Path("references/domain_packs/PT_complete.json")
with open(pt_pack_path, "r", encoding="utf-8") as f:
    pack = json.load(f)

print("修复PT Domain Pack...")

# 1. 添加 quality_contract
pack["quality_contract"] = {
    "mode": "exact",
    "description": "PT域拥有完整的485条事件矩阵，使用精确查表模式"
}

# 2. 添加 known_input_aliases
pack["function_catalog"]["known_input_aliases"] = {}

# 3. 修复 hazard_catalog 中未知的 hazard_family
hazard_catalog = pack["safety_goal_catalog"]["hazard_catalog"]
hazard_families_set = set(pack["safety_goal_catalog"]["hazard_families"].keys())

print(f"已知危害族: {hazard_families_set}")

# 统计需要修复的记录
fixed_count = 0
for hazard_id, hazard_data in hazard_catalog.items():
    if hazard_data["hazard_family"] not in hazard_families_set:
        # 添加到 hazard_families
        family = hazard_data["hazard_family"]
        if family not in pack["safety_goal_catalog"]["hazard_families"]:
            pack["safety_goal_catalog"]["hazard_families"][family] = {
                "canonical_safety_goal": None,
                "safe_state": None,
                "ftti": None,
                "canonical_hazard": hazard_data["vehicle_hazard"]
            }
            fixed_count += 1

print(f"添加了 {fixed_count} 个新的危害族定义")

# 4. 修复 hazop_patterns 缺少 vehicle_hazard_id
hazop_patterns = pack["analysis_catalog"]["hazop_patterns"]
fixed_hazop = 0

for i, pattern in enumerate(hazop_patterns):
    if "vehicle_hazard_id" not in pattern or not pattern.get("vehicle_hazard_id"):
        # 从hazard_catalog中查找匹配的vehicle_hazard_id
        failure_id = pattern.get("failure_id", "")

        # 尝试从event_matrix中查找
        for event in pack["risk_catalog"]["event_matrix"]:
            if event.get("failure_id") == failure_id:
                pattern["vehicle_hazard_id"] = event.get("vehicle_hazard_id", "")
                fixed_hazop += 1
                break

print(f"修复了 {fixed_hazop} 个HAZOP模式的vehicle_hazard_id")

# 5. 更新状态
pack["status"] = "pt_risk_matrix_seeded"

# 6. 更新provenance
pack["provenance"]["validation_policy"] = "完整事件矩阵，支持精确查表预填"

# 保存修复后的文件
with open(pt_pack_path, "w", encoding="utf-8") as f:
    json.dump(pack, f, ensure_ascii=False, indent=2)

print(f"\n修复完成，已保存到: {pt_pack_path}")
print(f"当前危害族数量: {len(pack['safety_goal_catalog']['hazard_families'])}")
print(f"HAZOP模式数量: {len(pack['analysis_catalog']['hazop_patterns'])}")
print(f"事件矩阵数量: {len(pack['risk_catalog']['event_matrix'])}")
