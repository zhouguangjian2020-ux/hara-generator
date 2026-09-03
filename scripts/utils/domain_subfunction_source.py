"""按域读取子功能 HARA 权威表并执行可审计匹配。

本模块服务所有提供 ``<DOMAIN>_subfunctions.json`` 的域。表格中的 ``HARA判定`` 是子功能 S1 的权威结论；
当前解析文档中的 Feature ID 不作为跨来源主键。匹配优先使用
``整车功能名称 + 子功能描述`` 的规范化精确匹配；对于已知同源文档，
在同一整车功能组内使用保序桥接，并把来源行/匹配方法写入中间结果。
"""
from __future__ import annotations

from collections import defaultdict
import json
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from pathlib import Path
from typing import Any
import unicodedata

from openpyxl import load_workbook

SOURCE_FILENAME = "<DOMAIN>_子功能.XLSX"  # offline build input only
RUNTIME_FILENAME = "<DOMAIN>_subfunctions.json"
SOURCE_SCHEMA = "domain_subfunction_authority.v1"
LEGACY_PT_SCHEMA = "pt_subfunction_authority.v1"

_HEADER_ALIASES = {
    "record_id": ("子功能ID", "子功能 ID"),
    "domain": ("域",),
    "function_name": ("整车功能名称", "相关项", "整车功能"),
    "feature_name": ("子功能描述", "子功能名称", "子功能"),
    "hara": ("HARA判定", "HARA 判定", "HARA"),
    "remark": ("注释", "备注"),
    "source": ("数据来源",),
    "filled_by": ("填写人",),
    "filled_at": ("填写时间",),
}


def default_subfunction_source_path(domain: str) -> Path:
    """返回指定域的子功能 JSON 权威资产路径。"""
    canonical = _canonical_domain(domain)
    if not canonical:
        raise ValueError("domain 不能为空")
    return Path(__file__).resolve().parents[2] / "references" / "domain_packs" / f"{canonical}_subfunctions.json"


