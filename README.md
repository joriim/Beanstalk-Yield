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
pip install -e ".[dev]"          # core + tests; add [geo,ingest,qa,fusion,slack] as needed
yieldsuite validate examples/field-2026.yaml
yieldsuite plan examples/field-2026.yaml --to L5
pytest
```

## Status

Pre-alpha. Implemented and tested: moisture standardisation, flow-curve fitting
from weighed loads, georeferencing and record polygons, effective area, load
reconciliation, record filters, mass-conserving gridding, Slack notifier, and
the L5 Sentinel-2 step (openEO retrieval, yield-image model, regression-kriging
gap fill; see [docs/l5-sentinel2.md](docs/l5-sentinel2.md)).
Not yet: ingest adapters, delay neighbour pairs, local Moran's I, stage wiring
for L1–L4 in `pipeline.py`, the other L5 corrections. `yieldsuite run` reports
the first unimplemented stage instead of producing a partial map.

L5 can already run on an L4 raster made elsewhere:

```bash
pip install -e ".[geo,fusion]"
yieldsuite fuse examples/field-2026.yaml --l4 my_L4.tif
```

## Slack

Run summaries can be posted to `#yield-data-suite`; see [docs/slack.md](docs/slack.md).

## Licence

Core: Apache-2.0 ([LICENSE](LICENSE)). QGIS plugin: GPL-2.0-or-later
([qgis_plugin/LICENSE](qgis_plugin/LICENSE)), as QGIS requires.
