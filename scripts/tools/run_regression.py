# scripts/tools/run_regression.py
# 统一的全量回归测试入口：加载 S1/S2/S3/S4 数据，依次运行所有验证规则，
# 按维度分类统计 errors/warnings，调用 quality_gate 评估质量，输出汇总报告。
#
# 用法：
#   python scripts/tools/run_regression.py --domain B --input-dir test_data/ --output reports/regression_report.txt
#
# 支持的命令行参数：
#   --domain <域>       域代码（如 B、CB、Info、P），用于命名校验等
#   --input-dir <目录>  输入数据目录，应包含 s1.json、s2.json、s3.json、s4_hara.json
#   --output <报告路径>  输出报告文件路径（文本格式）

import argparse
import json
import sys
import os
from pathlib import Path

# 确保 scripts/ 在 path 中
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.hara_rules import (
    validate_all,
    validate_failure_id_format,
    validate_bd_naming,
    validate_et_naming,
    validate_pt_gear_naming,
    validate_s2_s3_mode_coverage,
    validate_s1_coverage,
)
from utils.safety_goal_rules import generate_safety_goals
from utils.quality_gate import evaluate_quality, assert_dimension_quality


# ============================================================
# 数据加载
# ============================================================

def load_json(filepath: str) -> dict:
    """加载 JSON 文件，不存在时返回 None。

    兼容 utf-8 和 utf-8-sig（带 BOM）编码。
    """
    if not filepath or not Path(filepath).exists():
        return None
    try:
        with open(filepath, encoding="utf-8-sig") as f:
            return json.load(f)
    except UnicodeDecodeError:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)


def load_test_data(input_dir: str) -> dict:
    """从指定目录加载测试数据。

    预期文件：s1.json、s2.json、s3.json、s4_hara.json

    Returns:
        {
            "s1": dict or None,
            "s2": dict or None,
            "s3": dict or None,
            "s4": dict or None,
        }
    """
    d = Path(input_dir)
    return {
        "s1": load_json(str(d / "s1.json")),
        "s2": load_json(str(d / "s2.json")),
        "s3": load_json(str(d / "s3.json")),
        "s4": load_json(str(d / "s4_hara.json")),
    }


# ============================================================
# S1/S2/S3 一致性校验（核心逻辑参考 _validate_s1_vs_s2s3）
# ============================================================

def validate_s1_s2_s3_consistency(s1_data: dict, s2_data: dict, s3_data: dict) -> list:
    """校验 S1/S2/S3 表间一致性。

    检查内容：
    1. S1=否 的功能不应出现在 S2/S3 中
    2. S1=是 的功能应出现在 S3 HAZOP 中（validate_s1_coverage）
    3. S2→S3 失效模式覆盖（validate_s2_s3_mode_coverage）

    Returns:
        list of (level, message)
    """
    issues = []

    if not s1_data or not s2_data:
        issues.append(("warning", "S1/S2 数据缺失，跳过 S1/S2/S3 一致性检查"))
        return issues

    s3_entries = s3_data.get("entries", []) if s3_data else []

    # 1. S1=否 的功能不应出现在 S2/S3
    s1_no_funcs = set()
    for d in s1_data.get("decisions", []):
        subs = d.get("sub_functions", [])
        if subs and all(not s.get("is_hara", True) for s in subs):
            s1_no_funcs.add(d["func_id"])

    s2_funcs = set(d.get("func_id", "") for d in s2_data.get("decisions", []) if d.get("func_id"))
    s3_funcs = set(e.get("func_id", "") for e in s3_entries if e.get("func_id"))

    for fid in sorted(s1_no_funcs & s2_funcs):
        issues.append(("warning",
            f"[S1=否] 功能 {fid} 出现在失效模式(S2)中，但 S1 判定不做 HARA，请确认是否为过度分析"))

    for fid in sorted(s1_no_funcs & s3_funcs):
        issues.append(("warning",
            f"[S1=否] 功能 {fid} 出现在 HAZOP(S3)中，但 S1 判定不做 HARA，请确认是否为过度分析"))

    # 2. S1=是 → S3 覆盖检查
    if s3_entries:
        s1_coverage_issues = validate_s1_coverage(s1_data, s3_entries)
        issues.extend(s1_coverage_issues)

    # 3. S2→S3 失效模式覆盖检查
    if s3_entries:
        s2s3_issues = validate_s2_s3_mode_coverage(s2_data, s3_entries)
        issues.extend(s2s3_issues)

    return issues


# ============================================================
# 主回归测试逻辑
# ============================================================

