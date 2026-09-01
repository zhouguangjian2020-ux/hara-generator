#!/usr/bin/env python3
"""将 PT 单 Sheet 扁平案例表导入为可审计的参考案例资产。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from utils.pt_case_source import import_case_table  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导入 PT.XLSX 案例表并生成规范化参考资产")
    parser.add_argument("input", type=Path, help="输入 Excel 文件")
    parser.add_argument("--sheet", default=None, help="Sheet 名，默认优先使用‘案例表’")
    parser.add_argument("--output", "-o", type=Path, default=Path("output/pt_reference_inventory.json"))
    parser.add_argument("--audit", "-a", type=Path, default=Path("output/pt_reference_audit.json"))
    args = parser.parse_args()

    inventory, audit = import_case_table(args.input, sheet_name=args.sheet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PT import] records={audit['record_count']}")
    print(f"[PT import] output={args.output}")
    print(f"[PT import] audit={args.audit}")
    print(f"[PT import] raw_failure_id_cross_family_conflicts={len(audit['raw_failure_id_cross_family_conflicts'])}")
    print(f"[PT import] unresolved_function_rows={len(audit['unresolved_function_rows'])}")
    print(f"[PT import] unresolved_hazard_rows={len(audit['unresolved_hazard_rows'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
