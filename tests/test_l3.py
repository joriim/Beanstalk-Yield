import numpy as np
import pytest

from yieldsuite.l3 import (
    apply_reconciliation,
    flag_fill_finish,
    flag_global_outliers,
    flag_moisture,
    flag_speed,
    reconciliation_factor,
)
from yieldsuite.schema import Flag, is_clean, set_flag


def test_reconciliation():
    k = reconciliation_factor([10_000, 12_000], [10_500, 11_500])
    assert k == pytest.approx(1.0)
    out = apply_reconciliation([5.0, 6.0], ["a", "b"], {"a": 1.1, "b": 0.9})
    np.testing.assert_allclose(out, [5.5, 5.4])
    with pytest.raises(KeyError):
        apply_reconciliation([1.0], ["c"], {"a": 1.0})


def test_global_outliers():
    y = np.r_[np.full(50, 8.0) + np.linspace(-0.5, 0.5, 50), 30.0, 0.1]
    mask = flag_global_outliers(y)
    assert mask[-2] and mask[-1] and not mask[:50].any()


def test_speed_moisture_fill_finish():
    assert list(flag_speed([0.1, 1.5, 6.0], max_accel=None)) == [True, False, True]
    assert list(flag_moisture([9, 20, 35])) == [True, False, True]
    t = np.arange(20.0)
    mask = flag_fill_finish(np.zeros(20), t, seconds=3)
    assert mask[:3].all() and mask[-3:].all() and not mask[3:17].any()


def test_flag_bitmask():
    flags = np.zeros(3, dtype=np.int32)
    flags = set_flag(flags, [True, False, False], Flag.SPEED)
    flags = set_flag(flags, [True, True, False], Flag.MOISTURE)
    assert flags[0] == Flag.SPEED | Flag.MOISTURE
    assert list(is_clean(flags)) == [False, False, True]
