"""Record filters. Each returns a boolean mask (True = flag the record).

Combine them into the bitmask with ``yieldsuite.schema.set_flag``.
"""

from __future__ import annotations

import numpy as np

from ..l1.moisture import valid_moisture


def flag_global_outliers(yield_t_ha, k: float = 3.0):
    """Outside median +/- k * MAD (MAD scaled by 1.4826 to match SD for normal data)."""
    y = np.asarray(yield_t_ha, dtype=float)
    med = np.nanmedian(y)
    mad = 1.4826 * np.nanmedian(np.abs(y - med))
    if mad == 0:
        return ~np.isfinite(y)
    return ~(np.abs(y - med) <= k * mad)


def flag_moisture(moisture_pct, lo: float = 10.0, hi: float = 33.0):
    return ~valid_moisture(moisture_pct, lo, hi)


def flag_speed(
    speed_m_s, t_s=None, vmin: float = 0.3, vmax: float = 4.0, max_accel: float | None = 0.5
):
    """Speed outside machine limits, or |dv/dt| above ``max_accel`` [m/s2]."""
    v = np.asarray(speed_m_s, dtype=float)
    mask = (v < vmin) | (v > vmax) | ~np.isfinite(v)
    if t_s is not None and max_accel is not None and v.size > 1:
        t = np.asarray(t_s, dtype=float)
        accel = np.abs(np.gradient(v, t))
        mask |= accel > max_accel
    return mask


def flag_fill_finish(pass_id, t_s, seconds: float):
    """First and last ``seconds`` of every pass (flow ramps at pass ends).

    Use roughly tau_d + tau_c.
    """
    pid = np.asarray(pass_id)
    t = np.asarray(t_s, dtype=float)
    mask = np.zeros(t.shape, dtype=bool)
    for p in np.unique(pid):
        sel = pid == p
        tp = t[sel]
        mask[sel] = (tp - tp.min() < seconds) | (tp.max() - tp < seconds)
    return mask
