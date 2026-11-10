"""Level-by-level report of a chain run: figures, process description and audit.

``build_report(config)`` reads the products of a run (L3 records, L4 grid,
QA JSON, run manifest, and L5 / Q4 when present) and writes into
``<output_dir>/report/``:

* ``figures/`` – one PNG per level (L1A records, L1B signals, L2 yield of
  each record, L3 quality flags, L4 grid and quality bands, L5 / Q4 if run);
* ``<stem>_process.md`` – what each level did, with the parameters and
  numbers of this run;
* ``<stem>_audit.json`` – the run manifest plus the ingest decisions and a
  fresh re-hash of every input and output (unchanged / changed);
* ``<stem>_report.html`` – all of the above in one self-contained page.

Needs matplotlib (``pip install yieldsuite[report]``) and the geo extra.
"""

from __future__ import annotations

import base64
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .audit import SOFTWARE, verify_manifest
from .schema import Flag

TEXT = {
    "fi": {
        "title": "Satokartan analyysiketju",
        "summary": "Yhteenveto",
        "field": "Lohko",
        "year": "Vuosi",
        "class": "Tuoteluokka",
        "area": "Lohkon pinta-ala",
        "mean_yield": "Keskisato (puhtaat solut)",
        "mass": "Puitu massa yhteensä",
        "flagged": "Liputettu puintitietueista",
        "mass_check": "Massatase",
        "ok": "täsmää",
        "fail": "EI täsmää",
        "process": "Prosessikuvaus",
        "inputs": "Syötteet",
        "method": "Menetelmä",
        "parameters": "Parametrit",
        "result": "Tulos",
        "outputs": "Tuotteet",
        "audit": "Auditointi",
        "software": "Ohjelmisto",
        "run": "Ajo",
        "started": "Alkoi (UTC)",
        "finished": "Päättyi (UTC)",
        "file": "Tiedosto",
        "role": "Rooli",
        "size": "Koko",
        "status": "Tila",
        "unchanged": "ennallaan",
        "changed": "MUUTTUNUT",
        "decisions": "Syötteen tulkinta",
        "column": "Sarake",
        "unit": "Yksikkö",
        "source": "Peruste",
        "derived": "Johdetut",
        "warnings": "Varoitukset",
        "none": "ei",
        "generated": "Raportti luotu",
        "verify_ok": "Kaikki syötteet ja tuotteet ovat tavu tavulta samat kuin ajossa.",
        "verify_bad": "Tiedostoja on muuttunut ajon jälkeen:",
        "classes": {
            "absolute": "absoluuttinen (täsmäytetty vaakaan)",
            "absolute_unreconciled": "absoluuttinen, ei täsmäytetty vaakaan",
            "relative": "suhteellinen",
        },
    },
    "en": {
        "title": "Yield map processing chain",
        "summary": "Summary",
        "field": "Field",
        "year": "Year",
        "class": "Product class",
        "area": "Field area",
        "mean_yield": "Mean yield (clean cells)",
        "mass": "Total harvested mass",
        "flagged": "Flagged harvesting records",
        "mass_check": "Mass balance",
        "ok": "conserved",
        "fail": "NOT conserved",
        "process": "Process description",
        "inputs": "Inputs",
        "method": "Method",
        "parameters": "Parameters",
        "result": "Result",
        "outputs": "Outputs",
        "audit": "Audit",
        "software": "Software",
        "run": "Run",
        "started": "Started (UTC)",
        "finished": "Finished (UTC)",
        "file": "File",
        "role": "Role",
        "size": "Size",
        "status": "Status",
        "unchanged": "unchanged",
        "changed": "CHANGED",
        "decisions": "How the input was read",
        "column": "Column",
        "unit": "Unit",
        "source": "Basis",
        "derived": "Derived",
        "warnings": "Warnings",
        "none": "none",
        "generated": "Report generated",
        "verify_ok": "Every input and output is byte-identical to the run.",
        "verify_bad": "Files have changed since the run:",
        "classes": {
            "absolute": "absolute (reconciled to scale tickets)",
            "absolute_unreconciled": "absolute, not reconciled to scale tickets",
            "relative": "relative",
        },
    },
}


