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
covered. Cells with coverage < 0.5 or flagged share > 0.5 are filled, but only
inside the field: cells whose centre lies inside the boundary (`boundary:` in
the config), or without a boundary, inside the convex hull of the measured
cells. Neighbouring land in the grid's rectangle is never filled; the model
card records the mask (`field_mask`).

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

## Corrections from the records (chain runs)

When L5 runs inside the chain (`yieldsuite run --to L5`), the L3 records are
available and two corrections of the design document are applied before the
final fusion. With an L4 raster only (`yieldsuite fuse`) they are skipped and
the model card says so.

The spatial reference for both is the image prediction f(V), fitted on the L4
grid without kriging (a kriged residual would carry the stripes back in).

**Correction 1 – residual shift (a check).** Records are moved back along
their own heading by δ (−24…+24 m), re-binned to the grid and correlated with
f(V). A clear non-zero optimum (correlation gain ≥ 0.01, not at the edge of
the range) is reported as a suggested extra delay δ / speed in the model card
and the run notes. The map is not edited: correct τ_d in the machine profile
and re-run, as the design document specifies.

**Correction 2 – pass stripes.** log Y_i = log f(V) + b_p + e. Residuals are
averaged per pass and grid cell first (the reference is only resolved per
cell), b_p is the shrunken pass mean with method-of-moments variances, the
gains are renormalised so the field total is unchanged, and the corrected
record masses are apportioned to the grid again. Pass effects with σ_b below
3 % are reported but not corrected: a 20 m reference cannot separate them
from within-cell yield gradients.

Synthetic results (true yield known, image NDVI linear in yield with noise):

| Case | L4 RMSE | L5 RMSE | Result |
|---|---|---|---|
| pass gains σ 10 % | 0.485 | 0.162 | σ_b 0.101 estimated, levelled |
| pass gains σ 5 % | 0.172 | 0.132 | σ_b 0.052, levelled |
| no stripes | 0.043 | 0.043 | σ_b 0.014 reported, not corrected |
| τ_d 0 s (true 12 s) | 0.435 | 0.435 | shift check suggests +11 s |

## Live check

Verified against Copernicus Data Space (November 2026): `SENTINEL2_L2A`
offers every band the suite requests (B04, B05, B07, B08, B8A, B11, SCL),
and the backend validates the process graph without errors. A full run
still needs a Copernicus Data Space account.

## Grid alignment

Grid cells coincide with Sentinel-2 20 m pixels when the grid uses the UTM
zone of the field's Sentinel-2 tile, 20 m cells and origin (0, 0). Finland
spans zones 34–36: western Finland (e.g. Seinäjoki, tile 34VEQ) is
`EPSG:32634`, central `EPSG:32635`, eastern `EPSG:32636`. Choose it per
field: `yieldsuite s2-search` prints the EPSG code of the field's tiles. With EPSG:3067 each cell is averaged from the
pixels it overlaps, which smooths slightly.

## Prediction and gap fill

Two cases, as in the yield-mapping literature and in vendor software:

- **Narrow gaps** (edge strips, a missed pass, single holes: at most `gap_fill.narrow_gap_m`
  wide, default 20 m, `0` switches it off) are filled from the measured cells around them by
  kriging (block support: the 10 m cell means; field mean as trend). Such cells are better
  known from their neighbours than from imagery, and they are filled even where image
  features are missing. Source code 3.
- **Wide gaps** are filled by the prediction model plus kriging of its residuals. Source
  code 2. The model is chosen with `light_use.fill`:

| `light_use.fill` | Model |
|---|---|
| `image` (default) | statistical model on the image features of the same field (season statistics and the final image of Sentinel-2, PlanetScope if present), fitted on the measured cells |
| `ipar` | Y = RUE × IPAR, one field RUE (median of Y / IPAR on the measured cells) |
| `gpp` | Y = k × GPP: IPAR weighted by the daily temperature limit f(T) |
| `rue` | Y = RUE(x) × IPAR, RUE varying over the field (distance-weighted, `rue_sigma_m` 30 m) |
| `auto` | the best of `ipar`, `gpp`, `rue` in spatial block cross-validation |

