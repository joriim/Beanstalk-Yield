"""Yield from mass and area."""

from __future__ import annotations

import numpy as np


def yield_t_ha(mass_kg, area_m2):
    """Y = 10 * m / A  [t/ha] with m in kg and A in m2; NaN where A <= 0."""
    m = np.asarray(mass_kg, dtype=float)
    a = np.asarray(area_m2, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(a > 0, 10.0 * m / a, np.nan)


def yield_full_swath(mass_flow_kg_s, speed_m_s, width_m):
    """Y = 10 * mdot / (v * w) for a full, non-overlapping swath."""
    return yield_t_ha(mass_flow_kg_s, np.asarray(speed_m_s, float) * np.asarray(width_m, float))
