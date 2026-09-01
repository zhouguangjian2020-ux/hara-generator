# scripts/stages/stage_hara_simple.py
# S4 HARA 预填草稿：兼容 finalize 命令，但不绕过正式 validate 闸门

import json
import os
import sys
import tempfile
from pathlib import Path

# 确保 scripts/ 在 path 中
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from utils.case_library import get_case_library
from utils.data_models import canonicalize_domain


class S4ValidationError(ValueError):
    """S4 验证未通过"""


def _load_json(path: str, label: str) -> dict:
    """加载JSON文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"错误: {label} 文件不存在: {path}")
        raise
    except json.JSONDecodeError as e:
        print(f"错误: {label} JSON 格式无效: {e}")
        raise


def _save_json_atomic(data: dict, path: str):
    """原子写入JSON文件"""
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
    print(f"[S4] 已保存: {out}")


def _is_skip_hazard(hazard_desc: str) -> bool:
    """判断是否为skip危害"""
    if not isinstance(hazard_desc, str) or not hazard_desc.strip():
        return False
    skip_keywords = ("不涉及", "无整车层面危害", "无整车危害", "无危害", "N/A", "n/a")
    return any(keyword in hazard_desc for keyword in skip_keywords)


def finalize(s3_path: str, output_path: str = "s4_hara_prefill_draft.json"):
    """从 S3 生成案例库语义候选草稿，不生成正式 S4 final。

    该兼容命令只负责危害分组和 exact/similar/template 候选投影。
    ASIL、hazard_id、safety_goal_id 和正式 validation metadata 必须由
    ``hara validate`` 在工程确认后生成。
    """
    print(f"\n{'='*80}")
    print("[S4 prefill draft] 开始生成候选草稿")
    print(f"{'='*80}\n")

    # 1. 加载S3数据
    s3 = _load_json(s3_path, "s3_hazop")
    raw_domain = s3.get("domain", "")
    domain = canonicalize_domain(raw_domain)
    entries = s3.get("entries", [])
    if not domain or domain == "MULTI":
        # MULTI 只能由 entry 自带 domain 逐项解析；不能拿一个未知域继续跑案例库。
        if domain != "MULTI":
            raise ValueError("S3 缺少有效 canonical domain，无法加载 Domain Pack/案例库")

    if not entries:
        print("[S4] 警告: s3_hazop.json 中无 entries")
        return

    print(f"[S4] 域: {domain or raw_domain}")
    print(f"[S4] S3 entries: {len(entries)}")

    # 2. 加载案例库；P/PT 等历史别名统一到同一 canonical 资产。
    case_lib = get_case_library()
    if domain and domain != "MULTI":
        if not case_lib.load_domain(domain):
            print(f"[S4] 警告: 域 {domain} 没有可用 v2 案例库，后续只生成空骨架")
        stats = case_lib.get_statistics(domain)
        if stats:
            print(f"[S4] 案例库: {stats['total_cases']} 个案例")

    # 3. 生成危害组并应用预填
    hazards = []
    hazard_counter = {}

    exact_match_count = 0
    similar_match_count = 0
    template_match_count = 0
    no_match_count = 0

    for entry in entries:
        func_id = entry.get("func_id", "")
        func_name = entry.get("func_name", "")
        failure_mode = entry.get("failure_mode", "")
        analysis_unit_id = entry.get("analysis_unit_id", "")
        entry_domain = canonicalize_domain(entry.get("domain") or domain)
        if not entry_domain or entry_domain == "MULTI":
            raise ValueError(
                f"S3 entry {func_id or '<unknown>'} 缺少有效域；"
                "MULTI 输入必须在 entry 上提供 domain"
            )
        if entry_domain != domain and domain not in {"", "MULTI"}:
            raise ValueError(
                f"S3 entry {func_id or '<unknown>'} 的域 {entry_domain} 与顶层域 {domain} 不一致"
            )
        if not case_lib.load_domain(entry_domain):
            print(f"[S4] 警告: entry {func_id or '<unknown>'} 的域 {entry_domain} 无案例库")

        # 输入侧只从功能/失效/危害/场景语义构建 profile；func_id/failure_id 仅保留追溯。
        function_profile = entry.get("function_profile")
        if not isinstance(function_profile, dict) or not function_profile.get("canonical_function_family"):
            function_profile = case_lib.build_function_profile(
                entry_domain,
                function_name=func_name,
                semantic_texts=entry.get("function_semantic_texts", []),
                analysis_unit_id=analysis_unit_id,
            )

        for anom in entry.get("anomalies", []):
            failure_id = anom.get("failure_id", "")
            anom_desc = anom.get("description", "")

            # 支持hazards列表
            hazards_list = anom.get("hazards")
            if hazards_list is None:
                h = anom.get("hazard", "")
                hazards_list = [{"description": h}]

            for hz in hazards_list:
                hz_desc = hz.get("description", "")

                # 生成hazard_key
                if failure_id not in hazard_counter:
                    hazard_counter[failure_id] = 0
                hazard_counter[failure_id] += 1
                hazard_key = f"{failure_id}__{hazard_counter[failure_id]}"

                # 判断skip
                skip = _is_skip_hazard(hz_desc)

                # 初始化危害组
                hazard_group = {
                    "hazard_key": hazard_key,
                    "func_id": func_id,
                    "func_name": func_name,
                    "analysis_unit_id": analysis_unit_id,
                    "failure_id": failure_id,
                    "failure_mode": failure_mode,
                    "anomaly": anom_desc,
                    "hazard": hz_desc,
                    "domain": entry_domain,
                    "function_profile": function_profile,
                    "skip": skip,
                    "events": []
                }

                if skip:
                    hazard_group["skip_reason"] = "S3标注为不涉及/无整车层面危害"
                    hazards.append(hazard_group)
                    continue

                # 应用案例库预填
                # 需要为每个可能的场景匹配案例
                # 这里先创建一个通用事件，让Agent完成场景选择
                # 或者我们可以从案例库中获取所有相关场景

                failure_profile = anom.get("failure_profile")
                if not isinstance(failure_profile, dict):
                    failure_profile = {}
                failure_profile = {
                    "canonical_mode": failure_profile.get("canonical_mode") or failure_mode,
                    "anomaly_class": failure_profile.get("anomaly_class")
                    or anom.get("anomaly_class")
                    or case_lib.canonical_anomaly_class(anom_desc, failure_mode),
                }

                hazard_profile = hz.get("hazard_profile")
                if not isinstance(hazard_profile, dict):
                    hazard_profile = {}
                if not hazard_profile:
                    # 只有输入已提供 canonical hazard 语义时才参与过滤；
                    # 不把项目/Domain Pack 的 hazard 文本强行猜成案例库类别。
                    for key in ("hazard_family", "vehicle_hazard_class"):
                        if hz.get(key):
                            hazard_profile[key] = hz[key]

                scenario_profile = hz.get("scenario_profile") or anom.get("scenario_profile") or entry.get("scenario_profile")
                if not isinstance(scenario_profile, dict):
                    scenario_text = hz.get("scenario") or anom.get("scenario") or entry.get("scenario")
                    scenario_profile = {"scenario_text": scenario_text} if scenario_text else None

                # 语义匹配入口不再接收 func_id；项目编号仅用于输出追溯。
                match = case_lib.match_cases(
                    domain_profile=entry_domain,
                    function_profile=function_profile,
                    failure_profile=failure_profile,
                    hazard_profile=hazard_profile or None,
                    scenario_profile=scenario_profile,
                )

                prefill_data = case_lib.get_prefill_data(match)

                # 记录匹配统计
                if match.match_type == 'exact':
                    exact_match_count += 1
                elif match.match_type == 'similar':
                    similar_match_count += 1
                elif match.match_type == 'template':
                    template_match_count += 1
                else:
                    no_match_count += 1

                # 根据匹配类型生成events
                if match.match_type == 'exact':
                    # 精确匹配：直接使用案例数据
                    event = {
                        "scenario": prefill_data.get("scenario"),
                        "description": "",
                        "severity": prefill_data.get("severity"),
                        "severity_reason": prefill_data.get("severity_reason"),
                        "exposure": prefill_data.get("exposure"),
                        "exposure_reason": prefill_data.get("exposure_reason"),
                        "controllability": prefill_data.get("controllability"),
                        "controllability_reason": prefill_data.get("controllability_reason"),
                        "safety_goal": prefill_data.get("safety_goal"),
                        "safe_state": prefill_data.get("safe_state"),
                        "ftti": prefill_data.get("ftti"),
                        "prefill_source": "exact",
                        "prefill_locked": True,
                        "prefill_confidence": prefill_data.get("prefill_confidence"),
                        "matched_by": prefill_data.get("matched_by", []),
                        "prefill_candidates": prefill_data.get("prefill_candidates", []),
                        "prefill_reason": prefill_data.get("prefill_reason", ""),
                    }
                    hazard_group["events"].append(event)

                elif match.match_type in ('similar', 'template'):
                    # 相似或模板匹配：提供参考值，Agent可编辑
                    event = {
                        "scenario": prefill_data.get("reference_scenario") or "",
                        "description": "",
                        "severity": prefill_data.get("severity"),
                        "severity_reason": prefill_data.get("severity_reason"),
                        "exposure": prefill_data.get("exposure"),
                        "exposure_reason": prefill_data.get("exposure_reason"),
                        "controllability": prefill_data.get("controllability"),
                        "controllability_reason": prefill_data.get("controllability_reason"),
                        "safety_goal": prefill_data.get("safety_goal_template") or prefill_data.get("safety_goal"),
                        "safe_state": prefill_data.get("safe_state_template") or prefill_data.get("safe_state"),
                        "ftti": prefill_data.get("ftti_template") or prefill_data.get("ftti"),
                        "prefill_source": match.match_type,
                        "prefill_locked": False,
                        "prefill_confidence": prefill_data.get("prefill_confidence"),
                        "matched_by": prefill_data.get("matched_by", []),
                        "prefill_candidates": prefill_data.get("prefill_candidates", []),
                        "prefill_reason": prefill_data.get("prefill_reason", match.reason),
                        "prefill_note": match.reason
                    }
                    hazard_group["events"].append(event)

                else:
                    # 无匹配：空骨架，Agent全新分析
                    event = {
                        "scenario": "",
                        "description": "",
                        "severity": None,
                        "severity_reason": "",
                        "exposure": None,
                        "exposure_reason": "",
                        "controllability": None,
                        "controllability_reason": "",
                        "safety_goal": "",
                        "safe_state": "",
                        "ftti": "",
                        "prefill_source": "none",
                        "prefill_locked": False,
                        "prefill_candidates": [],
                        "prefill_reason": prefill_data.get("prefill_reason", match.reason),
                    }
                    hazard_group["events"].append(event)

                hazards.append(hazard_group)

    print(f"\n[S4] 预填统计:")
    print(f"  精确匹配: {exact_match_count}")
    print(f"  相似匹配: {similar_match_count}")
    print(f"  模板匹配: {template_match_count}")
    print(f"  无匹配: {no_match_count}")

    # 4. 不在草稿阶段计算 ASIL 或分配正式 ID。
    # similar/template/none 都需要工程确认；即使 exact 命中，也仍需正式
    # traceability 与 hara validate 校验后才能成为 final。

    # 6. 统计
    total_hazards = len(hazards)
    skip_count = sum(1 for h in hazards if h.get("skip"))
    active_hazards = total_hazards - skip_count

    total_events = sum(len(h.get("events", [])) for h in hazards if not h.get("skip"))

    review_required = sum(
        1 for hazard in hazards
        if not hazard.get("skip")
        and any(not event.get("prefill_locked") for event in hazard.get("events", []))
    )
    exact_candidate_groups = sum(
        1 for hazard in hazards
        if not hazard.get("skip")
        and hazard.get("events")
        and all(event.get("prefill_locked") for event in hazard.get("events", []))
    )

    # 5. 输出受控草稿。validation.passed=false 是硬边界，S5/write 会拒绝。
    s4 = {
        "artifact_kind": "s4_hara_prefill_draft",
        "domain": domain,
        "hazards": hazards,
        "validation": {
            "passed": False,
            "status": "not_validated",
            "error_count": None,
            "warning_count": None,
            "next_action": "工程确认候选后执行 hara validate；正式链路优先使用 hara prepare",
        },
        "statistics": {
            "total_hazards": total_hazards,
            "skip_count": skip_count,
            "active_hazards": active_hazards,
            "total_events": total_events,
            "exact_candidate_groups": exact_candidate_groups,
            "review_required_groups": review_required,
        }
    }

    _save_json_atomic(s4, output_path)

    print(f"\n{'='*80}")
    print(f"[S4 prefill draft] 完成（尚未验证，不是正式 final）")
    print(f"{'='*80}")
    print(f"危害组: {total_hazards} (跳过 {skip_count}, 活跃 {active_hazards})")
    print(f"危害事件: {total_events}")
    print(f"exact 候选危害组: {exact_candidate_groups}")
    print(f"需工程确认危害组: {review_required}")
    print(f"\n草稿文件: {Path(output_path).resolve()}")
    print("下一步: 完成追溯/工程确认后执行 hara validate，禁止直接交给 S5/write。")

    return s4


def run(args):
    """CLI入口"""
    subcmd = getattr(args, "hara_subcommand", None)

    if subcmd == "finalize":
        try:
            finalize(args.s3_hazop, args.output or "s4_hara_prefill_draft.json")
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[S4 finalize] 操作失败：{error}")
            raise SystemExit(1) from error
    else:
        print("用法: run_hara.py hara finalize <s3_hazop.json> [-o s4_hara_prefill_draft.json]")
