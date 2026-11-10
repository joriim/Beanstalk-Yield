# Seedbed: wetness, emergence delay and soil loggers

A per-cell soil temperature map cannot be made from public data: thermal
satellites are too coarse and see the surface skin, and scaling a 9 km model
by terrain only restates the terrain
([why](design/gpp-vpm.md#why-temperature-is-not-mapped-per-cell)). The suite
uses four things instead.

| What | Resolution | Answers |
|---|---|---|
| Thermal sums, air and topsoil (ERA5-Land) | whole field, daily | how warm was this spring and season ([weather](weather-agroclimate.md)) |
| Bare-soil wetness (Sentinel-2 NDMI) | 20 m, a few dates around sowing | where the soil was wetter than the rest of the field, and so slower to warm |
| Emergence delay (fAPAR curve) | grid cell, as fine as the image interval | where the crop came up later; the crop integrates temperature, moisture and sowing depth |
| Soil temperature loggers | 3–5 points, hourly | how many degrees and degree days the contrast is really worth |

## Bare-soil wetness

For every image date from `bare_days_before` (30) days before sowing to
`bare_days_after` (7) days after, cells with NDVI below `bare_ndvi` (0.25)
count as bare. Their NDMI (B8A, B11) is taken as the difference from the
field median of the same date, and the differences are averaged over the
dates. A date is skipped when fewer than half of the field's cells are bare
and cloud-free (ley, winter crop, cloud).

- Positive = wetter than the field on those dates, in NDMI units. It is a
  ranking within the field, not a water content.
- `repeatability` is the mean correlation of the pattern between dates. Near
  1: the same places are wet every time. Near 0 or negative: the pattern is
  tillage, residue or noise, and the band should not be used. With one date
  it is empty.
- `bare_soil_dates` gives the number of dates behind each cell.
- Straw and stubble also lower B11. Ploughed, harrowed and rolled surfaces
  differ; a field tilled in parts on an image date shows the tillage border.
  Taking each date against its own median removes whole-field changes only.
- The default Sentinel-2 window starts on 15 April. For early sowing set the
  imagery `start` so that it covers the days before sowing.

## Emergence delay

fAPAR is 0 at sowing and linear between images, as in the IPAR sum. For each
cell the suite finds the first day the curve reaches `emergence_fapar` (0.2)
and reports it in days and in degree days from sowing, and as the difference
from the field median (`emergence_lag_days`: positive = later).

`emergence_gap_days` is the distance between the two images around the
crossing (or sowing and the first image). The delay is **not known more
finely than this**. Cells that cross in the same interval differ only by how
far they had come at the later image, so with a 20-day gap the band is
closer to "canopy size at that image" than to a date. Read differences
smaller than about half the gap as ranking. PlanetScope or drone images in
June shorten the gap.

The threshold is canopy cover, not the botanical emergence date; the
difference between cells is what is used.

## Loggers

Three to five loggers at 5 cm, in places that differ (wet hollow, knoll,
peat, the main soil), from sowing for at least three weeks. One CSV:

```
logger;time;temp_c
hollow;2026-05-12 00:00;8,4
```

Separator and decimal comma are detected. A day counts when at least 20
hours have readings. The summary uses only the days that every logger has
and gives, per logger, the mean, the thermal sum above `soil_t_base_c`, the
mean difference from the ERA5-Land topsoil temperature, and the bare-soil
wetness and emergence delay of the cell it stands in. `contrast` is the
largest difference between loggers. A handful of points measures the size of
the effect; it does not calibrate a map.

## Config

```yaml
weather:
  hourly: [soil_temperature_0_to_7cm]
seedbed:
  bare_days_before: 30
  bare_days_after: 7
  bare_ndvi: 0.25
  emergence_fapar: 0.2
  loggers: loggers.csv
  logger_positions: {hollow: [287410, 6968230], knoll: [287520, 6968310]}   # x, y in the field CRS
```

Everything except the loggers runs with defaults as part of
`yieldsuite fuse` when `crop.sowing_date` is set and the image features
contain NDVI (and NDMI for the wetness).

## Outputs

`L5_<field>_<year>_seedbed.tif` with bands `bare_soil_wetness`,
`bare_soil_dates`, `emergence_days`, `emergence_lag_days`,
`emergence_gap_days`, `emergence_gdd`; and a `seedbed` block in
`L5_<field>_<year>_agroclimate.json` (dates used, repeatability, median
emergence, spread of the delay, image gap, loggers).
