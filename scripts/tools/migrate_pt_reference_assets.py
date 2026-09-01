"""已弃用：该旧脚本会直接覆盖 PT.json，禁止继续执行。

请改用 plan_pt_scene_consolidation.py 做只读方案验证。实际迁入必须在人工确认
事件级映射后，通过带原子写入与完整回归的专用发布命令执行。
"""

from __future__ import annotations

import sys


if __name__ == "__main__":
    print(
        "已拒绝执行旧 migrate_pt_reference_assets.py：该历史脚本会直接覆盖 PT.json。\n"
        "请先运行 scripts/tools/plan_pt_scene_consolidation.py（只读 dry-run）。"
    )
    raise SystemExit(2)
