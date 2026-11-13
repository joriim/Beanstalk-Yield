"""Runs the chain for one field-year.

Stages are wired one by one as they are implemented; unimplemented stages raise
``NotImplementedError`` so a run fails loudly instead of producing a half map.
In the server deployment each stage becomes a Dagster asset.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import stages
from .config import FieldYearConfig
from .levels import Level, ProductClass


@dataclass
class Stage:
    target: Level
    description: str
    run: Callable[[FieldYearConfig, dict], None] | None = None

    @property
    def implemented(self) -> bool:
        return self.run is not None


@dataclass
class RunSummary:
    field_id: str
    year: int
    level: Level
    product_class: ProductClass
    completed: list[Level] = field(default_factory=list)
    flagged_pct: float | None = None
    k_factors: dict[str, float] = field(default_factory=dict)
    mass_ok: bool | None = None
    viewer_url: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    certainty: dict | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


STAGES: list[Stage] = [
    Stage(Level.L1A, "read harvest logs (CSV; ISOXML not yet)", stages.stage_ingest),
    Stage(Level.L1B, "mass flow (vendor exports already at L1B)", stages.stage_mass_flow),
    Stage(
        Level.L2,
        "flow delay, antenna offset, moisture standardisation, effective area, yield",
        stages.stage_georeference,
    ),
    Stage(Level.L3, "quality flags, load reconciliation per machine", stages.stage_clean),
    Stage(Level.L4, "mass apportioning to the fixed grid, quality bands", stages.stage_grid),
    Stage(Level.L5, "Sentinel-2 fusion via openEO: image model, gap fill", stages.stage_fusion),
]


def plan(config: FieldYearConfig, to_level: Level, from_level: Level = Level.L1A) -> list[Stage]:
    if config.product_class is ProductClass.RELATIVE:
        # Imagery only: there are no records, only the L5-style image products.
        return [s for s in STAGES if s.target is Level.L5]
    return [s for s in STAGES if from_level <= s.target <= to_level]


def run(config: FieldYearConfig, to_level: Level, from_level: Level = Level.L1A) -> RunSummary:
    """Run the chain; the run manifest (inputs, settings, outputs with SHA-256)
    is written next to the products, for failed runs too."""
    from .audit import now_utc

    started = now_utc()
    summary = _run(config, to_level, from_level)
    if summary.completed:
        # the yield at the cell size where it is repeatable
        try:
            from . import present

            ps = present.run(config)
            if ps:
                summary.outputs["reliable"] = str(Path(config.output_dir) / ps["file"])
                rel = "n/a" if ps["reliability"] is None else f"{ps['reliability']:.2f}"
                summary.notes.append(
                    f"shown at {ps['resolution_m']:g} m cells (repeatability {rel}"
                    + ("" if ps["met_target"]
                       else f", below the target {ps['target_reliability']:g}")
                    + ")")
        except Exception as exc:  # noqa: BLE001 - never hide the products behind this
            summary.notes.append(f"FAILED cell size choice: {type(exc).__name__}: {exc}")
        # what is known, estimated, assumed and missing, and what to ask for
        try:
            from . import certainty

            res = certainty.assess(config)
            path = Path(config.output_dir) / f"{config.field_id}_{config.year}_certainty.json"
            path.write_text(json.dumps(res, indent=2, ensure_ascii=False))
            summary.outputs["certainty"] = str(path)
            summary.certainty = res
        except Exception as exc:  # noqa: BLE001 - never hide the products behind this
            summary.notes.append(f"FAILED certainty summary: {type(exc).__name__}: {exc}")
    if summary.completed or summary.error:
        from .audit import write_run_manifest

        try:
            summary.outputs["run_manifest"] = str(
                write_run_manifest(config, summary, started, now_utc())
            )
        except OSError as exc:  # pragma: no cover - read-only output folder
            summary.notes.append(f"run manifest not written: {exc}")
    return summary


def _run(config: FieldYearConfig, to_level: Level, from_level: Level) -> RunSummary:
    summary = RunSummary(config.field_id, config.year, to_level, config.product_class)
    state: dict = {}
    for stage in plan(config, to_level, from_level):
        if not stage.implemented:
            summary.error = f"{stage.target.value} not implemented yet: {stage.description}"
            return summary
        try:
            stage.run(config, state)
        except Exception as exc:  # noqa: BLE001 - reported to the user and to Slack
            summary.error = f"{stage.target.value} failed: {exc}"
            summary.outputs = state.get("outputs", {})
            return summary
        summary.completed.append(stage.target)
    summary.flagged_pct = state.get("flagged_pct")
    summary.k_factors = state.get("k_factors", {})
    summary.mass_ok = state.get("mass_ok")
    summary.outputs = state.get("outputs", {})
    summary.notes = list(state.get("notes", []))
    return summary


def estimate_delays(config: FieldYearConfig) -> dict:
    """Run ingest and L2 only and report, per machine, the flow delay the data
    suggest next to the profile value. Nothing is written."""
    state: dict = {}
    stages.stage_ingest(config, state)
    stages.stage_mass_flow(config, state)
    stages.stage_georeference(config, state)
    out = {}
    for mid, d in state["qa"]["delays"].items():
        prof = config.machines.get(mid)
        est = d.get("estimate", {})
        curve = d.get("estimate_curve", {})
        out[mid] = {
            "profile_tau_d_s": prof.tau_d_s if prof else None,
            "estimated_tau_d_s": est.get("tau_d_s"),
            "identifiable": est.get("identifiable", False),
            "reason": est.get(
                "reason", "vendor already applied the delay" if d.get("vendor_applied") else ""
            ),
            "opposite_pass_pairs": est.get("n_pairs", 0),
            "contrast": est.get("contrast"),
            "used_tau_d_s": d["tau_d_s"],
            "used_source": d.get("source", "vendor"),
            "curve": {str(k): v for k, v in curve.items()},
        }
    return out


def estimate_offsets(config: FieldYearConfig) -> dict:
    """Run ingest and L2 only and report, per machine, the lateral antenna offset
    the pass geometry suggests (see ``l2.offset``). Nothing is written."""
    from .l2.offset import estimate_lateral_offset

    state: dict = {}
    stages.stage_ingest(config, state)
    stages.stage_mass_flow(config, state)
    stages.stage_georeference(config, state)
    rec = state["records"]
    out = {}
    for mid, df in rec.groupby("machine_id", sort=False):
        harvesting = df["swath_used"].to_numpy() > 0
        pid = np.where(harvesting, df["pass_id"].to_numpy(), -1)
        est = estimate_lateral_offset(
            df["hx"], df["hy"], df["heading"], pid, df["swath_used"].where(harvesting)
        )
        prof = config.machines.get(mid)
        fwd, right = prof.antenna_offset_m if prof else (0.0, 0.0)
        d = est.as_dict()
        d["profile_antenna_offset_m"] = [fwd, right]
        if est.identifiable:
            lo, hi = est.ci95_m
            d["suggested_right_m"] = round(right - est.lateral_error_m, 2)
            d["significant"] = bool(abs(est.lateral_error_m) >= 0.1 and (lo > 0 or hi < 0))
        out[str(mid)] = d
    return out
