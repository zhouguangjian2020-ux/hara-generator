# stages/stage_write.py
# 阶段三/五编排：读取 JSON 决策 → 写入 Excel（可选 S3 HAZOP）

import json
import sys
from pathlib import Path

# 确保 scripts/ 在 path 中
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.excel_writer import ExcelWriter
from utils.run_manifest import write_run_manifest
from utils.hara_rules import (
    validate_all, compute_asil, validate_s2_s3_mode_coverage,
    validate_s1_downstream_exclusion, validate_s2_decisions_against_s1,
    validate_final_s1_against_intermediate,
    validate_s3_entries_against_s1_s2,
    validate_pt_gear_naming, validate_bd_naming, validate_et_naming,
    validate_failure_id_format,
)
from utils.compatible_baseline_contract import validate_s2_compatible_baselines, validate_s3_compatible_baselines
from utils.domain_pack_generation import (
    validate_compatible_s2_contract,
    validate_compatible_s3_scope_contract,
    validate_known_s2_contract,
    validate_known_s3_contract,
)


def _validate_s1_vs_s2s3(
    s1_path: str, s2_path: str, s3_path: str = None,
    intermediate_path: str = None,
) -> tuple[list, list]:
    """校验 S1=否（相关项功能清单 E 列=否、不做 HARA）的相关项，是否被错误地
    纳入了 S2 失效模式 / S3 HAZOP 分析。

    Returns:
        (errors, warnings) — 两个列表，errors 会阻断写入
    """
    errors = []
    warnings = []
    with open(s1_path, encoding="utf-8") as f:
        s1 = json.load(f)
    with open(s2_path, encoding="utf-8") as f:
        s2 = json.load(f)

    intermediate = None
    if intermediate_path:
        with open(intermediate_path, encoding="utf-8") as f:
            intermediate = json.load(f)

    # 最终 S1 必须精确复用 intermediate 中的 Domain Pack/规则锁定结论；
    # 先阻断父级/子功能矛盾，防止 Excel 与下游范围使用两套事实。
    for message in validate_final_s1_against_intermediate(s1, intermediate):
        errors.append(f"[S1合同] {message}")

    # S2 必须完整且只覆盖 S1=是功能；这是 P0-3 硬校验。
    for message in validate_s2_decisions_against_s1(s1, s2, intermediate):
        errors.append(f"[S2范围] {message}")
    for message in validate_s2_compatible_baselines(s2.get("decisions", [])):
        errors.append(f"[S2 compatible合同] {message}")
    if intermediate is not None:
        for message in validate_known_s2_contract(intermediate, s1, s2):
            errors.append(f"[S2 Pack合同] {message}")
        for message in validate_compatible_s2_contract(intermediate, s1, s2):
            errors.append(f"[S2 compatible派生合同] {message}")

    # S1=否 是硬边界：无论是否传 --force，都不能进入 S2/S3。
    s3_entries = []
    if s3_path and Path(s3_path).exists():
        with open(s3_path, encoding="utf-8") as f:
            s3 = json.load(f)
        s3_entries = s3.get("entries", [])

    if s3_path:
        s3_data = {"entries": s3_entries}
        # S3 功能/模式集合和最小结构是硬边界，不能通过 --force 绕过。
        for message in validate_s3_entries_against_s1_s2(
            s1, s2, s3_data, intermediate=intermediate
        ):
            errors.append(f"[S3范围] {message}")
        for message in validate_s3_compatible_baselines(s3_entries):
            errors.append(f"[S3 compatible合同] {message}")
        if intermediate is not None:
            for message in validate_known_s3_contract(intermediate, s1, s2, s3_data):
                errors.append(f"[S3 Pack合同] {message}")
            for message in validate_compatible_s3_scope_contract(intermediate, s1, s2, s3_data):
                errors.append(f"[S3 compatible派生合同] {message}")

    errors.extend(validate_s1_downstream_exclusion(s1, s2, s3_entries if s3_path else None))

    # ---- S2→S3 域特定失效模式检查 ----
    if s3_entries:
        with open(s2_path, encoding="utf-8") as f:
            s2 = json.load(f)
        mode_issues = validate_s2_s3_mode_coverage(
            s2, s3_entries, include_generic=False
        )
        for level, msg in mode_issues:
            if level == "error":
                errors.append(f"[S2→S3] {msg}")
            else:
                warnings.append(f"[S2→S3] {msg}")

        # ---- failure_id 格式 + 域前缀校验 ----
        # 从 s3 数据推断域前缀（取第一个 func_id 的前缀）
        domain_prefix = None
        for e in s3_entries:
            fid = e.get("func_id", "")
            if fid and "_func_" in fid:
                domain_prefix = fid.split("_func_")[0]
                break
        fid_issues = validate_failure_id_format(s3_entries, domain_prefix=domain_prefix)
        for level, msg in fid_issues:
            if level == "error":
                errors.append(f"[failure_id] {msg}")
            else:
                warnings.append(f"[failure_id] {msg}")

        # ---- PT 域档位控制命名白名单校验 ----
        gear_issues = validate_pt_gear_naming(s3_entries)
        for level, msg in gear_issues:
            if level == "error":
                errors.append(f"[PT档位控制] {msg}")
            else:
                warnings.append(f"[PT档位控制] {msg}")

        # ---- BD 域车身功能命名白名单校验 ----
        bd_issues = validate_bd_naming(s3_entries)
        for level, msg in bd_issues:
            if level == "error":
                errors.append(f"[BD域] {msg}")
            else:
                warnings.append(f"[BD域] {msg}")

        # ---- ET 域信息娱乐功能命名白名单校验 ----
        et_issues = validate_et_naming(s3_entries)
        for level, msg in et_issues:
            if level == "error":
                errors.append(f"[ET域] {msg}")
            else:
                warnings.append(f"[ET域] {msg}")

    return errors, warnings


