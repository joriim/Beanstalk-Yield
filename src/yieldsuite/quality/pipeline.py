"""Quality channel Q1-Q4 on top of the harvester chain."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..ingest.mapping import match_columns, normalise_header
from ..ingest.readers import read_any
from ..l3.filters import flag_global_outliers
from ..l4.grid import Grid
from ..timeutil import epoch_seconds

QUALITY_SYNONYMS = {
    "protein": [
        "protein",
        "proteiini",
        "crudeprotein",
        "rawprotein",
        "eiweiss",
        "rohprotein",
        "valkuainen",
        "rp",
    ],
    "moisture": ["moisture", "kosteus", "feuchte", "h2o", "water", "vesi"],
    "oil": ["oil", "oljy", "oel", "fat", "rasva"],
    "starch": ["starch", "tarkkelys", "staerke"],
    "carbohydrate": ["carbohydrate", "carbohydrates", "hiilihydraatti", "kohlenhydrate"],
    "nitrogen": ["nitrogen", "typpi", "stickstoff"],
    "gluten": ["wetgluten", "markagluteeni", "feuchtkleber", "gluten", "gluteeni"],
    "broken": ["brokenkernel", "brokenkernels", "rikkinainenjyva", "bruchkorn"],
    "green": ["greenkernel", "greenkernels", "vihreajyva"],
    "latitude": ["latitude", "leveysaste", "lat", "breite"],
    "longitude": ["longitude", "pituusaste", "lon", "lng", "laenge"],
    "session": ["session", "sessionname", "measurementsession", "sessio"],
}
MEASURES = ("protein", "moisture", "oil", "starch", "carbohydrate", "nitrogen", "gluten",
            "broken", "green")


@dataclass
class QualityResult:
    measurements: pd.DataFrame
    report: dict = field(default_factory=dict)


def _match_quality(headers) -> dict[str, str]:
    norm = {h: normalise_header(h) for h in headers}
    out = {}
    for canon, syns in QUALITY_SYNONYMS.items():
        for h in headers:
            n = norm[h]
            if h in out.values():
                continue
            if n in syns or any(n.startswith(s) and len(s) >= 4 for s in syns):
                out[canon] = h
                break
    return out


def _find_table(df: pd.DataFrame, notes: list, max_rows: int = 15) -> pd.DataFrame:
    """Measurement table below a summary block (e.g. a GrainSense app session sheet:
    one session summary row, then the header 'Mittausaika, Proteiini [%] ...').

    If the first row is already a header with time and protein, the frame is kept;
    otherwise the first row (within ``max_rows``) that holds both becomes the header.
    """

    def has_header(cells) -> bool:
        cols = [str(c) for c in cells]
        t = match_columns(cols, {})
        return "time" in t and "protein" in _match_quality([c for c in cols if c != t["time"]])

    if has_header(df.columns):
        return df
    for i in range(min(max_rows, len(df))):
        row = df.iloc[i].tolist()
        if has_header(row):
            out = df.iloc[i + 1 :].copy()
            out.columns = [str(c).strip() for c in row]
            out = out.loc[:, [c for c in out.columns if c and c != "nan"]]
            notes.append(f"table header found on row {i + 2}; rows above it skipped")
            return out.reset_index(drop=True)
    return df


def read_quality(path, fmt: str = "auto", columns: dict | None = None, sheet=None) -> QualityResult:
    """Q1: read an analyzer export (CSV/XLSX/...) into t + percentages.

    Values between 0 and 1 are read as fractions. Time must be absolute
    (ISO text or epoch seconds) so it can be matched to the harvester log.
    """
    df, meta = read_any(Path(path), fmt, sheet=sheet)
    df = _find_table(df, report_rows := [])
    explicit = dict(columns or {})
    # Only the time column comes from the generic recognition; the quality columns
    # (including moisture) are matched by the quality synonyms.
    tm = match_columns(list(df.columns), {k: v for k, v in explicit.items() if k == "time"})
    base = {"time": tm["time"]} if "time" in tm else {}
    qm = _match_quality([c for c in df.columns if c not in base.values()])
    qm.update({k: v for k, v in explicit.items() if k != "time"})
    report = {
        "source": str(path),
        "format": meta.get("format"),
        "rows": len(df),
        "mapping": {"time": base.get("time"), **qm},
        "warnings": list(report_rows),
    }
    if "time" not in base:
        raise ValueError(f"{path}: no time column found; map it with columns: {{time: ...}}")
    if "protein" not in qm:
        raise ValueError(f"{path}: no protein column found; map it with columns: {{protein: ...}}")
    tcol = df[base["time"]]
    if pd.api.types.is_numeric_dtype(tcol):
        t = tcol.to_numpy(float)
        if np.nanmedian(t) < 1e9:
            raise ValueError("quality time must be absolute (epoch or ISO), not relative")
    else:
        dayfirst = bool(tcol.astype(str).str.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}").any())
        t = epoch_seconds(pd.to_datetime(tcol, utc=True, dayfirst=dayfirst, format="mixed"))
    out = pd.DataFrame({"t": t})
    report["measured"], report["empty"] = [], []
    for canon in ("latitude", "longitude"):
        if canon in qm:
            out[canon] = pd.to_numeric(
                df[qm[canon]].astype(str).str.replace(",", ".", regex=False), errors="coerce"
            ).to_numpy(float)
    for canon in MEASURES:
        if canon in qm:
            v = pd.to_numeric(
                df[qm[canon]].astype(str).str.replace(",", ".", regex=False), errors="coerce"
            ).to_numpy(float)
            if not np.isfinite(v).any():
                report["empty"].append(canon)
                report["warnings"].append(f"{canon}: column {qm[canon]!r} has no values")
                continue
            report["measured"].append(canon)
            big = canon in ("protein", "moisture", "carbohydrate", "starch")
            if big and np.nanmedian(v) <= 1.0:
                v = v * 100.0
                report["warnings"].append(f"{canon}: values look like fractions, x100")
            out[f"{canon}_pct"] = v
    if "session" in qm:
        out["session"] = df[qm["session"]].astype(str).to_numpy()
    out = out[np.isfinite(out["t"]) & np.isfinite(out["protein_pct"])]
    out = out.sort_values("t", ignore_index=True)
    report["measurements"] = len(out)
    if len(out) > 1:
        report["median_interval_s"] = float(np.median(np.diff(out["t"])))
    return QualityResult(out, report)


def align_to_records(
    q: pd.DataFrame, rec_t: np.ndarray, delay_s: float, max_gap_s: float
) -> pd.DataFrame:
    """Q2: quality of the grain each record cut.

    Grain cut at record time t reaches the analyzer at t + delay; the nearest
    measurement within ``max_gap_s`` of that moment is used.
    """
    qt = q["t"].to_numpy(float)
    target = np.asarray(rec_t, float) + delay_s
    idx = (
        np.clip(np.searchsorted(qt, target), 1, len(qt) - 1)
        if len(qt) > 1
        else np.zeros(len(target), int)
    )
    left = np.clip(idx - 1, 0, len(qt) - 1)
    pick = np.where(np.abs(qt[left] - target) <= np.abs(qt[idx] - target), left, idx)
    ok = np.abs(qt[pick] - target) <= max_gap_s
    cols = [c for c in q.columns if c.endswith("_pct")]
    out = pd.DataFrame({c: np.where(ok, q[c].to_numpy(float)[pick], np.nan) for c in cols})
    out["q_index"] = np.where(ok, pick, -1)
    return out


def lab_bias(q: pd.DataFrame, lab: pd.DataFrame) -> dict:
    """Q3 calibration: mean (lab - analyzer) over lab samples.

    ``lab`` has protein_lab_pct and either start/end (the load or period the
    sample represents) or time (+- 60 s).
    """
    diffs = []
    for row in lab.itertuples(index=False):
        if hasattr(row, "start") and hasattr(row, "end"):
            a, b = _ts(row.start), _ts(row.end)
        else:
            a, b = _ts(row.time) - 60, _ts(row.time) + 60
        sel = (q["t"] >= a) & (q["t"] <= b)
        if sel.sum() >= 1:
            diffs.append(float(row.protein_lab_pct) - float(q.loc[sel, "protein_pct"].mean()))
    if not diffs:
        return {"applied": False, "reason": "no analyzer measurements inside lab sample windows"}
    d = np.asarray(diffs)
    return {
        "applied": True,
        "samples": len(d),
        "bias_pct_points": float(d.mean()),
        "sd_pct_points": float(d.std(ddof=1)) if len(d) > 1 else None,
    }


def _ts(v) -> float:
    if isinstance(v, int | float):
        return float(v)
    return (
        pd.Timestamp(v).tz_localize("UTC").timestamp()
        if pd.Timestamp(v).tzinfo is None
        else pd.Timestamp(v).timestamp()
    )


def grid_quality(
    view_cell: np.ndarray,
    mass: np.ndarray,
    clean: np.ndarray,
    protein_pct: np.ndarray,
    grid: Grid,
    yield_t_ha: np.ndarray,
    reference_moisture_pct: float,
    protein_basis: str = "dry",
    moisture_pct: np.ndarray | None = None,
    n_factor: float = 6.25,
) -> dict:
    """Q4: mass-weighted protein per cell, protein yield and N uptake.

    protein yield [kg/ha] = yield (t/ha at MC_ref) x 1000 x (1 - MC_ref/100) x protein_dry/100
    N uptake in grain [kg/ha] = protein yield / n_factor (e.g. wheat 5.7, others 6.25)
    """
    n = grid.ny * grid.nx
    p = np.asarray(protein_pct, float)
    if protein_basis == "wet":
        if moisture_pct is None:
            raise ValueError("wet-basis protein needs moisture to convert to dry basis")
        p = p / (1 - np.asarray(moisture_pct, float) / 100.0)
    elif protein_basis != "dry":
        raise ValueError("protein_basis must be dry or wet")
    has = clean & np.isfinite(p) & (view_cell >= 0)
    c = view_cell[has]
    m = mass[has]
    wsum = np.bincount(c, m, minlength=n)
    psum = np.bincount(c, m * p[has], minlength=n)
    all_mass = np.bincount(
        view_cell[clean & (view_cell >= 0)], mass[clean & (view_cell >= 0)], minlength=n
    )
    count = np.bincount(c, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        prot = np.where(wsum > 0, psum / wsum, np.nan)
        share = np.where(all_mass > 0, wsum / all_mass, np.nan)
    shape = (grid.ny, grid.nx)
    prot = prot.reshape(shape)
    y = np.asarray(yield_t_ha, float)
    prot_yield = y * 1000.0 * (1 - reference_moisture_pct / 100.0) * prot / 100.0
    return {
        "protein_pct_dm": prot,
        "protein_kg_ha": prot_yield,
        "n_uptake_kg_ha": prot_yield / n_factor,
        "quality_mass_share": share.reshape(shape),
        "n_records": count.reshape(shape).astype(float),
    }


Q4_BANDS = ("protein_pct_dm", "protein_kg_ha", "n_uptake_kg_ha", "quality_mass_share", "n_records")


def run_quality(config, state: dict) -> dict:
    """Q1-Q4 for every configured source, after L4. Writes Q4 GeoTIFF and QA."""
    from ..l5.records import RecordView, cell_index

    qc = config.quality
    rec = state["records"]
    view = RecordView.from_records(rec)
    cell = cell_index(state["grid"], view.cx, view.cy)
    protein = np.full(len(rec), np.nan)
    moisture = np.full(len(rec), np.nan)
    reports = []
    all_q = []
    extra: dict[str, np.ndarray] = {}
    for src in qc.sources:
        from .moisture import clean_readings

        res = read_quality(src.path, src.format, src.columns, src.sheet)
        q, cleaned = clean_readings(res.measurements, need_moisture=False)
        res.report["failed_scans_dropped"] = cleaned["dropped"]
        machine = src.machine_id
        prof = config.machines.get(machine)
        delay = (
            src.delay_s
            if src.delay_s is not None
            else (
                prof.tau_d_s
                if prof and prof.tau_d_s is not None
                else state["qa"].get("delays", {}).get(machine, {}).get("tau_d_s", 0.0)
            )
        )
        interval = res.report.get("median_interval_s", 5.0)
        max_gap = src.max_gap_s if src.max_gap_s is not None else 0.75 * interval + 1.0
        sel = (rec["machine_id"] == machine).to_numpy()
        aligned = align_to_records(q, rec["t"].to_numpy()[sel], delay, max_gap)
        protein[sel] = aligned["protein_pct"].to_numpy()
        if "moisture_pct" in aligned:
            moisture[sel] = aligned["moisture_pct"].to_numpy()
        for col in aligned.columns:
            if col.endswith("_pct") and col not in ("protein_pct", "moisture_pct"):
                extra.setdefault(col, np.full(len(rec), np.nan))[sel] = aligned[col].to_numpy()
        res.report["in_field_share"] = _in_field_share(config, q)
        if res.report["in_field_share"] is not None and res.report["in_field_share"] < 0.5:
            res.report["warnings"].append(
                f"only {100 * res.report['in_field_share']:.0f} % of the analyzer positions are "
                "on this field: is the session (sheet) the right one?"
            )
        res.report.update(
            machine_id=machine,
            delay_s=delay,
            max_gap_s=max_gap,
            records_with_quality=int(np.isfinite(aligned["protein_pct"]).sum()),
        )
        reports.append(res.report)
        all_q.append(q)

    # Lab samples are taken to be on the analyzer's basis; calibrate first, then
    # convert everything to dry matter once.
    lab = {"applied": False, "reason": "no lab samples configured"}
    if qc.lab_samples:
        lab = lab_bias(pd.concat(all_q, ignore_index=True), pd.read_csv(qc.lab_samples))
        if lab.get("applied"):
            protein = protein + lab["bias_pct_points"]
    if qc.protein_basis == "wet":
        if np.isnan(moisture).all():
            raise ValueError("wet-basis protein needs the analyzer's moisture column")
        protein = protein / (1 - moisture / 100.0)
    elif qc.protein_basis != "dry":
        raise ValueError("quality.protein_basis must be dry or wet")

    # Q3: flags
    usable = view.clean & np.isfinite(protein)
    out_flag = np.zeros(len(rec), bool)
    if usable.sum() > 10:
        out_flag[usable] = flag_global_outliers(protein[usable])
    clean = view.clean & ~out_flag

    bands = grid_quality(
        cell,
        view.mass,
        clean,
        protein,
        state["grid"],
        state["l4"]["yield"],
        config.reference_moisture_pct,
        qc.protein_basis,
        moisture,
        qc.n_factor,
    )
    # analyzer moisture and the other measured quantities, mass-weighted per cell
    names = list(Q4_BANDS)
    for col, vals in [("moisture_pct", moisture), *sorted(extra.items())]:
        if not np.isfinite(vals).any():
            continue
        key = "moisture_analyzer_pct" if col == "moisture_pct" else col.replace("_pct", "_pct_dm")
        if col == "nitrogen_pct":
            continue  # GrainSense N % is protein / 6.25: shown as N uptake already
        bands[key] = _cell_mean(cell, view.mass, clean & np.isfinite(vals), vals, state["grid"])
        names.append(key)
    qa = {
        "sources": reports,
        "measured": sorted({m for r in reports for m in r.get("measured", [])}),
        "not_measured": sorted({m for r in reports for m in r.get("empty", [])}),
        "protein_basis": qc.protein_basis,
        "n_factor": qc.n_factor,
        "lab_calibration": lab,
        "outliers_flagged": int(out_flag.sum()),
        "field_mean_protein_pct_dm": _wmean(protein, view.mass, clean),
        "product_class": "absolute" if lab.get("applied") else "relative",
    }
    if not lab.get("applied"):
        qa["note"] = (
            "not calibrated to lab samples: protein levels are the analyzer's own; "
            "patterns are usable, absolute values unverified"
        )
    out = Path(config.output_dir)
    stem = f"Q4_{config.field_id}_{config.year}"
    files = {}
    try:
        from ..l4.io import write_cog

        files["Q4"] = str(
            write_cog(bands, state["grid"], config.crs, out / f"{stem}.tif", names=tuple(names))
        )
    except ImportError:
        qa.setdefault("warnings", []).append("rasterio not installed: Q4 GeoTIFF not written")
    p = out / f"{stem}_qa.json"
    p.write_text(json.dumps(qa, indent=2, default=str))
    files["Q4_qa"] = str(p)
    state["quality"] = {"bands": bands, "qa": qa}
    state.setdefault("outputs", {}).update(files)
    return qa


def _wmean(v, w, mask):
    ok = mask & np.isfinite(v)
    return float(np.average(v[ok], weights=w[ok])) if ok.any() else None


def _cell_mean(cell, mass, ok, v, grid):
    n = grid.ny * grid.nx
    ok = ok & (cell >= 0)
    den = np.bincount(cell[ok], mass[ok], minlength=n)
    num = np.bincount(cell[ok], mass[ok] * v[ok], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan).reshape(grid.ny, grid.nx)


def _in_field_share(config, q: pd.DataFrame) -> float | None:
    """Share of analyzer positions (if the export has them) within 20 m of the field."""
    if "latitude" not in q or "longitude" not in q or config.boundary is None:
        return None
    try:
        import geopandas as gpd

        from ..public.boundary import load_boundary

        b = load_boundary(config.boundary, config.crs).buffer(20)
        ok = np.isfinite(q["latitude"]) & np.isfinite(q["longitude"])
        if not ok.any():
            return None
        pts = gpd.GeoSeries(
            gpd.points_from_xy(q.loc[ok, "longitude"], q.loc[ok, "latitude"]), crs=4326
        ).to_crs(config.crs)
        return float(pts.within(b).mean())
    except (ImportError, OSError, ValueError):
        return None
