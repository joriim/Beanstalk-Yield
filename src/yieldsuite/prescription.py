"""From zones to action: nitrogen removal and balance per zone, variable-rate
prescriptions, and their export as GeoPackage, Shapefile or ISOXML TaskData.

N removal is the nitrogen carried off in the grain. With a grain-quality map
(Q4, e.g. GrainSense) it comes from the measured protein per cell; without
one, from yield and a grain N content the user states (no silent default).

Prescription rates are either given per zone or derived from the removal
(rate proportional to the zone's removal, field mean kept, clipped to limits).
The derived rates are a transparent starting point for an adviser, not an
agronomic recommendation: they ignore soil supply, previous crop and
legislation (e.g. the Finnish nitrate decree limits).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import __version__
from .l4.grid import Grid

# ISO 11783-11 DDI 0x0006: Setpoint Mass Per Area Application Rate, unit mg/m2
DDI_SETPOINT_MASS_PER_AREA = "0006"
KG_HA_TO_MG_M2 = 100.0  # 1 kg/ha = 1e6 mg / 1e4 m2


@dataclass
class ZoneTable:
    """Per-zone statistics; rows ordered by zone number."""

    rows: list[dict]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"zones": self.rows, "notes": self.notes}


def _zone_mean(values: np.ndarray, zones: np.ndarray, z: int) -> float | None:
    v = values[(zones == z) & np.isfinite(values)]
    return float(v.mean()) if v.size else None


def n_removal(
    yield_t_ha: np.ndarray,
    *,
    n_uptake_kg_ha: np.ndarray | None = None,
    grain_n_pct_dm: float | None = None,
    moisture_pct: float = 14.0,
) -> tuple[np.ndarray, str]:
    """Nitrogen removed with the grain (kg/ha) per cell and how it was obtained.

    Measured N uptake (Q4 band ``n_uptake_kg_ha``) wins where it is finite;
    elsewhere yield x dry-matter share x grain N content is used when the
    content is given.
    """
    y = np.asarray(yield_t_ha, float)
    from_yield = None
    if grain_n_pct_dm is not None:
        if not 0.5 <= grain_n_pct_dm <= 5.0:
            raise ValueError(f"grain N content {grain_n_pct_dm} % of dry matter is implausible")
        from_yield = y * 1000.0 * (1 - moisture_pct / 100.0) * grain_n_pct_dm / 100.0
    if n_uptake_kg_ha is not None:
        q = np.asarray(n_uptake_kg_ha, float)
        if from_yield is None:
            return q, "measured grain protein (Q4)"
        return np.where(np.isfinite(q), q, from_yield), (
            f"measured grain protein (Q4), else yield x {grain_n_pct_dm} % N of dry matter"
        )
    if from_yield is None:
        raise ValueError(
            "N removal needs a grain-quality map (Q4) or the grain N content: "
            "give grain_n_pct_dm (e.g. protein % of dry matter / 6.25)"
        )
    return from_yield, f"yield x {grain_n_pct_dm} % N of dry matter (moisture {moisture_pct} %)"


def zone_table(
    zones: np.ndarray,
    grid: Grid,
    yield_t_ha: np.ndarray,
    *,
    n_removed_kg_ha: np.ndarray | None = None,
    protein_pct_dm: np.ndarray | None = None,
    applied_n_kg_ha: float | dict[int, float] | None = None,
) -> ZoneTable:
    """Area, mean yield, protein, N removal and N balance (applied - removed) per zone."""
    zones = np.asarray(zones)
    zs = sorted(int(z) for z in np.unique(zones[np.isfinite(zones)]) if z > 0)
    if not zs:
        raise ValueError("no zones in the zone raster")
    rows = []
    for z in zs:
        cells = int((zones == z).sum())
        row = {
            "zone": z,
            "area_ha": cells * grid.cell_area / 1e4,
            "yield_t_ha": _zone_mean(np.asarray(yield_t_ha, float), zones, z),
        }
        if protein_pct_dm is not None:
            row["protein_pct_dm"] = _zone_mean(np.asarray(protein_pct_dm, float), zones, z)
        if n_removed_kg_ha is not None:
            row["n_removed_kg_ha"] = _zone_mean(np.asarray(n_removed_kg_ha, float), zones, z)
            applied = (
                applied_n_kg_ha.get(z) if isinstance(applied_n_kg_ha, dict) else applied_n_kg_ha
            )
            if applied is not None and row["n_removed_kg_ha"] is not None:
                row["n_applied_kg_ha"] = float(applied)
                row["n_balance_kg_ha"] = float(applied) - row["n_removed_kg_ha"]
        rows.append(row)
    return ZoneTable(rows)


def rates_from_removal(
    table: ZoneTable,
    mean_rate: float,
    min_rate: float | None = None,
    max_rate: float | None = None,
) -> dict[int, float]:
    """Rate per zone proportional to its N removal, area-weighted mean = ``mean_rate``.

    Clipping to [min_rate, max_rate] is applied last, so the mean can then
    differ from ``mean_rate``; the caller reports the achieved mean.
    """
    rows = [r for r in table.rows if r.get("n_removed_kg_ha")]
    if not rows:
        raise ValueError("no zone has an N removal; run the N balance first")
    area = np.array([r["area_ha"] for r in rows])
    rem = np.array([r["n_removed_kg_ha"] for r in rows])
    rate = mean_rate * rem / np.average(rem, weights=area)
    if min_rate is not None or max_rate is not None:
        rate = np.clip(rate, min_rate, max_rate)
    return {int(r["zone"]): float(v) for r, v in zip(rows, rate, strict=True)}


def parse_rates(text: str) -> dict[int, float]:
    """``"1=120,2=100"`` -> {1: 120.0, 2: 100.0}."""
    out = {}
    for part in text.split(","):
        if part.strip():
            k, _, v = part.partition("=")
            out[int(k)] = float(v)
    if not out:
        raise ValueError("no rates given (expected e.g. 1=120,2=100)")
    return out


def zone_polygons(zones: np.ndarray, grid: Grid, crs: str):
    """Dissolved zone polygons (GeoDataFrame with ``zone`` and ``area_ha``)."""
    import geopandas as gpd
    import rasterio.features
    from rasterio.transform import Affine
    from shapely.geometry import shape

    z = np.where(np.isfinite(zones), zones, 0).astype(np.int16)
    polys = [
        {"zone": int(v), "geometry": shape(g)}
        for g, v in rasterio.features.shapes(z, mask=z > 0, transform=Affine(*grid.transform))
    ]
    if not polys:
        raise ValueError("no zones in the zone raster")
    gdf = gpd.GeoDataFrame(polys, crs=crs).dissolve(by="zone").reset_index()
    gdf["area_ha"] = gdf.area / 1e4
    return gdf


def prescription_polygons(zones, grid: Grid, crs: str, rates: dict[int, float], product: str):
    gdf = zone_polygons(zones, grid, crs)
    missing = sorted(set(gdf["zone"]) - set(rates))
    if missing:
        raise ValueError(f"no rate for zone(s) {missing}")
    gdf["RATE"] = gdf["zone"].map(rates).astype(float)
    gdf["PRODUCT"] = product
    return gdf[["zone", "RATE", "PRODUCT", "area_ha", "geometry"]]


def _ring(parent: ET.Element, ring, lsg_type: str) -> None:
    """LineString of a polygon ring; points are type 2 (other), C = lat, D = lon."""
    lsg = ET.SubElement(parent, "LSG", A=lsg_type)
    for lon, lat in ring.coords:
        ET.SubElement(lsg, "PNT", A="2", C=f"{lat:.9f}", D=f"{lon:.9f}")


def _polygon_elements(parent: ET.Element, geom, pln_type: str, name: str) -> None:
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for poly in parts:
        pln = ET.SubElement(parent, "PLN", A=pln_type, B=name)
        _ring(pln, poly.exterior, "1")  # PolygonExterior
        for hole in poly.interiors:
            _ring(pln, hole, "2")  # PolygonInterior


def write_isoxml_task(
    prescription,
    boundary,
    out_dir: str | Path,
    *,
    task_name: str,
    field_name: str,
    product: str,
    default_rate: float = 0.0,
) -> Path:
    """ISO 11783-10 TaskData (TASKDATA/TASKDATA.XML) with one planned task.

    Each zone is a treatment zone (TZN) with a polygon and a setpoint mass per
    area (DDI 6, rates in kg/ha converted to mg/m2). The partfield carries the
    field boundary. Outside the zones, and when the position is lost, the
    default treatment zone applies ``default_rate``. Check the file on the
    target terminal before use: terminals differ in what they accept.
    """
    import geopandas as gpd

    gdf = prescription.to_crs("EPSG:4326")
    b = (
        gpd.GeoSeries([boundary], crs=prescription.crs)
        if not hasattr(boundary, "crs")
        else boundary
    )
    bnd = b.to_crs("EPSG:4326").union_all()
    area_m2 = float(b.to_crs(prescription.crs).area.sum())

    root = ET.Element(
        "ISO11783_TaskData",
        VersionMajor="4",
        VersionMinor="3",
        ManagementSoftwareManufacturer="Beanstalk Yield Suite",
        ManagementSoftwareVersion=__version__,
        DataTransferOrigin="1",
    )
    ET.SubElement(root, "PDT", A="PDT1", B=product)
    pfd = ET.SubElement(root, "PFD", A="PFD1", C=field_name, D=str(round(area_m2)))
    _polygon_elements(pfd, bnd, "1", "Boundary")  # PartfieldBoundary
    tsk = ET.SubElement(root, "TSK", A="TSK1", B=task_name, E="PFD1", G="1", H="0", I="0", J="0")
    default = ET.SubElement(tsk, "TZN", A="0", B="Default")
    ET.SubElement(
        default,
        "PDV",
        A=DDI_SETPOINT_MASS_PER_AREA,
        B=str(round(default_rate * KG_HA_TO_MG_M2)),
        C="PDT1",
    )
    for code, row in enumerate(gdf.itertuples(), start=1):
        tzn = ET.SubElement(tsk, "TZN", A=str(code), B=f"Zone {row.zone}")
        _polygon_elements(tzn, row.geometry, "2", f"Zone {row.zone}")  # TreatmentZone
        ET.SubElement(
            tzn,
            "PDV",
            A=DDI_SETPOINT_MASS_PER_AREA,
            B=str(round(row.RATE * KG_HA_TO_MG_M2)),
            C="PDT1",
        )
    ET.indent(root)
    out = Path(out_dir) / "TASKDATA"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "TASKDATA.XML"
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def write_prescription(
    prescription,
    out_dir: str | Path,
    stem: str,
    formats=("gpkg", "shp"),
    *,
    boundary=None,
    task_name: str = "Variable rate",
    field_name: str = "",
    product: str = "",
    default_rate: float = 0.0,
    meta: dict | None = None,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    if "gpkg" in formats:
        p = out / f"{stem}.gpkg"
        prescription.to_file(p, driver="GPKG")
        files["gpkg"] = str(p)
    if "shp" in formats:
        # Terminals read WGS84 shapefiles most reliably; attribute names <= 10 chars
        p = out / f"{stem}_shp" / f"{stem}.shp"
        p.parent.mkdir(exist_ok=True)
        prescription.rename(columns={"area_ha": "AREA_HA", "zone": "ZONE"}).to_crs(
            "EPSG:4326"
        ).to_file(p)
        files["shp"] = str(p)
    if "isoxml" in formats:
        if boundary is None:
            raise ValueError("ISOXML export needs the field boundary")
        files["isoxml"] = str(
            write_isoxml_task(
                prescription,
                boundary,
                out / f"{stem}_isoxml",
                task_name=task_name,
                field_name=field_name,
                product=product,
                default_rate=default_rate,
            )
        )
    summary = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product": product,
        "unit": "kg/ha",
        "zones": [
            {"zone": int(r.zone), "rate_kg_ha": float(r.RATE), "area_ha": float(r.area_ha)}
            for r in prescription.itertuples()
        ],
        "mean_rate_kg_ha": float(np.average(prescription["RATE"], weights=prescription["area_ha"])),
        "total_kg": float((prescription["RATE"] * prescription["area_ha"]).sum()),
        **(meta or {}),
    }
    p = out / f"{stem}.json"
    p.write_text(json.dumps(summary, indent=2))
    files["summary"] = str(p)
    return files