def _fmt(v, nd=2, unit=""):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}".replace(",", " ") + (f" {unit}" if unit else "")
    return f"{v:,.{nd}f}".replace(",", " ") + (f" {unit}" if unit else "")


# --- process description ---------------------------------------------------------


def process_steps(qa: dict, manifest: dict, lang: str = "fi") -> list[dict]:
    """One entry per level: title, inputs, method, parameters, result, outputs."""
    fi = lang == "fi"
    settings = manifest.get("settings", {})
    ing = (qa.get("ingest") or [{}])[0]
    machines = settings.get("machines", {})
    delays = qa.get("delays", {})
    flags = qa.get("flags", {})
    edge = qa.get("field_edge", {})
    loc = qa.get("local_outliers", {})
    om = qa.get("outlier_mass", {})
    mc = qa.get("mass_conservation", {})
    grid = qa.get("grid", {})
    k = qa.get("k_factors", {})
    steps = []

    src = ", ".join(Path(i.get("source", "")).name for i in qa.get("ingest", []))
    steps.append(
        {
            "level": "L0",
            "title": "Raakadata" if fi else "Raw data",
            "inputs": src,
            "method": (
                "Alkuperäiset tiedostot säilytetään muuttamattomina; tarkistussumma (SHA-256) "
                "kirjataan auditointiin."
                if fi
                else "Original files are kept unchanged; their SHA-256 is recorded in the audit."
            ),
            "parameters": {},
            "result": f"{_fmt(ing.get('rows_in'))} {'riviä' if fi else 'rows'}",
            "outputs": "–",
        }
    )
    steps.append(
        {
            "level": "L1A",
            "title": "Tulkinta ja yhtenäistys" if fi else "Decoding",
            "inputs": src,
            "method": (
                "Muoto, sarakkeet ja yksiköt tunnistetaan; jokaisen yksikön peruste kirjataan "
                "(asetus, otsikko, arvot). Rivit muunnetaan yhteiseen tietuemuotoon "
                "(SI-yksiköt, yksi kello, kentän koordinaatisto)."
                if fi
                else "Format, columns and units are recognised; the basis of every unit is "
                "recorded (config, header, values). Rows become common records "
                "(SI units, one clock, the field CRS)."
            ),
            "parameters": {
                ("tulkintatapa" if fi else "input family"): ing.get("input_family"),
                ("tulotaso" if fi else "entry level"): ing.get("entry_level"),
                "CRS": settings.get("crs"),
            },
            "result": (
                f"{_fmt(ing.get('rows_out'))} {'tietuetta' if fi else 'records'}"
                + (f"; {len(ing.get('warnings', []))} {'varoitusta' if fi else 'warnings'}")
            ),
            "outputs": "–",
        }
    )
    steps.append(
        {
            "level": "L1B",
            "title": "Massavirta" if fi else "Mass flow",
            "inputs": "L1A",
            "method": (
                "Märkä massavirta (kg/s) tietueittain. Pistesatoviennistä massa rakennetaan "
                "uudelleen: sato × matka × leikkuuleveys. Kosteuden vakiointi tehdään vasta "
                "L2:ssa, kun kosteus on kohdistettu samaan jyvään."
                if fi
                else "Wet mass flow (kg/s) per record. For yield-point exports mass is rebuilt "
                "as yield × distance × swath. Moisture standardisation waits for L2, when "
                "moisture is aligned to the same grain."
            ),
            "parameters": {
                ("mediaani massavirta" if fi else "median mass flow"): _fmt(
                    ing.get("summary", {}).get("median_mass_flow_kg_s"), 2, "kg/s"
                )
            },
            "result": "; ".join(ing.get("derived", [])) or "–",
            "outputs": "–",
        }
    )
    d_txt = []
    for mid, d in delays.items():
        if d.get("vendor_applied"):
            d_txt.append(f"{mid}: " + ("viejä korjannut" if fi else "applied by the vendor"))
        else:
            d_txt.append(f"{mid}: τ_d {d.get('tau_d_s')} s, τ_m {d.get('tau_m_s')} s")
    steps.append(
        {
            "level": "L2",
            "title": "Paikannettu sato" if fi else "Georeferenced yield",
            "inputs": "L1B",
            "method": (
                "Virtausviive ja kosteusviive siirretään koneprofiilin mukaan, antennin "
                "sijainti siirretään leikkuupöydän keskelle, kosteus vakioidaan "
                "vertailukosteuteen, ja jokaiselle tietueelle lasketaan tehollinen "
                "(päällekkäisyydet poistettu) pinta-ala ja sato."
                if fi
                else "Flow and moisture delays are shifted per machine profile, the antenna "
                "position is moved to the header centre, moisture is standardised to the "
                "reference, and each record gets its effective (non-overlapping) area and yield."
            ),
            "parameters": {
                ("viiveet" if fi else "delays"): "; ".join(d_txt) or "–",
                ("vertailukosteus" if fi else "reference moisture"): _fmt(
                    settings.get("reference_moisture_pct"), 1, "%"
                ),
                ("koneprofiilit" if fi else "machine profiles"): ", ".join(
                    f"{m} v{p.get('version')} ({p.get('param_hash')})" for m, p in machines.items()
                ),
            },
            "result": "–",
            "outputs": "L3 records (GeoParquet)",
        }
    )
    flag_txt = ", ".join(f"{n} {v}" for n, v in flags.items() if v)
    k_txt = ", ".join(f"{m} = {v:.3f}" for m, v in k.items()) or "–"
    steps.append(
        {
            "level": "L3",
            "title": "Laatuliput ja täsmäytys" if fi else "Quality flags and reconciliation",
            "inputs": "L2" + (" + vaakakuitit" if fi else " + scale tickets") * bool(k),
            "method": (
                "Tietueita ei poisteta: jokainen saa lippubittikartan (leikkuupöytä ylhäällä, "
                "alku/loppu, nopeus, leveys, kosteus, GNSS, lohkon reuna, globaali ja "
                "paikallinen poikkeama). Paikallinen poikkeama: negatiivinen local Moran's I "
                "tai yli 4 robustia hajontaa naapurien mediaanista. Täsmäytyskerroin k "
                "koneittain vaakakuiteista; poikkeavat tietueet saavat naapuriensa "
                "mediaanisadon."
                if fi
                else "No record is deleted: each gets a flag bitmask (header up, fill/finish, "
                "speed, swath, moisture, GNSS, field edge, global and local outlier). Local "
                "outlier: negative local Moran's I or more than 4 robust SD from the "
                "neighbours' median. Reconciliation factor k per machine from scale tickets; "
                "outlier records are credited with their neighbours' median yield."
            ),
            "parameters": {
                ("reunavyöhyke" if fi else "edge buffer"): _fmt(edge.get("buffer_m"), 2, "m"),
                ("naapurusto" if fi else "neighbourhood"): _fmt(loc.get("radius_m"), 0, "m"),
                "k": k_txt,
            },
            "result": (
                f"{flag_txt}. "
                + (
                    f"{'Poikkeavia' if fi else 'Outlier records'} {om.get('records', 0)}: "
                    f"{_fmt(om.get('logged_kg'), 0)} → {_fmt(om.get('imputed_kg'), 0)} kg"
                    if om
                    else ""
                )
            ),
            "outputs": "L3 records (GeoParquet), QA JSON",
        }
    )
    steps.append(
        {
            "level": "L4",
            "title": "Ruudukko" if fi else "Grid",
            "inputs": "L3",
            "method": (
                "Puhtaiden tietueiden massa jaetaan kiinteän ruudukon soluille tietuepolygonien "
                "pinta-alaosuuksien mukaan (massa säilyy); sato = massa / puitu ala solussa. "
                "Laatukaistat: kattavuus, tietueiden määrä, hajonta, liputettujen osuus, "
                "ajokaistojen määrä."
                if fi
                else "The mass of clean records is apportioned to a fixed grid by the area "
                "share of each record polygon (mass is conserved); yield = mass / harvested "
                "area in the cell. Quality bands: coverage, record count, spread, flagged "
                "share, passes."
            ),
            "parameters": {
                ("solukoko" if fi else "cell size"): _fmt(grid.get("resolution"), 0, "m"),
                ("ruudukko" if fi else "grid"): f"{grid.get('nx')} × {grid.get('ny')}",
                ("origo" if fi else "origin"): str(settings.get("grid_origin")),
            },
            "result": (
                f"{'massa' if fi else 'mass'} {_fmt(mc.get('clean_mass_kg'), 0)} kg → "
                f"{_fmt(mc.get('cell_mass_kg'), 0)} kg "
                f"({'täsmää' if fi else 'conserved'}: {mc.get('ok')})"
            ),
            "outputs": "L4 GeoTIFF (COG)",
        }
    )
    return steps


