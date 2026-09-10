from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from utils.domain_packs import load_domain_pack, reset_domain_pack_cache
from utils.domain_subfunction_source import (
    DomainSubfunctionRecord,
    default_subfunction_source_path,
    match_domain_subfunctions,
)


class DomainSubfunctionSourceTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            DomainSubfunctionRecord(2, "CS_SFUNC_1", "CS", "转向助力功能", "基本助力功能", True),
            DomainSubfunctionRecord(3, "CS_SFUNC_2", "CS", "转向助力功能", "主动回正", True),
        ]

    def test_runtime_default_does_not_promote_unknown_by_order(self):
        results = match_domain_subfunctions(
            "转向助力功能",
            ["基本助力功能", "全新未确认功能"],
            self.records,
            domain="CS",
        )
        self.assertEqual(results[0]["hara_match_status"], "exact_known")
        self.assertEqual(results[0]["match_method"], "exact")
        self.assertEqual(results[1]["hara_match_status"], "unknown")
        self.assertEqual(results[1]["match_method"], "not_found")
        self.assertIsNone(results[1]["source_feature_name"])

    def test_domain_pack_manifest_uses_domain_subdirectories(self):
        project_root = SCRIPTS_DIR.parent
        pack_root = project_root / "references" / "domain_packs"
        manifest = json.loads((pack_root / "manifest.json").read_text(encoding="utf-8"))
        reset_domain_pack_cache()
        for domain, relative_path in manifest["domain_files"].items():
            self.assertEqual(relative_path, f"{domain}/{domain}.json")
            self.assertTrue((pack_root / relative_path).is_file())
            self.assertEqual(load_domain_pack(domain)["domain"], domain)

    def test_default_subfunction_path_is_nested_with_its_domain(self):
        expected = SCRIPTS_DIR.parent / "references" / "domain_packs" / "PT" / "PT_subfunctions.json"
        self.assertEqual(default_subfunction_source_path("PT"), expected)

    def test_same_source_bridge_requires_explicit_opt_in(self):
        result = match_domain_subfunctions(
            "转向助力功能",
            ["基础助力控制"],
            self.records,
            domain="CS",
            allow_source_order_bridge=True,
        )[0]
        self.assertEqual(result["hara_match_status"], "exact_known")
        self.assertEqual(result["match_method"], "source_order_bridge")


if __name__ == "__main__":
    unittest.main()
