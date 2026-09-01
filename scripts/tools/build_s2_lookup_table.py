# build_s2_lookup_table.py
# 从 extract_s2_from_excel.py 的输出 JSON 构建参考案例库
# 不做去重合并：同名功能在不同项目中有不同模式选择时，保留所有记录
# 补充 s2-failure-modes.md 中 10 个 few-shot 案例的独有功能
# 用法: python scripts/tools/build_s2_lookup_table.py

import json
from pathlib import Path

FAILURE_MODES = [
    "丢失", "非预期", "间歇", "过多", "过少",
    "过早", "反向", "振荡", "部分", "过晚", "卡滞",
]

# 10 个 few-shot 案例中可能不在参考 Excel 里的功能
FEW_SHOT_EXTRA = [
    {
        "domain": "*",
        "func_name": "增程控制系统",
        "selected_modes": ["丢失", "过多"],
        "reason": "增程控制只涉及状态检测与控制，不涉及数值方向变化",
        "source": "few_shot",
    },
]


def main():
    base_dir = Path(__file__).resolve().parent.parent.parent
    input_path = base_dir / "references" / "s2-lookup-table.json"
    output_path = base_dir / "references" / "s2-lookup-table.json"

    with open(input_path, "r", encoding="utf-8") as f:
        raw_entries = json.load(f)

    # 转换为参考案例格式，每条独立保留（不合并同名）
    cases = []
    seen = set()  # 用于判断 few-shot 是否已存在于参考 Excel

    for entry in raw_entries:
        case = {
            "domain": entry["domain"],
            "func_name": entry["func_name"],
            "selected_modes": entry["selected_modes"],
            "reason": entry["reason"],
            "groups": [entry["group"]],
            "source": "reference_excel",
        }
        cases.append(case)
        seen.add((entry["domain"], entry["func_name"]))

    # 补充 few-shot 独有功能（不在参考 Excel 中的）
    for fs in FEW_SHOT_EXTRA:
        key = (fs["domain"], fs["func_name"])
        if key not in seen:
            cases.append({
                "domain": fs["domain"],
                "func_name": fs["func_name"],
                "selected_modes": fs["selected_modes"],
                "reason": fs["reason"],
                "groups": [],
                "source": "few_shot",
            })
            seen.add(key)

    # 按 domain → func_name 排序
    cases.sort(key=lambda x: (x["domain"], x["func_name"], x["groups"][0] if x["groups"] else 0))

    # 统计
    print("=== S2 参考案例库统计 ===")
    print(f"总记录数: {len(cases)}")

    by_source = {}
    for r in cases:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    print(f"来源分布: {by_source}")

    by_domain = {}
    for r in cases:
        by_domain[r["domain"]] = by_domain.get(r["domain"], 0) + 1
    print(f"域分布: {dict(sorted(by_domain.items()))}")

    # 找同名不同选择的记录（保留所有，仅报告）
    name_map = {}
    for c in cases:
        key = (c["domain"], c["func_name"])
        name_map.setdefault(key, []).append(c)

    conflicts = {k: v for k, v in name_map.items() if len({tuple(c["selected_modes"]) for c in v}) > 1}
    if conflicts:
        print(f"\n⚠️ {len(conflicts)} 个同名功能在不同项目中模式不同（保留所有记录供 Agent 参考）:")
        for (d, n), entries in conflicts.items():
            print(f"  {d} / {n}:")
            for e in entries:
                print(f"    数据组{e['groups']} → {','.join(e['selected_modes'])}")
    else:
        print(f"\n✅ 无冲突")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)
    print(f"\n输出: {output_path}")

    # 打印完整表
    print(f"\n=== 完整参考案例库 ({len(cases)} 条) ===")
    print(f"{'域':6s} | {'功能名':30s} | {'选中模式':50s} | {'数据组':10s} | 来源")
    print("-" * 120)
    for r in cases:
        modes_str = ",".join(r["selected_modes"])
        groups_str = ",".join(str(g) for g in r["groups"]) if r["groups"] else "-"
        print(f"{r['domain']:6s} | {r['func_name']:30s} | {modes_str:50s} | {groups_str:10s} | {r['source']}")


if __name__ == "__main__":
    main()
