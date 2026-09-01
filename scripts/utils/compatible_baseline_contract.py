"""受控 compatible 基线的 S2/S3 差异合同。

PT reference 的 compatible 资产不是 exact lock：Agent 可以确认或按项目差异调整，
但不能静默改写、删除或抹掉来源标识。此模块只处理包含
``agent_action=review_compatible_baseline`` 的 JSON；旧域/未迁移项维持原路径。
"""

from __future__ import annotations

from typing import Any


_REVIEW_ACTION = "review_compatible_baseline"


def _read_review(payload: dict[str, Any], prefix: str, errors: list[str]) -> tuple[str | None, bool]:
    review = payload.get("agent_review")
    if not isinstance(review, dict):
        errors.append(f"{prefix} 缺少 compatible 基线的 agent_review")
        return None, False
    status = review.get("status")
    summary = review.get("summary")
    if status not in {"confirmed", "adjusted"}:
        errors.append(f"{prefix} compatible 基线必须将 agent_review.status 设为 confirmed 或 adjusted")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{prefix} compatible 基线必须填写 agent_review.summary，说明与当前项目是否一致")
    return status if isinstance(status, str) else None, True


def _read_changes(
    payload: dict[str, Any], prefix: str, allowed_actions: set[str],
    id_field: str, known_ids: set[str], errors: list[str],
) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    """验证 compatible_baseline_changes 的受控形状。

    返回：(以 action/id 作为键的变更, add 数量)。add 不引用 baseline ID，
    所以按出现次数统计；其余 action 对同一 baseline ID 只能出现一次。
    """
    raw = payload.get("compatible_baseline_changes", [])
    if not isinstance(raw, list):
        errors.append(f"{prefix}.compatible_baseline_changes 必须是数组")
        return {}, 0
    result: dict[tuple[str, str], dict[str, Any]] = {}
    add_count = 0
    for index, change in enumerate(raw):
        cp = f"{prefix}.compatible_baseline_changes[{index}]"
        if not isinstance(change, dict):
            errors.append(f"{cp} 必须是对象")
            continue
        action = change.get("action")
        reason = change.get("difference_reason")
        if action not in allowed_actions:
            errors.append(f"{cp}.action 仅允许 {'/'.join(sorted(allowed_actions))}")
            continue
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{cp} 必须填写 difference_reason")
        baseline_id = change.get(id_field)
        if action == "add":
            add_count += 1
            if baseline_id not in (None, ""):
                errors.append(f"{cp}: add 不得引用 {id_field}")
            continue
        if not isinstance(baseline_id, str) or baseline_id not in known_ids:
            errors.append(f"{cp} 引用了未知 {id_field}")
            continue
        key = (action, baseline_id)
        if key in result:
            errors.append(f"{cp} 与同一 {id_field} 的 {action} 处置重复")
        result[key] = change
    return result, add_count


