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

from utils.domain_packs import canonical_domain_pack_code, load_domain_pack, resolve_domain_pack_source


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_runtime_sources(project_root: Path, *, exclude_installed_domain_packs: bool = False) -> str:
    """Hash installed code/reference files, pruning unused Pack data externally."""
    candidates: list[Path] = [project_root / "SKILL.md"]
    for relative_root in ("scripts", "references"):
        root = project_root / relative_root
        if not root.exists():
            continue
        for directory, child_dirs, filenames in os.walk(root):
            directory_path = Path(directory)
            child_dirs[:] = [
                name for name in child_dirs
                if name != "__pycache__" and not (
                    exclude_installed_domain_packs
                    and directory_path == project_root / "references"
                    and name == "domain_packs"
                )
            ]
            candidates.extend(
                directory_path / name for name in filenames
                if not name.endswith(".bak") and (directory_path / name).is_file()
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
        canonical_domain_pack_code(item["domain"].strip()) for item in data.get("related_items", [])
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

    # Code lives in the Skill, even when its PT data lives in a workspace.
    project_root = Path(__file__).resolve().parents[2]
    domain = _read_domain(Path(intermediate))
    canonical_domain = canonical_domain_pack_code(domain or "") if domain else None
    domain_assets = {}
    source = None
    if domain:
        try:
            source = resolve_domain_pack_source(domain)
        except ValueError:
            # Preserve the installed unknown-domain compatibility behavior.
            # DomainPackSourceError is deliberately not caught here.
            pass
        if source is not None:
            if source.source == "workspace":
                load_domain_pack(domain)
                from utils.domain_subfunction_source import load_domain_subfunction_authority
                load_domain_subfunction_authority(domain)
            for name, candidate in {
                "domain_pack": source.pack_path,
                "subfunction_authority": source.subfunction_path,
            }.items():
                if source.source == "workspace" or candidate.is_file():
                    domain_assets[name] = {
                        "path": str(candidate.resolve()),
                        "sha256": _sha256_file(candidate),
                    }
        # Keep non-PT scenario reporting and paths unchanged. PT has no
        # separate scenario file: its scenario catalog lives in the main Pack.
        if canonical_domain != "PT":
            candidate = project_root / "references" / "scenarios" / f"{canonical_domain}.json"
            if candidate.is_file():
                domain_assets["scenario_asset"] = {
                    "path": str(candidate.resolve()),
                    "sha256": _sha256_file(candidate),
                }

    external = source is not None and source.source == "workspace"
    runtime_hash = _hash_runtime_sources(project_root, exclude_installed_domain_packs=external)
    if external:
        # Include active external data in the runtime fingerprint without ever
        # opening the unused installed domain_packs subtree (even for hashing).
        digest = hashlib.sha256()
        digest.update(runtime_hash.encode("ascii"))
        for name, asset in sorted(domain_assets.items()):
            digest.update(f"\n{name}\0{asset['sha256']}".encode("utf-8"))
        runtime_hash = digest.hexdigest()

    manifest = {
        "schema_version": "1.0",
        "run_id": str(uuid.uuid4()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "domain": domain,
        "runtime_source_sha256": runtime_hash,
        "domain_asset_source": source.source if source is not None else None,
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
