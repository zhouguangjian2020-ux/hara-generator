"""测试 BD 域命名白名单校验：
1. BD 域粗分类命名（如"位置灯丢失"）应被检出 warning
2. BD 域标准命名（如"位置灯无法点亮/位置灯点亮后熄灭"）不应报警
3. PT 域/其他域 entry 不受影响（不报警）
4. stage_write 导入与调用链正常
"""
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.hara_rules import (
    validate_bd_naming,
    validate_pt_gear_naming,
    _load_bd_whitelist,
)

print("=" * 70)
print("测试1: 加载 BD 白名单")
print("=" * 70)
wl = _load_bd_whitelist()
assert wl is not None, "BD 白名单未加载"
print(f"  total_anomalies: {wl.get('total_anomalies')}")
print(f"  coarse_keywords count: {len(wl.get('coarse_keywords', []))}")
print(f"  anomaly_list count: {len(wl.get('anomaly_list', []))}")
assert wl.get("total_anomalies") == 61
assert len(wl.get("coarse_keywords", [])) == 365
print("  PASS: 白名单加载正常")

print()
print("=" * 70)
print("测试2: BD 域粗分类命名应被检出 warning")
print("=" * 70)
s3_coarse = [
    {
        "func_id": "B_func_0001",
        "anomalies": [
            {"failure_id": "B_MF_0001_01", "failure_mode": "丢失", "description": "位置灯丢失"},
            {"failure_id": "B_MF_0001_02", "failure_mode": "非预期", "description": "位置灯非预期"},
        ],
    },
    {
        "func_id": "B_func_0002",
        "anomalies": [
            {"failure_id": "B_MF_0002_01", "failure_mode": "丢失", "description": "前雾灯丢失"},
        ],
    },
]
issues = validate_bd_naming(s3_coarse)
print(f"  issues count: {len(issues)}")
for lvl, msg in issues:
    print(f"  [{lvl}] {msg}")
assert len(issues) >= 3, "应检出 3 条粗分类 warning + 1 条条数不足"
assert any("位置灯丢失" in m for _, m in issues), "应检出'位置灯丢失'粗分类"
assert any("位置灯非预期" in m for _, m in issues), "应检出'位置灯非预期'粗分类"
assert any("前雾灯丢失" in m for _, m in issues), "应检出'前雾灯丢失'粗分类"
print("  PASS: 粗分类命名均被检出")

print()
print("=" * 70)
print("测试3: BD 域标准命名不应报警（除条数不足外）")
print("=" * 70)
# 取白名单前 45 条作为标准命名
std_items = wl["anomaly_list"][:45]
s3_std = []
for it in std_items:
    s3_std.append({
        "func_id": it["failure_id"].split("_MF_")[0] if "_MF_" in it["failure_id"] else "B_func_0001",
        "anomalies": [
            {"failure_id": it["failure_id"], "failure_mode": it["mode"], "description": it["anomaly"]}
        ],
    })
issues = validate_bd_naming(s3_std)
print(f"  issues count: {len(issues)}")
for lvl, msg in issues:
    print(f"  [{lvl}] {msg}")
# 标准命名不应有"粗分类"或"非标准命名"warning
non_count_warnings = [m for _, m in issues if "粗分类" in m or "非标准命名" in m]
assert len(non_count_warnings) == 0, f"标准命名不应报警，但有：{non_count_warnings}"
print("  PASS: 标准命名无粗分类/非标准命名 warning")

print()
print("=" * 70)
print("测试4: PT 域/其他域 entry 不受 BD 校验影响")
print("=" * 70)
s3_mixed = [
    {
        "func_id": "P_func_0001",
        "anomalies": [
            {"failure_id": "P_MF_0001_01", "failure_mode": "丢失", "description": "非预期换挡请求"},
        ],
    },
    {
        "func_id": "S_func_0001",  # 转向域
        "anomalies": [
            {"failure_id": "S_MF_0001_01", "failure_mode": "丢失", "description": "转向助力丢失"},
        ],
    },
    {
        "func_id": "E_func_0001",  # EPB域
        "anomalies": [
            {"failure_id": "E_MF_0001_01", "failure_mode": "丢失", "description": "接合丧失"},
        ],
    },
]
bd_issues = validate_bd_naming(s3_mixed)
print(f"  BD 校验 issues count: {len(bd_issues)}")
for lvl, msg in bd_issues:
    print(f"  [{lvl}] {msg}")
assert len(bd_issues) == 0, "非 BD 域 entry 不应触发 BD 校验"
print("  PASS: PT/转向/EPB 域均不受 BD 校验影响")

print()
print("=" * 70)
print("测试5: PT 域校验仍正常工作（验证未破坏 PT 逻辑）")
print("=" * 70)
pt_issues = validate_pt_gear_naming(s3_mixed)
print(f"  PT 校验 issues count: {len(pt_issues)}")
for lvl, msg in pt_issues:
    print(f"  [{lvl}] {msg}")
assert any("非预期换挡请求" in m for _, m in pt_issues), "PT 校验应检出粗分类'非预期换挡请求'"
print("  PASS: PT 域校验逻辑未被破坏")

print()
print("=" * 70)
print("测试6: stage_write 导入链正常")
print("=" * 70)
from stages import stage_write
assert hasattr(stage_write, "_validate_s1_vs_s2s3")
print("  PASS: stage_write 模块导入成功，_validate_s1_vs_s2s3 可用")

print()
print("=" * 70)
print("全部测试通过！")
print("=" * 70)
