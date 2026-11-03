import numpy as np
import pytest

from yieldsuite.l1 import (
    LoadSamples,
    estimate_tare,
    fit_flow_curve,
    flow_from_force,
    normalise_force,
    to_dry_matter,
    to_reference_moisture,
    valid_moisture,
)
from yieldsuite.l1.calibration import load_residuals_pct


def test_reference_moisture():
    # 1000 kg at 20 % moisture -> 930.2 kg at 14 %
    assert to_reference_moisture(1000, 20, 14) == pytest.approx(1000 * 80 / 86)
    assert to_dry_matter(1000, 20) == pytest.approx(800)
    assert list(valid_moisture([5, 15, 40])) == [False, True, False]


def test_tare_and_normalise():
    force = np.array([5.0, 5.2, 4.8, 50.0, 60.0])
    up = np.array([True, True, True, False, False])
    tare = estimate_tare(force, up)
    assert tare == pytest.approx(5.0)
    x = normalise_force(force, tare, elevator_speed=np.full(5, 20.0), reference_speed=40.0)
    assert x[3] == pytest.approx(90.0)


def test_fit_flow_curve_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    true = np.array([0.08, 0.0004])
    loads = []
    for level in (20, 40, 60, 80):  # different steady flow rates
        x = level + rng.normal(0, 2, 300)
        dt = np.ones_like(x)
        loads.append(LoadSamples(x=x, dt=dt, scale_mass_kg=float(np.sum(flow_from_force(x, true)))))
    coeffs = fit_flow_curve(loads, degree=2)
    np.testing.assert_allclose(coeffs, true, rtol=1e-6)
    assert np.all(np.abs(load_residuals_pct(loads, coeffs)) < 1e-6)


def test_fit_flow_curve_needs_enough_loads():
    with pytest.raises(ValueError):
        fit_flow_curve([LoadSamples(np.ones(3), np.ones(3), 1.0)], degree=2)
