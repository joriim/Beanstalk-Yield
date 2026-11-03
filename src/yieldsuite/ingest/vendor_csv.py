"""Generic vendor CSV export with mass flow already computed (entry level L1B or L2).

Column names are mapped in the config, e.g.::

    columns: {time: Time, x: Easting, y: Northing, mass_flow: "Mass Flow (kg/s)",
              moisture: Moisture, swath: Swath, speed: Speed, header: Header}

Positions are either ``x``/``y`` (field CRS, or ``position_crs``) or
``lon``/``lat`` (WGS84). Time is ISO-8601 text or seconds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import HarvestLog

MASS_FLOW_UNITS = {"kg/s": 1.0, "t/h": 1000.0 / 3600.0}
SPEED_UNITS = {"m/s": 1.0, "km/h": 1.0 / 3.6}


def _project(a, b, src_crs: str, dst_crs: str):
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("projecting positions needs pyproj: pip install yieldsuite[geo]") from exc
    tr = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return tr.transform(np.asarray(a, float), np.asarray(b, float))


def read_vendor_csv(log: HarvestLog, crs: str) -> pd.DataFrame:
    raw = pd.read_csv(log.path)
    col = {
        name: log.columns.get(name, name)
        for name in (
            "time",
            "x",
            "y",
            "lon",
            "lat",
            "mass_flow",
            "moisture",
            "swath",
            "speed",
            "heading",
            "header",
            "gnss_quality",
        )
    }

    def has(name):
        return col[name] in raw.columns

    def get(name, default=np.nan):
        return raw[col[name]].to_numpy(dtype=float) if has(name) else np.full(len(raw), default)

    for required in ("time", "mass_flow", "swath"):
        if not has(required):
            raise ValueError(
                f"{log.path}: no column for {required!r} (looked for {col[required]!r})"
            )

    tcol = raw[col["time"]]
    if pd.api.types.is_numeric_dtype(tcol):
        t = tcol.to_numpy(dtype=float)
    else:
        t = pd.to_datetime(tcol, utc=True).astype("int64").to_numpy() / 1e9

    if has("x") and has("y"):
        x, y = get("x"), get("y")
        if log.position_crs and log.position_crs.upper() != crs.upper():
            x, y = _project(x, y, log.position_crs, crs)
    elif has("lon") and has("lat"):
        x, y = _project(get("lon"), get("lat"), "EPSG:4326", crs)
    else:
        raise ValueError(f"{log.path}: needs x/y or lon/lat columns")

    if log.mass_flow_unit not in MASS_FLOW_UNITS:
        raise ValueError(f"mass_flow_unit must be one of {sorted(MASS_FLOW_UNITS)}")
    if log.speed_unit not in SPEED_UNITS:
        raise ValueError(f"speed_unit must be one of {sorted(SPEED_UNITS)}")
    heading = get("heading")
    if log.heading_unit == "deg":
        heading = np.deg2rad(heading)

    swath = get("swath")
    header = get("header", default=1.0)
    return pd.DataFrame(
        {
            "t": t,
            "machine_id": log.machine_id,
            "x": np.asarray(x, float),
            "y": np.asarray(y, float),
            "mass_flow_wet": get("mass_flow") * MASS_FLOW_UNITS[log.mass_flow_unit],
            "moisture": get("moisture"),
            "swath": swath,
            "speed": get("speed") * SPEED_UNITS[log.speed_unit],
            "heading": heading,
            "header_down": (header > 0) & (swath > 0),
            "gnss_quality": get("gnss_quality"),
            "entry_level": log.entry_level,
        }
    )
