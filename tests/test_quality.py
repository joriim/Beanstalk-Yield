"""Grain quality channel (GrainSense-style analyzer) on a synthetic field."""

import json

import numpy as np
import pandas as pd
import pytest
import yaml

from yieldsuite import synthetic
from yieldsuite.config import load_config
from yieldsuite.levels import Level
from yieldsuite.pipeline import run
from yieldsuite.quality import GrainSenseClient, nutrient_percentages, read_quality


def test_percentages_follow_the_documented_formula():
    mg = {"protein": 120.0, "carbohydrate": 650.0, "oil": 30.0, "moisture": 200.0}
    out = nutrient_percentages(mg, {"protein_moisture_basis": "dry", "oil_moisture_basis": "wet"})
    assert out["protein_pct"] == pytest.approx(100 * 120 / 800)  # dry: / (p + c + o)
    assert out["oil_pct"] == pytest.approx(100 * 30 / 1000)  # wet: / total
    assert out["moisture_pct"] == pytest.approx(20.0)
    with pytest.raises(NotImplementedError):
        nutrient_percentages(mg, {"protein_moisture_basis": "fixed"})


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return (
            self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()
        )


def test_client_login_sessions_and_export(monkeypatch):
    calls = []
    pages = {
        0: {"results": [{"uuid": "a"}, {"uuid": "b"}], "next": "more"},
        2: {"results": [{"uuid": "c"}], "next": None},
    }

    def opener(req, timeout):
        calls.append(req)
        url = req.full_url
        if url.endswith("/v1/login/"):
            assert json.loads(req.data) == {"email": "e@x", "password": "pw"}
            return _Resp({"token": "abc"})
        if "measurementSessions/?" in url:
            assert req.headers["Authorization"] == "JWT abc"
            offset = int(url.split("offset=")[1].split("&")[0])
            return _Resp(pages[offset])
        if url.endswith("measurementSessions/export/"):
            assert json.loads(req.data)["measurement_sessions"] == ["a"]
            return _Resp(b"xlsx-bytes")
        raise AssertionError(url)

    monkeypatch.setenv("GRAINSENSE_EMAIL", "e@x")
    monkeypatch.setenv("GRAINSENSE_PASSWORD", "pw")
    c = GrainSenseClient("https://gs.example/api", opener=opener)
    assert c.login() == "abc"
    assert [s["uuid"] for s in c.flow_sessions(start="2026-08-01T00:00:00Z")] == ["a", "b", "c"]
    assert c.export_flow_sessions(["a"]) == b"xlsx-bytes"
    assert "measured_at__gte=2026-08-01" in calls[1].full_url


def test_read_quality_recognises_columns_and_fractions(tmp_path):
    df = pd.DataFrame(
        {
            "Mittausaika": ["10.8.2026 10:00:05", "10.8.2026 10:00:10"],
            "Proteiini": [0.125, 0.13],
            "Kosteus": [0.18, 0.19],
        }
    )
    path = tmp_path / "q.csv"
    df.to_csv(path, sep=";", index=False)
    r = read_quality(path)
    assert r.report["mapping"]["protein"] == "Proteiini"
    assert r.measurements["protein_pct"].tolist() == pytest.approx([12.5, 13.0])
    assert r.measurements["moisture_pct"].tolist() == pytest.approx([18.0, 19.0])
    assert r.report["median_interval_s"] == 5.0


def _quality_case(tmp_path, lab=True, basis="dry"):
    cfg_path = synthetic.write_demo(tmp_path)
    export, lab_df = synthetic.quality_export(synthetic.make_field(0))
    export.to_csv(tmp_path / "gs.csv", index=False)
    lab_df.to_csv(tmp_path / "lab.csv", index=False)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["quality"] = {
        "sources": [{"path": "gs.csv", "machine_id": "combine-a"}],
        "n_factor": 5.7,
        "protein_basis": basis,
    }
    if lab:
        raw["quality"]["lab_samples"] = "lab.csv"
    cfg_path.write_text(yaml.safe_dump(raw))
    s = run(load_config(cfg_path), Level.L4)
    assert s.ok, s.error
    return s


def test_quality_map_with_lab_calibration(tmp_path):
    pytest.importorskip("rasterio")
    from yieldsuite.l4.io import read_bands
    from yieldsuite.l5.fuse import cell_centres

    s = _quality_case(tmp_path)
    qa = json.loads(open(s.outputs["Q4_qa"]).read())
    assert qa["lab_calibration"]["applied"]
    assert qa["lab_calibration"]["bias_pct_points"] == pytest.approx(-0.5, abs=0.1)
    assert qa["product_class"] == "absolute"
    assert qa["sources"][0]["mapping"]["moisture"] == "Moisture (%)"
    b, grid, _ = read_bands(s.outputs["Q4"], require_yield=False)
    _, _, xy = cell_centres(grid)
    truth = synthetic.true_protein(xy[:, 0], xy[:, 1]).reshape(grid.ny, grid.nx)
    good = b["quality_mass_share"] > 0.8
    err = b["protein_pct_dm"][good] - truth[good]
    assert abs(err.mean()) < 0.1 and np.sqrt((err**2).mean()) < 0.15
    # protein yield and N uptake follow from yield x dry matter x protein
    l4, _, _ = read_bands(s.outputs["L4"])
    expected = l4["yield"] * 1000 * 0.86 * b["protein_pct_dm"] / 100 / 5.7
    np.testing.assert_allclose(b["n_uptake_kg_ha"][good], expected[good], rtol=1e-5)
    assert any("lab-calibrated" in n for n in s.notes)


