# Architecture

One headless Python core, three front ends.

| Layer | What | Who uses it |
|---|---|---|
| Core library `yieldsuite` | L0–L5 as functions on GeoParquet and COG; parameters from versioned machine profiles | everything below |
| Command-line tool | `yieldsuite run field.yaml --to L4` | researchers, advisors, CI, batch jobs |
| QGIS plugin | Processing algorithms + flag/parameter review | agronomists on the desktop |
| Server (phase 2) | PostGIS, pgSTAC, Dagster, FastAPI/OGC API, MapLibre viewer | many farms and years |

## Why not SharePoint, plain HTML or a QGIS-only tool

- **SharePoint** is not open source, cannot run processing, and is not a geodata store.
- **HTML** is used for the docs site and the web viewer, not for processing.
- **QGIS-only** logic cannot run in batch, on a server, or in tests; the plugin
  therefore wraps the core instead of containing it.
- **Google Earth Engine** is avoided as a dependency; Sentinel-2 comes from
  openEO/STAC on Copernicus Data Space.

## Product classes

| Inputs | Class |
|---|---|
| Harvester + scale tickets (+ imagery) | `absolute` |
| Harvester only | `absolute_unreconciled` |
| Imagery only (harvester data from other years optional) | `relative` |
