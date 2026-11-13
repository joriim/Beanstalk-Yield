# Multi-source imagery: Sentinel-2, drone (WebODM) and PlanetScope

L5 can use several image sources at once. Each becomes rows of the same
feature table (`cell, date, index, value, source`); other sources are
harmonised to Sentinel-2 before the model sees them.

## Sources

| Kind | Input | Sensor preset | Notes |
|---|---|---|---|
| `sentinel2` | openEO (Copernicus Data Space) | – | the reference |
| `drone` | reflectance orthophoto (e.g. from WebODM) | `mavic3m`: bands G, R, RE, NIR (+ alpha), reflectance 0–1 | RE is 730 nm, between S2 B05 and B06 |
| `planetscope` | surface-reflectance GeoTIFF + optional UDM2 | `superdove` (8 bands, ×10 000), `dove4` (4 bands) | licence needed; raw scenes are not redistributed |

```yaml
imagery:
  - kind: sentinel2
    start: 2026-05-01
    end: 2026-08-16
  - kind: drone
    path: raw/drone_ortho/2026-06-30_orthophoto.tif
    date: 2026-06-30
    sensor: mavic3m
    # bands: {green: 1, red: 2, rededge: 3, nir: 4}   # override the preset
  - kind: planetscope
    path: data/ps_2026-07-03_SR_8b.tif
    date: 2026-07-03
    sensor: superdove
    mask: data/ps_2026-07-03_udm2.tif               # band 1 > 0 = clear
```

**Check the band order and scale of your products.** The presets are a
starting point; the model card lists the band map, the scale (and whether it
was guessed) and warnings for every raster.

## How a raster becomes features

1. Bands are mapped to Sentinel-2-equivalent names (green → B03, red → B04,
   red edge → B05, NIR → B08/B8A) so the same index formulas apply.
   Indices whose bands the sensor lacks are skipped (reported).
2. Indices are computed per pixel (cloud-masked pixels and alpha = 0 are
   invalid), then averaged onto the suite grid with area-weighted
   resampling. Cells less than half covered by valid pixels are left out.

## Harmonisation and reporting

For each source and index, a linear fit `S2 = a + b · source` is made on
cells observed by both within ±2 days. A source/index pair is dropped when
there are fewer than 30 co-located observations or R² < 0.5, and the reason is
reported. Without any Sentinel-2 data the sources are used as they are.

The model card section `sources` holds the sources used, the fit of every
pair (slope, offset, R², pairs) and an **ablation**: the spatial-CV RMSE of
the model without each source (`gain` > 0 means the source helped).

Synthetic check: drone NDVI = 0.15 + 0.07 · yield and Sentinel-2 NDVI =
0.2 + 0.06 · yield → fitted slope 0.857 (= 0.06 / 0.07), R² > 0.9; a random
source is dropped as "weak fit"; a PlanetScope cloud is removed by its mask.

## WebODM (drone processing)

```bash
export WEBODM_URL=https://webodm.example.org WEBODM_USERNAME=... WEBODM_PASSWORD=...
yieldsuite webodm submit --images flight_2026-06-30/ --project farm/field
yieldsuite webodm status --project-id 7 --task <task id>
yieldsuite webodm download --project-id 7 --task <task id> --out ortho.tif
```

MCP (with the farm data store): `upload_raw kind=drone_images` (the zipped
flight) → `webodm_submit` → `webodm_status` → `import_orthomosaic` (stores
the orthophoto in L0 as `raw/drone_ortho/<date>_orthophoto.tif` and adds the
`drone` imagery entry) → `run_chain to_level=L5`.

Defaults for Mavic 3M: only the multispectral frames (`*_MS_G/R/RE/NIR.TIF`)
are uploaded, in batches (WebODM partial upload), with options
`radiometric-calibration: camera+sun`, `orthophoto-resolution: 5`,
`dsm: true`, `auto-boundary: true`. Check the option names against your ODM
version. Credentials stay in the environment.

Tested against a fake WebODM only; the live API has not been reached from
the development environment.

## Ordering PlanetScope from Planet

`yieldsuite planet search | order | wait | download` (module
`yieldsuite.public.planet`) searches the field on a day, ranks scenes by their
clear share over the parcel, orders the chosen ones clipped to the field and
harmonised to Sentinel-2 (`analytic_sr_udm2`), and downloads the GeoTIFFs with a
licence note. Only the API key is needed: Basic auth for the Data API, the
`api-key` header for the Orders API (`PL_API_KEY`, or a network secret in a cloud
session, which carries one of the two). The repository skill
`.claude/skills/planet` walks through credentials, quota, timing and use at L5.

## Final image and the provider's harmonisation

Besides the season mean and max of each index, the model gets a **final-image**
feature `<index>_last`: each cell's latest clear value before harvest (from the
last image; where that one is cloudy or does not cover the cell, the cell's
previous clear value). It captures uneven ripening, lodging and late green
patches that a season mean hides. It is kept only when it lowers the spatial-CV
error; the model card's `feature_set` gives both errors and the choice, and the
report shows it under L5 ("final image").

Season features come from the reference source's dates (Sentinel-2); a raster
clipped to the field (PlanetScope, drone) feeds the final image and, where it
shares a date with Sentinel-2, that date's value. Date validity is checked per
source against the cells that source covers, so a clipped raster is not
dropped for covering only the field.

PlanetScope ordered with Planet's `harmonize` tool (file names contain
`harmonized`, or `harmonised: true` in the imagery entry) gets its own fit to
Sentinel-2 when at least 30 cells were observed by both within two days;
otherwise it is used as Planet delivered it, and the report says so
("Planet's own harmonisation to Sentinel-2").