def process_markdown(steps: list[dict], lang: str = "fi") -> str:
    t = TEXT[lang]
    lines = [f"# {t['process']}", ""]
    for s in steps:
        lines += [f"## {s['level']} – {s['title']}", ""]
        lines += [f"**{t['inputs']}:** {s['inputs']}", ""]
        lines += [f"**{t['method']}:** {s['method']}", ""]
        if s["parameters"]:
            lines += [f"**{t['parameters']}:**", ""]
            lines += [f"- {k}: {v}" for k, v in s["parameters"].items()]
            lines += [""]
        lines += [f"**{t['result']}:** {s['result']}", ""]
        lines += [f"**{t['outputs']}:** {s['outputs']}", ""]
    return "\n".join(lines)


# --- figures ---------------------------------------------------------------------


def _plt():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("the report needs matplotlib: pip install yieldsuite[report]") from exc
    return plt


def _save(fig, path: Path) -> Path:
    fig.savefig(path, dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


def _boundary(ax, boundary, color="k"):
    if boundary is not None:
        boundary.boundary.plot(ax=ax, color=color, lw=0.8)


def _decorate(ax):
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal")


def figure_l1a(rec, boundary, path, lang):
    plt = _plt()
    fi = lang == "fi"
    t_min = (rec["t"] - rec["t"].min()) / 60.0
    on = rec["header_down"].to_numpy(bool)
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(rec["x"][~on], rec["y"][~on], s=1, c="0.75", label="header up")
    sc = ax.scatter(rec["x"][on], rec["y"][on], s=1, c=t_min[on], cmap="viridis")
    fig.colorbar(sc, ax=ax, shrink=0.8, label="min " + ("alusta" if fi else "from start"))
    _boundary(ax, boundary)
    ax.set_title(
        "L1A – tietueet ajan mukaan (harmaa: leikkuupöytä ylhäällä)"
        if fi
        else "L1A – records by time (grey: header up)"
    )
    _decorate(ax)
    return _save(fig, path)


def figure_l1b(rec, path, lang):
    plt = _plt()
    fi = lang == "fi"
    step = max(1, len(rec) // 20000)
    r = rec.iloc[::step]
    t = pd.to_datetime(r["t"], unit="s", utc=True)
    on = r["header_down"].to_numpy(bool)
    fig, axes = plt.subplots(3, 1, figsize=(10, 6.5), sharex=True)
    series = [
        ("mass_flow_wet", "kg/s", "massavirta" if fi else "mass flow"),
        ("moisture_aligned", "%", "kosteus" if fi else "moisture"),
        ("speed", "m/s", "nopeus" if fi else "speed"),
    ]
    for ax, (col, unit, label) in zip(axes, series, strict=True):
        if col in r:
            ax.plot(t, r[col], lw=0.4, color="tab:blue")
            ax.scatter(t[~on], r[col][~on], s=1, color="0.6")
        ax.set_ylabel(f"{label} ({unit})")
    axes[0].set_title(
        "L1B – signaalit ajan funktiona (UTC; harmaa: leikkuupöytä ylhäällä)"
        if fi
        else "L1B – signals over time (UTC; grey: header up)"
    )
    return _save(fig, path)


def figure_l2(gdf, boundary, path, lang):
    plt = _plt()
    fi = lang == "fi"
    y = gdf["yield"].to_numpy(float)
    ok = np.isfinite(y) & gdf["header_down"].to_numpy(bool)
    lo, hi = np.nanpercentile(y[ok], [2, 98]) if ok.any() else (0, 1)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 5.5), width_ratios=[2, 1])
    gdf[ok].plot(ax=ax, column="yield", cmap="RdYlGn", vmin=lo, vmax=hi, linewidth=0)
    sm = plt.cm.ScalarMappable(cmap="RdYlGn", norm=plt.Normalize(lo, hi))
    fig.colorbar(sm, ax=ax, shrink=0.8, label="t/ha")
    _boundary(ax, boundary)
    _decorate(ax)
    ax.set_title("L2 – sato tietueittain" if fi else "L2 – yield per record")
    ratio = (gdf["effective_area"] / gdf["nominal_area"].where(gdf["nominal_area"] > 0))[ok]
    ax2.hist(ratio.clip(0, 1.05).dropna(), bins=40, color="tab:blue")
    ax2.set_xlabel("tehollinen / nimellinen ala" if fi else "effective / nominal area")
    ax2.set_title("päällekkäisyys" if fi else "overlap")
    return _save(fig, path)


