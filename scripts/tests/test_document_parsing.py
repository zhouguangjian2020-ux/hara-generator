from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from docx import Document
import openpyxl

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from utils.chapter_matcher import apply_exact_chapter_match
from utils.data_models import RelatedItem, SubFunction
from utils.document_context import (
    build_function_description_map,
    build_node_summary_map,
    build_parse_quality,
    split_heading_number,
)
from utils.docx_parser import _parse_chapter3_with_context, parse_docx_with_context
from utils.excel_writer import ExcelWriter
from utils.s1_rules import apply_s1_rules
from utils.s2_rules import compute_s2_for_related_items


def document_prefix():
    doc = Document()
    for title in ("范围", "引用文件", "功能描述"):
        doc.add_heading(title, 1)
    doc.add_heading("转向助力功能", 2)
    doc.add_heading("功能概述", 3)
    return doc


def match_document(doc, name="基础助力", description="", func_name="转向助力功能"):
    chapters, context = _parse_chapter3_with_context(doc)
    sub = SubFunction("S-201-01", name, description, "")
    item = RelatedItem("CS_func_0001", func_name, sub_functions=[sub])
    unmatched, warnings = apply_exact_chapter_match([item], chapters, context)
    return sub, chapters, context, unmatched, warnings


def add_description(doc, heading, level, *paragraphs):
    doc.add_heading(heading, level)
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)


