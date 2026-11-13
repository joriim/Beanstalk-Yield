"""PlanetScope (Planet Labs): search, parcel-level clear share, order, status, download.

Planet is a paid source with a quota counted on the clipped area, and its
images may not be passed on outside the organisation; only derived maps and
numbers go into reports.

Authentication (checked November 2026):

- Data API (search, coverage): HTTP Basic, the API key as username, empty password
- Orders API (order, status, download): header ``Authorization: api-key <key>``

The key is read from ``PL_API_KEY``. In a cloud session the network proxy can
inject one saved credential for ``api.planet.com`` instead: leave the variable
unset (or set it to ``proxy-injected``) and no header is sent. Only the key is
needed, not the Account ID or User ID.

Typical use (``yieldsuite planet ...``): search the field on a day, choose the
clearest scene over the parcel (``coverage?mode=estimate``, because a scene's
own ``clear_percent`` covers ~25 x 12 km), order it clipped to the field and
harmonised to Sentinel-2 (``analytic_sr_udm2`` bundle), wait for ``success``
(minutes; ~18 min for a 20-scene order) and download the GeoTIFFs. Then add them
to the field config as ``imagery: - kind: planetscope``.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from . import http

DATA = "https://api.planet.com/data/v1"
ORDERS = "https://api.planet.com/compute/ops/orders/v2"
PROXY = "proxy-injected"
BUNDLE = "analytic_sr_udm2"
LICENCE = "PlanetScope © Planet Labs PBC; not to be passed on outside the organisation"


class PlanetError(RuntimeError):
    pass


def _key() -> str | None:
    k = os.environ.get("PL_API_KEY")
    return None if not k or k == PROXY else k


def headers(api: str = "data") -> dict:
    k = _key()
    if not k:
        return {}
    if api == "orders":
        return {"Authorization": f"api-key {k}"}
    return {"Authorization": "Basic " + base64.b64encode(f"{k}:".encode()).decode()}


def _call(url: str, body=None, api: str = "data", method: str | None = None, opener=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": http.USER_AGENT, **headers(api)}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=120) as r:  # noqa: S310 - fixed host
            raw = r.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise PlanetError(
                "Planet rejected the credentials (401). Use the API key only (not the "
                "Account or User ID): PL_API_KEY, or a saved network secret for api.planet.com"
            ) from None
        if exc.code == 429:
            raise PlanetError("Planet rate limit (429): wait and retry") from None
        raise PlanetError(f"Planet HTTP {exc.code} for {url.split('?')[0]}") from None
    return json.loads(raw) if raw else {}


def search(geom: dict, start: date, end: date, opener=None) -> list[dict]:
    """PSScene items over ``geom`` (GeoJSON, WGS84) acquired in [start, end)."""
    body = {
        "item_types": ["PSScene"],
        "filter": {
            "type": "AndFilter",
            "config": [
                {"type": "GeometryFilter", "field_name": "geometry", "config": geom},
                {
                    "type": "DateRangeFilter",
                    "field_name": "acquired",
                    "config": {"gte": f"{start}T00:00:00Z", "lt": f"{end}T00:00:00Z"},
                },
            ],
        },
    }
    out, url = [], f"{DATA}/quick-search?_page_size=250"
    page = _call(url, body, opener=opener)
    while True:
        out += page.get("features", [])
        nxt = page.get("_links", {}).get("_next")
        if not nxt or len(out) > 2000:
            return out
        page = _call(nxt, opener=opener)


def parcel_clear(item_id: str, geom: dict, opener=None, tries: int = 4) -> float | None:
    """Clear share (%) of the scene over the parcel; no quota used."""
    for _ in range(tries):
        r = _call(
            f"{DATA}/item-types/PSScene/items/{item_id}/coverage?mode=estimate",
            {"geometry": geom},
            opener=opener,
        )
        if r.get("status") == "complete":
            return r.get("clear_percent")
        time.sleep(3)
    return None


def candidates(geom: dict, day: date, opener=None, check_parcel: bool = True) -> list[dict]:
    """Scenes of one day, clearest over the parcel first."""
    rows = []
    for f in search(geom, day, day + timedelta(days=1), opener):
        p = f["properties"]
        assets = set(f.get("assets", []))
        rows.append(
            {
                "id": f["id"],
                "acquired": p.get("acquired"),
                "instrument": p.get("instrument"),
                "quality": p.get("quality_category"),
                "scene_clear_pct": p.get("clear_percent"),
                "parcel_clear_pct": parcel_clear(f["id"], geom, opener) if check_parcel else None,
                "sun_elevation": p.get("sun_elevation"),
                "has_sr": bool(assets & {"ortho_analytic_4b_sr", "ortho_analytic_8b_sr"}),
            }
        )
    key = "parcel_clear_pct" if check_parcel else "scene_clear_pct"
    return sorted(rows, key=lambda r: (-(r[key] or -1), -(r["sun_elevation"] or 0)))


def order(
    item_ids: list[str],
    clip_geom: dict,
    name: str,
    bundle: str = BUNDLE,
    harmonize: bool = True,
    opener=None,
) -> str:
    """Place an order clipped to the field; returns the order id. Uses quota."""
    tools = [{"clip": {"aoi": clip_geom}}]
    if harmonize:
        tools.append({"harmonize": {"target_sensor": "Sentinel-2"}})
    body = {
        "name": name,
        "products": [{"item_ids": item_ids, "item_type": "PSScene", "product_bundle": bundle}],
        "tools": tools,
    }
    return _call(ORDERS, body, api="orders", opener=opener)["id"]


def status(order_id: str, opener=None) -> dict:
    r = _call(f"{ORDERS}/{order_id}", api="orders", opener=opener)
    return {
        "id": r.get("id"),
        "state": r.get("state"),
        "created_on": r.get("created_on"),
        "last_modified": r.get("last_modified"),
        "last_message": r.get("last_message"),
        "results": [x.get("name") for x in r.get("_links", {}).get("results", [])],
        "_results": r.get("_links", {}).get("results", []),
    }


def wait(order_id: str, timeout_s: float = 3600, opener=None, sleep=time.sleep) -> dict:
    delay, waited = 15.0, 0.0
    while True:
        s = status(order_id, opener)
        if s["state"] in ("success", "partial", "failed", "cancelled"):
            return s
        if waited >= timeout_s:
            raise PlanetError(f"order {order_id} still {s['state']} after {timeout_s:.0f} s")
        sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 120.0)


def download(order_id: str, out_dir, opener=None) -> list[str]:
    """Download the order's GeoTIFFs and metadata JSON into ``out_dir``."""
    s = status(order_id, opener)
    if s["state"] not in ("success", "partial"):
        raise PlanetError(f"order {order_id} is {s['state']}; nothing to download yet")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for res in s["_results"]:
        name = Path(res.get("name", "")).name
        if not name.endswith((".tif", "_metadata.json", "manifest.json")):
            continue
        req = urllib.request.Request(
            res["location"], headers={"User-Agent": http.USER_AGENT, **headers("orders")}
        )
        try:
            with (opener or urllib.request.urlopen)(req, timeout=600) as r:  # noqa: S310
                (out / name).write_bytes(r.read())
        except urllib.error.URLError as exc:
            raise PlanetError(
                f"download of {name} failed ({exc}); the links redirect to link.planet.com "
                "(and cloud storage), which the network must allow"
            ) from None
        saved.append(str(out / name))
    (out / "LICENCE.txt").write_text(LICENCE + "\n")
    return saved
