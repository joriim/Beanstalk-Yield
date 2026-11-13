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

## Position gate

GrainSense exports carry the analyzer's own GPS position. A reading is attached to a
record only if the two positions are within `max_dist_m` (default 15 m) at the matched
moment:

```yaml
quality:
  sources:
    - path: session.xlsx
      machine_id: combine-a
      delay_s: -20        # clock offset of the analyzer (see yieldsuite docs); not the flow delay
      max_dist_m: 15      # null switches the gate off
```

Matching by time alone puts a reading on the wrong place when the analyzer is on another
machine or the clock offset is wrong: on one field 245 of 395 matched records got a reading
whose own position was more than 15 m away (164 more than 50 m). The number of dropped
records is in the Q4 QA (`position_gate`) and in the warnings. The same gate applies to
the moisture correction and to the protein, oil and moisture maps in the timelapse. The
clock offset itself (here 21-22 s) is best read from the positions: the offset with the
smallest median distance between the analyzer track and the yield log.

## Entered combine moisture

A combine moisture sensor that is not reading (measuring cell empty, start of a
task, manual entry) logs one value unchanged for minutes. Such values are not
measurements, and a dry yield standardised with them is off by about 1.2 % per
point. L2 flags, per machine, the whole log when it hardly varies, runs of ≥ 30
identical values of a value that fills ≥ 100 records in such runs (e.g. 17.0 %),
any run ≥ 150 records, a value logged ≥ 50 times and ≥ 5 × as often as the
values next to it (a default written between real readings), readings outside
8–40 %, and transients more than 4 points from the running median of 61
records (sensor emptying at pass ends). Those records get the analyzer reading where one
matches, else moisture interpolated in time from measured neighbours (≤ 10 min),
else the field median. This works without an analyzer too. The records keep
`moisture_sensor` (as logged) and `moisture_source` (0 measured, 1 analyzer,
2 calibrated, 3 interpolated, 4 median, 5 vendor reference).

## Moisture map

`L4_<field>_<year>_moisture.tif` (every run with moisture): mass-weighted
moisture per cell as harvested, its harvest-time trend (30 min running median:
dew in the morning, drying during the day), the place part (field median +
departure from the trend: wetter spots with late ripening, lodging or wet
soil) and the share of mass with measured moisture. Harvest order is partly
spatial, so broad gradients along the harvest direction end up in the trend.

## Other analyzer quantities

Q4 also holds the analyzer's moisture (`moisture_analyzer_pct`) and any other
quantity the export carries (`carbohydrate_pct_dm`, `oil_pct_dm`,
`starch_pct_dm`), mass-weighted per cell. The QA lists which quantities were
measured and which columns were empty. GrainSense exports seen so far
(barley BRL_25_1, oat OAT_25_1): protein, moisture, carbohydrate and N %
(= protein / 6.25, not mapped separately) are measured; oil is "-" (not in
these calibrations); wet gluten, broken and green kernels and hectolitre
weight are empty. Each reading carries its GPS position: the share of
readings on the field is checked, so a session (sheet) assigned to the wrong
field is caught. Failed scans (carbohydrate 0 %, moisture outside 8–40 %) are
dropped from every map.

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