def run_regression(input_dir: str, domain: str = None) -> dict:
    """执行全量回归测试。

    Args:
        input_dir: 输入数据目录
        domain: 域代码（可选，自动推断优先）

    Returns:
        {
            "dimensions": {dim_name: {"errors": [...], "warnings": [...]}},
            "total_errors": int,
            "total_warnings": int,
            "quality": dict,  # evaluate_quality 返回值
            "dimension_results": [dict, ...],  # assert_dimension_quality 返回值列表
        }
    """
    # 加载数据
    data = load_test_data(input_dir)
    s1 = data["s1"]
    s2 = data["s2"]
    s3 = data["s3"]
    s4 = data["s4"]

    s3_entries = s3.get("entries", []) if s3 else []
    s4_hazards = s4.get("hazards", []) if s4 else []

    # 自动推断域
    if not domain:
        if s4 and s4.get("domain"):
            domain = s4["domain"]
        elif s3_entries:
            for e in s3_entries:
                fid = e.get("func_id", "")
                if fid and "_func_" in fid:
                    domain = fid.split("_func_")[0]
                    break
        if not domain:
            domain = "X"

    # 按维度分类存储 issues
    dimensions = {
        "S1/S2/S3一致性": {"errors": [], "warnings": []},
        "failure_id格式": {"errors": [], "warnings": []},
        "BD域命名规范": {"errors": [], "warnings": []},
        "ET域命名规范": {"errors": [], "warnings": []},
        "PT域档位命名": {"errors": [], "warnings": []},
        "S2→S3模式覆盖": {"errors": [], "warnings": []},
        "HARA全量验证": {"errors": [], "warnings": []},
        "安全目标验证": {"errors": [], "warnings": []},
    }

    # ---- 1. S1/S2/S3 一致性 ----
    if s1 and s2:
        s1s2s3_issues = validate_s1_s2_s3_consistency(s1, s2, s3)
        for level, msg in s1s2s3_issues:
            if level == "error":
                dimensions["S1/S2/S3一致性"]["errors"].append(msg)
            else:
                dimensions["S1/S2/S3一致性"]["warnings"].append(msg)

    # ---- 2. failure_id 格式校验 ----
    if s3_entries:
        fid_issues = validate_failure_id_format(s3_entries, domain_prefix=domain)
        for level, msg in fid_issues:
            if level == "error":
                dimensions["failure_id格式"]["errors"].append(msg)
            else:
                dimensions["failure_id格式"]["warnings"].append(msg)

    # ---- 3. BD 域命名校验 ----
    if s3_entries:
        bd_issues = validate_bd_naming(s3_entries)
        for level, msg in bd_issues:
            if level == "error":
                dimensions["BD域命名规范"]["errors"].append(msg)
            else:
                dimensions["BD域命名规范"]["warnings"].append(msg)

    # ---- 4. ET 域命名校验 ----
    if s3_entries:
        et_issues = validate_et_naming(s3_entries)
        for level, msg in et_issues:
            if level == "error":
                dimensions["ET域命名规范"]["errors"].append(msg)
            else:
                dimensions["ET域命名规范"]["warnings"].append(msg)

    # ---- 5. PT 域档位命名校验 ----
    if s3_entries:
        pt_issues = validate_pt_gear_naming(s3_entries)
        for level, msg in pt_issues:
            if level == "error":
                dimensions["PT域档位命名"]["errors"].append(msg)
            else:
                dimensions["PT域档位命名"]["warnings"].append(msg)

    # ---- 6. S2→S3 模式覆盖（已包含在 S1/S2/S3 一致性中，此处单独统计便于查看） ----
    # 注意：S2→S3 模式覆盖已在 validate_s1_s2_s3_consistency 中调用过，
    # 这里单独列出是为了按维度清晰展示。为避免重复统计，只在 s1 缺失时单独统计。
    if s2 and s3_entries and not s1:
        mode_issues = validate_s2_s3_mode_coverage(s2, s3_entries)
        for level, msg in mode_issues:
            if level == "error":
                dimensions["S2→S3模式覆盖"]["errors"].append(msg)
            else:
                dimensions["S2→S3模式覆盖"]["warnings"].append(msg)

    # ---- 7. HARA 全量验证 ----
    if s4_hazards:
        # validate_all 需要 hazards 列表和可选的 s3_hazards
        hara_issues = validate_all(s4_hazards, s3_hazards=s3_entries)
        for level, msg in hara_issues:
            if level == "error":
                dimensions["HARA全量验证"]["errors"].append(msg)
            else:
                dimensions["HARA全量验证"]["warnings"].append(msg)

    # ---- 8. 安全目标验证 ----
    if s4:
        # generate_safety_goals 返回的结果中包含 validation_issues
        sg_result = generate_safety_goals(s4)
        sg_issues = sg_result.get("validation_issues", [])
        for level, msg in sg_issues:
            if level == "error":
                dimensions["安全目标验证"]["errors"].append(msg)
            else:
                dimensions["安全目标验证"]["warnings"].append(msg)

    # 统计总数
    total_errors = sum(len(d["errors"]) for d in dimensions.values())
    total_warnings = sum(len(d["warnings"]) for d in dimensions.values())

    # 按维度质量断言
    dimension_results = []
    # 命名规范类：0 error 为必须，warning 不限制
    naming_dims = ["failure_id格式", "BD域命名规范", "ET域命名规范", "PT域档位命名"]
    for dim in naming_dims:
        dim_data = dimensions[dim]
        result = assert_dimension_quality(dim, dim_data["errors"], dim_data["warnings"],
                                          max_errors=0, max_warnings=None)
        dimension_results.append(result)

    # 一致性类：0 error 为必须，warning 不限制
    consistency_dims = ["S1/S2/S3一致性", "S2→S3模式覆盖"]
    for dim in consistency_dims:
        dim_data = dimensions[dim]
        result = assert_dimension_quality(dim, dim_data["errors"], dim_data["warnings"],
                                          max_errors=0, max_warnings=None)
        dimension_results.append(result)

    # HARA 验证类：0 error 为必须，warning 上限 30
    hara_dims = ["HARA全量验证", "安全目标验证"]
    for dim in hara_dims:
        dim_data = dimensions[dim]
        result = assert_dimension_quality(dim, dim_data["errors"], dim_data["warnings"],
                                          max_errors=0, max_warnings=30)
        dimension_results.append(result)

    # 总体质量评估
    quality = evaluate_quality(total_errors, total_warnings)

    return {
        "domain": domain,
        "input_dir": input_dir,
        "dimensions": dimensions,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "quality": quality,
        "dimension_results": dimension_results,
    }


