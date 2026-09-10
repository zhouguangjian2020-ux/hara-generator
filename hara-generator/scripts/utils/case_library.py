"""Semantic HARA case-library matcher.

Runtime matching is intentionally project-independent. The v2 asset is matched
by canonical domain, function semantics, failure/anomaly semantics, hazard
semantics, and scenario semantics. Project identifiers such as func_id,
analysis_unit_id, and failure_id are not fields in the v2 runtime asset.

The runtime API exposes only semantic matching. Project-level IDs remain
outside this module's matching contract and are never accepted as match keys.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .domain_packs import DomainPackSourceError, is_external_domain_pack, load_domain_pack, resolve_domain_pack_source


_CANONICAL_DOMAIN_ALIASES = {
    "P": "PT",
    "POWERTRAIN": "PT",
    "PT": "PT",
    "A": "AD",
    "ADAS": "AD",
    "AD": "AD",
    "INFO": "ET",
    "INFOTAINMENT": "ET",
    "ET": "ET",
    "B": "BD",
    "BODY": "BD",
    "BD": "BD",
    "CB": "CB",
    "CHASSIS_BRAKE": "CB",
    "CS": "CS",
    "CHASSIS_STEERING": "CS",
}

_MODE_ALIASES = {
    "过大": "过多",
    "过小": "过少",
}


@dataclass
class CaseMatch:
    """A ranked semantic match result.

    ``case`` remains the top candidate for compatibility. New callers should
    use ``candidates`` and make the selected-case decision explicitly.
    """

    match_type: str  # 'exact', 'similar', 'template', 'none'
    case: Optional[Dict]
    confidence: float
    reason: str
    candidates: List[Dict] = field(default_factory=list)
    matched_by: List[str] = field(default_factory=list)
    score: float = 0.0


class CaseLibrary:
    """Load and rank semantic v2 case-library candidates."""

    def __init__(self, library_dir: Path | None = None, domain_pack_dir: Path | None = None):
        # PT 运行时只保留一套主资产：references/domain_packs/PT/PT.json。
        # library_dir 仅为其他域的兼容扩展点保留，不作为 PT 的回退来源。
        project_root = Path(__file__).resolve().parent.parent.parent / "references"
        if library_dir is None:
            library_dir = project_root / "case_library"
        self.library_dir = Path(library_dir)
        self._explicit_domain_pack_dir = domain_pack_dir is not None
        if domain_pack_dir is None:
            domain_pack_dir = project_root / "domain_packs"
        self.domain_pack_dir = Path(domain_pack_dir)
        self.cases_by_domain: dict[str, list[dict]] = {}
        self.loaded_domains: set[str] = set()
        self.loaded_files: dict[str, Path] = {}
        self._loaded_source_keys: dict[str, tuple[str, str, bool]] = {}

    # ------------------------------------------------------------------
    # Domain and asset loading
    # ------------------------------------------------------------------
    @staticmethod
    def canonical_domain(domain: Any) -> str:
        value = str(domain or "").strip().upper()
        if not value:
            return ""
        return _CANONICAL_DOMAIN_ALIASES.get(value, value)

    def _clear_domain_cache(self, canonical: str) -> None:
        self.loaded_domains.discard(canonical)
        self.cases_by_domain.pop(canonical, None)
        self.loaded_files.pop(canonical, None)
        self._loaded_source_keys.pop(canonical, None)

    def load_domain(self, domain: str) -> bool:
        """Load PT through the shared resolver; keep non-PT compatibility paths."""
        canonical = self.canonical_domain(domain)
        if not canonical:
            print("WARNING: Cannot load case library for empty domain")
            return False
        strict_external = is_external_domain_pack(canonical)
        try:
            if strict_external or not self._explicit_domain_pack_dir:
                source = resolve_domain_pack_source(canonical)
                pack_file = source.pack_path.resolve()
                if strict_external and self._explicit_domain_pack_dir:
                    if self.domain_pack_dir.resolve() not in (pack_file.parent, pack_file.parent.parent):
                        raise DomainPackSourceError("严格外部 PT 案例库不允许指定其他 Domain Pack 目录")
            else:
                # Explicit offline fixtures and non-PT retain their legacy layout.
                flat = self.domain_pack_dir / f"{canonical}.json"
                nested = self.domain_pack_dir / canonical / f"{canonical}.json"
                pack_file = (flat if flat.exists() else nested).resolve()
        except DomainPackSourceError:
            self._clear_domain_cache(canonical)
            raise

        source_key = (str(pack_file), str(self.library_dir.resolve()), strict_external)
        if canonical in self.loaded_domains and self._loaded_source_keys.get(canonical) == source_key:
            return True
        self._clear_domain_cache(canonical)
        case_file = pack_file
        try:
            cases = None
            if pack_file.exists():
                if strict_external or not self._explicit_domain_pack_dir:
                    pack = load_domain_pack(canonical)
                else:
                    pack = json.loads(pack_file.read_text(encoding="utf-8"))
                if isinstance(pack, dict) and "case_library_catalog" in pack:
                    catalog = pack.get("case_library_catalog")
                    if not isinstance(catalog, dict) or not isinstance(catalog.get("cases"), list):
                        raise ValueError(f"{canonical}.json case_library_catalog.cases 必须是数组")
                    cases = catalog["cases"]

            if cases is None:
                if strict_external:
                    raise DomainPackSourceError(f"外部 PT 缺少 case_library_catalog.cases，禁止回退: {pack_file}")
                case_file = (self.library_dir / f"{canonical}_cases_v2.json").resolve()
                if not case_file.exists():
                    print(f"WARNING: v2 case library not found for domain {domain} (canonical={canonical}): {case_file}")
                    return False
                cases = json.loads(case_file.read_text(encoding="utf-8"))

            if not isinstance(cases, list):
                raise ValueError(f"case library root must be a list for {canonical}")
            invalid = [i for i, case in enumerate(cases)
                       if not isinstance(case, dict) or case.get("schema_version") != "2.0"]
            if invalid:
                raise ValueError(f"v2 asset contains invalid records for {canonical}: {invalid[:5]}")
            wrong_domain = [i for i, case in enumerate(cases)
                            if self.canonical_domain(case.get("domain")) != canonical]
            if wrong_domain:
                raise ValueError(f"case library contains cross-domain records for {canonical}: {wrong_domain[:5]}")
        except DomainPackSourceError:
            raise
        except (OSError, ValueError) as error:
            if strict_external:
                raise DomainPackSourceError(f"严格外部 Domain Pack 案例库加载失败，不允许降级: {pack_file}: {error}") from error
            print(f"ERROR: Failed to load case library for {canonical}: {error}")
            return False

        self.cases_by_domain[canonical] = cases
        self.loaded_domains.add(canonical)
        self.loaded_files[canonical] = case_file
        self._loaded_source_keys[canonical] = source_key
        print(f"Loaded {len(cases)} v2 cases for domain {canonical} from {case_file.name}")
        return True

    def _cases(self, domain: Any) -> tuple[str, list[dict]]:
        canonical = self.canonical_domain(domain)
        # All matching/ID-resolution entrypoints must recheck the source, not
        # only explicit calls to load_domain(). Same-source reads remain cached.
        if not self.load_domain(canonical):
            return canonical, []
        return canonical, self.cases_by_domain.get(canonical, [])

    def resolve_case_ids(self, domain_profile: str | dict, case_ids: list[str]) -> list[dict]:
        """Resolve already-selected case-library provenance IDs.

        This is deliberately not a semantic matching API. ``case_id`` must never
        be used to infer a function for a new project. The method is only for a
        controlled downstream hand-off where an earlier stage has already
        selected cases from the same loaded runtime asset and persisted their
        stable asset IDs. Missing, duplicate, or cross-domain IDs fail closed.
        """
        domain = self._profile_domain(domain_profile)
        canonical, cases = self._cases(domain)
        if not isinstance(case_ids, list) or not case_ids:
            raise ValueError("source case_ids 必须是非空数组")
        normalized = [str(case_id or "").strip() for case_id in case_ids]
        if any(not case_id for case_id in normalized):
            raise ValueError("source case_ids 包含空值")
        if len(normalized) != len(set(normalized)):
            raise ValueError("source case_ids 包含重复值")
        index = {self._case_id(case): case for case in cases}
        missing = [case_id for case_id in normalized if case_id not in index]
        if missing:
            raise ValueError(f"source case_ids 无法在 {canonical} 运行时案例库解引用: {missing[:5]}")
        resolved = [index[case_id] for case_id in normalized]
        cross_domain = [
            self._case_id(case) for case in resolved
            if self.canonical_domain(case.get("domain")) != canonical
        ]
        if cross_domain:
            raise ValueError(f"source case_ids 指向其他域案例: {cross_domain[:5]}")
        return resolved

    def resolve_source_cases(
        self,
        *,
        domain_profile: str | dict,
        case_ids: list[str],
        function_profile: dict,
        failure_profile: dict,
        hazard_profile: dict | None = None,
    ) -> list[dict]:
        """Resolve an S3-controlled source-case hand-off for one hazard.

        The IDs only dereference cases already selected upstream. Semantic
        profiles are still checked so a stale/tampered S3 artifact cannot bind
        unrelated cases. A source case is lockable only when it is trusted, has
        a complete assessment, and contains its original event description.
        """
        resolved = self.resolve_case_ids(domain_profile, case_ids)
        function = self._normalize_function_profile(function_profile)
        failure = self._normalize_failure_profile(failure_profile)
        hazard = self._normalize_hazard_profile(hazard_profile)
        matched: list[dict] = []
        for case in resolved:
            case_function = self._normalize_function_profile(case.get("function_profile", {}))
            if case_function.get("canonical_function_family") != function.get("canonical_function_family"):
                continue
            case_failure = self._normalize_failure_profile(case.get("failure_profile", {}))
            if case_failure.get("canonical_mode") != failure.get("canonical_mode"):
                continue
            if case_failure.get("anomaly_class") != failure.get("anomaly_class"):
                continue
            if hazard:
                case_hazard = self._normalize_hazard_profile(case.get("hazard_profile", {}))
                if self._hazard_match(hazard, case_hazard) is not True:
                    continue
            if case.get("evidence_status") != "trusted_reference":
                raise ValueError(f"source case {self._case_id(case)} 不是 trusted_reference")
            if not self._assessment_complete_for_exact(case):
                raise ValueError(f"source case {self._case_id(case)} assessment 不完整，不能锁定")
            if not str(case.get("event_description") or "").strip():
                raise ValueError(
                    f"source case {self._case_id(case)} 缺少 event_description；"
                    "请从权威案例源重建 PT Domain Pack"
                )
            matched.append(case)
        if not matched:
            raise ValueError("source case_ids 与当前 S3 功能/失效/异常/危害语义不一致")
        return matched

    def project_case_candidate(self, case: dict) -> dict:
        """Return the public, project-ID-free candidate projection."""
        return self._candidate_projection(case)

    def project_source_case_event(self, case: dict) -> dict:
        """Project one trusted source case into a locked S4 event."""
        assessment = self._case_assessment(case)
        scenario_profile = case.get("scenario_profile") if isinstance(case.get("scenario_profile"), dict) else {}
        return {
            "source_case_id": self._case_id(case),
            "vehicle_safety_goal_id": case.get("vehicle_safety_goal_id"),
            "source_refs": deepcopy(case.get("source_refs") or []),
            "scenario_id": scenario_profile.get("semantic_fingerprint"),
            "scenario": self._case_scenario(case),
            "description": str(case.get("event_description") or "").strip(),
            "severity": assessment.get("severity"),
            "severity_reason": assessment.get("severity_reason"),
            "exposure": assessment.get("exposure"),
            "exposure_reason": assessment.get("exposure_reason"),
            "controllability": assessment.get("controllability"),
            "controllability_reason": assessment.get("controllability_reason"),
            "safety_goal": assessment.get("safety_goal"),
            "safe_state": assessment.get("safe_state"),
            "ftti": assessment.get("ftti"),
            "expected_asil": assessment.get("asil"),
            "sec_source": "case_library_exact",
            "note": "由 S3 source_case_ids 解引用同一运行时可信案例；禁止 Agent 改写。",
            "engineering_override": None,
        }

    # ------------------------------------------------------------------
    # New semantic API
    # ------------------------------------------------------------------
    def match_cases(
        self,
        domain_profile: str | dict,
        function_profile: dict,
        failure_profile: dict,
        hazard_profile: dict | None = None,
        scenario_profile: dict | None = None,
        limit: int = 50,
    ) -> CaseMatch:
        """Rank all v2 candidates without using project-level IDs.

        Exact is possible only when an anomaly and a scenario fingerprint (or
        an equivalent exact scenario text) are both available. When the
        scenario is missing, the strongest result is similar/template.
        """
        domain = self._profile_domain(domain_profile)
        _, cases = self._cases(domain)
        if not cases:
            return CaseMatch("none", None, 0.0, f"Domain {domain or '<empty>'} not available")

        function = self._normalize_function_profile(function_profile)
        failure = self._normalize_failure_profile(failure_profile)
        hazard = self._normalize_hazard_profile(hazard_profile)
        scenario = self._normalize_scenario_profile(scenario_profile)
        if not function.get("canonical_function_family"):
            return CaseMatch("none", None, 0.0, "Missing canonical function family")
        if not failure.get("canonical_mode"):
            return CaseMatch("none", None, 0.0, "Missing canonical failure mode")

        exact: list[tuple[float, dict, list[str]]] = []
        similar: list[tuple[float, dict, list[str]]] = []
        template: list[tuple[float, dict, list[str]]] = []

        for case in cases:
            evaluation = self._evaluate_case(function, failure, hazard, scenario, case)
            if evaluation is None:
                continue
            level, score, matched_by = evaluation
            if level == "exact":
                exact.append((score, case, matched_by))
            elif level == "similar":
                similar.append((score, case, matched_by))
            elif level == "template":
                template.append((score, case, matched_by))

        semantic_exact_conflict = False
        semantic_exact_incomplete = False
        if exact:
            # Semantic equality alone is not enough to lock when migrated
            # references disagree on S/E/C/ASIL or carry incomplete assessments.
            # In that case keep every exact-semantic candidate, but downgrade to
            # editable ``similar`` so an engineer chooses/resolves the conflict.
            exact.sort(key=lambda item: (-item[0], self._case_id(item[1])))
            semantic_exact_incomplete = any(
                not self._assessment_complete_for_exact(item[1]) for item in exact
            )
            signatures = {
                self._assessment_signature(item[1]) for item in exact
                if self._assessment_complete_for_exact(item[1])
            }
            semantic_exact_conflict = len(signatures) > 1

        selected = exact or similar or template
        if not selected:
            return CaseMatch(
                "none",
                None,
                0.0,
                "No semantic case matched: "
                f"domain={domain}, family={function.get('canonical_function_family')}, "
                f"mode={failure.get('canonical_mode')}",
            )

        selected.sort(key=lambda item: (-item[0], self._case_id(item[1])))
        if exact and not semantic_exact_conflict and not semantic_exact_incomplete:
            level = "exact"
        elif exact or similar:
            level = "similar"
        else:
            level = "template"
        candidates = [item[1] for item in selected[: max(1, limit)]]
        top_score, top_case, matched_by = selected[0]
        confidence = self._confidence(level, top_score)
        if level == "exact":
            reason = f"语义精确匹配且 assessment 一致：{', '.join(matched_by)}；候选 {len(selected)} 条"
        elif semantic_exact_conflict:
            reason = (
                f"语义键完全一致，但 {len(selected)} 条案例的 assessment 冲突；"
                "已降级为可编辑候选，禁止锁定第一条"
            )
        elif semantic_exact_incomplete:
            reason = (
                f"语义键完全一致，但候选 assessment 不完整；候选 {len(selected)} 条；"
                "已降级为可编辑候选"
            )
        elif level == "similar":
            reason = f"语义相似匹配：{', '.join(matched_by)}；场景不同或未提供；候选 {len(selected)} 条"
        else:
            reason = f"模板匹配：{', '.join(matched_by)}；异常或场景语义未完全一致；候选 {len(selected)} 条"
        return CaseMatch(
            level,
            top_case,
            confidence,
            reason,
            candidates=candidates,
            matched_by=matched_by,
            score=top_score,
        )

    def _evaluate_case(
        self,
        function: dict,
        failure: dict,
        hazard: dict,
        scenario: dict,
        case: dict,
    ) -> tuple[str, float, list[str]] | None:
        case_function = self._normalize_function_profile(case.get("function_profile", {}))
        if case_function.get("canonical_function_family") != function.get("canonical_function_family"):
            return None

        matched_by = ["domain", "canonical_function_family"]
        score = 0.50
        input_roles = set(function.get("semantic_roles", []))
        case_roles = set(case_function.get("semantic_roles", []))
        role_overlap = input_roles & case_roles
        if role_overlap:
            matched_by.append("semantic_roles")
            score += min(0.15, 0.03 * len(role_overlap))

        case_failure = self._normalize_failure_profile(case.get("failure_profile", {}))
        if case_failure.get("canonical_mode") != failure.get("canonical_mode"):
            return None
        matched_by.append("canonical_failure_mode")
        score += 0.20

        input_anomaly = failure.get("anomaly_class")
        case_anomaly = case_failure.get("anomaly_class")
        anomaly_exact = bool(input_anomaly and case_anomaly and input_anomaly == case_anomaly)
        if anomaly_exact:
            matched_by.append("anomaly_class")
            score += 0.20

        if hazard:
            case_hazard = self._normalize_hazard_profile(case.get("hazard_profile", {}))
            hazard_match = self._hazard_match(hazard, case_hazard)
            if hazard_match is False:
                return None
            if hazard_match:
                matched_by.append("hazard_profile")
                score += 0.10

        scenario_exact = self._scenario_exact(scenario, case.get("scenario_profile", {}))
        dimension_score = self._scenario_dimension_score(scenario, case.get("scenario_profile", {}))
        if scenario_exact:
            matched_by.append("scenario_semantic_fingerprint")
            score += 0.15

        if anomaly_exact and scenario_exact:
            return "exact", min(score, 1.0), matched_by
        if anomaly_exact:
            if dimension_score:
                score += min(0.10, 0.10 * dimension_score)
            return "similar", min(score, 0.95), matched_by
        # Template does not claim S/E/C or scenario equivalence.
        return "template", min(0.50 + score * 0.25, 0.70), matched_by[:3]

    @staticmethod
    def _confidence(level: str, score: float) -> float:
        if level == "exact":
            return 1.0
        if level == "similar":
            return round(max(0.6, min(0.95, score)), 3)
        if level == "template":
            return round(max(0.4, min(0.7, score)), 3)
        return 0.0

    # ------------------------------------------------------------------
    # Input-side semantic profile helpers
    # ------------------------------------------------------------------
    def build_function_profile(
        self,
        domain_profile: str | dict,
        *,
        function_name: str = "",
        semantic_texts: list[str] | None = None,
        analysis_unit_id: str = "",
    ) -> dict:
        """Build a cross-project function profile from text/Domain Pack semantics.

        ``analysis_unit_id`` is accepted only as a Domain Pack semantic hint;
        project ``func_id`` values are deliberately not accepted here.
        """
        domain = self._profile_domain(domain_profile)
        texts = [str(function_name or "").strip()]
        texts.extend(str(value).strip() for value in (semantic_texts or []) if str(value or "").strip())
        texts = list(dict.fromkeys(value for value in texts if value))
        profile: dict[str, Any] = {
            "canonical_function_family": "",
            "semantic_roles": [],
            "controlled_objects": [],
            "capability_tokens": texts,
        }

        try:
            from utils.domain_packs import load_domain_pack
            pack = load_domain_pack(domain)
        except (ImportError, ValueError):
            return profile

        units = pack.get("analysis_catalog", {}).get("analysis_units", [])
        if analysis_unit_id:
            unit = next(
                (
                    item for item in units
                    if isinstance(item, dict) and item.get("analysis_unit_id") == analysis_unit_id
                ),
                None,
            )
            if isinstance(unit, dict) and unit.get("canonical_function_family"):
                profile["canonical_function_family"] = unit["canonical_function_family"]
                profile["semantic_roles"] = sorted({
                    str(role).strip()
                    for role in unit.get("covered_semantic_roles", [])
                    if str(role).strip()
                })
                return profile

        scores: dict[str, int] = {}
        for matcher in pack.get("function_catalog", {}).get("function_matchers", []) or []:
            if not isinstance(matcher, dict):
                continue
            family = str(matcher.get("canonical_function_family") or "").strip()
            if not family:
                continue
            name_patterns = [
                value for value in matcher.get("name_patterns", [])
                if isinstance(value, str) and value.strip()
            ]
            keyword_patterns = [
                value for value in matcher.get("keywords", [])
                if isinstance(value, str) and value.strip()
            ]
            if any(pattern in text for pattern in name_patterns for text in texts):
                scores[family] = scores.get(family, 0) + 4
            elif any(pattern in text for pattern in keyword_patterns for text in texts):
                scores[family] = scores.get(family, 0) + 2

        if scores:
            highest = max(scores.values())
            winners = sorted(family for family, score in scores.items() if score == highest)
            if len(winners) == 1:
                profile["canonical_function_family"] = winners[0]

        role_map = pack.get("function_catalog", {}).get("semantic_roles", {})
        roles = []
        for role, patterns in (role_map or {}).items():
            if not isinstance(patterns, list):
                continue
            if any(
                isinstance(pattern, str) and pattern.strip() and pattern in text
                for pattern in patterns
                for text in texts
            ):
                roles.append(str(role))
        profile["semantic_roles"] = sorted(set(roles))
        return profile

    def canonical_anomaly_class(self, anomaly: str, mode: str) -> str:
        """Normalize S3 anomaly text into the v2 failure semantic class."""
        normalized_mode = _MODE_ALIASES.get(str(mode or "").strip(), str(mode or "").strip())
        return self._normalize_anomaly_text(str(anomaly or ""), normalized_mode)

    @staticmethod
    def _normalize_anomaly_text(anomaly: str, mode: str) -> str:
        value = anomaly.replace(" ", "")
        if ("切入" in value or "切换" in value) and ("档" in value or "挡" in value):
            if "反向" in value or "进入R" in value or "进入D" in value:
                return "gear_transition_wrong_state"
            return "unintended_gear_transition" if mode == "非预期" else "gear_transition_not_executed"
        if "挡位" in value or "档位" in value or "档" in value or "挡" in value:
            return "gear_state_display_failure" if "显示" in value else "gear_state_transition_failure"
        if "驱动扭矩" in value:
            if "反向" in value:
                return "drive_torque_reversed"
            if "过大" in value:
                return "drive_torque_excessive"
            if "过小" in value:
                return "drive_torque_insufficient"
            if "非预期" in value:
                return "drive_torque_unintended"
            return "required_drive_torque_not_provided"
        if "制动能量回收" in value:
            return "braking_regeneration_anomaly"
        if "滑行能量回收" in value:
            return "coasting_regeneration_anomaly"
        if "DCDC" in value:
            return "dcdc_unintended_short_circuit" if "短路" in value else "dcdc_output_regulation_anomaly"
        if "充电" in value:
            return "charging_control_anomaly"
        if "放电" in value:
            return "discharging_control_anomaly"
        if "互锁" in value:
            return "hv_interlock_detection_anomaly"
        if "绝缘" in value:
            return "hv_insulation_detection_anomaly"
        if "碰撞" in value:
            return "collision_hv_shutdown_anomaly"
        if "上电" in value:
            return "hv_power_on_anomaly"
        if "下电" in value:
            return "hv_power_off_anomaly"
        if "加热" in value:
            return "battery_heating_anomaly"
        if "冷却" in value:
            return "thermal_cooling_anomaly"
        return "legacy_anomaly_" + re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")

    # ------------------------------------------------------------------
    # Prefill projection
    # ------------------------------------------------------------------
    def get_prefill_data(self, match: CaseMatch) -> Dict:
        """Project a semantic match into the current prefill shape.

        The projection keeps the first candidate for old callers but exposes
        the complete candidate list to new callers through ``candidates`` and
        ``prefill_candidates``. Similar/template remain editable.
        """
        if match.match_type == "none" or not match.case:
            return {
                "prefill_source": "none",
                "editable": True,
                "prefill_candidates": [],
                "prefill_reason": match.reason,
            }

        case = match.case
        prefill = {
            "prefill_source": match.match_type,
            "prefill_confidence": match.confidence,
            "prefill_reason": match.reason,
            "matched_by": list(match.matched_by),
            "editable": match.match_type != "exact",
            "prefill_candidates": [self._candidate_projection(candidate) for candidate in match.candidates],
        }
        assessment = self._case_assessment(case)
        scenario = self._case_scenario(case)
        hazard = case.get("hazard_profile", {}) if isinstance(case.get("hazard_profile"), dict) else {}
        failure = case.get("failure_profile", {}) if isinstance(case.get("failure_profile"), dict) else {}

        if match.match_type == "exact":
            prefill.update({
                "scenario": scenario,
                "description": case.get("event_description"),
                "vehicle_safety_goal_id": case.get("vehicle_safety_goal_id"),
                "vehicle_hazard": hazard.get("vehicle_hazard_class"),
                "severity": assessment.get("severity"),
                "severity_reason": assessment.get("severity_reason"),
                "exposure": assessment.get("exposure"),
                "exposure_reason": assessment.get("exposure_reason"),
                "controllability": assessment.get("controllability"),
                "controllability_reason": assessment.get("controllability_reason"),
                "asil": assessment.get("asil"),
                "safety_goal": assessment.get("safety_goal"),
                "safe_state": assessment.get("safe_state"),
                "ftti": assessment.get("ftti"),
            })
        elif match.match_type == "similar":
            prefill.update({
                "reference_scenario": scenario,
                "vehicle_safety_goal_id": case.get("vehicle_safety_goal_id"),
                "vehicle_hazard": hazard.get("vehicle_hazard_class"),
                "severity": assessment.get("severity"),
                "severity_reason": assessment.get("severity_reason"),
                "exposure": assessment.get("exposure"),
                "exposure_reason": assessment.get("exposure_reason"),
                "controllability": assessment.get("controllability"),
                "controllability_reason": assessment.get("controllability_reason"),
                "asil": assessment.get("asil"),
                "safety_goal": assessment.get("safety_goal"),
                "safe_state": assessment.get("safe_state"),
                "ftti": assessment.get("ftti"),
            })
        else:
            prefill.update({
                "reference_anomaly": failure.get("anomaly_class"),
                "reference_scenario": scenario,
                "safety_goal_template": assessment.get("safety_goal"),
                "safe_state_template": assessment.get("safe_state"),
                "ftti_template": assessment.get("ftti"),
            })
        return prefill

    def _candidate_projection(self, case: dict) -> dict:
        assessment = self._case_assessment(case)
        hazard = case.get("hazard_profile", {}) if isinstance(case.get("hazard_profile"), dict) else {}
        function = case.get("function_profile", {}) if isinstance(case.get("function_profile"), dict) else {}
        failure = case.get("failure_profile", {}) if isinstance(case.get("failure_profile"), dict) else {}
        return {
            "case_id": self._case_id(case),
            "vehicle_safety_goal_id": case.get("vehicle_safety_goal_id"),
            "canonical_function_family": function.get("canonical_function_family"),
            "canonical_mode": failure.get("canonical_mode"),
            "anomaly_class": failure.get("anomaly_class"),
            "hazard_family": hazard.get("hazard_family"),
            "vehicle_hazard_class": hazard.get("vehicle_hazard_class"),
            "scenario": self._case_scenario(case),
            "scenario_profile": case.get("scenario_profile"),
            "assessment": assessment,
        }

    @staticmethod
    def _case_id(case: dict) -> str:
        return str(case.get("case_id") or "")

    @staticmethod
    def _case_scenario(case: dict) -> str:
        profile = case.get("scenario_profile")
        if isinstance(profile, dict):
            return str(profile.get("scenario_text") or "").strip()
        return str(case.get("scenario") or "").strip()

    @classmethod
    def _assessment_signature(cls, case: dict) -> tuple:
        assessment = cls._case_assessment(case)
        fields = (
            "severity", "severity_reason", "exposure", "exposure_reason",
            "controllability", "controllability_reason", "asil",
            "safety_goal", "safe_state", "ftti",
        )
        return tuple(assessment.get(field) for field in fields)

    @classmethod
    def _assessment_complete_for_exact(cls, case: dict) -> bool:
        assessment = cls._case_assessment(case)
        severity = assessment.get("severity")
        if not isinstance(severity, int) or isinstance(severity, bool) or severity not in range(4):
            return False
        if not str(assessment.get("severity_reason") or "").strip():
            return False
        if severity == 0:
            return all(
                assessment.get(field) in (None, "")
                for field in (
                    "exposure", "exposure_reason", "controllability",
                    "controllability_reason", "asil", "safety_goal",
                )
            )
        for field, allowed in (("exposure", range(1, 5)), ("controllability", range(0, 4))):
            value = assessment.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value not in allowed:
                return False
        return all(
            str(assessment.get(field) or "").strip()
            for field in ("exposure_reason", "controllability_reason", "asil")
        )

    @staticmethod
    def _case_assessment(case: dict) -> dict:
        assessment = case.get("assessment")
        if isinstance(assessment, dict):
            return assessment
        # Read-only compatibility projection for legacy assets.
        return {
            "severity": case.get("severity"),
            "severity_reason": case.get("severity_reason"),
            "exposure": case.get("exposure"),
            "exposure_reason": case.get("exposure_reason"),
            "controllability": case.get("controllability"),
            "controllability_reason": case.get("controllability_reason"),
            "asil": case.get("asil"),
            "safety_goal": case.get("safety_goal"),
            "safe_state": case.get("safe_state"),
            "ftti": case.get("ftti"),
        }

    # ------------------------------------------------------------------
    # Profile helpers
    # ------------------------------------------------------------------
    def _profile_domain(self, profile: str | dict) -> str:
        if isinstance(profile, dict):
            return self.canonical_domain(profile.get("canonical_domain") or profile.get("domain"))
        return self.canonical_domain(profile)

    @staticmethod
    def _normalize_function_profile(profile: dict | None) -> dict:
        profile = profile if isinstance(profile, dict) else {}
        return {
            "canonical_function_family": str(profile.get("canonical_function_family") or "").strip(),
            "semantic_roles": sorted(set(str(x).strip() for x in profile.get("semantic_roles", []) if str(x).strip())),
            "controlled_objects": sorted(set(str(x).strip() for x in profile.get("controlled_objects", []) if str(x).strip())),
            "capability_tokens": sorted(set(str(x).strip() for x in profile.get("capability_tokens", []) if str(x).strip())),
        }

    @staticmethod
    def _normalize_failure_profile(profile: dict | None) -> dict:
        profile = profile if isinstance(profile, dict) else {}
        mode = str(profile.get("canonical_mode") or profile.get("failure_mode") or "").strip()
        return {
            "canonical_mode": _MODE_ALIASES.get(mode, mode),
            "anomaly_class": str(profile.get("anomaly_class") or "").strip(),
        }

    @staticmethod
    def _normalize_hazard_profile(profile: dict | None) -> dict:
        profile = profile if isinstance(profile, dict) else {}
        return {
            "hazard_family": str(profile.get("hazard_family") or "").strip(),
            "vehicle_hazard_class": str(profile.get("vehicle_hazard_class") or "").strip(),
        }

    @staticmethod
    def _normalize_scenario_profile(profile: dict | None) -> dict:
        if not isinstance(profile, dict):
            return {}
        return {
            "semantic_fingerprint": str(profile.get("semantic_fingerprint") or "").strip(),
            "scenario_text": str(profile.get("scenario_text") or profile.get("scenario") or "").strip(),
            "dimensions": profile.get("dimensions") if isinstance(profile.get("dimensions"), dict) else {},
        }

    @staticmethod
    def _hazard_match(left: dict, right: dict) -> bool | None:
        supplied = {
            key: left.get(key)
            for key in ("hazard_family", "vehicle_hazard_class")
            if left.get(key) not in (None, "")
        }
        if not supplied:
            return None
        # Hazard fields are typed semantic dimensions, not a bag of aliases.
        # When both are supplied, both must agree.
        return all(right.get(key) == value for key, value in supplied.items())

    def _scenario_exact(self, input_profile: dict, case_profile: dict) -> bool:
        if not input_profile:
            return False
        case_profile = self._normalize_scenario_profile(case_profile)
        input_fingerprint = input_profile.get("semantic_fingerprint")
        if input_fingerprint and case_profile.get("semantic_fingerprint"):
            return input_fingerprint == case_profile["semantic_fingerprint"]
        input_text = input_profile.get("scenario_text")
        case_text = case_profile.get("scenario_text")
        return bool(input_text and case_text and self._normalize_text(input_text) == self._normalize_text(case_text))

    def _scenario_dimension_score(self, input_profile: dict, case_profile: dict) -> float:
        if not input_profile:
            return 0.0
        left = input_profile.get("dimensions", {}) or {}
        right = self._normalize_scenario_profile(case_profile).get("dimensions", {}) or {}
        comparable = [
            key for key, value in left.items()
            if value not in (None, "", "unknown", "unspecified")
            and right.get(key) not in (None, "", "unknown", "unspecified")
        ]
        if not comparable:
            return 0.0
        return sum(1 for key in comparable if left.get(key) == right.get(key)) / len(comparable)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"[\s，。；、：:（）()\[\]【】,;]+", "", str(value or "")).lower()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    def get_statistics(self, domain: str) -> Dict:
        canonical, cases = self._cases(domain)
        if not cases:
            return {}
        functions = {
            (case.get("function_profile") or {}).get("canonical_function_family")
            for case in cases
            if isinstance(case.get("function_profile"), dict)
        }
        modes = {
            (case.get("failure_profile") or {}).get("canonical_mode")
            for case in cases
            if isinstance(case.get("failure_profile"), dict)
        }
        scenarios = {
            (case.get("scenario_profile") or {}).get("semantic_fingerprint")
            for case in cases
            if isinstance(case.get("scenario_profile"), dict)
        }
        asil_dist: dict[str, int] = {}
        for case in cases:
            assessment = self._case_assessment(case)
            asil = assessment.get("asil") or "NULL"
            asil_dist[asil] = asil_dist.get(asil, 0) + 1
        return {
            "domain": canonical,
            "asset": self.loaded_files.get(canonical).name if canonical in self.loaded_files else None,
            "total_cases": len(cases),
            "functions": len(functions),
            "failure_modes": len(modes),
            "scenarios": len(scenarios),
            "asil_distribution": asil_dist,
        }


_case_library: CaseLibrary | None = None


def get_case_library() -> CaseLibrary:
    """Return the process-global case-library instance."""
    global _case_library
    if _case_library is None:
        _case_library = CaseLibrary()
    return _case_library