def validate_s2_compatible_baselines(decisions: list[dict[str, Any]]) -> list[str]:
    """验证 S2 compatible 失效模式基线是否被确认或有理由地调整。"""
    errors: list[str] = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or decision.get("agent_action") != _REVIEW_ACTION:
            continue
        prefix = f"S2 decisions[{index}] ({decision.get('func_id', '?')})"
        if decision.get("evidence_status") != "compatible":
            errors.append(f"{prefix} 具有 {_REVIEW_ACTION} 时必须标记 evidence_status=compatible")
        review_status, _ = _read_review(decision, prefix, errors)
        baseline = decision.get("compatible_baseline")
        if not isinstance(baseline, dict):
            errors.append(f"{prefix} 缺少 compatible_baseline 快照")
            continue
        baseline_modes = baseline.get("selected_modes")
        actual_modes = decision.get("selected_modes")
        if not isinstance(baseline_modes, list) or not all(isinstance(mode, str) and mode for mode in baseline_modes):
            errors.append(f"{prefix}.compatible_baseline.selected_modes 必须是非空字符串数组")
            continue
        if not isinstance(actual_modes, list) or not all(isinstance(mode, str) and mode for mode in actual_modes):
            errors.append(f"{prefix}.selected_modes 必须是非空字符串数组")
            continue
        baseline_set, actual_set = set(baseline_modes), set(actual_modes)
        # S2 的差异使用 add_mode/remove_mode，和 S3 的 add/modify/remove 不是同一语义，
        # 因此在本函数中单独严格校验。
        raw_changes = decision.get("compatible_baseline_changes", [])
        declared: set[tuple[str, str]] = set()
        if not isinstance(raw_changes, list):
            errors.append(f"{prefix}.compatible_baseline_changes 必须是数组")
            raw_changes = []
        for change_index, change in enumerate(raw_changes):
            cp = f"{prefix}.compatible_baseline_changes[{change_index}]"
            if not isinstance(change, dict):
                errors.append(f"{cp} 必须是对象")
                continue
            action = change.get("action")
            mode = change.get("failure_mode")
            reason = change.get("difference_reason")
            if action not in {"add_mode", "remove_mode"}:
                errors.append(f"{cp}.action 仅允许 add_mode/remove_mode")
                continue
            if not isinstance(mode, str) or not mode:
                errors.append(f"{cp}.failure_mode 必须是非空字符串")
                continue
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{cp} 必须填写 difference_reason")
            key = (action, mode)
            if key in declared:
                errors.append(f"{cp} 与同一失效模式的 {action} 处置重复")
            declared.add(key)
        expected = {("add_mode", mode) for mode in actual_set - baseline_set} | {
            ("remove_mode", mode) for mode in baseline_set - actual_set
        }
        if expected != declared:
            missing = sorted(expected - declared)
            unexpected = sorted(declared - expected)
            if missing:
                errors.append(f"{prefix} 模式差异缺少受控登记: {missing}")
            if unexpected:
                errors.append(f"{prefix} 声明了与实际模式集合不一致的差异: {unexpected}")
        changed = bool(expected)
        if review_status == "confirmed" and changed:
            errors.append(f"{prefix} 有模式差异时 agent_review.status 必须为 adjusted")
        if review_status == "adjusted" and not changed:
            errors.append(f"{prefix} agent_review.status=adjusted 但 selected_modes 未发生差异")
    return errors


def _anomaly_view(anomaly: dict[str, Any]) -> dict[str, Any]:
    """只比较工程语义字段；hazard 顺序和 Excel 展示顺序不是业务主键。"""
    hazards = anomaly.get("hazards") if isinstance(anomaly.get("hazards"), list) else []
    hazard_view = [
        {
            "description": hazard.get("description"),
            "associated_hara": hazard.get("associated_hara"),
            "vehicle_hazard_id": hazard.get("vehicle_hazard_id"),
            "hazard_family": hazard.get("hazard_family"),
        }
        for hazard in hazards if isinstance(hazard, dict)
    ]
    return {
        "failure_id": anomaly.get("failure_id"),
        "description": anomaly.get("description"),
        "vehicle_hazard_id": anomaly.get("vehicle_hazard_id"),
        "hazard_family": anomaly.get("hazard_family"),
        "hazards": sorted(hazard_view, key=lambda value: (
            str(value.get("vehicle_hazard_id") or ""), str(value.get("description") or ""),
            str(value.get("associated_hara") or ""),
        )),
    }


