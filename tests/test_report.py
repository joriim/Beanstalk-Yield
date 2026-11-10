"""Run manifest (audit) and the level-by-level report."""

import json

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("geopandas")
pytest.importorskip("rasterio")

from yieldsuite import synthetic  # noqa: E402
from yieldsuite.audit import sha256, verify_manifest  # noqa: E402
from yieldsuite.cli import main  # noqa: E402
from yieldsuite.config import load_config  # noqa: E402
from yieldsuite.levels import Level  # noqa: E402
from yieldsuite.pipeline import run  # noqa: E402


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    out = tmp_path_factory.mktemp("rep")
    cfg_path = synthetic.write_demo(out)
    cfg = load_config(cfg_path)
    summary = run(cfg, Level.L4)
    assert summary.ok, summary.error
    return cfg, cfg_path, summary


def test_run_manifest_lists_inputs_outputs_and_settings(ran):
    cfg, cfg_path, summary = ran
    man = json.loads(open(summary.outputs["run_manifest"]).read())
    assert man["ok"] and man["levels_completed"] == ["L1A", "L1B", "L2", "L3", "L4"]
    roles = {e["role"].split()[0] for e in man["inputs"]}
    assert {"config", "harvest_log", "scale_tickets"} <= roles
    cfg_entry = next(e for e in man["inputs"] if e["role"] == "config")
    assert cfg_entry["sha256"] == sha256(cfg_path)
    assert {e["role"] for e in man["outputs"]} >= {"L3", "L4", "qa"}
    m = next(iter(man["settings"]["machines"].values()))
    assert len(m["param_hash"]) == 16 and "tau_d_s" in m
    assert man["software"]["name"] == "Beanstalk Yield Suite"
    assert verify_manifest(summary.outputs["run_manifest"]) == []


def test_verify_detects_a_changed_input(ran, tmp_path):
    cfg, cfg_path, summary = ran
    man = json.loads(open(summary.outputs["run_manifest"]).read())
    copy = tmp_path / "run.json"
    tickets = next(e for e in man["inputs"] if e["role"] == "scale_tickets")
    tickets["sha256"] = "0" * 64
    copy.write_text(json.dumps(man))
    problems = verify_manifest(copy)
    assert len(problems) == 1 and "scale_tickets" in problems[0]
    assert main(["verify", str(copy)]) == 1


@pytest.mark.parametrize("lang", ["fi", "en"])
def test_report_has_every_level_figure_process_and_audit(ran, tmp_path, lang):
    cfg, cfg_path, summary = ran
    out = tmp_path / lang
    assert main(["report", str(cfg_path), "--out", str(out), "--lang", lang]) == 0
    stem = f"{cfg.field_id}_{cfg.year}"
    html = (out / f"{stem}_report.html").read_text()
    for level in ("L0", "L1A", "L1B", "L2", "L3", "L4"):
        assert f"<h3>{level} " in html
    assert html.count("data:image/png;base64,") == 7  # pipeline, summary, L1A-L4
    assert ("tavu tavulta samat" if lang == "fi" else "byte-identical") in html
    process = (out / f"{stem}_process.md").read_text()
    assert "## L3" in process and "k" in process
    audit = json.loads((out / f"{stem}_audit.json").read_text())
    assert audit["verification"]["ok"] is True
    assert audit["ingest"][0]["units"]
    assert sorted(p.name for p in (out / "figures").iterdir()) == [
        "L1A_records.png",
        "L1B_signals.png",
        "L2_yield_records.png",
        "L3_flags.png",
        "L4_grid.png",
        "pipeline.png",
        "summary.png",
    ]


def test_report_needs_a_run(tmp_path, capsys):
    cfg_path = synthetic.write_demo(tmp_path)
    assert main(["report", str(cfg_path)]) == 1
    assert "run the chain" in capsys.readouterr().err


def test_report_notes_title_and_redaction(ran, tmp_path):
    cfg, cfg_path, summary = ran
    notes = tmp_path / "notes.txt"
    notes.write_text("## Key point\n- level is **high**\n\nA paragraph.\n")
    out = tmp_path / "shared"
    rc = main(
        [
            "report",
            str(cfg_path),
            "--out",
            str(out),
            "--lang",
            "en",
            "--notes",
            str(notes),
            "--title",
            "Prototype X",
            "--redact",
        ]
    )
    assert rc == 0
    stem = f"{cfg.field_id}_{cfg.year}"
    html = (out / f"{stem}_report.html").read_text()
    assert "<h1>Prototype X</h1>" in html and "<b>high</b>" in html
    assert "Figure 7." in html and "Prototype – results not yet validated" in html
    assert str(tmp_path) not in html  # no absolute paths
    audit = (out / f"{stem}_audit.json").read_text()
    assert str(cfg_path.parent) not in audit
