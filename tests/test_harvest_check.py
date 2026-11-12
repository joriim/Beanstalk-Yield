"""Unlogged area: harvested or not, from synthetic before/after scenes."""

from datetime import date

import numpy as np

from yieldsuite.l4.grid import Grid
from yieldsuite.l5 import harvest_check as hc
from yieldsuite.public.optical import Scene, _clear_from_mask

GRID = Grid(xmin=0.0, ymax=400.0, resolution=10.0, nx=40, ny=40)


def _scene(fam, day, ndvi, ndti, rng):
    """Bands that give the wanted NDVI/NDTI per cell (red = 0.1, swir2 = 0.15)."""
    noise = lambda: rng.normal(0, 0.005, ndvi.shape)  # noqa: E731
    red = np.full(ndvi.shape, 0.1)
    nir = red * (1 + ndvi + noise()) / (1 - ndvi)
    sw2 = np.full(ndvi.shape, 0.15)
    sw1 = sw2 * (1 + ndti + noise()) / (1 - ndti)
    bands = {"red": red, "nir": nir, "swir1": sw1, "swir2": sw2}
    return Scene(fam, day, f"{fam}-{day}", "test", bands, np.ones(ndvi.shape), 20.0)


def _setup():
    field = np.zeros((40, 40), bool)
    field[2:38, 2:38] = True
    unlogged = np.zeros_like(field)
    unlogged[5:15, 5:15] = True  # block A: will be harvested (not logged)
    unlogged[25:35, 25:35] = True  # block B: left standing
    unlogged[2:38, 36:38] = True  # 20 m strip at the edge: too narrow
    logged = field & ~unlogged
    return field, logged, unlogged


def _scenes(rng, field, standing_mask, before_stubble=None):
    ripe_v, ripe_t, stub_v, stub_t = 0.45, 0.20, 0.25, 0.08
    v0 = np.full(field.shape, ripe_v)
    t0 = np.full(field.shape, ripe_t)
    if before_stubble is not None:
        v0[before_stubble], t0[before_stubble] = stub_v, stub_t
    v1 = np.where(standing_mask, ripe_v - 0.02, stub_v)
    t1 = np.where(standing_mask, ripe_t - 0.01, stub_t)
    return [_scene("S2", "2026-08-20", v0, t0, rng), _scene("S2", "2026-08-30", v1, t1, rng)]


def test_harvested_standing_and_too_narrow():
    rng = np.random.default_rng(0)
    field, logged, unlogged = _setup()
    standing = np.zeros_like(field)
    standing[25:35, 25:35] = True
    res = hc.check(_scenes(rng, field, standing), GRID, field, logged, unlogged,
                   date(2026, 8, 24), date(2026, 8, 26))
    v = {r["verdict"] for r in res["regions"]}
    assert {hc.HARVESTED, hc.STANDING} <= v
    by = {r["verdict"]: r for r in res["regions"]}
    assert by[hc.STANDING]["area_ha"] == 1.0 and by[hc.HARVESTED]["harvested_by"] == "2026-08-30"
    assert res["pairs_used"] and res["pairs_used"][0]["separation"] > 2
    sev, fi, en = hc.finding(res, None)
    assert sev == "alert" and "puimattomalta" in fi and "unharvested" in en


def test_strip_alone_suggests_planet():
    rng = np.random.default_rng(1)
    field, logged, _ = _setup()
    strip = np.zeros_like(field)
    strip[2:38, 36:38] = True
    res = hc.check(_scenes(rng, field, np.zeros_like(field)), GRID, field, field & ~strip, strip,
                   date(2026, 8, 24), date(2026, 8, 26))
    assert res["regions"][0]["verdict"] == "too_narrow" and res["planet_suggested"]
    sug = hc.planet_suggestion(date(2026, 8, 26), 13.0, [
        {"id": "x1", "acquired": "2026-08-27T10:00:00Z", "parcel_clear_pct": 100.0,
         "has_sr": True}])
    assert "x1" in sug["fi"] and sug["first_day"] == "2026-08-27"
    _, fi, _ = hc.finding(res, sug)
    assert "PlanetScope" in fi


def test_harvested_before_logged_harvest():
    rng = np.random.default_rng(2)
    field, logged, _ = _setup()
    early = np.zeros_like(field)
    early[5:15, 5:15] = True
    res = hc.check(_scenes(rng, field, np.zeros_like(field), before_stubble=early), GRID, field,
                   field & ~early, early, date(2026, 8, 24), date(2026, 8, 26))
    assert res["regions"][0]["verdict"] == hc.BEFORE


def test_no_pair_is_unclear():
    rng = np.random.default_rng(3)
    field, logged, unlogged = _setup()
    sc = _scenes(rng, field, np.zeros_like(field))[1:]  # only an after scene
    res = hc.check(sc, GRID, field, logged, unlogged, date(2026, 8, 24), date(2026, 8, 26))
    assert not res["pairs_used"] and res["planet_suggested"]
    assert hc.finding(res, None)[0] == "watch"


def test_masks():
    assert _clear_from_mask("S2", np.array([4.0, 5, 8, 9, 3])).tolist() == [1, 1, 0, 0, 0]
    clear = 1 << 6
    cloud = (1 << 6) | (1 << 3)
    assert _clear_from_mask("LS", np.array([clear, cloud, 0])).tolist() == [True, False, False]


def test_feature_cache_follows_the_grid(tmp_path):
    import json

    import pandas as pd

    from yieldsuite.l5 import run as l5run

    old = Grid(xmin=10.0, ymax=390.0, resolution=10.0, nx=3, ny=2)
    f = tmp_path / "L5_x_s2_features.csv"
    pd.DataFrame({"cell": [0, 5], "date": ["2026-07-01"] * 2, "index": ["NDVI"] * 2,
                  "value": [0.5, 0.7]}).to_csv(f, index=False)
    (tmp_path / "L5_x_s2_features.grid.json").write_text(json.dumps(l5run._grid_signature(old)))
    assert l5run._cached_features(f, old, "EPSG:3067") is not None
    new = Grid(xmin=0.0, ymax=400.0, resolution=10.0, nx=5, ny=4)
    assert l5run._cached_features(f, new, "EPSG:3067") is None
    assert not f.exists() and (tmp_path / "L5_x_s2_features.stale.csv").exists()
    df = l5run._remap_stale_features(f, new)
    # old (row 0, col 0) -> new (1, 1) = 6; old (1, 2) -> new (2, 3) = 13
    assert sorted(df["cell"].tolist()) == [6, 13]
