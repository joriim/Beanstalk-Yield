"""Yield shown at the cell size where it is repeatable.

The chain works on a fine grid (``grid.resolution_m``); single cells of it are
often mostly noise. This step measures, from the harvest records, at which cell
size the cell yield repeats (``l4/resolution.py``), aggregates the L4 and L5
maps to that size and writes

- ``L4_<field>_<year>_reliable.tif``: ``yield`` (measured, coverage-weighted),
  ``coverage``, ``yield_filled`` (the gap-filled L5 map, when there is one) and
  ``measured_share`` of each cell;
- ``<field>_<year>_cell_size.json``: the repeatability of every cell size, the
  choice and why.

Aggregation keeps the mass: a coarse cell is the coverage-weighted mean of the
fine cells inside it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .l4.grid import Grid
from .l4.resolution import ResolutionSettings, assess_records

BANDS = ("yield", "coverage", "yield_filled", "measured_share")
MAX_CELL_M = 60.0


def _blocks(a: np.ndarray, grid: Grid, coarse: Grid, k: int) -> np.ndarray:
    """(coarse.ny, coarse.nx, k*k) view of ``a`` padded with NaN to the coarse grid."""
    c0 = round((grid.xmin - coarse.xmin) / grid.resolution)
    r0 = round((coarse.ymax - grid.ymax) / grid.resolution)
    big = np.full((coarse.ny * k, coarse.nx * k), np.nan)
    big[r0:r0 + grid.ny, c0:c0 + grid.nx] = a
    return big.reshape(coarse.ny, k, coarse.nx, k).swapaxes(1, 2).reshape(coarse.ny, coarse.nx, -1)


def aggregate(grid: Grid, k: int, origin, l4: dict, l5: dict | None = None) -> tuple[dict, Grid]:
    """Aggregate fine-grid bands to cells of ``k`` x ``k`` fine cells."""
    r = grid.resolution
    bounds = (grid.xmin, grid.ymax - grid.ny * r, grid.xmin + grid.nx * r, grid.ymax)
    coarse = Grid.from_bounds(bounds, k * r, origin)
    y = _blocks(np.asarray(l4["yield"], float), grid, coarse, k)
    w = np.nan_to_num(_blocks(np.asarray(l4["coverage"], float), grid, coarse, k))
    w = np.where(np.isfinite(y), w, 0.0)
    sw = w.sum(axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = {
            "yield": np.where(sw > 0, np.nansum(w * np.nan_to_num(y), axis=2) / sw, np.nan),
            "coverage": np.where(sw > 0, sw / (k * k), np.nan),
        }
        nan = np.full(sw.shape, np.nan)
        out["yield_filled"], out["measured_share"] = nan, nan.copy()
        if l5 is not None:
            yf = _blocks(np.asarray(l5["yield"], float), grid, coarse, k)
            src = _blocks(np.asarray(l5["source"], float), grid, coarse, k)
            ok = np.isfinite(yf) & (np.nan_to_num(src) >= 1)
            n = ok.sum(axis=2)
            out["yield_filled"] = np.where(n > 0, np.where(ok, yf, 0.0).sum(axis=2) / n, np.nan)
            out["measured_share"] = np.where(n > 0, (ok & (src == 1)).sum(axis=2) / n, np.nan)
    return out, coarse


def candidates(resolution: float, max_m: float = MAX_CELL_M) -> tuple[float, ...]:
    """Multiples of the working cell size, so that aggregation is exact."""
    n = max(1, int(max_m // resolution))
    return tuple(resolution * k for k in range(1, n + 1))


def run(config, out_dir=None) -> dict | None:
    """Assess cell sizes, aggregate the maps and write the products. None when switched off
    or when there is no L4 grid."""
    mode = getattr(config, "presentation", "auto")
    if mode in ("off", False, None):
        return None
    import geopandas as gpd

    from .l4.io import read_bands, write_cog

    out = Path(out_dir or config.output_dir)
    stem = f"{config.field_id}_{config.year}"
    l4_path, l3_path = out / f"L4_{stem}.tif", out / f"L3_{stem}_records.parquet"
    if not l4_path.exists() or not l3_path.exists():
        return None
    l4, grid, crs = read_bands(l4_path)
    l5_path = out / f"L5_{stem}.tif"
    l5 = read_bands(l5_path)[0] if l5_path.exists() else None
    settings = ResolutionSettings(
        candidates_m=candidates(grid.resolution),
        min_reliability=float(getattr(config, "presentation_min_reliability", 0.8)),
    )
    res = assess_records(gpd.read_parquet(l3_path), config.grid_origin, settings)
    choice = dict(res["choice"])
    if mode != "auto":
        size = float(mode)
        row = next((t for t in res["table"] if t["resolution_m"] == size), None)
        choice = {"resolution_m": size, "met_target": bool(
            row and row["reliability"] is not None and row["reliability"] >= res["target"]),
            "reason": "cell size set in the config (grid.presentation_m)"}
    elif choice["resolution_m"] is None:
        choice["resolution_m"] = grid.resolution
    k = max(1, round(choice["resolution_m"] / grid.resolution))
    bands, coarse = aggregate(grid, k, config.grid_origin, l4, l5)
    row = next((t for t in res["table"] if t["resolution_m"] == coarse.resolution), {})
    path = write_cog(bands, coarse, crs, out / f"L4_{stem}_reliable.tif", names=BANDS)
    fine_mass = float(np.nansum(np.asarray(l4["yield"]) * np.nan_to_num(l4["coverage"])))
    coarse_mass = float(np.nansum(bands["yield"] * np.nan_to_num(bands["coverage"])) * k * k)
    info = {
        "working_resolution_m": grid.resolution,
        "resolution_m": coarse.resolution,
        "mode": "auto" if mode == "auto" else "fixed",
        "target_reliability": res["target"],
        "met_target": choice["met_target"],
        "reason": choice["reason"],
        "reliability": row.get("reliability"),
        "se_t_ha": row.get("se_t_ha"),
        "cells": int(np.isfinite(bands["yield"]).sum()),
        "mass_ratio": coarse_mass / fine_mass if fine_mass else None,
        "table": res["table"],
        "file": path.name,
    }
    (out / f"{stem}_cell_size.json").write_text(json.dumps(info, indent=2))
    return info
