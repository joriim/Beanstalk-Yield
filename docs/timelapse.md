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