def test_quality_without_lab_is_relative(tmp_path):
    s = _quality_case(tmp_path, lab=False)
    qa = json.loads(open(s.outputs["Q4_qa"]).read())
    assert qa["product_class"] == "relative" and "not calibrated" in qa["note"]
    assert qa["field_mean_protein_pct_dm"] == pytest.approx(12.9, abs=0.3)  # bias kept


def test_quality_wet_basis_converted(tmp_path):
    s = _quality_case(tmp_path, lab=False, basis="wet")
    qa = json.loads(open(s.outputs["Q4_qa"]).read())
    # wet-basis numbers divided by (1 - moisture) -> higher on dry matter
    assert qa["field_mean_protein_pct_dm"] > 14.0


def test_quality_time_must_overlap(tmp_path):
    cfg_path = synthetic.write_demo(tmp_path)
    pd.DataFrame({"time": ["2025-01-01T00:00:00Z"], "protein": [12.0]}).to_csv(
        tmp_path / "gs.csv", index=False
    )
    raw = yaml.safe_load(cfg_path.read_text())
    raw["quality"] = {"sources": [{"path": "gs.csv", "machine_id": "combine-a"}]}
    cfg_path.write_text(yaml.safe_dump(raw))
    s = run(load_config(cfg_path), Level.L4)
    assert s.ok
    assert any("no matched measurements" in n for n in s.notes)


def test_session_sheet_with_summary_block_above_the_table(tmp_path):
    """GrainSense app export: each session sheet starts with a summary row, the
    measurement table (Finnish headers, UTC times) follows below it."""
    pytest.importorskip("openpyxl")
    from yieldsuite.quality.pipeline import read_quality

    rows = [
        ["Istunto aloitettu", "Kalibrointi", "Proteiinin keskiarvo [%]", "Nimi", None],
        ["2026-08-22 08:53:07+0000", "Ohra", 15.1, "harjunpaa", None],
        ["Mittausaika", "Proteiini [%]", "Kosteus [%]", "Öljy [%]", "Leveysaste"],
        ["2026-08-22 08:53:20+0000", 13.48, 23.47, "-", 62.45],
        ["2026-08-22 08:53:21+0000", 13.10, 22.90, "-", 62.45],
        ["2026-08-22 08:53:22+0000", 14.02, 22.10, "-", 62.45],
    ]
    p = tmp_path / "session.xlsx"
    with pd.ExcelWriter(p) as w:
        pd.DataFrame(rows[1:], columns=rows[0]).to_excel(w, sheet_name="harjunpaa", index=False)
    r = read_quality(p, sheet="harjunpaa")
    m = r.measurements
    assert len(m) == 3 and m["protein_pct"].tolist() == [13.48, 13.10, 14.02]
    assert "oil_pct" not in m and m["moisture_pct"].iloc[0] == 23.47
    assert m["t"].iloc[0] == pd.Timestamp("2026-08-22 08:53:20", tz="UTC").timestamp()
    assert any("header found on row" in w for w in r.report["warnings"])


# --- analyzer moisture into the yield chain (L2) --------------------------------


def test_moisture_calibration_modes():
    from yieldsuite.quality.moisture import corrected_moisture

    rng = np.random.default_rng(0)
    sensor = 14 + rng.normal(0, 0.3, 500)
    analyzer = np.where(np.arange(500) < 200, sensor + 2.0 + rng.normal(0, 0.2, 500), np.nan)
    new, info = corrected_moisture(sensor, analyzer, "auto")
    assert info["mode"] == "calibrate" and info["method"] == "offset"
    assert np.median(new - sensor) == pytest.approx(2.0, abs=0.05)
    new, info = corrected_moisture(sensor, analyzer, "replace")
    np.testing.assert_allclose(new[:200], analyzer[:200])
    assert corrected_moisture(sensor, analyzer, "off")[0] is None
    few = np.where(np.arange(500) < 10, analyzer, np.nan)
    assert corrected_moisture(sensor, few)[0] is None
    new, info = corrected_moisture(np.full(500, np.nan), analyzer)
    assert info["mode"] == "analyzer" and np.isfinite(new).all()
    # varying combine moisture: linear fit
    s2 = np.linspace(12, 20, 300)
    new, info = corrected_moisture(s2, 1.0 + 0.9 * s2, "calibrate")
    assert info["method"] == "linear" and info["slope"] == pytest.approx(0.9, abs=0.01)


