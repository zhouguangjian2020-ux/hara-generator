"""Direct semantic smoke tests for the project-independent PT case library."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.case_library import get_case_library


def _profiles(case: dict, *, with_scenario: bool = True) -> dict:
    return {
        "domain": case["domain"],
        "function": case["function_profile"],
        "failure": case["failure_profile"],
        "hazard": case["hazard_profile"],
        "scenario": case["scenario_profile"] if with_scenario else None,
    }


def test_load_library(lib, cases):
    assert lib.load_domain("P")
    assert lib.canonical_domain("P") == "PT"
    assert lib.loaded_files["PT"].name == "PT.json"
    assert len(cases) == len(lib.cases_by_domain["PT"]) == 490
    print(f"[OK] 加载 PT.json 的 case_library_catalog：{len(cases)} 条，不依赖项目 func_id")


def test_exact_match(lib, case):
    p = _profiles(case)
    match = lib.match_cases(p["domain"], p["function"], p["failure"], p["hazard"], p["scenario"])
    assert match.match_type == "exact"
    prefill = lib.get_prefill_data(match)
    assert prefill["editable"] is False
    assert "scenario_semantic_fingerprint" in prefill["matched_by"]
    print(f"[OK] exact：{match.confidence:.3f}，候选 {len(match.candidates)} 条")


def test_similar_match(lib, case):
    p = _profiles(case, with_scenario=False)
    match = lib.match_cases(p["domain"], p["function"], p["failure"], p["hazard"], None)
    assert match.match_type == "similar"
    assert len(match.candidates) > 1
    assert lib.get_prefill_data(match)["editable"] is True
    print(f"[OK] similar：候选 {len(match.candidates)} 条，未锁定")


def test_template_match(lib, case):
    p = _profiles(case, with_scenario=False)
    failure = {**p["failure"], "anomaly_class": "unseen_project_anomaly"}
    match = lib.match_cases(p["domain"], p["function"], failure, None, None)
    assert match.match_type == "template"
    prefill = lib.get_prefill_data(match)
    assert prefill["editable"] is True
    assert "severity" not in prefill
    print(f"[OK] template：{match.confidence:.3f}，只提供参考模板")


def test_no_match(lib):
    match = lib.match_cases(
        "PT",
        {"canonical_function_family": "project_specific_unknown"},
        {"canonical_mode": "未知", "anomaly_class": "unknown"},
    )
    assert match.match_type == "none"
    print("[OK] none：未知语义不伪造匹配")


def main():
    cases = json.loads((ROOT / "references/domain_packs/PT.json").read_text(encoding="utf-8"))["case_library_catalog"]["cases"]
    lib = get_case_library()
    test_load_library(lib, cases)
    key_counts = {}
    for case in cases:
        key = (
            case["function_profile"]["canonical_function_family"],
            case["failure_profile"]["canonical_mode"],
            case["failure_profile"]["anomaly_class"],
            case["hazard_profile"]["hazard_family"],
            case["hazard_profile"]["vehicle_hazard_class"],
            case["scenario_profile"]["semantic_fingerprint"],
        )
        key_counts[key] = key_counts.get(key, 0) + 1
    exact_case = next(
        case for case in cases
        if key_counts[(
            case["function_profile"]["canonical_function_family"],
            case["failure_profile"]["canonical_mode"],
            case["failure_profile"]["anomaly_class"],
            case["hazard_profile"]["hazard_family"],
            case["hazard_profile"]["vehicle_hazard_class"],
            case["scenario_profile"]["semantic_fingerprint"],
        )] == 1
    )
    test_exact_match(lib, exact_case)
    test_similar_match(lib, cases[0])
    test_template_match(lib, exact_case)
    test_no_match(lib)
    print("所有案例库 v2 语义测试通过！")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[ERROR] 测试失败: {error}")
        raise
