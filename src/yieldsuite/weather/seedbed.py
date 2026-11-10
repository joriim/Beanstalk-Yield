"""Seedbed conditions per cell without a temperature map.

A per-cell soil temperature cannot be had from public data (see
docs/design/gpp-vpm.md). Three things stand in for it:

- **Bare-soil wetness** from Sentinel-2 NDMI (B8A, B11; 20 m) on dates around
  sowing when the cell is still bare. Wet soil warms slowly. Each date is
  expressed as the difference from the field median of that date, so dates in
  different tillage states can be averaged; the correlation between dates
  says whether the pattern repeats at all.
- **Emergence delay**: the day the fAPAR curve of a cell first reaches a
  threshold, against the field median. The crop integrates seedbed
  temperature, moisture and sowing depth. The delay is resolved no finer than
  the gap between the two images around the crossing, which is reported.
- **Soil temperature loggers** at a few places, to measure how large the
  within-field contrast really is.

The maps are for comparing cells with each other. Each difference from the
field median therefore comes with a standard deviation and a class: -1 / +1
when the cell differs from the median by more than two standard deviations,
0 when it cannot be told apart from it.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .agro import SOIL_TEMP_COLUMN, growing_degree_days
from .rue import fapar_from_ndvi


def distinct(diff, sigma, k: float = 2.0) -> np.ndarray:
    """-1 / 0 / +1: is ``diff`` from the field median larger than ``k`` sigma? NaN if unknown."""
    diff, sigma = np.asarray(diff, float), np.asarray(sigma, float)
    with np.errstate(invalid="ignore"):
        cls = np.where(np.abs(diff) > k * sigma, np.sign(diff), 0.0)
    return np.where(np.isfinite(diff) & np.isfinite(sigma), cls, np.nan)


def _by_date(wide: pd.DataFrame) -> pd.DataFrame:
    out = wide.copy()
    out.index = pd.to_datetime(out.index).date
    return out.sort_index()


def bare_soil_wetness(
    ndvi: pd.DataFrame,
    ndmi: pd.DataFrame,
    sowing: date,
    days_before: int = 30,
    days_after: int = 7,
    bare_ndvi: float = 0.25,
    min_share: float = 0.5,
) -> dict:
    """Mean NDMI anomaly of bare soil per cell. Frames: rows = dates, columns = cells."""
    ndvi, ndmi = _by_date(ndvi), _by_date(ndmi)
    lo, hi = sowing - timedelta(days=days_before), sowing + timedelta(days=days_after)
    n_cells = ndmi.shape[1]
    in_field = ndmi.notna().any(axis=0).to_numpy() | ndvi.notna().any(axis=0).to_numpy()
    anomalies, used = [], []
    for d in ndmi.index:
        if not (lo <= d <= hi) or d not in ndvi.index:
            continue
        v = ndmi.loc[d].to_numpy(dtype=float)
        bare = np.isfinite(v) & (ndvi.loc[d].to_numpy(dtype=float) < bare_ndvi)
        if bare.sum() < max(3, min_share * in_field.sum()):
            continue  # cloud, or the field is green (ley, winter crop, weeds)
        anomalies.append(np.where(bare, v - np.median(v[bare]), np.nan))
        used.append(d)
    if not anomalies:
        nan = np.full(n_cells, np.nan)
        return {
            "wetness": nan,
            "sigma": nan,
            "class": nan,
            "n_dates": np.zeros(n_cells),
            "dates": [],
            "repeatability": None,
        }
    a = np.vstack(anomalies)
    n = np.isfinite(a).sum(axis=0)
    with np.errstate(invalid="ignore"):
        wet = np.where(n > 0, np.nansum(a, axis=0) / np.maximum(n, 1), np.nan)
    # Standard error of the cell mean from the scatter between dates, pooled over
    # cells: two or three dates are too few to estimate it cell by cell.
    dof = np.maximum(n - 1, 0).sum()
    sigma = np.full(n_cells, np.nan)
    if dof > 0:
        pooled = np.sqrt(np.nansum((a - wet) ** 2) / dof)
        sigma = np.where(n > 0, pooled / np.sqrt(np.maximum(n, 1)), np.nan)
    return {
        "wetness": wet,
        "sigma": sigma,
        "class": distinct(wet, sigma),
        "n_dates": n.astype(float),
        "dates": [str(d) for d in used],
        "repeatability": _mean_pairwise_correlation(a),
    }


def _mean_pairwise_correlation(a: np.ndarray) -> float | None:
    """Mean correlation of the cell pattern between dates; None with fewer than two dates."""
    r = []
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            ok = np.isfinite(a[i]) & np.isfinite(a[j])
            if ok.sum() >= 10 and a[i][ok].std() > 0 and a[j][ok].std() > 0:
                r.append(float(np.corrcoef(a[i][ok], a[j][ok])[0, 1]))
    return float(np.mean(r)) if r else None


def emergence(
    ndvi: pd.DataFrame,
    sowing: date,
    harvest: date | None = None,
    gdd_cum: pd.Series | None = None,
    threshold: float = 0.2,
    fapar_noise: float = 0.025,
) -> dict:
    """Day from sowing when fAPAR first reaches ``threshold``, per cell.

    fAPAR is 0 at sowing and linear between images, as in the IPAR sum.
    ``gap_days`` is the distance between the two images (or sowing and the
    first image) around the crossing: the delay is not known more finely.
    More images in that interval (PlanetScope, drone) narrow it.

    ``sigma_days`` is the precision of the day from image noise alone
    (``fapar_noise``, one standard deviation per image). Cells that cross in
    the same interval share the error of the straight-line assumption, so
    their *difference* is known to about ``sigma_days`` even when the date
    itself is only known to within the gap. ``lag_class`` uses it.
    """
    ndvi = _by_date(ndvi)
    obs = [d for d in ndvi.index if d > sowing and (harvest is None or d < harvest)]
    n_cells = ndvi.shape[1]
    days = np.full(n_cells, np.nan)
    gap = np.full(n_cells, np.nan)
    sigma = np.full(n_cells, np.nan)
    start = np.full(n_cells, np.nan)  # day of the image (or sowing) before the crossing
    if obs:
        t = np.asarray([(d - sowing).days for d in obs], dtype=float)
        f = fapar_from_ndvi(ndvi.loc[obs].to_numpy(dtype=float))
        f[~np.isfinite(ndvi.loc[obs].to_numpy(dtype=float))] = np.nan
        for c in range(n_cells):
            ok = np.isfinite(f[:, c])
            if not ok.any():
                continue
            tc = np.concatenate([[0.0], t[ok]])
            fc = np.concatenate([[0.0], f[ok, c]])
            above = np.nonzero(fc >= threshold)[0]
            if not len(above):
                continue  # never emerged as far as the images show
            k = above[0]
            days[c] = tc[k - 1] + (threshold - fc[k - 1]) / (fc[k] - fc[k - 1]) * (
                tc[k] - tc[k - 1]
            )
            gap[c] = tc[k] - tc[k - 1]
            start[c] = tc[k - 1]
            rise = fc[k] - fc[k - 1]
            # the value at sowing is 0 by definition and carries no noise
            before = 0.0 if k == 1 else (fc[k] - threshold) ** 2
            sigma[c] = (
                gap[c] * fapar_noise * np.sqrt(before + (threshold - fc[k - 1]) ** 2) / rise**2
            )
    out = {
        "days": days,
        "gap_days": gap,
        "gap_start_days": start,
        "lag_days": days - np.nanmedian(days) if obs else days,
    }
    out["sigma_days"] = sigma
    out["lag_class"] = distinct(out["lag_days"], sigma)
    if gdd_cum is not None and np.isfinite(days).any():
        g = pd.Series(gdd_cum.to_numpy(dtype=float), index=pd.to_datetime(pd.Index(gdd_cum.index)))
        x = np.asarray([(d.date() - sowing).days for d in g.index], dtype=float)
        y = g.to_numpy() - np.interp(0.0, x, g.to_numpy())
        out["gdd"] = np.where(np.isfinite(days), np.interp(np.nan_to_num(days), x, y), np.nan)
        out["lag_gdd"] = out["gdd"] - np.nanmedian(out["gdd"])
    return out


def read_loggers(path: str | Path) -> pd.DataFrame:
    """Logger CSV with columns ``logger``, ``time``, ``temp_c`` -> daily means.

    Separator and decimal comma are detected. Returns a frame indexed by date
    with one column per logger; a day needs at least 20 hours of readings.
    """
    raw = pd.read_csv(path, sep=None, engine="python", dtype=str)
    raw.columns = [c.strip().lower() for c in raw.columns]
    missing = {"logger", "time", "temp_c"} - set(raw.columns)
    if missing:
        raise ValueError(f"logger file {path}: missing columns {sorted(missing)}")
    df = pd.DataFrame(
        {
            "logger": raw["logger"].str.strip(),
            "time": pd.to_datetime(raw["time"]),
            "temp_c": pd.to_numeric(raw["temp_c"].str.replace(",", "."), errors="coerce"),
        }
    ).dropna()
    df["date"] = df["time"].dt.date
    df["hour"] = df["time"].dt.hour
    g = df.groupby(["logger", "date"])
    daily = g["temp_c"].mean()[g["hour"].nunique() >= 20]
    return daily.unstack("logger").sort_index()


def logger_summary(
    daily: pd.DataFrame,
    sowing: date | None,
    harvest: date | None,
    t_base_c: float,
    weather: pd.DataFrame | None = None,
) -> dict:
    """Thermal sums per logger over the days all loggers share, and their contrast."""
    idx = pd.Index(daily.index)
    sel = np.ones(len(daily), bool)
    if sowing:
        sel &= idx >= sowing
    if harvest:
        sel &= idx <= harvest
    common = daily[sel].dropna(how="any")
    if common.empty:
        return {"loggers": {}, "note": "no day with readings from every logger in the season"}
    era = None
    if weather is not None and SOIL_TEMP_COLUMN in weather:
        era = weather[SOIL_TEMP_COLUMN].reindex(common.index)
    loggers = {}
    for name in common.columns:
        t = common[name].to_numpy(dtype=float)
        loggers[str(name)] = {
            "mean_c": float(t.mean()),
            "thermal_sum_c_days": float(growing_degree_days(t, t_base_c).sum()),
        }
        if era is not None and era.notna().any():
            loggers[str(name)]["minus_era5_land_c"] = float(np.nanmean(t - era.to_numpy(float)))
    sums = [v["thermal_sum_c_days"] for v in loggers.values()]
    means = [v["mean_c"] for v in loggers.values()]
    return {
        "period": {
            "start": str(common.index[0]),
            "end": str(common.index[-1]),
            "days": len(common),
        },
        "t_base_c": t_base_c,
        "loggers": loggers,
        "contrast": {
            "mean_c": float(max(means) - min(means)),
            "thermal_sum_c_days": float(max(sums) - min(sums)),
        },
    }
