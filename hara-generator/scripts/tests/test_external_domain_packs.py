from __future__ import annotations

import hashlib
import json
import io
import builtins
from contextlib import contextmanager, ExitStack
from unittest.mock import patch
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from utils.case_library import CaseLibrary
from utils.data_models import RelatedItem, SubFunction
from utils.s1_rules import apply_s1_rules
import utils.case_library as case_library_module
import stages.stage_hara as stage_hara
import stages.stage_safety_goal as stage_safety_goal
from utils.domain_packs import (
    DomainPackSourceError,
    load_domain_pack,
    reset_domain_pack_cache,
    resolve_domain_pack_source,
)
from utils.domain_subfunction_source import (
    default_subfunction_source_path,
    load_domain_subfunction_authority,
)
from utils.hara_rules import load_domain_scenario, reset_scenario_asset_caches
from utils.run_manifest import write_run_manifest, _hash_runtime_sources


@contextmanager
def reject_installed_pack_access():
    """Block reads, existence checks and traversal, including manifest hashing."""
    installed = os.path.normcase(os.path.abspath(ROOT / "references" / "domain_packs"))
    attempts = []

    def wrapper(original):
        def guarded(candidate, *args, **kwargs):
            if isinstance(candidate, (str, bytes, os.PathLike)):
                absolute = os.path.normcase(os.path.abspath(os.fsdecode(candidate)))
                if absolute == installed or absolute.startswith(installed + os.sep):
                    attempts.append(absolute)
                    raise AssertionError(f"Unexpected installed Domain Pack access: {absolute}")
            return original(candidate, *args, **kwargs)
        return guarded

    with ExitStack() as stack:
        for module, attr in ((builtins, "open"), (io, "open"), (Path, "open"),
                             (Path, "exists"), (Path, "is_file"), (os, "scandir")):
            stack.enter_context(patch.object(module, attr, wrapper(getattr(module, attr))))
        yield attempts
    if attempts:
        raise AssertionError(f"Installed Domain Pack accesses: {attempts}")


class ExternalDomainPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_root = os.environ.pop("HARA_DOMAIN_PACKS_ROOT", None)
        self.previous_library = case_library_module._case_library
        case_library_module._case_library = None
        self.temp = tempfile.TemporaryDirectory(prefix="hara_external_domain_packs_")
        self.external_root = Path(self.temp.name) / "references" / "domain_packs"
        for domain in ("PT", "CS", "CB", "AD", "ET", "BD"):
            external_dir = self.external_root / domain
            external_dir.mkdir(parents=True)
            installed_dir = ROOT / "references" / "domain_packs" / domain
            pack_source = installed_dir / f"{domain}.json"
            if pack_source.is_file():
                shutil.copy2(pack_source, external_dir / pack_source.name)
            sub_source = installed_dir / f"{domain}_subfunctions.json"
            if sub_source.is_file():
                shutil.copy2(sub_source, external_dir / sub_source.name)
            else:
                (external_dir / f"{domain}_subfunctions.json").write_text(json.dumps({
                    "schema_version": "domain_subfunction_authority.v1",
                    "domain": domain, "records": [], "record_count": 0,
                    "function_count": 0, "conflict_count": 0, "conflicts": [],
                }), encoding="utf-8")
        self._clear_caches()

    def tearDown(self) -> None:
        if self.previous_root is None:
            os.environ.pop("HARA_DOMAIN_PACKS_ROOT", None)
        else:
            os.environ["HARA_DOMAIN_PACKS_ROOT"] = self.previous_root
        self._clear_caches()
        case_library_module._case_library = self.previous_library
        self.temp.cleanup()

    @staticmethod
    def _clear_caches() -> None:
        reset_domain_pack_cache()
        reset_scenario_asset_caches()

    def _use_external(self) -> None:
        os.environ["HARA_DOMAIN_PACKS_ROOT"] = str(self.external_root)
        self._clear_caches()

    def test_external_pt_uses_coherent_pair_and_does_not_switch_non_pt(self) -> None:
        self._use_external()
        source = resolve_domain_pack_source("P")
        self.assertEqual(source.source, "workspace")
        self.assertEqual(source.pack_path, (self.external_root / "PT" / "PT.json").resolve())
        self.assertEqual(default_subfunction_source_path("P"), source.subfunction_path)
        self.assertEqual(load_domain_pack("P")["domain"], "PT")
        records, summary = load_domain_subfunction_authority("P")
        self.assertTrue(records)
        self.assertEqual(summary["record_count"], len(records))
        self.assertEqual(resolve_domain_pack_source("CS").source, "workspace")
        self.assertEqual(load_domain_pack("CS")["domain"], "CS")
        scenario = load_domain_scenario("P")
        self.assertEqual(scenario["scenario_asset"], str(source.pack_path.resolve()))

    def test_missing_external_pair_fails_closed(self) -> None:
        self._use_external()
        (self.external_root / "PT" / "PT_subfunctions.json").unlink()
        with self.assertRaises(DomainPackSourceError):
            resolve_domain_pack_source("PT")
        with self.assertRaises(DomainPackSourceError):
            load_domain_pack("P")

    @staticmethod
    def _match(library, case):
        return library.match_cases("P", case["function_profile"], case["failure_profile"],
                                   case["hazard_profile"], case["scenario_profile"])

    def _workspace_variant(self, name):
        destination = Path(self.temp.name) / name / "domain_packs"
        pair = destination / "PT"
        pair.mkdir(parents=True)
        pack = json.loads((self.external_root / "PT" / "PT.json").read_text(encoding="utf-8"))
        pack["domain_name"] += "-" + name
        for case in pack["case_library_catalog"]["cases"]:
            case["path_test_marker"] = name
        (pair / "PT.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        shutil.copy2(self.external_root / "PT" / "PT_subfunctions.json", pair / "PT_subfunctions.json")
        return destination

    def test_case_library_matching_reloads_after_workspace_switch(self) -> None:
        library = case_library_module.get_case_library()
        original = load_domain_pack("PT")
        case = original["case_library_catalog"]["cases"][0]
        self.assertEqual(self._match(library, case).match_type, "exact")
        old_scenario_name = load_domain_scenario("PT")["domain_name"]
        for name in ("workspace_A", "workspace_B"):
            destination = self._workspace_variant(name)
            # Intentionally do not reset caches or manually call load_domain.
            os.environ["HARA_DOMAIN_PACKS_ROOT"] = str(destination)
            match = self._match(library, case)
            self.assertEqual(match.match_type, "exact")
            self.assertEqual(match.case["path_test_marker"], name)
            selected = library.resolve_case_ids("P", [case["case_id"]])
            self.assertEqual(selected[0]["path_test_marker"], name)
            self.assertEqual(library.loaded_files["PT"], (destination / "PT" / "PT.json").resolve())
            self.assertTrue(load_domain_pack("PT")["domain_name"].endswith(name))
            self.assertTrue(load_domain_scenario("PT")["domain_name"].endswith(name))
        os.environ.pop("HARA_DOMAIN_PACKS_ROOT")
        self.assertNotIn("path_test_marker", self._match(library, case).case)
        self.assertEqual(load_domain_scenario("PT")["domain_name"], old_scenario_name)

    def test_run_manifest_records_external_assets(self) -> None:
        self._use_external()
        intermediate = Path(self.temp.name) / "intermediate.json"
        s1 = Path(self.temp.name) / "s1.json"
        s2 = Path(self.temp.name) / "s2.json"
        result = Path(self.temp.name) / "result.xlsx"
        intermediate.write_text(json.dumps({"related_items": [{"domain": "P"}]}), encoding="utf-8")
        s1.write_text("{}", encoding="utf-8")
        s2.write_text("{}", encoding="utf-8")
        result.write_bytes(b"probe")

        manifest_path = write_run_manifest(
            str(result), intermediate=str(intermediate), s1=str(s1), s2=str(s2)
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["domain_asset_source"], "workspace")
        self.assertEqual(
            Path(manifest["domain_assets"]["domain_pack"]["path"]),
            (self.external_root / "PT" / "PT.json").resolve(),
        )
        self.assertEqual(
            Path(manifest["domain_assets"]["subfunction_authority"]["path"]),
            (self.external_root / "PT" / "PT_subfunctions.json").resolve(),
        )
        self.assertNotIn("scenario_asset", manifest["domain_assets"])


    def _manifest_fixture(self, domain="P"):
        folder = Path(self.temp.name)
        for name, data in (("intermediate", {"related_items": [{"domain": domain}]}), ("s1", {}), ("s2", {})):
            (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        # This tests the provenance writer, not Excel generation.
        result = folder / "manifest_fixture.xlsx"
        result.write_bytes(b"provenance fixture, not a generated workbook")
        path = write_run_manifest(str(result), intermediate=str(folder / "intermediate.json"),
                                  s1=str(folder / "s1.json"), s2=str(folder / "s2.json"))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_no_installed_access_including_run_manifest_hashing(self):
        self._use_external()
        with reject_installed_pack_access() as attempts:
            for domain in ("PT", "P", "POWERTRAIN", "p", " pt "):
                self.assertEqual(load_domain_pack(domain)["domain"], "PT")
                self.assertEqual(resolve_domain_pack_source(domain).source, "workspace")
            load_domain_subfunction_authority("P")
            library = CaseLibrary()
            case = load_domain_pack("PT")["case_library_catalog"]["cases"][0]
            self.assertEqual(self._match(library, case).match_type, "exact")
            self.assertEqual(len(library.resolve_case_ids("P", [case["case_id"]])), 1)
            load_domain_scenario("PT")
            manifest = self._manifest_fixture()
            self.assertEqual(manifest["domain_asset_source"], "workspace")
            for asset in manifest["domain_assets"].values():
                self.assertEqual(asset["sha256"], hashlib.sha256(Path(asset["path"]).read_bytes()).hexdigest())
            self.assertFalse(attempts)

    def test_all_domains_use_external_pairs(self):
        self._use_external()
        for domain in ("PT", "CS", "CB", "AD", "ET", "BD"):
            with self.subTest(domain=domain):
                source = resolve_domain_pack_source(domain)
                self.assertEqual(source.source, "workspace")
                self.assertEqual(source.pack_path, (self.external_root / domain / f"{domain}.json").resolve())
                self.assertEqual(load_domain_pack(domain)["domain"], domain)
                records, summary = load_domain_subfunction_authority(domain)
                self.assertEqual(summary["record_count"], len(records))

    def test_non_pt_uses_installed_scenarios_with_external_pack(self):
        installed_scenario = load_domain_scenario("CS")
        self._use_external()
        scenario = load_domain_scenario("CS")
        self.assertEqual(scenario, installed_scenario)

    def test_non_pt_ignores_even_invalid_external_pairs(self):
        expected = {d: load_domain_pack(d) for d in ("CS", "CB", "AD", "ET", "BD")}
        expected_scenarios = {d: load_domain_scenario(d) for d in expected}
        self._use_external()
        for domain in expected:
            pair = self.external_root / domain
            pair.mkdir(exist_ok=True)
            (pair / f"{domain}.json").write_text("invalid JSON", encoding="utf-8")
            (pair / f"{domain}_subfunctions.json").write_text("invalid JSON", encoding="utf-8")
            with self.subTest(domain=domain):
                self.assertEqual(resolve_domain_pack_source(domain).source, "workspace")
                with self.assertRaises(DomainPackSourceError):
                    load_domain_pack(domain)
        # The external pair is intentionally invalid; no manifest should be written.

    def test_invalid_external_main_propagates_to_s1_s4_s5_and_manifest(self):
        self._use_external()
        (self.external_root / "PT" / "PT.json").write_text("invalid JSON", encoding="utf-8")
        self._clear_caches()
        item = RelatedItem("PT_func_0001", "外部测试功能", domain="PT", sub_functions=[
            SubFunction("F1", "外部测试子功能", "测试", "1")])
        s3 = Path(self.temp.name) / "invalid_pack_s3.json"
        s3.write_text(json.dumps({"domain": "PT", "entries": []}), encoding="utf-8")
        s4 = Path(self.temp.name) / "invalid_pack_s4.json"
        s4.write_text(json.dumps({"domain": "PT", "hazards": [], "validation": {
            "passed": True, "error_count": 0, "warning_count": 0}}), encoding="utf-8")
        loaders = (
            lambda: load_domain_pack("PT"), lambda: load_domain_scenario("PT"),
            lambda: CaseLibrary().get_statistics("PT"), lambda: apply_s1_rules([item]),
            lambda: stage_hara.prepare(str(s3), str(Path(self.temp.name) / "draft.json")),
            lambda: stage_safety_goal.generate(str(s4), str(Path(self.temp.name) / "goals.json")),
            self._manifest_fixture,
        )
        with reject_installed_pack_access():
            for index, loader in enumerate(loaders):
                with self.subTest(index=index), self.assertRaises(DomainPackSourceError):
                    loader()
        self.assertFalse((Path(self.temp.name) / "draft.json").exists())
        self.assertFalse((Path(self.temp.name) / "goals.json").exists())

    def test_invalid_external_subfunction_fails_s1_and_manifest(self):
        self._use_external()
        (self.external_root / "PT" / "PT_subfunctions.json").write_text("{}", encoding="utf-8")
        item = RelatedItem("PT_func_0001", "外部测试功能", domain="PT", sub_functions=[
            SubFunction("F1", "外部测试子功能", "测试", "1")])
        for loader in (lambda: load_domain_subfunction_authority("PT"),
                       lambda: apply_s1_rules([item]), self._manifest_fixture):
            with self.assertRaises(DomainPackSourceError):
                loader()

    def test_missing_root_and_each_missing_file_do_not_reuse_cached_cases(self):
        case = load_domain_pack("PT")["case_library_catalog"]["cases"][0]
        for missing in ("root", "PT.json", "PT_subfunctions.json"):
            library = CaseLibrary()
            os.environ.pop("HARA_DOMAIN_PACKS_ROOT", None)
            self._match(library, case)
            destination = self._workspace_variant("missing_" + missing)
            if missing == "root":
                destination = destination / "does_not_exist"
            else:
                (destination / "PT" / missing).unlink()
            os.environ["HARA_DOMAIN_PACKS_ROOT"] = str(destination)
            with self.subTest(missing=missing), reject_installed_pack_access():
                with self.assertRaises(DomainPackSourceError):
                    self._match(library, case)
                self.assertNotIn("PT", library.loaded_domains)
                with self.assertRaises(DomainPackSourceError):
                    self._manifest_fixture()

    def test_relative_root_is_rejected_and_whitespace_is_default(self):
        os.environ["HARA_DOMAIN_PACKS_ROOT"] = "relative/path"
        with self.assertRaises(DomainPackSourceError):
            resolve_domain_pack_source("PT")
        os.environ["HARA_DOMAIN_PACKS_ROOT"] = "   "
        self.assertEqual(resolve_domain_pack_source("PT").source, "installed")

    def test_explicit_installed_paths_cannot_bypass_external_mode(self):
        self._use_external()
        installed = ROOT / "references" / "domain_packs"
        with self.assertRaises(DomainPackSourceError):
            CaseLibrary(domain_pack_dir=installed).get_statistics("PT")
        with self.assertRaises(DomainPackSourceError):
            load_domain_subfunction_authority("PT", installed / "PT" / "PT_subfunctions.json")

    def test_invalid_catalog_and_absent_scenario_contract_never_fall_back(self):
        self._use_external()
        main = self.external_root / "PT" / "PT.json"
        original = main.read_text(encoding="utf-8")
        for corrupt in ("missing_catalog", "invalid_cases", "scenario_contract"):
            pack = json.loads(original)
            if corrupt == "missing_catalog":
                pack.pop("case_library_catalog", None)
            elif corrupt == "invalid_cases":
                pack["case_library_catalog"]["cases"] = {}
            else:
                pack["risk_catalog"].pop("scenario_single_source_contract", None)
            main.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            self._clear_caches()
            with self.subTest(corrupt=corrupt), reject_installed_pack_access():
                with self.assertRaises(DomainPackSourceError):
                    if corrupt == "scenario_contract":
                        load_domain_scenario("PT")
                    else:
                        CaseLibrary().get_statistics("PT")

    def test_default_runtime_hash_keeps_previous_definition(self):
        candidates = [ROOT / "SKILL.md"]
        for relative in ("scripts", "references"):
            candidates.extend(p for p in (ROOT / relative).rglob("*")
                              if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".bak"))
        digest = hashlib.sha256()
        for path in sorted(set(candidates), key=lambda p: p.as_posix()):
            digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
            digest.update(b"\n")
        self.assertEqual(_hash_runtime_sources(ROOT), digest.hexdigest())

    def test_external_runtime_hash_changes_with_external_data(self):
        self._use_external()
        first = self._manifest_fixture()
        os.environ["HARA_DOMAIN_PACKS_ROOT"] = str(self._workspace_variant("hash_variant"))
        second = self._manifest_fixture()
        self.assertNotEqual(first["runtime_source_sha256"], second["runtime_source_sha256"])

    def test_s4_prepare_prefills_external_case_without_installed_access(self):
        self._use_external()
        with reject_installed_pack_access():
            case = load_domain_pack("PT")["case_library_catalog"]["cases"][0]
            folder = Path(self.temp.name)
            func_id = "external_pt_fixture"
            func_name = "外部PT测试功能"
            mode = case["failure_profile"]["canonical_mode"]
            self._write_fixture(folder / "s3.json", {"domain": "P", "entries": [{
                "func_id": func_id, "func_name": func_name, "analysis_unit_id": func_id,
                "failure_mode": mode, "function_profile": case["function_profile"], "anomalies": [{
                    "failure_id": "PT_MF_0001_01", "description": case["failure_profile"]["anomaly_class"],
                    "function_profile": case["function_profile"], "failure_profile": case["failure_profile"],
                    "hazards": [{"description": case.get("event_description") or "外部案例测试危害",
                                 "associated_hara": "", "hazard_profile": case["hazard_profile"],
                                 "scenario_profile": case["scenario_profile"]}]}]}]})
            self._write_fixture(folder / "intermediate.json", {"related_items": [{
                "domain": "P", "func_id": func_id, "func_name": func_name, "sub_functions": [{
                    "feature_list_id": "F1", "s1_rule_is_hara": True, "s1_disposition": "analyze",
                    "analysis_unit_id": func_id, "reason_code": "R1"}]}]})
            self._write_fixture(folder / "s1.json", {"decisions": [{
                "func_id": func_id, "func_name": func_name, "is_hara": True, "sub_functions": [{
                    "feature_list_id": "F1", "is_hara": True, "disposition": "analyze",
                    "analysis_unit_id": func_id, "reason_code": "R1"}]}]})
            self._write_fixture(folder / "s2.json", {"decisions": [{"func_id": func_id,
                "func_name": func_name, "selected_modes": [mode], "reason": "external fixture"}]})
            draft = folder / "s4_draft.json"
            stage_hara.prepare(str(folder / "s3.json"), str(draft),
                               s1_path=str(folder / "s1.json"), s2_path=str(folder / "s2.json"),
                               intermediate_path=str(folder / "intermediate.json"))
            data = json.loads(draft.read_text(encoding="utf-8"))
            event = data["hazards"][0]["events"][0]
            self.assertEqual(event["sec_source"], "case_library_exact")
            for field in ("severity", "exposure", "controllability"):
                self.assertEqual(event[field], case["assessment"][field])

    @staticmethod
    def _write_fixture(path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
