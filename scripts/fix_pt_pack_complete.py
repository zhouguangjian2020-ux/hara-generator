#!/usr/bin/env python3
"""
完整修复PT_complete.json，使其通过Domain Pack验证
参考CS.json的结构，补全所有必需字段
"""

import json
import sys
from pathlib import Path

# 设置输出编码
sys.stdout.reconfigure(encoding='utf-8')

# 读取PT pack
pt_pack_path = Path("references/domain_packs/PT_complete.json")
with open(pt_pack_path, "r", encoding="utf-8") as f:
    pack = json.load(f)

print("开始完整修复PT Domain Pack...\n")

# 1. 修复 quality_contract
pack["quality_contract"] = {
    "mode": "compatible",
    "known_exact_requirements": [],
    "unknown_policy": "工程师必须明确填写并提交审批"
}
print("✓ 添加 quality_contract")

# 2. 清理 hazop_patterns 中的问题
# 从event_matrix构建 failure_id → vehicle_hazard_id 映射
failure_to_hazard = {}
for event in pack["risk_catalog"]["event_matrix"]:
    fid = event.get("failure_id")
    hid = event.get("vehicle_hazard_id")
    if fid and hid:
        if fid not in failure_to_hazard:
            failure_to_hazard[fid] = set()
        failure_to_hazard[fid].add(hid)

# 修复每个hazop_pattern
hazop_patterns = pack["analysis_catalog"]["hazop_patterns"]
hazard_catalog = pack["safety_goal_catalog"]["hazard_catalog"]

fixed_hazop = 0
removed_hazop = []

for i, pattern in enumerate(hazop_patterns):
    fid = pattern.get("failure_id", "")

    # 如果没有vehicle_hazard_id，尝试从映射中获取
    if not pattern.get("vehicle_hazard_id"):
        if fid in failure_to_hazard:
            # 取第一个
            pattern["vehicle_hazard_id"] = list(failure_to_hazard[fid])[0]
            fixed_hazop += 1
        else:
            # 标记为待移除
            removed_hazop.append(i)
            continue

    # 确保vehicle_hazard_id在catalog中
    vh_id = pattern.get("vehicle_hazard_id", "")
    if vh_id and vh_id not in hazard_catalog:
        # 添加到catalog
        hazard_catalog[vh_id] = {
            "hazard_family": pattern.get("hazard_family", "other"),
            "vehicle_hazard": pattern.get("vehicle_hazard", "")
        }

    # 确保hazard_family一致
    if vh_id in hazard_catalog:
        catalog_family = hazard_catalog[vh_id]["hazard_family"]
        pattern["hazard_family"] = catalog_family

# 移除无效的hazop_patterns
for idx in reversed(removed_hazop):
    hazop_patterns.pop(idx)

print(f"✓ 修复HAZOP模式: {fixed_hazop} 个, 移除无效: {len(removed_hazop)} 个")

# 3. 清理 event_matrix 中的问题
# 构建有效的failure_id集合（从HAZOP patterns）
valid_failure_ids = {p["failure_id"] for p in hazop_patterns if p.get("failure_id")}

event_matrix = pack["risk_catalog"]["event_matrix"]
cleaned_events = []
removed_events = 0

for event in event_matrix:
    fid = event.get("failure_id", "")

    # 跳过功能组标题行（没有有效的failure_id）
    if not fid or "func_" in fid.lower() and "mf_" not in fid.lower():
        removed_events += 1
        continue

    # 确保vehicle_hazard_id在catalog中
    vh_id = event.get("vehicle_hazard_id", "")
    if vh_id and vh_id not in hazard_catalog:
        # 从整车危害描述推断hazard_family
        vh_text = ""
        for pattern in hazop_patterns:
            if pattern.get("failure_id") == fid:
                vh_text = pattern.get("vehicle_hazard", "")
                break

        family = extract_hazard_family(vh_text) if vh_text else "other"
        hazard_catalog[vh_id] = {
            "hazard_family": family,
            "vehicle_hazard": vh_text
        }

    cleaned_events.append(event)

pack["risk_catalog"]["event_matrix"] = cleaned_events
print(f"✓ 清理事件矩阵: 保留 {len(cleaned_events)} 条, 移除 {removed_events} 条")

# 4. 确保所有hazard_family都有定义
hazard_families = pack["safety_goal_catalog"]["hazard_families"]
all_families = set()

for hdata in hazard_catalog.values():
    all_families.add(hdata["hazard_family"])

for family in all_families:
    if family not in hazard_families:
        # 从event_matrix中查找该family的第一个安全目标
        sg = None
        ss = None
        ftti = None
        vh = None

        for event in cleaned_events:
            vh_id = event.get("vehicle_hazard_id", "")
            if vh_id in hazard_catalog and hazard_catalog[vh_id]["hazard_family"] == family:
                if event.get("safety_goal"):
                    sg = event["safety_goal"]
                    ss = event.get("safe_state")
                    ftti = event.get("ftti")
                    vh = hazard_catalog[vh_id]["vehicle_hazard"]
                    break

        hazard_families[family] = {
            "canonical_safety_goal": sg,
            "safe_state": ss,
            "ftti": ftti,
            "canonical_hazard": vh or family
        }

print(f"✓ 危害族定义: {len(hazard_families)} 个")

# 5. 更新状态和provenance
pack["status"] = "pt_risk_matrix_seeded"
pack["provenance"]["fix_date"] = "2026-08-26"
pack["provenance"]["fix_policy"] = "修复验证错误，清理无效数据，补全必需字段"

# 保存
with open(pt_pack_path, "w", encoding="utf-8") as f:
    json.dump(pack, f, ensure_ascii=False, indent=2)

print(f"\n修复完成！")
print(f"最终统计:")
print(f"  失效模式规则: {len(pack['analysis_catalog']['failure_mode_rules'])} 个功能")
print(f"  HAZOP模式: {len(pack['analysis_catalog']['hazop_patterns'])} 条")
print(f"  事件矩阵: {len(pack['risk_catalog']['event_matrix'])} 条")
print(f"  危害族: {len(pack['safety_goal_catalog']['hazard_families'])} 个")
print(f"  危害目录: {len(pack['safety_goal_catalog']['hazard_catalog'])} 条")

def extract_hazard_family(vehicle_hazard):
    """从整车危害中提取危害族"""
    if not vehicle_hazard:
        return "other"

    vh = vehicle_hazard.lower()

    if any(w in vh for w in ["加速", "减速", "制动", "纵向", "速度", "扭矩"]):
        if "非预期" in vh:
            return "unexpected_longitudinal_motion"
        elif "丢失" in vh or "失效" in vh:
            return "loss_of_longitudinal_control"
        return "longitudinal_motion"

    if any(w in vh for w in ["转向", "横向", "方向"]):
        if "非预期" in vh:
            return "unexpected_lateral_motion"
        return "lateral_motion"

    if any(w in vh for w in ["能量", "电", "充电"]):
        return "energy_management"

    if any(w in vh for w in ["显示", "信息"]):
        return "information_display"

    return "other"