def _require_s4_validation_passed(s4_path: str) -> None:
    """写入前只接受由成功 S4 validate 产出的正式 final 文件。"""
    marker = Path(f"{s4_path}.validation_failed")
    if marker.exists():
        raise ValueError(
            "检测到该 S4 final 的本轮验证失败标记；请修复后重新执行 'hara validate'"
        )

    with open(s4_path, "r", encoding="utf-8") as f:
        s4 = json.load(f)

    validation = s4.get("validation")
    if not isinstance(validation, dict):
        raise ValueError(
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
        raise ValueError(
            "S4 final 验证未通过；请修复 S4 错误后重新执行 'hara validate'"
        )


def _require_s5_validation_passed(s5_path: str) -> None:
    """拒绝包含 error 级安全目标质量问题的 S5 产物。"""
    with open(s5_path, "r", encoding="utf-8") as handle:
        s5 = json.load(handle)
    issues = s5.get("validation_issues")
    if not isinstance(issues, list):
        raise ValueError("S5 缺少 validation_issues；请重新执行 'sg generate'")
    errors = [
        issue for issue in issues
        if isinstance(issue, (list, tuple)) and len(issue) >= 2 and issue[0] == "error"
    ]
    if errors:
        raise ValueError(
            f"S5 安全目标验证未通过（{len(errors)} 个错误）；请修正 S4 安全目标后重新执行 'sg generate'"
        )


def _validate_s4_before_write(s4_path: str) -> tuple[bool, list]:
    """写 Excel 前验证 s4 JSON，返回 (通过, 问题列表)。

    有任何 error 级问题则不通过，拒绝写 Excel。
    会先计算 ASIL（如果尚未计算），以便预填合规检查判断 S=0/QM 事件。
    """
    with open(s4_path, "r", encoding="utf-8") as f:
        s4 = json.load(f)

    hazards = s4.get("hazards", [])

    # 确保 ASIL 已计算（validate 阶段会算，但防止用户直接传未 validate 的文件）
    for h in hazards:
        for ev in h.get("events", []):
            if ev.get("asil") is None and ev.get("severity") is not None:
                if ev.get("engineering_override") == "QM" and ev.get("severity") != 0:
                    ev["asil"] = "QM"
                elif ev.get("exposure") is not None and ev.get("controllability") is not None:
                    asil, _ = compute_asil(ev["severity"], ev["exposure"], ev["controllability"])
                    ev["asil"] = asil

    issues = validate_all(hazards)
    errors = [i for i in issues if i[0] == "error"]
    return len(errors) == 0, issues


def run(args):
    """被 run_hara.py 调用的入口"""
    intermediate = args.intermediate
    s1_path = args.s1_decisions
    s2_path = args.s2_decisions
    s3_path = getattr(args, "s3", None)
    s4_path = getattr(args, "s4", None)
    s5_path = getattr(args, "s5", None)
    force = getattr(args, "force", False)

    for p, label in [(intermediate, "intermediate"), (s1_path, "s1_decisions"), (s2_path, "s2_decisions")]:
        if not Path(p).exists():
            print(f"错误: {label} 文件不存在: {p}")
            raise SystemExit(1)
    if s3_path and not Path(s3_path).exists():
        print(f"错误: s3_hazop 文件不存在: {s3_path}")
        raise SystemExit(1)
    if s4_path and not Path(s4_path).exists():
        print(f"错误: s4_hara 文件不存在: {s4_path}")
        raise SystemExit(1)
    if s5_path and not Path(s5_path).exists():
        print(f"错误: s5_safety_goals 文件不存在: {s5_path}")
        raise SystemExit(1)
    if s5_path and not s4_path:
        print("错误: 写入整车安全目标(--s5)需要同时传入 --s4 s4_hara_final.json（事件级明细来源）")
        raise SystemExit(1)

    if s5_path:
        try:
            _require_s5_validation_passed(s5_path)
        except (ValueError, json.JSONDecodeError) as error:
            print(f"[write] S5 安全目标状态校验失败: {error}")
            raise SystemExit(1) from error

    # ---- S4 验证状态与内容均为硬边界，--force 不得绕过 ----
    if s4_path:
        try:
            _require_s4_validation_passed(s4_path)
        except (ValueError, json.JSONDecodeError) as error:
            print(f"[write] S4 final 状态校验失败: {error}")
            raise SystemExit(1) from error

        print("[write] 正在验证 s4_hara 数据...")
        passed, issues = _validate_s4_before_write(s4_path)
        errors = [i for i in issues if i[0] == "error"]
        warnings = [i for i in issues if i[0] == "warning"]

        if warnings:
            print(f"[write] 验证警告: {len(warnings)} 条")
            for level, msg in warnings[:10]:
                print(f"  [WARN] {msg}")
            if len(warnings) > 10:
                print(f"  ... 还有 {len(warnings) - 10} 条警告")

        if not passed:
            print(f"\n[write] 验证失败: {len(errors)} 个错误，拒绝写入 Excel。")
            print("[write] 请先运行 'python run_hara.py hara validate' 修复错误后再写 Excel。")
            print("[write] S4 验证错误属于硬阻断，--force 不能绕过。\n")
            for level, msg in errors[:20]:
                print(f"  [ERROR] {msg}")
            if len(errors) > 20:
                print(f"  ... 还有 {len(errors) - 20} 个错误")
            raise SystemExit(1)

        print(f"[write] s4 验证通过（{len(warnings)} 条警告，0 个错误）")

    # ---- 表间一致性 + 命名规范 + failure_id格式校验（error 会阻断写入）----
    try:
        s1s2s3_errors, s1s2s3_warnings = _validate_s1_vs_s2s3(
            s1_path, s2_path, s3_path, intermediate_path=intermediate
        )

        if s1s2s3_warnings:
            print(f"[write] S1/S2/S3 一致性警告: {len(s1s2s3_warnings)} 条（不阻断，请人工确认）")
            for w in s1s2s3_warnings[:10]:
                print(f"  [WARN] {w}")
            if len(s1s2s3_warnings) > 10:
                print(f"  ... 还有 {len(s1s2s3_warnings) - 10} 条警告")
        else:
            print("[write] S1/S2/S3 一致性检查通过")

        # S1→S2/S3 和 S2 范围错误都属于阶段边界错误，--force 不得绕过。
        boundary_errors = [
            e for e in s1s2s3_errors
            if e.startswith("[S1合同]") or e.startswith("[S1=否→")
            or e.startswith("[S2范围]") or e.startswith("[S3范围]")
        ]
        bypassable_errors = [e for e in s1s2s3_errors if e not in boundary_errors]
        if boundary_errors or (bypassable_errors and not force):
            print(f"\n[write] S1/S2/S3 校验失败: {len(s1s2s3_errors)} 个错误，拒绝写入 Excel。")
            print("[write] 请先修复以下错误后再写入：")
            for err in s1s2s3_errors[:20]:
                print(f"  [ERROR] {err}")
            if len(s1s2s3_errors) > 20:
                print(f"  ... 还有 {len(s1s2s3_errors) - 20} 个错误")
            if boundary_errors:
                print("\n[write] S1/S2/S3 阶段边界错误属于硬阻断，--force 也不能绕过。")
            else:
                print("\n[write] （紧急情况可加 --force 跳过非边界验证，但不推荐）")
            raise SystemExit(1)
    except Exception as e:
        # 一致性校验无法执行时必须失败关闭，不能把 S1 边界检查静默跳过。
        print(f"[write] S1/S2/S3 一致性检查失败，拒绝写入（{e}）")
        raise SystemExit(1) from e

    template = getattr(args, "template", None)
    if not template:
        # stage_write.py 在 scripts/stages/ 下，需上溯三层到项目根目录
        default = Path(__file__).resolve().parent.parent.parent / "assets" / "template.xlsx"
        if default.exists():
            template = str(default)

    output = args.output or "output/result.xlsx"

    print(f"intermediate: {intermediate}")
    print(f"s1_decisions: {s1_path}")
    print(f"s2_decisions: {s2_path}")
    if s3_path:
        print(f"s3_hazop:     {s3_path}")
    if s4_path:
        print(f"s4_hara:      {s4_path}")
    if template:
        print(f"模板: {template}")
    print(f"输出: {output}")

    writer = ExcelWriter(template_path=template)
    writer.write(intermediate, s1_path, s2_path, output,
                 s3_hazop_path=s3_path, s4_hara_path=s4_path,
                 s5_safety_goals_path=s5_path)
    manifest_path = write_run_manifest(
        output,
        intermediate=intermediate,
        s1=s1_path,
        s2=s2_path,
        s3=s3_path,
        s4=s4_path,
        s5=s5_path,
        template=template,
    )
    print(f"[write] 运行清单: {manifest_path.resolve()}")
    print(f"\n完成! 输出: {Path(output).resolve()}")
