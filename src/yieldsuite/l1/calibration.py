"""Impact-plate flow calibration.

Model (design doc, L0 -> L1):
    x_k   = (F_k - F_0) / (omega_e / omega_ref)
    mdot_k = g(x_k) * (1 + beta * alpha_k),   g(x) = sum_{j=1..J} a_j x^j

The coefficients a_j are fitted so that the integrated mass of each weighed
load matches its scale weight. Because g is linear in a, this is an ordinary
least-squares problem with one row per load.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass
class LoadSamples:
    """Samples belonging to one weighed calibration load."""

    x: np.ndarray  # normalised force per sample
    dt: np.ndarray  # sample durations [s]
    scale_mass_kg: float
    pitch_rad: np.ndarray | None = None


def estimate_tare(force, header_up) -> float:
    """Tare F_0: median force while the elevator runs empty (header up)."""
    force = np.asarray(force, dtype=float)
    mask = np.asarray(header_up, dtype=bool)
    if not mask.any():
        raise ValueError("no header-up samples to estimate tare from")
    return float(np.median(force[mask]))


def normalise_force(force, tare: float, elevator_speed=None, reference_speed: float | None = None):
    """Tare-corrected force, divided by relative elevator speed if given."""
    x = np.asarray(force, dtype=float) - tare
    if elevator_speed is not None:
        if reference_speed is None:
            raise ValueError("reference_speed is required with elevator_speed")
        x = x / (np.asarray(elevator_speed, dtype=float) / reference_speed)
    return x


def flow_from_force(x, coefficients: Sequence[float], pitch_rad=None, beta: float = 0.0):
    """Mass flow [kg/s] from normalised force using g(x) = sum a_j x^j (j >= 1)."""
    x = np.asarray(x, dtype=float)
    mdot = np.zeros_like(x)
    for j, a in enumerate(coefficients, start=1):
        mdot += a * x**j
    if pitch_rad is not None and beta:
        mdot *= 1.0 + beta * np.asarray(pitch_rad, dtype=float)
    return mdot


def fit_flow_curve(loads: Sequence[LoadSamples], degree: int = 2, beta: float = 0.0) -> np.ndarray:
    """Fit a_1..a_J to weighed loads by least squares on integrated load mass.

    Extension guidance is 3-6 loads at different, steady flow rates; at least
    ``degree`` loads are required.
    """
    if len(loads) < degree:
        raise ValueError(f"need at least {degree} loads for a degree-{degree} curve")
    A = np.zeros((len(loads), degree))
    b = np.zeros(len(loads))
    for i, load in enumerate(loads):
        x = np.asarray(load.x, dtype=float)
        w = np.asarray(load.dt, dtype=float)
        if load.pitch_rad is not None and beta:
            w = w * (1.0 + beta * np.asarray(load.pitch_rad, dtype=float))
        for j in range(1, degree + 1):
            A[i, j - 1] = np.sum(x**j * w)
        b[i] = load.scale_mass_kg
    coeffs, *_ = np.linalg.lstsq(A, b, rcond=None)
    return coeffs


def load_residuals_pct(loads: Sequence[LoadSamples], coefficients: Sequence[float]) -> np.ndarray:
    """Per-load error of integrated mass vs scale weight, in percent."""
    out = []
    for load in loads:
        m = np.sum(flow_from_force(load.x, coefficients) * np.asarray(load.dt, dtype=float))
        out.append(100.0 * (m - load.scale_mass_kg) / load.scale_mass_kg)
    return np.asarray(out)
