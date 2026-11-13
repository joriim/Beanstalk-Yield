# Cell size: showing the yield where it repeats

A yield map cell is worth reading only if harvesting it again would give about the same
number. Small cells are noisy: grain mixes inside the combine, the header is several metres
wide, and position and width errors shift mass between neighbouring passes. Large cells
repeat well but hide detail. The chain measures this trade-off from the harvest itself and
shows the yield at the finest cell size that repeats.

## How it is measured

The passes are split into two interleaved halves (every other pass). For each candidate cell
size the yield of every cell is computed from each half separately, and the two are compared
across cells:

| Quantity | Definition |
| --- | --- |
| `r` | correlation of the two half-cell yields across cells |
| repeatability | `2 r / (1 + r)` (Spearman-Brown: reliability of the whole cell) |
| cell uncertainty | `sd(half A - half B) / 2`, t/ha |

Candidates are multiples of the working grid (`grid.resolution_m`) up to 60 m, so that the
shown map is an exact aggregate of the working grid. A size is *not assessable* when fewer
than half of the logged cells, or fewer than 30 cells, hold both halves: the cell is then
narrower than the pass spacing.

The choice is the finest assessable size with repeatability of at least 0.8
(`grid.min_reliability`). When no size reaches it, the most repeatable size is used and the
report says that within-field differences are close to measurement noise.

## What is written

- `L4_<field>_<year>_reliable.tif`: `yield` (measured, coverage-weighted mean of the working
  cells, so mass is kept), `coverage`, `yield_filled` (the gap-filled L5 map aggregated the
  same way) and `measured_share`.
- `<field>_<year>_cell_size.json`: the table for every size, the choice and the reason.
- The report shows the working grid next to the chosen size, and the certainty summary gets
  an item that grades the yield pattern by the repeatability.

Processing (cleaning, gap fill, image model) still runs on the working grid.

## Settings

```yaml
grid:
  resolution_m: 10        # working grid
  presentation_m: auto    # auto | a multiple of resolution_m | off
  min_reliability: 0.8
```

`yieldsuite cell-size field.yaml` prints the table and rewrites the products.

## Reading the result

- Repeatability includes everything that differs between neighbouring passes: sensor noise,
  flow delay, an antenna offset that is not entered, and real variation narrower than the
  cell. An unentered lateral antenna offset lowers it; entering the offset
  (`yieldsuite machine set`) raises it.
- A field with little real variation never reaches the target however large the cells are.
  That is a finding about the field, not a failure: its map should be read as a field mean
  with broad tendencies.
- It measures repeatability within one harvest. It says nothing about the yield level, which
  depends on scale tickets.
