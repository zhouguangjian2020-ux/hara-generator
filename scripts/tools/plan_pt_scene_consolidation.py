"""只读验证 PT 场景单源收敛方案；本脚本不会写入任何文件。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.pt_scene_consolidation import (
    build_pt_single_source_candidate,
    extract_expanded_pt_reference_events,
    validate_pt_single_source_candidate,
)

DEFAULT_WORKBOOK = ROOT / "关键数据" / "PT域" / "ALL-HARA_FUSA-100-GPFA-H002______FUSA HARA PT DOMAIN.xlsx"
DEFAULT_PACK = ROOT / "references" / "domain_packs" / "PT.json"
DEFAULT_SCENARIO_ASSET = ROOT / "references" / "scenarios" / "PT.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="验证 PT 展开场景能否收敛到 PT.json 单一场景目录；严格只读。"
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--scenario-asset", type=Path, default=DEFAULT_SCENARIO_ASSET)
    args = parser.parse_args()

    events = extract_expanded_pt_reference_events(args.workbook)
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    asset = (json.loads(args.scenario_asset.read_text(encoding="utf-8"))
             if args.scenario_asset.exists() else {"function_types": {}})
    candidate, summary = build_pt_single_source_candidate(pack, asset, events)
    errors = validate_pt_single_source_candidate(candidate)

    print("[PT 场景单源收敛：dry-run]")
    print("本脚本只在内存中构造候选 PT.json；不会写入 PT.json、scenarios/PT.json 或任何报告文件。")
    for key in (
        "source_event_count",
        "source_unique_scenarios",
        "existing_pack_or_asset_unique_scenarios",
        "existing_scene_texts_reused",
        "source_scene_texts_not_in_existing_assets",
        "legacy_scene_texts_not_in_expanded_source",
        "candidate_catalog_count",
        "candidate_reference_event_count",
        "candidate_compatible_baseline_count",
    ):
        print(f"{key}: {summary[key]}")
    if summary["lookup_conflicts"]:
        print("lookup_conflicts:")
        for conflict in summary["lookup_conflicts"]:
            print(f"  - {conflict}")
    if errors:
        print("验证失败：")
        for error in errors:
            print(f"  - {error}")
        return 2
    print("验证通过：候选结构中场景原文仅位于 risk_catalog.scenario_catalog，所有引用均为 scenario_id。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
