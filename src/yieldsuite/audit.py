"""Run manifest: what went into a run, with which software and settings, and what
came out. Written next to the products as ``<field>_<year>_run.json``.

Every input and output file is listed with its size and SHA-256, so a product
can be traced to the exact bytes it was made from and checked later
(``verify_manifest``). Credentials never enter the manifest: settings are
copied from the config, which holds only the names of environment variables.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

SOFTWARE = "Beanstalk Yield Suite"


def sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def file_entry(path: str | Path, role: str) -> dict:
    p = Path(path)
    if p.is_dir():  # e.g. an ISOXML TASKDATA folder: hash every file
        files = sorted(q for q in p.rglob("*") if q.is_file())
        return {
            "role": role,
            "path": str(p),
            "kind": "directory",
            "files": [
                {"path": str(q.relative_to(p)), "bytes": q.stat().st_size, "sha256": sha256(q)}
                for q in files
            ],
        }
    if not p.exists():
        return {"role": role, "path": str(p), "missing": True}
    return {"role": role, "path": str(p), "bytes": p.stat().st_size, "sha256": sha256(p)}


def _git_commit() -> str | None:
    """Commit of the source tree when run from a checkout; None when installed."""
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(here), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        return out + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


def config_inputs(config) -> list[dict]:
    """Every file the config points at, with its role."""
    entries = []
    if config.source_path is not None:
        entries.append(file_entry(config.source_path, "config"))
    for log in config.harvest_logs:
        entries.append(file_entry(log.path, f"harvest_log ({log.machine_id})"))
    if config.scale_tickets is not None:
        entries.append(file_entry(config.scale_tickets, "scale_tickets"))
    if config.boundary is not None:
        entries.append(file_entry(config.boundary, "boundary"))
    for img in config.imagery:
        if img.path is not None:
            entries.append(file_entry(img.path, f"imagery ({img.kind} {img.date})"))
    if config.quality is not None:
        for src in config.quality.sources:
            entries.append(file_entry(src.path, "grain_quality"))
        if config.quality.lab_samples is not None:
            entries.append(file_entry(config.quality.lab_samples, "lab_samples"))
    return entries


def _settings(config) -> dict:
    """The parameters that shape the result (a readable subset of the config)."""
    return {
        "field_id": config.field_id,
        "year": config.year,
        "parcel_ids": list(config.parcel_ids),
        "crs": config.crs,
        "grid_resolution_m": config.grid_resolution_m,
        "grid_origin": list(config.grid_origin),
        "reference_moisture_pct": config.reference_moisture_pct,
        "edge_buffer_m": config.edge_buffer_m,
        "local_outlier_radius_m": config.local_outlier_radius_m,
        "product_class": config.product_class.value,
        "machines": {
            mid: {**asdict(m), "param_hash": m.param_hash()} for mid, m in config.machines.items()
        },
        "harvest_logs": [
            {
                "path": str(h.path),
                "machine_id": h.machine_id,
                "format": h.format,
                "columns": h.columns,
                "units": h.unit_overrides(),
                "where": h.where,
                "entry_level": h.entry_level,
                "vendor_reference_moisture_pct": h.vendor_reference_moisture_pct,
                "time_zone": h.time_zone,
            }
            for h in config.harvest_logs
        ],
    }


def write_run_manifest(config, summary, started: datetime, finished: datetime) -> Path:
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{config.field_id}_{config.year}_run.json"
    outputs = [file_entry(p, name) for name, p in (summary.outputs or {}).items()]
    manifest = {
        "run_id": str(uuid.uuid4()),
        "software": {
            "name": SOFTWARE,
            "package": "yieldsuite",
            "version": __version__,
            "git_commit": _git_commit(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": finished.isoformat(timespec="seconds"),
        "levels_requested": summary.level.value,
        "levels_completed": [lv.value for lv in summary.completed],
        "ok": summary.ok,
        "error": summary.error,
        "settings": _settings(config),
        "inputs": config_inputs(config),
        "outputs": outputs,
        "result": {
            "flagged_pct": summary.flagged_pct,
            "k_factors": summary.k_factors,
            "mass_conserved": summary.mass_ok,
            "notes": summary.notes,
        },
    }
    path.write_text(json.dumps(manifest, indent=2, default=str))
    return path


def verify_manifest(path: str | Path) -> list[str]:
    """Re-hash every listed file; return the differences (empty = unchanged)."""
    m = json.loads(Path(path).read_text())
    problems = []
    for e in m.get("inputs", []) + m.get("outputs", []):
        p = Path(e["path"])
        if e.get("missing"):
            continue
        if e.get("kind") == "directory":
            for f in e["files"]:
                q = p / f["path"]
                if not q.exists() or sha256(q) != f["sha256"]:
                    problems.append(f"{e['role']}: {q} changed or missing")
            continue
        if not p.exists():
            problems.append(f"{e['role']}: {p} is missing")
        elif sha256(p) != e["sha256"]:
            problems.append(f"{e['role']}: {p} has changed since the run")
    return problems


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
