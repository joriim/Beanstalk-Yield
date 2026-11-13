"""Covariate plugins: rasters from other packages, kept outside this repository.

A plugin is any installed Python package that registers a provider under the
entry-point group ``yieldsuite.covariates``::

    [project.entry-points."yieldsuite.covariates"]
    omamaatu = "omamaatu.yieldsuite_liitannainen:TurveSelittaja"

The suite never imports a plugin by name and ships none of its code, models or
data. That keeps plugins with their own licences (for example a model derived
from data that may not be redistributed) out of this open repository: the
farmer or adviser installs the plugin next to the suite, and its output lands
in their own data folder with the plugin's licence recorded beside it.

Provider contract (duck-typed, version ``API_VERSION``)::

    class Provider:
        api_version = 1
        name = "omamaatu"                    # short id
        description = "Peat probability ..."  # one line
        licence = "..."                       # shown in provenance and reports
        version = "0.2.0"

        def layers(self, boundary, crs: str) -> dict:
            # boundary: shapely geometry of the field in ``crs``
            return {
                "bands": {"peat_p30": 2-D array, ...},   # float, NaN = no value
                "transform": (a, b, c, d, e, f),          # rasterio affine order
                "crs": "EPSG:3067",
                "notes": ["..."],                         # optional
                "meta": {...},                            # optional, JSON-serialisable
            }

Band values are averaged onto the suite grid by cell area (``to_grid``), so a
50 m plugin raster becomes a 10 m or 20 m covariate for zones and models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import entry_points
from pathlib import Path

import numpy as np

from .l4.grid import Grid

GROUP = "yieldsuite.covariates"
API_VERSION = 1


class PluginError(RuntimeError):
    pass


@dataclass
class PluginInfo:
    name: str
    target: str  # "module:attr"
    distribution: str | None


@dataclass
class CovariateLayers:
    bands: dict[str, np.ndarray]
    transform: tuple
    crs: str
    notes: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def available() -> list[PluginInfo]:
    """Installed covariate plugins (without importing them)."""
    out = []
    for ep in entry_points(group=GROUP):
        dist = getattr(ep, "dist", None)
        out.append(PluginInfo(ep.name, ep.value, dist.name if dist else None))
    return sorted(out, key=lambda p: p.name)


def load(name: str):
    """Instantiate the provider registered as ``name``."""
    matches = [ep for ep in entry_points(group=GROUP) if ep.name == name]
    if not matches:
        names = ", ".join(p.name for p in available()) or "none installed"
        raise PluginError(f"no covariate plugin {name!r} ({names})")
    obj = matches[0].load()
    provider = obj() if isinstance(obj, type) else obj
    v = getattr(provider, "api_version", None)
    if v != API_VERSION:
        raise PluginError(f"plugin {name!r} speaks API {v}, the suite {API_VERSION}")
    for attr in ("name", "licence", "layers"):
        if not hasattr(provider, attr):
            raise PluginError(f"plugin {name!r} has no {attr!r}")
    return provider


def fetch(provider, boundary, crs: str) -> CovariateLayers:
    try:
        raw = provider.layers(boundary, crs)
    except Exception as exc:  # noqa: BLE001 - plugin code: report, do not crash the suite
        raise PluginError(f"plugin {provider.name!r} failed: {exc}") from exc
    try:
        bands = {str(k): np.asarray(v, dtype="float64") for k, v in raw["bands"].items()}
        layers = CovariateLayers(
            bands=bands,
            transform=tuple(raw["transform"]),
            crs=str(raw["crs"]),
            notes=list(raw.get("notes", [])),
            meta=dict(raw.get("meta", {})),
        )
    except (KeyError, TypeError) as exc:
        raise PluginError(f"plugin {provider.name!r} returned no valid layers: {exc}") from None
    shapes = {a.shape for a in layers.bands.values()}
    if len(shapes) != 1 or any(len(s) != 2 for s in shapes):
        raise PluginError(f"plugin {provider.name!r}: bands must be 2-D and of equal shape")
    return layers


def to_grid(layers: CovariateLayers, grid: Grid, crs: str) -> dict[str, np.ndarray]:
    """Area-weighted mean of every band on the suite grid (reprojected if needed)."""
    from rasterio.transform import Affine
    from rasterio.warp import Resampling, reproject

    out = {}
    for name, src in layers.bands.items():
        dst = np.full((grid.ny, grid.nx), np.nan)
        reproject(
            source=src,
            destination=dst,
            src_transform=Affine(*layers.transform),
            src_crs=layers.crs,
            src_nodata=np.nan,
            dst_transform=Affine(*grid.transform),
            dst_crs=crs,
            dst_nodata=np.nan,
            resampling=Resampling.average,
        )
        out[name] = dst
    return out


def write_covariates(
    provider, layers: CovariateLayers, grid: Grid, crs: str, out_dir, stem: str | None = None
) -> dict:
    """GeoTIFF on the suite grid (usable with ``yieldsuite zones --covariate``) and a
    provenance JSON with the plugin, its version and licence."""
    from .l4.io import write_cog

    stem = stem or f"covariate_{provider.name}"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bands = to_grid(layers, grid, crs)
    names = tuple(bands)
    tif = write_cog(bands, grid, crs, out / f"{stem}.tif", names=names)
    prov = {
        "plugin": provider.name,
        "plugin_version": getattr(provider, "version", None),
        "description": getattr(provider, "description", ""),
        "licence": provider.licence,
        "api_version": API_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bands": list(names),
        "source_crs": layers.crs,
        "source_resolution_m": abs(layers.transform[0]),
        "grid_resolution_m": grid.resolution,
        "notes": layers.notes,
        "meta": layers.meta,
    }
    pj = out / f"{stem}.provenance.json"
    pj.write_text(json.dumps(prov, indent=2, ensure_ascii=False, default=str))
    return {"raster": str(tif), "provenance": str(pj), "bands": list(names)}
