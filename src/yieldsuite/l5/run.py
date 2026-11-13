"""Run the Sentinel-2 L5 step for a field-year config: fetch (or reuse) features, fuse, write."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import FieldYearConfig
from ..l4.grid import Grid
from .fuse import FusionResult, fuse
from .indices import DEFAULT_INDICES
from .raster_features import raster_features
from .sentinel2 import S2Request, fetch_features, load_features, save_features


@dataclass
class L5Outputs:
    result: FusionResult
    raster: Path | None
    model_card: Path
    features: Path


def s2_request(config: FieldYearConfig) -> S2Request:
    src = config.sentinel2
    if src is None:
        raise ValueError("config has no imagery entry with kind: sentinel2")
    return S2Request(
        start=src.start or f"{config.year}-04-15",
        end=src.end or f"{config.year}-09-15",
        indices=tuple(src.indices or DEFAULT_INDICES),
        max_cloud_cover=src.max_cloud_cover,
        backend=src.backend,
    )


def run_sentinel2_fusion(
    config: FieldYearConfig,
    l4: dict,
    grid: Grid,
    features_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    method: str = "linear",
    residual: str = "kriging",
    batch: bool = False,
    connection=None,
    write_raster: bool = True,
    records=None,
    polygons=None,
) -> L5Outputs:
    request = s2_request(config) if config.sentinel2 is not None else None
    out = Path(out_dir or config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"L5_{config.field_id}_{config.year}"

    parts = []
    remapped = False
    if request is not None:
        features_path = Path(features_path) if features_path else out / f"{stem}_s2_features.csv"
        s2 = _cached_features(features_path, grid, config.crs)
        if s2 is None:
            try:
                s2 = fetch_features(
                    grid, config.crs, request, connection=connection, workdir=out, batch=batch
                )
            except Exception:
                s2 = _remap_stale_features(features_path, grid)
                if s2 is None:
                    raise
                # keep the moved features, so later runs and the gap-fill story find them;
                # cells outside the earlier grid have no Sentinel-2 features
                save_features(s2, features_path)
                _grid_sidecar(features_path).write_text(json.dumps(_grid_signature(grid)))
                remapped = True
            else:
                save_features(s2, features_path)
                _grid_sidecar(features_path).write_text(json.dumps(_grid_signature(grid)))
        parts.append(s2.assign(source="sentinel2"))
    local_reports = []
    for img in config.imagery:
        if img.kind not in ("drone", "planetscope"):
            continue
        df, rep = raster_features(
            img.path,
            grid,
            config.crs,
            img.date,
            source=img.kind,
            sensor=img.sensor or ("mavic3m" if img.kind == "drone" else "superdove"),
            bands=img.bands or None,
            scale=img.scale,
            mask_path=img.mask,
            mask_clear_band=img.mask_clear_band,
        )
        parts.append(df)
        local_reports.append(rep)
    if not parts:
        raise ValueError("no imagery configured (sentinel2, drone or planetscope)")
    features = pd.concat(parts, ignore_index=True)
    min_valid_share = config.sentinel2.min_valid_share if config.sentinel2 else 0.8

    corrections = _record_corrections(
        l4, grid, features, records, polygons, method, min_valid_share
    )
    if corrections.get("l4") is not None:
        l4 = corrections.pop("l4")
    else:
        corrections.pop("l4", None)

    field_mask = None
    if config.boundary is not None and Path(config.boundary).exists():
        from ..public.boundary import load_boundary
        from .fuse import boundary_mask

        field_mask = boundary_mask(load_boundary(config.boundary, config.crs), grid)
    vendor = {
        img.kind
        for img in config.imagery
        if img.kind == "planetscope"
        and (
            img.harmonised if img.harmonised is not None else "harmonized" in str(img.path).lower()
        )
    }
    result = fuse(
        l4,
        grid,
        features,
        method=method,
        residual=residual,
        min_valid_share=min_valid_share,
        request=request,
        field_mask=field_mask,
        vendor_harmonised=vendor,
    )
    if remapped:
        result.model_card["sentinel2_features_remapped"] = (
            "Sentinel-2 could not be fetched for the new grid: features moved from the earlier "
            "grid; cells outside it have no Sentinel-2 features"
        )
    result.model_card.update(
        field_id=config.field_id,
        year=config.year,
        crs=config.crs,
        product_class=config.product_class.value,
    )
    result.model_card["record_corrections"] = corrections
    result.model_card["local_imagery"] = local_reports
    lightuse_raster = None
    has_sowing = config.crop is not None and bool(config.crop.sowing_date)
    result.model_card["prediction_model"] = "image"
    if config.light_use.enabled and not has_sowing:
        result.model_card["prediction_note"] = (
            "crop.sowing_date is missing: IPAR/GPP/RUE need it; the statistical image "
            "model predicts and fills instead"
        )
    if config.light_use.enabled and has_sowing:
        try:
            lu_card, lu_bands = apply_light_use(config, grid, l4, result, features, records)
            tbl = lu_card.pop("_season_table", None)
            img_dates = lu_card.pop("_image_dates", [])
            if tbl is not None:
                tbl.to_csv(out / f"{stem}_season.csv")
                lu_card["season"]["table"] = str(out / f"{stem}_season.csv")
                try:
                    from ..weather.season import figure as season_figure

                    lu_card["season"]["figure"] = str(
                        season_figure(tbl, lu_card["season"], out / f"{stem}_season.png", img_dates)
                    )
                except ImportError:
                    pass
            result.model_card["light_use"] = lu_card
            result.model_card["prediction_model"] = lu_card["fill_used"]
            if write_raster:
                from ..l4.io import write_cog

                lightuse_raster = write_cog(
                    lu_bands, grid, config.crs, out / f"{stem}_lightuse.tif", names=tuple(lu_bands)
                )
                lu_card["raster"] = str(lightuse_raster)
        except Exception as exc:  # noqa: BLE001 - L5 stays usable, but the failure is loud
            _problem(config, result, "light_use", exc,
                     "light-use models not applied: the statistical image model was used")
    if records is not None:
        try:
            result.model_card["review"] = run_review(
                config, grid, l4, result, records, field_mask, lightuse_raster, out, stem
            )
        except Exception as exc:  # noqa: BLE001 - L5 stays usable, but the failure is loud
            _problem(config, result, "review", exc, "critical review missing")
    card_path = out / f"{stem}_model_card.json"
    card_path.write_text(json.dumps(result.model_card, indent=2, default=str))

    # Agroclimate and RUE: only when the crop calendar is known; a weather
    # failure (e.g. no network) is reported, not fatal for L5.
    if config.crop is not None and config.crop.sowing_date:
        try:
            from ..weather.run import run_agroclimate, write_agroclimate

            agro = run_agroclimate(config, grid, l5_bands=result.bands, features_long=features)
            result.model_card["agroclimate"] = agro["summary"]
            result.model_card["agroclimate_files"] = write_agroclimate(config, agro, grid)
        except Exception as exc:  # noqa: BLE001
            _problem(config, result, "agroclimate", exc, "agroclimate summary missing")
        card_path.write_text(json.dumps(result.model_card, indent=2, default=str))

    raster = None
    if write_raster:
        from ..l4.io import write_cog
        from ..schema import L5_BANDS

        raster = write_cog(result.bands, grid, config.crs, out / f"{stem}.tif", names=L5_BANDS)
    return L5Outputs(result, raster, card_path, features_path)


def _problem(config, result, step: str, exc: Exception, consequence: str) -> None:
    """A failed optional step: recorded in the model card (``problems``), printed, and
    raised when ``strict`` is set (config ``strict: true`` or YIELDSUITE_STRICT=1)."""
    import os
    import sys

    if getattr(config, "strict", False) or os.environ.get("YIELDSUITE_STRICT") == "1":
        raise exc
    err = f"{type(exc).__name__}: {exc}"[:400]
    entry = {"step": step, "error": err, "consequence": consequence}
    result.model_card.setdefault("problems", []).append(entry)
    result.model_card[f"{step}_error"] = str(exc)
    print(f"WARNING: L5 step '{step}' failed: {entry['error']} -> {consequence}", file=sys.stderr)


def _record_corrections(l4, grid, features, records, polygons, method, min_valid_share) -> dict:
    """Corrections 1-2 of the design document, when L3 records are available."""
    if records is None or polygons is None:
        return {
            "applied": False,
            "reason": "L3 records not available (L5 run from an L4 raster only)",
        }
    import numpy as np

    from ..l4.grid import apportion_mass, check_mass_conservation
    from .records import RecordView
    from .shift import residual_shift
    from .stripes import level_pass_stripes

    # Image prediction f(V) as the spatial reference: fitted on the L4 grid,
    # without kriging (the residual field would carry the stripes back in).
    vendor = (
        {"planetscope"}
        if "source" in features and (features["source"] == "planetscope").any()
        else set()
    )
    prelim = fuse(
        l4,
        grid,
        features,
        method=method,
        residual="none",
        min_valid_share=min_valid_share,
        vendor_harmonised=vendor,
    )
    reference = prelim.bands["prediction"]
    view = RecordView.from_records(records)
    out: dict = {"applied": True, "residual_shift": residual_shift(view, grid, reference)}
    corrected, stripes = level_pass_stripes(view, grid, reference)
    out["pass_stripes"] = stripes
    if stripes.get("applied"):
        use = view.area > 0
        bands = apportion_mass(
            np.asarray(polygons, dtype=object)[use],
            corrected[use],
            grid,
            flags=view.flags[use],
            pass_ids=view.pass_key[use],
        )
        clean_total = float(corrected[use & (view.flags == 0)].sum())
        stripes["mass_conserved"] = check_mass_conservation(bands["mass"], clean_total)
        stripes["field_total_kg"] = {
            "before": float(view.mass.sum()),
            "after": float(corrected.sum()),
        }
        out["l4"] = bands
    return out


def _harvest_date(config: FieldYearConfig, records) -> date:
    if config.crop is not None and config.crop.harvest_date:
        return date.fromisoformat(config.crop.harvest_date)
    if records is not None and len(records):
        t = pd.to_datetime(records["t"].max(), unit="s", utc=True).tz_convert("Europe/Helsinki")
        return t.date()
    raise ValueError("light use needs crop.harvest_date or harvest records")


def apply_light_use(config: FieldYearConfig, grid: Grid, l4: dict, result, features, records):
    """IPAR, GPP and yield-based RUE per cell; refill the L5 gaps with the light-use
    predictor when it beats the image model in block cross-validation."""
    import numpy as np

    from ..weather.agro import daily_indices
    from ..weather.run import agro_settings, field_location, get_weather
    from .fuse import cell_centres
    from .gapfill import SOURCE_FILLED, gap_fill
    from .lightuse import (
        LIGHT_MODELS,
        choose_fill,
        clear_sky_par,
        compare_models,
        light_use,
        predict,
    )

    s = config.light_use
    if config.crop is None or not config.crop.sowing_date:
        raise ValueError("light use needs crop.sowing_date")
    sowing = date.fromisoformat(config.crop.sowing_date)
    harvest = _harvest_date(config, records)
    days = pd.date_range(sowing, harvest, freq="D")
    try:
        weather, prov, (lat, lon) = get_weather(config, grid)
        di = daily_indices(weather, lat, agro_settings(config, harvest))
        par = di["par_mj_m2"]
        if "temperature_2m_mean" in weather:
            tmean = weather["temperature_2m_mean"]
        elif {"temperature_2m_max", "temperature_2m_min"} <= set(weather.columns):
            tmean = (weather["temperature_2m_max"] + weather["temperature_2m_min"]) / 2
        else:
            tmean = None
        par_source = prov.get("source", "Open-Meteo ERA5")
        season = (weather, prov)
    except Exception as exc:  # noqa: BLE001 - no weather: clear-sky fallback, flagged
        lat, _ = field_location(config, grid)
        par = clear_sky_par(lat, days, s.par_fraction)
        tmean = None
        season = None
        par_source = f"clear-sky model (weather unavailable: {str(exc)[:120]})"

    nd = features[features["index"] == "NDVI"]
    if nd.empty:
        raise ValueError("light use needs NDVI from Sentinel-2")
    n = grid.ny * grid.nx
    wide = nd.pivot_table(index="date", columns="cell", values="value").reindex(columns=range(n))
    lu = light_use(wide, par, sowing, harvest, s, tmean)
    ipar, gpp = lu["ipar_mj_m2"], lu["gpp_g_c_m2"]

    y4 = np.asarray(l4["yield"], float).ravel()
    cov = np.asarray(l4.get("coverage", np.isfinite(l4["yield"])), float).ravel()
    reliable = np.isfinite(y4) & (cov >= 0.5)
    if "flagged_share" in l4:
        reliable &= ~(np.asarray(l4["flagged_share"], float).ravel() > 0.5)
    rows, cols, xy = cell_centres(grid)
    cands = compare_models(y4, ipar, gpp, reliable, rows, cols, xy, s)
    image_cv = result.model_card.get("fit", {}).get("cv_rmse")
    choice, reason, clear = choose_fill(image_cv, cands, s)

    bands = result.bands
    shape = (grid.ny, grid.nx)
    src = bands["source"].ravel()
    out_y = bands["yield"].ravel().copy()
    replaced = 0
    lu_pred = np.full(n, np.nan)
    if choice in LIGHT_MODELS:
        lu_pred = predict(choice, y4, ipar, gpp, reliable, xy, s)
        filled = gap_fill(
            y4,
            lu_pred,
            reliable & np.isfinite(lu_pred),
            xy,
            cands[choice]["cv_rmse"],
            grid.resolution,
            residual="kriging",
        )
        # the light-use model replaces the image model: prediction, fill and uncertainty
        target = src == SOURCE_FILLED
        out_y[target] = filled["yield"][target]
        sigma = bands["sigma"].ravel().copy()
        sigma[target] = filled["sigma"][target]
        fm = np.isfinite(bands["prediction"].ravel()) | np.isfinite(bands["yield"].ravel())
        newly = fm & ~np.isfinite(bands["yield"].ravel()) & np.isfinite(filled["yield"])
        out_y[newly] = filled["yield"][newly]
        sigma[newly] = filled["sigma"][newly]
        src = src.copy()
        src[newly] = SOURCE_FILLED
        bands["source"] = src.reshape(shape)
        bands["sigma"] = sigma.reshape(shape)
        bands["image_prediction"] = bands["prediction"].copy()
        bands["prediction"] = np.where(fm, lu_pred, np.nan).reshape(shape)
        replaced = int(target.sum() + newly.sum())
        bands["yield"] = out_y.reshape(shape)
        with np.errstate(invalid="ignore"):
            bands["relative_yield"] = (100.0 * out_y / np.nanmean(out_y)).reshape(shape)

    inside = np.isfinite(out_y)
    with np.errstate(divide="ignore", invalid="ignore"):
        rue = np.where(inside & (ipar > 1), out_y * 100.0 / ipar, np.nan)  # g grain / MJ
    card = {
        "par_source": par_source,
        "temperature": "ERA5 daily mean" if tmean is not None else "not available: f(T) = 1",
        "sowing_date": sowing.isoformat(),
        "harvest_date": harvest.isoformat(),
        "image_dates": len(lu["dates_used"]),
        "eps_g_c_mj": s.eps_g_c_mj,
        "field_median": {
            "ipar_mj_m2": float(np.nanmedian(ipar[inside])),
            "gpp_g_c_m2": float(np.nanmedian(gpp[inside])),
            "rue_g_grain_mj": float(np.nanmedian(rue[inside])),
        },
        "fill_candidates": {"image": {"cv_rmse": image_cv}, **cands},
        "fill_used": choice,
        "fill_reason": reason,
        "clear_winner": clear,
        "prediction_model": choice,
        "cells_refilled": replaced,
        "note": "RUE here is grain (at reference moisture) per MJ intercepted PAR; "
        "sowing date and eps scale IPAR/GPP but not the ratio-based fill.",
    }
    if season is not None:
        from ..weather.season import season_table

        tbl, summ = season_table(
            season[0],
            sowing,
            harvest,
            t_base_air=config.crop.t_base_c,
            t_base_soil=config.crop.t_base_c,
            par_fraction=s.par_fraction,
        )
        summ["weather_source"] = par_source
        if season[1].get("station_distance_km") is not None:
            summ["station_distance_km"] = season[1]["station_distance_km"]
        if season[1].get("validation"):
            summ["radiation_validation"] = season[1]["validation"]
        card["season"] = summ
        card["_season_table"] = tbl
        card["_image_dates"] = lu["dates_used"]
    lu_bands = {
        "ipar_mj_m2": np.where(inside, ipar, np.nan).reshape(shape),
        "gpp_g_c_m2": np.where(inside, gpp, np.nan).reshape(shape),
        "rue_g_grain_mj": rue.reshape(shape),
        "lightuse_prediction": np.where(inside, lu_pred, np.nan).reshape(shape),
    }
    return card, lu_bands


def run_review(config, grid, l4, result, records, field_mask, lightuse_raster, out, stem) -> dict:
    """Critical review of the yield map against the growth prediction (see l5.review)."""
    import numpy as np

    from .review import figure, review

    lu = result.model_card.get("light_use", {})
    pred = result.bands["prediction"]
    ref = "image model"
    if lu.get("fill_used") in ("ipar", "gpp", "rue") and lightuse_raster is not None:
        from ..l4.io import read_bands

        b, _, _ = read_bands(lightuse_raster, require_yield=False)
        pred = np.where(np.isfinite(b["lightuse_prediction"]), b["lightuse_prediction"], pred)
        ref = f"light use ({lu['fill_used'].upper()})"
    boundary = edge = None
    if config.boundary is not None and Path(config.boundary).exists():
        from ..public.boundary import load_boundary
        from .fuse import boundary_mask

        geom = load_boundary(config.boundary, config.crs)
        buf = config.edge_buffer_m if config.edge_buffer_m else 2 * grid.resolution
        inner = boundary_mask(geom.buffer(-max(buf, grid.resolution)), grid)
        edge = ~inner
        try:
            import geopandas as gpd

            boundary = gpd.GeoSeries([geom], crs=config.crs)
        except ImportError:
            boundary = None
    res = review(records, grid, l4, pred, field_mask=field_mask, edge_mask=edge, reference_name=ref)
    arrays = res.pop("_arrays")
    path = out / f"{stem}_review.json"
    path.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    try:
        fig = figure(
            {**res, "_arrays": arrays}, grid, boundary, records, out / f"{stem}_review.png"
        )
        res["figure"] = str(fig)
    except ImportError:
        pass
    res["file"] = str(path)
    unl = next((c for c in res["checks"] if c["id"] == "unlogged"), None)
    hc = config.harvest_check
    if hc.enabled == "true" or (
        hc.enabled == "auto" and unl is not None and unl["severity"] != "ok"
    ):
        try:
            res["harvest_check"] = run_harvest_check(
                config, grid, l4, records, field_mask, out, stem, boundary
            )
            c = res["harvest_check"].pop("_check")
            res["checks"].append(c)
            order = {"alert": 0, "watch": 1, "ok": 2}
            res["checks"].sort(key=lambda c: order[c["severity"]])
            res["summary"] = {s: sum(c["severity"] == s for c in res["checks"]) for s in order}
        except Exception as exc:  # noqa: BLE001 - network or data problem: noted, not fatal
            res["harvest_check_error"] = str(exc)
    path.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return {"file": str(path), "summary": res["summary"], "reference": ref}


def _grid_signature(grid: Grid) -> dict:
    return {"xmin": grid.xmin, "ymax": grid.ymax, "resolution": grid.resolution,
            "nx": grid.nx, "ny": grid.ny}


def _grid_sidecar(features_path: Path) -> Path:
    return features_path.with_suffix(".grid.json")


def _cached_features(features_path: Path, grid: Grid, crs: str):
    """Cached features if they belong to this grid. Cell ids are grid positions, so a
    cache from another grid (e.g. before the grid was extended to the boundary) is
    set aside as ``*.stale.csv`` and None is returned."""
    if not features_path.exists():
        return None
    side = _grid_sidecar(features_path)
    if side.exists() and json.loads(side.read_text()) == _grid_signature(grid):
        return load_features(features_path)
    if not side.exists():
        # legacy cache without a grid record (written before grids were recorded):
        # kept as before when its cell ids fit the grid; delete it after a grid change
        df = load_features(features_path)
        if int(df["cell"].max()) < grid.nx * grid.ny:
            side.write_text(json.dumps(_grid_signature(grid)))
            return df
    stale = features_path.with_suffix(".stale.csv")
    features_path.replace(stale)
    if side.exists():
        side.replace(stale.with_suffix(".grid.json"))
    return None


def _remap_stale_features(features_path: Path, grid: Grid):
    """When features cannot be fetched, move a stale cache onto the new grid (same
    resolution, aligned): cells outside the old grid get no Sentinel-2 features."""
    stale = features_path.with_suffix(".stale.csv")
    side = stale.with_suffix(".grid.json")
    if not stale.exists() or not side.exists():
        return None
    g = json.loads(side.read_text())
    if g["resolution"] != grid.resolution:
        return None
    dc = round((g["xmin"] - grid.xmin) / grid.resolution)
    dr = round((grid.ymax - g["ymax"]) / grid.resolution)
    df = load_features(stale)
    r, c = np.divmod(df["cell"].to_numpy(), g["nx"])
    r, c = r + dr, c + dc
    ok = (r >= 0) & (r < grid.ny) & (c >= 0) & (c < grid.nx)
    return df[ok].assign(cell=(r * grid.nx + c)[ok])


def harvest_window(records) -> tuple[date, date]:
    t = pd.to_datetime(records["t"], unit="s", utc=True).dt.tz_convert("Europe/Helsinki")
    return t.min().date(), t.max().date()


def run_harvest_check(config, grid, l4, records, field_mask, out, stem, boundary=None) -> dict:
    """Sentinel-2/Landsat look at the unlogged area (see l5.harvest_check); writes
    ``<stem>_harvest_check.json`` and ``.png`` and returns the result with a review check."""
    import numpy as np

    from ..public import optical
    from ..public.field import field_bbox_wgs84
    from . import harvest_check as hc

    s = config.harvest_check
    start, end = harvest_window(records)
    fm = np.asarray(field_mask, bool).reshape(grid.ny, grid.nx)
    nrec = np.asarray(l4.get("n_records", np.isfinite(l4["yield"])), float).reshape(fm.shape)
    cov = np.asarray(l4.get("coverage", nrec > 0), float).reshape(fm.shape)
    logged = (nrec > 0) & (cov >= 0.5) & fm
    unlogged = fm & ~(nrec > 0)
    bbox = field_bbox_wgs84(config, grid)
    log: list[str] = []
    scenes = optical.fetch_scenes(
        bbox,
        grid,
        config.crs,
        start - timedelta(days=s.days_before),
        end + timedelta(days=s.days_after),
        families=s.families,
        field_mask=fm,
        log=log.append,
    )
    for img in config.imagery:
        if img.kind == "planetscope" and img.path and img.date:
            try:
                scenes.append(
                    optical.planetscope_scene(img.path, str(img.date), grid, config.crs, img.mask)
                )
            except Exception as exc:  # noqa: BLE001
                log.append(f"{img.path}: {exc}")
    res = hc.check(scenes, grid, fm, logged, unlogged, start, end, s)
    planet = None
    if res["planet_suggested"] and s.planet_suggest:
        cands, err = None, None
        if s.planet_search and config.boundary is not None:
            try:
                from shapely.geometry import mapping

                from ..public.boundary import load_boundary

                geom = load_boundary(config.boundary, "EPSG:4326")
                cands = hc.search_planet(mapping(geom), end)
            except Exception as exc:  # noqa: BLE001 - no key, quota or network
                err = str(exc)[:120]
        planet = hc.planet_suggestion(end, float(fm.sum() * grid.cell_area / 1e4), cands, err)
        res["planet"] = planet
    sev, fi, en = hc.finding(res, planet)
    a = res["area_ha_by_verdict"]
    value = 100 * a.get(hc.STANDING, 0) / res["unlogged_ha"] if res["unlogged_ha"] else 0.0
    classes = res.pop("_classes")
    res["log"] = log
    res["licences"] = sorted({optical.LICENCE[sc.family] for sc in scenes})
    try:
        res["figure"] = str(
            hc.figure({**res, "_classes": classes}, scenes, grid, boundary,
                      out / f"{stem}_harvest_check.png", field_mask=fm, unlogged=unlogged)
        )
    except ImportError:
        pass
    path = out / f"{stem}_harvest_check.json"
    path.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    res["file"] = str(path)
    res["_check"] = {
        "id": "harvest_status",
        "value": float(value),
        "unit": "% of unlogged area looks unharvested",
        "thresholds": {"watch": None, "alert": None},
        "severity": sev,
        "finding": {"fi": fi, "en": en},
        "file": str(path),
    }
    return res


def harvest_check_from_outputs(config: FieldYearConfig) -> dict:
    """Run the harvest check on existing L3/L4 outputs and merge it into the review JSON."""
    import numpy as np

    from ..l4.io import read_bands

    out = Path(config.output_dir)
    base = f"{config.field_id}_{config.year}"
    l4, grid, _ = read_bands(out / f"L4_{base}.tif")
    records = pd.read_parquet(out / f"L3_{base}_records.parquet", columns=["t"])
    boundary = None
    if config.boundary is not None and Path(config.boundary).exists():
        from ..public.boundary import load_boundary
        from .fuse import boundary_mask

        geom = load_boundary(config.boundary, config.crs)
        fm = boundary_mask(geom, grid)
        try:
            import geopandas as gpd

            boundary = gpd.GeoSeries([geom], crs=config.crs)
        except ImportError:
            pass
    else:
        fm = np.isfinite(l4["yield"])
    stem = f"L5_{base}"
    res = run_harvest_check(config, grid, l4, records, fm, out, stem, boundary)
    check = res.pop("_check")
    rv_path = out / f"{stem}_review.json"
    if rv_path.exists():
        rv = json.loads(rv_path.read_text())
        rv["checks"] = [c for c in rv["checks"] if c["id"] != "harvest_status"] + [check]
        order = {"alert": 0, "watch": 1, "ok": 2}
        rv["checks"].sort(key=lambda c: order[c["severity"]])
        rv["summary"] = {k: sum(c["severity"] == k for c in rv["checks"]) for k in order}
        rv["harvest_check"] = res
        rv_path.write_text(json.dumps(rv, indent=2, ensure_ascii=False, default=str))
    return {**res, "check": check}
