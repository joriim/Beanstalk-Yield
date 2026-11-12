"""NLS 2 m elevation model from Paituli (CSC) without an API key.

Paituli's STAC catalogue lists the NLS ``korkeusmalli 2 m`` map sheets, served
as GeoTIFFs from ``www.nic.funet.fi``; only the window around the field is read
(HTTP range requests). Licence: CC BY 4.0, "Contains data from the National
Land Survey of Finland".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import http

STAC = "https://paituli.csc.fi/geoserver/ogc/stac/v1/search"
COLLECTION = "nls_digital_elevation_model_2m_at_paituli"
ATTRIBUTION = "Contains data from the National Land Survey of Finland (CC BY 4.0), via Paituli/CSC"


def tile_urls(bbox_3067, post=None) -> list[str]:
    """GeoTIFF URLs of the newest version of every sheet that touches the bbox."""
    from pyproj import Transformer

    post = post or http.post_json
    tr = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
    x0, y0, x1, y1 = bbox_3067
    lon0, lat0 = tr.transform(x0, y0)
    lon1, lat1 = tr.transform(x1, y1)
    page = post(STAC, {"collections": [COLLECTION], "bbox": [lon0, lat0, lon1, lat1],
                       "limit": 50})
    newest: dict[str, tuple[str, str]] = {}
    for f in page.get("features", []):
        href = next((a["href"] for k, a in f["assets"].items()
                     if a.get("href", "").startswith("http")), None)
        if not href:
            continue
        when = f["properties"].get("datetime") or ""
        if href not in newest or when > newest[href][0]:
            newest[href] = (when, href)
    return sorted(newest)


def fetch(bbox_3067, out: str | Path, post=None) -> dict:
    """Mosaic the DEM over ``bbox_3067`` (EPSG:3067, metres) into ``out``."""
    import rasterio
    from rasterio.merge import merge

    from .optical import gdal_env

    urls = tile_urls(bbox_3067, post)
    if not urls:
        raise RuntimeError("no NLS 2 m DEM sheet found for the area (Paituli STAC)")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(**gdal_env()):
        srcs = [rasterio.open(u) for u in urls]
        try:
            arr, transform = merge(srcs, bounds=bbox_3067, nodata=-9999.0)
            meta = srcs[0].meta.copy()
        finally:
            for s in srcs:
                s.close()
    meta.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2], transform=transform,
                count=1, dtype="float32", nodata=-9999.0, compress="deflate")
    with rasterio.open(out, "w", **meta) as dst:
        dst.write(arr[:1].astype("float32"))
    prov = {
        "source": "NLS elevation model 2 m via Paituli (CSC)",
        "tiles": urls,
        "bbox_epsg3067": list(bbox_3067),
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence": "CC BY 4.0",
        "attribution": ATTRIBUTION,
    }
    out.with_suffix(".provenance.json").write_text(json.dumps(prov, indent=2))
    return prov
