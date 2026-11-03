import numpy as np
import pytest

from yieldsuite.l2 import (
    delayed_positions,
    effective_area,
    estimate_delay,
    header_position,
    record_polygons,
    yield_full_swath,
    yield_t_ha,
)


def test_yield_units():
    # 2 kg/s at 1.5 m/s with a 6 m header: 2 / 9 kg/m2 = 2.22 t/ha
    assert yield_full_swath(2.0, 1.5, 6.0) == pytest.approx(10 * 2 / 9)
    assert np.isnan(yield_t_ha(1.0, 0.0))


def test_header_position_heading_north_and_east():
    x, y = header_position(0.0, 0.0, 0.0, forward_m=2.0, right_m=1.0)
    assert (x, y) == pytest.approx((1.0, 2.0))
    x, y = header_position(0.0, 0.0, np.pi / 2, forward_m=2.0, right_m=1.0)
    assert (x, y) == pytest.approx((2.0, -1.0))


def test_delayed_positions():
    t = np.arange(10.0)
    x = t * 1.5
    px, _ = delayed_positions(t, x, np.zeros(10), tau_d_s=2.0)
    assert np.isnan(px[0]) and np.isnan(px[1])
    assert px[5] == pytest.approx(4.5)


def test_record_polygons_and_effective_area():
    # Two records driving north, the second overlaps the first by half its length.
    polys = record_polygons(
        x=[0.0, 0.0], y=[10.0, 15.0], heading=[0.0, 0.0], length_m=10.0, width_m=6.0
    )
    assert polys[0].area == pytest.approx(60.0)
    eff = effective_area(polys)
    assert eff == pytest.approx([60.0, 30.0])


def test_estimate_delay_finds_minimum():
    best, values = estimate_delay(range(5, 25), lambda tau: (tau - 13) ** 2)
    assert best == 13
    assert len(values) == 20