def test_failed_scans_dropped():
    from yieldsuite.quality.moisture import clean_readings

    q = pd.DataFrame({"t": [1, 2, 3, 4], "protein_pct": [13, 13, 30, 13],
                      "moisture_pct": [15, 5, 15, 16], "carbohydrate_pct": [80, 80, 80, 0]})
    out, info = clean_readings(q)
    assert out["t"].tolist() == [1] and info["dropped"] == 3


def test_vendor_dry_yield_rewetted_with_corrected_moisture():
    from types import SimpleNamespace

    from yieldsuite.l1.moisture import to_reference_moisture
    from yieldsuite.quality import moisture as qm

    n = 100
    t = np.arange(n, dtype=float) * 2 + 1.7e9
    sensor = 12.0 + 0.1 * np.sin(np.arange(n))  # measured: varies
    df = pd.DataFrame({"moisture_sensor": sensor, "vendor_dry_moisture": 14.0})
    cfg = SimpleNamespace(
        quality=SimpleNamespace(moisture="auto", sources=[SimpleNamespace(
            path="x", machine_id="m", format="auto", columns={}, sheet=None, delay_s=0.0,
            max_gap_s=1.0)]),
        machines={},
    )
    q = pd.DataFrame({"t": t, "protein_pct": 13.0, "moisture_pct": 15.0})
    cache = {"x": (SimpleNamespace(report={"median_interval_s": 2.0}), q, {"readings": n})}
    dry = np.full(n, 1.0)  # kg/s at the vendor's 14 %, from a combine reading 12 %
    cache = {"x|None": cache["x"]}
    mc, mass, info, _ = qm.apply(cfg, df, t, np.full(n, 14.0), dry, "m", cache)
    assert info["vendor_dry_rewetted"] == n and np.allclose(mc, 15.0, atol=0.15)
    # wet mass = 1 x 86 / (100 - sensor); at 14 % with the corrected moisture
    expect = 86 / (100 - sensor) * (100 - mc) / 86
    np.testing.assert_allclose(to_reference_moisture(mass, mc, 14.0), expect)


def test_chain_uses_analyzer_moisture(tmp_path):
    cfg_path = synthetic.write_demo(tmp_path)
    export, _ = synthetic.quality_export(synthetic.make_field(0))
    export["Moisture (%)"] = export["Moisture (%)"] + 2.0  # combine reads 2 points dry
    export.to_csv(tmp_path / "gs.csv", index=False)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["quality"] = {"sources": [{"path": "gs.csv", "machine_id": "combine-a"}]}
    del raw["scale_tickets"]
    cfg_path.write_text(yaml.safe_dump(raw))
    s = run(load_config(cfg_path), Level.L4)
    assert s.ok, s.error
    qa = json.loads(open(s.outputs["qa"]).read())
    m = qa["moisture"]["combine-a"]
    assert m["mode"] == "calibrate" and m["median_offset_pct"] == pytest.approx(2.0, abs=0.2)
    assert m["mass_at_reference_change_pct"] == pytest.approx(-2.3, abs=0.3)
    raw["quality"]["moisture"] = "off"
    cfg_path.write_text(yaml.safe_dump(raw))
    s = run(load_config(cfg_path), Level.L4)
    assert "moisture" not in json.loads(open(s.outputs["qa"]).read())


def test_entered_combine_moisture_detected_and_replaced():
    from yieldsuite.quality.moisture import SOURCE_CODES, entered_values, interpolate_in_time

    rng = np.random.default_rng(0)
    t = np.arange(3000, dtype=float) * 2
    mc = np.round(15 + 0.5 * np.sin(t / 900) + rng.normal(0, 0.2, t.size), 1)
    mc[100:300] = 17.0  # held default value, twice
    mc[2000:2100] = 17.0
    bad, info = entered_values(t, mc)
    assert bad[100:300].all() and bad[2000:2100].all() and bad.sum() == 300
    assert "17.0" in info["default_values_pct"]
    filled = interpolate_in_time(t, mc, bad)
    assert np.isfinite(filled[100:300]).all() and abs(np.median(filled[100:300]) - 15) < 1
    const, info = entered_values(t, np.full(t.size, 17.0))
    assert const.all() and info["measured"] is False
    assert SOURCE_CODES["interpolated"] == 3


def test_chain_replaces_entered_moisture(tmp_path):
    cfg_path = synthetic.write_demo(tmp_path)
    raw = yaml.safe_load(cfg_path.read_text())
    log = tmp_path / raw["harvest_logs"][0]["path"]
    df = pd.read_csv(log)
    col = raw["harvest_logs"][0].get("columns", {}).get("moisture", "Moisture_pct")
    df.loc[200:600, col] = 17.0
    df.to_csv(log, index=False)
    s = run(load_config(cfg_path), Level.L4)
    assert s.ok, s.error
    qa = json.loads(open(s.outputs["qa"]).read())
    ent = qa["moisture"]["combine-a"]["combine_moisture"]
    assert ent["entered_records"] >= 300
    assert any("entered or held" in w for w in qa["warnings"])
    rec = pd.read_parquet(s.outputs["L3"])
    assert (rec["moisture_source"] == 3).sum() >= 300
