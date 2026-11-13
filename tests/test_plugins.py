"""Covariate plugins via the entry-point group yieldsuite.covariates."""

import json
from importlib.metadata import EntryPoint

import numpy as np
import pytest

pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")

from shapely.geometry import box  # noqa: E402

from yieldsuite import plugins  # noqa: E402
from yieldsuite.cli import main  # noqa: E402
from yieldsuite.l4.grid import Grid  # noqa: E402

X0, Y1 = 380000.0, 6960300.0


class FakeProvider:
    """A 50 m raster: west half 0.2, east half 0.8 (like a peat probability)."""

    api_version = 1
    name = "fake"
    description = "test plugin"
    licence = "test licence: internal use only"
    version = "9.9"

    def layers(self, boundary, crs):
        p = np.full((6, 8), 0.2)
        p[:, 4:] = 0.8
        return {
            "bands": {"peat_p30": p, "agreement": np.full((6, 8), 90.0)},
            "transform": (50.0, 0.0, X0, 0.0, -50.0, Y1),
            "crs": "EPSG:3067",
            "notes": ["fake note"],
            "meta": {"model": "fake"},
        }


class OldProvider(FakeProvider):
    api_version = 0


@pytest.fixture
def registered(monkeypatch):
    eps = [
        EntryPoint("fake", "test_plugins:FakeProvider", plugins.GROUP),
        EntryPoint("old", "test_plugins:OldProvider", plugins.GROUP),
    ]
    monkeypatch.setattr(
        plugins, "entry_points", lambda group: eps if group == plugins.GROUP else []
    )


def test_list_and_load(registered):
    assert [p.name for p in plugins.available()] == ["fake", "old"]
    assert plugins.load("fake").licence.startswith("test licence")
    with pytest.raises(plugins.PluginError, match="API 0"):
        plugins.load("old")
    with pytest.raises(plugins.PluginError, match="no covariate plugin 'nope'"):
        plugins.load("nope")


def test_to_grid_is_area_weighted_and_reprojects(registered):
    layers = plugins.fetch(plugins.load("fake"), box(X0, Y1 - 300, X0 + 400, Y1), "EPSG:3067")
    grid = Grid(xmin=X0, ymax=Y1, resolution=10.0, nx=40, ny=30)
    b = plugins.to_grid(layers, grid, "EPSG:3067")
    assert np.allclose(b["peat_p30"][:, :20], 0.2) and np.allclose(b["peat_p30"][:, 20:], 0.8)
    # a 20 m grid offset by 10 m straddles the 50 m edge at x0 + 200: mean of both
    g2 = Grid(xmin=X0 + 190.0, ymax=Y1, resolution=20.0, nx=1, ny=1)
    assert plugins.to_grid(layers, g2, "EPSG:3067")["peat_p30"][0, 0] == pytest.approx(0.5)
    # another CRS (UTM 35N): values stay within the source range
    from pyproj import Transformer

    tx, ty = Transformer.from_crs(3067, 32635, always_xy=True).transform(X0 + 100, Y1 - 150)
    g3 = Grid(xmin=tx - 50, ymax=ty + 50, resolution=10.0, nx=10, ny=10)
    v = plugins.to_grid(layers, g3, "EPSG:32635")["peat_p30"]
    assert np.nanmin(v) >= 0.2 - 1e-9 and np.nanmax(v) <= 0.8 + 1e-9


def test_cli_fetch_writes_raster_and_provenance(registered, tmp_path, capsys):
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame(geometry=[box(X0, Y1 - 300, X0 + 400, Y1)], crs="EPSG:3067").to_file(bnd)
    cfg = tmp_path / "field.yaml"
    cfg.write_text(
        "field_id: f1\nyear: 2026\nboundary: boundary.gpkg\ncrs: EPSG:3067\n"
        "grid: {resolution_m: 20, origin: [0, 0]}\n"
        "harvest_logs: [{path: x.csv}]\noutput_dir: output\n"
    )
    assert main(["covariate", "list"]) == 0
    assert "fake" in capsys.readouterr().out
    assert main(["covariate", "fetch", "fake", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "test licence" in out and "fake note" in out
    prov = json.loads((tmp_path / "covariates" / "covariate_fake.provenance.json").read_text())
    assert prov["plugin_version"] == "9.9" and prov["bands"] == ["peat_p30", "agreement"]
    assert prov["source_resolution_m"] == 50.0 and prov["grid_resolution_m"] == 20.0
    from yieldsuite.l4.io import read_bands

    bands, grid, crs = read_bands(
        tmp_path / "covariates" / "covariate_fake.tif", require_yield=False
    )
    assert set(bands) == {"peat_p30", "agreement"} and grid.resolution == 20.0
    assert main(["covariate", "fetch", "old", str(cfg)]) == 1
