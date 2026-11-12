"""Grain moisture from the quality analyzer (e.g. GrainSense) into the yield chain.

The yield at reference moisture depends on the moisture used to standardise
the weighed or sensed mass: one point of moisture bias is about 1.2 % of dry
yield. Combine moisture sensors drift with crop and temperature; an NIR
analyzer such as GrainSense measures moisture of the same grain stream, so its
readings are used to correct the combine's moisture before standardisation
(L2), and so before reconciliation and the grid.

Modes (``quality: moisture:``):

- ``auto`` (default): ``calibrate`` when at least ``MIN_PAIRS`` analyzer readings
  match records with combine moisture; the analyzer values directly when the
  combine logged no moisture at all; otherwise nothing changes.
- ``calibrate``: fit analyzer = a + b x combine (Theil-Sen; offset only when the
  combine moisture hardly varies) on matched records and apply it to every
  record. Robust to an analyzer session that covers only part of the field.
- ``replace``: analyzer value where a reading matches the record, calibrated
  combine value elsewhere.
- ``off``.

Analyzer readings outside 8-40 % moisture, 6-25 % protein, or with a zero
carbohydrate share (failed scans) are dropped first.

Dry or standard-moisture vendor yields are first turned back into wet mass with
the combine moisture the vendor used, then standardised with the corrected
moisture: ``wet = dry x (100 - vendor_ref) / (100 - combine)``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_PAIRS = 30
MC_RANGE = (8.0, 40.0)
PROTEIN_RANGE = (6.0, 25.0)


def clean_readings(q: pd.DataFrame, need_moisture: bool = True) -> tuple[pd.DataFrame, dict]:
    """Drop failed scans; with ``need_moisture`` an export without moisture gives none."""
    if "moisture_pct" not in q.columns:
        if need_moisture:
            return q.iloc[0:0], {"reason": "the analyzer export has no moisture column"}
        ok = pd.Series(True, index=q.index)
    else:
        ok = q["moisture_pct"].between(*MC_RANGE)
    if "protein_pct" in q.columns:
        ok &= q["protein_pct"].between(*PROTEIN_RANGE)
    if "carbohydrate_pct" in q.columns:
        ok &= q["carbohydrate_pct"] > 0
    return q[ok].reset_index(drop=True), {"readings": int(len(q)), "dropped": int((~ok).sum())}


def calibrate(sensor: np.ndarray, analyzer: np.ndarray) -> dict:
    """Fit analyzer = a + b x sensor on matched pairs."""
    ok = np.isfinite(sensor) & np.isfinite(analyzer)
    s, a = sensor[ok], analyzer[ok]
    out = {"pairs": int(ok.sum())}
    if ok.sum() < MIN_PAIRS:
        return {**out, "method": None, "reason": f"only {ok.sum()} matched pairs (< {MIN_PAIRS})"}
    offset = float(np.median(a - s))
    out.update(
        median_combine_pct=float(np.median(s)),
        median_analyzer_pct=float(np.median(a)),
        median_offset_pct=offset,
    )
    if np.std(s) >= 1.0:
        from scipy.stats import theilslopes

        b, a0, _, _ = theilslopes(a, s)
        if 0.5 <= b <= 1.5:
            return {**out, "method": "linear", "intercept": float(a0), "slope": float(b)}
    return {**out, "method": "offset", "intercept": offset, "slope": 1.0}


def corrected_moisture(sensor, analyzer, mode: str = "auto") -> tuple[np.ndarray | None, dict]:
    """New moisture per record, or None when nothing changes."""
    sensor = np.asarray(sensor, float)
    analyzer = np.asarray(analyzer, float)
    if mode == "off":
        return None, {"mode": "off"}
    n_an = int(np.isfinite(analyzer).sum())
    if not np.isfinite(sensor).any():
        if n_an == 0:
            return None, {"mode": mode, "reason": "no analyzer moisture matches the records"}
        fill = float(np.nanmedian(analyzer))
        new = np.where(np.isfinite(analyzer), analyzer, fill)
        return new, {"mode": "analyzer", "pairs": n_an, "fill_pct": fill,
                     "reason": "the combine logged no moisture"}
    cal = calibrate(sensor, analyzer)
    if cal["method"] is None:
        return None, {"mode": mode, **cal}
    fitted = np.clip(cal["intercept"] + cal["slope"] * sensor, 5.0, 45.0)
    if mode == "replace":
        new = np.where(np.isfinite(analyzer), analyzer, fitted)
        return new, {"mode": "replace", **cal}
    return fitted, {"mode": "calibrate", **cal}


# --- entered (not measured) combine moisture ---------------------------------------

MIN_RUN = 30  # identical consecutive values counted as a run
LONG_RUN = 150  # a run this long is not a measurement, whatever the value
DEFAULT_TOTAL = 100  # a value in runs totalling this many records is a default/entered value
INTERP_MAX_S = 600.0  # fill entered values from measured ones at most this far in time

SOURCE_CODES = {
    "logged": 0,
    "analyzer": 1,
    "calibrated": 2,
    "interpolated": 3,
    "median": 4,
    "vendor_reference": 5,  # dry vendor yield without the moisture behind it
}


def entered_values(t, mc) -> tuple[np.ndarray, dict]:
    """Records whose combine moisture was entered or held, not measured.

    A sensor that is not reading (empty measuring cell, start of a task, a
    manual entry) logs one value unchanged for minutes. Flagged: the whole log
    when it hardly varies; otherwise runs of >= ``MIN_RUN`` identical values of
    a value that fills >= ``DEFAULT_TOTAL`` records in such runs (e.g. 17.0 %),
    and any run >= ``LONG_RUN``.
    """
    mc = np.asarray(mc, float)
    t = np.asarray(t, float)
    n = len(mc)
    bad = np.zeros(n, bool)
    fin = np.isfinite(mc)
    if fin.sum() < 2:
        return bad, {"measured": False, "reason": "no combine moisture"}
    order = np.argsort(t, kind="stable")
    v = np.round(mc[order], 2)
    if np.nanstd(v) < 0.05 or len(np.unique(v[np.isfinite(v)])) <= 2:
        bad[fin] = True
        return bad, {
            "measured": False,
            "entered_value_pct": float(np.nanmedian(v)),
            "reason": "constant combine moisture: entered, not measured",
        }
    change = np.r_[True, (v[1:] != v[:-1]) | ~np.isfinite(v[1:])]
    run_id = np.cumsum(change)
    size = np.bincount(run_id)[run_id]
    long_ = (size >= MIN_RUN) & np.isfinite(v)
    totals: dict[float, int] = {}
    runs: dict[float, int] = {}
    first = change & long_
    for val, sz in zip(v[first], size[first], strict=True):
        totals[float(val)] = totals.get(float(val), 0) + int(sz)
        runs[float(val)] = runs.get(float(val), 0) + 1
    defaults = {k for k, tot in totals.items() if tot >= DEFAULT_TOTAL or runs[k] >= 3}
    flag_sorted = long_ & (np.isin(v, list(defaults)) | (size >= LONG_RUN))
    bad[order] = flag_sorted
    return bad, {
        "measured": True,
        "entered_records": int(bad.sum()),
        "entered_share": float(bad.mean()),
        "default_values_pct": {
            str(k): {"runs": runs[k], "records": totals[k]} for k in sorted(defaults)
        },
    }


def interpolate_in_time(t, values, missing, max_s: float = INTERP_MAX_S) -> np.ndarray:
    """Fill ``missing`` records from the nearest finite values in time (linear)."""
    t = np.asarray(t, float)
    v = np.asarray(values, float).copy()
    ok = np.isfinite(v) & ~missing
    if ok.sum() < 2:
        return np.where(missing, np.nan, v)
    order = np.argsort(t[ok])
    tk, vk = t[ok][order], v[ok][order]
    fill = np.interp(t, tk, vk)
    j = np.clip(np.searchsorted(tk, t), 1, len(tk) - 1)
    near = np.minimum(np.abs(t - tk[j - 1]), np.abs(tk[j] - t))
    v[missing] = np.where(near[missing] <= max_s, fill[missing], np.nan)
    return v


def _analyzer_moisture(config, t, machine_id, cache):
    from .pipeline import align_to_records, read_quality

    qc = config.quality
    analyzer = np.full(len(t), np.nan)
    reports = []
    if qc is None or qc.moisture == "off":
        return analyzer, reports
    for src in [s for s in qc.sources if s.machine_id == machine_id]:
        key = f"{src.path}|{src.sheet}"
        if key not in cache:
            res = read_quality(src.path, src.format, src.columns, src.sheet)
            cache[key] = (res, *clean_readings(res.measurements))
        res, q, clean = cache[key]
        if not len(q):
            reports.append({"source": str(src.path), **clean})
            continue
        prof = config.machines.get(machine_id)
        delay = src.delay_s if src.delay_s is not None else (
            prof.tau_d_s if prof and prof.tau_d_s is not None else 0.0)
        interval = res.report.get("median_interval_s", 5.0)
        gap = src.max_gap_s if src.max_gap_s is not None else 0.75 * interval + 1.0
        al = align_to_records(q, t, delay, gap)["moisture_pct"].to_numpy()
        analyzer = np.where(np.isfinite(analyzer), analyzer, al)
        matched = int(np.isfinite(al).sum())
        reports.append({"source": str(src.path), **clean, "delay_s": delay, "matched": matched})
    return analyzer, reports


def apply(config, df: pd.DataFrame, t: np.ndarray, moisture: np.ndarray, mass_wet: np.ndarray,
          machine_id: str, cache: dict):
    """Moisture of one machine in L2: entered combine values replaced, analyzer
    moisture applied, vendor-dry yields rewetted. Returns (moisture, mass_wet, info,
    source) or None when nothing changes; ``source`` codes are ``SOURCE_CODES``.

    ``cache`` keeps the analyzer exports read once per run.
    """
    t = np.asarray(t, float)
    vendor = (df["vendor_dry_moisture"].to_numpy(float) if "vendor_dry_moisture" in df
              else np.full(len(t), np.nan))
    sensor_raw = (df["moisture_sensor"].to_numpy(float) if "moisture_sensor" in df
                  else np.full(len(t), np.nan))
    is_dry = np.isfinite(vendor)
    used = np.where(is_dry, sensor_raw, moisture)  # what the vendor / the log used
    entered, ent = entered_values(t, used)
    sensor = np.where(entered, np.nan, used)  # measured combine moisture only
    analyzer, reports = _analyzer_moisture(config, t, machine_id, cache)
    mode = config.quality.moisture if config.quality is not None else "off"
    new, info = (corrected_moisture(sensor, analyzer, mode) if mode != "off"
                 else (None, {"mode": "off"}))
    info["combine_moisture"] = ent
    if reports:
        info["sources"] = reports
    if new is None and not entered.any() and not (is_dry & np.isfinite(used)).any():
        return None if not reports else (moisture, mass_wet, info, None)
    source = np.full(len(t), SOURCE_CODES["logged"], np.int8)
    if new is None:  # no analyzer correction: keep measured values, refill entered ones
        new = sensor.copy()
    else:
        cal = info.get("mode") in ("calibrate", "replace")
        source[np.isfinite(sensor)] = SOURCE_CODES["calibrated" if cal else "analyzer"]
    if info.get("mode") in ("replace", "analyzer"):
        source[np.isfinite(analyzer)] = SOURCE_CODES["analyzer"]
    # entered records: analyzer reading if any, else in time from measured neighbours
    take = entered & np.isfinite(analyzer)
    new = np.where(take, analyzer, new)
    source[take] = SOURCE_CODES["analyzer"]
    rest = entered & ~take
    if rest.any():
        filled = interpolate_in_time(t, new, rest)
        ok = rest & np.isfinite(filled)
        new = np.where(ok, filled, new)
        source[ok] = SOURCE_CODES["interpolated"]
        left = rest & ~ok
        if left.any() and np.isfinite(new[~rest]).any():
            new[left] = np.nanmedian(new[~rest])
            source[left] = SOURCE_CODES["median"]
    mass = np.asarray(mass_wet, float).copy()
    undo = is_dry & np.isfinite(used) & np.isfinite(new)
    mass[undo] = mass[undo] * (100.0 - vendor[undo]) / (100.0 - used[undo])
    keep = ~np.isfinite(new) | (is_dry & ~np.isfinite(used))
    new = np.where(keep, moisture, new)
    source[keep] = np.where(is_dry[keep], SOURCE_CODES["vendor_reference"], SOURCE_CODES["logged"])
    info.update(
        records=int(len(t)),
        records_with_analyzer=int(np.isfinite(analyzer).sum()),
        vendor_dry_rewetted=int(undo.sum()),
        median_moisture_before_pct=float(np.nanmedian(used)) if np.isfinite(used).any() else None,
        median_moisture_after_pct=float(np.nanmedian(new)) if np.isfinite(new).any() else None,
        source_records={k: int((source == c).sum()) for k, c in SOURCE_CODES.items()},
    )
    return new, mass, info, source


# --- moisture map (L4) -------------------------------------------------------------

TREND_WINDOW = "30min"
MOISTURE_BANDS = (
    "moisture_pct",
    "moisture_time_trend_pct",
    "moisture_spatial_pct",
    "moisture_measured_share",
)


def time_trend(t, mc, machine, window: str = TREND_WINDOW) -> np.ndarray:
    """Centred rolling median of moisture over harvest time, per machine.

    Grain dries during the day and takes up moisture at night and after rain;
    this part of the moisture belongs to the harvest time, not to the place.
    """
    out = np.full(len(t), np.nan)
    s = pd.DataFrame({"t": pd.to_datetime(np.asarray(t, float), unit="s"), "mc": mc,
                      "m": np.asarray(machine)})
    for _, g in s.groupby("m", sort=False):
        g = g.sort_values("t")
        r = g.set_index("t")["mc"].rolling(window, center=True, min_periods=10).median()
        out[g.index.to_numpy()] = r.to_numpy()
    return out


def moisture_bands(rec: pd.DataFrame, grid, cell: np.ndarray, clean: np.ndarray) -> dict:
    """Mass-weighted moisture per cell (as harvested), its harvest-time trend, the
    place-related part (field median + residual from the trend), and the share of
    mass whose moisture was measured (analyzer or measured combine value)."""
    n = grid.ny * grid.nx
    mc = rec["moisture_aligned"].to_numpy(float)
    mass = rec["mass_kg_reconciled"].to_numpy(float)
    src = rec["moisture_source"].to_numpy() if "moisture_source" in rec else np.zeros(len(rec))
    trend = time_trend(rec["t"].to_numpy(float), mc, rec["machine_id"].to_numpy())
    use = clean & np.isfinite(mc) & (cell >= 0)
    w = np.bincount(cell[use], mass[use], minlength=n)

    def wmean(v):
        ok = use & np.isfinite(v)
        num = np.bincount(cell[ok], mass[ok] * v[ok], minlength=n)
        den = np.bincount(cell[ok], mass[ok], minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / den, np.nan).reshape(grid.ny, grid.nx)

    med = float(np.nanmedian(mc[use])) if use.any() else np.nan
    measured = np.isin(src, (SOURCE_CODES["logged"], SOURCE_CODES["analyzer"],
                             SOURCE_CODES["calibrated"])).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(
            w > 0, np.bincount(cell[use], mass[use] * measured[use], minlength=n) / w, np.nan
        ).reshape(grid.ny, grid.nx)
    return {
        "moisture_pct": wmean(mc),
        "moisture_time_trend_pct": wmean(trend),
        "moisture_spatial_pct": wmean(med + mc - trend),
        "moisture_measured_share": share,
    }
