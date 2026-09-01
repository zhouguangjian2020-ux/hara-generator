# scripts/utils/quality_gate.py
# HARA 输出质量断言（Quality Gate）— 定义什么算"合格"的 HARA 输出

# 质量等级
QUALITY_LEVELS = {
    "excellent": "优秀",   # 0 errors, <= 5 warnings
    "good": "良好",        # 0 errors, <= 20 warnings
    "acceptable": "合格",  # 0 errors, <= 50 warnings
    "poor": "不合格",      # > 0 errors 或 > 50 warnings
}


def evaluate_quality(errors: int, warnings: int) -> dict:
    """评估 HARA 输出质量等级。

    Returns:
        {
            "level": str,  # excellent/good/acceptable/poor
            "label": str,  # 中文标签
            "errors": int,
            "warnings": int,
            "passed": bool,  # 是否通过质量门（acceptable 及以上算通过）
            "details": str,  # 说明文字
        }
    """
    if errors > 0 or warnings > 50:
        level = "poor"
    elif warnings <= 5:
        level = "excellent"
    elif warnings <= 20:
        level = "good"
    else:
        level = "acceptable"

    passed = level != "poor"

    details = f"错误 {errors} 个，警告 {warnings} 个"
    if level == "poor":
        details += "，不满足质量要求，请先修复错误"
    elif level == "excellent":
        details += "，质量优秀"
    elif level == "good":
        details += "，质量良好"
    else:
        details += "，质量合格（警告较多，建议优化）"

    return {
        "level": level,
        "label": QUALITY_LEVELS[level],
        "errors": errors,
        "warnings": warnings,
        "passed": passed,
        "details": details,
    }


# 分类质量断言：按维度检查
def assert_dimension_quality(dimension: str, errors: list, warnings: list,
                              max_errors: int = 0, max_warnings: int = None) -> dict:
    """按维度检查质量是否达标。

    Args:
        dimension: 维度名称（如"命名规范"、"SEC评级"等）
        errors: 该维度的 error 列表
        warnings: 该维度的 warning 列表
        max_errors: 最大允许 error 数，默认 0
        max_warnings: 最大允许 warning 数，None 表示不限制

    Returns:
        {
            "dimension": str,
            "errors": int,
            "warnings": int,
            "passed": bool,
            "reason": str,
        }
    """
    err_count = len(errors)
    warn_count = len(warnings)

    passed = err_count <= max_errors
    reason = ""

    if err_count > max_errors:
        reason = f"错误数 {err_count} 超过限值 {max_errors}"
    elif max_warnings is not None and warn_count > max_warnings:
        reason = f"警告数 {warn_count} 超过限值 {max_warnings}"
        passed = False
    else:
        reason = "通过"

    return {
        "dimension": dimension,
        "errors": err_count,
        "warnings": warn_count,
        "passed": passed,
        "reason": reason,
    }
