"""Harvest timelapse and 3D viewer on the synthetic field."""

import base64
import gzip
import json
import re

import numpy as np
import pytest

from yieldsuite import synthetic
from yieldsuite.cli import main
from yieldsuite.config import load_config
from yieldsuite.levels import Level
from yieldsuite.pipeline import run


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    pytest.importorskip("rasterio")
    path = synthetic.write_demo(tmp_path_factory.mktemp("viz"))
    assert run(load_config(path), Level.L4).ok
    return path


def _viewer_data(html: str) -> dict:
    b64 = re.search(r'unpack\("([A-Za-z0-9+/=]+)"\)', html).group(1)
    return json.loads(gzip.decompress(base64.b64decode(b64)))


def test_track_layers_header_state_and_compressed_time(demo):
    from yieldsuite.viz.timelapse import GAP_CAP_S, load_track

    tr = load_track(load_config(demo))
    assert len(tr.t) == len(tr.play) == len(tr.polygons)
    assert np.all(np.diff(tr.play) <= GAP_CAP_S + 1e-9) and np.all(np.diff(tr.play) >= 0)
    assert tr.header_down.any() and (~tr.header_down).any()
    assert np.isfinite(tr.yield_t_ha).sum() > 100
    assert tr.available() == ["yield", "moisture"]
    # no analyzer on the demo: protein and oil are listed with the reason, not dropped
    assert not tr.layers["oil"].available and "analysaattori" in tr.layers["oil"].reason
    assert not tr.layers["protein"].available and tr.layers["protein"].reason_en


def test_parse_layer_list():
    from yieldsuite.viz.timelapse import parse_layer_list

    assert parse_layer_list("sato, kosteus,oil") == ["yield", "moisture", "oil"]
    assert parse_layer_list(None) is None
    with pytest.raises(ValueError):
        parse_layer_list("yield,nonsense")


def test_viewer_is_self_contained_and_lists_every_layer(demo, tmp_path):
    assert main(["timelapse", str(demo), "--out", str(tmp_path), "--no-dem", "--no-video"]) == 0
    html = next(tmp_path.glob("*_harvest_3d.html")).read_text()
    assert "THREE.OrbitControls" in html and "/*DATA*/" not in html and "/*COMMON*/" not in html
    assert not re.search(r"""(src|href)=["']https?://""", html)
    data = _viewer_data(html)
    assert data["visible"] == ["yield", "moisture"] and data["layout"] == "row"
    lay = data["layers"]
    assert lay["yield"]["available"] and lay["yield"]["range"][1] > lay["yield"]["range"][0]
    assert not lay["oil"]["available"] and "values" not in lay["oil"] and lay["oil"]["reason"]
    assert len(lay["yield"]["values"]) == len(data["x"]) == len(data["down"]) == len(data["poly"])
    assert min(v for v in data["x"] if v is not None) >= 0  # local coordinates


def test_viewer_layer_choice_and_layout(demo, tmp_path):
    assert main(["timelapse", str(demo), "--out", str(tmp_path), "--no-dem", "--no-video",
                 "--layers", "moisture", "--layout", "grid"]) == 0
    data = _viewer_data(next(tmp_path.glob("*_harvest_3d.html")).read_text())
    assert data["visible"] == ["moisture"] and data["layout"] == "grid"
    assert data["layers"]["yield"]["available"]  # still in the file, can be ticked on


def test_cli_rejects_unknown_layer(demo, tmp_path):
    assert main(["timelapse", str(demo), "--out", str(tmp_path), "--no-dem",
                 "--layers", "yield,bogus"]) == 2


def test_video_with_a_missing_layer_panel(demo, tmp_path):
    assert main(["timelapse", str(demo), "--out", str(tmp_path), "--no-dem", "--no-html",
                 "--layers", "yield,oil,moisture", "--seconds", "1", "--fps", "5"]) == 0
    out = list(tmp_path.glob("*_timelapse.*"))
    assert out and out[0].stat().st_size > 5_000


def test_grid_shape():
    from yieldsuite.viz.video import grid_shape

    assert [grid_shape(n) for n in (1, 2, 3, 4, 5)] == [(1, 1), (1, 2), (1, 3), (2, 2), (2, 3)]
