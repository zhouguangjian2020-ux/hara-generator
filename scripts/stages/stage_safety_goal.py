# scripts/stages/stage_safety_goal.py
# S5 整车安全目标汇总阶段：从 s4_hara_final.json 提取、去重、编号安全目标
#
# 用法:
#   python run_hara.py sg generate <s4_hara_final.json> [-o safety_goals.json]

import json
import os
import sys
import tempfile
from pathlib import Path

# 确保 scripts/ 在 path 中
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.safety_goal_rules import generate_safety_goals
from utils.domain_packs import canonical_domain_pack_code, load_domain_pack


class S4FinalValidationError(ValueError):
    """S4 final 没有通过验证，不能进入 S5。"""


class SafetyGoalValidationError(ValueError):
    """S5 安全目标质量校验失败，不能发布 safety_goals.json。"""


def _require_s4_validation_passed(s4_final: dict, s4_path: str) -> None:
    """拒绝缺少、失败或不完整的 S4 验证状态。"""
    marker = Path(f"{s4_path}.validation_failed")
    if marker.exists():
        raise S4FinalValidationError(
            "检测到该 S4 final 的本轮验证失败标记；请修复后重新执行 'hara validate'"
        )

    validation = s4_final.get("validation")
    if not isinstance(validation, dict):
        raise S4FinalValidationError(
            "S4 final 未包含验证状态；请先使用新版 'hara validate' 重新验证"
        )

    error_count = validation.get("error_count")
    warning_count = validation.get("warning_count")
    if (validation.get("passed") is not True
            or not isinstance(error_count, int)
            or isinstance(error_count, bool)
            or error_count != 0
            or not isinstance(warning_count, int)
            or isinstance(warning_count, bool)
            or warning_count < 0):
        raise S4FinalValidationError(
            "S4 final 验证未通过；请修复 S4 错误后重新执行 'hara validate'"
        )


def _save_json_atomic(data: dict, path: str) -> None:
    """原子写入 S5 正式文件，避免异常时覆盖已有有效结果。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".tmp",
            prefix=f".{out.stem}.", dir=out.parent, delete=False
        ) as temp:
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        os.replace(temp_path, out)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _load_json(path: str, label: str) -> dict:
    """加载 JSON 文件，失败时打印错误并退出"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"[S5] 错误: {label} 文件不存在: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[S5] 错误: {label} JSON 格式无效: {e}")
        sys.exit(1)


def generate(s4_hara_path: str, output_path: str = "safety_goals.json") -> None:
    """从 s4_hara_final.json 生成整车安全目标汇总 JSON。

    Args:
        s4_hara_path: validate 后的 s4_hara_final.json 路径
        output_path: 输出 safety_goals.json 路径
    """
    s4_final = _load_json(s4_hara_path, "s4_hara_final")
    _require_s4_validation_passed(s4_final, s4_hara_path)

    domain = canonical_domain_pack_code(s4_final.get("domain", "X"))
    vehicle_safety_goal_catalog = None
    if domain == "PT":
        # PT 的整车安全目标目录来自同一运行时 Domain Pack，作为 S5
        # 的权威预填源；目录缺失时保持旧的自动编号兼容行为。
        pack = load_domain_pack("PT")
        vehicle_safety_goal_catalog = pack.get("vehicle_safety_goal_catalog")
    result = generate_safety_goals(s4_final, vehicle_safety_goal_catalog)

    issues = result.get("validation_issues", [])
    errors = [issue for issue in issues if issue[0] == "error"]
    if errors:
        details = "; ".join(str(issue[1]) for issue in errors[:5])
        raise SafetyGoalValidationError(
            f"S5 安全目标验证失败（{len(errors)} 个错误）：{details}"
        )

    out = Path(output_path)
    _save_json_atomic(result, output_path)

    # 统计输出
    domain = result["domain"]
    total = result["total_events"]
    unique = result["unique_safety_goals"]
    sgs = result["safety_goals"]

    # ASIL分布统计
    asil_dist = {}
    for sg in sgs:
        a = sg["asil"]
        asil_dist[a] = asil_dist.get(a, 0) + 1

    print(f"[S5] 已保存: {out}")
    print(f"[S5] 域: {domain}")
    print(f"[S5] 原始有SG的非QM事件: {total} 个")
    print(f"[S5] 去重后安全目标: {unique} 个")
    print(f"[S5] ASIL分布: " + ", ".join(f"{k}={asil_dist.get(k, 0)}" for k in ["D", "C", "B", "A"] if asil_dist.get(k, 0) > 0))

    # 验证结果
    issues = result.get("validation_issues", [])
    errors = [i for i in issues if i[0] == "error"]
    warnings = [i for i in issues if i[0] == "warning"]
    if errors:
        print(f"[S5] 验证错误: {len(errors)} 个")
        for e in errors[:10]:
            print(f"  [ERROR] {e[1]}")
    if warnings:
        print(f"[S5] 验证警告: {len(warnings)} 个")
        for w in warnings[:10]:
            print(f"  [WARN] {w[1]}")
    if not issues:
        print("[S5] 安全目标验证通过")

    # 显示前5个
    print(f"[S5] 安全目标列表（前5个）:")
    for sg in sgs[:5]:
        funcs = ", ".join(sg["functions"][:2])
        if len(sg["functions"]) > 2:
            funcs += f" 等{len(sg['functions'])}个"
        print(f"  {sg['sg_id']} [{sg['asil']}] {sg['safety_goal'][:50]}")
        print(f"         功能: {funcs}, 关联事件: {sg['event_count']}")
    if len(sgs) > 5:
        print(f"  ... 还有 {len(sgs) - 5} 个")


def run(args):
    """被 run_hara.py 调用的入口"""
    subcmd = getattr(args, "sg_subcommand", None)

    if subcmd == "generate":
        try:
            generate(
                args.s4_hara_final,
                args.output or "safety_goals.json"
            )
        except (S4FinalValidationError, SafetyGoalValidationError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S5] 操作失败：{error}")
            raise SystemExit(1) from error
    else:
        print("用法: run_hara.py sg generate <s4_hara_final.json> [-o safety_goals.json]")