def _canonical_domain(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return {"P": "PT", "POWERTRAIN": "PT", "PT": "PT", "A": "AD", "ADAS": "AD", "AD": "AD", "B": "BD", "BODY": "BD", "BD": "BD", "INFO": "ET", "INFOTAINMENT": "ET", "ET": "ET", "CB": "CB", "CS": "CS"}.get(raw, raw)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return unicodedata.normalize("NFKC", str(value)).replace("\u3000", " ").strip()


def normalize_function_name(value: Any) -> str:
    """规范化整车功能名；仅去掉排版差异和末尾的“功能/”。"""
    text = re.sub(r"[\s\u00a0]+", "", _text(value))
    text = text.replace("／", "/").rstrip("/")
    text = re.sub(r"功能$", "", text)
    return text


def normalize_feature_name(value: Any) -> str:
    """规范化子功能名；不做无边界模糊相似度判断。"""
    text = re.sub(r"[\s\u00a0]+", "", _text(value))
    return text.replace("／", "/").replace("（", "(").replace("）", ")")


def _header_key(value: Any) -> str:
    return re.sub(r"[\s_]+", "", _text(value)).lower()


def _find_columns(headers: list[Any]) -> dict[str, int]:
    normalized = [_header_key(value) for value in headers]
    result: dict[str, int] = {}
    for key, aliases in _HEADER_ALIASES.items():
        alias_keys = {_header_key(alias) for alias in aliases}
        for index, header in enumerate(normalized):
            if header in alias_keys:
                result[key] = index
                break
    return result


def _parse_hara(value: Any) -> bool | None:
    text = _text(value)
    if text == "是":
        return True
    if text == "否":
        return False
    return None


@dataclass(frozen=True)
class DomainSubfunctionRecord:
    source_row: int
    record_id: str
    domain: str
    function_name: str
    feature_name: str
    hara: bool | None
    remark: str = ""
    source: str = ""
    filled_by: str = ""
    filled_at: str = ""

    @property
    def function_key(self) -> str:
        return normalize_function_name(self.function_name)

    @property
    def feature_key(self) -> str:
        return normalize_feature_name(self.feature_name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "record_id": self.record_id,
            "domain": self.domain,
            "function_name": self.function_name,
            "feature_name": self.feature_name,
            "hara": self.hara,
            "remark": self.remark,
            "source": self.source,
            "filled_by": self.filled_by,
            "filled_at": self.filled_at,
        }


def _load_domain_subfunction_authority_xlsx(source_path: Path, *, sheet_name: str | None = None) -> tuple[list[DomainSubfunctionRecord], dict[str, Any]]:
    """离线读取并校验原始 域子功能 Excel。"""
    source_path = source_path.resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"域子功能权威表不存在: {source_path}")

    workbook = load_workbook(source_path, read_only=True, data_only=True)
    if not workbook.worksheets:
        workbook.close()
        raise ValueError("域子功能权威表没有工作表")
    if sheet_name:
        if sheet_name not in workbook.sheetnames:
            workbook.close()
            raise ValueError(f"域子功能权威表缺少工作表: {sheet_name}")
        sheet = workbook[sheet_name]
    else:
        sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    try:
        headers = list(next(rows))
    except StopIteration as error:
        workbook.close()
        raise ValueError("域子功能权威表为空") from error
    columns = _find_columns(headers)
    required = ("domain", "function_name", "feature_name", "hara")
    missing = [key for key in required if key not in columns]
    if missing:
        workbook.close()
        raise ValueError(f"域子功能权威表缺少列: {', '.join(missing)}")

    records: list[DomainSubfunctionRecord] = []
    errors: list[str] = []
    for row_number, values in enumerate(rows, 2):
        if not any(value not in (None, "") for value in values):
            continue
        get = lambda key: (values[columns[key]] if columns[key] < len(values) else None) if key in columns else None
        hara = _parse_hara(get("hara"))
        function_name = _text(get("function_name"))
        feature_name = _text(get("feature_name"))
        domain = _text(get("domain"))
        if not function_name or not feature_name:
            errors.append(f"第{row_number}行缺少整车功能名称或子功能描述")
        if hara is None:
            errors.append(f"第{row_number}行 HARA判定必须为“是/否”")
        records.append(DomainSubfunctionRecord(
            source_row=row_number,
            record_id=_text(get("record_id")),
            domain=domain,
            function_name=function_name,
            feature_name=feature_name,
            hara=hara,
            remark=_text(get("remark")),
            source=_text(get("source")),
            filled_by=_text(get("filled_by")),
            filled_at=_text(get("filled_at")),
        ))

    workbook.close()

    grouped: dict[tuple[str, str, str], list[DomainSubfunctionRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.domain.upper(), record.function_key, record.feature_key)].append(record)
    conflicts = []
    for key, candidates in grouped.items():
        values = {record.hara for record in candidates}
        if len(values) > 1:
            conflicts.append({
                "domain": key[0],
                "function_key": key[1],
                "feature_key": key[2],
                "source_rows": [record.source_row for record in candidates],
                "hara_values": sorted("unknown" if value is None else ("是" if value else "否") for value in values),
            })
    if errors:
        raise ValueError("域子功能权威表校验失败: " + "; ".join(errors))
    summary = {
        "schema_version": SOURCE_SCHEMA,
        "source_file": str(source_path),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "sheet": sheet.title,
        "record_count": len(records),
        "function_count": len({record.function_key for record in records}),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }
    return records, summary



def _record_from_json(payload: dict[str, Any], index: int) -> DomainSubfunctionRecord:
    """Convert one normalized JSON record into the runtime dataclass."""
    source_row = payload.get("source_row")
    if not isinstance(source_row, int):
        raise ValueError(f"域子功能JSON records[{index}].source_row必须为整数")
    hara = payload.get("hara")
    if hara not in {True, False, None}:
        raise ValueError(f"域子功能JSON records[{index}].hara必须为true/false/null")
    return DomainSubfunctionRecord(
        source_row=source_row,
        record_id=_text(payload.get("record_id")),
        domain=_text(payload.get("domain")),
        function_name=_text(payload.get("function_name")),
        feature_name=_text(payload.get("feature_name")),
        hara=hara,
        remark=_text(payload.get("remark")),
        source=_text(payload.get("source")),
        filled_by=_text(payload.get("filled_by")),
        filled_at=_text(payload.get("filled_at")),
    )


