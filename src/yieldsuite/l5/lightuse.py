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

Yield prediction and gap filling use light, not a statistical image model. Three
predictors, all fitted on the reliable measured cells:

- ``ipar``: Y = RUE x IPAR with one field RUE (median of Y / IPAR): yield follows
  intercepted light.
- ``gpp``: Y = k x GPP: as above with the daily temperature limit f(T), so light
  caught on cold days counts less.
- ``rue``: Y = RUE(x) x IPAR with a RUE that varies over the field: the
  distance-weighted RUE of the measured cells around each cell (Gaussian, sigma
  ``rue_sigma_m``). It keeps what light cannot see (soil, water, lodging).

Kriging of the residuals then adds what the neighbours know. ``fill: auto`` compares
them in block cross-validation, next to the field mean (no model) and, for reference,
the statistical image model, and uses the best of the three. When the best two are
within ``min_gain`` there is no clear winner: ``prefer`` is used and the model card
says so, so the user can pick (``fill: ipar | gpp | rue | image``).

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
    enabled: bool = True  # needs crop.sowing_date; without it the image model is used
    fill: str = "auto"  # auto (best of ipar, gpp, rue) | ipar | gpp | rue | image
    prefer: str = "gpp"  # used by auto when there is no clear winner
    rue_sigma_m: float = 30.0  # smoothing distance of the local RUE
    eps_g_c_mj: float = 1.8  # light-use efficiency for GPP (C3 crops ~1.5-2.5)
    t_min_c: float = 0.0
    t_opt_low_c: float = 15.0
    t_opt_high_c: float = 25.0
    t_max_c: float = 35.0
    par_fraction: float = 0.48
    cv_block_cells: int = 5
    min_gain: float = 0.02  # CV RMSE difference (share) below which there is no clear winner


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


LIGHT_MODELS = ("ipar", "gpp", "rue")


def local_ratio(y, x, fit, xy, sigma_m: float, targets=None) -> np.ndarray:
    """Distance-weighted Y / X of the ``fit`` cells around every cell (or ``targets``).

    Cells with no fit cell within 3 sigma get the median ratio of the fit cells.
    """
    from scipy.spatial import cKDTree

    y, x = np.asarray(y, float), np.asarray(x, float)
    fit = np.asarray(fit, bool) & np.isfinite(y) & np.isfinite(x) & (x > 0)
    out = np.full(y.shape, np.nan)
    if fit.sum() < 3:
        return out
    r = y[fit] / x[fit]
    lo, hi = np.percentile(r, [2, 98])
    r = np.clip(r, lo, hi)  # single cells must not dominate their surroundings
    tgt = np.ones(y.shape, bool) if targets is None else np.asarray(targets, bool)
    tree = cKDTree(xy[fit])
    idx = np.flatnonzero(tgt)
    k = min(64, int(fit.sum()))
    d, j = tree.query(xy[idx], k=k, distance_upper_bound=3 * sigma_m)
    d, j = d.reshape(len(idx), -1), j.reshape(len(idx), -1)
    ok = np.isfinite(d)
    w = np.where(ok, np.exp(-0.5 * (d / sigma_m) ** 2), 0.0)
    rr = np.where(ok, r[np.where(ok, j, 0)], 0.0)
    ws = w.sum(axis=1)
    out[idx] = np.where(ws > 0, (w * rr).sum(axis=1) / np.where(ws > 0, ws, 1), np.median(r))
    return out


def predict(name: str, y, ipar, gpp, fit, xy, s: LightUseSettings, targets=None) -> np.ndarray:
    """Yield predicted by one light-use model fitted on the ``fit`` cells."""
    x = gpp if name == "gpp" else ipar
    x = np.asarray(x, float)
    if name == "rue":
        return local_ratio(y, x, fit, xy, s.rue_sigma_m, targets) * x
    ok = np.asarray(fit, bool) & np.isfinite(y) & np.isfinite(x) & (x > 0)
    if ok.sum() < 3:
        return np.full(x.shape, np.nan)
    return float(np.median(np.asarray(y, float)[ok] / x[ok])) * x


def compare_models(y, ipar, gpp, train, rows, cols, xy, s: LightUseSettings) -> dict:
    """Block cross-validation of the light-use models and of the field mean."""
    y = np.asarray(y, float)
    out = {}
    b = _blocks(rows, cols, s.cv_block_cells)
    for name in (*LIGHT_MODELS, "mean"):
        x = gpp if name == "gpp" else ipar
        ok = np.asarray(train, bool) & np.isfinite(y) & np.isfinite(x) & (np.asarray(x) > 0)
        if ok.sum() < 20:
            out[name] = {"cv_rmse": None, "n": int(ok.sum())}
            continue
        err = np.full(y.shape, np.nan)
        for blk in np.unique(b[ok]):
            test, fit = ok & (b == blk), ok & (b != blk)
            if fit.sum() < 10:
                continue
            if name == "mean":
                err[test] = y[test] - y[fit].mean()
            else:
                err[test] = y[test] - predict(name, y, ipar, gpp, fit, xy, s, test)[test]
        e = err[np.isfinite(err)]
        res = {
            "cv_rmse": float(np.sqrt(np.mean(e**2))) if e.size else None,
            "cv_bias": float(np.mean(e)) if e.size else None,
            "n": int(ok.sum()),
        }
        if name != "mean":
            xx = np.asarray(x, float)
            res["ratio"] = float(np.median(y[ok] / xx[ok]))
            res["corr"] = float(np.corrcoef(y[ok], xx[ok])[0, 1]) if name != "rue" else None
        out[name] = res
    return out


def choose_fill(image_cv_rmse, candidates: dict, s: LightUseSettings) -> tuple[str, str, bool]:
    """Pick the predictor; returns (name, reason, clear_winner)."""
    if s.fill in (*LIGHT_MODELS, "image"):
        return s.fill, "chosen by the user (light_use.fill)", True
    ranked = sorted(
        ((c["cv_rmse"], n) for n, c in candidates.items()
         if n in LIGHT_MODELS and c.get("cv_rmse") is not None)
    )
    if not ranked:
        return "image", "light-use models could not be cross-validated; image model used", True
    best_rmse, best = ranked[0]
    if len(ranked) > 1 and ranked[1][0] - best_rmse < s.min_gain * best_rmse:
        close = [n for r, n in ranked if r - best_rmse < s.min_gain * best_rmse]
        pick = s.prefer if s.prefer in close else best
        return (
            pick,
            f"no clear winner: {', '.join(close)} are within {100 * s.min_gain:.0f} % of each "
            f"other in block CV (best {best_rmse:.2f} t/ha); '{pick}' used "
            f"({'the preferred model' if pick == s.prefer else 'lowest error'}). "
            "Set light_use.fill to choose",
            False,
        )
    return best, f"lowest block-CV error of the light-use models ({best_rmse:.2f} t/ha)", True
