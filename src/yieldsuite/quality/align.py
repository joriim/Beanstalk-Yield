"""One place where analyzer readings are attached to harvester records.

Used by the quality maps (Q2-Q4), the moisture correction (L2) and the timelapse
layers, so the rules are the same everywhere:

    analyzer time of the grain a record cut = t_record + transit_s - clock_offset_s

- ``clock_offset_s``: yield-monitor clock minus analyzer clock (the two devices are
  not synchronised; +21 s means the analyzer's clock is 21 s behind). ``auto`` reads
  it from the positions: the offset at which most analyzer positions fall within
  10 m of the logged position.
- ``transit_s``: time the grain takes from the header to the analyzer.
- ``delay_s`` (older configs) is the two combined, transit_s - clock_offset_s.
- ``max_gap_s``: a reading further than this from the target moment is not used.
- ``max_dist_m``: position gate; see ``pipeline.align_to_records``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def clock_offset_from_positions(q: pd.DataFrame, rec_t, rec_x, rec_y, search_s=(-120, 120),
                                near_m: float = 10.0) -> dict:
    """Offset (s) to add to analyzer times so that its positions match the log."""
    if "x" not in q or "y" not in q or len(q) < 20:
        return {"clock_offset_s": None, "reason": "the analyzer export has no positions"}
    rt = np.asarray(rec_t, float)
    order = np.argsort(rt)
    rt, rx, ry = rt[order], np.asarray(rec_x, float)[order], np.asarray(rec_y, float)[order]
    qt, qx, qy = q["t"].to_numpy(float), q["x"].to_numpy(float), q["y"].to_numpy(float)
    ok = np.isfinite(qx) & np.isfinite(qy)
    qt, qx, qy = qt[ok], qx[ok], qy[ok]
    rows = []
    for c in np.arange(search_s[0], search_s[1] + 0.5, 1.0):
        tq = qt + c
        j = np.clip(np.searchsorted(rt, tq), 1, len(rt) - 1)
        jj = np.where(np.abs(rt[j] - tq) < np.abs(rt[j - 1] - tq), j, j - 1)
        hit = np.abs(rt[jj] - tq) <= 3.0
        d = np.hypot(qx - rx[jj], qy - ry[jj])
        near = hit & (d <= near_m)
        med = float(np.median(d[near])) if near.any() else np.inf
        rows.append((float(c), int(near.sum()), med, int(hit.sum())))
    top = max(r[1] for r in rows)
    # many offsets put the readings within reach of the track: among those, the closest
    c, score, med, n_hit = min((r for r in rows if r[1] >= 0.9 * top), key=lambda r: r[2])
    med = None if not np.isfinite(med) else med
    if score < 20:
        return {"clock_offset_s": None, "matched": score,
                "reason": "fewer than 20 analyzer positions fall on the logged track"}
    # how sharp is the optimum: scores 5 s away should be clearly lower
    far = [r[2] for r in rows if 5 <= abs(r[0] - c) <= 8 and np.isfinite(r[2])]
    return {"clock_offset_s": c, "matched": score, "readings_with_log": n_hit,
            "share_on_track": score / max(len(qt), 1), "median_dist_m": med,
            "sharp": bool(not far or (med is not None and min(far) > 1.5 * med + 1.0))}


def timing(src, profile, q: pd.DataFrame | None = None, rec=None) -> dict:
    """Resolve clock offset, transit and the resulting delay for one analyzer source."""
    transit = getattr(src, "transit_s", None)
    clock = getattr(src, "clock_offset_s", None)
    info: dict = {}
    if clock == "auto" or (clock is None and transit is None and src.delay_s is None
                           and q is not None and rec is not None and "x" in q):
        est = clock_offset_from_positions(q, rec[0], rec[1], rec[2])
        info["clock_estimate"] = est
        clock = est["clock_offset_s"]
        info["clock_source"] = "estimated from positions" if clock is not None else "unknown"
    elif clock is not None:
        info["clock_source"] = "config"
    if clock is not None or transit is not None:
        delay = float(transit or 0.0) - float(clock or 0.0)
        info.update(clock_offset_s=clock, transit_s=transit, delay_s=delay)
        if transit is None:
            info["transit_note"] = "transit time not given: taken as 0 s"
        return info
    if src.delay_s is not None:
        return {**info, "delay_s": float(src.delay_s), "clock_offset_s": None, "transit_s": None,
                "clock_source": "delay_s (clock offset and transit time not separated)"}
    tau = profile.tau_d_s if profile is not None and profile.tau_d_s is not None else 0.0
    return {**info, "delay_s": float(tau), "clock_offset_s": None, "transit_s": None,
            "clock_source": "machine tau_d; device clocks assumed synchronised"}


def analyzer_for_records(config, src, rec: pd.DataFrame, cache: dict, need_moisture=False):
    """Readings of one analyzer source aligned to ``rec`` (records of its machine).

    Returns (aligned DataFrame indexed like ``rec``, report dict) or (None, report).
    ``cache`` keeps the parsed export and the timing per source for the run.
    """
    from .moisture import clean_readings
    from .pipeline import add_xy, align_to_records, read_quality

    key = f"{src.path}|{src.sheet}"
    if key not in cache:
        res = read_quality(src.path, src.format, src.columns, src.sheet)
        q, clean = clean_readings(res.measurements, need_moisture=False)
        cache[key] = {"res": res, "q": add_xy(q, config.crs), "clean": clean}
    c = cache[key]
    res, q = c["res"], c["q"]
    report = {"source": str(src.path), "sheet": src.sheet, **c["clean"]}
    if need_moisture and "moisture_pct" not in q:
        return None, {**report, "reason": "the analyzer export has no moisture column"}
    if not len(q):
        return None, {**report, "reason": "no usable readings"}
    t = rec["t"].to_numpy(float)
    nan = np.full(len(rec), np.nan)
    x = rec["x"].to_numpy(float) if "x" in rec else nan
    y = rec["y"].to_numpy(float) if "y" in rec else nan
    if "timing" not in c:
        c["timing"] = timing(src, config.machines.get(src.machine_id), q, (t, x, y))
    tm = c["timing"]
    interval = res.report.get("median_interval_s", 5.0)
    gap = src.max_gap_s if src.max_gap_s is not None else 0.75 * interval + 1.0
    al = align_to_records(q, t, tm["delay_s"], gap, (x, y), src.max_dist_m)
    al.index = rec.index
    report.update(
        timing=tm,
        delay_s=tm["delay_s"],
        max_gap_s=gap,
        matched=int((al["q_index"] >= 0).sum()),
        position_gate={"max_dist_m": src.max_dist_m,
                       "records_dropped": int(al["gate_dropped"].sum()),
                       "records_kept": int((al["q_index"] >= 0).sum())},
        readings=int(len(q)),
        readings_used=int(al.loc[al["q_index"] >= 0, "q_index"].nunique()),
    )
    return al, report
