"""Regression coverage for controlled S3 -> S4 source-case semantics.

Optional HARA_HANDOFF_TEST_DOMAIN_PACKS_ROOT repeats the same tests against a
real external pack root. No import-path overlays or case assets are modified.
"""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from stages import stage_hara, stage_s2, stage_s3
from utils import case_library as case_library_module
from utils.case_library import CaseLibrary
from utils.domain_pack_generation import _group_domain_cases, generate_known_s3
from utils.domain_packs import load_domain_pack, reset_domain_pack_cache
from utils.hara_rules import reset_scenario_asset_caches


class SourceCaseHandoffTests(unittest.TestCase):
    def setUp(self):
        self.previous_root = os.environ.pop("HARA_DOMAIN_PACKS_ROOT", None)
        external = os.environ.get("HARA_HANDOFF_TEST_DOMAIN_PACKS_ROOT")
        if external:
            os.environ["HARA_DOMAIN_PACKS_ROOT"] = external
        self.previous_library = case_library_module._case_library
        case_library_module._case_library = None
        reset_domain_pack_cache()
        reset_scenario_asset_caches()
        self.temp = tempfile.TemporaryDirectory(prefix="hara_source_case_handoff_")
        self.folder = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()
        if self.previous_root is None:
            os.environ.pop("HARA_DOMAIN_PACKS_ROOT", None)
        else:
            os.environ["HARA_DOMAIN_PACKS_ROOT"] = self.previous_root
        reset_domain_pack_cache()
        reset_scenario_asset_caches()
        case_library_module._case_library = self.previous_library

    def save(self, name, data):
        path = self.folder / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def load(self, name):
        return json.loads((self.folder / name).read_text(encoding="utf-8"))

    def s3_flow(self, domain="CS", function_name="转向助力功能"):
        pack = load_domain_pack(domain)
        ref = pack["case_library_catalog"]["cases"][0]["source_refs"][0]
        intermediate = {
            "domain": domain,
            "doc_name": ref.get("source_project") or ref["source_file"],
            "related_items": [{
                "func_id": f"{domain}_func_0001", "func_name": function_name,
                "domain": domain, "sub_functions": [{
                    "feature_list_id": "F1", "name": "回归测试子功能",
                    "s1_rule_is_hara": None,
                }],
            }],
        }
        s1 = {"decisions": [{
            "func_id": f"{domain}_func_0001", "func_name": function_name,
            "is_hara": True, "sub_functions": [{
                "feature_list_id": "F1", "is_hara": True, "remark": "测试输入",
            }],
        }]}
        self.save("intermediate.json", intermediate)
        self.save("s1.json", s1)
        self.save("agent_s2.json", {"decisions": []})
        self.save("agent_s3.json", {"entries": []})
        stage_s2.generate(str(self.folder / "intermediate.json"), str(self.folder / "s1.json"), str(self.folder / "s2_draft.json"))
        s2 = stage_s2.compose(str(self.folder / "intermediate.json"), str(self.folder / "s1.json"), str(self.folder / "s2_draft.json"), str(self.folder / "agent_s2.json"), str(self.folder / "s2.json"))
        stage_s3.generate(str(self.folder / "intermediate.json"), str(self.folder / "s1.json"), str(self.folder / "s2.json"), str(self.folder / "s3_draft.json"))
        s3 = stage_s3.compose(str(self.folder / "intermediate.json"), str(self.folder / "s1.json"), str(self.folder / "s2.json"), str(self.folder / "s3_draft.json"), str(self.folder / "agent_s3.json"), str(self.folder / "s3.json"))
        self.assertEqual(stage_s3.validate(s1, s2, s3, intermediate), [])
        self.assertEqual(self.load("s3_draft.json")["generation"]["agent_required_func_ids"], [])
        return intermediate, s1, s2, s3

    def prepare(self):
        stage_hara.prepare(str(self.folder / "s3.json"), str(self.folder / "s4.json"),
                           s1_path=str(self.folder / "s1.json"), s2_path=str(self.folder / "s2.json"),
                           intermediate_path=str(self.folder / "intermediate.json"))
        return self.load("s4.json")

    def assert_source_roundtrip(self, domain, s3, s4, *, publish=True):
        cases = {c["case_id"]: c for c in load_domain_pack(domain)["case_library_catalog"]["cases"]}
        anomalies = {a["failure_id"]: a for e in s3["entries"] for a in e["anomalies"]}
        for hazard in s4["hazards"]:
            source = anomalies[hazard["failure_id"]]
            if hazard["skip"]:
                self.assertEqual(hazard["events"], [])
                continue
            self.assertTrue(hazard["source_case_locked"])
            self.assertEqual(hazard["agent_action"], "locked")
            self.assertEqual(hazard["failure_profile"], source["failure_profile"])
            for event in hazard["events"]:
                case = cases[event["source_case_id"]]
                self.assertIn(case["case_id"], source["case_ids"])
                self.assertEqual(hazard["failure_profile"]["anomaly_class"], case["failure_profile"]["anomaly_class"])
                self.assertEqual(event["description"], case["event_description"])
                for field in ("severity", "exposure", "controllability"):
                    self.assertEqual(event[field], case["assessment"][field])
        # Re-resolve the exact same profiles without relying on derived ASIL fields.
        lib = case_library_module.get_case_library()
        for hazard in s4["hazards"]:
            if hazard["skip"]:
                continue
            expected, _ = stage_hara._resolve_source_case_events(
                lib, domain=domain, case_ids=hazard["source_case_ids"],
                function_profile=hazard["function_profile"], failure_profile=hazard["failure_profile"],
                hazard_profile={"hazard_family": hazard["hazard_family"]},
            )
            self.assertEqual([e["source_case_id"] for e in expected], [e["source_case_id"] for e in hazard["events"]])
        if publish:
            stage_hara.validate(str(self.folder / "s4.json"), str(self.folder / "final.json"), s3_path=str(self.folder / "s3.json"))
            self.assertTrue((self.folder / "final.json").is_file())
            self.assertEqual(stage_hara._validate_source_case_lock(domain, self.load("final.json")["hazards"]), [])

    def test_cs_full_controlled_pipeline_never_reinfers_source_class(self):
        with patch.object(CaseLibrary, "canonical_anomaly_class", side_effect=AssertionError("Source class must not be inferred from text")):
            _, _, _, s3 = self.s3_flow()
            s4 = self.prepare()
            self.assertEqual(len(s3["entries"]), 5)
            self.assertEqual(sum(len(h["events"]) for h in s4["hazards"]), 95)
            self.assert_source_roundtrip("CS", s3, s4)

    def test_cb_epb_keeps_distinct_aliases_and_no_hazard(self):
        with patch.object(CaseLibrary, "canonical_anomaly_class", side_effect=AssertionError("No text fallback for source cases")):
            _, _, _, s3 = self.s3_flow("CB", "电子驻车制动")
            s4 = self.prepare()
            self.assertEqual(sum(len(h["events"]) for h in s4["hazards"]), 45)
            skipped = [h for h in s4["hazards"] if h["skip"]]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["failure_mode"], "过多")
            # CB's source assessments have separate ASIL/FTTI quality defects;
            # this test verifies handoff, not approval of that source data.
            self.assert_source_roundtrip("CB", s3, s4, publish=False)

    def test_pt_controlled_source_pipeline_is_compatible(self):
        with patch.object(CaseLibrary, "canonical_anomaly_class", side_effect=AssertionError("No text fallback for source cases")):
            _, _, _, s3 = self.s3_flow("PT", "档位控制及显示功能")
            self.assert_source_roundtrip("PT", s3, self.prepare())

    def test_chinese_source_classes_are_preserved_verbatim(self):
        lib = CaseLibrary()
        cases = load_domain_pack("CS")["case_library_catalog"]["cases"]
        for case in cases:
            profile = case["failure_profile"]
            actual = stage_hara._build_failure_profile(lib, {"failure_profile": profile}, profile["canonical_mode"], profile["anomaly_aliases"][0])
            self.assertEqual(actual["anomaly_class"], profile["anomaly_class"])

    def test_s3_rejects_missing_changed_or_forged_case_metadata(self):
        intermediate, s1, s2, s3 = self.s3_flow()
        for field, value in (
            ("failure_profile", None),
            ("failure_profile", {"canonical_mode": "丢失", "anomaly_class": "伪造类别"}),
            ("case_ids", ["CS_UNKNOWN_CASE"]),
            ("source_refs", []),
            ("source_failure_id", "FAKE"),
        ):
            with self.subTest(field=field, value=value):
                edited = deepcopy(s3)
                a = edited["entries"][0]["anomalies"][0]
                if value is None:
                    a.pop(field)
                else:
                    a[field] = value
                self.assertTrue(stage_s3.validate(s1, s2, edited, intermediate))
        edited = deepcopy(s3)
        edited["entries"][0]["source"] = "agent"
        self.assertTrue(stage_s3.validate(s1, s2, edited, intermediate))

    def test_prepare_rejects_missing_source_profile_before_lookup(self):
        _, _, _, s3 = self.s3_flow()
        del s3["entries"][0]["anomalies"][0]["failure_profile"]
        self.save("s3.json", s3)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.folder / "s4.json").exists())

    def test_compose_rejects_edited_controlled_profile(self):
        self.s3_flow()
        draft = self.load("s3_draft.json")
        draft["entries"][0]["anomalies"][0]["failure_profile"]["anomaly_class"] = "伪造类别"
        self.save("s3_draft.json", draft)
        with self.assertRaises(ValueError):
            stage_s3.compose(str(self.folder / "intermediate.json"), str(self.folder / "s1.json"), str(self.folder / "s2.json"), str(self.folder / "s3_draft.json"), str(self.folder / "agent_s3.json"), str(self.folder / "invalid.json"))
        self.assertFalse((self.folder / "invalid.json").exists())

    def test_s4_rejects_changed_or_missing_profile(self):
        self.s3_flow()
        s4 = self.prepare()
        for value in (None, {"canonical_mode": "丢失", "anomaly_class": "伪造类别"},
                      {"canonical_mode": "非预期", "anomaly_class": "失去转向助力"}):
            with self.subTest(value=value):
                edited = deepcopy(s4)
                if value is None:
                    edited["hazards"][0].pop("failure_profile")
                else:
                    edited["hazards"][0]["failure_profile"] = value
                errors = stage_hara._validate_source_case_lock("CS", edited["hazards"])
                self.assertTrue(any("来源案例无法重新解引用" in message for _, message in errors))
                self.save("invalid_s4.json", edited)
                with self.assertRaises(stage_hara.S4ValidationError):
                    stage_hara.validate(str(self.folder / "invalid_s4.json"), str(self.folder / "invalid_final.json"), s3_path=str(self.folder / "s3.json"))
                self.assertFalse((self.folder / "invalid_final.json").exists())

    def test_s4_rejects_event_tampering(self):
        self.s3_flow()
        s4 = self.prepare()
        event_group = next(h for h in s4["hazards"] if h["events"])
        event_group["events"][0]["description"] += " 被改写"
        errors = stage_hara._validate_source_case_lock("CS", s4["hazards"])
        self.assertTrue(any("description" in message for _, message in errors))
        self.save("s4.json", s4)
        with self.assertRaises(stage_hara.S4ValidationError):
            stage_hara.validate(str(self.folder / "s4.json"), str(self.folder / "invalid_final.json"), s3_path=str(self.folder / "s3.json"))

    def test_s4_traceability_rejects_case_ids_and_profile_changes(self):
        _, _, _, s3 = self.s3_flow()
        s4 = self.prepare()
        for field, value in (("source_case_ids", []), ("source_refs", []),
                             ("failure_profile", {"canonical_mode": "丢失", "anomaly_class": "另一类别"})):
            with self.subTest(field=field):
                hazards = deepcopy(s4["hazards"])
                hazards[0][field] = value
                errors = stage_hara._validate_s4_source_traceability("CS", hazards, s3["entries"])
                self.assertTrue(any(field in message for _, message in errors))

    def test_source_evidence_downgrade_still_rejected(self):
        self.s3_flow()
        s4 = self.prepare()
        lib = case_library_module.get_case_library()
        originals = lib.resolve_case_ids
        def downgraded(*args, **kwargs):
            cases = deepcopy(originals(*args, **kwargs))
            for case in cases:
                case["evidence_status"] = "needs_review"
            return cases
        with patch.object(lib, "resolve_case_ids", side_effect=downgraded):
            errors = stage_hara._validate_source_case_lock("CS", s4["hazards"])
        self.assertTrue(any("trusted_reference" in message for _, message in errors))

    def test_group_rejects_conflicting_or_missing_source_classes(self):
        pack = load_domain_pack("CS")
        original = pack["case_library_catalog"]["cases"][0]
        for value in ("另一类别", ""):
            with self.subTest(value=value):
                other = deepcopy(original)
                other["failure_profile"]["anomaly_class"] = value
                groups, error = _group_domain_cases([original, other], [original["failure_profile"]["canonical_mode"]], pack["safety_goal_catalog"]["hazard_catalog"], "CS")
                self.assertEqual(groups, [])
                self.assertEqual(error, "CASE_SET_FAILURE_GROUP_MISMATCH")

    def test_generation_does_not_mutate_pack_profiles(self):
        pack = load_domain_pack("CS")
        original = deepcopy(pack["case_library_catalog"]["cases"])
        intermediate, s1, s2, _ = self.s3_flow()
        entries, pending = generate_known_s3(intermediate, s1, s2)
        self.assertFalse(pending)
        entries[0]["anomalies"][0]["failure_profile"]["anomaly_class"] = "测试修改"
        self.assertEqual(pack["case_library_catalog"]["cases"], original)

    def test_legacy_pt_text_and_explicit_top_level_class_still_supported(self):
        lib = CaseLibrary()
        result = stage_hara._build_failure_profile(lib, {}, "丢失", "P档切入D档位失效")
        self.assertEqual(result, {"canonical_mode": "丢失", "anomaly_class": "gear_transition_not_executed"})
        result = stage_hara._build_failure_profile(lib, {"anomaly_class": "失去转向助力"}, "丢失", "失去转向助力")
        self.assertEqual(result["anomaly_class"], "失去转向助力")

    def test_legacy_pt_locked_hazard_without_profile_still_validates(self):
        self.s3_flow("PT", "档位控制及显示功能")
        self.prepare()
        stage_hara.validate(str(self.folder / "s4.json"), str(self.folder / "final.json"), s3_path=str(self.folder / "s3.json"))
        lib = case_library_module.get_case_library()
        legacy = []
        for hazard in self.load("final.json")["hazards"]:
            if not hazard["source_case_locked"]:
                continue
            expected = hazard["failure_profile"]["anomaly_class"]
            if lib.canonical_anomaly_class(hazard["anomaly"], hazard["failure_mode"]) == expected:
                hazard.pop("failure_profile")
                legacy.append(hazard)
        self.assertTrue(legacy)
        self.assertEqual(stage_hara._validate_source_case_lock("PT", legacy), [])

    def test_profile_mode_aliases_are_preserved(self):
        result = stage_hara._build_failure_profile(CaseLibrary(), {
            "failure_profile": {"canonical_mode": "过多", "anomaly_class": "drive_torque_excessive"},
        }, "过大", "驱动扭矩过大")
        self.assertEqual(result["canonical_mode"], "过多")

    def test_malformed_or_contradictory_profiles_fail_closed(self):
        lib = CaseLibrary()
        for data in (
            {"failure_profile": "invalid"},
            {"failure_profile": {"canonical_mode": "非预期", "anomaly_class": "失去转向助力"}},
            {"failure_profile": {"anomaly_class": "失去转向助力"}, "anomaly_class": "另一类别"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                stage_hara._build_failure_profile(lib, data, "丢失", "失去转向助力")


if __name__ == "__main__":
    unittest.main()
