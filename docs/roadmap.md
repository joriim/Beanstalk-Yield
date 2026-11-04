# Roadmap

## Functions from other software worth adding

From a review of existing yield-data and farm-management software (November 2026):
USDA-ARS Yield Editor, CSIRO PAT, GeoFIS, the Yield Data Cleaner QGIS plugin,
John Deere Operations Center, Climate FieldView, GeoPard, xarvio FIELD MANAGER
and Wisu. Commercial features are taken from vendor and dealer pages and are
unverified.

| Function | Seen in | Priority |
|---|---|---|
| Variable-rate prescription export (ISOXML task data or shapefile, by zone) | xarvio, GeoPard, Deere | High. This is what turns a map into action on the farm. |
| Nitrogen removal and balance per zone (yield × protein) | HarvestLab and Operations Center | High. We already have both yield and GrainSense protein. |
| AgGateway ADAPT import | Yield Data Cleaner | Medium. Covers many proprietary formats at once. |
| Direct sync from machine clouds (Deere, CNH, Claas, FieldView APIs) | Commercial platforms | Medium. Avoids exporting files by hand. |
| Reassigning data to the right field | SMS | Medium. Our boundary clipping partly covers this. |
| Interactive HTML review report | Yield Data Cleaner | Medium. Makes our QA easier to share without QGIS. |
| Yield-stability classes over several years (stable/unstable × high/low) | Blackmore method, Nebraska framework | Medium. A natural addition next to our zones. |
| Correcting outliers instead of deleting them (Leroux: restored about 15 %) | Research | Low to medium. |

### Sources

- [USDA-ARS Yield Editor](https://catalog.data.gov/dataset/yield-editor-2-0-7)
- [CSIRO PAT QGIS plugin](https://plugins.qgis.org/plugins/pat)
- [GeoFIS](https://doaj.org/article/f147826071a040209887f306e10e8032)
- [Yield Data Cleaner QGIS plugin](https://plugins.qgis.org/plugins/yield_data_cleaner/)
- [John Deere HarvestLab 3000](https://www.futurefarming.com/crop-solutions/john-deere-harvestlab-3000-available-on-combines-for-analysis-of-grain-and-rapeseed/)
- [GeoPard yield cleaning and calibration](https://docs.geopard.tech/geopard-tutorials/product-tour-web-app/geopard-mcp/clean-calibrate-yield-data)
- [xarvio FIELD MANAGER](https://www.xarvio.com/ca/en/products/field-manager/field-monitor.html)
- [Nebraska multi-year yield map framework](https://digitalcommons.unl.edu/agronomyfacpub/365)
- [Leroux et al. 2018, filtering defective yield observations](https://agris.fao.org/search/fr/records/65df798a0f3e94b9e5d8d0d0)
