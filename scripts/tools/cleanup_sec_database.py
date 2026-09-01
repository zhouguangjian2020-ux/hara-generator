# -*- coding: utf-8 -*-
"""
SEC参考数据库清理脚本
=====================
清理 sec_reference_database.json 中的无效记录：
1. S/E/C 全为 None 的记录（列偏移或提取失败产生的垃圾数据）
2. scenario 为 "头脑风暴 项目经验" 等来源标注文本的记录（不是真实场景）
3. 重建 scenario_index 和统计字段

使用方法：
    python scripts/tools/cleanup_sec_database.py [--dry-run]

注意：直接覆盖 references/sec_reference_database.json，操作前会自动备份。
"""
import json
import shutil
import argparse
from pathlib import Path
from collections import defaultdict


DB_PATH = Path(__file__).parent.parent.parent / "references" / "sec_reference_database.json"


def is_valid_record(rec: dict) -> bool:
    """判断一条记录是否有效。"""
    # S/E/C 全为 None → 无效（提取失败）
    s = rec.get("s")
    e = rec.get("e")
    c = rec.get("c")
    if s is None and e is None and c is None:
        return False

    # scenario 是来源标注文本 → 无效
    scenario = str(rec.get("scenario", "")).strip()
    invalid_scenario_markers = ["头脑风暴", "项目经验", "专家判断", "标准参考", "文献"]
    if any(marker in scenario for marker in invalid_scenario_markers):
        return False

    # scenario 为空 → 无效
    if not scenario:
        return False

    # S 不是有效数字（0-3） → 无效
    if s is not None:
        try:
            s_val = int(s)
            if not (0 <= s_val <= 3):
                return False
        except (ValueError, TypeError):
            return False

    # E 不是有效数字（1-4）→ 无效（S=0时E可以是None）
    if e is not None:
        try:
            e_val = int(e)
            if not (1 <= e_val <= 4):
                return False
        except (ValueError, TypeError):
            return False

    # C 不是有效数字（0-3）→ 无效（S=0时C可以是None）
    if c is not None:
        try:
            c_val = int(c)
            if not (0 <= c_val <= 3):
                return False
        except (ValueError, TypeError):
            return False

    return True


def build_scenario_index(records: list) -> dict:
    """从记录列表重建 scenario_index。"""
    index = defaultdict(list)
    for rec in records:
        scen = rec.get("scenario", "")
        if scen:
            index[scen].append(rec)
    return dict(index)


def domain_stats(records: list) -> dict:
    """按域统计记录数。"""
    stats = defaultdict(int)
    for r in records:
        stats[r.get("domain_tag", "unknown")] += 1
    return dict(stats)


def main():
    parser = argparse.ArgumentParser(description="清理SEC参考数据库")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不写入")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"错误: 数据库文件不存在: {DB_PATH}")
        return

    # 加载
    with open(DB_PATH, encoding="utf-8") as f:
        db = json.load(f)

    records = db.get("all_records", [])
    old_count = len(records)
    old_domains = domain_stats(records)

    # 过滤
    valid_records = [r for r in records if is_valid_record(r)]
    new_count = len(valid_records)
    new_domains = domain_stats(valid_records)
    removed = old_count - new_count

    print(f"原始记录数: {old_count}")
    print(f"有效记录数: {new_count}")
    print(f"移除记录数: {removed} ({removed/old_count*100:.1f}%)")

    print(f"\n各域记录数变化:")
    all_domains = sorted(set(list(old_domains.keys()) + list(new_domains.keys())))
    for d in all_domains:
        old = old_domains.get(d, 0)
        new = new_domains.get(d, 0)
        pct = f"{new/old*100:.0f}%" if old > 0 else "N/A"
        status = "✓" if new > 0 else "✗ 已清空"
        print(f"  {d.upper():10s} {old:4d} → {new:4d} ({pct}) {status}")

    # 唯一场景数
    unique_scenarios = len(set(r.get("scenario", "") for r in valid_records if r.get("scenario")))
    print(f"\n唯一有效场景数: {unique_scenarios}")

    if args.dry_run:
        print("\n[dry-run] 未写入文件")
        return

    # 备份
    backup_path = DB_PATH.with_suffix(".json.bak_before_cleanup")
    shutil.copy2(DB_PATH, backup_path)
    print(f"\n已备份到: {backup_path}")

    # 重建索引
    scenario_index = build_scenario_index(valid_records)

    # 更新数据库
    db["all_records"] = valid_records
    db["total_records"] = new_count
    db["unique_scenarios"] = unique_scenarios
    db["scenario_index"] = scenario_index
    if "version" in db:
        db["version"] = str(float(db.get("version", "1.0")) + 0.1)
    db["cleanup_note"] = f"cleanup: removed {removed} invalid records (None SEC values / source-annotation scenarios)"

    # 写入
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print(f"已写入: {DB_PATH}")
    print("清理完成 ✓")


if __name__ == "__main__":
    main()