def _primary_flag(flags: np.ndarray) -> np.ndarray:
    out = np.full(flags.shape, "", dtype=object)
    for f in Flag:
        hit = (flags & int(f)) != 0
        out[(out == "") & hit] = f.name
    return out


def figure_l3(gdf, boundary, qa, path, lang):
    plt = _plt()
    fi = lang == "fi"
    on = gdf["header_down"].to_numpy(bool)
    fl = gdf["flags"].to_numpy(np.int64)
    prim = _primary_flag(fl)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 5.5), width_ratios=[2, 1])
    clean = on & (fl == 0)
    gdf[clean].plot(ax=ax, color="0.85", linewidth=0)
    names = [f.name for f in Flag if f.name != "HEADER_UP" and (prim[on] == f.name).any()]
    cmap = plt.get_cmap("tab10")
    for i, n in enumerate(names):
        sel = on & (prim == n)
        gdf[sel].plot(ax=ax, color=cmap(i % 10), linewidth=0, label=n)
    ax.legend(loc="lower left", fontsize=7, markerscale=4, frameon=True)
    _boundary(ax, boundary)
    _decorate(ax)
    ax.set_title("L3 – laatuliput (harmaa: puhdas)" if fi else "L3 – quality flags (grey: clean)")
    counts = {k: v for k, v in qa.get("flags", {}).items() if v}
    ax2.barh(list(counts), list(counts.values()), color="tab:orange")
    ax2.invert_yaxis()
    ax2.set_xlabel("tietueita" if fi else "records")
    return _save(fig, path)


