"""The gap-fill story: colours per chapter, captions, the 3D page and the video."""

import base64
import gzip
import json
import re

import numpy as np
import pytest

from yieldsuite.l4.grid import Grid
from yieldsuite.viz import gapstory as gs


def _story(with_frames=True):
    grid = Grid(xmin=0.0, ymax=100.0, resolution=10.0, nx=10, ny=10)
    mask = np.ones((10, 10), bool)
    mask[0, :] = False
    final = np.linspace(5, 9, 100).reshape(10, 10)
    measured = np.where(mask & (np.arange(100).reshape(10, 10) % 10 < 4), final, np.nan)
    gap = mask & ~np.isfinite(measured)
    pred = np.where(mask, final - 0.5, np.nan)
    rank = np.zeros((10, 10))
    rank[gap] = np.linspace(0, 1, int(gap.sum()))
    frames = []
    if with_frames:
        v = np.where(mask, 0.8 + 0.1 * np.sin(np.arange(100).reshape(10, 10)), np.nan)
        frames = [gs.Frame("sentinel2", "2026-06-15", v, range=gs._range(v)),
                  gs.Frame("sentinel2", "2026-08-18", v, final_image=True, range=gs._range(v)),
                  gs.Frame("planetscope", "2026-08-18", v, note="own fit", range=gs._range(v))]
    facts = dict(measured_ha=0.34, gap_ha=0.53, empty_ha=0.0, field_ha=0.9, cv_rmse=0.8,
                 feature_set="season + final image", final_image_date="2026-08-18",
                 sigma_median=0.6, sigma_max=1.1, fill_used="image", n_s2=2, n_planet=1,
                 mean_pred_gap=7.0, mean_final_gap=7.5, mean_measured=6.0)
    s = gs.Story(grid, "EPSG:3067", "1", 2026, mask, measured, pred, final,
                 np.where(gap, 0.5, np.nan), gap, np.zeros((10, 10), bool), rank, frames, facts,
                 [], (5.0, 9.0))
    s.chapters = gs.chapters(s)
    return s


def test_chapters_follow_the_story():
    s = _story()
    kinds = [c["kind"] for c in s.chapters]
    assert kinds == ["measured", "source", "source", "source", "model", "krige", "final"]
    fi = [c["text"]["fi"] for c in s.chapters]
    assert "Sentinel-2" in fi[1] and "loppukuva" in fi[2] and "PlanetScope" in fi[3]
    assert "oma sovitus" in fi[3] and "0.80 t/ha" in fi[4]
    assert all(c["title"]["en"] and c["text"]["en"] for c in s.chapters)
    assert gs.chapters(_story(False))[1]["kind"] == "model"


def test_cell_colours_per_chapter():
    s = _story()
    ch = {c["kind"]: c for c in s.chapters}
    a = gs.cell_rgba(s, ch["measured"], 0)
    assert a[0, 0, 3] == 0  # outside the field: transparent
    assert a[5, 1, 3] == 1 and a[5, 8, 3] == pytest.approx(0.65)  # measured vs grey gap
    m0, m1 = gs.cell_rgba(s, ch["model"], 0), gs.cell_rgba(s, ch["model"], 1)
    assert m0[5, 8, 3] == pytest.approx(0.65) and m1[5, 8, 3] == pytest.approx(1)
    k0, k1 = gs.cell_rgba(s, ch["krige"], 0), gs.cell_rgba(s, ch["krige"], 1)
    final = gs.cell_rgba(s, ch["final"], 1)
    # the filling ends on the final values; the filled cells are lightened in the last chapter
    assert not np.allclose(k0[5, 8], k1[5, 8])
    assert (final[5, 8, :3] >= k1[5, 8, :3] - 1e-9).all() and not np.allclose(final[5, 8], k1[5, 8])
    assert np.allclose(final[5, 1], k1[5, 1])  # measured cells unchanged
    src = gs.cell_rgba(s, s.chapters[1], 0)
    assert src[3, 3, 3] == 1


def test_story_page_is_self_contained(tmp_path):
    s = _story()
    out = gs.build_html(s, tmp_path / "story.html", None, None, "T", "fi")
    html = out.read_text()
    assert "/*DATA*/" not in html and "/*COMMON*/" not in html and "THREE.OrbitControls" in html
    b64 = re.search(r'unpack\("([A-Za-z0-9+/=]+)"\)', html).group(1)
    d = json.loads(gzip.decompress(base64.b64decode(b64)))
    assert len(d["chapters"]) == 7 and len(d["frames"]) == 3 and len(d["frames"][0]["range"]) == 2
    assert len(d["measured"]) == len(d["final"]) == len(d["mask"]) == 100
    assert sum(d["gap"]) == int(s.gap.sum())


def test_story_video_is_written(tmp_path):
    s = _story()
    for c in s.chapters:
        c["dur"] = 0.2
    out = gs.write_video(s, tmp_path / "story.mp4", None, None, "fi", fps=5)
    assert out.exists() and out.stat().st_size > 2_000
