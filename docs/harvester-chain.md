# Harvester chain L1A → L4

`yieldsuite run field.yaml --to L4` turns harvester logs and scale tickets into
a mass-conserving yield grid.

## Try it

```bash
yieldsuite demo demo/                    # synthetic field with a known true yield
yieldsuite run demo/field.yaml --to L4
```

The synthetic combine logs mass flow 12 s late, reads 7 % high, records grain
at 18 % moisture and has 15 sensor spikes. The chain recovers the true map to
about 0.04 t/ha RMSE on well-covered cells.

## Input: almost any yield or machine data file

```bash
yieldsuite inspect my_export.csv            # or .txt .xlsx .shp .gpkg .geojson .zip TASKDATA
yieldsuite inspect export.csv --crop wheat --vendor-moisture 15.5
```

`inspect` reports what it recognised and prints a `harvest_logs` entry with
every assumption written out. Paste it into `field.yaml` (or use the MCP tool
`register_harvest_log`, which also stores the file in L0).

| Source | Detection | Entry level |
|---|---|---|
| CSV / TXT / TSV | delimiter, decimal comma and encoding sniffed (Nordic exports with `;` and `,` work) | L1B (mass flow) or L2 (yield points) |
| Excel | first sheet or `sheet:` | as above |
| Shapefile, GeoPackage, GeoJSON, zipped shapefile | geometry and CRS from the file | as above |
| ISOXML TaskData (folder, TASKDATA.XML or zip) | TimeLog binaries decoded; DDI 87 wet mass flow, 182 dry mass flow, 99 moisture, 67 width, 397 speed, 141 work state; values forward-filled | L1B |
| Ag Leader advanced text | `format: agleader_advanced` (no header, fixed columns) | L1B |

**Columns** are recognised from English, Finnish and German names and from
common vendor field names (e.g. `Yld_Vol_Dr`, `VRYIELDMAS`, `Swth_Wdth_`,
`Massavirta`, `Työleveys`, `Ertrag`, `Arbeitsbreite`). An explicit
`columns:` mapping always wins.

**Units** come, in this order, from the config, the format itself, the
column header (`kg/s`, `lb/s`, `t/h`, `bu/ac`, `kg/ha`, `dt/ha`, `ft`, `in`,
`mph`, `km/h`, `ppm` …), consistency with the GNSS positions (distance and
speed), or the value range. The report says which; guessed units come with a
warning. Ambiguous cases (a bu/ac yield without a crop, a mass flow with no
unit) stop with a message instead of guessing.

**Yield-point exports** (yield per area at each point, typical of
shapefile exports from farm-management software) are turned back into mass
as yield × distance × swath, so they still reconcile to scale tickets. They
enter at L2 because the vendor has already applied the flow delay; set the
machine's antenna offset to 0 if the vendor positions are already at the
header. Dry/standard yields are re-standardised from
`vendor_reference_moisture_pct` to the suite's reference moisture.

ISOXML decoding is experimental: it is tested on files written to the
specification's layout, not yet on real terminal exports. Check the first real
TaskData against the vendor's totals.

Example entry for the synthetic demo:

```yaml
harvest_logs:
  - path: combine-a_2026.csv
    machine_id: combine-a
    format: table
    columns: {time: Time, x: Easting, y: Northing, mass_flow: Mass_Flow_kg_s}
    units: {speed: km/h}
```

Scale tickets (`scale_tickets:`) are a CSV with `mass_kg`, and optionally
`machine_id`, `moisture_pct` and `field_share` (the share of a load that came
from this field when one load holds grain from several fields).

## Entering antenna offsets and scale tickets

Both are entered with commands that edit `field.yaml` in place (comments are
kept, the file is parsed again before it is written) and append every change,
with the old value, who and why, to `inputs_log.jsonl` next to the config. The
same is available in QGIS (*Inputs → Machine profile*, *Inputs → Add scale
ticket*) and in the MCP server (`estimate_antenna_offset`,
`update_machine_profile`, `add_scale_ticket`).