def figure_raster(path_tif, bands, titles, boundary, path, suptitle):
    plt = _plt()
    import rasterio
    from rasterio.plot import plotting_extent

    with rasterio.open(path_tif) as src:
        desc = list(src.descriptions)
        data = {d: src.read(i + 1, masked=True) for i, d in enumerate(desc) if d in bands}
        ext = plotting_extent(src)
    bands = [b for b in bands if b in data]
    n = len(bands)
    cols = 2 if n > 1 else 1
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(6.2 * cols, 5.2 * rows), squeeze=False)
    for ax, b in zip(axes.ravel(), bands, strict=False):
        a = data[b]
        vals = a.compressed()
        lo, hi = np.percentile(vals, [2, 98]) if vals.size else (0, 1)
        cmap = "RdYlGn" if b in ("yield", "protein_pct_dm", "n_uptake_kg_ha") else "viridis"
        im = ax.imshow(a, extent=ext, cmap=cmap, vmin=lo, vmax=hi)
        fig.colorbar(im, ax=ax, shrink=0.8)
        _boundary(ax, boundary)
        _decorate(ax)
        ax.set_title(titles.get(b, b))
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    fig.suptitle(suptitle)
    return _save(fig, path)


# --- report ----------------------------------------------------------------------


def _img(path: Path) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f'<img alt="{html.escape(path.stem)}" src="data:image/png;base64,{data}">'


