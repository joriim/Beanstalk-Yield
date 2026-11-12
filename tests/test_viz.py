"""Harvest timelapse and 3D viewer on the synthetic field."""

import json
import re

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


def test_track_has_header_state_and_compressed_time(demo):
    import numpy as np

    from yieldsuite.viz.timelapse import GAP_CAP_S, load_track

    tr = load_track(load_config(demo))
    assert len(tr.t) == len(tr.play) == len(tr.polygons)
    assert np.all(np.diff(tr.play) <= GAP_CAP_S + 1e-9) and np.all(np.diff(tr.play) >= 0)
    assert tr.header_down.any() and (~tr.header_down).any()
    assert np.isfinite(tr.yield_t_ha).sum() > 100 and tr.quality_name == "moisture"


def test_viewer_is_self_contained(demo, tmp_path):
    assert main(["timelapse", str(demo), "--out", str(tmp_path), "--no-dem", "--no-video"]) == 0
    html = next(tmp_path.glob("*_harvest_3d.html")).read_text()
    assert "THREE.OrbitControls" in html and "/*DATA*/" not in html
    assert "https://" not in re.sub(r"https://[^\s\"']*(threejs\.org|github\.com)[^\s\"']*", "",
                                    html.split("const D = ")[0])
    data = json.loads(html.split("const D = ")[1].split(";\nconst FI")[0])
    assert data["layers"]["yield"]["range"][1] > data["layers"]["yield"]["range"][0]
    assert len(data["x"]) == len(data["down"]) == len(data["poly"])
    assert min(v for v in data["x"] if v is not None) >= 0  # local coordinates


def test_video_written(demo, tmp_path):
    assert main(["timelapse", str(demo), "--out", str(tmp_path), "--no-dem", "--no-html",
                 "--seconds", "1", "--fps", "5"]) == 0
    out = list(tmp_path.glob("*_timelapse.*"))
    assert out and out[0].stat().st_size > 10_000