class HeadingParsingTests(unittest.TestCase):
    def test_explicit_numbers_with_and_without_spaces(self):
        for text, expected in (
            ("3.1.11 转向模式控制", ("3.1.11", "转向模式控制")),
            ("3.1.3.1摩擦补偿", ("3.1.3.1", "摩擦补偿")),
            ("3.1.2.2.2功能描述", ("3.1.2.2.2", "功能描述")),
            ("3．1．2　基础助力", ("3.1.2", "基础助力")),
            ("3.1.2 EPS", ("3.1.2", "EPS")),
            ("3.1.2 4WD控制", ("3.1.2", "4WD控制")),
            ("3 功能描述", ("3", "功能描述")),
        ):
            with self.subTest(text=text):
                self.assertEqual(split_heading_number(text), expected)

    def test_numeric_function_names_are_not_stripped(self):
        for title in ("4WD控制", "3D楼块信息显示", "360°全景影像", "12V电源", "四驱控制", "一键启动"):
            with self.subTest(title=title):
                self.assertEqual(split_heading_number(title), ("", title))

    def test_explicit_number_is_authoritative_even_when_counter_disagrees(self):
        doc = document_prefix()
        doc.add_heading("3.1.11 转向模式控制", 3)
        add_description(doc, "3.1.11.2功能描述", 4, "切换助力曲线。")
        sub, chapters, context, unmatched, _ = match_document(doc, "转向模式控制")
        self.assertEqual(sub.chapter, "3.1.11 转向模式控制")
        self.assertEqual(chapters[0]["label"], sub.chapter)
        node = next(n for n in context["nodes"] if n["heading_text"] == "3.1.11 转向模式控制")
        self.assertEqual(node["label"], sub.chapter)
        self.assertEqual(sub._match_method, "exact")
        self.assertFalse(unmatched)

    def test_unnumbered_heading_styles_keep_existing_counter_labels(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        add_description(doc, "功能描述", 4, "提供助力。")
        sub, _, _, unmatched, _ = match_document(doc)
        self.assertEqual(sub.chapter, "3.1.2 基础助力")
        self.assertEqual(sub.description, "提供助力。")
        self.assertFalse(unmatched)

    def test_custom_outline_level_is_recognized(self):
        doc = document_prefix()
        p = doc.add_paragraph("3.1.11 转向模式控制")
        p._p.get_or_add_pPr().get_or_add_outlineLvl().val = 2
        add_description(doc, "功能描述", 4, "切换助力曲线。")
        sub, _, _, _, _ = match_document(doc, "转向模式控制")
        self.assertEqual(sub.chapter, "3.1.11 转向模式控制")


class FunctionDescriptionTests(unittest.TestCase):
    def test_numbered_child_exact_match_does_not_take_sibling_description(self):
        doc = document_prefix()
        doc.add_heading("3.1.2 基础助力控制", 3)
        doc.add_heading("3.1.2.1 基础助力", 4)
        add_description(doc, "3.1.2.1.1目的", 5, "减轻驾驶员负荷。")
        add_description(doc, "3.1.2.1.2 功能描述", 5, "提供对应转向助力。")
        doc.add_heading("3.1.2.2 转角监测", 4)
        add_description(doc, "3.1.2.2.2功能描述", 5, "计算方向盘转角。")
        sub, _, _, unmatched, _ = match_document(doc)
        self.assertEqual(sub.chapter, "3.1.2.1 基础助力")
        self.assertEqual(sub.description, "提供对应转向助力。")
        self.assertEqual(sub._match_method, "exact")
        self.assertFalse(unmatched)

    def test_purpose_order_does_not_affect_selection(self):
        for purpose_first in (True, False):
            with self.subTest(purpose_first=purpose_first):
                doc = document_prefix()
                doc.add_heading("基础助力", 3)
                blocks = [("目的", "减轻负荷。"), ("功能描述", "提供助力。")]
                for heading, body in blocks if purpose_first else reversed(blocks):
                    add_description(doc, heading, 4, body)
                self.assertEqual(match_document(doc)[0].description, "提供助力。")

    def test_all_description_aliases(self):
        for title in ("功能描述", "功能详细定义", "功能需求"):
            with self.subTest(title=title):
                doc = document_prefix()
                doc.add_heading("基础助力", 3)
                add_description(doc, title, 4, "提供助力。")
                self.assertEqual(match_document(doc)[0].description, "提供助力。")

    def test_legitimate_semicolons_and_paragraphs_are_preserved(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        paragraphs = ("低速增加助力；高速降低助力。", "输入异常时停止输出。", "恢复后重新计算。")
        add_description(doc, "功能描述", 4, *paragraphs)
        add_description(doc, "目的", 4, "改善驾驶体验。")
        self.assertEqual(match_document(doc)[0].description, "；".join(paragraphs))

    def test_description_does_not_append_nested_requirements(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        add_description(doc, "功能描述", 4, "提供助力。")
        add_description(doc, "底盘转向需求", 5, "使能条件：IG ON。")
        self.assertEqual(match_document(doc)[0].description, "提供助力。")

    def test_parent_function_can_aggregate_its_own_child_descriptions(self):
        doc = document_prefix()
        doc.add_heading("基础助力控制", 3)
        for title in ("基础助力", "转角监测", "扭矩监测"):
            doc.add_heading(title, 4)
            add_description(doc, "目的", 5, "目的不应输出。")
            add_description(doc, "功能描述", 5, title + "的描述。")
        sub = match_document(doc, "基础助力控制")[0]
        self.assertEqual(sub.description, "基础助力的描述。目的不应输出。；转角监测的描述。目的不应输出。；扭矩监测的描述。目的不应输出。")

    def test_plain_body_without_description_heading_is_supported(self):
        doc = document_prefix()
        add_description(doc, "基础助力", 3, "正文直接介绍助力。")
        self.assertEqual(match_document(doc)[0].description, "正文直接介绍助力。")

    def test_plain_child_bodies_without_description_headings_are_supported(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        add_description(doc, "低速助力", 4, "低速补偿。")
        add_description(doc, "高速助力", 4, "高速补偿。")
        self.assertEqual(match_document(doc)[0].description, "低速补偿。；高速补偿。")

    def test_missing_description_does_not_borrow_parent_or_sibling(self):
        doc = document_prefix()
        add_description(doc, "基础助力控制", 3, "父级功能介绍。")
        doc.add_heading("基础助力", 4)
        add_description(doc, "目的", 5, "减轻驾驶负荷。")
        doc.add_heading("转角监测", 4)
        add_description(doc, "功能描述", 5, "计算转角。")
        sub, _, context, unmatched, _ = match_document(doc)
        self.assertEqual(sub.description, "")
        self.assertEqual(unmatched[0]["status"], "low_confidence")
        self.assertEqual(unmatched[0]["reason"], "matched_section_description_missing")
        item = RelatedItem("CS_func_0001", "转向助力功能", sub_functions=[sub])
        self.assertFalse(build_parse_quality([item], context, unmatched=unmatched)["analysis_context_ready"])

    def test_source_table_description_is_not_overwritten(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        add_description(doc, "功能描述", 4, "章节描述。")
        self.assertEqual(match_document(doc, description="原功能表明确描述。")[0].description, "原功能表明确描述。")

    def test_deep_exact_match_uses_description_selector(self):
        doc = document_prefix()
        doc.add_heading("其他控制", 3)
        doc.add_heading("子系统", 4)
        doc.add_heading("3.1.2.1.1 基础助力", 5)
        add_description(doc, "目的", 6, "改善体验。")
        add_description(doc, "功能描述", 6, "提供助力。")
        sub, _, _, unmatched, _ = match_document(doc)
        self.assertEqual(sub._match_method, "deep_exact")
        self.assertEqual(sub.description, "提供助力。")
        self.assertFalse(unmatched)

    def test_empty_description_heading_does_not_use_nested_requirement_body(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        add_description(doc, "目的", 4, "改善体验。")
        doc.add_heading("功能描述", 4)
        add_description(doc, "IDCU需求", 5, "发送助力控制信号。")
        self.assertEqual(match_document(doc)[0].description, "改善体验。")

    def test_deep_match_with_missing_body_clears_legacy_parent_description(self):
        doc = document_prefix()
        doc.add_heading("基础助力控制", 3)
        doc.add_heading("子系统", 4)
        doc.add_heading("基础助力", 5)
        add_description(doc, "目的", 6, "改善体验。")
        doc.add_heading("转角监测", 5)
        add_description(doc, "功能描述", 6, "计算转角。")
        sub, _, _, unmatched, _ = match_document(doc, func_name="另一相关项")
        self.assertEqual(sub._match_method, "deep_exact")
        self.assertEqual(sub.description, "")
        self.assertEqual(unmatched[0]["reason"], "matched_section_description_missing")

    def test_generic_context_summary_remains_separate(self):
        doc = document_prefix()
        doc.add_heading("基础助力", 3)
        add_description(doc, "目的", 4, "减轻负荷。")
        add_description(doc, "功能描述", 4, "提供助力。")
        _, context = _parse_chapter3_with_context(doc)
        node = next(n for n in context["nodes"] if n["heading_text"] == "基础助力")
        self.assertEqual(build_node_summary_map(context)[node["node_id"]], "提供助力。；减轻负荷。")
        self.assertEqual(build_function_description_map(context)[node["node_id"]], "提供助力。减轻负荷。")

    def test_description_and_purpose_are_joined_in_function_then_purpose_order(self):
        doc = document_prefix()
        doc.add_heading("摩擦补偿", 3)
        add_description(doc, "目的", 4, "通过补偿转向辅助扭矩，克服系统中的摩擦力，以减少转向迟滞感。")
        add_description(doc, "功能描述", 4, "根据转向电机旋转方向、方向盘手力矩和车速计算系统摩擦力矩。")
        self.assertEqual(
            match_document(doc, "摩擦补偿")[0].description,
            "根据转向电机旋转方向、方向盘手力矩和车速计算系统摩擦力矩。"
            "通过补偿转向辅助扭矩，克服系统中的摩擦力，以减少转向迟滞感。",
        )

    def test_description_only_is_kept_when_purpose_is_absent(self):
        doc = document_prefix()
        doc.add_heading("滑行能量回收功能", 3)
        add_description(doc, "功能描述", 4, "滑行能量回收功能即当车辆向前或向后运行过程中，当驾驶员松开加速踏板时，激活此功能，将机械能转化为电能存储到高压电池中。")
        self.assertEqual(
            match_document(doc, "滑行能量回收功能")[0].description,
            "滑行能量回收功能即当车辆向前或向后运行过程中，当驾驶员松开加速踏板时，激活此功能，将机械能转化为电能存储到高压电池中。",
        )

    def test_real_group5_and_group8_use_direct_sentence_or_purpose_only(self):
        root = os.environ.get("HARA_DOCX_TEST_ROOT")
        if not root:
            self.skipTest("set HARA_DOCX_TEST_ROOT to run real DOCX description examples")
        expectations = {
            5: ("空调系统开启/关闭", "整车上电后，用户通过中控屏上的ON/OFF按键或语音控制开启空调。空调开启状态下，用户通过中控屏的ON/OFF按键或语音控制关闭空调系统。"),
            8: ("摩擦补偿", "根据转向电机旋转方向、方向盘手力矩和车速计算系统摩擦力矩。通过补偿转向辅助扭矩，克服系统中的摩擦力，以减少转向迟滞感。"),
        }
        for group, (name, expected) in expectations.items():
            with self.subTest(group=group):
                docx = next((Path(root) / f"数据组{group}").glob("*.docx"))
                _, items, chapters, context, _ = parse_docx_with_context(docx)
                apply_exact_chapter_match(items, chapters, context)
                matches = [sub for item in items for sub in item.sub_functions if sub.name == name]
                self.assertTrue(matches)
                self.assertEqual(matches[0].description, expected)
                self.assertNotIn("IDCU需求", matches[0].description)
                self.assertNotIn("使能条件", matches[0].description)

    def test_writer_accepts_unchanged_subfunction_schema(self):
        doc = document_prefix()
        doc.add_heading("3.1.11 转向模式控制", 3)
        add_description(doc, "功能描述", 4, "切换助力曲线。")
        sub = match_document(doc, "转向模式控制")[0]
        item = RelatedItem("CS_func_0001", "转向助力功能", sub_functions=[sub])
        writer = ExcelWriter()
        wb = writer._create_minimal_workbook()
        writer._write_related_items(wb, {"doc_name": "source.docx", "related_items": [asdict(item)]}, {})
        data = io.BytesIO()
        wb.save(data)
        data.seek(0)
        loaded = openpyxl.load_workbook(data)
        sheet = loaded["相关项功能清单"]
        self.assertEqual(sheet["D2"].value, "切换助力曲线。")
        self.assertEqual(sheet["G2"].value, "3.1.11 转向模式控制")
        self.assertEqual(sheet["F2"].value, "source.docx")


@unittest.skipUnless(os.environ.get("HARA_DOCX_TEST_ROOT"), "set HARA_DOCX_TEST_ROOT to run the 11-group DOCX regression")
class RealDocumentRegressionTests(unittest.TestCase):
    def test_all_eleven_groups(self):
        root = Path(os.environ["HARA_DOCX_TEST_ROOT"])
        expected_counts = [7, 6, 2, 45, 87, 21, 8, 15, 29, 196, 125]
        baseline_path = os.environ.get("HARA_PARSE_BASELINE")
        baseline = {r["group"]: r for r in json.loads(Path(baseline_path).read_text(encoding="utf-8"))} if baseline_path else {}
        for group, count in enumerate(expected_counts, 1):
            with self.subTest(group=group):
                paths = [p for p in (root / f"数据组{group}").glob("*.docx") if not p.name.startswith("~$")]
                self.assertEqual(len(paths), 1)
                _, items, chapters, context, _ = parse_docx_with_context(paths[0])
                unmatched, _ = apply_exact_chapter_match(items, chapters, context)
                subs = [s for item in items for s in item.sub_functions]
                self.assertEqual(len(subs), count)
                for sub in subs:
                    self.assertNotRegex(sub.chapter or "", r"^(\d+(?:\.\d+)+)\s+\1(?:\s|[^\d.])")
                previous = baseline.get(f"数据组{group}")
                if previous:
                    old = previous["related_items"]
                    current = [asdict(item) for item in items]
                    old_subs = [s for item in old for s in item["sub_functions"]]
                    if group != 8:
                        self.assertEqual([s.chapter for s in subs], [s["chapter"] for s in old_subs])
                    newly_missing = {
                        s.name for s, before in zip(subs, old_subs)
                        if before["description"] and not s.description
                    }
                    # 两处源文档缺少独立功能描述标题，必须复核，不能继续拿目的填充。
                    expected_missing = {5: {"制动能量回收"}, 11: {"方向盘电动调节"}}
                    self.assertEqual(newly_missing, expected_missing.get(group, set()))
                    self.assertEqual(
                        {r["sub_name"] for r in unmatched if r.get("reason") == "matched_section_description_missing"},
                        expected_missing.get(group, set()),
                    )
                    before_items = [
                        RelatedItem(
                            **{k: v for k, v in item.items() if k != "sub_functions"},
                            sub_functions=[SubFunction(**s) for s in item["sub_functions"]],
                        )
                        for item in old
                    ]
                    self.assertEqual(apply_s1_rules(before_items), apply_s1_rules(deepcopy(items)))
                    self.assertEqual(compute_s2_for_related_items(deepcopy(old)), compute_s2_for_related_items(deepcopy(current)))
                    normalized = deepcopy([old, current])
                    for rows in normalized:
                        for item in rows:
                            for sub in item["sub_functions"]:
                                for field in ("chapter", "description", "evidence_refs"):
                                    sub.pop(field, None)
                    self.assertEqual(*normalized)
                    before_missing = sum(r["status"] == "unmatched" for r in previous["unmatched"])
                    self.assertLessEqual(sum(r["status"] == "unmatched" for r in unmatched), before_missing)
                if group == 8:
                    basic = next(s for s in subs if s.name == "基础助力")
                    self.assertEqual(basic.chapter, "3.1.2.1 基础助力")
                    self.assertEqual(basic.description, "控制单元会根据实时车速和方向盘手力矩提供对应转向助力。")
                    friction = next(s for s in subs if s.name == "摩擦补偿")
                    self.assertEqual(friction.description, "根据转向电机旋转方向、方向盘手力矩和车速计算系统摩擦力矩。")


if __name__ == "__main__":
    unittest.main()
