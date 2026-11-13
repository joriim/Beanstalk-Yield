"""Entering antenna offsets and scale tickets; lateral offset from pass geometry."""

import json

import numpy as np
import pytest
import yaml

from yieldsuite import inputs, synthetic
from yieldsuite.cli import main
from yieldsuite.config import load_config
from yieldsuite.ingest.scale import read_scale_tickets
from yieldsuite.l2.offset import estimate_lateral_offset

CONFIG = """\
# Field 1 (comment kept)
field_id: "1"
year: 2026
machines:
  - machine_id: combine-a   # main combine
    version: 1
    header_width_m: 7.5
    antenna_offset_m: [3.0, 0.0]  # measured 2025
  - machine_id: combine-b
    version: 1
harvest_logs: []
output_dir: output
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "field.yaml"
    p.write_text(CONFIG)
    return p


def test_set_machine_keeps_comments_and_logs(cfg):
    ch = inputs.set_machine(cfg, "combine-a", antenna_right_m=-0.6, reason="tape")
    text = cfg.read_text()
    assert "# Field 1 (comment kept)" in text and "# measured 2025" in text
    raw = yaml.safe_load(text)
    assert raw["machines"][0]["antenna_offset_m"] == [3.0, -0.6]
    assert ch.old == {"antenna_offset_m": [3.0, 0.0]}
    inputs.set_machine(cfg, "combine-b", antenna_forward_m=2.5, header_width_m=6, reason="x")
    raw = yaml.safe_load(cfg.read_text())
    assert raw["machines"][1]["antenna_offset_m"] == [2.5, 0.0]
    assert raw["machines"][1]["header_width_m"] == 6
    assert load_config(cfg).machines["combine-b"].antenna_offset_m == (2.5, 0.0)
    log = inputs.read_log(cfg)
    assert [e["action"] for e in log] == ["set_machine", "set_machine"]
    assert log[0]["reason"] == "tape" and log[0]["new"]["antenna_offset_m"] == [3.0, -0.6]


def test_set_machine_new_profile_and_checks(cfg):
    inputs.set_machine(cfg, "combine-c", antenna_right_m=0.4, reason="new")
    assert load_config(cfg).machines["combine-c"].antenna_offset_m == (0.0, 0.4)
    with pytest.raises(inputs.InputError):
        inputs.set_machine(cfg, "combine-a", antenna_right_m=0.4)  # no reason
    with pytest.raises(inputs.InputError):
        inputs.set_machine(cfg, "combine-a", antenna_right_m=40, reason="typo")
    before = cfg.read_text()
    inputs.set_machine(cfg, "combine-a", antenna_right_m=1, reason="r", dry_run=True)
    assert cfg.read_text() == before


def test_tickets_add_import_remove(cfg, tmp_path):
    inputs.add_tickets(
        cfg,
        [
            {"mass_kg": 12000, "machine_id": "combine-a", "moisture_pct": 20, "ticket_ref": "1"},
            {"mass_kg": 8000, "machine_id": "combine-b", "ticket_ref": "2", "field_share": 0.5},
        ],
        reason="weighbridge",
    )
    assert load_config(cfg).scale_tickets == cfg.parent / "scale_tickets.csv"
    with pytest.raises(inputs.InputError, match="already entered"):
        inputs.add_tickets(cfg, [{"mass_kg": 1, "machine_id": "combine-a", "ticket_ref": "1"}])
    with pytest.raises(inputs.InputError, match="not in the config"):
        inputs.add_tickets(cfg, [{"mass_kg": 1, "machine_id": "nope"}])
    with pytest.raises(inputs.InputError, match="mix"):
        inputs.add_tickets(cfg, [{"mass_kg": 1}])
    src = tmp_path / "vaaka.csv"
    src.write_text("Kuitti;Netto t;Kosteus;Kone\nA7;10,5;18,2;combine-a\nYhteensä;10,5;;\n")
    # the total row is read as a load too (removed below)
    rows = inputs.import_tickets(
        cfg,
        src,
        {
            "mass_kg": "Netto t",
            "moisture_pct": "Kosteus",
            "ticket_ref": "Kuitti",
            "machine_id": "Kone",
        },
        mass_unit="t",
        machine_id="combine-a",
    )
    assert rows[0]["mass_kg"] == 10500 and rows[0]["moisture_pct"] == 18.2
    t = read_scale_tickets(cfg.parent / "scale_tickets.csv", 14)
    b = t[t.machine_id == "combine-b"].mass_ref_kg.sum()
    assert b == pytest.approx(4000)  # half of the load from this field
    a = t[t.machine_id == "combine-a"].mass_ref_kg.to_numpy()
    assert a[0] == pytest.approx(12000 * 80 / 86)
    inputs.remove_ticket(cfg, "Yhteensä", reason="total row, not a load")
    assert "Yhteensä" not in set(inputs.read_tickets(cfg).ticket_ref.astype(str))
    s = inputs.ticket_summary(cfg)
    assert set(s.machine_id) == {"combine-a", "combine-b"}
    actions = [e["action"] for e in inputs.read_log(cfg)]
    assert actions == ["add_tickets", "add_tickets", "remove_ticket"]


def test_empty_machine_column_is_generic(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("ticket_ref,machine_id,mass_kg\n1,,1000\n")
    assert read_scale_tickets(p, 14).machine_id.tolist() == ["*"]


def _passes(e, n_pass=12, w=7.0, step=1.0, length=200.0, seed=0):
    """Back-and-forth passes, logged ``e`` m to the right of the true centre line."""
    rng = np.random.default_rng(seed)
    xs, ys, hs, pid = [], [], [], []
    for k in range(n_pass):
        north = k % 2 == 0
        h = 0.0 if north else np.pi
        y = np.arange(0, length, step)
        y = y if north else y[::-1]
        right_x = np.cos(h)  # right unit vector x component (cos h, -sin h)
        x = k * w + e * right_x + rng.normal(0, 0.05, y.size)
        xs.append(x)
        ys.append(y)
        hs.append(np.full(y.size, h))
        pid.append(np.full(y.size, k))
    cat = np.concatenate
    return cat(xs), cat(ys), cat(hs), cat(pid), np.full(cat(xs).size, w)


@pytest.mark.parametrize("e", [0.0, 0.5, -0.8])
def test_lateral_offset_recovered(e):
    est = estimate_lateral_offset(*_passes(e))
    assert est.identifiable
    assert est.lateral_error_m == pytest.approx(e, abs=0.05)
    lo, hi = est.ci95_m
    assert lo <= est.lateral_error_m <= hi


def test_lateral_offset_needs_both_pair_kinds():
    x, y, h, pid, w = _passes(0.3, n_pass=2)
    est = estimate_lateral_offset(x, y, h, pid, w)
    assert not est.identifiable and "too few" in est.reason


def test_estimate_offsets_on_demo_and_cli(tmp_path, capsys):
    from yieldsuite.pipeline import estimate_offsets

    path = synthetic.write_demo(tmp_path)
    d = estimate_offsets(load_config(path))["combine-a"]
    assert d["identifiable"] and abs(d["lateral_error_m"]) < 0.1 and not d["significant"]
    # enter a wrong lateral offset: the estimate must point back to zero
    assert (
        main(
            [
                "machine",
                "set",
                str(path),
                "--machine",
                "combine-a",
                "--antenna-right",
                "0.6",
                "--reason",
                "test",
            ]
        )
        == 0
    )
    d = estimate_offsets(load_config(path))["combine-a"]
    assert d["lateral_error_m"] == pytest.approx(0.6, abs=0.1)
    assert d["significant"] and abs(d["suggested_right_m"]) < 0.1
    assert main(["machine", "estimate-offset", str(path)]) == 0
    assert "suggestion" in capsys.readouterr().out
    assert main(["machine", "show", str(path)]) == 0
    assert "right +0.60" in capsys.readouterr().out


def test_cli_tickets_and_reconciled_run(tmp_path, capsys):
    from yieldsuite.levels import Level
    from yieldsuite.pipeline import run

    path = synthetic.write_demo(tmp_path)
    raw = yaml.safe_load(path.read_text())
    truth = read_scale_tickets(path.parent / raw["scale_tickets"], 14).mass_ref_kg.sum()
    # start from a config without tickets and enter one total by hand
    text = "\n".join(
        line for line in path.read_text().splitlines() if not line.startswith("scale_tickets")
    )
    path.write_text(text + "\n")
    (path.parent / raw["scale_tickets"]).unlink()
    s = run(load_config(path), Level.L3)
    assert s.product_class.value == "absolute_unreconciled"
    assert (
        main(
            [
                "tickets",
                "add",
                str(path),
                "--mass-kg",
                f"{truth:.1f}",
                "--ref",
                "T1",
                "--machine",
                "combine-a",
                "--reason",
                "weighbridge",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "1 tickets" in out and "rerun" in out
    assert (
        main(
            ["tickets", "add", str(path), "--mass-kg", "5", "--ref", "T1", "--machine", "combine-a"]
        )
        == 2
    )
    s = run(load_config(path), Level.L3)
    assert s.ok and s.product_class.value == "absolute"
    assert json.loads((path.parent / inputs.LOG_NAME).read_text().splitlines()[0])["actor"]
