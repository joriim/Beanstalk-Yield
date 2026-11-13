"""Sentinel-2 L2A and Landsat 8/9 Collection 2 L2 scenes read onto the field grid.

Source: Microsoft Planetary Computer (STAC search, free SAS tokens, Cloud-
Optimised GeoTIFFs). Only the window around the field is read from each COG.
Per scene and grid cell the result holds red, NIR, SWIR1 and SWIR2 surface
reflectance and the clear share (cloud, shadow and snow masked):

- Sentinel-2: SCL classes 4 (vegetation) and 5 (bare soil) count as clear;
  digital numbers from processing baseline 04.00 on carry a +1000 offset.
- Landsat 8/9: QA_PIXEL clear bit (6) set and no dilated cloud, cirrus, cloud,
  shadow or snow bit; reflectance = DN x 2.75e-5 - 0.2. Landsat 7 is left out
  (scan-line gaps).

Licences: Copernicus Sentinel data (free, attribution "contains modified
Copernicus Sentinel data"); Landsat courtesy of the U.S. Geological Survey
(public domain).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from . import http

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
LICENCE = {
    "S2": "Contains modified Copernicus Sentinel data (Sentinel-2 L2A, Planetary Computer)",
    "LS": "Landsat 8/9 Collection 2 L2 courtesy of the U.S. Geological Survey",
    "PS": "PlanetScope © Planet Labs PBC; not to be passed on outside the organisation",
}
FAMILY = {"sentinel-2-l2a": "S2", "landsat-c2-l2": "LS"}
RES_M = {"S2": 20.0, "LS": 30.0, "PS": 3.0}  # resolution of the coarsest band used
ASSETS = {
    "S2": {"red": "B04", "nir": "B08", "swir1": "B11", "swir2": "B12", "mask": "SCL"},
    "LS": {"red": "red", "nir": "nir08", "swir1": "swir16", "swir2": "swir22", "mask": "qa_pixel"},
}


@dataclass
class Scene:
    family: str  # S2, LS or PS
    date: str
    item_id: str
    platform: str
    bands: dict = field(default_factory=dict)  # name -> (ny, nx) reflectance, NaN where unclear
    clear: np.ndarray | None = None  # (ny, nx) clear share 0..1
    res_m: float = 20.0

    @property
    def clear_share(self) -> float:
        return float(np.nanmean(self.clear)) if self.clear is not None else 0.0


def gdal_env() -> dict:
    env = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MAX_RETRY": "3",
        "GDAL_HTTP_RETRY_DELAY": "2",
    }
    ca = os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not ca and os.path.exists("/root/.ccr/ca-bundle.crt"):
        ca = "/root/.ccr/ca-bundle.crt"
    if ca:
        env["CURL_CA_BUNDLE"] = ca
    return env


def _get_json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": http.USER_AGENT, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - fixed host
        return json.loads(r.read())


def search(
    bbox_wgs84,
    start: date,
    end: date,
    families=("S2", "LS"),
    max_cloud: float = 80.0,
    get=_get_json,
) -> list[dict]:
    """STAC items over the bbox in [start, end], oldest first."""
    cols = [c for c, f in FAMILY.items() if f in families]
    out = []
    for col in cols:
        body = {
            "collections": [col],
            "bbox": list(bbox_wgs84),
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "query": {"eo:cloud_cover": {"lt": max_cloud}},
            "limit": 200,
        }
        if col == "landsat-c2-l2":
            body["query"]["platform"] = {"in": ["landsat-8", "landsat-9"]}
        page = get(STAC, body)
        for f in page.get("features", []):
            f["_family"] = FAMILY[col]
            f["_collection"] = col
            out.append(f)
    return sorted(out, key=lambda f: f["properties"]["datetime"])


class _Signer:
    def __init__(self, get=_get_json):
        self.get, self.tokens = get, {}

    def __call__(self, href: str, collection: str) -> str:
        if collection not in self.tokens:
            self.tokens[collection] = self.get(SAS.format(collection=collection))["token"]
        return href + ("&" if "?" in href else "?") + self.tokens[collection]


def _read_to_grid(url: str, grid, crs: str, resampling: str = "average", nodata=None):
    """Read the field window of a COG and resample it onto ``grid``."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.warp import reproject, transform_bounds
    from rasterio.windows import from_bounds

    x0, y1 = grid.xmin, grid.ymax
    x1, y0 = x0 + grid.nx * grid.resolution, y1 - grid.ny * grid.resolution
    with rasterio.open(url) as src:
        b = transform_bounds(crs, src.crs, x0, y0, x1, y1)
        px = abs(src.res[0])
        w = from_bounds(
            b[0] - 2 * px, b[1] - 2 * px, b[2] + 2 * px, b[3] + 2 * px, transform=src.transform
        ).round_offsets().round_lengths()
        a = src.read(1, window=w, boundless=True, fill_value=src.nodata or 0).astype("float64")
        t = src.window_transform(w)
        s_crs = src.crs
        nd = src.nodata if nodata is None else nodata
    if nd is not None:
        a[a == nd] = np.nan
    dst = np.full((grid.ny, grid.nx), np.nan)
    reproject(
        a,
        dst,
        src_transform=t,
        src_crs=s_crs,
        src_nodata=np.nan,
        dst_transform=Affine(grid.resolution, 0, x0, 0, -grid.resolution, y1),
        dst_crs=crs,
        dst_nodata=np.nan,
        resampling=getattr(Resampling, resampling),
    )
    return dst


