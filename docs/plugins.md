# Covariate plugins

Some useful covariates come from models or data that cannot live in this open
repository, for example a peat model trained on data whose licence forbids
redistribution. They plug in as separately installed Python packages.

## Using a plugin

```bash
pip install -e /path/to/omamaatu          # the plugin, installed next to the suite
yieldsuite covariate list                 # installed plugins
yieldsuite covariate fetch omamaatu field.yaml --grid output/L4_<field>_<year>.tif
yieldsuite zones L4_2025.tif L4_2026.tif --covariate covariates/covariate_omamaatu.tif
```

`fetch` asks the plugin for rasters over the field boundary, averages them onto
the suite grid by cell area (reprojecting if the CRS differs) and writes into the
field's own data folder:

| File | Content |
|---|---|
| `covariates/covariate_<plugin>.tif` | one band per plugin layer on the suite grid |
| `covariates/covariate_<plugin>.provenance.json` | plugin name and version, **licence**, source CRS and resolution, notes, plugin metadata |

The suite never imports a plugin by name and ships none of its code, models or
data; the licence travels with every output.

## Writing a plugin

Register a provider under the entry-point group `yieldsuite.covariates`:

```toml
[project.entry-points."yieldsuite.covariates"]
omamaatu = "omamaatu.yieldsuite_liitannainen:TurveSelittaja"
```

The provider (contract version 1, see `yieldsuite.plugins`) has `api_version = 1`,
`name`, `description`, `licence`, `version` and a method
`layers(boundary, crs) -> {"bands": {...}, "transform": (...), "crs": "...", "notes": [...], "meta": {...}}`
returning 2-D float arrays (NaN = no value) on its own grid.

## Known plugins

| Plugin | Layers | Licence |
|---|---|---|
| `omamaatu` (private package, not distributed) | probability of peat ≥ 10, ≥ 30, ≥ 40, > 60 cm and model agreement, 50 m | derived from GTK data under the GTK basic licence: internal and research use only |
