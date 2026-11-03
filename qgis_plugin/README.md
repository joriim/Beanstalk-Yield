# QGIS plugin

Processing provider that exposes the yieldsuite core as QGIS algorithms.
Licensed GPL-2.0-or-later (see `LICENSE`), as required for QGIS plugins; the
core library stays Apache-2.0.

Development install: symlink `yieldsuite_qgis/` into your QGIS profile's
`python/plugins/` folder, and `pip install -e .` the core into the Python that
QGIS uses. Status: provider and one algorithm skeleton (L4 gridding).