**Antenna position.** `antenna_offset_m` is `[forward, right]` from the GNSS
antenna to the header centre, in metres. An antenna mounted 0.5 m left of the
centre line has `right = +0.5`; one on the cab roof 3 m behind the cutter bar
has `forward = +3`.

```bash
yieldsuite machine show field.yaml
yieldsuite machine estimate-offset field.yaml     # lateral offset from the pass spacing
yieldsuite machine set field.yaml --machine combine-a --antenna-right 0.5 \
    --reason "measured with a tape, antenna 0.5 m left of the centre line"
```

`estimate-offset` uses adjacent passes driven in opposite directions. A lateral
offset `e` left out of the profile draws facing pairs `w - 2e` apart and
back-to-back pairs `w + 2e` apart; in back-and-forth harvesting the two kinds
alternate, so `e = (median back-to-back - median facing) / 4`, independent of
the header width and the driver's overlap. The answer comes with a bootstrap
95 % interval and is called significant at |e| ≥ 0.1 m with an interval not
covering 0; confirm it with a tape before entering it. The forward offset
cannot be told apart from the flow delay this way: measure it. In the L5
critical review a lateral offset shows up as a level difference between passes
driven in opposite directions.

**Scale tickets.**

```bash
yieldsuite tickets add field.yaml --machine combine-a --mass-kg 12340 --moisture 19.5 --ref 4711
yieldsuite tickets add field.yaml --machine combine-a --mass-t 8.2 --share 0.4 --ref 4712
yieldsuite tickets import field.yaml vaaka.csv --map mass_kg=Netto --map moisture_pct=Kosteus \
    --map ticket_ref=Kuitti --unit kg --machine combine-a
yieldsuite tickets list field.yaml
yieldsuite tickets remove field.yaml --ref 4712 --reason "wrong field"
yieldsuite run field.yaml --to L5                 # reconciles per machine, product class absolute
```

The first ticket creates `scale_tickets.csv` and points the config to it.
Duplicate ticket numbers, unknown machines, implausible masses or moisture,
and a mix of machine-specific and unassigned tickets are refused before
anything is written. Imports read `,` or `;` separated files with decimal
commas, or Excel. Give the moisture only if the weight is wet; a dryer's dry
weight at the reference moisture is entered without it. With tickets for every
machine the chain multiplies each machine's logged mass by
`k = Σ scale / Σ monitor`, so the field total equals the weighed total.

## Multi-field point exports

Some services deliver one file of yield points for many parcels, with the
vendor's own quality verdict per point and a raw sensor flow next to the
finished yield. Pick the field's rows, take the finished yield, and read the
header state from the verdict:

```yaml
harvest_logs:
  - path: data/yield_2026_points.gpkg
    machine_id: combine-a
    layer: points
    where: {pid: "1641431471"}           # one parcel out of the export
    ignore_columns: [flow_raw, yield_rel] # raw flow of unknown unit: not used
    columns: {time: time_local, yield_dry: yield_raw, moisture: moisture_pct,
              distance: dist_m, swath: width_m, header: drop_reason, gnss_quality: gnss_q}
    units: {yield_dry: kg/ha, distance: m, swath: m, moisture: "%"}
    header_up_values: [header_up]         # only this verdict means "header up"
    vendor_reference_moisture_pct: 14
    time_zone: Europe/Helsinki            # timestamps carry no offset
```

