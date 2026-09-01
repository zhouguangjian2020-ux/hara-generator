#!/usr/bin/env python3
"""从新 PT.XLSX 重建 PT Domain Pack 候选和可信案例库。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from utils.pt_domain_pack_builder import build_pt_pack_from_xlsx  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="从工程师审核的 PT.XLSX 重建 PT Domain Pack")
    parser.add_argument("input", type=Path)
    parser.add_argument("--pack-output", type=Path, default=Path("temp/PT.generated.candidate.json"))
    parser.add_argument("--report-output", type=Path, default=Path("temp/PT.generated.report.json"))
    parser.add_argument("--template", type=Path, default=Path("references/domain_packs/PT.json"))
    args = parser.parse_args()

    inventory, pack, report = build_pt_pack_from_xlsx(args.input, semantic_template_path=args.template)
    # 单套运行时资产：语义案例索引已经嵌入 pack["case_library_catalog"]。
    # 不再额外写出 PT_reference_case_library.v2.json 或 PT_cases_v2.json。
    for target in (args.pack_output, args.report_output):
        target.parent.mkdir(parents=True, exist_ok=True)
    args.pack_output.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PT pack build] source_records={report['summary']['source_record_count']}")
    print(f"[PT pack build] reference_events={report['summary']['reference_event_count']}")
    print(f"[PT pack build] exact_events={report['summary']['exact_event_count']}")
    print(f"[PT pack build] trusted_variants={report['summary']['trusted_variant_count']}")
    print(f"[PT pack build] pack={args.pack_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
