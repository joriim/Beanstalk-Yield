# Server (phase 2)

Not started. Planned components, from the design document:

| Component | Implementation |
|---|---|
| Machine and calibration registry | PostgreSQL tables |
| Catalog | pgSTAC, one STAC item per product |
| Storage | S3-compatible object store (L0, GeoParquet, COG) + PostGIS |
| Orchestration | Dagster: one asset graph per field-year; `dagster-slack` failure sensor → `#yield-data-suite` |
| Access | FastAPI with OGC API Features/Tiles; MapLibre viewer |
| Review | Slack Bolt app (Socket Mode) with QA buttons that call the API |

The core library must stay usable without any of this.