The records enter at L2 (the vendor's yields are taken as delay-corrected),
mass is rebuilt as yield x distance x swath, and the suite's own quality flags are
set from the data; the vendor's other verdicts (pass edge, outlier, speed) are
not copied, so the two can be compared.

## What each stage does

| Stage | Operations |
|---|---|
| L1A | Read logs, unit conversion, project positions to the field CRS |
| L1B | Nothing for vendor exports (mass flow already computed) |
| L2 | Shift mass flow by τ_d and moisture by τ_m so each record gets the grain it cut; standardise to reference moisture; antenna → header offset; pass segmentation; record polygons; effective (non-overlapping) area; yield |
| L3 | Flags: header up, fill/finish, speed, moisture range, effective swath, GNSS, field edge, global outliers (median ± 3·MAD), local outliers (negative local Moran's I with a permutation test, or > 4 robust SD from the neighbours' median). Reconciliation factor k per machine from scale tickets |
| L4 | Mass apportioning of clean records to the fixed grid; quality bands; mass-conservation check |
| L5 | Sentinel-2 fusion, if `--to L5` and a `sentinel2` imagery entry exist |

Moisture standardisation happens in L2 rather than L1B, because it needs
moisture aligned to the same grain as the mass flow.

## Flow delay

The delay τ_d (header → mass-flow sensor) is the single parameter that
damages a map most when wrong. It comes from the machine profile; the chain
also estimates it from the data every run:

```bash
yieldsuite delay field.yaml        # per machine: profile value, estimate, curve
```

The estimate is the delay that makes adjacent passes driven in opposite
directions agree best (mean squared difference of log yields, records
compared at their centres, yields smoothed with a 5-record running median so
sensor spikes do not decide). It is reported as *identifiable* only when the
discrepancy at a typical wrong delay is at least twice the minimum and the
minimum is not at the edge of the 0–30 s search range.

| Profile has τ_d | Estimate | Used |
|---|---|---|
| yes | agrees within 3 s | profile |
| yes | differs by > 3 s | profile, with a QA warning to check |
| no | identifiable | estimate, with a QA warning to store it |
| no | not identifiable | 12 s default, with a QA warning |

On the synthetic field (true 12 s) estimates over six random seeds were
11.6–12.4 s. A field without along-track yield pattern gives a flat curve and
is correctly reported as not identifiable. Vendor exports that already
applied the delay (entry level L2) are not shifted again.

## Reconciliation and outliers

Reconciliation uses all harvested mass, including flagged records, because
they still carried grain to the tank. The exception is outlier records
(global or local): their logged value is a sensor error, so they enter the
reconciliation with the median yield of the clean records around them
(within twice the local-outlier radius; the field median where fewer than four
are in range) over their effective area. Without this, 15 spikes in the
synthetic field biased the whole map by −1 %. The QA report's `outlier_mass`
entry gives the logged and imputed mass.

## Outputs

In `output_dir`:

- `L3_<field>_<year>_records.parquet`: GeoParquet of record polygons with all
  columns and the flag bitmask (CSV without geometry if geopandas is missing)
- `L4_<field>_<year>.tif`: bands yield, coverage, n_records, spread,
  flagged_share, passes
- `L4_<field>_<year>_qa.json`: delays used, flag counts, k factors,
  mass-conservation check, warnings
- `<field>_<year>_run.json`: run manifest for audit: software version and
  commit, start/end time, settings and machine-profile hashes, and every input
  and output with size and SHA-256 (`yieldsuite verify <manifest>` re-checks them)

`yieldsuite report field.yaml [--lang fi|en]` then writes `output/report/`: a
figure per level (L1A records, L1B signals, L2 yield per record, L3 flags, L4
grid and quality bands; L5 and Q4 when run), the process description of each
level with this run's parameters (`*_process.md`), the audit (`*_audit.json`,
with a fresh re-hash) and one self-contained HTML report with a pipeline
diagram, summary charts (yield distribution, record flow, mass balance) and a
caption for every figure. `--notes findings.txt` puts the analyst's findings
first ('## ' headings, '- ' bullets, `**bold**`), `--title` names the report,
and `--redact` shows file names instead of paths so the report can be shared.
Needs `pip install yieldsuite[report]`.

## Not yet

Moisture-delay (τ_m) estimation, field-boundary edge flag,
raw force (L0) signals.
