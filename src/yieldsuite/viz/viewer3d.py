"""3D harvest viewer: several maps (yield, moisture, protein, oil ...) side by side on the
terrain, with the combine and its header animated over the harvest. One self-contained
HTML file (three.js r147, MIT, embedded; the data is gzip-compressed inside), so it opens
offline and is not uploaded anywhere.

The viewer lets the user choose which maps are shown, lay them out in a row, a grid or a
stack, focus on one (double click, or its button) and zoom out to see them all. A layer that
was not measured is listed with the reason. Coordinates are written relative to the
south-west corner of the view (metres), not as map coordinates.
"""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

import numpy as np

from .timelapse import LABEL, LAYER_ORDER, PALETTE, Track, choose_layers, colour_range

TEMPLATE_DIR = Path(__file__).parent
VENDOR = TEMPLATE_DIR / "vendor"
TEMPLATE = TEMPLATE_DIR / "viewer3d.html"


def _terrain(dem_path, x0, y0, x1, y1, step: float):
    """Heights on a regular grid (row 0 = south) over the view; flat if no DEM."""
    nx = int(np.ceil((x1 - x0) / step)) + 1
    ny = int(np.ceil((y1 - y0) / step)) + 1
    if not dem_path or not Path(dem_path).exists():
        return np.zeros((ny, nx)), None
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.warp import reproject

    with rasterio.open(dem_path) as src:
        dst = np.full((ny, nx), np.nan)
        # north-up target, then flipped so row 0 is the south edge
        reproject(
            src.read(1).astype("float64"),
            dst,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=Affine(step, 0, x0 - step / 2, 0, -step, y0 + (ny - 0.5) * step),
            dst_crs=src.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    dst = dst[::-1]
    if np.isnan(dst).all():
        return np.zeros((ny, nx)), None
    dst = np.where(np.isfinite(dst), dst, np.nanmedian(dst))
    prov = Path(dem_path).with_suffix(".provenance.json")
    attribution = json.loads(prov.read_text()).get("attribution") if prov.exists() else "DEM"
    return dst, attribution


def pack(data: dict) -> str:
    """JSON -> gzip -> base64 (the viewer unpacks it with DecompressionStream)."""
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, 9)).decode("ascii")


def build_html(
    track: Track,
    path: str | Path,
    boundary=None,
    dem_path=None,
    title: str = "",
    lang: str = "fi",
    terrain_step_m: float = 4.0,
    layers: list[str] | None = None,
    layout: str = "row",
) -> Path:
    """``layers``: the maps shown at first (default: every available one of yield, moisture,
    protein and oil). All other available layers are still in the file and can be ticked."""
    fi = lang == "fi"
    ok = np.isfinite(track.x) & np.isfinite(track.y)
    pad = 30.0
    x0, x1 = np.nanmin(track.x[ok]) - pad, np.nanmax(track.x[ok]) + pad
    y0, y1 = np.nanmin(track.y[ok]) - pad, np.nanmax(track.y[ok]) + pad
    if boundary is not None:
        bx0, by0, bx1, by1 = boundary.total_bounds
        x0, y0 = min(x0, bx0 - 15), min(y0, by0 - 15)
        x1, y1 = max(x1, bx1 + 15), max(y1, by1 + 15)
    h, attribution = _terrain(dem_path, x0, y0, x1, y1, terrain_step_m)
    nyh, nxh = h.shape
    base = float(np.nanmin(h))

    def r(v, d=2):
        return [None if not np.isfinite(a) else round(float(a), d) for a in v]

    polys = []
    for p in track.polygons:
        if p is None or p.is_empty:
            polys.append(None)
            continue
        c = np.asarray(p.exterior.coords)[:4]
        polys.append([round(float(v), 1) for xy in c for v in (xy[0] - x0, xy[1] - y0)])
    shown = choose_layers(track, layers)
    out_layers = {}
    for key in LAYER_ORDER:
        lay = track.layers.get(key)
        if lay is None:
            continue
        entry = {
            "label": lay.label(lang),
            "palette": key,
            "available": bool(lay.available),
            "reason": (lay.reason if fi else lay.reason_en or lay.reason),
        }
        if lay.available:
            entry["values"] = r(lay.values, 2)
            entry["range"] = [round(v, 3) for v in colour_range(lay.values)]
        out_layers[key] = entry
    visible = [k for k in shown if out_layers.get(k, {}).get("available")]
    ring = []
    if boundary is not None:
        from shapely.geometry import MultiPolygon

        g = boundary.union_all() if hasattr(boundary, "union_all") else boundary.unary_union
        parts = g.geoms if isinstance(g, MultiPolygon) else [g]
        for part in parts:
            for rr in [part.exterior, *part.interiors]:
                ring.append([round(float(v), 1) for xy in rr.coords
                             for v in (xy[0] - x0, xy[1] - y0)])
    machines = sorted(set(track.machine))
    data = {
        "title": title,
        "lang": lang,
        "size": [round(x1 - x0, 1), round(y1 - y0, 1)],
        "terrain": {"nx": nxh, "ny": nyh, "step": terrain_step_m, "base": round(base, 2),
                    "h": [round(float(v) - base, 1) for v in h.ravel()],
                    "attribution": attribution},
        "t": [round(float(v), 1) for v in track.t],
        "play": [round(float(v), 2) for v in track.play],
        "machine": [machines.index(m) for m in track.machine],
        "machines": machines,
        "x": r(track.x - x0, 1),
        "y": r(track.y - y0, 1),
        "heading": r(track.heading, 3),
        "down": [int(v) for v in track.header_down],
        "cut": [int(v) for v in (track.cut if track.cut is not None else track.header_down)],
        "swath": r(track.swath, 2),
        "poly": polys,
        "layers": out_layers,
        "boundary": ring,
        "order": [k for k in LAYER_ORDER if k in out_layers],
        "visible": visible,
        "layout": layout,
        "palettes": PALETTE,
        "labels": LABEL[lang],
    }
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*THREE*/", (VENDOR / "three.min.js").read_text(encoding="utf-8"))
    html = html.replace("/*ORBIT*/", (VENDOR / "OrbitControls.js").read_text(encoding="utf-8"))
    common = (Path(__file__).parent / "common3d.js").read_text(encoding="utf-8")
    html = html.replace("/*COMMON*/", common)
    html = html.replace("/*DATA*/", pack(data))
    html = html.replace("__TITLE__", title or ("Puinti 3D" if fi else "Harvest 3D"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
