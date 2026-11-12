"""3D harvest viewer: the yield or quality map as a layer on the terrain, with the
combine and its header animated over the harvest. One self-contained HTML file
(three.js r147, MIT, embedded), so it opens offline and is not uploaded anywhere.

Coordinates are written relative to the south-west corner of the view (metres),
not as map coordinates.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .timelapse import QUALITY_LABEL, Track, colour_range

VENDOR = Path(__file__).parent / "vendor"
TEMPLATE = Path(__file__).parent / "viewer3d.html"


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
    return dst, json.loads(Path(dem_path).with_suffix(".provenance.json").read_text()).get(
        "attribution") if Path(dem_path).with_suffix(".provenance.json").exists() else "DEM"


def build_html(
    track: Track,
    path: str | Path,
    boundary=None,
    dem_path=None,
    title: str = "",
    lang: str = "fi",
    terrain_step_m: float = 4.0,
) -> Path:
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
        polys.append([round(float(v), 2) for xy in c for v in (xy[0] - x0, xy[1] - y0)])
    layers = {"yield": {"label": "sato (t/ha)" if fi else "yield (t/ha)",
                        "values": r(track.yield_t_ha), "range": colour_range(track.yield_t_ha),
                        "palette": "yield"}}
    if track.quality is not None:
        layers["quality"] = {
            "label": QUALITY_LABEL[lang].get(track.quality_name, track.quality_name),
            "values": r(track.quality),
            "range": colour_range(track.quality),
            "palette": "protein" if track.quality_name == "protein" else "moisture",
        }
    ring = []
    if boundary is not None:
        from shapely.geometry import MultiPolygon

        g = boundary.union_all() if hasattr(boundary, "union_all") else boundary.unary_union
        parts = g.geoms if isinstance(g, MultiPolygon) else [g]
        for part in parts:
            for rr in [part.exterior, *part.interiors]:
                ring.append([round(float(v), 2) for xy in rr.coords
                             for v in (xy[0] - x0, xy[1] - y0)])
    machines = sorted(set(track.machine))
    data = {
        "title": title,
        "lang": lang,
        "size": [round(x1 - x0, 1), round(y1 - y0, 1)],
        "terrain": {"nx": nxh, "ny": nyh, "step": terrain_step_m, "base": round(base, 2),
                    "h": [round(float(v) - base, 2) for v in h.ravel()],
                    "attribution": attribution},
        "t": [round(float(v), 1) for v in track.t],
        "play": [round(float(v), 2) for v in track.play],
        "machine": [machines.index(m) for m in track.machine],
        "machines": machines,
        "x": r(track.x - x0),
        "y": r(track.y - y0),
        "heading": r(track.heading, 3),
        "down": [int(v) for v in track.header_down],
        "cut": [int(v) for v in (track.cut if track.cut is not None else track.header_down)],
        "swath": r(track.swath, 2),
        "poly": polys,
        "layers": layers,
        "boundary": ring,
    }
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("/*THREE*/", (VENDOR / "three.min.js").read_text(encoding="utf-8"))
    html = html.replace("/*ORBIT*/", (VENDOR / "OrbitControls.js").read_text(encoding="utf-8"))
    html = html.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    html = html.replace("__TITLE__", title or ("Puinti 3D" if fi else "Harvest 3D"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
