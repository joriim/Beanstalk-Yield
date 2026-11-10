# Design: daily GPP per cell with a VPM-type model

Status: design, not implemented. The ERA5-Land thermal sums it refers to are
implemented ([weather-agroclimate.md](../weather-agroclimate.md)).

## Goal

Per grid cell and day: intercepted PAR and gross primary production (GPP),
their running sums from sowing, and from these the days and places where
growth slowed or stopped. Outputs are a daily raster stack, season maps and
a time-lapse video.

## Model

Vegetation Photosynthesis Model (Xiao et al. 2004; parameters as in Zhang et
al. 2017), with the fAPAR the suite already uses:

$$
\mathrm{GPP}_{c,d} = \varepsilon_0 \; T_d \; W_{c,d} \; \mathrm{fAPAR}_{c,d} \; \mathrm{PAR}_d
$$

`c` = cell, `d` = day. Only fAPAR and W vary within the field.

| Term | Definition | Source | Varies |
|---|---|---|---|
| PAR | 0.48 × all-sky shortwave [MJ/m²/d] | ERA5, existing | by day |
| fAPAR | 1.1638 NDVI − 0.1426, clipped 0–0.95, daily by interpolation | Sentinel-2, existing | by cell and day |
| T | ((t − Tmin)(t − Tmax)) / ((t − Tmin)(t − Tmax) − (t − Topt)²), 0 outside Tmin…Tmax; t = daytime temperature ≈ (Tmax + Tmean) / 2 | ERA5 / ERA5-Land | by day |
| W | (1 + NDMI) / (1 + NDMI_max) | Sentinel-2 NDMI (B8A, B11), existing index | by cell and day |
| ε₀ | maximum light use efficiency, default 1.9 g C/MJ APAR (0.42 g C/mol × 4.6 mol/MJ, C3) | assumption | no |

The LSWI of the papers is the same normalised difference of NIR and
1.6 µm SWIR as the suite's NDMI.

Defaults for spring cereals: Tmin 0 °C, Topt 20 °C, Tmax 35 °C (the global
cropland values −1/30/48 °C would scale a normal 15 °C Finnish summer day
down to 0.7). All four are `gpp:` config keys.

Left out on purpose: the leaf-age scalar P of the original model. It is
again a function of NDMI and would count the same signal twice.

### Derived quantities

| Name | Definition | Use |
|---|---|---|
| `ipar_cum` | Σ PAR × fAPAR | light captured; equals the existing season IPAR on the last day |
| `gpp_cum` | Σ GPP | carbon fixed |
| `gpp_potential` | ε₀ × PAR × fAPAR (T = W = 1) | what the canopy could have fixed |
| `stress_ratio` | GPP / potential = T × W | growth lost to temperature and water, 0–1 |
| `w_anomaly` | W of the cell − field median W on the same day | where water status deviates; phenology largely cancels |
| `low_growth_days` | days between emergence and maturity with GPP < `low_growth_g_c` (default 1 g C/m²/d) | map of days without growth |
| `limiting_factor` | per day, the smallest of: PAR / clear-sky PAR, T, W | why (light, temperature, water) |

Emergence and maturity per cell: first and last day with fAPAR ≥ 0.1, so
bare soil before emergence is not counted as a day without growth.

## What the water scalar can and cannot show

This is the weakest term and decides how the maps may be read.

1. **Range.** NDMI of a cereal runs from about −0.1 (bare or ripe) to 0.5
   (full canopy), so W stays between about 0.6 and 1. W alone never produces
   a day without growth; those come from temperature, light and missing
   canopy. W shifts the sum by up to tens of per cent.
2. **It follows the canopy, not only water.** NDMI rises with leaf area and
   falls at ripening. Low W in June may be a thin stand, low W in August is
   senescence. Both reduce photosynthesis, so the GPP is not wrong, but W is
   not a drought map. `w_anomaly` is the diagnostic for water: it compares
   cells at the same growth stage on the same day.
3. **NDMI_max per cell hides permanent differences.** With the maximum taken
   per cell (as in the paper), a knoll that is dry every year is normalised
   to itself and shows stress only when it is drier than its own best state.
   The alternative, one maximum for the field, keeps permanent differences
   but they are already in fAPAR and would be counted twice. Default: per
   cell; `ndmi_max: field` (95th percentile of cell maxima) as an option.
   With few images the maximum is noisy: use the second-highest value, and
   earlier years of the same field when the store has them.
4. **Time.** NDMI is known on cloud-free Sentinel-2 dates (often 5–15 days
   apart) and interpolated between them. A dry spell that starts and ends
   between two images is not seen. Before the first and after the last image
   W is held constant, not extrapolated. Every day carries
   `days_to_nearest_image`; days further than 10 from an image are marked in
   the video and excluded from `low_growth_days` unless temperature or light
   alone explains them.
5. **Space.** B11 is a 20 m band. On a 10 m grid W is smoother than fAPAR,
   and edge cells mix with the surroundings.
6. **Wet soil and dew** raise NDMI independent of the crop, mostly before
   canopy closure.

