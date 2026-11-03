# Yield Data Suite

Open-source processing chain for **as-good-as-achievable yield maps** from
combine harvester logs, weighed loads, drone and public satellite data
(Sentinel-2).

The chain follows six processing levels, modelled on NASA EOSDIS data levels.
Each level has a defined input, model, output and uncertainty, so any
field-year can be reprocessed from raw data when a model improves.

| Level | Content |
|---|---|
| L0 | Raw harvester log, archived as received |
| L1A / L1B | Decoded samples in SI units → mass flow at reference moisture |
| L2 | Georeferenced record polygons: delay, antenna offset, effective area, yield |
| L3 | Reconciled to weighed loads, quality-flagged (records are never deleted) |
| L4 | Mass-conserving grid (10/20 m Cloud-Optimised GeoTIFF) with quality bands |
| L5 | Fused with imagery: residual shift, pass stripes, gap fill, zones |

Full design: [docs/design/processing-levels.md](docs/design/processing-levels.md).

## What each data stack can produce

| Inputs | Product class |
|---|---|
| Harvester + scale tickets (+ imagery) | `absolute` (t/ha, reconciled) |
| Harvester only | `absolute_unreconciled` |
| Imagery only (harvester from other years optional) | `relative` |

Every product carries its class, so a relative map is never mistaken for an absolute one.

## Layout

```
src/yieldsuite/   core library (headless, pip-installable)
  ingest/         vendor + ISOXML adapters → common record schema
  l1/ … l5/       one package per processing level
  registry.py     versioned machine profiles (delays, offsets, curves, k factors)
  catalog.py      STAC product metadata
  pipeline.py     runs a field-year; becomes Dagster assets on the server
  notify/         optional Slack notifications
  cli.py          `yieldsuite` command
qgis_plugin/      QGIS Processing provider (GPL-2.0-or-later)
server/           phase 2: PostGIS, pgSTAC, Dagster, FastAPI, web viewer
docs/             MkDocs site
examples/         field-year config
tests/            pytest suite
```

## Quick start

```bash
pip install -e ".[dev,geo]"
yieldsuite demo demo/                     # synthetic field with a known true yield
yieldsuite run demo/field.yaml --to L4    # harvester log -> calibrated yield grid
pytest
```

## Status

Pre-alpha. Working end to end on vendor CSV exports:

- **L1A–L4** ([docs/harvester-chain.md](docs/harvester-chain.md)): flow-delay
  shift, antenna offset, moisture standardisation, effective area, quality
  flags, load reconciliation per machine, mass-conserving grid. On the
  synthetic field the map is within about 0.04 t/ha RMSE of the truth.
- **L5** ([docs/l5-sentinel2.md](docs/l5-sentinel2.md)): Sentinel-2 via openEO,
  yield-image model, regression-kriging gap fill. Not yet run against the live
  backend.

Not yet: ISOXML and raw-force (L0) input, delay estimation from opposite
passes, local Moran's I, field-boundary edge flag, the other L5 corrections
(position shift, stripes, zones), the QGIS algorithm wiring, the server.

## Työnkulkuohje (suomeksi)

QGIS, WebODM, openEO ja tiladata-MCP vaiheittain:
[docs/ohje/tyonkulku-qgis-webodm-openeo-mcp.md](docs/ohje/tyonkulku-qgis-webodm-openeo-mcp.md).

## Slack

Run summaries can be posted to `#yield-data-suite`; see [docs/slack.md](docs/slack.md).

## Licence

Core: Apache-2.0 ([LICENSE](LICENSE)). QGIS plugin: GPL-2.0-or-later
([qgis_plugin/LICENSE](qgis_plugin/LICENSE)), as QGIS requires.
