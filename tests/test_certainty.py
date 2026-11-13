"""Gaps and certainty: what is known, estimated, assumed or missing, and what to ask."""

import json

import yaml

from yieldsuite import certainty, inputs, synthetic
from yieldsuite.cli import main
from yieldsuite.config import load_config
from yieldsuite.levels import Level
from yieldsuite.pipeline import run


def _by_id(res):
    return {i["id"]: i for i in res["items"]}


def test_demo_with_tickets_has_a_verified_yield_level(tmp_path):
    path = synthetic.write_demo(tmp_path)
    s = run(load_config(path), Level.L4)
    assert s.ok and s.certainty and "certainty" in s.outputs
    res = json.loads(open(s.outputs["certainty"]).read())
    items = _by_id(res)
    assert items["scale_tickets"]["status"] == "known"
    assert res["products"]["yield_level"]["certainty"] in ("medium", "high")
    assert items["antenna:combine-a"]["status"] == "assumed"  # in the config, not measured
    assert "protein" not in res["products"]  # no analyzer on the demo


def test_missing_inputs_become_questions_and_lower_the_grade(tmp_path, capsys):
    path = synthetic.write_demo(tmp_path)
    raw = yaml.safe_load(path.read_text())
    del raw["scale_tickets"]
    path.write_text(yaml.safe_dump(raw))
    assert run(load_config(path), Level.L4).ok
    res = certainty.assess(load_config(path))
    items = _by_id(res)
    assert items["scale_tickets"]["status"] == "missing"
    assert res["products"]["yield_level"]["certainty"] == "low"
    assert "scale_tickets" in res["products"]["yield_level"]["because"]
    q = [x for x in res["questions"] if x["id"] == "scale_tickets"][0]
    assert "tickets add" in q["command"] and q["ask"]["fi"] and q["ask"]["en"]
    assert res["counts"]["missing"] >= 1
    # entering the antenna position turns the item into a known one
    inputs.set_machine(path, "combine-a", antenna_right_m=0.0, reason="measured with a tape")
    assert _by_id(certainty.assess(load_config(path)))["antenna:combine-a"]["status"] == "known"
    assert main(["gaps", str(path), "--all", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "Open questions" in out and "Scale tickets" in out and "low" in out
    assert main(["gaps", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["field_id"]


def test_text_lists_products_and_questions():
    res = {"field_id": "1", "year": 2026,
           "products": {"yield_level": {"name": {"fi": "Satotaso", "en": "Yield level"},
                                        "certainty": "low", "because": ["scale_tickets"]}},
           "questions": [{"id": "scale_tickets", "ask": {"fi": "Paljonko?", "en": "How much?"},
                          "command": "tickets add", "certainty": "low"}]}
    t = certainty.text(res, "fi")
    assert "heikko" in t and "Paljonko?" in t and "tickets add" in t
