"""Runs the chain for one field-year.

Stages are wired one by one as they are implemented; unimplemented stages raise
``NotImplementedError`` so a run fails loudly instead of producing a half map.
In the server deployment each stage becomes a Dagster asset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

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
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _stage_l5(config: FieldYearConfig, state: dict) -> None:
    """Sentinel-2 fusion on the L4 grid held in ``state`` (see ``yieldsuite fuse``)."""
    if "l4" not in state:
        raise RuntimeError(
            "L5 needs an L4 yield grid; imagery-only relative maps are not built yet"
        )
    from .l5.run import run_sentinel2_fusion

    result = run_sentinel2_fusion(config, state["l4"], state["grid"])
    state["l5"] = result


STAGES: list[Stage] = [
    Stage(Level.L1A, "decode logs to SI units, one clock"),
    Stage(Level.L1B, "tare, flow calibration curve, moisture standardisation"),
    Stage(Level.L2, "flow delay, antenna offset, record polygons, effective area, yield"),
    Stage(Level.L3, "load reconciliation per machine, quality flags"),
    Stage(Level.L4, "mass apportioning to the fixed grid, quality bands"),
    Stage(Level.L5, "Sentinel-2 fusion via openEO: image model, gap fill", _stage_l5),
]


def plan(config: FieldYearConfig, to_level: Level, from_level: Level = Level.L1A) -> list[Stage]:
    if config.product_class is ProductClass.RELATIVE:
        # Imagery only: there are no records, only the L5-style image products.
        return [s for s in STAGES if s.target is Level.L5]
    return [s for s in STAGES if from_level <= s.target <= to_level]


def run(config: FieldYearConfig, to_level: Level, from_level: Level = Level.L1A) -> RunSummary:
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
            return summary
        summary.completed.append(stage.target)
    summary.flagged_pct = state.get("flagged_pct")
    summary.k_factors = state.get("k_factors", {})
    summary.mass_ok = state.get("mass_ok")
    return summary
