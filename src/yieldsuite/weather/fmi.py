"""Daily weather from FMI open data (Finnish Meteorological Institute, CC BY 4.0).

FMI measures global radiation at only a handful of stations, too far apart for
field work, but synoptic stations report cloud cover (``n_man``, oktas) and
temperature every hour. Daily global radiation is therefore modelled at the
nearest synoptic station:

    GHI_clear = 1098 cos(Z) exp(-0.057 / cos(Z))           Haurwitz (1945) [W/m2]
    GHI       = GHI_clear x (1 - 0.75 (N/8)^3.4)           Kasten & Czeplak (1980)
    Rs_day    = k x sum_h GHI x 3600 / 1e6                  [MJ/m2/day]

``k`` corrects the model's bias; it is estimated from a station that has both
cloud cover and measured global radiation (Jokioinen by default) over the
same period, and the validation (bias, RMSE, r) is kept in the provenance.

Output columns match the Open-Meteo frame used elsewhere:
``shortwave_radiation_sum`` [MJ/m2] and ``temperature_2m_mean`` [deg C].
"""

from __future__ import annotations

import math
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..public import http

WFS = "https://opendata.fmi.fi/wfs"
NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "BsWfs": "http://xml.fmi.fi/schema/wfs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
}
VALIDATION_FMISID = "101104"  # Jokioinen Ilmala: cloud cover and global radiation
CHUNK_DAYS = 7


def _get(params: dict, opener=None) -> bytes:
    q = urllib.parse.urlencode(
        {"service": "WFS", "version": "2.0.0", "request": "getFeature", **params}
    )
    req = urllib.request.Request(f"{WFS}?{q}", headers={"User-Agent": http.USER_AGENT})
    with (opener or urllib.request.urlopen)(req, timeout=120) as r:  # noqa: S310 - fixed host
        return r.read()


