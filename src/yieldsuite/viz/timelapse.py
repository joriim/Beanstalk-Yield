"""Harvest timelapse data: records in time order with the combine and header state.

Every L3 record is one piece of cut area (record polygon) with its time, the
heading, the header state (lowered = harvesting, raised = turning or driving),
the swath and one value per *layer*: yield, grain moisture and, when an analyzer
ran, protein, oil, carbohydrate and starch of the grain each record cut. A
layer that cannot be shown (no analyzer on the field, the analyzer does not
measure it) is kept in the list with the reason, so a viewer can say why. Idle
time between passes and sessions is compressed so the playback shows driving,
not waiting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

GAP_CAP_S = 6.0  # a pause longer than this plays as this long

LAYER_ORDER = ("yield", "moisture", "protein", "oil", "carbohydrate", "starch")
DEFAULT_LAYERS = ("yield", "moisture", "protein", "oil")
LABEL = {
    "fi": {
        "yield": "sato (t/ha)",
        "moisture": "kosteus (%)",
        "protein": "proteiini (% ka)",
        "oil": "öljy (% ka)",
        "carbohydrate": "hiilihydraatti (% ka)",
        "starch": "tärkkelys (% ka)",
    },
    "en": {
        "yield": "yield (t/ha)",
        "moisture": "moisture (%)",
        "protein": "protein (% DM)",
        "oil": "oil (% DM)",
        "carbohydrate": "carbohydrate (% DM)",
        "starch": "starch (% DM)",
    },
}
PALETTE = {  # matplotlib colormap per layer; viewer3d.html has the same palettes
    "yield": "RdYlGn",
    "moisture": "YlGnBu",
    "protein": "viridis",
    "oil": "YlOrBr",
    "carbohydrate": "cividis",
    "starch": "PuBuGn",
}
ANALYZER_COLUMN = {
    "protein": "protein_pct",
    "oil": "oil_pct",
    "carbohydrate": "carbohydrate_pct",
    "starch": "starch_pct",
}


@dataclass
class Layer:
    key: str
    values: np.ndarray | None  # per record, NaN where the record has no value
    available: bool = True
    reason: str = ""  # why not available (fi)
    reason_en: str = ""

    def label(self, lang: str = "fi") -> str:
        return LABEL[lang][self.key]


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
    layers: dict[str, Layer] = field(default_factory=dict)
    cut: np.ndarray | None = None  # header down and cutting (also flagged records)
    extra: dict = field(default_factory=dict)

    @property
    def yield_t_ha(self) -> np.ndarray:
        return self.layers["yield"].values

    def available(self) -> list[str]:
        return [k for k in LAYER_ORDER if k in self.layers and self.layers[k].available]


def parse_layer_list(text: str | None) -> list[str] | None:
    """``"yield,moisture,protein"`` -> keys (Finnish/English names accepted)."""
    if not text:
        return None
    alias = {
        "sato": "yield",
        "kosteus": "moisture",
        "proteiini": "protein",
        "oljy": "oil",
        "öljy": "oil",
        "hiilihydraatti": "carbohydrate",
        "tarkkelys": "starch",
        "tärkkelys": "starch",
    }
    out = []
    for part in text.split(","):
        k = alias.get(part.strip().lower(), part.strip().lower())
        if k not in LAYER_ORDER:
            raise ValueError(f"unknown layer {part!r}; choose from {', '.join(LAYER_ORDER)}")
        if k not in out:
            out.append(k)
    return out


def _analyzer_layers(config, rec: pd.DataFrame, clean: np.ndarray) -> dict[str, Layer]:
    """Protein, oil, carbohydrate and starch of the grain each record cut."""
    layers: dict[str, Layer] = {}
    qc = config.quality
    if qc is None or not qc.sources:
        for k in ANALYZER_COLUMN:
            layers[k] = Layer(
                k, None, False,
                "ei analysaattoridataa (GrainSense) tällä lohkolla",
                "no analyzer data (GrainSense) on this field",
            )
        return layers
    from ..quality.moisture import clean_readings
    from ..quality.pipeline import add_xy, align_to_records, read_quality

    vals = {k: np.full(len(rec), np.nan) for k in ANALYZER_COLUMN}
    empty: set[str] = set()
    mapped: set[str] = set()
    for src in qc.sources:
        res = read_quality(src.path, src.format, src.columns, src.sheet)
        empty |= set(res.report.get("empty", []))
        mapped |= set(res.report.get("mapping", {}))
        q, _ = clean_readings(res.measurements, need_moisture=False)
        if not len(q):
            continue
        sel = (rec["machine_id"] == src.machine_id).to_numpy()
        prof = config.machines.get(src.machine_id)
        delay = src.delay_s if src.delay_s is not None else (
            prof.tau_d_s if prof and prof.tau_d_s is not None else 0.0)
        gap = src.max_gap_s if src.max_gap_s is not None else (
            0.75 * res.report.get("median_interval_s", 5.0) + 1.0)
        al = align_to_records(
            add_xy(q, config.crs),
            rec["t"].to_numpy(float)[sel],
            delay,
            gap,
            (rec["x"].to_numpy(float)[sel], rec["y"].to_numpy(float)[sel]),
            src.max_dist_m,
        )
        for k, col in ANALYZER_COLUMN.items():
            if col not in al:
                continue
            v = al[col].to_numpy()
            if k == "protein" and qc.protein_basis == "wet" and "moisture_pct" in al:
                v = v / (1 - al["moisture_pct"].to_numpy() / 100)
            cur = vals[k][sel]
            vals[k][sel] = np.where(np.isfinite(cur), cur, v)
    for k in ANALYZER_COLUMN:
        v = np.where(clean, vals[k], np.nan)
        if np.isfinite(v).any():
            layers[k] = Layer(k, v)
        elif k in empty:
            layers[k] = Layer(
                k, None, False,
                "ei mitattu: analysaattorin vientitiedoston sarake on tyhjä (laite ei mittaa sitä "
                "tällä kalibroinnilla)",
                "not measured: the analyzer export column is empty (the device does not measure "
                "it with this calibration)",
            )
        elif k not in mapped:
            layers[k] = Layer(
                k, None, False,
                "analysaattorin viennissä ei ole tätä saraketta",
                "the analyzer export has no such column",
            )
        else:
            layers[k] = Layer(
                k, None, False,
                "analysaattorin mittaukset eivät osu tämän lohkon tietueisiin",
                "the analyzer readings do not match the records of this field",
            )
    return layers


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
    layers: dict[str, Layer] = {
        "yield": Layer("yield", np.where(clean, rec["yield"].to_numpy(float), np.nan))
    }
    mc = rec["moisture_aligned"].to_numpy(float) if "moisture_aligned" in rec else None
    if mc is not None and np.isfinite(mc[clean]).sum() > 50:
        layers["moisture"] = Layer("moisture", np.where(clean, mc, np.nan))
    else:
        layers["moisture"] = Layer("moisture", None, False, "kosteutta ei ole kirjattu",
                                   "no moisture was logged")
    layers.update(_analyzer_layers(config, rec, clean))
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
        layers=layers,
        cut=rec["header_down"].to_numpy(bool) & (swath_cut > 0),
    )


def colour_range(v: np.ndarray, lo_p: float = 2, hi_p: float = 98) -> tuple[float, float]:
    v = v[np.isfinite(v)]
    if not v.size:
        return 0.0, 1.0
    lo, hi = np.percentile(v, [lo_p, hi_p])
    return (float(lo), float(hi)) if hi > lo else (float(lo) - 0.5, float(hi) + 0.5)


def choose_layers(track: Track, wanted: list[str] | None) -> list[str]:
    """Keys to show: the requested ones (unavailable included, so a viewer can explain),
    else every available layer among yield, moisture, protein and oil."""
    if wanted:
        return list(wanted)
    return [k for k in DEFAULT_LAYERS if track.layers[k].available]
