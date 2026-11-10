"""Seedbed proxies: bare-soil wetness, emergence delay, soil loggers. Known answers."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from yieldsuite.weather.seedbed import (
    bare_soil_wetness,
    emergence,
    logger_summary,
    read_loggers,
)

SOW = date(2026, 5, 12)
NDVI_AT = lambda f: (f + 0.1426) / 1.1638  # noqa: E731 - NDVI giving fAPAR f


def test_bare_soil_wetness_is_anomaly_from_the_date_median():
    n = 40
    wet = np.arange(n) < 10  # a wet hollow: NDMI 0.10 above the rest
    rng = np.random.default_rng(1)
    dates = [date(2026, 4, 25), date(2026, 5, 5), date(2026, 5, 15), date(2026, 6, 20)]
    # each date has its own level (tillage, rain): only the difference counts
    ndmi = pd.DataFrame(
        [lvl + 0.10 * wet + rng.normal(0, 0.005, n) for lvl in (-0.05, 0.05, 0.0, 0.3)],
        index=dates,
    )
    ndvi = pd.DataFrame([[0.15] * n, [0.15] * n, [0.18] * n, [0.7] * n], index=dates)
    ndvi.iloc[1, 39] = 0.5  # a green patch on one date: not bare there
    out = bare_soil_wetness(ndvi, ndmi, SOW)
    assert out["dates"] == ["2026-04-25", "2026-05-05", "2026-05-15"]  # June is green
    assert out["wetness"][wet].mean() - out["wetness"][~wet].mean() == pytest.approx(0.10, abs=0.01)
    assert out["n_dates"][0] == 3 and out["n_dates"][39] == 2
    assert out["repeatability"] > 0.9
    # scatter between dates is 0.005 -> standard error of a three-date mean about 0.003
    assert np.nanmedian(out["sigma"]) == pytest.approx(0.005 / np.sqrt(3), abs=0.001)
    assert (out["class"][wet] == 1).all()  # the hollow stands out from the field median
    assert (out["class"][~wet] == 0).mean() > 0.8  # the rest cannot be told apart from it
    one = bare_soil_wetness(ndvi.iloc[:1], ndmi.iloc[:1], SOW)  # one date: no way to judge
    assert np.isnan(one["sigma"]).all() and np.isnan(one["class"]).all()

    green = bare_soil_wetness(ndvi * 0 + 0.6, ndmi, SOW)  # ley: no bare-soil date
    assert green["dates"] == [] and np.isnan(green["wetness"]).all()
    assert green["repeatability"] is None


def test_emergence_day_lag_and_gap():
    dates = [SOW + timedelta(days=d) for d in (10, 20, 40)]
    # cell 0: fAPAR 0.4 on day 20 -> 0.2 on day 15; cell 1: 0.2 exactly on day 20;
    # cell 2: 0.1 on day 20, 0.5 on day 40 -> day 25; cell 3: never; cell 4: outside the field
    f = np.array(
        [[0.0, 0.0, 0.0, 0.0, np.nan], [0.4, 0.2, 0.1, 0.05, np.nan], [0.8, 0.8, 0.5, 0.1, np.nan]]
    )
    ndvi = pd.DataFrame(NDVI_AT(f), index=dates)
    days = pd.date_range(SOW - timedelta(days=5), periods=80).date
    gdd_cum = pd.Series(np.arange(80) * 10.0, index=days)  # 10 °Cd per day
    e = emergence(ndvi, SOW, gdd_cum=gdd_cum)
    assert e["days"][:3] == pytest.approx([15, 20, 25])
    assert np.isnan(e["days"][3:]).all()
    assert e["lag_days"][:3] == pytest.approx([-5, 0, 5])
    assert e["gap_days"][:3] == pytest.approx([10, 10, 20])
    assert e["gdd"][:3] == pytest.approx([150, 200, 250])  # counted from sowing
    assert e["lag_gdd"][:3] == pytest.approx([-50, 0, 50])
    # noise 0.025 per image through the straight line: gap x noise x sqrt(a² + b²) / rise²
    assert e["sigma_days"][:3] == pytest.approx([0.442, 1.25, 0.988], abs=0.001)
    assert list(e["lag_class"][:3]) == [-1, 0, 1] and np.isnan(e["lag_class"][3:]).all()
    noisy = emergence(ndvi, SOW, fapar_noise=0.3)  # poor images: five days is within the noise
    assert list(noisy["lag_class"][:3]) == [0, 0, 0]


def test_loggers_contrast_and_offset(tmp_path):
    rows = ["logger;time;temp_c"]
    for day in range(10):
        for hour in range(24):
            t = f"2026-05-{12 + day:02d} {hour:02d}:00"
            rows.append(f"knoll;{t};12,0")
            if not (day == 3 and hour > 5):  # the hollow logger lost most of one day
                rows.append(f"hollow;{t};9,0")
    path = tmp_path / "loggers.csv"
    path.write_text("\n".join(rows))
    daily = read_loggers(path)
    assert daily.shape == (10, 2) and np.isnan(daily.loc[date(2026, 5, 15), "hollow"])
    weather = pd.DataFrame(
        {"soil_temperature_0_to_7cm_daily_mean": 10.0}, index=pd.Index(daily.index, name="date")
    )
    out = logger_summary(daily, SOW, None, 5.0, weather)
    assert out["period"]["days"] == 9  # only the days both loggers have
    assert out["loggers"]["knoll"]["thermal_sum_c_days"] == pytest.approx(9 * 7)
    assert out["loggers"]["hollow"]["minus_era5_land_c"] == pytest.approx(-1.0)
    assert out["contrast"] == pytest.approx({"mean_c": 3.0, "thermal_sum_c_days": 27.0})
    assert logger_summary(daily, date(2027, 1, 1), None, 5.0)["loggers"] == {}
    with pytest.raises(ValueError, match="missing columns"):
        bad = tmp_path / "bad.csv"
        bad.write_text("id,temp\na,1")
        read_loggers(bad)


def test_run_writes_seedbed_summary_and_bands(tmp_path, example_config_path):
    from test_weather import fake_payload

    from yieldsuite.config import CropSettings, load_config
    from yieldsuite.l4 import Grid
    from yieldsuite.weather import openmeteo, parse_response
    from yieldsuite.weather.run import run_agroclimate, weather_path

    grid = Grid(xmin=0.0, ymax=100.0, resolution=20.0, nx=5, ny=5)
    cfg = load_config(example_config_path)
    cfg.output_dir = tmp_path / "output"
    cfg.location = (62.8, 22.85)
    cfg.crop = CropSettings(sowing_date=str(SOW), harvest_date="2026-08-25")
    openmeteo.save(parse_response(fake_payload()), {"request": {}}, weather_path(cfg))
    cells = np.arange(25)
    late = cells % 5 == 0  # the west column is wet and emerges late
    recs = []
    for d, ndvi_v, ndmi_v in [
        ("2026-05-02", np.full(25, 0.15), 0.1 * late),
        ("2026-05-10", np.full(25, 0.15), 0.1 * late + 0.05),
        ("2026-06-01", NDVI_AT(np.where(late, 0.2, 0.4)), np.full(25, 0.2)),
        ("2026-07-01", NDVI_AT(np.full(25, 0.8)), np.full(25, 0.4)),
    ]:
        recs += [(c, d, "NDVI", ndvi_v[c]) for c in cells]
        recs += [(c, d, "NDMI", ndmi_v[c]) for c in cells]
    feats = pd.DataFrame.from_records(recs, columns=["cell", "date", "index", "value"])
    loggers = tmp_path / "loggers.csv"
    loggers.write_text(
        "logger,time,temp_c\n"
        + "\n".join(
            f"{name},2026-05-{d:02d} {h:02d}:00,{t}"
            for name, t in (("west", 8), ("east", 11))
            for d in range(12, 20)
            for h in range(24)
        )
    )
    cfg.seedbed.loggers = loggers
    cfg.seedbed.logger_positions = {"west": (10.0, 90.0), "east": (90.0, 90.0)}

    res = run_agroclimate(cfg, grid, features_long=feats)
    sb = res["summary"]["seedbed"]
    assert sb["bare_soil"]["dates"] == ["2026-05-02", "2026-05-10"]
    assert sb["bare_soil"]["repeatability"] == pytest.approx(1.0)
    assert sb["emergence"]["median_days_from_sowing"] == pytest.approx(10.0)
    assert sb["emergence"]["median_image_gap_days"] == 20
    assert sb["emergence"]["cells_distinct_from_median"] == {
        "lower": 0.0,
        "not_distinct": 0.8,
        "higher": 0.2,
    }
    refine = sb["emergence"]["refine"]  # every cell came up between sowing and 1 June
    assert (refine["from"], refine["to"], refine["share_of_cells"]) == (str(SOW), "2026-06-01", 1.0)
    b = res["seedbed_bands"]
    assert b["emergence_lag_days"][0, 0] == pytest.approx(10.0)  # day 20 against day 10
    assert b["bare_soil_wetness"][0, 0] > b["bare_soil_wetness"][0, 1]
    west, east = sb["loggers"]["loggers"]["west"], sb["loggers"]["loggers"]["east"]
    assert west["cell"] == 0 and east["cell"] == 4
    assert west["emergence_lag_days"] == pytest.approx(10.0) and east["emergence_lag_days"] == 0
    assert sb["loggers"]["contrast"]["mean_c"] == pytest.approx(3.0)


def test_planetscope_images_narrow_the_emergence_interval(tmp_path, example_config_path):
    """Sentinel-2 on days -2, 30 and 50; PlanetScope (other NDVI scale) on days 31, 10, 14, 18."""
    from test_weather import fake_payload

    from yieldsuite.config import CropSettings, load_config
    from yieldsuite.l4 import Grid
    from yieldsuite.weather import openmeteo, parse_response
    from yieldsuite.weather.run import run_agroclimate, weather_path

    grid = Grid(xmin=0.0, ymax=120.0, resolution=20.0, nx=6, ny=6)
    cfg = load_config(example_config_path)
    cfg.output_dir = tmp_path / "output"
    cfg.location = (62.8, 22.85)
    cfg.crop = CropSettings(sowing_date=str(SOW), harvest_date="2026-08-25")
    openmeteo.save(parse_response(fake_payload()), {"request": {}}, weather_path(cfg))
    cells = np.arange(36)
    true_day = np.where(cells < 18, 12.0, 16.0)  # the south half emerges four days later
    rate = 0.02 + 0.0002 * cells  # fAPAR per day after emergence, a little different per cell

    def fapar(day):
        return np.clip(0.2 + (day - true_day) * rate, 0, 0.9)  # reaches 0.2 on true_day

    def rows(day, source, to_sensor=lambda v: v):
        d = str(SOW + timedelta(days=day))
        return [(c, d, "NDVI", to_sensor(NDVI_AT(fapar(day)))[c], source) for c in cells]

    s2 = [r for day in (-2, 30, 50) for r in rows(day, "sentinel2")]
    ps = [r for day in (31, 10, 14, 18) for r in rows(day, "planetscope", lambda v: 0.8 * v - 0.05)]
    cols = ["cell", "date", "index", "value", "source"]

    alone = run_agroclimate(cfg, grid, features_long=pd.DataFrame.from_records(s2, columns=cols))
    em = alone["summary"]["seedbed"]["emergence"]
    assert em["median_image_gap_days"] == 30 and em["refine"]["gap_days"] == 30

    both = run_agroclimate(
        cfg, grid, features_long=pd.DataFrame.from_records(s2 + ps, columns=cols)
    )
    em = both["summary"]["seedbed"]["emergence"]
    assert both["summary"]["image_sources"]["ndvi_dates"] == {"planetscope": 4, "sentinel2": 3}
    assert em["median_image_gap_days"] == 4 and em["refine"] is None
    lag = both["seedbed_bands"]["emergence_days"].ravel()
    assert lag[18:].mean() - lag[:18].mean() == pytest.approx(4.0, abs=0.3)

    # without an image near a Sentinel-2 date the sensors cannot be matched: left out, and said
    far = [r for r in ps if r[1] != str(SOW + timedelta(days=31))]
    res = run_agroclimate(
        cfg, grid, features_long=pd.DataFrame.from_records(s2 + far, columns=cols)
    )
    assert "planetscope" in res["summary"]["image_sources"]["left_out"]
    assert res["summary"]["seedbed"]["emergence"]["median_image_gap_days"] == 30
