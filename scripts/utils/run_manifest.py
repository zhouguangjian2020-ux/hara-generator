# scripts/utils/run_manifest.py
"""Immutable provenance manifest for formal HARA Excel outputs."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_runtime_sources(project_root: Path) -> str:
    """Hash executable instructions and reference assets used by the Skill."""
    candidates: list[Path] = [project_root / "SKILL.md"]
    for relative_root in ("scripts", "references"):
        root = project_root / relative_root
        if not root.exists():
            continue
        candidates.extend(
            path for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".bak")
        )
    digest = hashlib.sha256()
    for path in sorted(set(candidates), key=lambda item: item.as_posix()):
        relative = path.relative_to(project_root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_domain(intermediate_path: Path) -> str | None:
    try:
        data = json.loads(intermediate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    domains = {
        item.get("domain") for item in data.get("related_items", [])
        if isinstance(item, dict) and isinstance(item.get("domain"), str) and item["domain"].strip()
    }
    return next(iter(domains)) if len(domains) == 1 else None


def _atomic_write_json(data: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, output)


def write_run_manifest(
    output_excel: str,
    *,
    intermediate: str,
    s1: str,
    s2: str,
    s3: str | None = None,
    s4: str | None = None,
    s5: str | None = None,
    template: str | None = None,
) -> Path:
    """Write ``<result.xlsx>.run_manifest.json`` with chain and asset hashes."""
    output_path = Path(output_excel).resolve()
    artifact_paths: Mapping[str, str | None] = {
        "intermediate": intermediate,
        "s1_decisions": s1,
        "s2_decisions": s2,
        "s3_hazop": s3,
        "s4_hara_final": s4,
        "s5_safety_goals": s5,
        "template": template,
        "result_excel": str(output_path),
    }
    artifacts = {}
    for name, raw_path in artifact_paths.items():
        if not raw_path:
            continue
        path = Path(raw_path).resolve()
        artifacts[name] = {
            "path": str(path),
            "sha256": _sha256_file(path),
        }

    project_root = Path(__file__).resolve().parents[2]
    domain = _read_domain(Path(intermediate))
    domain_assets = {}
    if domain:
        for name, candidate in {
            "domain_pack": project_root / "references" / "domain_packs" / f"{domain}.json",
            "scenario_asset": project_root / "references" / "scenarios" / f"{domain}.json",
        }.items():
            if candidate.exists():
                domain_assets[name] = {
                    "path": str(candidate.resolve()),
                    "sha256": _sha256_file(candidate),
                }

    manifest = {
        "schema_version": "1.0",
        "run_id": str(uuid.uuid4()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "domain": domain,
        "runtime_source_sha256": _hash_runtime_sources(project_root),
        "domain_assets": domain_assets,
        "artifacts": artifacts,
        "chain": {
            "s3_supplied": bool(s3),
            "s4_supplied": bool(s4),
            "s5_supplied": bool(s5),
            "statement": "所有 artifact 哈希来自同一次 write 调用；正式 S4 的额外阶段合同由 hara validate 执行。",
        },
    }
    manifest_path = Path(f"{output_path}.run_manifest.json")
    _atomic_write_json(manifest, manifest_path)
    return manifest_path
