"""写入后 Excel/JSON 语义对账。

只读取本工具刚生成的五张目标 Sheet，以列标题和业务键比对 JSON；不使用参考文件的
行号、合并单元格、文件名或数据组编号作为主键。此模块的使命是及时发现“JSON 有、
Excel 展示漏了”的问题，防止展示范围与下游范围分叉。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl

from utils.data_models import FAILURE_MODES


class OutputReconciliationError(ValueError):
    """输出 Excel 与 JSON 语义集合不一致。"""


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return "".join(str(value).replace("\n", " ").split()).lower()


def _find_sheet(workbook, aliases: tuple[str, ...]):
    for name in workbook.sheetnames:
        normalized = _norm(name)
        if any(_norm(alias) in normalized for alias in aliases):
            return workbook[name]
    return None


def _find_header_row(sheet, aliases: tuple[str, ...], *, max_rows: int = 10) -> tuple[int | None, dict[str, int]]:
    """在前若干行中按标题语义定位列；不依赖固定行列位置。"""
    target = {_norm(alias) for alias in aliases}
    best_row = None
    best_map: dict[str, int] = {}
    for row in range(1, min(sheet.max_row, max_rows) + 1):
        result: dict[str, int] = {}
        for col in range(1, sheet.max_column + 1):
            header = _norm(sheet.cell(row=row, column=col).value)
            if not header:
                continue
            for alias in target:
                if alias and (header == alias or alias in header):
                    result.setdefault(alias, col)
        if len(result) > len(best_map):
            best_row, best_map = row, result
    return best_row, best_map


def _counter_diff(label: str, expected: Counter, actual: Counter) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    missing = expected - actual
    extra = actual - expected
    if missing:
        examples = list(missing.elements())[:5]
        issues.append(("error", f"{label}: Excel 缺少 {sum(missing.values())} 条 JSON 记录，例如 {examples}"))
    if extra:
        examples = list(extra.elements())[:5]
        issues.append(("error", f"{label}: Excel 多出 {sum(extra.values())} 条无法追溯的记录，例如 {examples}"))
    return issues


def _s1_expected(intermediate: dict, s1: dict) -> Counter:
    decision_index = {
        item.get("func_id"): {
            feature.get("feature_list_id"): feature.get("is_hara")
            for feature in item.get("sub_functions", [])
            if isinstance(feature, dict)
        }
        for item in s1.get("decisions", [])
        if isinstance(item, dict)
    }
    result: Counter = Counter()
    for item in intermediate.get("related_items", []):
        if not isinstance(item, dict):
            continue
        func_id = item.get("func_id")
        for feature in item.get("sub_functions", []):
            if not isinstance(feature, dict):
                continue
            feature_id = feature.get("feature_list_id")
            is_hara = decision_index.get(func_id, {}).get(feature_id)
            if isinstance(func_id, str) and isinstance(feature_id, str) and isinstance(is_hara, bool):
                result[(func_id, feature_id, "是" if is_hara else "否")] += 1
    return result


def _reconcile_s1(workbook, intermediate: dict, s1: dict) -> list[tuple[str, str]]:
    sheet = _find_sheet(workbook, ("相关项功能清单", "相关项功能列表"))
    if sheet is None:
        return [("error", "S1 对账: 输出缺少「相关项功能清单」Sheet")]
    aliases = ("整车功能id", "featurelistid", "是否进行hara分析")
    header_row, header_map = _find_header_row(sheet, aliases)
    if header_row is None or len(header_map) < 3:
        return [("error", "S1 对账: 无法按列标题识别功能、Feature 和 HARA 判定列")]
    fid_col = header_map[_norm("整车功能id")]
    feature_col = header_map[_norm("featurelistid")]
    hara_col = header_map[_norm("是否进行hara分析")]
    actual: Counter = Counter()
    # Writer 为了可读性会合并父功能和重复 Feature 的展示单元格。读回时只按
    # 连续数据行前向继承业务键，不读取或依赖合并区域本身。
    last_func_id = None
    last_feature_id = None
    last_hara = None
    for row in range(header_row + 1, sheet.max_row + 1):
        # 仅处理实际数据行；模板尾部的空白/说明行不能被前向继承为重复记录。
        if not any(sheet.cell(row=row, column=column).value is not None for column in range(1, sheet.max_column + 1)):
            continue
        raw_func_id = sheet.cell(row=row, column=fid_col).value
        raw_feature_id = sheet.cell(row=row, column=feature_col).value
        raw_hara = sheet.cell(row=row, column=hara_col).value
        if raw_func_id:
            last_func_id = str(raw_func_id)
        if raw_feature_id:
            last_feature_id = str(raw_feature_id)
        if raw_hara in ("是", "否"):
            last_hara = str(raw_hara)
        if last_func_id and last_feature_id and last_hara in ("是", "否"):
            actual[(last_func_id, last_feature_id, last_hara)] += 1
    return _counter_diff("S1 相关项功能清单", _s1_expected(intermediate, s1), actual)


def _reconcile_s2(workbook, s2: dict) -> list[tuple[str, str]]:
    sheet = _find_sheet(workbook, ("失效模式",))
    if sheet is None:
        return [("error", "S2 对账: 输出缺少「失效模式」Sheet")]
    header_row, header_map = _find_header_row(sheet, ("整车功能id", *FAILURE_MODES))
    id_col = header_map.get(_norm("整车功能id"))
    if header_row is None or not id_col:
        return [("error", "S2 对账: 无法按列标题识别整车功能 ID")]
    mode_columns = {
        mode: header_map[_norm(mode)]
        for mode in FAILURE_MODES
        if _norm(mode) in header_map
    }
    expected: Counter = Counter()
    for decision in s2.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        func_id = decision.get("func_id")
        for mode in decision.get("selected_modes", []):
            if isinstance(func_id, str) and isinstance(mode, str):
                expected[(func_id, mode)] += 1
    actual: Counter = Counter()
    for row in range(header_row + 1, sheet.max_row + 1):
        func_id = sheet.cell(row=row, column=id_col).value
        if not func_id:
            continue
        for mode, col in mode_columns.items():
            if _norm(sheet.cell(row=row, column=col).value) in {"√", "v", "yes", "是", "true", "1"}:
                actual[(str(func_id), mode)] += 1
    return _counter_diff("S2 失效模式", expected, actual)


def _reconcile_s3(workbook, s3: dict) -> list[tuple[str, str]]:
    sheet = _find_sheet(workbook, ("hazop分析", "hazop 分析"))
    if sheet is None:
        return [("error", "S3 对账: 输出缺少「HAZOP 分析」Sheet")]
    header_row, header_map = _find_header_row(sheet, ("功能失效id",))
    failure_col = header_map.get(_norm("功能失效id"))
    if header_row is None or not failure_col:
        return [("error", "S3 对账: 无法按列标题识别功能失效 ID")]
    expected: Counter = Counter(
        anomaly.get("failure_id")
        for entry in s3.get("entries", []) if isinstance(entry, dict)
        for anomaly in entry.get("anomalies", []) if isinstance(anomaly, dict)
        if isinstance(anomaly.get("failure_id"), str) and anomaly.get("failure_id")
    )
    actual: Counter = Counter(
        str(sheet.cell(row=row, column=failure_col).value)
        for row in range(header_row + 1, sheet.max_row + 1)
        if sheet.cell(row=row, column=failure_col).value
    )
    return _counter_diff("S3 HAZOP failure_id", expected, actual)


def _reconcile_s4(workbook, s4: dict) -> list[tuple[str, str]]:
    sheet = _find_sheet(workbook, ("hara分析", "hara 分析"))
    if sheet is None:
        return [("error", "S4 对账: 输出缺少「HARA 分析」Sheet")]
    header_row, header_map = _find_header_row(sheet, ("危害事件id", "功能失效id"))
    _hazard_header_row, hazard_header_map = _find_header_row(sheet, ("整车危害",))
    event_col = header_map.get(_norm("危害事件id"))
    failure_col = header_map.get(_norm("功能失效id"))
    hazard_col = hazard_header_map.get(_norm("整车危害"))
    if header_row is None or not event_col or not failure_col or not hazard_col:
        return [("error", "S4 对账: 无法按列标题识别危害事件 ID/功能失效 ID/整车危害")]
    expected: Counter = Counter(
        (
            event.get("hazard_id"),
            hazard.get("failure_id"),
            _norm(hazard.get("hazard") or hazard.get("vehicle_hazard")),
        )
        for hazard in s4.get("hazards", []) if isinstance(hazard, dict) and not hazard.get("skip")
        for event in hazard.get("events", []) if isinstance(event, dict)
        if event.get("hazard_id") and hazard.get("failure_id")
    )
    actual: Counter = Counter(
        (
            str(sheet.cell(row=row, column=event_col).value),
            str(sheet.cell(row=row, column=failure_col).value),
            _norm(sheet.cell(row=row, column=hazard_col).value),
        )
        for row in range(header_row + 1, sheet.max_row + 1)
        if sheet.cell(row=row, column=event_col).value and sheet.cell(row=row, column=failure_col).value
    )
    return _counter_diff("S4 HARA 事件及整车危害", expected, actual)


def _reconcile_s5(workbook, s4: dict, s5: dict) -> list[tuple[str, str]]:
    sheet = _find_sheet(workbook, ("整车安全目标", "安全目标"))
    if sheet is None:
        return [("error", "S5 对账: 输出缺少「整车安全目标」Sheet")]
    aliases = ("安全目标id", "整车安全目标id")
    header_row, header_map = _find_header_row(sheet, aliases)
    # 右侧名称含“整车”，左侧不含；标题匹配优先使用本列实际文字。
    if header_row is None:
        return [("error", "S5 对账: 无法按列标题识别事件级/整车级安全目标 ID")]
    event_col = None
    vehicle_col = None
    for col in range(1, sheet.max_column + 1):
        header = _norm(sheet.cell(row=header_row, column=col).value)
        if not header:
            continue
        if "整车安全目标id" in header:
            vehicle_col = col
        elif "安全目标id" in header:
            event_col = col
    if not event_col or not vehicle_col:
        return [("error", "S5 对账: 缺少事件级或整车级安全目标 ID 列")]

    expected_events = Counter(
        event.get("safety_goal_id")
        for hazard in s4.get("hazards", []) if isinstance(hazard, dict) and not hazard.get("skip")
        for event in hazard.get("events", []) if isinstance(event, dict)
        if event.get("safety_goal_id")
    )
    actual_events = Counter(
        str(sheet.cell(row=row, column=event_col).value)
        for row in range(header_row + 1, sheet.max_row + 1)
        if sheet.cell(row=row, column=event_col).value
    )
    issues = _counter_diff("S5 事件级安全目标", expected_events, actual_events)

    expected_vehicle = Counter(
        goal.get("sg_id")
        for goal in s5.get("safety_goals", []) if isinstance(goal, dict) and goal.get("sg_id")
    )
    actual_vehicle = Counter(
        str(sheet.cell(row=row, column=vehicle_col).value)
        for row in range(header_row + 1, sheet.max_row + 1)
        if sheet.cell(row=row, column=vehicle_col).value
    )
    issues.extend(_counter_diff("S5 整车级安全目标", expected_vehicle, actual_vehicle))

    mappings = s5.get("event_safety_goal_mappings", [])
    if mappings:
        mapped_ids = Counter(
            row.get("event_safety_goal_id")
            for row in mappings if isinstance(row, dict) and row.get("event_safety_goal_id")
        )
        issues.extend(_counter_diff("S5 事件→整车目标映射", expected_events, mapped_ids))
        valid_vehicle_ids = set(expected_vehicle)
        invalid = [
            row.get("vehicle_safety_goal_id") for row in mappings
            if isinstance(row, dict) and row.get("vehicle_safety_goal_id") not in valid_vehicle_ids
        ]
        if invalid:
            issues.append(("error", f"S5 映射引用不存在的整车级 SG: {invalid[:5]}"))
    return issues


def reconcile_generated_workbook(
    workbook_path: str | Path,
    *,
    intermediate: dict,
    s1: dict,
    s2: dict,
    s3: dict | None = None,
    s4: dict | None = None,
    s5: dict | None = None,
) -> list[tuple[str, str]]:
    """读回刚写出的 Excel，并按业务键对账所有实际写入的目标 Sheet。"""
    workbook = openpyxl.load_workbook(str(workbook_path), data_only=False)
    issues: list[tuple[str, str]] = []
    issues.extend(_reconcile_s1(workbook, intermediate, s1))
    issues.extend(_reconcile_s2(workbook, s2))
    if s3 is not None:
        issues.extend(_reconcile_s3(workbook, s3))
    if s4 is not None:
        issues.extend(_reconcile_s4(workbook, s4))
    if s5 is not None:
        issues.extend(_reconcile_s5(workbook, s4 or {}, s5))
    return issues


def require_reconciled_workbook(*args, **kwargs) -> None:
    issues = reconcile_generated_workbook(*args, **kwargs)
    errors = [message for level, message in issues if level == "error"]
    if errors:
        raise OutputReconciliationError("Excel/JSON 对账失败：" + "; ".join(errors))
