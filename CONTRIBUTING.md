# Contributing

## Design rules (from the design document)

1. L0 is never modified; everything above it is derived and reproducible.
2. Each record carries its entry level, so vendor-applied corrections are never applied twice.
3. Records are flagged, never deleted.
4. Mass conservation is an automated test: L4 cell masses add up to the reconciled field mass.
5. All years and images share one grid origin.
6. Imagery only corrects within the same field-year; filled cells are labelled and kept out of model training.

## Practicalities

- The core (`src/yieldsuite`) must not import QGIS, Slack, or server code. Heavy
  dependencies go in optional extras and are imported inside the function that needs them.
- Every model function gets a test with a synthetic case whose answer is known.
- Never commit farm data. Use synthetic fixtures; `data/` and `output/` are git-ignored.
- `ruff check . && ruff format --check . && pytest` must pass before a pull request.
