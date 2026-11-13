"""Choose the grid cell size at which the cell yield is repeatable.

A cell value is only worth showing if harvesting the same cell twice would give
about the same number. That can be measured from one harvest: split the passes
into two interleaved halves (every other pass), compute the yield of each cell
from each half separately, and compare the two across cells.

    r           correlation of the two half-cell yields across cells
    reliability 2 r / (1 + r)   (Spearman-Brown: reliability of the whole cell)
    se          sd(half A - half B) / 2   [t/ha], standard error of the cell yield

Small cells are noisy (grain mixes inside the combine, the header is several
metres wide); large cells are repeatable but hide detail. ``choose`` takes the
finest cell size whose reliability reaches the target, and the most reliable
one when none does. Cell sizes narrower than the pass spacing cannot be
assessed, because few cells hold two passes; they are never chosen.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CANDIDATES_M = (5.0, 10.0, 15.0, 20.0, 30.0, 40.0)


@dataclass(frozen=True)
class ResolutionSettings:
    candidates_m: tuple[float, ...] = CANDIDATES_M
    min_reliability: float = 0.8
    min_cells: int = 30  # cells with both halves needed for an estimate
    min_assessed_share: float = 0.5  # of logged cells; below this the size is not assessable
    min_half_share: float = 0.1  # each half must cover this share of the cell


def _half_of_pass(pass_ids) -> np.ndarray:
    """0/1 per record: every other pass, in pass order."""
    _, rank = np.unique(np.asarray(pass_ids), return_inverse=True)
    return (rank % 2).astype(int)


def assess(x, y, mass_kg, area_m2, pass_ids, resolution: float, origin=(0.0, 0.0),
           settings: ResolutionSettings | None = None) -> dict:
    """Split-half repeatability of cell yield at one cell size (clean records only)."""
    s = settings or ResolutionSettings()
    x, y = np.asarray(x, float), np.asarray(y, float)
    m, a = np.asarray(mass_kg, float), np.asarray(area_m2, float)
    half = _half_of_pass(pass_ids)
    col = np.floor((x - origin[0]) / resolution).astype(np.int64)
    row = np.floor((y - origin[1]) / resolution).astype(np.int64)
    _, cell = np.unique(np.c_[row, col], axis=0, return_inverse=True)
    cell = np.ravel(cell)
    n = int(cell.max()) + 1 if len(cell) else 0
    out = {"resolution_m": float(resolution), "cells": n, "assessed_cells": 0,
           "assessed_share": 0.0, "r": None, "reliability": None, "se_t_ha": None,
           "sd_t_ha": None, "assessable": False}
    if n == 0:
        return out
    ma = [np.bincount(cell[half == h], m[half == h], n) for h in (0, 1)]
    aa = [np.bincount(cell[half == h], a[half == h], n) for h in (0, 1)]
    tot = aa[0] + aa[1]
    lim = s.min_half_share * np.maximum(tot, 1e-9)
    both = (aa[0] > lim) & (aa[1] > lim)
    out["assessed_cells"] = int(both.sum())
    out["assessed_share"] = float(both.sum() / max(1, (tot > 0).sum()))
    ok = both.sum() >= s.min_cells and out["assessed_share"] >= s.min_assessed_share
    if both.sum() < 3:
        return out
    ya = 10.0 * ma[0][both] / aa[0][both]
    yb = 10.0 * ma[1][both] / aa[1][both]
    yc = 10.0 * (ma[0][both] + ma[1][both]) / tot[both]
    if ya.std() == 0 or yb.std() == 0:
        return out
    r = float(np.corrcoef(ya, yb)[0, 1])
    out.update(
        r=r,
        reliability=float(2 * r / (1 + r)) if r > -1 else None,
        se_t_ha=float(np.std(ya - yb) / 2.0),
        sd_t_ha=float(np.std(yc)),
        assessable=bool(ok),
    )
    return out


def choose(table: list[dict], settings: ResolutionSettings | None = None) -> dict:
    """Pick a cell size from ``assess`` rows. Returns {resolution_m, reason, met_target}."""
    s = settings or ResolutionSettings()
    rows = sorted((t for t in table if t["assessable"] and t["reliability"] is not None),
                  key=lambda t: t["resolution_m"])
    if not rows:
        return {"resolution_m": None, "met_target": False,
                "reason": "no cell size could be assessed (too few cells with two passes)"}
    good = [t for t in rows if t["reliability"] >= s.min_reliability]
    if good:
        t = good[0]
        return {"resolution_m": t["resolution_m"], "met_target": True,
                "reason": f"finest cell size with reliability >= {s.min_reliability:g} "
                          f"({t['reliability']:.2f}, +-{t['se_t_ha']:.2f} t/ha per cell)"}
    t = max(rows, key=lambda t: t["reliability"])
    return {"resolution_m": t["resolution_m"], "met_target": False,
            "reason": f"no cell size reaches reliability {s.min_reliability:g}; "
                      f"the most repeatable is used ({t['reliability']:.2f}, "
                      f"+-{t['se_t_ha']:.2f} t/ha per cell)"}


def assess_records(rec, origin=(0.0, 0.0), settings: ResolutionSettings | None = None) -> dict:
    """Assess every candidate cell size from an L3 record table and choose one."""
    s = settings or ResolutionSettings()
    if "hx" in rec.columns:
        from ..l5.records import RecordView

        view = RecordView.from_records(rec)
        cx, cy = np.asarray(view.cx), np.asarray(view.cy)
    else:  # records read back from an L3 file: polygon centres
        c = rec.geometry.centroid
        cx, cy = c.x.to_numpy(), c.y.to_numpy()
    use = (rec["flags"].to_numpy() == 0) & (rec["effective_area"].to_numpy(float) > 0)
    args = (cx[use], cy[use],
            rec["mass_kg_reconciled"].to_numpy(float)[use],
            rec["effective_area"].to_numpy(float)[use], rec["pass_key"].to_numpy()[use])
    table = [assess(*args, resolution=g, origin=origin, settings=s) for g in s.candidates_m]
    return {"table": table, "choice": choose(table, s), "target": s.min_reliability}
