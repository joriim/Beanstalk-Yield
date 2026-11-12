# Grain quality channel (GrainSense Flow / GS Combine)

Protein, moisture and oil measured on the combine, turned into a protein
map, protein yield and nitrogen uptake. Quality is not mass: it is attached
to the harvester records and averaged with mass weights, never spread over
area.

## Getting the data

GrainSense cloud REST API (from the GrainSense public documentation,
16/10/2024): JWT login, combine data as **Flow measurement sessions**, export
as XLSX/CSV.

```bash
export GRAINSENSE_EMAIL=... GRAINSENSE_PASSWORD=...   # or GRAINSENSE_TOKEN
yieldsuite grainsense sessions --start 2026-08-01T00:00:00Z
yieldsuite grainsense export --uuid <session uuid> --format xlsx --out gs_2026.xlsx
```

Credentials stay in the environment, never in the store or the repository.
The live API has not been tested from the development environment.

Measurements in the API carry **milligrams** (protein, carbohydrate, oil,
moisture); percentages depend on each calibration's moisture basis
(`applicationData`): dry = protein + carbohydrate + oil, `dry` basis divides
by dry, `wet` by dry + moisture. `nutrient_percentages()` implements this;
the `fixed` basis is not documented completely and is refused. Exports
already contain percentages, so exports are the recommended input.

## Config

```yaml
quality:
  sources:
    - path: gs_2026.xlsx
      machine_id: combine-a        # the combine carrying the analyzer
      # columns: {time: ..., protein: ..., moisture: ...}   # only if not recognised
      # delay_s: 12                # default: the machine's tau_d
  protein_basis: dry               # basis of the analyzer's protein %: dry | wet
  n_factor: 5.7                    # wheat 5.7, barley/oats 6.25
  lab_samples: lab_protein.csv     # protein_lab_pct + start/end (or time)
```

## Q1–Q4

| Level | What happens |
|---|---|
| Q1 | Export read with column recognition (English/Finnish/German names: protein/proteiini, moisture/kosteus, oil/öljy …), fractions turned into %, time must be absolute |
| Q2 | Grain cut by a record at time t reaches the analyzer at t + delay; the nearest measurement within 0.75 × interval + 1 s is attached to that record (and so gets its position and mass) |
| Q3 | Bias to lab samples (mean lab − analyzer over the sample windows) on the analyzer's basis; then conversion to dry matter (wet: ÷ (1 − moisture)); protein outliers (median ± 3 MAD) flagged |
| Q4 | Per cell: mass-weighted protein (% DM), protein yield = yield × (1 − MC_ref) × protein, N uptake = protein yield / n_factor, share of the cell's mass with a quality value, record count |

Without lab samples the product class is `relative`: patterns are usable,
absolute protein levels are the analyzer's own.

Outputs: `Q4_<field>_<year>.tif` (bands `protein_pct_dm`, `protein_kg_ha`,
`n_uptake_kg_ha`, `quality_mass_share`, `n_records`) and
`Q4_<field>_<year>_qa.json`.

Synthetic check (analyzer bias +0.5 %-points, 5 s interval, 12 s delay): lab
bias estimated −0.51, protein map RMSE 0.06 %-points against the truth.

## Analyzer moisture in the yield map

If the analyzer export has a moisture column (GrainSense: `Kosteus [%]` /
`Moisture`), it corrects the combine's moisture before the mass is
standardised to the reference moisture (L2), so the correction reaches the
reconciliation, the grid and L5. One point of moisture bias is about 1.2 % of
dry yield.

```yaml
quality:
  moisture: auto        # auto | calibrate | replace | off
```

- `auto` (default) = `calibrate` when at least 30 analyzer readings match
  records that carry combine moisture; the analyzer values directly when the
  combine logged no moisture; otherwise nothing changes.
- `calibrate`: analyzer = a + b × combine, fitted on the matched records
  (Theil–Sen; a median offset when the combine moisture varies by less than
  1 point or the slope is implausible) and applied to every record, also
  where the analyzer session did not run.
- `replace`: the analyzer reading where one matches, the calibrated combine
  value elsewhere.

Failed scans are dropped first (moisture outside 8–40 %, protein outside
6–25 %, carbohydrate 0 %). Dry or standard-moisture vendor yields (yield-point
exports) are first turned back into wet mass with the combine moisture the
vendor used, `wet = dry × (100 − vendor_ref) / (100 − combine)`, then
standardised with the corrected moisture. The fit, the number of matched
readings and the change of the mass at reference moisture are in the L4 QA
(`analyzer_moisture`). The analyzer's own moisture is not lab-checked by the
suite; a lab moisture sample makes the correction traceable.

## Real exports (November 2026)

A GrainSense app session export (XLSX) has a `Summary` sheet and one sheet per
session. Each session sheet starts with a summary row; the measurement table
follows below it with Finnish headers (`Mittausaika`, `Proteiini [%]`,
`Kosteus [%]`, `Hiilihydraatti [%]`, `Öljy [%]`, `Typpi [N%]`, `Leveysaste`,
`Pituusaste`). `read_quality()` finds the table header itself; pick the session
with `sheet:`. Times are UTC (`+0000`), protein is on dry basis and moisture on
wet basis (the summary row says so), and every measurement carries a position.

On the first real fields, the analyzer's timestamp was about 20 s earlier than
the harvester record at the same position (median over hundreds of matched
positions). Use the positions to estimate `delay_s` for a combine and session:
the harvester's own delay correction and the analyzer's geotag decide the sign.

## Still unknown

Whether the analyzer's geotag marks where the grain was cut or where it was
measured; with it, `delay_s` could be derived instead of estimated.
