"""N balance per zone and variable-rate prescriptions (GeoPackage, Shapefile, ISOXML)."""

import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")

from shapely.geometry import box  # noqa: E402

from yieldsuite.cli import main  # noqa: E402
from yieldsuite.l4.grid import Grid  # noqa: E402
from yieldsuite.l4.io import write_cog  # noqa: E402
from yieldsuite.prescription import (  # noqa: E402
    KG_HA_TO_MG_M2,
    ZoneTable,
    n_removal,
    rates_from_removal,
    zone_table,
)

CRS = "EPSG:3067"


def _field(tmp_path):
    """20 x 30 cells of 10 m: zone 1 (west, 6 t/ha), zone 2 (east, 9 t/ha)."""
    grid = Grid(xmin=380000.0, ymax=6960300.0, resolution=10.0, nx=30, ny=20)
    zones = np.where(np.arange(30)[None, :] < 12, 1.0, 2.0) * np.ones((20, 1))
    yld = np.where(zones == 1, 6.0, 9.0)
    protein = np.where(zones == 1, 12.5, 10.0)  # % of dry matter
    uptake = yld * 1000 * 0.86 * protein / 6.25 / 100
    zp = write_cog({"zone": zones}, grid, CRS, tmp_path / "zones.tif", names=("zone",))
    yp = write_cog(
        {"yield": yld, "coverage": np.ones_like(yld)},
        grid,
        CRS,
        tmp_path / "L4.tif",
        names=("yield", "coverage"),
    )
    qp = write_cog(
        {"protein_pct_dm": protein, "n_uptake_kg_ha": uptake},
        grid,
        CRS,
        tmp_path / "Q4.tif",
        names=("protein_pct_dm", "n_uptake_kg_ha"),
    )
    b = gpd.GeoDataFrame(geometry=[box(380000, 6960100, 380300, 6960300)], crs=CRS)
    bp = tmp_path / "boundary.gpkg"
    b.to_file(bp, driver="GPKG")
    return grid, zones, yld, zp, yp, qp, bp


def test_n_removal_needs_a_source_and_uses_measured_protein_first():
    y = np.array([6.0, 9.0])
    with pytest.raises(ValueError, match="grain N content"):
        n_removal(y)
    est, how = n_removal(y, grain_n_pct_dm=1.8)
    assert np.allclose(est, y * 1000 * 0.86 * 0.018)
    meas, how = n_removal(y, n_uptake_kg_ha=np.array([100.0, np.nan]), grain_n_pct_dm=1.8)
    assert meas[0] == 100.0 and np.isclose(meas[1], 9000 * 0.86 * 0.018)
    assert "Q4" in how


def test_zone_table_and_rates_keep_the_field_mean(tmp_path):
    grid, zones, yld, *_ = _field(tmp_path)
    removed, _ = n_removal(yld, grain_n_pct_dm=1.8)
    t = zone_table(zones, grid, yld, n_removed_kg_ha=removed, applied_n_kg_ha=110)
    z1, z2 = t.rows
    assert np.isclose(z1["area_ha"], 2.4) and np.isclose(z2["area_ha"], 3.6)
    assert np.isclose(z2["n_balance_kg_ha"], 110 - 9000 * 0.86 * 0.018)
    rates = rates_from_removal(t, mean_rate=100)
    mean = (rates[1] * 2.4 + rates[2] * 3.6) / 6.0
    assert np.isclose(mean, 100) and rates[2] > rates[1]
    clipped = rates_from_removal(ZoneTable(t.rows), 100, min_rate=90, max_rate=105)
    assert clipped[1] == 90 and clipped[2] == 105


def test_cli_nbalance_then_prescription_to_isoxml_and_shapefile(tmp_path, capsys):
    grid, zones, yld, zp, yp, qp, bp = _field(tmp_path)
    out = tmp_path / "out"
    assert (
        main(
            [
                "nbalance",
                "--zones",
                str(zp),
                "--yield-map",
                str(yp),
                "--quality",
                str(qp),
                "--applied-n",
                "1=100,2=120",
                "--out",
                str(out),
                "--stem",
                "nb",
            ]
        )
        == 0
    )
    nb = json.loads((out / "nb.json").read_text())
    z1 = nb["zones"][0]
    assert np.isclose(z1["protein_pct_dm"], 12.5)
    assert np.isclose(z1["n_removed_kg_ha"], 6000 * 0.86 * 0.125 / 6.25)
    assert np.isclose(z1["n_balance_kg_ha"], 100 - z1["n_removed_kg_ha"])

    rc = main(
        [
            "prescription",
            "--zones",
            str(zp),
            "--from-nbalance",
            str(out / "nb.json"),
            "--mean-rate",
            "300",
            "--product",
            "Test 27-3-3",
            "--boundary",
            str(bp),
            "--format",
            "gpkg,shp,isoxml",
            "--out",
            str(out),
            "--stem",
            "rx",
        ]
    )
    assert rc == 0
    s = json.loads((out / "rx.json").read_text())
    assert np.isclose(s["mean_rate_kg_ha"], 300)
    rates = {z["zone"]: z["rate_kg_ha"] for z in s["zones"]}

    shp = gpd.read_file(out / "rx_shp" / "rx.shp")
    assert shp.crs.to_epsg() == 4326 and set(shp["ZONE"]) == {1, 2}

    root = ET.parse(out / "rx_isoxml" / "TASKDATA" / "TASKDATA.XML").getroot()
    assert root.tag == "ISO11783_TaskData" and root.get("VersionMajor") == "4"
    assert root.find("PDT").get("B") == "Test 27-3-3"
    pfd = root.find("PFD")
    assert abs(int(pfd.get("D")) - 60000) < 10  # 6 ha in m2
    tzns = root.find("TSK").findall("TZN")
    assert [t.get("A") for t in tzns] == ["0", "1", "2"]
    for tzn in tzns[1:]:
        z = int(tzn.get("B").split()[-1])
        pdv = tzn.find("PDV")
        assert pdv.get("A") == "0006"
        assert int(pdv.get("B")) == round(rates[z] * KG_HA_TO_MG_M2)
        pnt = tzn.find("PLN/LSG/PNT")
        lat, lon = float(pnt.get("C")), float(pnt.get("D"))
        assert 62 < lat < 63 and 24 < lon < 25  # EPSG:3067 x=380 km, y=6960 km

    assert (
        main(
            [
                "prescription",
                "--zones",
                str(zp),
                "--rates",
                "1=100",
                "--product",
                "x",
                "--out",
                str(out),
            ]
        )
        == 1
    )
    assert "no rate for zone" in capsys.readouterr().err
