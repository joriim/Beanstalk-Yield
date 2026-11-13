import numpy as np
import pytest

from yieldsuite.l4.grid import Grid
from yieldsuite.l4.resolution import ResolutionSettings, assess, choose
from yieldsuite.present import aggregate, candidates


def _field(noise, seed=0, size=240.0, swath=6.0, step=2.0):
    """Passes along x every ``swath`` m; yield = smooth pattern + noise per record."""
    rng = np.random.default_rng(seed)
    xs, ys, ps = [], [], []
    for p, y in enumerate(np.arange(swath / 2, size, swath)):
        x = np.arange(step / 2, size, step)
        xs.append(x), ys.append(np.full_like(x, y)), ps.append(np.full(len(x), p))
    x, y, p = np.concatenate(xs), np.concatenate(ys), np.concatenate(ps)
    true = 5.0 + 2.0 * np.sin(x / 60.0) * np.cos(y / 60.0)
    area = np.full(len(x), swath * step)
    mass = (true + rng.normal(0, noise, len(x))) * area / 10.0
    return x, y, mass, area, p


def _table(noise, sizes=(6.0, 12.0, 24.0, 48.0)):
    return [assess(*_field(noise), resolution=g) for g in sizes]


def test_clean_data_is_repeatable_at_the_finest_assessable_size():
    table = _table(noise=0.05)
    assert not table[0]["assessable"]  # one pass per 6 m cell: nothing to compare
    c = choose(table)
    assert c["met_target"] and c["resolution_m"] == 12.0


def test_noise_pushes_the_choice_to_larger_cells():
    table = _table(noise=6.0)
    rel = [t["reliability"] for t in table if t["assessable"]]
    assert rel == sorted(rel)  # averaging more records helps
    c = choose(table)
    assert c["resolution_m"] > 12.0
    se = {t["resolution_m"]: t["se_t_ha"] for t in table}
    assert se[48.0] < se[12.0]


def test_pure_noise_meets_no_target_and_says_so():
    x, y, mass, area, p = _field(noise=0.0)
    rng = np.random.default_rng(1)
    mass = rng.normal(5.0, 1.0, len(x)) * area / 10.0
    table = [assess(x, y, mass, area, p, resolution=g) for g in (12.0, 24.0, 48.0)]
    c = choose(table, ResolutionSettings(min_reliability=0.8))
    assert not c["met_target"] and "no cell size reaches" in c["reason"]


def test_nothing_assessable():
    c = choose([assess(*_field(0.1), resolution=6.0)])
    assert c["resolution_m"] is None and not c["met_target"]


def test_aggregate_keeps_mass_and_snaps_to_origin():
    rng = np.random.default_rng(0)
    grid = Grid(xmin=30.0, ymax=170.0, resolution=10.0, nx=7, ny=5)
    y = rng.uniform(3, 8, (5, 7))
    cov = rng.uniform(0.2, 1.0, (5, 7))
    y[0, 0], cov[0, 0] = np.nan, np.nan
    src = np.where(np.isfinite(y), 1.0, 2.0)
    filled = np.where(np.isfinite(y), y, 4.0)
    out, coarse = aggregate(grid, 2, (0.0, 0.0), {"yield": y, "coverage": cov},
                            {"yield": filled, "source": src})
    assert coarse.resolution == 20.0 and coarse.xmin % 20 == 0 and coarse.ymax % 20 == 0
    fine = np.nansum(y * cov) * 100.0
    assert np.nansum(out["yield"] * out["coverage"]) * 400.0 == pytest.approx(fine)
    assert np.nanmin(out["measured_share"]) < 1.0 and np.nanmax(out["measured_share"]) == 1.0
    assert np.isfinite(out["yield_filled"]).sum() >= np.isfinite(out["yield"]).sum()


def test_candidates_are_multiples_of_the_working_grid():
    assert candidates(10.0) == (10.0, 20.0, 30.0, 40.0, 50.0, 60.0)
    assert candidates(20.0) == (20.0, 40.0, 60.0)
    assert candidates(100.0) == (100.0,)


def test_config_presentation(tmp_path):
    from yieldsuite.config import load_config

    base = "field_id: a\nyear: 2026\ngrid: {resolution_m: 10, %s}\n"
    for text, want in (("presentation_m: 40", 40.0), ("presentation_m: off", "off"),
                       ("min_reliability: 0.7", "auto")):
        p = tmp_path / "f.yaml"
        p.write_text(base % text)
        cfg = load_config(p)
        assert cfg.presentation == want
        assert not any("presentation" in m for m in cfg.validate())
    p.write_text(base % "presentation_m: 25")
    assert any("multiple" in m for m in load_config(p).validate())
