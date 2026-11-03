# L5 · Sentinel-2 fusion via openEO

Uses Sentinel-2 L2A imagery from the same season to fill gaps in an L4 yield
grid. The model is fitted only on reliable cells of the **same field and year**.

## What runs where

| Step | Where | Module |
|---|---|---|
| Grid cells → GeoJSON polygons (EPSG:4326) | local | `l5.sentinel2.grid_cells_geojson` |
| Load `SENTINEL2_L2A`, mask clouds with SCL (classes 0, 1, 3, 8, 9, 10, 11), compute indices per pixel, mean per cell and date | **openEO backend** (Copernicus Data Space) | `l5.sentinel2.build_process_graph` |
| Drop cloudy dates; per index, season mean and max (plus per-date values for random forest) | local | `l5.features.feature_matrix` |
| Fit Y = f(V) on reliable cells (linear or random forest) with spatial block cross-validation | local | `l5.model.fit_yield_image_model` |
| Regression kriging of residuals into low-coverage cells, σ² = σ_f² + σ²_OK | local | `l5.gapfill.gap_fill` |
| Write L5 GeoTIFF + model card JSON | local | `l5.run.run_sentinel2_fusion` |

Only grid-cell polygons leave your machine; yield data never does.

## Setup

1. Create a free account at <https://dataspace.copernicus.eu>.
2. `pip install "yieldsuite[geo,fusion]"`
3. On the first run, the openEO client prints a login link (OIDC device flow) and caches a refresh token.

## Config

```yaml
imagery:
  - kind: sentinel2
    start: 2026-05-01
    end: 2026-08-16              # exclusive
    indices: [NDVI, NDRE, CIre, NDMI]   # also available: GNDVI
    max_cloud_cover: 80          # scene pre-filter
    min_valid_share: 0.8         # drop dates where < 80 % of cells are cloud-free
```

Choose the window around the period where canopy is most related to yield,
e.g. 105–135 days after planting for maize. For cereals, several dates work
better than one image (see the design document).

## Run

```bash
yieldsuite fuse field.yaml --l4 L4_field_2026.tif               # linear model
yieldsuite fuse field.yaml --l4 L4_field_2026.tif --method rf   # random forest
yieldsuite fuse field.yaml --l4 L4.tif --notify slack           # post summary to #yield-data-suite
```

The L4 raster needs a band described as `yield` (t/ha). A `coverage` band
(0–1) is used when present; otherwise every cell with a yield counts as
covered. Cells with coverage < 0.5 or flagged share > 0.5 are filled.

Sentinel-2 features are cached as `L5_<field>_<year>_s2_features.csv`; delete
it to fetch again. Use `--batch` for large fields (openEO batch job instead of
a synchronous request).

## Outputs

`L5_<field>_<year>.tif` bands:

| Band | Meaning |
|---|---|
| `yield` | measured yield where reliable, filled elsewhere [t/ha] |
| `source` | 0 no value · 1 measured (L4) · 2 filled from imagery |
| `sigma` | standard error of filled cells [t/ha] |
| `prediction` | image-model prediction f(V) for every cell |
| `relative_yield` | yield as % of the field mean |
| `coverage` | L4 coverage, carried over |

`L5_<field>_<year>_model_card.json` holds the features, R², RMSE, spatial-block
CV RMSE and bias, the Sentinel-2 dates used and dropped, the variogram, and the
filled share.

## Grid alignment

With `crs: EPSG:32635` (UTM 35N), 20 m cells and origin (0, 0), grid cells
coincide with Sentinel-2 20 m pixels. With EPSG:3067 each cell is averaged
from the pixels it overlaps, which smooths slightly.

## Not yet included

Residual position shift, pass-stripe levelling, zones, drone orthomosaic
features, and imagery-only relative maps (no harvester data).
