"""Mass apportioning from record polygons to a fixed grid.

    M_g = sum_i m_i |P_i ∩ g| / |P_i|,   C_g = sum_i |P_i ∩ g|,   Y_g = 10 M_g / C_g

Cells are snapped to a fixed origin so that every year and every image shares
the same cells (design rule 5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely


@dataclass(frozen=True)
class Grid:
    """Raster grid in a metric CRS; row 0 is the northern edge."""

    xmin: float
    ymax: float
    resolution: float
    nx: int
    ny: int

    @classmethod
    def from_bounds(cls, bounds, resolution: float, origin=(0.0, 0.0)) -> Grid:
        """Smallest grid snapped to ``origin`` that covers ``bounds`` (xmin, ymin, xmax, ymax)."""
        bx0, by0, bx1, by1 = bounds
        ox, oy = origin
        r = resolution
        xmin = ox + math.floor((bx0 - ox) / r) * r
        xmax = ox + math.ceil((bx1 - ox) / r) * r
        ymin = oy + math.floor((by0 - oy) / r) * r
        ymax = oy + math.ceil((by1 - oy) / r) * r
        return cls(
            xmin, ymax, r, max(1, round((xmax - xmin) / r)), max(1, round((ymax - ymin) / r))
        )

    @property
    def cell_area(self) -> float:
        return self.resolution**2

    @property
    def transform(self) -> tuple[float, float, float, float, float, float]:
        """Affine transform (a, b, c, d, e, f) as used by rasterio."""
        return (self.resolution, 0.0, self.xmin, 0.0, -self.resolution, self.ymax)

    def cell_ranges(self, bounds):
        """Row and column index ranges of cells touching ``bounds``, clipped to the grid."""
        bx0, by0, bx1, by1 = bounds
        r = self.resolution
        c0 = max(0, math.floor((bx0 - self.xmin) / r))
        c1 = min(self.nx - 1, math.ceil((bx1 - self.xmin) / r) - 1)
        r0 = max(0, math.floor((self.ymax - by1) / r))
        r1 = min(self.ny - 1, math.ceil((self.ymax - by0) / r) - 1)
        return r0, r1, c0, c1


def apportion_mass(polygons, masses_kg, grid: Grid, flags=None, pass_ids=None) -> dict:
    """Apportion record mass to grid cells.

    Only clean records (flag 0) contribute to mass and yield; flagged records
    contribute to ``flagged_share`` only, so their area shows up as reduced
    coverage rather than as wrong yield.

    Returns a dict of (ny, nx) arrays named after ``yieldsuite.schema.L4_BANDS``
    plus ``mass`` [kg] and ``covered_area`` [m2].
    """
    polys = np.asarray(polygons, dtype=object)
    m = np.asarray(masses_kg, dtype=float)
    clean = np.ones(len(polys), bool) if flags is None else np.asarray(flags) == 0
    shape = (grid.ny, grid.nx)
    mass = np.zeros(shape)
    area = np.zeros(shape)
    flagged_area = np.zeros(shape)
    n_rec = np.zeros(shape, dtype=np.int32)
    sum_ay = np.zeros(shape)
    sum_ay2 = np.zeros(shape)
    passes: dict[tuple[int, int], set] = {}
    r = grid.resolution

    for i, poly in enumerate(polys):
        p_area = poly.area
        if p_area <= 0 or not np.isfinite(m[i]):
            continue
        r0, r1, c0, c1 = grid.cell_ranges(poly.bounds)
        if r0 > r1 or c0 > c1:
            continue
        rows, cols = np.mgrid[r0 : r1 + 1, c0 : c1 + 1]
        rows, cols = rows.ravel(), cols.ravel()
        x0 = grid.xmin + cols * r
        y1 = grid.ymax - rows * r
        boxes = shapely.box(x0, y1 - r, x0 + r, y1)
        a = shapely.area(shapely.intersection(poly, boxes))
        hit = a > 0
        rows, cols, a = rows[hit], cols[hit], a[hit]
        if not clean[i]:
            np.add.at(flagged_area, (rows, cols), a)
            continue
        y_rec = 10.0 * m[i] / p_area
        np.add.at(mass, (rows, cols), m[i] * a / p_area)
        np.add.at(area, (rows, cols), a)
        np.add.at(n_rec, (rows, cols), 1)
        np.add.at(sum_ay, (rows, cols), a * y_rec)
        np.add.at(sum_ay2, (rows, cols), a * y_rec**2)
        if pass_ids is not None:
            for rr, cc in zip(rows, cols, strict=True):
                passes.setdefault((int(rr), int(cc)), set()).add(pass_ids[i])

    with np.errstate(divide="ignore", invalid="ignore"):
        yld = np.where(area > 0, 10.0 * mass / area, np.nan)
        mean = np.where(area > 0, sum_ay / area, np.nan)
        spread = np.sqrt(np.maximum(np.where(area > 0, sum_ay2 / area, np.nan) - mean**2, 0.0))
        total = area + flagged_area
        flagged_share = np.where(total > 0, flagged_area / total, np.nan)
    n_passes = np.zeros(shape, dtype=np.int32)
    for (rr, cc), s in passes.items():
        n_passes[rr, cc] = len(s)

    return {
        "yield": yld,
        "coverage": area / grid.cell_area,
        "n_records": n_rec,
        "spread": spread,
        "flagged_share": flagged_share,
        "passes": n_passes,
        "mass": mass,
        "covered_area": area,
    }


def check_mass_conservation(cell_mass_kg, expected_total_kg: float, rtol: float = 1e-6) -> bool:
    """Design rule 4: cell masses add up to the (clean, in-grid) record mass."""
    total = float(np.nansum(cell_mass_kg))
    return math.isclose(total, expected_total_kg, rel_tol=rtol, abs_tol=1e-9)
