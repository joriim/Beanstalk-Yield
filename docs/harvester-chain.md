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

## Input: vendor CSV

Most yield-monitor software can export points with mass flow already computed
(entry level L1B). Map your column names in the config:

```yaml
harvest_logs:
  - path: combine-a_2026.csv
    machine_id: combine-a
    format: csv
    columns:
      time: Time              # ISO-8601 text or seconds
      x: Easting              # or lon/lat (WGS84)
      y: Northing
      mass_flow: Mass_Flow_kg_s
      moisture: Moisture_pct
      swath: Swath_m
      speed: Speed_kmh
      heading: Heading_deg    # optional; derived from positions if missing
      header: Header          # optional; 1 = harvesting
      gnss_quality: GPS_Fix   # optional; 4 = RTK fix
    mass_flow_unit: kg/s      # or t/h
    speed_unit: km/h          # or m/s
    entry_level: L1B          # L2 if the vendor already applied the flow delay
```

Scale tickets (`scale_tickets:`) are a CSV with `mass_kg`, and optionally
`machine_id` and `moisture_pct`.

ISOXML TaskData is not read yet; export CSV meanwhile.

## What each stage does

| Stage | Operations |
|---|---|
| L1A | Read logs, unit conversion, project positions to the field CRS |
| L1B | Nothing for vendor exports (mass flow already computed) |
| L2 | Shift mass flow by τ_d and moisture by τ_m so each record gets the grain it cut; standardise to reference moisture; antenna → header offset; pass segmentation; record polygons; effective (non-overlapping) area; yield |
| L3 | Flags: header up, fill/finish, speed, moisture range, effective swath, GNSS, global outliers (median ± 3·MAD). Reconciliation factor k per machine from scale tickets |
| L4 | Mass apportioning of clean records to the fixed grid; quality bands; mass-conservation check |
| L5 | Sentinel-2 fusion, if `--to L5` and a `sentinel2` imagery entry exist |

Moisture standardisation happens in L2 rather than L1B, because it needs
moisture aligned to the same grain as the mass flow.

## Reconciliation and outliers

Reconciliation uses all harvested mass, including flagged records, because
they still carried grain to the tank. The exception is outlier records
(global or local): their logged value is a sensor error, so they enter the
reconciliation with the field median yield over their effective area. Without
this, 15 spikes in the synthetic field biased the whole map by −1 %.

## Outputs

In `output_dir`:

- `L3_<field>_<year>_records.parquet`: GeoParquet of record polygons with all
  columns and the flag bitmask (CSV without geometry if geopandas is missing)
- `L4_<field>_<year>.tif`: bands yield, coverage, n_records, spread,
  flagged_share, passes
- `L4_<field>_<year>_qa.json`: delays used, flag counts, k factors,
  mass-conservation check, warnings

## Not yet

Delay estimation from opposite passes (τ_d comes from the machine profile;
default 12 s with a warning), local Moran's I, field-boundary edge flag,
ISOXML, raw force (L0) signals.