# ============================================================
# 报告生成
# ============================================================

def generate_report(result: dict) -> str:
    """生成文本格式的汇总报告。"""
    lines = []
    sep = "=" * 70

    lines.append(sep)
    lines.append("HARA 全量回归测试报告")
    lines.append(sep)
    lines.append(f"域: {result['domain']}")
    lines.append(f"输入目录: {result['input_dir']}")
    lines.append("")

    # 总体质量
    q = result["quality"]
    status_icon = "PASS" if q["passed"] else "FAIL"
    lines.append(sep)
    lines.append(f"质量门结果: [{status_icon}] {q['label']} ({q['level']})")
    lines.append(f"  {q['details']}")
    lines.append(f"  总计: {result['total_errors']} 个错误, {result['total_warnings']} 个警告")
    lines.append(sep)
    lines.append("")

    # 各维度结果
    lines.append(sep)
    lines.append("各维度质量断言")
    lines.append(sep)
    lines.append(f"{'维度':<20} {'状态':<8} {'错误':>6} {'警告':>6}  说明")
    lines.append("-" * 70)
    for dr in result["dimension_results"]:
        status = "PASS" if dr["passed"] else "FAIL"
        lines.append(f"{dr['dimension']:<20} {status:<8} {dr['errors']:>6} {dr['warnings']:>6}  {dr['reason']}")
    lines.append("")

    # 各维度详细问题（仅列出前 5 条 error 和前 5 条 warning）
    lines.append(sep)
    lines.append("各维度详细问题（每个维度最多显示 5 条 error + 5 条 warning）")
    lines.append(sep)

    for dim_name, dim_data in result["dimensions"].items():
        errors = dim_data["errors"]
        warnings = dim_data["warnings"]
        if not errors and not warnings:
            continue

        lines.append("")
        lines.append(f"--- {dim_name} ---")

        if errors:
            lines.append(f"  Errors ({len(errors)}):")
            for err in errors[:5]:
                lines.append(f"    [E] {err}")
            if len(errors) > 5:
                lines.append(f"    ... 还有 {len(errors) - 5} 条错误")

        if warnings:
            lines.append(f"  Warnings ({len(warnings)}):")
            for warn in warnings[:5]:
                lines.append(f"    [W] {warn}")
            if len(warnings) > 5:
                lines.append(f"    ... 还有 {len(warnings) - 5} 条警告")

    lines.append("")
    lines.append(sep)
    lines.append("报告结束")
    lines.append(sep)

    return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="HARA 全量回归测试工具")
    parser.add_argument("--domain", type=str, default=None,
                        help="域代码（如 B、CB、Info、P），默认自动推断")
    parser.add_argument("--input-dir", type=str, required=True,
                        help="输入数据目录，应包含 s1.json、s2.json、s3.json、s4_hara.json")
    parser.add_argument("--output", type=str, default=None,
                        help="输出报告文件路径（文本格式），不指定则输出到控制台")

    args = parser.parse_args()

    # 执行回归测试
    result = run_regression(args.input_dir, domain=args.domain)

    # 生成报告
    report = generate_report(result)

    # 输出
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入: {args.output}")
        print(f"质量门结果: {result['quality']['label']} ({result['quality']['level']})")
    else:
        print(report)

    # 返回退出码
    if result["quality"]["passed"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
