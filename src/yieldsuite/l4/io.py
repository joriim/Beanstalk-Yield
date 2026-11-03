"""Write L4/L5 bands as a multi-band Cloud-Optimised GeoTIFF (needs ``yieldsuite[geo]``)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..schema import L4_BANDS
from .grid import Grid


def write_cog(bands: dict, grid: Grid, crs: str, path: str | Path) -> Path:
    try:
        import rasterio
        from rasterio.transform import Affine
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "writing GeoTIFF needs the geo extra: pip install yieldsuite[geo]"
        ) from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stack = np.stack([np.asarray(bands[name], dtype="float32") for name in L4_BANDS])
    profile = {
        "driver": "COG",
        "dtype": "float32",
        "count": len(L4_BANDS),
        "width": grid.nx,
        "height": grid.ny,
        "crs": crs,
        "transform": Affine(*grid.transform),
        "nodata": np.nan,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(stack)
        for i, name in enumerate(L4_BANDS, start=1):
            dst.set_band_description(i, name)
    return path
