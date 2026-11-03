"""Sentinel-2 scene search via a STAC API (default: Copernicus Data Space).

Lists the scenes over a field with their date and scene cloud cover, so the
user (or an agent) can see before an openEO run whether the season has
enough clear dates. Scene cloud cover is for the whole 100 km tile; the
field-level cloud mask is applied later in openEO with the SCL layer.
"""

from __future__ import annotations

from . import http

DEFAULT_STAC_URL = "https://stac.dataspace.copernicus.eu/v1"
DEFAULT_S2_COLLECTION = "sentinel-2-l2a"


def search_sentinel2(
    bbox_wgs84: tuple[float, float, float, float],
    start: str,
    end: str,
    max_cloud: float = 60.0,
    url: str = DEFAULT_STAC_URL,
    collection: str = DEFAULT_S2_COLLECTION,
    limit: int = 100,
    max_pages: int = 20,
    post=None,
) -> dict:
    """Return {"scenes": [...], "dates": [...]} sorted by date.

    One entry per scene with id, datetime, date, cloud cover and tile; ``dates``
    keeps the least cloudy scene per day.
    """
    post = post or http.post_json
    body = {
        "collections": [collection],
        "bbox": list(bbox_wgs84),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "limit": limit,
    }
    search_url = url.rstrip("/") + "/search"
    scenes = []
    for _ in range(max_pages):
        page = post(search_url, body)
        for f in page.get("features", []):
            p = f.get("properties", {})
            cc = p.get("eo:cloud_cover")
            scenes.append(
                {
                    "id": f.get("id"),
                    "datetime": p.get("datetime"),
                    "date": (p.get("datetime") or "")[:10],
                    "cloud_cover": None if cc is None else float(cc),
                    "tile": p.get("s2:mgrs_tile") or p.get("grid:code") or p.get("tileId"),
                    "platform": p.get("platform"),
                }
            )
        nxt = next((ln for ln in page.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt:
            break
        if nxt.get("body"):
            body = {**body, **nxt["body"]}
        if nxt.get("href"):
            search_url = nxt["href"]
    scenes.sort(
        key=lambda s: (s["date"], s["cloud_cover"] if s["cloud_cover"] is not None else 101)
    )
    kept = [s for s in scenes if s["cloud_cover"] is None or s["cloud_cover"] <= max_cloud]
    best: dict[str, dict] = {}
    for s in kept:
        best.setdefault(s["date"], s)
    return {
        "source": search_url.split("/search")[0],
        "collection": collection,
        "n_scenes": len(scenes),
        "n_below_max_cloud": len(kept),
        "max_cloud": max_cloud,
        "dates": [
            {"date": d, "cloud_cover": s["cloud_cover"], "tile": s["tile"], "id": s["id"]}
            for d, s in sorted(best.items())
        ],
    }
