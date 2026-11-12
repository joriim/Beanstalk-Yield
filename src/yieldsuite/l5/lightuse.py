"""Light use per cell (IPAR, GPP, RUE) and gap filling from it.

Per cell and day d between sowing and harvest:

    fAPAR_d  = 1.1638 NDVI_d - 0.1426 (clipped 0..0.95), interpolated between
               Sentinel-2 dates (Myneni & Williams 1994; see weather.rue)
    APAR_d   = PAR_d x fAPAR_d                                   [MJ/m2]
    IPAR     = sum_d APAR_d                                       [MJ/m2]
    GPP      = sum_d eps x f(T_d) x APAR_d                        [g C/m2]

f(T) is a trapezoid: 0 below ``t_min``, 1 between ``t_opt_low`` and
``t_opt_high``, 0 above ``t_max``. ``eps`` (g C per MJ APAR) sets the GPP
level only; the fill below uses ratios, so it does not change filled yields.

Gap filling: on reliable cells, the yield per unit of light (RUE_y = Y / IPAR,
or Y / GPP) varies far less than yield itself. Its field median times the
cell's IPAR (or GPP) predicts the yield of an empty cell; kriging of the
residuals then adds what the neighbours know. The predictor (image model,
IPAR or GPP) with the lowest block cross-validation error fills the gaps;
the comparison is written to the model card.

PAR comes from ERA5 (Open-Meteo). When no weather is available, a clear-sky
PAR (extraterrestrial radiation x 0.75 x PAR fraction) is used and flagged:
it keeps day length and sun angle but not clouds, and without temperature
f(T) = 1, so GPP then carries no information beyond IPAR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..weather.agro import extraterrestrial_radiation
from ..weather.rue import _daily_fapar, fapar_from_ndvi


@dataclass
class LightUseSettings:
    enabled: bool = False
    fill: str = "auto"  # auto | image | ipar | gpp
    eps_g_c_mj: float = 1.8  # light-use efficiency for GPP (C3 crops ~1.5-2.5)
    t_min_c: float = 0.0
    t_opt_low_c: float = 15.0
    t_opt_high_c: float = 25.0
    t_max_c: float = 35.0
    par_fraction: float = 0.48
    cv_block_cells: int = 5
    min_gain: float = 0.02  # a light-use fill must beat the image model by 2 % CV RMSE


def temperature_scalar(t, s: LightUseSettings) -> np.ndarray:
    t = np.asarray(t, float)
    up = np.clip((t - s.t_min_c) / max(s.t_opt_low_c - s.t_min_c, 1e-9), 0, 1)
    down = np.clip((s.t_max_c - t) / max(s.t_max_c - s.t_opt_high_c, 1e-9), 0, 1)
    return np.where(np.isfinite(t), np.minimum(up, down), 1.0)


def clear_sky_par(lat: float, days: pd.DatetimeIndex, par_fraction: float = 0.48) -> pd.Series:
    """Clear-sky PAR [MJ/m2/day]: extraterrestrial radiation x 0.75 x PAR fraction."""
    ra = extraterrestrial_radiation(lat, days.dayofyear.to_numpy())
    return pd.Series(np.asarray(ra) * 0.75 * par_fraction, index=days)


def light_use(
    ndvi_wide: pd.DataFrame,
    par_daily: pd.Series,
    sowing: date,
    harvest: date,
    s: LightUseSettings,
    tmean_daily: pd.Series | None = None,
) -> dict:
    """IPAR and GPP per cell. ``ndvi_wide``: rows = observation dates, columns = cells."""
    days = pd.date_range(sowing, harvest, freq="D")
    par = pd.Series(par_daily.values, index=pd.to_datetime(pd.Index(par_daily.index))).reindex(days)
    if par.isna().any():
        raise ValueError(f"PAR missing for {int(par.isna().sum())} days between sowing and harvest")
    nd = ndvi_wide.copy()
    nd.index = pd.to_datetime(nd.index).date
    obs = sorted(d for d in nd.index if sowing < d < harvest)
    if len(obs) < 2:
        raise ValueError("fewer than two image dates between sowing and harvest")
    f = fapar_from_ndvi(nd.loc[obs].to_numpy(dtype=float))
    daily = _daily_fapar(obs, f, days, sowing, harvest)  # (n_days, n_cells)
    apar = par.to_numpy()[:, None] * daily
    if tmean_daily is not None:
        t = pd.Series(tmean_daily.values, index=pd.to_datetime(pd.Index(tmean_daily.index)))
        ft = temperature_scalar(t.reindex(days).to_numpy(), s)
    else:
        ft = np.ones(len(days))
    with np.errstate(invalid="ignore"):
        return {
            "ipar_mj_m2": apar.sum(axis=0),
            "gpp_g_c_m2": (s.eps_g_c_mj * ft[:, None] * apar).sum(axis=0),
            "dates_used": [d.isoformat() for d in obs],
        }


def _blocks(rows, cols, size: int) -> np.ndarray:
    return (np.asarray(rows) // size) * 100_000 + (np.asarray(cols) // size)


def ratio_cv(y, x, train, rows, cols, block: int) -> dict:
    """Block cross-validation of y ~ median(y / x) * x on the training cells."""
    y, x = np.asarray(y, float), np.asarray(x, float)
    ok = np.asarray(train, bool) & np.isfinite(y) & np.isfinite(x) & (x > 0)
    if ok.sum() < 20:
        return {"cv_rmse": None, "ratio": None, "n": int(ok.sum())}
    b = _blocks(rows, cols, block)
    err = []
    for blk in np.unique(b[ok]):
        test = ok & (b == blk)
        fit = ok & (b != blk)
        if fit.sum() < 10:
            continue
        r = np.median(y[fit] / x[fit])
        err.append(y[test] - r * x[test])
    e = np.concatenate(err) if err else np.array([])
    ratio = float(np.median(y[ok] / x[ok]))
    pred = ratio * x
    ss_res = np.nansum((y[ok] - pred[ok]) ** 2)
    ss_tot = np.nansum((y[ok] - y[ok].mean()) ** 2)
    return {
        "ratio": ratio,
        "cv_rmse": float(np.sqrt(np.mean(e**2))) if e.size else None,
        "cv_bias": float(np.mean(e)) if e.size else None,
        "r2": float(1 - ss_res / ss_tot) if ss_tot > 0 else None,
        "corr": float(np.corrcoef(y[ok], x[ok])[0, 1]),
        "n": int(ok.sum()),
    }


def choose_fill(image_cv_rmse, candidates: dict, s: LightUseSettings) -> tuple[str, str]:
    """Pick the gap-fill predictor; returns (name, reason)."""
    if s.fill in ("image", "ipar", "gpp"):
        return s.fill, "set in the config"
    best, best_rmse = "image", image_cv_rmse
    for name, c in candidates.items():
        r = c.get("cv_rmse")
        if r is not None and best_rmse is not None and r < best_rmse * (1 - s.min_gain):
            best, best_rmse = name, r
    if best == "image":
        return best, "the image model has the lowest block-CV error"
    return best, f"block-CV RMSE {best_rmse:.2f} t/ha beats the image model ({image_cv_rmse:.2f})"
