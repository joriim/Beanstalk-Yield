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


def clean_readings(q: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if "moisture_pct" not in q.columns:
        return q.iloc[0:0], {"reason": "the analyzer export has no moisture column"}
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


def apply(config, df: pd.DataFrame, t: np.ndarray, moisture: np.ndarray, mass_wet: np.ndarray,
          machine_id: str, cache: dict) -> tuple[np.ndarray, np.ndarray, dict] | None:
    """Correct one machine's moisture (and wet mass of vendor-dry yields) in L2.

    ``cache`` keeps the analyzer exports read once per run.
    """
    from .pipeline import align_to_records, read_quality

    qc = config.quality
    if qc is None or qc.moisture == "off":
        return None
    sources = [s for s in qc.sources if s.machine_id == machine_id]
    if not sources:
        return None
    analyzer = np.full(len(t), np.nan)
    reports = []
    for src in sources:
        key = str(src.path)
        if key not in cache:
            res = read_quality(src.path, src.format, src.columns, src.sheet)
            cache[key] = (res, *clean_readings(res.measurements))
        res, q, clean = cache[key]
        if not len(q):
            reports.append({"source": key, **clean})
            continue
        prof = config.machines.get(machine_id)
        delay = src.delay_s if src.delay_s is not None else (
            prof.tau_d_s if prof and prof.tau_d_s is not None else 0.0)
        interval = res.report.get("median_interval_s", 5.0)
        gap = src.max_gap_s if src.max_gap_s is not None else 0.75 * interval + 1.0
        al = align_to_records(q, t, delay, gap)["moisture_pct"].to_numpy()
        analyzer = np.where(np.isfinite(analyzer), analyzer, al)
        matched = int(np.isfinite(al).sum())
        reports.append({"source": key, **clean, "delay_s": delay, "matched": matched})
    vendor = (df["vendor_dry_moisture"].to_numpy(float) if "vendor_dry_moisture" in df
              else np.full(len(t), np.nan))
    sensor_raw = (df["moisture_sensor"].to_numpy(float) if "moisture_sensor" in df
                  else np.full(len(t), np.nan))
    is_dry = np.isfinite(vendor)
    sensor = np.where(is_dry, sensor_raw, moisture)
    new, info = corrected_moisture(sensor, analyzer, qc.moisture)
    info["sources"] = reports
    if new is None:
        return moisture, mass_wet, info
    mass = np.asarray(mass_wet, float).copy()
    undo = is_dry & np.isfinite(sensor)
    mass[undo] = mass[undo] * (100.0 - vendor[undo]) / (100.0 - sensor[undo])
    skipped = int((is_dry & ~np.isfinite(sensor)).sum())
    new = np.where(is_dry & ~np.isfinite(sensor), moisture, new)
    info.update(
        records=int(len(t)),
        records_with_analyzer=int(np.isfinite(analyzer).sum()),
        vendor_dry_rewetted=int(undo.sum()),
        vendor_dry_without_combine_moisture=skipped,
        median_moisture_before_pct=float(np.nanmedian(np.where(is_dry, sensor, moisture))),
        median_moisture_after_pct=float(np.nanmedian(new)),
    )
    return new, mass, info
