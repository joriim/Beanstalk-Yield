# Public data: Sentinel-2 search, field parcels, boundary

Endpoints are configuration, not code. Public services move and rename more
often than this suite is released, so check them when setting up:

```yaml
public_data:
  stac_url: https://stac.dataspace.copernicus.eu/v1   # default; any STAC API works
  s2_collection: sentinel-2-l2a                       # default
  parcels_wfs_url: <WFS endpoint of the parcel register>
  parcels_typename: <feature type of the parcels layer>
  parcel_id_column: <parcel id attribute>
edge_buffer_m: 6            # FIELD_EDGE buffer; default one header width
```

## Sentinel-2 scenes

```bash
yieldsuite s2-search field.yaml [--l4 L4.tif] [--max-cloud 60]
```

MCP: `search_sentinel2(farm, field, year)`. Lists the scenes over the field
(STAC API `POST /search`, paginated) with their date, tile and scene cloud
cover, keeping the least cloudy scene per day. Scene cloud cover describes the
whole 100 km tile; the field-level cloud mask is applied in openEO (SCL).
The field is located from its boundary, else the L4 grid, else `location`.

## Field parcels and boundary

1. Get parcel polygons, either
   - from a file you downloaded (e.g. the farm's parcels as GeoPackage or
     Shapefile), or
   - from the parcel register's WFS: MCP `get_parcels(farm, field, year)`
     (needs `public_data.parcels_wfs_url` and `parcels_typename`), which saves
     `public/parcels.geojson` and lists parcel ids and areas.
2. Choose the field: MCP `import_boundary(farm, field, year, source_path,
   parcel_ids?, id_column?)`. Without ids, the parcels overlapping the
   harvested area (from the last L3 run) are taken. The boundary is stored in
   L0 as `raw/boundary/boundary_<n>.gpkg` and the config points to it.
3. The next run flags records whose centre lies within `edge_buffer_m` of the
   boundary (or outside it) as `FIELD_EDGE`; their area shows up as reduced
   coverage instead of edge-biased yield. QA reports the count and the
   boundary area.

Only a bounding box of the field is sent to the WFS and STAC services.
