import numpy as np
import pytest
import shapely

from yieldsuite.l2 import record_polygons
from yieldsuite.l4 import Grid, apportion_mass, check_mass_conservation


def test_grid_snaps_to_origin():
    g = Grid.from_bounds((3.0, 7.0, 41.0, 39.0), resolution=20.0)
    assert (g.xmin, g.ymax, g.nx, g.ny) == (0.0, 40.0, 3, 2)


def test_mass_conservation_and_uniform_yield():
    # 4 adjacent north-going passes, 6 m wide, of 1 m records over a 24 x 40 m block.
    xs, ys, pids = [], [], []
    for p in range(4):
        for k in range(1, 41):
            xs.append(3.0 + 6.0 * p)
            ys.append(float(k))
            pids.append(p)
    polys = record_polygons(xs, ys, np.zeros(len(xs)), 1.0, 6.0)
    # 8 t/ha = 0.8 kg/m2, each record covers 6 m2
    masses = np.full(len(xs), 0.8 * 6.0)
    grid = Grid.from_bounds(shapely.total_bounds(polys), resolution=10.0)
    out = apportion_mass(polys, masses, grid, pass_ids=pids)

    assert check_mass_conservation(out["mass"], masses.sum())
    covered = out["coverage"] > 0
    np.testing.assert_allclose(out["yield"][covered], 8.0)
    np.testing.assert_allclose(out["spread"][covered], 0.0, atol=1e-9)
    # Cells x 0-10 | 10-20 | 20-24 touch passes {0,1} | {1,2,3} | {3}
    assert list(out["passes"][0]) == [2, 3, 1]


def test_flagged_records_reduce_coverage_not_yield():
    polys = record_polygons([5.0, 5.0], [10.0, 20.0], [0.0, 0.0], 10.0, 10.0)
    grid = Grid.from_bounds((0, 0, 10, 20), resolution=10.0)
    out = apportion_mass(polys, [10.0, 999.0], grid, flags=[0, 1])
    # Row 0 is the northern cell (y 10..20), covered by the flagged record.
    assert out["coverage"][0, 0] == 0 and out["flagged_share"][0, 0] == pytest.approx(1.0)
    assert out["yield"][1, 0] == pytest.approx(10 * 10.0 / 100.0)
    assert check_mass_conservation(out["mass"], 10.0)
