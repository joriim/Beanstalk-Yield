# Gaps and certainty

The chain never stops because an input is missing: it fills the gap as well as it can.
`certainty.py` makes that visible. After every run it writes
`<field>_<year>_certainty.json`, the run prints the open questions, and the report starts
with the summary.

```bash
yieldsuite gaps field.yaml          # product grades and the open questions
yieldsuite gaps field.yaml --all    # every input and assumption
yieldsuite gaps field.yaml --json   # for other software
```

Every input or assumption is an item with a **status** (`known`, `estimated` from the data,
`assumed`, `missing`), a **certainty** (`high`, `medium`, `low`), **how it was filled**,
its **effect** on the results, and, where the user can help, a **question** with the
command or config key that takes the answer. Products are graded by the weakest item
they depend on.

| Item | Filled with | Asks for |
|---|---|---|
| Scale tickets | the monitor's own level (k = 1) | weighed loads (`tickets add`) |
| Antenna position | the profile value; lateral offset estimated from pass spacing | tape measurement (`machine set`) |
| Flow delay | vendor's, profile, estimate from opposite passes, or default | the delay |
| Moisture | combine sensor, calibrated to the analyzer; entered values replaced | dryer or lab moisture when the two disagree by > 2 points |
| Analyzer timing | clock offset from GPS (`clock_offset_s: auto`); transit 0 s | `transit_s` |
| Analyzer coverage, other machine | readings far from the logged position are not used | other sessions, the other combine's log |
| Protein level | the analyzer's own values | lab result (`quality.lab_samples`) |
| Area without records | satellite check (harvested or not), yield from the model | why the log is missing |
| Sowing date | the configured date (`crop.sowing_date_assumed: true` marks a guess) | the date |
| Prediction and gap fill | narrow gaps from measured neighbours; wide gaps by the image model (or IPAR, GPP, RUE) | the model, when it does not beat the field mean |
| Failed steps | listed with their consequence | – |

Products graded: yield level, yield pattern on the measured area, filled area, moisture,
protein. Entering an answer (for example `machine set ... --reason "measured"`) turns the
item into a known one on the next run.

The report shows the grades, the questions, a collapsible table of all items (for people
working with the data) and the list of machine-readable outputs (for software).

## Loud failures

A failed optional L5 step (light use, review, agroclimate) no longer disappears: it is
printed, listed in the model card (`problems`), in the run notes as `FAILED ...`, and as a
low-certainty item. `strict: true` in the config or `YIELDSUITE_STRICT=1` stops the run.