def _table(headers, rows) -> str:
    th = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


CSS = """
:root{--bg:#fff;
  --fg:#1d2327;
  --muted:#5f6b73;
  --line:#d9dee2;
  --card:#f6f8f9;
  --accent:#2f7d4f;
  --bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#15191c;
  --fg:#e6e9eb;
  --muted:#a3adb4;
  --line:#2e3539;
  --card:#1d2226;
  --accent:#6cc28f;
  --bad:#f2b8b5}
img{background:#fff}
}
body{
  background:var(--bg);
  color:var(--fg);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
  margin:0;
}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.7rem;margin:.2em 0}
h2{margin-top:2em;border-bottom:1px solid var(--line);padding-bottom:.2em}
h3{margin-bottom:.3em}
.muted{color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.kpi b{display:block;font-size:1.25rem}
.kpi span{color:var(--muted);font-size:.85rem}
section.level{
  background:var(--card);
  border:1px solid var(--line);
  border-radius:10px;
  padding:4px 18px 16px;
  margin:18px 0;
}
img{max-width:100%;height:auto;border-radius:6px;margin-top:8px}
table{
  border-collapse:collapse;
  width:100%;
  font-size:.86rem;
  margin:8px 0;
  display:block;
  overflow-x:auto;
}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
code{font-size:.82rem;word-break:break-all}
.bad{color:var(--bad);font-weight:600}
.good{color:var(--accent);font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;margin:6px 0}
dt{color:var(--muted)}"""


