"""Light use (IPAR, GPP, RUE fill), the critical review and the FMI radiation model."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from yieldsuite.l4.grid import Grid
from yieldsuite.l5.lightuse import (
    LightUseSettings,
    choose_fill,
    clear_sky_par,
    light_use,
    ratio_cv,
    temperature_scalar,
)

S = LightUseSettings(enabled=True)


def test_temperature_scalar_trapezoid():
    f = temperature_scalar([-5, 7.5, 20, 30, 40, np.nan], S)
    assert np.allclose(f[:5], [0, 0.5, 1, 0.5, 0]) and f[5] == 1


def test_ipar_gpp_scale_with_greenness_and_temperature():
    days = pd.date_range("2026-05-10", "2026-08-30", freq="D")
    par = pd.Series(8.0, index=days)
    dates = pd.to_datetime(["2026-06-01", "2026-07-01", "2026-08-01"])
    ndvi = pd.DataFrame({0: [0.4, 0.8, 0.6], 1: [0.4, 0.6, 0.4]}, index=dates)
    out = light_use(ndvi, par, date(2026, 5, 10), date(2026, 8, 30), S)
    assert out["ipar_mj_m2"][0] > out["ipar_mj_m2"][1] > 0
    assert np.allclose(out["gpp_g_c_m2"], S.eps_g_c_mj * out["ipar_mj_m2"])  # no temperature
    cold = light_use(ndvi, par, date(2026, 5, 10), date(2026, 8, 30), S, pd.Series(7.5, index=days))
    assert np.allclose(cold["gpp_g_c_m2"], 0.5 * out["gpp_g_c_m2"])


def test_clear_sky_par_longer_days_in_june():
    p = clear_sky_par(62.5, pd.to_datetime(["2026-03-21", "2026-06-21"]))
    assert p.iloc[1] > 2 * p.iloc[0] > 0


def test_ratio_cv_and_fill_choice():
    rng = np.random.default_rng(0)
    rows, cols = np.divmod(np.arange(400), 20)
    x = 800 + 200 * np.sin(cols / 4)
    y = 0.012 * x + rng.normal(0, 0.2, x.size)
    c = ratio_cv(y, x, np.ones(400, bool), rows, cols, block=5)
    assert abs(c["ratio"] - 0.012) < 5e-4 and c["cv_rmse"] < 0.3 and c["corr"] > 0.9
    name, why = choose_fill(1.0, {"ipar": c}, S)
    assert name == "ipar" and "beats" in why
    assert choose_fill(0.1, {"ipar": c}, S)[0] == "image"
    assert choose_fill(1.0, {"ipar": c}, LightUseSettings(fill="image"))[0] == "image"


def _records(grid, rng, drift=0.0, deficit_box=None):
    """Clean records on a 30 x 30 m grid of 10 m cells, passes along x."""
    rows = []
    t = 0.0
    for p in range(30):
        ys = grid.ymax - 2 - p * 3.2
        xs = np.arange(2.0, 298.0, 1.2)
        if p % 2:
            xs = xs[::-1]
        for x in xs:
            rows.append((t, x, ys, np.pi / 2 if p % 2 == 0 else 3 * np.pi / 2, 1.2))
            t += 1.0
    df = pd.DataFrame(rows, columns=["t", "x", "y", "heading", "speed"])
    df["hx"], df["hy"], df["length"] = df["x"], df["y"], 0.0
    df["header_down"], df["flags"], df["machine_id"] = True, 0, "m1"
    base = 8.0 + drift * (df["t"] / df["t"].max())
    if deficit_box:
        x0, x1, y0, y1 = deficit_box
        inside = (df.x > x0) & (df.x < x1) & (df.y > y0) & (df.y < y1)
        base = base - 3.0 * inside
    df["yield"] = base + rng.normal(0, 0.2, len(df))
    return df


def test_review_finds_drift_deficit_and_unlogged():
    from yieldsuite.l5.review import review

    rng = np.random.default_rng(1)
    grid = Grid(xmin=0.0, ymax=100.0, resolution=10.0, nx=30, ny=10)
    pred = np.full((10, 30), 8.0)
    rec = _records(grid, rng, drift=-1.6, deficit_box=(100, 160, 40, 80))
    y4 = np.full((10, 30), np.nan)
    ci = np.floor((grid.ymax - rec.y) / 10).astype(int) * 30 + np.floor(rec.x / 10).astype(int)
    m = rec.assign(c=ci).groupby("c")["yield"].mean()
    y4.ravel()[m.index] = m.values
    y4[:, 25:] = np.nan  # an unlogged strip
    nrec = np.where(np.isfinite(y4), 5.0, 0.0)
    out = review(rec, grid, {"yield": y4, "n_records": nrec}, pred)
    by = {c["id"]: c for c in out["checks"]}
    assert by["drift"]["value"] < -10 and by["drift"]["severity"] == "alert"
    assert by["deficit"]["severity"] in ("watch", "alert") and by["deficit"]["clusters"]
    assert by["unlogged"]["value"] == pytest.approx(5 / 30 * 100, abs=0.5)
    assert by["unlogged"]["predicted_grain_t"] == pytest.approx(8.0 * 50 * 100 / 1e4, rel=0.01)
    assert by["direction"]["severity"] == "ok"
    assert "fi" in by["drift"]["finding"] and "en" in by["drift"]["finding"]


def test_fmi_parse_and_cloud_radiation_model():
    from yieldsuite.weather import fmi

    xml = b"""<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
      xmlns:BsWfs="http://xml.fmi.fi/schema/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">
      <wfs:member><BsWfs:BsWfsElement><BsWfs:Location><gml:Point><gml:pos>62.68 22.84 </gml:pos>
      </gml:Point></BsWfs:Location><BsWfs:Time>2026-07-01T09:00:00Z</BsWfs:Time>
      <BsWfs:ParameterName>n_man</BsWfs:ParameterName><BsWfs:ParameterValue>8.0</BsWfs:ParameterValue>
      </BsWfs:BsWfsElement></wfs:member></wfs:FeatureCollection>"""
    df = fmi.parse_simple(xml)
    assert df.iloc[0]["parameter"] == "n_man" and df.iloc[0]["value"] == 8.0
    times = pd.date_range("2026-06-21", periods=48, freq="h", tz="UTC")

    def frame(n):
        return pd.DataFrame(
            {"time": times, "lat": 62.68, "lon": 22.84, "parameter": "n_man", "value": n}
        )

    clear = fmi.modelled_daily_radiation(frame(0.0), 62.68, 22.84)
    cloudy = fmi.modelled_daily_radiation(frame(8.0), 62.68, 22.84)
    assert 25 < clear.iloc[0] < 35  # a clear midsummer day at 62.7 N
    assert cloudy.iloc[0] == pytest.approx(0.25 * clear.iloc[0], rel=0.01)
