"""Validate domain scenario assets and PT single-source invariants."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "references"
SCENARIOS = REF / "scenarios"
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from utils.domain_packs import load_domain_pack, validate_domain_pack  # noqa: E402
from utils.pt_scene_consolidation import validate_pt_single_source_candidate  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate() -> list[str]:
    errors: list[str] = []
    manifest = load(SCENARIOS / "manifest.json")
    legacy = load(REF / "scenario_lookup_table.json")
    scenario_file_domains = ["CS", "CB", "AD", "ET", "BD"]

    # PT 的运行时主数据已迁入 Domain Pack；manifest 不得再要求不存在的
    # scenarios/PT.json，也不得在旧总表中继续保存第二份 PT function_types。
    if "PT" in manifest.get("domain_files", {}):
        errors.append("manifest 不得将 PT 映射到 references/scenarios/PT.json；PT 必须从 Domain Pack 单源读取")
    if "PT" not in manifest.get("domain_pack_domains", []):
        errors.append("manifest.domain_pack_domains 必须声明 PT")
    if "PT" in legacy.get("domains", {}):
        errors.append("旧 scenario_lookup_table.json 仍含 PT 域副本，违反 PT 场景单源约束")
    try:
        pt_pack = load_domain_pack("PT")
        errors.extend(f"PT Domain Pack: {message}" for message in validate_domain_pack(pt_pack, expected_domain="PT"))
        errors.extend(f"PT 单源合同: {message}" for message in validate_pt_single_source_candidate(pt_pack))
    except ValueError as error:
        errors.append(f"PT Domain Pack 无法加载: {error}")

    for domain in scenario_file_domains:
        filename = manifest.get("domain_files", {}).get(domain)
        if not filename:
            errors.append(f"manifest 缺少域文件映射: {domain}")
            continue
        path = SCENARIOS / filename
        if not path.exists():
            errors.append(f"域文件不存在: {path}")
            continue
        data = load(path)
        if data.get("domain") != domain:
            errors.append(f"域文件 {filename} 的 domain 不匹配: {data.get('domain')!r}")
        if domain in ("AD", "ET", "BD"):
            actual = data.get("function_types", {})
            expected = legacy.get("domains", {}).get(domain, {}).get("function_types", {})
            if actual != expected:
                errors.append(f"{domain}.json function_types 与旧总表不一致")

    legacy_sets = legacy.get("hardcoded_scenarios", {}).get("scenarios", {})
    for key, owner in manifest.get("scenario_set_domains", {}).items():
        filename = manifest.get("domain_files", {}).get(owner)
        if not filename:
            errors.append(f"场景集 {key} 的归属域 {owner} 不是 scenarios 文件域")
            continue
        data = load(SCENARIOS / filename)
        actual = data.get("scenario_sets", {}).get(key)
        if actual != legacy_sets.get(key):
            errors.append(f"场景集 {key} 与旧总表不一致")

    profile = load(REF / "profiles" / "CS_steering_assist.json")
    if profile.get("expected_failure_modes") != ["丢失", "非预期", "过多", "反向", "卡滞"]:
        errors.append("CS 转向 Profile 的失效模式集合不符合数据组1/8基线")
    if profile.get("expected_hazop_entry_count") != 5:
        errors.append("CS 转向 Profile 的 HAZOP entry 数量必须为 5")
    if profile.get("allow_subfunction_split") is not False:
        errors.append("CS 转向 Profile 必须禁止按子功能拆分")

    aliases = load(REF / "aliases" / "CS_feature_aliases.json")
    for required in ("S-201-08", "S-201-13", "S-201-15"):
        if required not in aliases.get("feature_id_examples", {}):
            errors.append(f"CS 别名表缺少版本漂移示例: {required}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验按域场景资产及 PT 单源合同")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    errors = validate()
    if errors:
        if not args.quiet:
            print("场景资产校验失败:")
            for error in errors:
                print(f"  - {error}")
        return 1
    if not args.quiet:
        print("场景资产校验通过：PT Domain Pack 单源、其他域文件、旧总表边界、CS Profile 与语义别名均正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
