"""Season table: air and soil temperature, growing degree days, radiation.

Daily, from sowing to harvest:

- ``tair_c``: mean air temperature (FMI station or ERA5)
- ``tsoil_c``: soil temperature near the surface. ERA5 ``soil_temperature_0_to_7cm``
  when the weather frame has it; otherwise modelled from air temperature as a
  damped, lagged response (first-order filter, time constant ``tau_days``),
  which is how a 5-10 cm soil layer follows the daily air mean. The source is
  reported, and a modelled soil temperature should be read as an estimate.
- ``gdd_air`` / ``gdd_soil``: daily max(T - base, 0); cumulative sums from sowing
- ``rad_mj_m2`` and ``par_mj_m2``: global radiation and PAR

The cumulative sums tell how far the crop got (e.g. a spring cereal needs
roughly 1 000-1 200 degree days above 5 C from sowing to yellow ripeness);
soil degree days drive emergence and early root growth.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

SOIL_COLUMNS = ("soil_temperature_0_to_7cm", "soil_temperature_7_to_28cm")


def model_soil_temperature(tair: pd.Series, tau_days: float = 3.0, start=None) -> pd.Series:
    """First-order lag of daily air temperature: T_s(d) = T_s(d-1) + (T_a(d) - T_s(d-1)) / tau."""
    a = tair.astype(float).interpolate(limit=3).to_numpy()
    out = np.empty_like(a)
    s = a[0] if start is None or not np.isfinite(start) else float(start)
    for i, v in enumerate(a):
        if np.isfinite(v):
            s = s + (v - s) / max(tau_days, 1.0)
        out[i] = s
    return pd.Series(out, index=tair.index)


def season_table(
    weather: pd.DataFrame,
    sowing: date,
    harvest: date,
    t_base_air: float = 5.0,
    t_base_soil: float = 5.0,
    par_fraction: float = 0.48,
    tau_days: float = 3.0,
) -> tuple[pd.DataFrame, dict]:
    w = weather.copy()
    w.index = pd.to_datetime(pd.Index(w.index))
    if "temperature_2m_mean" in w:
        tair = w["temperature_2m_mean"]
    elif {"temperature_2m_max", "temperature_2m_min"} <= set(w.columns):
        tair = (w["temperature_2m_max"] + w["temperature_2m_min"]) / 2
    else:
        raise ValueError("weather has no air temperature")
    soil_col = next((c for c in SOIL_COLUMNS if c in w and w[c].notna().any()), None)
    if soil_col:
        tsoil = w[soil_col].astype(float)
        soil_source = f"ERA5 {soil_col}"
    else:
        tsoil = model_soil_temperature(tair, tau_days)
        soil_source = f"modelled from air temperature (first-order lag, tau {tau_days:g} d)"
    days = pd.date_range(sowing, harvest, freq="D")
    t = pd.DataFrame(
        {
            "tair_c": tair.reindex(days),
            "tsoil_c": tsoil.reindex(days),
            "rad_mj_m2": w["shortwave_radiation_sum"].reindex(days)
            if "shortwave_radiation_sum" in w
            else np.nan,
        },
        index=days,
    )
    t["par_mj_m2"] = t["rad_mj_m2"] * par_fraction
    t["gdd_air"] = np.maximum(t["tair_c"] - t_base_air, 0)
    t["gdd_soil"] = np.maximum(t["tsoil_c"] - t_base_soil, 0)
    t["gdd_air_cum"] = t["gdd_air"].cumsum()
    t["gdd_soil_cum"] = t["gdd_soil"].cumsum()
    t.index.name = "date"
    summary = {
        "sowing": sowing.isoformat(),
        "harvest": harvest.isoformat(),
        "days": int(len(t)),
        "tair_mean_c": float(t["tair_c"].mean()),
        "tsoil_mean_c": float(t["tsoil_c"].mean()),
        "gdd_air": float(t["gdd_air"].sum()),
        "gdd_soil": float(t["gdd_soil"].sum()),
        "t_base_air_c": t_base_air,
        "t_base_soil_c": t_base_soil,
        "radiation_mj_m2": float(t["rad_mj_m2"].sum()),
        "par_mj_m2": float(t["par_mj_m2"].sum()),
        "soil_temperature_source": soil_source,
        "missing_days": {c: int(t[c].isna().sum()) for c in ("tair_c", "tsoil_c", "rad_mj_m2")},
    }
    return t, summary


def figure(table: pd.DataFrame, summary: dict, path, image_dates=(), lang: str = "fi"):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fi = lang == "fi"
    fig, ax = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    ax[0].plot(table.index, table["tair_c"], label="ilma" if fi else "air", color="tab:red")
    ax[0].plot(table.index, table["tsoil_c"], label="maa" if fi else "soil", color="tab:brown")
    ax[0].set_ylabel("°C")
    ax[0].legend(loc="upper left")
    src = summary["soil_temperature_source"]
    if fi and src.startswith("modelled"):
        src = "mallinnettu ilman lämpötilasta"
    ax[0].set_title(("Lämpötila (maa: " if fi else "Temperature (soil: ") + src + ")")
    ax[1].plot(
        table.index,
        table["gdd_air_cum"],
        color="tab:red",
        label=f"{'ilma' if fi else 'air'} {summary['gdd_air']:.0f} °Cd",
    )
    ax[1].plot(
        table.index,
        table["gdd_soil_cum"],
        color="tab:brown",
        label=f"{'maa' if fi else 'soil'} {summary['gdd_soil']:.0f} °Cd",
    )
    ax[1].set_ylabel("°Cd")
    ax[1].legend(loc="upper left")
    ax[1].set_title(f"{'Lämpösumma' if fi else 'Degree days'} (> {summary['t_base_air_c']:g} °C)")
    ax[2].bar(table.index, table["rad_mj_m2"], color="goldenrod", width=1.0)
    ax[2].set_ylabel("MJ/m²/d")
    ax[2].set_title(
        f"{'Säteily' if fi else 'Global radiation'} {summary['radiation_mj_m2']:.0f} MJ/m² "
        f"(PAR {summary['par_mj_m2']:.0f})"
    )
    for d in image_dates:
        for a in ax:
            a.axvline(pd.Timestamp(d), color="0.6", lw=0.6, ls=":")
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path
