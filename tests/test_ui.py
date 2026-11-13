"""Browser UI server: API, token on changes, files only from the output folder."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from yieldsuite import synthetic
from yieldsuite.config import load_config
from yieldsuite.levels import Level
from yieldsuite.pipeline import run
from yieldsuite.ui.server import find_configs, serve


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    root = tmp_path_factory.mktemp("ui")
    path = synthetic.write_demo(root / "demo")
    assert run(load_config(path), Level.L4).ok
    server, app = serve([root], port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", app, path
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url) as r:  # noqa: S310 - local test server
        return r.status, r.read()


def _post(url, body, token=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req) as r:  # noqa: S310
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_find_configs(tmp_path):
    synthetic.write_demo(tmp_path / "a")
    synthetic.write_demo(tmp_path / "b")
    assert sorted(find_configs([tmp_path])) == ["a", "b"]
    assert list(find_configs([tmp_path / "a" / "field.yaml"])) == ["a"]


def test_page_and_api(ui):
    base, app, _ = ui
    code, html = _get(base + "/")
    assert code == 200 and app.token.encode() in html and b"__TOKEN__" not in html
    fields = json.loads(_get(base + "/api/fields")[1])["fields"]
    assert fields[0]["key"] == "demo" and fields[0]["products"]["yield_level"]
    f = json.loads(_get(base + "/api/field/demo")[1])
    assert f["kpi"]["yield_t_ha"] > 0 and f["certainty"]["questions"] is not None
    assert f["machines"][0]["machine_id"] == "combine-a" and "L4" in f["files"]
    assert _get(base + "/files/demo/" + f["files"]["certainty"])[0] == 200


def test_changes_need_the_token_and_are_validated(ui):
    base, app, path = ui
    url = base + "/api/field/demo/"
    assert _post(url + "ticket", {"mass_kg": 1000, "machine_id": "combine-a"})[0] == 403
    assert _post(url + "ticket", {"mass_kg": 1000, "machine_id": "combine-a"}, "wrong")[0] == 403
    code, res = _post(url + "ticket", {"mass_kg": -5, "machine_id": "combine-a"}, app.token)
    assert code == 400 and "mass" in res["error"]
    code, _ = _post(url + "machine", {"machine_id": "combine-a", "antenna_right_m": "0.4",
                                      "reason": "tape"}, app.token)
    assert code == 200 and load_config(path).machines["combine-a"].antenna_offset_m[1] == 0.4
    assert _post(url + "setting", {"key": "light_use.fill", "value": "gpp"}, app.token)[0] == 200
    assert load_config(path).light_use.fill == "gpp"
    assert _post(url + "setting", {"key": "output_dir", "value": "/tmp"}, app.token)[0] == 400
    code, _ = _post(url + "setting", {"key": "crop.sowing_date", "value": "2026-05-10"}, app.token)
    cfg = load_config(path)
    assert code == 200 and cfg.crop.sowing_date == "2026-05-10" and not cfg.crop.sowing_date_assumed
    assert _post(base + "/api/field/nope/run", {}, app.token)[0] == 404


def test_files_stay_inside_the_output_folder(ui):
    base, app, _ = ui
    for rel in ("../field.yaml", "..%2Ffield.yaml", "/etc/passwd", "%2e%2e/field.yaml"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(base + "/files/demo/" + rel)
        assert e.value.code == 404
    assert app.file_path("demo", "../field.yaml") is None
