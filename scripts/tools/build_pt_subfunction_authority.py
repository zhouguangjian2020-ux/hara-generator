#!/usr/bin/env python3
"""Build the runtime PT subfunction authority JSON from the offline Excel source."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from utils.pt_subfunction_source import (  # noqa: E402
    RUNTIME_FILENAME,
    SOURCE_FILENAME,
    SOURCE_SCHEMA,
    load_pt_subfunction_authority,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="从PT_子功能.XLSX生成运行时PT子功能权威JSON")
    parser.add_argument("input", type=Path, help="离线输入PT_子功能.XLSX")
    parser.add_argument(
        "--sheet",
        default=None,
        help="输入Excel中的工作表名称；不指定时使用第一个工作表",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("references/domain_packs") / RUNTIME_FILENAME,
        help="运行时JSON输出路径",
    )
    args = parser.parse_args()

    source = args.input.resolve()
    records, summary = load_pt_subfunction_authority(source, sheet_name=args.sheet)
    payload = {
        "schema_version": SOURCE_SCHEMA,
        "domain": "PT",
        "asset_type": "pt_subfunction_authority",
        "runtime": {
            "read_by": "scripts/utils/pt_subfunction_source.py",
            "source_of_truth": f"references/domain_packs/{RUNTIME_FILENAME}",
            "offline_source_only": SOURCE_FILENAME,
        },
        "source": {
            "source_file": summary.get("source_file") or SOURCE_FILENAME,
            "source_sheet": summary.get("source_sheet") or summary.get("sheet", ""),
            "source_sha256": summary.get("source_sha256"),
            "source_path_at_build": str(source),
        },
        "record_count": len(records),
        "function_count": len({record.function_key for record in records}),
        "conflict_count": summary.get("conflict_count", 0),
        "conflicts": summary.get("conflicts", []),
        "records": [record.as_dict() for record in records],
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PT authority build] records={len(records)}")
    print(f"[PT authority build] functions={len({record.function_key for record in records})}")
    print(f"[PT authority build] conflicts={summary.get('conflict_count', 0)}")
    print(f"[PT authority build] output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