The image model is the default because it was the best on every field tested and because
it is what the literature does (a model calibrated on the harvested part of the same field).
On the four fields of 2026 the block-CV RMSE (t/ha) was:

| Field | ipar | gpp | rue | field mean | image |
|---|---|---|---|---|---|
| 1641431471 | 1.94 | 1.94 | 1.43 | 1.62 | 0.94 |
| 1641175029 | 1.17 | 1.17 | 1.04 | 1.07 | 0.97 |
| 1641426926 | 0.77 | 0.77 | 0.74 | 0.64 | 0.63 |
| 1640608991 | 1.16 | 1.15 | 1.07 | 1.05 | 0.76 |

NDVI saturates in a closed cereal canopy, so intercepted light varies little inside a field;
a light-use-efficiency model did not explain within-field wheat yield in the literature either
without assimilating leaf area. The light-use maps (IPAR, GPP, RUE) and this comparison are
still produced in every run (`light_use.fill_candidates` in the model card), and the user can
switch model. With `auto`, when the best two are within 2 % (`min_gain`) there is no clear
winner and `prefer` (default `gpp`) is used. When the model in use does not beat the field
mean, the certainty summary says that the filled values are in practice local averages.

## Was the unlogged area harvested?

When the critical review flags field area without harvest records (`unlogged`
at watch or alert), L5 looks at free satellite images around the harvest
(`l5/harvest_check.py`, images from `public/optical.py`):

- **Sources**: Sentinel-2 L2A and Landsat 8/9 Collection 2 L2 from Microsoft
  Planetary Computer (no account; hosts `planetarycomputer.microsoft.com`,
  `sentinel2l2a01.blob.core.windows.net`, `landsateuwest.blob.core.windows.net`),
  plus any PlanetScope scenes already in the config. Only the field window is
  read; clouds, shadows and snow are masked (S2 SCL, Landsat QA_PIXEL).
- **Signal**: harvest turns ripe crop into stubble, which lowers NDTI
  (SWIR1 vs SWIR2) and usually NDVI. Per image family, the change between the
  last clear scene before the first logged harvest day and each clear scene
  after the last one is measured on the logged (harvested) area. A pair is used
  only if that change stands out from the cell-to-cell spread (separation ≥ 2).
- **Verdict per unlogged cell**: its own change projected on the harvested
  area's change, ~1 = changed like the harvested area, ~0 = unchanged.
  `harvested` (≥ 0.6, with the first date), `harvested_before` (already stubble
  before the logged harvest), `not_harvested` (≤ 0.3 in every pair), `unclear`.
  Regions are judged by the majority of their pure cells.
- **Resolution**: only cells at least half a source pixel inside the unlogged
  area and the field are classified (20 m SWIR, 30 m Landsat). Narrower strips,
  typically the outermost header width along the edge, are `too_narrow`.
- **PlanetScope**: when no usable pair exists or ≥ 30 % of the unlogged area is
  too narrow or unclear, the review suggests a PlanetScope scene (3 m) after
  the harvest. If a Planet key is available, the free search lists scenes with
  the parcel's clear share and the quota they would use; ordering stays manual
  (`yieldsuite planet order ... --confirm`, see the planet skill). Once the
  scene is in the config (`imagery: - kind: planetscope`), the check uses it.

The result is a review row `harvest_status`, `L5_<field>_<year>_harvest_check.json`
and `.png`, and a table and figure in the report. To run it on existing
outputs without redoing L5:

```bash
yieldsuite harvest-check field.yaml            # --days-after 40, --no-planet
```

Settings (optional): `review: {harvest_check: auto | true | false}` or a mapping
with `days_before`, `days_after`, `families: [S2, LS]`, `planet_search`.

The L4 grid covers the whole field boundary (not just the logged area), so
edge strips without records count as unlogged. Cached Sentinel-2 features
record their grid (`*_s2_features.grid.json`); after a grid change they are
fetched again, or moved onto the new grid if fetching fails.

## Not yet included

Zones, drone orthomosaic
features, and imagery-only relative maps (no harvester data).
