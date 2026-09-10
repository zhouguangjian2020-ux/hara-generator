"""PT 子功能权威表兼容包装。

新代码应使用 :mod:`utils.domain_subfunction_source`；本模块保留旧 PT
接口，避免迁移期调用方和离线构建工具一次性失效。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .domain_subfunction_source import (
    DomainSubfunctionRecord,
    match_domain_subfunctions,
    load_domain_subfunction_authority,
    validate_domain_subfunction_authority,
    default_subfunction_source_path,
)

PTSubfunctionRecord = DomainSubfunctionRecord
SOURCE_FILENAME = "PT_子功能.XLSX"
RUNTIME_FILENAME = "PT_subfunctions.json"
SOURCE_SCHEMA = "pt_subfunction_authority.v1"

def default_pt_subfunction_source_path() -> Path:
    return default_subfunction_source_path("PT")

def load_pt_subfunction_authority(path: str | Path | None = None, *, sheet_name: str | None = None):
    return load_domain_subfunction_authority("PT", path, sheet_name=sheet_name)

def match_pt_subfunctions(function_name: str, feature_names: list[str], records):
    return match_domain_subfunctions(function_name, feature_names, records, domain="PT")

def validate_pt_subfunction_authority(path: str | Path | None = None):
    return validate_domain_subfunction_authority("PT", path)