def _clear_from_mask(family: str, m: np.ndarray) -> np.ndarray:
    m = np.nan_to_num(m, nan=0).astype(np.int64)
    if family == "S2":
        return np.isin(m, (4, 5)).astype(float)
    bad = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
    return (((m >> 6) & 1) == 1) & ((m & bad) == 0)


def read_scene(item: dict, grid, crs: str, sign=None, min_clear: float = 0.99) -> Scene:
    """One STAC item on the grid; cells with a clear share under ``min_clear`` are NaN."""
    import rasterio

    fam, col = item["_family"], item["_collection"]
    sign = sign or _Signer()
    names = ASSETS[fam]
    p = item["properties"]
    with rasterio.Env(**gdal_env()):
        # mask first, at its native values: nearest per source pixel, then average the
        # clear flag so a cell is clear only if every source pixel in it is
        raw = _read_to_grid(sign(item["assets"][names["mask"]]["href"], col), grid, crs,
                            resampling="nearest", nodata=-1)
        clear = _clear_from_mask(fam, raw).astype(float)
        clear[~np.isfinite(raw)] = np.nan
        scene = Scene(fam, p["datetime"][:10], item["id"], p.get("platform", ""), {}, clear,
                      RES_M[fam])
        if not np.nansum(clear >= min_clear):
            return scene
        if fam == "S2":
            base = float(p.get("s2:processing_baseline") or 0)
            offset = 1000.0 if base >= 4.0 else 0.0
        for band in ("red", "nir", "swir1", "swir2"):
            v = _read_to_grid(sign(item["assets"][names[band]]["href"], col), grid, crs, nodata=0)
            v = (v - offset) / 1e4 if fam == "S2" else v * 2.75e-5 - 0.2
            scene.bands[band] = np.where(clear >= min_clear, v, np.nan)
    return scene


def fetch_scenes(
    bbox_wgs84,
    grid,
    crs: str,
    start: date,
    end: date,
    families=("S2", "LS"),
    max_cloud: float = 80.0,
    min_field_clear: float = 0.5,
    field_mask: np.ndarray | None = None,
    get=_get_json,
    log=None,
) -> list[Scene]:
    """Scenes with at least ``min_field_clear`` of the field clear, one per family and day."""
    items = search(bbox_wgs84, start, end, families, max_cloud, get)
    sign = _Signer(get)
    seen, out = set(), []
    for it in items:
        key = (it["_family"], it["properties"]["datetime"][:10])
        if key in seen:
            continue
        try:
            sc = read_scene(it, grid, crs, sign)
        except Exception as exc:  # noqa: BLE001 - one unreadable scene must not stop the check
            if log:
                log(f"{it['id']}: {exc}")
            continue
        m = field_mask if field_mask is not None else np.ones((grid.ny, grid.nx), bool)
        share = float(np.nanmean(np.where(m, sc.clear >= 0.99, np.nan)))
        if sc.bands and share >= min_field_clear:
            seen.add(key)
            out.append(sc)
    return out


def planetscope_scene(path, date_str: str, grid, crs: str, mask_path=None) -> Scene:
    """A downloaded PlanetScope GeoTIFF (4- or 8-band SR) on the grid: red and NIR only."""
    import rasterio

    with rasterio.open(path) as s:
        n = s.count
    red_b, nir_b = (6, 8) if n >= 8 else (3, 4)

    def band(i):
        import rasterio as rio
        from rasterio.enums import Resampling
        from rasterio.transform import Affine
        from rasterio.warp import reproject

        with rio.open(path) as s:
            a = s.read(i).astype("float64")
            a[a <= 0] = np.nan
            dst = np.full((grid.ny, grid.nx), np.nan)
            reproject(a, dst, src_transform=s.transform, src_crs=s.crs, src_nodata=np.nan,
                      dst_transform=Affine(grid.resolution, 0, grid.xmin, 0, -grid.resolution,
                                           grid.ymax),
                      dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.average)
        return dst / 1e4

    clear = np.ones((grid.ny, grid.nx))
    if mask_path:
        clear = _read_to_grid(str(mask_path), grid, crs, resampling="average")
    red, nir = band(red_b), band(nir_b)
    ok = (clear >= 0.99) & np.isfinite(red) & np.isfinite(nir)
    return Scene("PS", date_str, str(path), "PlanetScope",
                 {"red": np.where(ok, red, np.nan), "nir": np.where(ok, nir, np.nan)},
                 clear, RES_M["PS"])