def build_report(config, out_dir: str | Path | None = None, lang: str = "fi") -> dict:
    """Write figures, process description, audit JSON and the HTML report."""
    import geopandas as gpd

    if lang not in TEXT:
        raise ValueError(f"lang must be one of {sorted(TEXT)}")
    t = TEXT[lang]
    out = Path(config.output_dir)
    stem = f"{config.field_id}_{config.year}"
    qa_path = out / f"L4_{stem}_qa.json"
    man_path = out / f"{stem}_run.json"
    l3_path = out / f"L3_{stem}_records.parquet"
    l4_path = out / f"L4_{stem}.tif"
    for p in (qa_path, l3_path):
        if not p.exists():
            raise FileNotFoundError(f"{p} not found: run the chain to L4 first")
    qa = json.loads(qa_path.read_text())
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    rdir = Path(out_dir) if out_dir else out / "report"
    fdir = rdir / "figures"
    fdir.mkdir(parents=True, exist_ok=True)

    gdf = gpd.read_parquet(l3_path)
    boundary = None
    if config.boundary is not None and Path(config.boundary).exists():
        boundary = gpd.read_file(config.boundary).to_crs(gdf.crs)

    figs = {
        "L1A": figure_l1a(gdf, boundary, fdir / "L1A_records.png", lang),
        "L1B": figure_l1b(gdf, fdir / "L1B_signals.png", lang),
        "L2": figure_l2(gdf, boundary, fdir / "L2_yield_records.png", lang),
        "L3": figure_l3(gdf, boundary, qa, fdir / "L3_flags.png", lang),
    }
    fi = lang == "fi"
    if l4_path.exists():
        figs["L4"] = figure_raster(
            l4_path,
            ["yield", "coverage", "n_records", "flagged_share"],
            {
                "yield": "sato (t/ha)" if fi else "yield (t/ha)",
                "coverage": "kattavuus" if fi else "coverage",
                "n_records": "tietueita / solu" if fi else "records per cell",
                "flagged_share": "liputettujen osuus" if fi else "flagged share",
            },
            boundary,
            fdir / "L4_grid.png",
            "L4 – " + ("ruudukko ja laatukaistat" if fi else "grid and quality bands"),
        )
    l5_path = out / f"L5_{stem}.tif"
    if l5_path.exists():
        figs["L5"] = figure_raster(
            l5_path,
            ["yield", "filled", "uncertainty"],
            {},
            boundary,
            fdir / "L5.png",
            "L5 – " + ("fuusio kuvien kanssa" if fi else "fusion with imagery"),
        )
    q4_path = out / f"Q4_{stem}.tif"
    if q4_path.exists():
        figs["Q4"] = figure_raster(
            q4_path,
            ["protein_pct_dm", "n_uptake_kg_ha"],
            {},
            boundary,
            fdir / "Q4.png",
            "Q4 – " + ("jyvän laatu" if fi else "grain quality"),
        )

    steps = process_steps(qa, manifest, lang)
    md_path = rdir / f"{stem}_process.md"
    md_path.write_text(process_markdown(steps, lang))

    problems = verify_manifest(man_path) if man_path.exists() else ["run manifest missing"]
    audit = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest": manifest,
        "ingest": qa.get("ingest", []),
        "qa_warnings": qa.get("warnings", []),
        "verification": {"ok": not problems, "problems": problems},
    }
    audit_path = rdir / f"{stem}_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, default=str))

    html_path = rdir / f"{stem}_report.html"
    html_path.write_text(_html(config, qa, manifest, steps, figs, audit, t, lang))
    return {
        "html": str(html_path),
        "process": str(md_path),
        "audit": str(audit_path),
        "figures": {k: str(v) for k, v in figs.items()},
    }


