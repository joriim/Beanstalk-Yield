"""Harvest timelapse data: records in time order with the combine and header state.

Every L3 record is one piece of cut area (record polygon) with its time, the
heading, the header state (lowered = harvesting, raised = turning or driving),
the swath, the yield at reference moisture and, when an analyzer ran, the grain
quality of that grain (protein, else moisture). Idle time between passes and
sessions is compressed so the playback shows driving, not waiting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

GAP_CAP_S = 6.0  # a pause longer than this plays as this long


@dataclass
class Track:
    t: np.ndarray  # epoch s
    play: np.ndarray  # compressed playback seconds
    machine: np.ndarray
    x: np.ndarray  # header centre (record front edge), field CRS
    y: np.ndarray
    heading: np.ndarray  # rad, clockwise from north
    header_down: np.ndarray
    swath: np.ndarray
    length: np.ndarray
    polygons: list  # shapely polygons of the cut area
    yield_t_ha: np.ndarray  # NaN where not a clean harvesting record
    cut: np.ndarray | None = None  # header down and cutting (also flagged records)
    quality: np.ndarray | None = None
    quality_name: str = ""
    quality_unit: str = "%"
    extra: dict = field(default_factory=dict)


def _quality_per_record(config, rec: pd.DataFrame) -> tuple[np.ndarray | None, str]:
    """Protein (% DM) of the grain each record cut, from the analyzer; else moisture."""
    qc = config.quality
    if qc is not None and qc.sources:
        from ..quality.moisture import clean_readings
        from ..quality.pipeline import align_to_records, read_quality

        out = np.full(len(rec), np.nan)
        for src in qc.sources:
            res = read_quality(src.path, src.format, src.columns, src.sheet)
            q, _ = clean_readings(res.measurements, need_moisture=False)
            if not len(q):
                continue
            sel = (rec["machine_id"] == src.machine_id).to_numpy()
            prof = config.machines.get(src.machine_id)
            delay = src.delay_s if src.delay_s is not None else (
                prof.tau_d_s if prof and prof.tau_d_s is not None else 0.0)
            gap = src.max_gap_s if src.max_gap_s is not None else (
                0.75 * res.report.get("median_interval_s", 5.0) + 1.0)
            al = align_to_records(q, rec["t"].to_numpy(float)[sel], delay, gap)
            p = al["protein_pct"].to_numpy()
            if qc.protein_basis == "wet" and "moisture_pct" in al:
                p = p / (1 - al["moisture_pct"].to_numpy() / 100)
            out[sel] = np.where(np.isfinite(out[sel]), out[sel], p)
        if np.isfinite(out).any():
            return out, "protein"
    if "moisture_aligned" in rec:
        mc = rec["moisture_aligned"].to_numpy(float)
        if np.isfinite(mc).any() and np.nanstd(mc) > 0:
            return mc, "moisture"
    return None, ""


def load_track(config) -> Track:
    out = Path(config.output_dir)
    base = f"{config.field_id}_{config.year}"
    rec = pd.read_parquet(out / f"L3_{base}_records.parquet").drop(columns="geometry",
                                                                    errors="ignore")
    rec = rec.sort_values(["t", "machine_id"], kind="stable").reset_index(drop=True)
    from ..l2.georef import header_position, record_polygons

    h = rec["heading"].to_numpy(float)
    length = np.nan_to_num(rec["length"].to_numpy(float))
    x = np.full(len(rec), np.nan)
    y = np.full(len(rec), np.nan)
    for mid, idx in rec.groupby("machine_id").indices.items():
        prof = config.machines.get(mid)
        fwd, right = prof.antenna_offset_m if prof else (0.0, 0.0)
        x[idx], y[idx] = header_position(rec["x"].to_numpy(float)[idx],
                                         rec["y"].to_numpy(float)[idx], h[idx], fwd, right)
    swath_cut = rec["swath_used"].to_numpy(float)
    polys = record_polygons(x, y, h, length, np.where(swath_cut > 0, swath_cut, 0.0))
    t = rec["t"].to_numpy(float)
    dt = np.r_[0.0, np.diff(t)]
    play = np.cumsum(np.clip(dt, 0, GAP_CAP_S))
    clean = (rec["flags"].to_numpy() == 0) & (rec["effective_area"].to_numpy(float) > 0)
    y_t = np.where(clean, rec["yield"].to_numpy(float), np.nan)
    q, qname = _quality_per_record(config, rec)
    if q is not None:
        q = np.where(clean, q, np.nan)
    return Track(
        t=t,
        play=play,
        machine=rec["machine_id"].astype(str).to_numpy(),
        x=x,
        y=y,
        heading=h,
        header_down=rec["header_down"].to_numpy(bool),
        swath=np.where(rec["swath"].to_numpy(float) > 0, rec["swath"].to_numpy(float),
                       np.nanmedian(rec["swath_used"].replace(0, np.nan))),
        length=length,
        polygons=list(polys),
        yield_t_ha=y_t,
        cut=rec["header_down"].to_numpy(bool) & (swath_cut > 0),
        quality=q,
        quality_name=qname,
    )


def colour_range(v: np.ndarray, lo_p: float = 2, hi_p: float = 98) -> tuple[float, float]:
    v = v[np.isfinite(v)]
    if not v.size:
        return 0.0, 1.0
    lo, hi = np.percentile(v, [lo_p, hi_p])
    return (float(lo), float(hi)) if hi > lo else (float(lo) - 0.5, float(hi) + 0.5)


QUALITY_LABEL = {
    "fi": {"protein": "proteiini (% ka)", "moisture": "kosteus (%)"},
    "en": {"protein": "protein (% DM)", "moisture": "moisture (%)"},
}
