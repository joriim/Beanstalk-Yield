"""Gap filling by regression kriging.

    Y_hat(x0) = f(V(x0)) + sum_i lambda_i r(x_i),
    sigma^2(x0) = sigma_f^2 + sigma_OK^2(x0)

Cells with good coverage keep their measured yield. Cells with low coverage
(or only flagged records) get the prediction, are labelled as filled, and are
never used to train f.
"""

from __future__ import annotations

import warnings

import numpy as np

SOURCE_MEASURED = 1
SOURCE_FILLED = 2  # predicted by the model (+ kriged residuals)
SOURCE_INTERPOLATED = 3  # narrow gap: kriged from the measured cells around it
SOURCE_NONE = 0


def _krige_residuals(xy_train, r, xy_target, max_dist, resolution, max_points, seed):
    try:
        import gstools as gs
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("residual kriging needs gstools: pip install yieldsuite[fusion]") from exc

    rng = np.random.default_rng(seed)
    if len(r) > max_points:
        pick = rng.choice(len(r), max_points, replace=False)
        xy_train, r = xy_train[pick], r[pick]
    x, y = xy_train[:, 0], xy_train[:, 1]
    sample_var = float(np.var(r)) or 1e-12
    model = gs.Exponential(dim=2, var=sample_var, len_scale=max_dist / 4)
    try:
        edges = np.linspace(0, max_dist / 2, 16)
        centres, gamma = gs.vario_estimate((x, y), r, edges)
        ok = np.isfinite(gamma)
        model.set_arg_bounds(
            var=[1e-12, 5 * sample_var],
            len_scale=[resolution, max_dist],
            nugget=[0, 5 * sample_var],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit_variogram(centres[ok], gamma[ok], nugget=True)
    except Exception:  # noqa: BLE001 - keep the fixed fallback variogram
        model = gs.Exponential(dim=2, var=sample_var, len_scale=max_dist / 4)
    krig = gs.krige.Ordinary(model, cond_pos=(x, y), cond_val=r)
    field, var = krig((xy_target[:, 0], xy_target[:, 1]), mesh_type="unstructured", return_var=True)
    variogram = {
        "model": "exponential",
        "var": float(model.var),
        "len_scale_m": float(model.len_scale),
        "nugget": float(model.nugget),
    }
    return np.asarray(field), np.maximum(np.asarray(var), 0.0), variogram


def gap_fill(
    yield_obs,
    prediction,
    train_mask,
    xy,
    sigma_f: float,
    resolution: float,
    residual: str = "kriging",
    max_points: int = 2000,
    seed: int = 0,
) -> dict:
    """Combine measured yield and model prediction for every cell.

    All arrays are flat (one value per cell). ``xy`` holds cell centres (n, 2).
    Returns flat arrays ``yield``, ``source``, ``sigma``, ``residual`` and a
    ``variogram`` dict (or None).
    """
    y = np.asarray(yield_obs, dtype=float)
    pred = np.asarray(prediction, dtype=float)
    train = np.asarray(train_mask, dtype=bool) & np.isfinite(y) & np.isfinite(pred)
    target = ~train & np.isfinite(pred)
    xy = np.asarray(xy, dtype=float)

    out_y = np.where(train, y, np.nan)
    sigma = np.full(y.shape, np.nan)
    resid_field = np.zeros(y.shape)
    variogram = None

    if target.any():
        if residual == "kriging" and train.sum() >= 10:
            extent = xy[train].max(axis=0) - xy[train].min(axis=0)
            max_dist = float(max(np.hypot(*extent), 3 * resolution))
            rk, rvar, variogram = _krige_residuals(
                xy[train],
                y[train] - pred[train],
                xy[target],
                max_dist,
                resolution,
                max_points,
                seed,
            )
            resid_field[target] = rk
            sigma[target] = np.sqrt(sigma_f**2 + rvar)
        elif residual in ("kriging", "none"):
            sigma[target] = sigma_f
        else:
            raise ValueError(f"unknown residual method {residual!r}")
        out_y[target] = pred[target] + resid_field[target]

    source = np.full(y.shape, SOURCE_NONE, dtype=np.int8)
    source[train] = SOURCE_MEASURED
    source[target] = SOURCE_FILLED
    return {
        "yield": out_y,
        "source": source,
        "sigma": sigma,
        "residual": resid_field,
        "variogram": variogram,
    }


def narrow_gaps(measured: np.ndarray, field: np.ndarray, resolution: float, width_m: float):
    """Field cells without a measured value that lie in gaps at most ``width_m`` wide
    (edge strips, a missed pass, single holes). 2-D boolean masks in, 2-D mask out.

    A gap is narrow where a disk of radius width/2 does not fit into it: morphological
    opening of the gap mask removes exactly those parts. Land outside the field does not
    count as gap, so an unlogged strip along the boundary is narrow too.
    """
    from scipy import ndimage

    gap = np.asarray(field, bool) & ~np.asarray(measured, bool)
    k = int(np.ceil(width_m / (2.0 * resolution)))
    if k < 1 or not gap.any():
        return np.zeros_like(gap)
    yy, xx = np.ogrid[-k : k + 1, -k : k + 1]
    disk = (xx * xx + yy * yy) <= k * k
    wide = ndimage.binary_opening(gap, structure=disk)
    return gap & ~wide


def interpolate_narrow(y, measured, narrow, xy, resolution: float, seed: int = 0) -> dict:
    """Kriging of the measured cell values into the narrow-gap cells (flat arrays).

    The data are cell (block) means on the grid and the targets are cells of the same
    size, so this is block-to-block kriging; the field mean of the measured cells is the
    trend. Returns ``yield`` and ``sigma`` (kriging standard deviation) for the narrow cells.
    """
    y = np.asarray(y, float)
    measured = np.asarray(measured, bool) & np.isfinite(y)
    narrow = np.asarray(narrow, bool)
    out = {"yield": np.full(y.shape, np.nan), "sigma": np.full(y.shape, np.nan), "n": 0}
    if not narrow.any() or measured.sum() < 10:
        return out
    mean = float(y[measured].mean())
    sd = float(y[measured].std())
    pred = np.where(measured | narrow, mean, np.nan)
    res = gap_fill(y, pred, measured, xy, sd, resolution, residual="kriging", seed=seed)
    out["yield"][narrow] = res["yield"][narrow]
    out["sigma"][narrow] = res["sigma"][narrow]
    out["n"] = int(np.isfinite(out["yield"]).sum())
    return out