def _validate_runtime_records(
    records: list[DomainSubfunctionRecord], *, source_path: Path, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Validate normalized records and return a runtime audit summary."""
    errors: list[str] = []
    for index, record in enumerate(records):
        if not record.function_name or not record.feature_name:
            errors.append(f"records[{index}]缺少整车功能名称或子功能描述")
        if record.hara is None:
            errors.append(f"records[{index}].hara必须为true/false")
    grouped: dict[tuple[str, str, str], list[DomainSubfunctionRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.domain.upper(), record.function_key, record.feature_key)].append(record)
    conflicts = []
    for key, candidates in grouped.items():
        values = {record.hara for record in candidates}
        if len(values) > 1:
            conflicts.append({
                "domain": key[0],
                "function_key": key[1],
                "feature_key": key[2],
                "source_rows": [record.source_row for record in candidates],
                "hara_values": sorted("unknown" if value is None else ("是" if value else "否") for value in values),
            })
    if errors:
        raise ValueError("域子功能JSON校验失败: " + "; ".join(errors))
    summary = {
        "schema_version": SOURCE_SCHEMA,
        "source_file": metadata.get("source_file") or SOURCE_FILENAME,
        "source_sha256": metadata.get("source_sha256"),
        "source_sheet": metadata.get("source_sheet") or metadata.get("sheet") or "",
        "runtime_file": str(source_path),
        "record_count": len(records),
        "function_count": len({record.function_key for record in records}),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }
    if conflicts:
        raise ValueError("域子功能JSON存在HARA判定冲突: " + json.dumps(conflicts, ensure_ascii=False))
    return summary


def _load_domain_subfunction_authority_json(source_path: Path, *, expected_domain: str | None = None) -> tuple[list[DomainSubfunctionRecord], dict[str, Any]]:
    """Load the normalized PT subfunction authority JSON used at runtime."""
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"域子功能JSON格式无效: {source_path}") from error
    if not isinstance(payload, dict):
        raise ValueError("域子功能JSON根节点必须为对象")
    schema_version = payload.get("schema_version")
    if schema_version not in {SOURCE_SCHEMA, LEGACY_PT_SCHEMA}:
        raise ValueError(
            f"域子功能JSON schema_version必须为 {SOURCE_SCHEMA}（PT 兼容 {LEGACY_PT_SCHEMA}）: {schema_version!r}"
        )
    records_payload = payload.get("records")
    if not isinstance(records_payload, list):
        raise ValueError("域子功能JSON缺少records数组")
    records = [_record_from_json(item, index) for index, item in enumerate(records_payload) if isinstance(item, dict)]
    if len(records) != len(records_payload):
        raise ValueError("域子功能JSON records中的每一项必须为对象")
    metadata = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    if expected_domain:
        wrong_domain = [record.domain for record in records if _canonical_domain(record.domain) != expected_domain]
        if wrong_domain:
            raise ValueError(f"域子功能JSON包含不属于 {expected_domain} 的记录")
    summary = _validate_runtime_records(records, source_path=source_path, metadata=metadata)
    summary["schema_version"] = schema_version
    declared_count = payload.get("record_count")
    if declared_count is not None and declared_count != len(records):
        raise ValueError(f"域子功能JSON record_count={declared_count!r}与实际记录数{len(records)}不一致")
    declared_conflicts = payload.get("conflict_count")
    if declared_conflicts is not None and declared_conflicts != summary["conflict_count"]:
        raise ValueError("域子功能JSON conflict_count与实际冲突数不一致")
    return records, summary


def load_domain_subfunction_authority(domain: str, path: str | Path | None = None, *, sheet_name: str | None = None) -> tuple[list[DomainSubfunctionRecord], dict[str, Any]]:
    """Load PT authority from JSON at runtime.

    An explicitly supplied ``.xlsx`` path remains supported solely for offline
    migration/tests.  The default path is always ``references/domain_packs/PT_subfunctions.json``.
    """
    source_path = Path(path) if path else default_subfunction_source_path(domain)
    source_path = source_path.resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"域子功能权威JSON不存在: {source_path}")
    if source_path.suffix.lower() == ".json":
        return _load_domain_subfunction_authority_json(source_path, expected_domain=_canonical_domain(domain))
    # Legacy explicit input path: never used by the deployed runtime default.
    if source_path.suffix.lower() in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        return _load_domain_subfunction_authority_xlsx(source_path, sheet_name=sheet_name)
    raise ValueError(f"域子功能权威资产必须为JSON: {source_path}")

def _runtime_filename(domain: Any = None) -> str:
    canonical = _canonical_domain(domain)
    return f"{canonical}_subfunctions.json" if canonical else RUNTIME_FILENAME


def _match_exact(candidates: list[DomainSubfunctionRecord], feature_name: str) -> list[DomainSubfunctionRecord]:
    key = normalize_feature_name(feature_name)
    return [record for record in candidates if record.feature_key == key]


def match_domain_subfunctions(
    function_name: str,
    feature_names: list[str],
    records: list[DomainSubfunctionRecord],
    *,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """为同一整车功能的一组 Feature 建立一次性可审计映射。"""
    function_key = normalize_function_name(function_name)
    expected_domain = _canonical_domain(domain) if domain else None
    candidates = [record for record in records if record.function_key == function_key and (expected_domain is None or _canonical_domain(record.domain) == expected_domain)]
    results: list[dict[str, Any] | None] = [None] * len(feature_names)
    consumed: set[int] = set()

    for index, feature_name in enumerate(feature_names):
        exact = _match_exact(candidates, feature_name)
        if len(exact) == 1:
            record = exact[0]
            record_index = candidates.index(record)
            consumed.add(record_index)
            results[index] = _known_result(record, "exact")
        elif len(exact) > 1:
            hara_values = {record.hara for record in exact}
            if len(hara_values) > 1:
                results[index] = _ambiguous_result(exact, "exact_conflict")
            else:
                record = exact[0]
                consumed.add(candidates.index(record))
                results[index] = _known_result(record, "exact_duplicate_same_conclusion")

    # 同源 DOCX 的短名/完整描述在当前表中保持原始顺序；仅在整组具有足够
    # 行数时启用此有限桥接。若已有 exact 锚点，则按“表索引-文档索引”
    # 的稳定偏移对齐，允许权威表在末尾存在额外记录。此结果会写入
    # intermediate，禁止隐藏式模糊匹配。
    anchor_offsets = set()
    for feature_index, result in enumerate(results):
        if not result or result.get("match_method") not in {"exact", "exact_duplicate_same_conclusion"}:
            continue
        source_row = result.get("source_row")
        anchor_index = next(
            (candidate_index for candidate_index, candidate in enumerate(candidates)
             if candidate.source_row == source_row),
            None,
        )
        if anchor_index is not None:
            anchor_offsets.add(anchor_index - feature_index)
    offset = next(iter(anchor_offsets)) if len(anchor_offsets) == 1 else 0
    for index, result in enumerate(results):
        if result is not None:
            continue
        candidate_index = index + offset
        if 0 <= candidate_index < len(candidates) and candidate_index not in consumed:
            record = candidates[candidate_index]
            results[index] = _known_result(record, "source_order_bridge")
            consumed.add(candidate_index)
        else:
            results[index] = {
                "hara_match_status": "unknown",
                "hara": None,
                "hara_source": _runtime_filename(expected_domain or domain),
                "match_method": "not_found",
                "source_row": None,
                "source_record_id": None,
                "source_function_name": function_name,
                "source_feature_name": None,
                "source_remark": None,
            }
    return [result for result in results if result is not None]


def _known_result(record: DomainSubfunctionRecord, method: str) -> dict[str, Any]:
    return {
        "hara_match_status": "exact_known",
        "hara": record.hara,
        "hara_source": _runtime_filename(record.domain),
        "match_method": method,
        "source_row": record.source_row,
        "source_record_id": record.record_id,
        "source_function_name": record.function_name,
        "source_feature_name": record.feature_name,
        "source_remark": record.remark or None,
    }


def _ambiguous_result(records: list[DomainSubfunctionRecord], method: str) -> dict[str, Any]:
    return {
        "hara_match_status": "ambiguous",
        "hara": None,
        "hara_source": _runtime_filename(records[0].domain if records else None),
        "match_method": method,
        "source_row": None,
        "source_record_id": None,
        "source_function_name": records[0].function_name if records else None,
        "source_feature_name": records[0].feature_name if records else None,
        "source_remark": "同一匹配对象在权威表中存在冲突 HARA 判定。",
        "candidate_source_rows": [record.source_row for record in records],
    }


def validate_domain_subfunction_authority(domain: str, path: str | Path | None = None) -> dict[str, Any]:
    """供测试/发布门调用的表格校验入口。"""
    records, summary = load_domain_subfunction_authority(domain, path)
    if summary["conflict_count"]:
        raise ValueError(f"域子功能权威表存在 {summary['conflict_count']} 个冲突匹配对象")
    return summary