def _html(config, qa, manifest, steps, figs, audit, t, lang) -> str:
    e = html.escape
    area = (qa.get("field_edge") or {}).get("boundary_area_ha")
    mc = qa.get("mass_conservation", {})
    cls = qa.get("product_class", "")
    kpis = [
        (t["field"], e(str(config.field_id))),
        (t["year"], str(config.year)),
        (t["class"], e(t["classes"].get(cls, cls))),
        (t["area"], _fmt(area, 2, "ha")),
        (t["mean_yield"], _fmt(qa.get("field_mean_yield_t_ha"), 2, "t/ha")),
        (t["mass"], _fmt((qa.get("reconciled_mass_kg") or 0) / 1000, 1, "t")),
        (t["flagged"], _fmt((manifest.get("result") or {}).get("flagged_pct"), 1, "%")),
        (t["mass_check"], t["ok"] if mc.get("ok") else t["fail"]),
    ]
    parts = [
        "<!doctype html><html lang='" + lang + "'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{e(t['title'])} – {e(str(config.field_id))} {config.year}</title>",
        f"<style>{CSS}</style></head><body><main>",
        f"<p class='muted'>{SOFTWARE}</p><h1>{e(t['title'])}: {e(str(config.field_id))} / "
        f"{config.year}</h1>",
        f"<h2>{e(t['summary'])}</h2><div class='kpis'>",
        "".join(f"<div class='kpi'><span>{e(k)}</span><b>{v}</b></div>" for k, v in kpis),
        "</div>",
    ]
    notes = (manifest.get("result") or {}).get("notes") or []
    if notes:
        parts.append("<ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>")
    parts.append(f"<h2>{e(t['process'])}</h2>")
    for s in steps:
        parts.append(f"<section class='level'><h3>{e(s['level'])} – {e(s['title'])}</h3><dl>")
        parts.append(f"<dt>{e(t['inputs'])}</dt><dd>{e(str(s['inputs']))}</dd>")
        parts.append(f"<dt>{e(t['method'])}</dt><dd>{e(s['method'])}</dd>")
        for k, v in s["parameters"].items():
            parts.append(f"<dt>{e(k)}</dt><dd>{e(str(v))}</dd>")
        parts.append(f"<dt>{e(t['result'])}</dt><dd>{e(str(s['result']))}</dd>")
        parts.append(f"<dt>{e(t['outputs'])}</dt><dd>{e(str(s['outputs']))}</dd></dl>")
        if s["level"] in figs:
            parts.append(_img(figs[s["level"]]))
        parts.append("</section>")
    for lvl in ("L5", "Q4"):
        if lvl in figs:
            parts.append(f"<section class='level'><h3>{lvl}</h3>{_img(figs[lvl])}</section>")

    parts.append(f"<h2>{e(t['audit'])}</h2>")
    v = audit["verification"]
    parts.append(
        f"<p class='good'>{e(t['verify_ok'])}</p>"
        if v["ok"]
        else f"<p class='bad'>{e(t['verify_bad'])}</p><ul>"
        + "".join(f"<li>{e(p)}</li>" for p in v["problems"])
        + "</ul>"
    )
    sw = manifest.get("software", {})
    parts.append("<dl>")
    for k, val in [
        (t["software"], f"{sw.get('name', SOFTWARE)} {sw.get('version', '')}"),
        ("git", sw.get("git_commit") or "–"),
        ("Python", f"{sw.get('python', '')} · {sw.get('platform', '')}"),
        (t["run"], manifest.get("run_id", "–")),
        (t["started"], manifest.get("started_utc", "–")),
        (t["finished"], manifest.get("finished_utc", "–")),
        (t["generated"], audit["generated_utc"]),
    ]:
        parts.append(f"<dt>{e(k)}</dt><dd><code>{e(str(val))}</code></dd>")
    parts.append("</dl>")

    def rows(entries):
        for x in entries:
            size = _fmt(x.get("bytes"), unit="B") if "bytes" in x else "–"
            sha = x.get("sha256", "")
            parts_sha = f"<code title='{sha}'>{sha[:16]}…</code>" if sha else "–"
            yield [
                e(x.get("role", "")),
                f"<code>{e(Path(x.get('path', '')).name)}</code>",
                size,
                parts_sha,
            ]

    head = [t["role"], t["file"], t["size"], "SHA-256"]
    parts.append(f"<h3>{e(t['inputs'])}</h3>" + _table(head, rows(manifest.get("inputs", []))))
    parts.append(f"<h3>{e(t['outputs'])}</h3>" + _table(head, rows(manifest.get("outputs", []))))

    parts.append(f"<h3>{e(t['decisions'])}</h3>")
    for ing in audit["ingest"]:
        parts.append(
            f"<p><code>{e(Path(ing.get('source', '')).name)}</code> · "
            f"{e(str(ing.get('format')))} · {e(str(ing.get('input_family')))}</p>"
        )
        unit_rows = [
            [
                e(c),
                f"<code>{e(u.get('column', ''))}</code>",
                e(u.get("unit", "")),
                e(u.get("source", "")),
            ]
            for c, u in (ing.get("units") or {}).items()
        ]
        parts.append(_table([t["column"], t["file"], t["unit"], t["source"]], unit_rows))
        for title, items in (
            (t["derived"], ing.get("derived")),
            (t["warnings"], ing.get("warnings")),
        ):
            parts.append(
                f"<p><b>{e(title)}:</b> "
                + ("; ".join(e(i) for i in items) if items else e(t["none"]))
                + "</p>"
            )
    settings = manifest.get("settings", {})
    parts.append(
        f"<h3>{e(t['parameters'])}</h3><pre><code>"
        f"{e(json.dumps(settings, indent=2, ensure_ascii=False, default=str))}"
        "</code></pre>"
    )
    parts.append("</main></body></html>")
    return "".join(parts)