## What the level means

ε₀ in the papers belongs with an EVI-based fAPAR of chlorophyll, which is
lower than the NDVI-based canopy fAPAR used here. With the same ε₀ the
season GPP will be too high, perhaps by 20–40 %. The suite keeps one fAPAR
for IPAR, RUE and GPP so that GPP / IPAR is purely ε₀ × T × W; an EVI2
variant (no blue band) can be added as `fapar_source: evi2` later.

So: compare cells and dates within a field-year. Do not report the absolute
level as measured carbon uptake. NDVI also saturates in dense stands, which
compresses differences between the best parts of a field.

## Checks

- T = W = 1 gives `gpp_cum` = ε₀ × `ipar_cum` exactly (unit test).
- Synthetic field with a planted dry patch and a cold week: the patch shows
  in `w_anomaly`, the week in `limiting_factor` for every cell.
- Carbon balance against the yield map: above-ground biomass carbon
  (0.45 × dry biomass) / `gpp_cum` should fall in about 0.3–0.6. The field
  value goes to the model card; cells outside are flagged, not corrected.
- Season total against published flux-tower sums for spring cereals at
  similar latitudes, as a plausibility range only.

## Implementation

| Step | Where | Content |
|---|---|---|
| 1 | `weather/rue.py` | expose the daily fAPAR matrix; `ipar()` becomes its sum |
| 2 | `weather/gpp.py` (new, numpy only) | `t_scalar`, `w_scalar`, `daily_gpp` → arrays (days × cells); derived maps |
| 3 | `config.py` | `gpp:` block: `epsilon0_g_c_mj`, `t_min_c`, `t_opt_c`, `t_max_c`, `ndmi_max`, `low_growth_g_c` |
| 4 | `weather/run.py` | `L5_<field>_<year>_gpp.tif` (season maps) and `..._gpp_daily.tif` (one band per day, band description = date, for the QGIS temporal controller) |
| 5 | `timelapse.py` (new, `report` extra) | MP4 with matplotlib + ffmpeg: cumulative IPAR, cumulative GPP, daily `stress_ratio`; weather strip with image dates and a cursor |
| 6 | `cli.py`, `report.py`, MCP | `yieldsuite gpp`, `yieldsuite timelapse`; maps in the report |

Steps 1–2 need no new data or dependencies. NDMI is already in the default
index list, so existing feature files work.

## Why temperature is not mapped per cell

T uses one temperature for the field. Two ways to a per-cell soil or surface
temperature were examined and rejected for now.

**Landsat thermal images of bare soil in spring**

- The thermal sensor has 100 m pixels (delivered resampled to 30 m). The
  average Finnish parcel is under 3 ha, a few pixels across, each mixed with
  forest edge, road or ditch. Within-field patterns are resolvable only on
  fields above roughly 10–15 ha.
- It measures the top millimetre near midday. That depends on surface
  moisture, colour, roughness and residue more than on the seedbed at 3–5 cm.
  Dry peat is the clear case: dark and poorly conducting, its surface is
  among the hottest at midday while the seedbed below stays cold. The sign
  can be the opposite of what is wanted.
- Emissivity in the product comes from a 100 m climatology; the error over
  bare soil is 1–2 K, the size of the differences of interest.
- Bare soil lasts three to six weeks. With cloud, two to four usable scenes
  per spring are realistic, some years none, each in a different tillage
  state.
- That the pattern repeats from year to year is a hypothesis. It has to be
  shown on at least three dates per field, and it has to add something over
  terrain wetness and the peat model, which the suite has at 2 m.

**Scaling ERA5-Land by terrain and soil**

- The model soil temperature itself has errors of 1–3 K in spring, mostly
  from snowmelt timing and a generic soil.
- Slope and aspect matter little on arable land: 3° towards north or south
  changes midday beam radiation by about 5 % in May. Wetness, organic
  matter and residue dominate, and no coefficient for them is known without
  measurements.
- The result would be the terrain wetness index expressed in degrees. A
  statistical model gets the same information from the index itself, without
  the false precision.

**Instead** (implemented, see [seedbed.md](../seedbed.md))

- Field-level thermal sums from ERA5-Land, air and topsoil.
- Bare-soil Sentinel-2 NDMI around sowing as a 20 m wetness proxy, with a
  repeatability figure between dates.
- The crop as the sensor: per-cell delay of emergence from the fAPAR curve
  integrates seedbed temperature, moisture and sowing depth.
- Three to five loggers at 5 cm in contrasting zones measure the size of the
  effect directly and give the only sound basis for any later scaling.

## References

- Xiao, X. et al. 2004. Modeling gross primary production of temperate
  deciduous broadleaf forest using satellite images and climate data.
  Remote Sensing of Environment 91, 256–270.
- Zhang, Y. et al. 2017. A global moderate resolution dataset of gross
  primary production of vegetation for 2000–2016. Scientific Data 4, 170165.
- Myneni, R. B. & Williams, D. L. 1994. On the relationship between FAPAR
  and NDVI. Remote Sensing of Environment 49, 200–211.
