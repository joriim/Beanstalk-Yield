# Harvest timelapse and 3D viewer

```bash
yieldsuite timelapse field.yaml            # both, into <output_dir>/viz/
yieldsuite timelapse field.yaml --layers yield,moisture,protein,oil --layout grid
yieldsuite timelapse field.yaml --no-video --dem my_dem.tif
```

Run after the chain (needs the L3 records).

**Maps (layers).** Yield, grain moisture, protein, oil, carbohydrate and starch, each
as one map per record (moisture as harvested, after the analyzer correction;
protein, oil, carbohydrate and starch from the analyzer, aligned to the records with
the position gate). `--layers` chooses which are shown (default: the available ones of
yield, moisture, protein and oil; Finnish names work too). A layer that was not measured
is kept in the list with the reason, for example oil: *the analyzer export column is
empty (the device does not measure it with this calibration)*; it is shown as an
explanatory panel in the video and as a greyed entry in the viewer.

**Timelapse video** (`<field>_<year>_timelapse.mp4`, ffmpeg; GIF without it):
the chosen maps fill in record by record side by side (1-4 in a grid) as the combine
drives. Each record is drawn as the piece it cut (record length × swath, at the
header position from the machine profile). The combine is the dark arrow; the
header is the bar across the swath, solid green when lowered (harvesting),
dashed red when raised (turns, driving). Grey: cut, but the record is flagged,
or no quality reading matches it. The quality map is analyzer protein (% DM)
when a GrainSense session is configured, else grain moisture. Pauses longer
than 6 s play as 6 s, so turns and breaks between sessions do not stall the
video. The DEM hillshade is the background.

**3D viewer** (`<field>_<year>_harvest_3d.html`): one self-contained file
(three.js r147, MIT, embedded; the data is gzip-compressed inside, so the file is
small) that opens offline in a modern browser. Every chosen map is a tile: a texture on
its own copy of the terrain, painted as the harvest progresses, with the combine and
its header animated on each tile. The tiles are placed in a row, a grid or a stack
(`--layout`, or the layout control); tick the maps to show in the panel, click a tile's
label or button (or double-click the tile) to fly to it and *Kaikki / All* (or double-click
empty space) to zoom out to see them all. `#yield,protein` at the end of the address
preselects maps. Drag to
rotate, wheel to zoom, right button to pan. Controls: play/pause and time
slider, speed (playback seconds per second), maps, layout, vertical
exaggeration (Finnish fields are flat; default × 3), combine size (the model
is a combine at true size × the factor), follow camera. The header lowers and
rises with the logged header state and is yellow when lowered, red when raised;
the black line is the recent path. Coordinates in the file are metres from the
south-west corner of the view, not map coordinates. The files contain the
field's yield data: share them like the data itself.

**Terrain**: the NLS 2 m elevation model read from Paituli (CSC) without an API
key (`public/paituli_dem.py`, sheets from `www.nic.funet.fi`), cached as
`<output_dir>/dem_2m.tif`; CC BY 4.0, "Contains data from the National Land
Survey of Finland". Outside EPSG:3067 give `--dem`; `--no-dem` draws flat.

## Gap-fill story (sketch)

```bash
yieldsuite gapfill-story field.yaml        # 3D page and video into <output_dir>/viz/
```

How the empty parts of the yield map are filled, step by step, from the L5 outputs of the
field-year (`<field>_<year>_gapfill_3d.html` and `<field>_<year>_gapfill.mp4`):

1. **Measured map**: the cells with harvester records; the rest (grey) is empty.
2. **Sources**: one map each, in date order: every Sentinel-2 date the model used (NDVI, 10 m,
   clouds masked; the final image before harvest is marked ★) and every PlanetScope scene
   (NDVI, with how it was harmonised to Sentinel-2). The colours stretch to each image's own
   range, with the scale shown, so the spatial pattern is visible.
3. **Image model**: its prediction appears in the empty cells, with the cross-validated error.
4. **Kriging correction**: the difference between measured yield and prediction is spread from
   the measured cells inwards (cells ordered by distance to the nearest measured cell); each
   gap cell's colour moves from the prediction to the final value; the moving front is lighter.
5. **Finished map**: measured cells as they are, filled cells lightened (an estimate, not a
   measurement); grey that remains has no value because image features are missing there.

The 3D page has play/pause, a slider, buttons for the five parts, the source list and the
terrain (rotate, zoom, exaggeration). Captions are generated from the model card (dates,
feature set, cross-validated error, median kriging uncertainty, the fill that was used), so
they change with the data. It is a sketch: it shows one field-year's own sources and does
not yet show per-source contributions (the model card has the ablation) or the uncertainty
map (the `sigma` band).
