"""当前项目运行期 ID 的确定性分配工具。

跨项目资产可以携带稳定的语义身份，但不能把来源项目的功能序号直接当成
当前项目编号。本模块只根据本轮输入中的 `func_id` 分配项目功能号；这些
编号用于 failure/hazard/safety-goal 等追溯 ID，不参与任何语义匹配。
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def allocate_project_function_numbers(
    func_ids: Iterable[object], *, width: int = 4,
) -> dict[str, str]:
    """为当前项目功能分配不冲突的数字编号。

    优先兼容 `P_func_0001`、`F16` 等带数字的项目编号；无法提取数字或
    数字与其他功能冲突时，按首次出现顺序分配尚未使用的正整数。返回值仅
    用于当前项目产物编号，不得参与案例库或 Domain Pack 语义匹配。
    """
    ordered: list[str] = []
    for value in func_ids:
        func_id = str(value or "").strip()
        if func_id and func_id not in ordered:
            ordered.append(func_id)

    numeric: dict[str, int] = {}
    used: set[int] = set()
    pending: list[str] = []
    for func_id in ordered:
        candidate: int | None = None
        if "_func_" in func_id:
            suffix = func_id.split("_func_", 1)[1]
            if suffix.isdigit():
                candidate = int(suffix)
        else:
            match = re.search(r"(\d+)$", func_id)
            if match:
                candidate = int(match.group(1))
        if candidate is not None and candidate > 0 and candidate not in used:
            numeric[func_id] = candidate
            used.add(candidate)
        else:
            pending.append(func_id)

    next_number = 1
    for func_id in pending:
        while next_number in used:
            next_number += 1
        numeric[func_id] = next_number
        used.add(next_number)
        next_number += 1

    return {func_id: str(number).zfill(width) for func_id, number in numeric.items()}