def parse_simple(xml: bytes) -> pd.DataFrame:
    """BsWfs simple features -> time, lat, lon, parameter, value."""
    root = ET.fromstring(xml)
    rows = []
    for el in root.iter(f"{{{NS['BsWfs']}}}BsWfsElement"):
        pos = el.find(".//gml:pos", NS).text.split()
        rows.append(
            (
                el.find("BsWfs:Time", NS).text,
                float(pos[0]),
                float(pos[1]),
                el.find("BsWfs:ParameterName", NS).text,
                float(el.find("BsWfs:ParameterValue", NS).text),
            )
        )
    df = pd.DataFrame(rows, columns=["time", "lat", "lon", "parameter", "value"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def fetch_hourly(
    query: str, where: dict, parameters: str, start: date, end: date, opener=None
) -> pd.DataFrame:
    """Hourly values in weekly requests (FMI caps the time span per request)."""
    parts = []
    t = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    stop = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    while t < stop:
        t1 = min(t + timedelta(days=CHUNK_DAYS), stop)
        xml = _get(
            {
                "storedquery_id": query,
                **where,
                "parameters": parameters,
                "starttime": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endtime": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timestep": 60,
            },
            opener,
        )
        parts.append(parse_simple(xml))
        t = t1
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return df.drop_duplicates(["time", "lat", "lon", "parameter"])


def nearest_station(df: pd.DataFrame, lat: float, lon: float) -> tuple[float, float, float]:
    """(lat, lon, distance km) of the station with cloud data closest to the field."""
    st = df[df["parameter"] == "n_man"].groupby(["lat", "lon"]).size().reset_index(name="n")
    if st.empty:
        raise ValueError("no FMI station with cloud cover in the search area")
    st["km"] = [
        6371
        * math.acos(
            min(
                1.0,
                math.sin(math.radians(lat)) * math.sin(math.radians(a))
                + math.cos(math.radians(lat))
                * math.cos(math.radians(a))
                * math.cos(math.radians(b - lon)),
            )
        )
        for a, b in zip(st["lat"], st["lon"], strict=True)
    ]
    st = st[st["n"] >= 0.5 * st["n"].max()].sort_values("km")
    r = st.iloc[0]
    return float(r["lat"]), float(r["lon"]), float(r["km"])


def _cos_zenith(times: pd.DatetimeIndex, lat: float, lon: float) -> np.ndarray:
    """Cosine of the solar zenith angle at the middle of each hour (UTC times)."""
    t = times.shift(30, freq="min")
    doy = t.dayofyear.to_numpy()
    hour = t.hour.to_numpy() + t.minute.to_numpy() / 60
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (
        0.006918
        - 0.399912 * np.cos(g)
        + 0.070257 * np.sin(g)
        - 0.006758 * np.cos(2 * g)
        + 0.000907 * np.sin(2 * g)
        - 0.002697 * np.cos(3 * g)
        + 0.00148 * np.sin(3 * g)
    )
    eqt = 229.18 * (
        0.000075
        + 0.001868 * np.cos(g)
        - 0.032077 * np.sin(g)
        - 0.014615 * np.cos(2 * g)
        - 0.040849 * np.sin(2 * g)
    )
    solar_time = hour * 60 + eqt + 4 * lon
    ha = np.radians(solar_time / 4 - 180)
    phi = math.radians(lat)
    return np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(ha)


def modelled_daily_radiation(hourly: pd.DataFrame, lat: float, lon: float) -> pd.Series:
    """Daily global radiation [MJ/m2] from hourly cloud cover at one station."""
    n = hourly[hourly["parameter"] == "n_man"].set_index("time")["value"].sort_index()
    if n.empty:
        raise ValueError("no cloud cover values")
    idx = pd.date_range(n.index.min().floor("D"), n.index.max().ceil("D"), freq="h", tz="UTC")
    n = n.reindex(idx).interpolate(limit=6).clip(0, 8)
    cz = np.clip(_cos_zenith(idx, lat, lon), 0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        clear = np.where(cz > 0.01, 1098 * cz * np.exp(-0.057 / np.maximum(cz, 1e-3)), 0.0)
    ghi = clear * (1 - 0.75 * (n.to_numpy() / 8) ** 3.4)
    s = pd.Series(ghi * 3600 / 1e6, index=idx.tz_convert("Europe/Helsinki"))
    daily = s.groupby(s.index.date).sum(min_count=18)
    daily.index = pd.to_datetime(daily.index)
    return daily


def measured_daily_radiation(hourly: pd.DataFrame) -> pd.Series:
    g = hourly[hourly["parameter"] == "GLOB_1MIN"].set_index("time")["value"].sort_index()
    g = g.clip(lower=0)
    s = g * 3600 / 1e6
    s.index = s.index.tz_convert("Europe/Helsinki")
    daily = s.groupby(s.index.date).sum(min_count=20)
    daily.index = pd.to_datetime(daily.index)
    return daily


def validate(start: date, end: date, opener=None) -> dict:
    """Model vs measured global radiation at the validation station; returns k and stats."""
    where = {"fmisid": VALIDATION_FMISID}
    clouds = fetch_hourly("fmi::observations::weather::simple", where, "n_man", start, end, opener)
    rad = fetch_hourly(
        "fmi::observations::radiation::simple", where, "GLOB_1MIN", start, end, opener
    )
    lat, lon = float(clouds["lat"].iloc[0]), float(clouds["lon"].iloc[0])
    m = modelled_daily_radiation(clouds, lat, lon)
    o = measured_daily_radiation(rad)
    both = pd.concat({"model": m, "obs": o}, axis=1).dropna()
    if len(both) < 10:
        raise ValueError("too few days to validate the radiation model")
    k = float(both["obs"].sum() / both["model"].sum())
    err = both["obs"] - k * both["model"]
    return {
        "station": "Jokioinen Ilmala",
        "fmisid": VALIDATION_FMISID,
        "days": int(len(both)),
        "k": k,
        "rmse_mj_m2": float(np.sqrt(np.mean(err**2))),
        "mean_obs_mj_m2": float(both["obs"].mean()),
        "r": float(np.corrcoef(both["obs"], both["model"])[0, 1]),
    }


def fetch_daily(
    lat: float, lon: float, start: date, end: date, opener=None
) -> tuple[pd.DataFrame, dict]:
    """Daily radiation and mean temperature for a field at (lat, lon)."""
    box = f"{lon - 0.6},{lat - 0.4},{lon + 0.6},{lat + 0.4}"
    probe_end = min(start + timedelta(days=2), end)
    probe = fetch_hourly(
        "fmi::observations::weather::simple", {"bbox": box}, "n_man", start, probe_end, opener
    )
    slat, slon, km = nearest_station(probe, lat, lon)
    sbox = f"{slon - 0.01},{slat - 0.01},{slon + 0.01},{slat + 0.01}"
    hourly = fetch_hourly(
        "fmi::observations::weather::simple", {"bbox": sbox}, "n_man,t2m", start, end, opener
    )
    hourly = hourly[(hourly["lat"] == slat) & (hourly["lon"] == slon)]
    val = validate(start, end, opener)
    rs = modelled_daily_radiation(hourly, slat, slon) * val["k"]
    t = hourly[hourly["parameter"] == "t2m"].set_index("time")["value"].sort_index()
    t.index = t.index.tz_convert("Europe/Helsinki")
    tday = t.groupby(t.index.date).mean()
    tday.index = pd.to_datetime(tday.index)
    df = pd.concat({"shortwave_radiation_sum": rs, "temperature_2m_mean": tday}, axis=1)
    df = df.loc[str(start) : str(end)]
    df.index.name = "date"
    prov = {
        "source": "FMI open data (CC BY 4.0)",
        "station_lat_lon": [slat, slon],
        "station_distance_km": round(km, 1),
        "radiation": "cloud cover -> Haurwitz clear sky x Kasten-Czeplak, scaled by k",
        "validation": val,
        "request": {
            "lat": round(lat, 2),
            "lon": round(lon, 2),
            "start": str(start),
            "end": str(end),
        },
    }
    return df, prov
