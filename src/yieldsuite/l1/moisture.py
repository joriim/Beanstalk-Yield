"""Moisture standardisation (UNL EC2004 yield equation)."""

from __future__ import annotations

import numpy as np

MOISTURE_SENSOR_RANGE_PCT = (10.0, 33.0)


def to_reference_moisture(mass, moisture_pct, reference_pct: float = 14.0):
    """Mass (or mass flow) at reference moisture: m * (100 - MC) / (100 - MC_ref)."""
    mass = np.asarray(mass, dtype=float)
    mc = np.asarray(moisture_pct, dtype=float)
    return mass * (100.0 - mc) / (100.0 - reference_pct)


def to_dry_matter(mass, moisture_pct):
    """Dry-matter mass: m * (100 - MC) / 100."""
    return np.asarray(mass, dtype=float) * (100.0 - np.asarray(moisture_pct, dtype=float)) / 100.0


def valid_moisture(
    moisture_pct, lo: float = MOISTURE_SENSOR_RANGE_PCT[0], hi: float = MOISTURE_SENSOR_RANGE_PCT[1]
):
    """True where moisture lies inside the sensor's valid range."""
    mc = np.asarray(moisture_pct, dtype=float)
    return (mc >= lo) & (mc <= hi)
