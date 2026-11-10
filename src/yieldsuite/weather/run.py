"""Fetch (or reuse) field weather and compute the agroclimate summary and RUE."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import FieldYearConfig
from ..l4.grid import Grid
from . import openmeteo
from .agro import AgroSettings, daily_indices, season_summary
from .rue import field_rue, rue_map
from .seedbed import bare_soil_wetness, emergence, logger_summary, read_loggers


def field_location(config: FieldYearConfig, grid: Grid | None = None) -> tuple[float, float]:
    """(lat, lon) of the field: from the config, else the centre of the yield grid."""
    if config.location:
        return config.location
    if grid is None:
        raise ValueError("no location: set location: {lat, lon} or run the chain to L4 first")
    from pyproj import Transformer

    x = grid.xmin + grid.nx * grid.resolution / 2
    y = grid.ymax - grid.ny * grid.resolution / 2
    lon, lat = Transformer.from_crs(config.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return float(lat), float(lon)


def agro_settings(config: FieldYearConfig, harvest_fallback: date | None = None) -> AgroSettings:
    c = config.crop
    if c is None:
        return AgroSettings(harvest_date=harvest_fallback)
    return AgroSettings(
        crop=c.name,
        sowing_date=date.fromisoformat(c.sowing_date) if c.sowing_date else None,
        harvest_date=date.fromisoformat(c.harvest_date) if c.harvest_date else harvest_fallback,
        t_base_c=c.t_base_c,
        t_cap_c=c.t_cap_c,
        soil_t_base_c=c.soil_t_base_c,
        par_fraction=c.par_fraction,
        harvest_index=c.harvest_index,
        stages_gdd=dict(c.stages_gdd),
        ptq_window=tuple(c.ptq_window) if c.ptq_window else None,
    )


def weather_path(config: FieldYearConfig) -> Path:
    """Public-data cache next to the outputs: <output_dir>/../public/era5_daily.csv."""
    return Path(config.output_dir).parent / "public" / "era5_daily.csv"


def get_weather(
    config: FieldYearConfig,
    grid: Grid | None = None,
    refresh: bool = False,
    fetch=None,
) -> tuple[pd.DataFrame, dict, tuple[float, float]]:
    path = weather_path(config)
    lat, lon = field_location(config, grid)
    cached = openmeteo.load(path) if path.exists() else None
    if cached and not refresh and not _cache_stale(cached, config):
        return cached[0], cached[1], (lat, lon)
    req = openmeteo.WeatherRequest(
        lat=lat,
        lon=lon,
        start=f"{config.year}-03-01",
        end=f"{config.year}-10-31",
        model=config.weather.model,
        hourly=tuple(config.weather.hourly),
        round_deg=config.weather.round_deg,
    )
    today = date.today().isoformat()
    if req.end > today:
        req.end = today
    try:
        df, prov = (fetch or openmeteo.fetch_daily)(req)
    except Exception:
        if cached is None or refresh:
            raise
        # No network: the older cache is better than nothing; it lacks what was added.
        stale = "cache predates the configured weather model or hourly variables; refetch failed"
        return cached[0], cached[1] | {"stale": stale}, (lat, lon)
    openmeteo.save(df, prov, path)
    return df, prov, (lat, lon)


def _cache_stale(cached: tuple[pd.DataFrame, dict], config: FieldYearConfig) -> bool:
    """True when the config asks for a model or hourly variable the cache was not fetched with."""
    df, prov = cached
    if any(f"{v}_daily_mean" not in df for v in config.weather.hourly):
        return True
    model = prov.get("request", {}).get("model")
    return model is not None and model != config.weather.model


def run_agroclimate(
    config: FieldYearConfig,
    grid: Grid | None = None,
    harvest_fallback: date | None = None,
    refresh: bool = False,
    l5_bands: dict | None = None,
    features_long: pd.DataFrame | None = None,
    fetch=None,
) -> dict:
    """Season summary (+ field RUE and a per-cell RUE map when yield and NDVI exist)."""
    weather, prov, (lat, lon) = get_weather(config, grid, refresh, fetch)
    s = agro_settings(config, harvest_fallback)
    summary = season_summary(weather, lat, s)
    summary["location_used"] = {
        "lat": prov.get("request", {}).get("lat", round(lat, 2)),
        "lon": prov.get("request", {}).get("lon", round(lon, 2)),
    }
    summary["weather_source"] = prov.get("source")
    if prov.get("stale"):
        summary["weather_note"] = prov["stale"]
    summary["weather_model"] = prov.get("request", {}).get("model", config.weather.model)
    summary["crop"] = {
        "name": s.crop,
        "sowing_date": str(s.sowing_date),
        "harvest_date": str(s.harvest_date),
        "harvest_index": s.harvest_index,
    }
    daily = daily_indices(weather, lat, s)
    if s.sowing_date:
        daily = daily[pd.Index(daily.index) >= s.sowing_date]
    if s.harvest_date:
        daily = daily[pd.Index(daily.index) <= s.harvest_date]
    out = {"summary": summary, "weather_file": str(weather_path(config)), "daily": daily}
    if s.sowing_date is None:
        summary["note"] = "no sowing date: totals cover the whole fetched period"

    _seedbed(config, grid, s, weather, daily, features_long, out)

    if (
        l5_bands is not None
        and features_long is not None
        and s.sowing_date
        and s.harvest_date
        and grid is not None
    ):
        par = daily_indices(weather, lat, s)["par_mj_m2"]
        nd = features_long[features_long["index"] == "NDVI"]
        if not nd.empty:
            wide = nd.pivot_table(index="date", columns="cell", values="value")
            wide = wide.reindex(columns=range(grid.ny * grid.nx))
            y = np.asarray(l5_bands["yield"], float).ravel()
            res = rue_map(
                y,
                wide,
                par,
                s.sowing_date,
                s.harvest_date,
                config.reference_moisture_pct,
                s.harvest_index,
            )
            shape = (grid.ny, grid.nx)
            out["rue_bands"] = {k: v.reshape(shape) for k, v in res.items()}
            fld = field_rue(
                float(np.nanmean(y)),
                wide.mean(axis=1),
                par,
                s.sowing_date,
                s.harvest_date,
                config.reference_moisture_pct,
                s.harvest_index,
            )
            summary["field_rue"] = fld
            r = res["rue_g_mj"]
            summary["rue_map"] = {"p10": _pct(r, 10), "median": _pct(r, 50), "p90": _pct(r, 90)}
    return out


def _wide(features_long: pd.DataFrame, index: str, n_cells: int) -> pd.DataFrame | None:
    part = features_long[features_long["index"] == index]
    if part.empty:
        return None
    wide = part.pivot_table(index="date", columns="cell", values="value")
    return wide.reindex(columns=range(n_cells))


def _seedbed(config, grid, s: AgroSettings, weather, daily, features_long, out: dict) -> None:
    """Bare-soil wetness, emergence delay and loggers into ``out`` (summary and bands)."""
    sb = config.seedbed
    info: dict = {}
    bands: dict = {}
    if features_long is not None and grid is not None and s.sowing_date:
        n = grid.ny * grid.nx
        ndvi, ndmi = _wide(features_long, "NDVI", n), _wide(features_long, "NDMI", n)
        if ndvi is not None and ndmi is not None:
            w = bare_soil_wetness(
                ndvi, ndmi, s.sowing_date, sb.bare_days_before, sb.bare_days_after, sb.bare_ndvi
            )
            bands["bare_soil_wetness"] = w["wetness"]
            bands["bare_soil_dates"] = w["n_dates"]
            info["bare_soil"] = {"dates": w["dates"], "repeatability": w["repeatability"]}
        elif ndvi is not None:
            info["bare_soil"] = {"dates": [], "note": "no NDMI in the image features"}
        if ndvi is not None:
            e = emergence(ndvi, s.sowing_date, s.harvest_date, daily["gdd_cum"], sb.emergence_fapar)
            bands["emergence_days"] = e["days"]
            bands["emergence_lag_days"] = e["lag_days"]
            bands["emergence_gap_days"] = e["gap_days"]
            if "gdd" in e:
                bands["emergence_gdd"] = e["gdd"]
            info["emergence"] = {
                "fapar_threshold": sb.emergence_fapar,
                "median_days_from_sowing": _pct(e["days"], 50),
                "median_gdd": _pct(e["gdd"], 50) if "gdd" in e else None,
                "lag_days": {"p10": _pct(e["lag_days"], 10), "p90": _pct(e["lag_days"], 90)},
                "median_image_gap_days": _pct(e["gap_days"], 50),
                "cells_not_emerged": int(
                    (~np.isfinite(e["days"]) & ndvi.notna().any().to_numpy()).sum()
                ),
            }
    if sb.loggers is not None:
        base = s.t_base_c if s.soil_t_base_c is None else s.soil_t_base_c
        lg = logger_summary(read_loggers(sb.loggers), s.sowing_date, s.harvest_date, base, weather)
        if grid is not None:
            for name, (x, y) in sb.logger_positions.items():
                if name not in lg["loggers"]:
                    continue
                col = int((x - grid.xmin) // grid.resolution)
                row = int((grid.ymax - y) // grid.resolution)
                if not (0 <= col < grid.nx and 0 <= row < grid.ny):
                    lg["loggers"][name]["note"] = "position outside the grid"
                    continue
                cell = row * grid.nx + col
                lg["loggers"][name]["cell"] = cell
                for band in ("bare_soil_wetness", "emergence_lag_days"):
                    if band in bands and np.isfinite(bands[band][cell]):
                        lg["loggers"][name][band] = float(bands[band][cell])
        info["loggers"] = lg
    if info:
        out["summary"]["seedbed"] = info
    if bands and grid is not None:
        out["seedbed_bands"] = {k: v.reshape((grid.ny, grid.nx)) for k, v in bands.items()}


def _pct(a, q):
    a = np.asarray(a, float)
    return float(np.nanpercentile(a, q)) if np.isfinite(a).any() else None


def write_agroclimate(config: FieldYearConfig, result: dict, grid: Grid | None) -> dict:
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"L5_{config.field_id}_{config.year}"
    files = {}
    p = out / f"{stem}_agroclimate.json"
    p.write_text(json.dumps(result["summary"], indent=2, default=str))
    files["agroclimate"] = str(p)
    if "daily" in result:
        cols = ["gdd", "gdd_cum", "soil_gdd", "soil_gdd_cum", "par_mj_m2", "clearness_index"]
        d = out / f"{stem}_agroclimate_daily.csv"
        result["daily"][[c for c in cols if c in result["daily"]]].to_csv(d, index_label="date")
        files["agroclimate_daily"] = str(d)
    if "seedbed_bands" in result and grid is not None:
        try:
            from ..l4.io import write_cog

            b = result["seedbed_bands"]
            path = out / f"{stem}_seedbed.tif"
            files["seedbed"] = str(write_cog(b, grid, config.crs, path, names=tuple(b)))
        except ImportError:
            pass
    if "rue_bands" in result and grid is not None:
        try:
            from ..l4.io import write_cog

            names = ("rue_g_mj", "ipar_mj_m2", "biomass_g_m2")
            files["rue"] = str(
                write_cog(
                    result["rue_bands"], grid, config.crs, out / f"{stem}_rue.tif", names=names
                )
            )
        except ImportError:
            pass
    return files
