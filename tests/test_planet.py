"""PlanetScope connector against a fake Planet API (no network)."""

import base64
import io
import json

import pytest

from yieldsuite.public import planet

GEOM = {
    "type": "Polygon",
    "coordinates": [[[22.8, 62.4], [22.81, 62.4], [22.81, 62.41], [22.8, 62.4]]],
}


class FakePlanet:
    def __init__(self):
        self.calls = []
        self.polls = 0

    def __call__(self, req, timeout=None):
        url = req.full_url
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), url, dict(req.header_items()), body))
        if "quick-search" in url:
            out = {
                "features": [
                    {
                        "id": "A",
                        "properties": {
                            "acquired": "2026-08-21T09:40:00Z",
                            "clear_percent": 40,
                            "instrument": "PSB.SD",
                            "sun_elevation": 30,
                        },
                        "assets": ["ortho_analytic_8b_sr"],
                    },
                    {
                        "id": "B",
                        "properties": {
                            "acquired": "2026-08-21T09:41:00Z",
                            "clear_percent": 90,
                            "instrument": "PSB.SD",
                            "sun_elevation": 30,
                        },
                        "assets": [],
                    },
                ]
            }
        elif "/coverage" in url:
            out = {"status": "complete", "clear_percent": 100 if "/items/A/" in url else 10}
        elif url.endswith("/orders/v2") and body:
            out = {"id": "ord-1"}
        elif "/orders/v2/ord-1" in url:
            self.polls += 1
            state = "running" if self.polls < 3 else "success"
            out = {
                "id": "ord-1",
                "state": state,
                "_links": {
                    "results": [
                        {
                            "name": "ord-1/PSScene/A_3B_AnalyticMS_SR_8b_harmonized_clip.tif",
                            "location": "https://dl/a",
                        },
                        {"name": "ord-1/PSScene/A_3B_udm2_clip.tif", "location": "https://dl/u"},
                        {"name": "ord-1/PSScene/A_metadata.json", "location": "https://dl/m"},
                        {
                            "name": "ord-1/PSScene/A_3B_AnalyticMS_metadata_clip.xml",
                            "location": "https://dl/x",
                        },
                    ]
                },
            }
        elif url.startswith("https://dl/"):
            return io.BytesIO(b"bytes-" + url[-1].encode())
        else:
            raise AssertionError(url)
        return io.BytesIO(json.dumps(out).encode())


def test_headers_differ_by_api(monkeypatch):
    monkeypatch.setenv("PL_API_KEY", "k123")
    assert planet.headers("orders") == {"Authorization": "api-key k123"}
    assert planet.headers("data")["Authorization"] == "Basic " + base64.b64encode(b"k123:").decode()
    monkeypatch.setenv("PL_API_KEY", "proxy-injected")
    assert planet.headers("data") == {} and planet.headers("orders") == {}


def test_candidates_rank_by_parcel_not_scene(monkeypatch):
    monkeypatch.delenv("PL_API_KEY", raising=False)
    from datetime import date

    rows = planet.candidates(GEOM, date(2026, 8, 21), opener=FakePlanet())
    assert [r["id"] for r in rows] == ["A", "B"]  # A: 40 % scene but 100 % over the parcel
    assert rows[0]["parcel_clear_pct"] == 100 and rows[0]["has_sr"] and not rows[1]["has_sr"]


def test_order_wait_download(monkeypatch, tmp_path):
    monkeypatch.setenv("PL_API_KEY", "k123")
    fake = FakePlanet()
    oid = planet.order(["A"], GEOM, "test", opener=fake)
    method, url, hdrs, body = fake.calls[-1]
    assert oid == "ord-1" and method == "POST" and hdrs["Authorization"] == "api-key k123"
    assert body["products"][0]["product_bundle"] == "analytic_sr_udm2"
    assert body["tools"][1] == {"harmonize": {"target_sensor": "Sentinel-2"}}
    st = planet.wait("ord-1", opener=fake, sleep=lambda s: None)
    assert st["state"] == "success" and fake.polls == 3
    files = planet.download("ord-1", tmp_path, opener=fake)
    names = sorted(p.split("/")[-1] for p in files)
    assert names == [
        "A_3B_AnalyticMS_SR_8b_harmonized_clip.tif",
        "A_3B_udm2_clip.tif",
        "A_metadata.json",
    ]
    assert (tmp_path / "LICENCE.txt").exists()


def test_401_explains_the_key(monkeypatch):
    import urllib.error

    def deny(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    with pytest.raises(planet.PlanetError, match="API key only"):
        planet.status("x", opener=deny)
