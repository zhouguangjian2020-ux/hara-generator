"""受控发布 PT 场景单源迁移。

默认拒绝写入。只有同时给出 --apply 和明确的 guidance 冲突裁决时，才会把
展开版 FUSA PT 参考事件与当前 PT 场景资产收敛到 references/domain_packs/PT.json，
随后删除重复的 references/scenarios/PT.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.domain_packs import validate_domain_pack
from utils.pt_scene_consolidation import (
    build_pt_single_source_candidate,
    extract_expanded_pt_reference_events,
    validate_pt_single_source_candidate,
)

DEFAULT_WORKBOOK = ROOT / "关键数据" / "PT域" / "ALL-HARA_FUSA-100-GPFA-H002______FUSA HARA PT DOMAIN.xlsx"
DEFAULT_PACK = ROOT / "references" / "domain_packs" / "PT.json"
DEFAULT_SCENARIO_ASSET = ROOT / "references" / "scenarios" / "PT.json"


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp", prefix=f".{path.stem}.",
            dir=path.parent, delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _publish_metadata(candidate: dict, summary: dict, conflicts: list[str]) -> None:
    candidate["status"] = "pt_reference_assets_migrated_single_scenario_source"
    provenance = candidate.setdefault("provenance", {})
    provenance["scene_single_source_migration"] = {
        "published_on": "2026-08-25",
        "reference_source": "FUSA_PT_EXPANDED_2026_08_25",
        "reference_event_count": summary["source_event_count"],
        "canonical_scenario_count": summary["candidate_catalog_count"],
        "legacy_scenario_count": summary["legacy_scene_texts_not_in_expanded_source"],
        "guidance_conflict_policy": "domain_pack_wins",
        "guidance_conflict_paths": conflicts,
        "external_pt_scenario_asset": "references/scenarios/PT.json",
        "external_pt_scenario_asset_status": "removed",
        "note": (
            "展开版 FUSA 事件仅作为来源参考矩阵；exact_known/compatible/"
            "reference_variant/needs_review 的工程证据等级仍须按既有合同单独裁决。"
        ),
    }
    contract = candidate["risk_catalog"]["scenario_single_source_contract"]
    contract["migration_state"] = "published"
    contract["external_pt_scenario_file_action"] = "removed"


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 PT 场景单源迁移（默认拒绝写入）。")
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--scenario-asset", type=Path, default=DEFAULT_SCENARIO_ASSET)
    parser.add_argument("--apply", action="store_true", help="确认写入 PT.json 并删除重复 PT 场景文件")
    parser.add_argument(
        "--resolve-guidance", choices=["domain-pack-wins"],
        help="必须显式确认 guidance 冲突采用 PT Domain Pack 当前值。",
    )
    args = parser.parse_args()

    if not args.apply:
        parser.error("默认只允许 dry-run；实际发布必须显式提供 --apply")
    if args.resolve_guidance != "domain-pack-wins":
        parser.error("实际发布必须显式提供 --resolve-guidance domain-pack-wins")
    if not args.scenario_asset.exists():
        parser.error(f"重复 PT 场景文件不存在，拒绝执行首次迁移: {args.scenario_asset}")

    events = extract_expanded_pt_reference_events(args.workbook)
    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    asset = json.loads(args.scenario_asset.read_text(encoding="utf-8"))
    candidate, summary = build_pt_single_source_candidate(pack, asset, events)
    candidate_errors = validate_pt_single_source_candidate(candidate)
    pack_errors = validate_domain_pack(candidate, expected_domain="PT")
    if candidate_errors or pack_errors:
        print("迁移前验证失败，未写入任何文件：")
        for error in candidate_errors + pack_errors:
            print(f"  - {error}")
        return 2

    conflicts = summary["lookup_conflicts"]
    _publish_metadata(candidate, summary, conflicts)
    # 元数据更新后再验证一次，确保写入对象仍满足 Domain Pack 合同。
    pack_errors = validate_domain_pack(candidate, expected_domain="PT")
    if pack_errors:
        print("发布元数据验证失败，未写入任何文件：")
        for error in pack_errors:
            print(f"  - {error}")
        return 2

    _atomic_write_json(args.pack, candidate)
    args.scenario_asset.unlink()
    print("[PT 场景单源迁移] 已发布")
    print(f"  PT Pack: {args.pack}")
    print(f"  已删除重复场景文件: {args.scenario_asset}")
    print(f"  来源事件: {summary['source_event_count']}")
    print(f"  唯一场景目录: {summary['candidate_catalog_count']}")
    print(f"  guidance 冲突采用 PT Domain Pack 当前值: {len(conflicts)} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
