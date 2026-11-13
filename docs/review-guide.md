# Review guide: splitting PR #1

PR #1 holds the whole pre-alpha (about 25,000 added lines). It cannot be reviewed as one
piece. Proposed split into nine stacked pull requests, each reviewable on its own, in the
order they build on each other. Sizes are added lines including tests.

| # | Part | Paths | Lines | What to check |
|---|---|---|---|---|
| 1 | Ingest and harvester chain L1–L4 | `ingest/`, `l2/`, `l3/`, `l4/`, `stages.py`, `pipeline.py`, `config.py`, `registry.py`, `schema.py`, `synthetic.py`, `timeutil.py` | ~3,400 | units and column recognition, delay and offset geometry, flags, mass conservation |
| 2 | Audit and inputs | `audit.py`, `inputs.py`, `l2/offset.py` | ~900 | manifest hashes, YAML round-trip, ticket checks, lateral-offset estimator |
| 3 | Public data and weather | `public/`, `weather/`, `covariates/`, `plugins.py` | ~2,500 | licences and attribution, network hosts, FMI radiation model |
| 4 | Quality and moisture | `quality/` | ~1,150 | alignment rules (`align.py`), position gate, entered-moisture detection |
| 5 | L5 | `l5/` | ~3,300 | light-use models and their cross-validation, kriging, review checks, harvest check |
| 6 | Certainty and report | `certainty.py`, `report.py` | ~2,050 | item rules and grades, wording of questions |
| 7 | Visualisation | `viz/` | ~2,850 (600 of it vendored three.js) | viewer code, data packing, video |
| 8 | Zones, N balance, prescriptions | `zones.py`, `prescription.py` | ~620 | clustering, ISOXML output |
| 9 | Front ends | `cli.py`, `tasks.py`, `store.py`, `mcp_server.py`, `qgis_plugin/`, `drone/` | ~3,400 | command surface, store audit log, QGIS wrappers |

Docs and tests travel with the part they describe.

Until the split exists: **feature freeze**. Only fixes, tests and refactors that shrink
`cli.py` and `report.py` go in.

How to split without rewriting history: for each part, create a branch from `main`
(part 1) or from the previous part, check out that part's paths from
`ccr-0e1a72ee-l8iljf`, run the tests that belong to it, and open a pull request against
the previous part's branch. Later parts import earlier ones, never the other way round;
the two exceptions to untangle first are `stages.py` importing `quality` and `l5`
(parts 4 and 5), which should move behind the stage registry.

Known debts for the reviewers (not fixed yet):

- `report.py` (1,650 lines) mixes figures, bilingual text and HTML; `cli.py` has 29 subcommands.
- Thresholds (15 m gate, entered-moisture rules, harvest-check cut-offs) are module constants
  tuned on four fields; they are not in the run manifest.
- Europe/Helsinki and EPSG:3067 are assumed in several places (`viz`, `l5/run.py`, Paituli DEM).
- The viewer JavaScript has no automated test; it is checked by hand in a headless browser.
- QGIS tests need a QGIS runtime; the MCP server has not been exercised with the moisture,
  gate, certainty and light-use changes.
- Two stores (a `field.yaml` folder and `store.py`) keep scale tickets in different formats.