def validate_s3_compatible_baselines(entries: list[dict[str, Any]]) -> list[str]:
    """验证 S3 compatible HAZOP 基线未被静默改写。"""
    errors: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("agent_action") != _REVIEW_ACTION:
            continue
        prefix = f"S3 entries[{index}] ({entry.get('func_id', '?')} / {entry.get('failure_mode', '?')})"
        if entry.get("evidence_status") != "compatible":
            errors.append(f"{prefix} 具有 {_REVIEW_ACTION} 时必须标记 evidence_status=compatible")
        review_status, _ = _read_review(entry, prefix, errors)
        baseline = entry.get("compatible_baseline")
        if not isinstance(baseline, dict):
            errors.append(f"{prefix} 缺少 compatible_baseline 快照")
            continue
        for field in ("failure_mode", "analysis_unit_id"):
            if entry.get(field) != baseline.get(field):
                errors.append(f"{prefix} 的 {field} 不得脱离 compatible 基线修改")
        baseline_anomalies = baseline.get("anomalies")
        if not isinstance(baseline_anomalies, list) or not baseline_anomalies:
            errors.append(f"{prefix}.compatible_baseline.anomalies 必须是非空数组")
            continue
        baseline_by_id: dict[str, dict[str, Any]] = {}
        for anomaly_index, anomaly in enumerate(baseline_anomalies):
            ap = f"{prefix}.compatible_baseline.anomalies[{anomaly_index}]"
            if not isinstance(anomaly, dict):
                errors.append(f"{ap} 必须是对象")
                continue
            baseline_id = anomaly.get("baseline_failure_id")
            if not isinstance(baseline_id, str) or not baseline_id or baseline_id in baseline_by_id:
                errors.append(f"{ap}.baseline_failure_id 缺失或重复")
                continue
            baseline_by_id[baseline_id] = anomaly
        if not baseline_by_id:
            continue
        changes, add_count = _read_changes(
            entry, prefix, {"modify", "remove", "add"}, "baseline_failure_id", set(baseline_by_id), errors
        )
        actual_anomalies = entry.get("anomalies")
        if not isinstance(actual_anomalies, list) or not actual_anomalies:
            errors.append(f"{prefix}.anomalies 必须是非空数组")
            continue
        actual_by_id: dict[str, dict[str, Any]] = {}
        added = 0
        for anomaly_index, anomaly in enumerate(actual_anomalies):
            ap = f"{prefix}.anomalies[{anomaly_index}]"
            if not isinstance(anomaly, dict):
                errors.append(f"{ap} 必须是对象")
                continue
            baseline_id = anomaly.get("baseline_failure_id")
            if isinstance(baseline_id, str) and baseline_id:
                if baseline_id not in baseline_by_id:
                    errors.append(f"{ap} 使用未知 baseline_failure_id")
                    continue
                if baseline_id in actual_by_id:
                    errors.append(f"{ap} baseline_failure_id 重复")
                    continue
                actual_by_id[baseline_id] = anomaly
                if anomaly.get("evidence_status") != "compatible":
                    errors.append(f"{ap} 保留 baseline_failure_id 时必须标记 evidence_status=compatible")
                baseline_anomaly = baseline_by_id[baseline_id]
                if anomaly.get("failure_id") != baseline_anomaly.get("failure_id"):
                    errors.append(f"{ap} 保留 baseline_failure_id 时不得修改 failure_id")
                if anomaly.get("reference_failure_ids") != baseline_anomaly.get("reference_failure_ids"):
                    errors.append(f"{ap} 不得删除或改写 reference_failure_ids 来源追溯")
            else:
                added += 1
                if anomaly.get("evidence_status") != "project_specific":
                    errors.append(f"{ap} 新增异常必须标记 evidence_status=project_specific")
                if not isinstance(anomaly.get("difference_reason"), str) or not anomaly.get("difference_reason").strip():
                    errors.append(f"{ap} 新增异常必须填写 difference_reason")
        changed = False
        for baseline_id, baseline_anomaly in baseline_by_id.items():
            actual = actual_by_id.get(baseline_id)
            change = changes.get(("modify", baseline_id))
            remove = changes.get(("remove", baseline_id))
            if actual is None:
                changed = True
                if remove is None:
                    errors.append(f"{prefix} 删除了基线异常 {baseline_id}，必须声明 remove 差异和理由")
                continue
            if remove is not None:
                errors.append(f"{prefix} {baseline_id} 标记 remove 但异常仍存在")
                continue
            if _anomaly_view(actual) != _anomaly_view(baseline_anomaly):
                changed = True
                if change is None:
                    errors.append(f"{prefix} 修改了基线异常 {baseline_id}，必须声明 modify 差异和理由")
            elif change is not None:
                errors.append(f"{prefix} {baseline_id} 声明 modify 但工程语义字段未发生变化")
        if add_count != added:
            errors.append(f"{prefix} 新增异常数量与 add 差异登记不一致（新增 {added}，登记 {add_count}）")
        if added:
            changed = True
        if review_status == "confirmed" and changed:
            errors.append(f"{prefix} 有 HAZOP 差异时 agent_review.status 必须为 adjusted")
        if review_status == "adjusted" and not changed:
            errors.append(f"{prefix} agent_review.status=adjusted 但未提供实际 HAZOP 差异")
    return errors
