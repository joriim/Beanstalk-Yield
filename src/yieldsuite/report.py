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
import shutil
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
        "prototype": "Prototyyppi – tulokset eivät ole vielä validoituja",
        "findings": "Havainnot",
        "overview": "Ketju yhdellä silmäyksellä",
        "figure": "Kuva",
        "captions": {
            "pipeline": "Tasot L0–L4 (ja L5, jos ajettu) ja tietueiden määrä kullakin "
            "tasolla. Jokainen taso "
            "on oma tuotteensa, ja minkä tahansa tason voi laskea uudelleen raakadatasta.",
            "summary": "Vasemmalla 10 m solujen satojakauma (pystyviiva: keskiarvo). "
            "Keskellä tietueiden kulku: rivit tiedostossa → tietueet → puintitietueet → "
            "puhtaat tietueet. Oikealla massatase: puitu massa, siitä puhtaiden "
            "tietueiden massa ja ruudukkoon jaettu massa (kahden viimeisen pitää olla "
            "yhtä suuret).",
            "L1A": "Jokainen piste on yksi sekunnin tietue, väri kertoo ajan puinnin "
            "alusta. Harmaat pisteet: leikkuupöytä ylhäällä (käännökset, siirtymät).",
            "L1B": "Massavirta, kosteus ja nopeus koko puintipäivältä. Katkot ovat "
            "päisteitä ja taukoja; harmaat pisteet ovat aikoja, jolloin leikkuupöytä "
            "oli ylhäällä.",
            "L2": "Sato tietueittain, kun viive, antennin paikka ja päällekkäisyys on "
            "huomioitu. Oikealla tietueiden tehollisen ja nimellisen pinta-alan suhde: "
            "1 = ei päällekkäisyyttä.",
            "L3": "Liputetut tietueet värin mukaan (ensimmäinen lippu), puhtaat harmaana. "
            "Tietueita ei poisteta, joten säännöt voi muuttaa ja ajaa uudelleen.",
            "L4": "Lopullinen 10 m satokartta ja sen laatukaistat: kattavuus, tietueiden "
            "määrä solussa ja liputettujen osuus. Heikon kattavuuden solut ovat "
            "epävarmempia.",
            "L5": "Fuusion tulos: ajokaistavaikutuksesta korjattu satokartta, solun "
            "lähde (mitattu tai kuvien avulla täytetty), täytettyjen solujen epävarmuus ja "
            "suhteellinen sato (lohkon keskiarvo = 100 %). Vain lohkon sisäiset solut.",
            "Q4": "Jyvän laatu (proteiini, typen otto).",
            "review": "Vasemmalla sadon ja kasvuennusteen ero soluittain (punainen: sato "
            "ennustetta pienempi; mustat ruudut: vajealueet, magenta: ylijäämäalueet). "
            "Oikealla tietueiden ero puinnin aikana; musta viiva on liukuva keskiarvo.",
            "season": "Kasvukausi kylvöstä puintiin: ilman ja maan lämpötila, kertyvät "
            "lämpösummat ja päivittäinen säteily; pystyviivat ovat käytetyt kuvapäivät.",
            "moisture": "Jyvän kosteus soluittain (massapainotettu): puitaessa, paikan osuus "
            "(lohkon mediaani + poikkeama puinti-ajan trendistä: märemmät kohdat, joissa "
            "tuleentuminen on myöhässä tai maa märkää), puinti-ajan trendi (30 min liukuva "
            "mediaani: aamukaste, kuivuminen päivän mittaan) ja mitatun kosteuden osuus.",
            "moisture_time": "Kosteus puinnin aikana: harmaa = puimurin kirjaama, värit = "
            "käytetty kosteus lähteittäin. Pitkät vakiojaksot ovat syötettyjä tai jumittuneita "
            "arvoja, ei mittauksia.",
            "harvest": "Jäikö puuttuva alue puimatta? NDTI ennen ja jälkeen puinnin (sänki "
            "laskee NDTI:tä) ja puuttuvan alueen luokka: muutosta verrataan kirjatun, puidun "
            "alan muutokseen. Magenta viiva rajaa alueen, jolla ei ole puintitietueita. "
            "Luokitellaan vain solut, jotka ovat vähintään puolen pikselin päässä alueen "
            "reunasta.",
        },
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
        "prototype": "Prototype – results not yet validated",
        "findings": "Findings",
        "overview": "The chain at a glance",
        "figure": "Figure",
        "captions": {
            "pipeline": "Levels L0–L4 (and L5 when run) and the number of records at "
            "each. Every level is "
            "a product of its own and can be recomputed from the raw data.",
            "summary": "Left: yield distribution of the 10 m cells (line: mean). Middle: "
            "record flow, rows in the file → records → harvesting records → clean "
            "records. Right: mass balance, harvested mass, mass of clean records and "
            "mass apportioned to the grid (the last two must be equal).",
            "L1A": "Each dot is a one-second record, coloured by time since the start. "
            "Grey: header up (turns, transport).",
            "L1B": "Mass flow, moisture and speed over the harvest day. Gaps are "
            "headlands and breaks; grey dots are times with the header up.",
            "L2": "Yield per record after delay, antenna offset and overlap. Right: "
            "effective / nominal area per record, 1 = no overlap.",
            "L3": "Flagged records by colour (first flag), clean records grey. Nothing "
            "is deleted, so the rules can be changed and re-run.",
            "L4": "The final 10 m yield map and its quality bands: coverage, records per "
            "cell and flagged share. Cells with low coverage are less certain.",
            "L5": "Fusion result: yield map corrected for pass effects, the source of "
            "each cell (measured or filled from imagery), the uncertainty of filled cells "
            "and relative yield (field mean = 100 %). Cells inside the field only.",
            "Q4": "Grain quality (protein, N uptake).",
            "review": "Left: yield minus growth prediction per cell (red: below; black "
            "squares: deficit clusters, magenta: surplus). Right: record residual over the "
            "harvest; the black line is a running mean.",
            "season": "Season from sowing to harvest: air and soil temperature, cumulative "
            "degree days and daily radiation; vertical lines are the image dates used.",
            "moisture": "Grain moisture per cell (mass-weighted): as harvested, the place part "
            "(field median + departure from the harvest-time trend: wetter spots with late "
            "ripening or wet soil), the harvest-time trend (30 min running median: dew, drying "
            "during the day) and the share with measured moisture.",
            "moisture_time": "Moisture during the harvest: grey = as logged by the combine, "
            "colours = moisture used, by source. Long constant runs are entered or held "
            "values, not measurements.",
            "harvest": "Was the unlogged area harvested? NDTI before and after the harvest "
            "(stubble lowers NDTI) and the class of the unlogged area: its change is compared "
            "with the change of the logged, harvested area. The magenta line outlines the "
            "area without records. Only cells at least half a pixel inside it are classified.",
        },
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


def process_steps(
    qa: dict, manifest: dict, lang: str = "fi", card: dict | None = None
) -> list[dict]:
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
            "result": _l0_result(ing, fi),
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
    if card:
        steps.append(_l5_step(card, fi))
    return steps


def _final_txt(card: dict, fi: bool) -> str:
    fs = card.get("feature_set") or {}
    d = fs.get("final_image_date") or (card.get("sentinel2") or {}).get("final_image_date")
    if not d:
        return "–"
    a, b = fs.get("cv_rmse_season"), fs.get("cv_rmse_season_and_final_image")
    cv = f" (CV RMSE {a:.2f} → {b:.2f} t/ha)" if a is not None and b is not None else ""
    used = fs.get("chosen") == "season + final image"
    if fi:
        return f"{d}: " + ("käytössä" if used else "ei parantanut ennustetta, ei käytössä") + cv
    return f"{d}: " + ("used" if used else "did not improve the prediction, not used") + cv


def _sources_txt(card: dict, fi: bool) -> str:
    """Per source: own fit to Sentinel-2, the provider's harmonisation, or not used."""
    names = {"planetscope": "PlanetScope", "drone": "drone" if not fi else "drooni"}
    h = (card.get("sources") or {}).get("harmonisation") or {}
    parts = ["Sentinel-2 (" + ("viite" if fi else "reference") + ")"]
    for src, rep in (h.get("sources") or {}).items():
        nm = names.get(src, src)
        dates = [
            x.get("date") for x in card.get("local_imagery", []) if x.get("source", src) == src
        ]
        when = f" {', '.join(str(d) for d in dates if d)}" if dates else ""
        idx = []
        for index, info in (rep.get("indices") or {}).items():
            if not info.get("used"):
                continue
            if info.get("method") == "vendor":
                idx.append(
                    f"{index}: "
                    + (
                        "Planetin oma harmonisointi Sentinel-2:een (ei Sentinel-2-kuvaa "
                        "2 vrk sisällä omaan sovitukseen)"
                        if fi
                        else "Planet's own harmonisation to Sentinel-2 (no Sentinel-2 date "
                        "within 2 days for an own fit)"
                    )
                )
            else:
                idx.append(
                    f"{index}: "
                    + ("oma sovitus" if fi else "own fit")
                    + f" (R² {info.get('r2', float('nan')):.2f}, {info.get('pairs')} "
                    + ("paria)" if fi else "pairs)")
                )
        parts.append(
            f"{nm}{when}: " + ("; ".join(idx) if idx else ("ei käytetty" if fi else "not used"))
        )
    return " · ".join(parts)


def _l5_step(card: dict, fi: bool) -> dict:
    fit = card.get("fit", {})
    cells = card.get("cells", {})
    s2 = card.get("sentinel2", {})
    rc = card.get("record_corrections", {})
    stripes = rc.get("pass_stripes", {})
    shift = rc.get("residual_shift", {})
    dates = s2.get("dates_used", [])
    stripe_txt = (
        (
            f"σ_b {100 * stripes['sigma_b']:.1f} % ({stripes.get('n_passes')} "
            + ("ajokaistaa, korjattu" if fi else "passes, corrected")
            + ")"
        )
        if stripes.get("applied")
        else ("ei korjattu" if fi else "not corrected")
    )
    shift_txt = (
        f"{shift.get('suggested_extra_delay_s'):+.1f} s "
        f"(r {shift.get('corr_at_zero'):.3f} → {shift.get('corr_at_best'):.3f})"
        if shift.get("suggested_extra_delay_s") is not None
        else "–"
    )
    return {
        "level": "L5",
        "title": "Fuusio satelliittikuvien kanssa" if fi else "Fusion with imagery",
        "inputs": "L4 + L3 + Sentinel-2 L2A (openEO, " + str(s2.get("backend", "")) + ")",
        "method": (
            "Sentinel-2-kasvillisuusindeksit (" + ", ".join(s2.get("indices", [])) + ") "
            "haetaan openEO:lla vain ruudukon soluille (satodataa ei lähetetä), pilviset "
            "päivät hylätään. Sadon ja kuvan malli sovitetaan luotettaviin L4-soluihin ja "
            "arvioidaan lohkoittaisella ristiinvalidoinnilla. Kuvaennustetta käytetään "
            "vertailupintana: ajokaistavaikutus poistetaan (lohkon kokonaismassa säilyy) "
            "ja viive tarkistetaan. Lohkon sisäiset aukot täytetään regressiokrigingillä; "
            "täytetyt solut merkitään eikä niitä käytetä opetukseen."
            if fi
            else "Sentinel-2 indices (" + ", ".join(s2.get("indices", [])) + ") are "
            "retrieved with openEO for the grid cells only (no yield data is sent); "
            "cloudy dates are dropped. The yield–image model is fitted on reliable L4 "
            "cells and checked by block cross-validation. The image prediction is the "
            "reference for removing pass effects (field total kept) and checking the "
            "delay. Gaps inside the field are filled by regression kriging; filled cells "
            "are labelled and never used for training."
        ),
        "parameters": {
            ("kuvapäivät" if fi else "image dates"): f"{len(dates)}: " + ", ".join(dates),
            ("hylätyt päivät" if fi else "dropped dates"): str(len(s2.get("dates_dropped", []))),
            ("malli" if fi else "model"): f"{card.get('method')} + {card.get('residual')}",
            ("lähteet" if fi else "sources"): _sources_txt(card, fi),
            ("loppukuva" if fi else "final image"): _final_txt(card, fi),
            ("ajokaistavaikutus" if fi else "pass effect"): stripe_txt,
            ("viivetarkistus" if fi else "delay check"): shift_txt,
        },
        "result": (
            f"R² {fit.get('r2', float('nan')):.2f}, "
            f"CV RMSE {fit.get('cv_rmse', float('nan')):.2f} t/ha; "
            + (
                f"{cells.get('measured')} mitattua ja {cells.get('filled')} täytettyä solua"
                f" ({100 * cells.get('filled_share', 0):.0f} %)"
                if fi
                else f"{cells.get('measured')} measured and {cells.get('filled')} filled "
                f"cells ({100 * cells.get('filled_share', 0):.0f} %)"
            )
        ),
        "outputs": "L5 GeoTIFF (COG), model card (JSON)",
    }


def _l0_result(ing: dict, fi: bool) -> str:
    rows_field, rows_file = _rows_for_field(ing)
    if rows_field != rows_file:
        return (
            f"{_fmt(rows_file)} riviä tiedostossa, joista tämän lohkon {_fmt(rows_field)}"
            if fi
            else f"{_fmt(rows_file)} rows in the file, {_fmt(rows_field)} of them this field"
        )
    return f"{_fmt(rows_file)} {'riviä' if fi else 'rows'}"


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
        if b == "source":  # categorical: 1 measured, 2 filled; 0 = no value
            from matplotlib.colors import ListedColormap

            a = np.ma.masked_where(a.filled(0) < 1, a)
            im = ax.imshow(
                a, extent=ext, cmap=ListedColormap(["#9db8d2", "#e08a3c"]), vmin=0.5, vmax=2.5
            )
            cb = fig.colorbar(im, ax=ax, shrink=0.8, ticks=[1, 2])
            cb.ax.set_yticklabels(["1", "2"])
        else:
            vals = a.compressed()
            lo, hi = np.percentile(vals, [2, 98]) if vals.size else (0, 1)
            if b.endswith("_share"):
                lo, hi = 0.0, 1.0
            cmap = (
                "RdYlGn" if b in ("yield", "protein_pct_dm", "n_uptake_kg_ha")
                else "YlGnBu" if b.startswith("moisture") and b != "moisture_measured_share"
                else "viridis"
            )
            im = ax.imshow(a, extent=ext, cmap=cmap, vmin=lo, vmax=hi)
            fig.colorbar(im, ax=ax, shrink=0.8)
        _boundary(ax, boundary)
        _decorate(ax)
        ax.set_title(titles.get(b, b))
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    fig.suptitle(suptitle)
    return _save(fig, path)


def _rows_for_field(ing: dict) -> tuple[int | None, int | None]:
    """(rows of this field, rows in the file): a 'where' filter keeps a subset."""
    import re

    for d in ing.get("derived", []):
        m = re.match(r"kept (\d+) of (\d+) rows", d)
        if m:
            return int(m.group(1)), int(m.group(2))
    return ing.get("rows_in"), ing.get("rows_in")


def _harvesting(gdf) -> np.ndarray:
    """Harvesting records as counted in the QA (header down and harvested area)."""
    on = gdf["header_down"].to_numpy(bool)
    if "effective_area" in gdf:
        on &= gdf["effective_area"].to_numpy(float) > 0
    return on


def figure_pipeline(qa, manifest, path, lang, card=None):
    """Boxes L0 -> L4 with the number of records and the key result of each level."""
    plt = _plt()
    from matplotlib.patches import FancyBboxPatch

    fi = lang == "fi"
    ing = (qa.get("ingest") or [{}])[0]
    n_h = qa.get("harvesting_records")
    n_clean = None
    if n_h is not None:
        res = manifest.get("result") or {}
        pct = res.get("flagged_pct")
        n_clean = round(n_h * (1 - pct / 100)) if pct is not None else None
    mc = qa.get("mass_conservation", {})
    grid = qa.get("grid", {})
    rows_field, _ = _rows_for_field(ing)
    mf = ing.get("summary", {}).get("median_mass_flow_kg_s")
    w = {
        "rows": "riviä" if fi else "rows",
        "rec": "tietuetta" if fi else "records",
        "harv": "puinnissa" if fi else "harvesting",
        "clean": "puhdasta" if fi else "clean",
    }
    boxes = [
        ("L0", "raakadata" if fi else "raw data", f"{_fmt(rows_field)} {w['rows']}"),
        ("L1A", "tulkinta" if fi else "decoding", f"{_fmt(ing.get('rows_out'))} {w['rec']}"),
        ("L1B", "massavirta" if fi else "mass flow", f"med. {_fmt(mf, 1, 'kg/s')}"),
        ("L2", "paikannettu sato" if fi else "georef. yield", f"{_fmt(n_h)} {w['harv']}"),
        ("L3", "liput, täsmäytys" if fi else "flags, reconcile", f"{_fmt(n_clean)} {w['clean']}"),
        (
            "L4",
            f"{_fmt(grid.get('resolution'), 0)} m " + ("ruudukko" if fi else "grid"),
            f"{_fmt((mc.get('cell_mass_kg') or 0) / 1000, 1)} t",
        ),
    ]
    if card:
        fit = card.get("fit", {})
        boxes.append(
            (
                "L5",
                "fuusio Sentinel-2" if fi else "Sentinel-2 fusion",
                f"R² {fit.get('r2', float('nan')):.2f}",
            )
        )
    fig, ax = plt.subplots(figsize=(2 * len(boxes), 2.4))
    ax.set_xlim(0, len(boxes) * 2)
    ax.set_ylim(0, 2)
    ax.axis("off")
    colours = ["#d9e6f2", "#d9e6f2", "#d9e6f2", "#dcefdc", "#fbe8cc", "#e3dcf2", "#f6d5d5"]
    for i, (lvl, name, num) in enumerate(boxes):
        x = i * 2 + 0.15
        ax.add_patch(
            FancyBboxPatch((x, 0.35), 1.6, 1.3, boxstyle="round,pad=0.05", fc=colours[i], ec="#555")
        )
        ax.text(x + 0.8, 1.35, lvl, ha="center", va="center", fontsize=14, weight="bold")
        ax.text(x + 0.8, 0.98, name, ha="center", va="center", fontsize=9)
        ax.text(x + 0.8, 0.62, num, ha="center", va="center", fontsize=9, color="#333")
        if i < len(boxes) - 1:
            ax.annotate(
                "",
                xy=(x + 1.95, 1.0),
                xytext=(x + 1.68, 1.0),
                arrowprops={"arrowstyle": "->", "color": "#555", "lw": 1.5},
            )
    return _save(fig, path)


def figure_summary(gdf, qa, l4_path, path, lang):
    """Yield distribution of the grid, record flow and mass balance."""
    plt = _plt()
    fi = lang == "fi"
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    ax = axes[0]
    if l4_path is not None and Path(l4_path).exists():
        import rasterio

        with rasterio.open(l4_path) as src:
            y = src.read(1, masked=True).compressed()
        ax.hist(y, bins=40, color="#4c9a6a")
        ax.axvline(float(np.mean(y)), color="k", lw=1)
        ax.set_xlabel("t/ha")
        ax.set_ylabel("soluja" if fi else "cells")
    ax.set_title("L4 satojakauma" if fi else "L4 yield distribution")

    ing = (qa.get("ingest") or [{}])[0]
    on = _harvesting(gdf)
    clean = on & (gdf["flags"].to_numpy(np.int64) == 0)
    labels = (
        ["rivit", "tietueet", "puinnissa", "puhtaat"]
        if fi
        else ["rows", "records", "harvesting", "clean"]
    )
    rows_field, _ = _rows_for_field(ing)
    values = [rows_field or 0, len(gdf), int(on.sum()), int(clean.sum())]
    axes[1].barh(labels, values, color=["#9aa5ad", "#7fa7c9", "#4f86b5", "#2f6e9e"])
    axes[1].invert_yaxis()
    for i, v in enumerate(values):
        axes[1].text(v, i, f" {_fmt(v)}", va="center", fontsize=8)
    axes[1].set_title("tietueiden kulku" if fi else "record flow")
    axes[1].set_xlim(0, max(values) * 1.3 if values and max(values) else 1)

    mc = qa.get("mass_conservation", {})
    total = (qa.get("reconciled_mass_kg") or 0) / 1000
    clean_t = (mc.get("clean_mass_kg") or 0) / 1000
    cell_t = (mc.get("cell_mass_kg") or 0) / 1000
    mlabels = ["puitu", "puhtaat", "ruudukossa"] if fi else ["harvested", "clean", "on grid"]
    axes[2].bar(mlabels, [total, clean_t, cell_t], color=["#b08850", "#d4a95c", "#e8c77f"])
    for i, v in enumerate([total, clean_t, cell_t]):
        axes[2].text(i, v, f"{v:.1f} t", ha="center", va="bottom", fontsize=8)
    axes[2].set_ylabel("t")
    axes[2].set_title("massatase" if fi else "mass balance")
    fig.tight_layout()
    return _save(fig, path)


def _redact(obj):
    """File paths -> file names, for a report that may be shown to others."""
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str) and ("/" in obj or "\\" in obj) and len(obj) > 1:
        return Path(obj).name
    return obj


def _notes_html(text: str) -> str:
    """Analyst notes: '## ' headings, '- ' bullets, blank-line paragraphs, **bold**."""
    import re

    out, items, para = [], [], []

    def inline(s):
        s = html.escape(s)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    for line in text.splitlines():
        st = line.strip()
        if not st:
            flush()
        elif st.startswith("## "):
            flush()
            out.append(f"<h3>{inline(st[3:])}</h3>")
        elif st.startswith("- "):
            if para:
                flush()
            items.append(st[2:])
        else:
            if items:
                flush()
            para.append(st)
    flush()
    return "".join(out)


# --- report ----------------------------------------------------------------------


def _img(path: Path) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f'<img alt="{html.escape(path.stem)}" src="data:image/png;base64,{data}">'


def _table(headers, rows) -> str:
    th = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


CSS = """
.sev{font-weight:700;font-size:.78rem;border-radius:4px;padding:1px 6px}
.sev.alert{background:#f6d5d5;color:#8a1c1c}
.sev.watch{background:#fbe8cc;color:#6b4a12}
.sev.ok{background:#dcefdc;color:#1f5c33}
.badge{background:#fbe8cc;color:#6b4a12;border-radius:999px;padding:2px 10px;font-size:.8rem}
figure{margin:12px 0}
figcaption{color:var(--muted);font-size:.88rem;margin-top:4px}
.notes{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
  border-radius:8px;padding:4px 16px}
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
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:2px 14px;margin:6px 0}
dt{color:var(--muted)}
dd{margin:0;min-width:0;overflow-wrap:anywhere}"""


def build_report(
    config,
    out_dir: str | Path | None = None,
    lang: str = "fi",
    *,
    notes: str | None = None,
    title: str | None = None,
    redact: bool = False,
) -> dict:
    """Write figures, process description, audit JSON and the HTML report.

    ``notes``: the analyst's findings (plain text with '## ' headings and '- '
    bullets) shown at the top. ``redact``: show file names instead of paths in
    the HTML and the audit JSON, for a report that is shown to others.
    """
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

    card_path = out / f"L5_{stem}_model_card.json"
    card = json.loads(card_path.read_text()) if card_path.exists() else None
    figs = {
        "pipeline": figure_pipeline(qa, manifest, fdir / "pipeline.png", lang, card=card),
        "summary": figure_summary(gdf, qa, l4_path, fdir / "summary.png", lang),
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
            ["yield", "source", "sigma", "relative_yield"],
            {
                "yield": "sato (t/ha)" if fi else "yield (t/ha)",
                "source": "1 = mitattu, 2 = täytetty" if fi else "1 = measured, 2 = filled",
                "sigma": "epävarmuus täytetyissä (t/ha)" if fi else "uncertainty, filled (t/ha)",
                "relative_yield": "suhteellinen sato (%)" if fi else "relative yield (%)",
            },
            boundary,
            fdir / "L5.png",
            "L5 – " + ("fuusio kuvien kanssa" if fi else "fusion with imagery"),
        )
    q4_path = out / f"Q4_{stem}.tif"
    if q4_path.exists():
        figs["Q4"] = figure_raster(
            q4_path,
            ["protein_pct_dm", "n_uptake_kg_ha", "moisture_analyzer_pct", "carbohydrate_pct_dm",
             "oil_pct_dm", "starch_pct_dm"],
            {
                "protein_pct_dm": "proteiini (% ka)" if fi else "protein (% DM)",
                "n_uptake_kg_ha": "typen otto jyviin (kg/ha)" if fi else "N uptake (kg/ha)",
                "moisture_analyzer_pct": "kosteus, GrainSense (%)" if fi
                else "moisture, analyzer (%)",
                "carbohydrate_pct_dm": "hiilihydraatti (% ka)" if fi else "carbohydrate (% DM)",
                "oil_pct_dm": "öljy (% ka)" if fi else "oil (% DM)",
                "starch_pct_dm": "tärkkelys (% ka)" if fi else "starch (% DM)",
            },
            boundary,
            fdir / "Q4.png",
            "Q4 – " + ("jyvän laatu" if fi else "grain quality"),
        )

    extras = {}
    m_path = out / f"L4_{stem}_moisture.tif"
    if m_path.exists():
        figs["moisture"] = figure_raster(
            m_path,
            ["moisture_pct", "moisture_spatial_pct", "moisture_time_trend_pct",
             "moisture_measured_share"],
            {
                "moisture_pct": "kosteus puitaessa (%)" if fi else "moisture as harvested (%)",
                "moisture_spatial_pct": "paikan osuus: ilman puinti-ajan vaikutusta (%)" if fi
                else "place part: harvest-time effect removed (%)",
                "moisture_time_trend_pct": "puinti-ajan trendi (%)" if fi
                else "harvest-time trend (%)",
                "moisture_measured_share": "mitatun kosteuden osuus" if fi
                else "share with measured moisture",
            },
            boundary,
            fdir / "moisture.png",
            ("Jyvän kosteus" if fi else "Grain moisture"),
        )
        l3 = out / f"L3_{stem}_records.parquet"
        if l3.exists():
            try:
                figs["moisture_time"] = moisture_timeseries(l3, fdir / "moisture_time.png", lang)
            except (ImportError, KeyError, ValueError):
                pass
    extras["qa"] = qa
    review_path = out / f"L5_{stem}_review.json"
    if review_path.exists():
        extras["review"] = json.loads(review_path.read_text())
        png = out / f"L5_{stem}_review.png"
        if png.exists():
            figs["review"] = Path(shutil.copy(png, fdir / "review.png"))
        hpng = out / f"L5_{stem}_harvest_check.png"
        if hpng.exists() and extras["review"].get("harvest_check"):
            figs["harvest"] = Path(shutil.copy(hpng, fdir / "harvest_check.png"))
    lu = (card or {}).get("light_use")
    if lu:
        extras["light_use"] = lu
        sp = (lu.get("season") or {}).get("figure")
        if sp and Path(sp).exists():
            figs["season"] = Path(shutil.copy(sp, fdir / "season.png"))
    steps = process_steps(qa, manifest, lang, card=card)
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
    if redact:
        audit = _redact(audit)
        manifest = audit["manifest"]
        qa = {**qa, "ingest": audit["ingest"]}
    audit_path = rdir / f"{stem}_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, default=str))

    html_path = rdir / f"{stem}_report.html"
    html_path.write_text(
        _html(
            config,
            qa,
            manifest,
            steps,
            figs,
            audit,
            t,
            lang,
            notes=notes,
            title=title,
            extras=extras,
        )
    )
    return {
        "html": str(html_path),
        "process": str(md_path),
        "audit": str(audit_path),
        "figures": {k: str(v) for k, v in figs.items()},
    }


def _figure(figs, key, t, n) -> str:
    cap = t["captions"].get(key, "")
    return (
        f"<figure>{_img(figs[key])}<figcaption><b>{html.escape(t['figure'])} {n}.</b> "
        f"{html.escape(cap)}</figcaption></figure>"
    )


def _review_html(rv: dict, figs: dict, lang: str, n: int) -> tuple[str, int]:
    e = html.escape
    fi = lang == "fi"
    label = {
        "alert": "HÄLYTYS" if fi else "ALERT",
        "watch": "SEURAA" if fi else "WATCH",
        "ok": "OK",
    }
    rows = []
    for c in rv.get("checks", []):
        v = "–" if c["value"] is None else f"{c['value']:+.1f} {c['unit']}"
        rows.append(
            [
                f"<span class='sev {c['severity']}'>{label[c['severity']]}</span>",
                e(c["id"]),
                e(v),
                e(c["finding"]["fi" if fi else "en"]),
            ]
        )
    head = (
        ["Taso", "Tarkistus", "Arvo", "Havainto"] if fi else ["Level", "Check", "Value", "Finding"]
    )
    intro = (
        f"Satokarttaa verrataan kasvuennusteeseen ({e(rv.get('reference', ''))}); "
        f"{rv.get('records_used', 0)} puhdasta tietuetta ja {rv.get('cells_compared', 0)} solua. "
        "Järjestelmälliset erot osoittavat, mihin satodatassa kannattaa katsoa: ne eivät todista "
        "syytä."
        if fi
        else f"The yield map is compared with the growth prediction ({e(rv.get('reference', ''))});"
        f" {rv.get('records_used', 0)} clean records and {rv.get('cells_compared', 0)} cells. "
        "Systematic differences show where to look in the yield data; they do not prove a cause."
    )
    out = f"<h2>{'Kriittinen arvio' if fi else 'Critical review'}</h2><p>{intro}</p>"
    out += _table(head, rows)
    if "review" in figs:
        out += _figure(figs, "review", TEXT[lang], n)
        n += 1
    hc = rv.get("harvest_check")
    if hc:
        verdict = {
            "harvested": "puitu" if fi else "harvested",
            "harvested_before": "puitu ennen" if fi else "harvested before",
            "not_harvested": "puimaton" if fi else "not harvested",
            "unclear": "epäselvä" if fi else "unclear",
            "too_narrow": "liian kapea" if fi else "too narrow",
        }
        rows = [
            [str(r["region"]), f"{r['area_ha']:.2f}", str(r["pure_cells"]),
             e(verdict.get(r["verdict"], r["verdict"])), e(r.get("harvested_by") or "–")]
            for r in hc.get("regions", [])
        ]
        if rows:
            out += _table(
                ["Alue", "ha", "Luokiteltuja soluja", "Tulos", "Puitu viimeistään"]
                if fi else ["Area", "ha", "Classified cells", "Verdict", "Harvested by"],
                rows,
            )
        pairs = ", ".join(f"{p['family']} {p['before']} → {p['after']}"
                          for p in hc.get("pairs_used", []))
        if pairs:
            out += f"<p>{'Kuvaparit' if fi else 'Image pairs'}: {e(pairs)}. " + e(
                " ".join(hc.get("licences", []))) + "</p>"
        if "harvest" in figs:
            out += _figure(figs, "harvest", TEXT[lang], n)
            n += 1
    return out, n


def _lightuse_html(lu: dict, figs: dict, lang: str, n: int) -> tuple[str, int]:
    e = html.escape
    fi = lang == "fi"
    fm = lu.get("field_median", {})
    rows = [
        [
            e(k),
            "–" if c.get("cv_rmse") is None else f"{c['cv_rmse']:.2f}",
            "–" if c.get("corr") is None else f"{c['corr']:+.2f}",
        ]
        for k, c in lu.get("fill_candidates", {}).items()
    ]
    out = f"<h2>{'Valonkäyttö ja kasvukausi' if fi else 'Light use and season'}</h2>"
    out += "<dl>"
    items = [
        ("PAR", lu.get("par_source", "")),
        ("T", lu.get("temperature", "")),
        (
            "kylvö – puinti" if fi else "sowing – harvest",
            f"{lu.get('sowing_date')} – {lu.get('harvest_date')}",
        ),
        ("IPAR (mediaani)" if fi else "IPAR (median)", f"{fm.get('ipar_mj_m2', 0):.0f} MJ/m²"),
        ("GPP (mediaani)" if fi else "GPP (median)", f"{fm.get('gpp_g_c_m2', 0):.0f} g C/m²"),
        (
            "RUE (jyvä / IPAR)" if fi else "RUE (grain / IPAR)",
            f"{fm.get('rue_g_grain_mj', 0):.2f} g/MJ",
        ),
        (
            "aukkojen täyttö" if fi else "gap fill",
            f"{lu.get('fill_used')} – {lu.get('fill_reason', '')}",
        ),
    ]
    se = lu.get("season") or {}
    if se:
        items += [
            ("ilman lämpötila" if fi else "air temperature", f"{se['tair_mean_c']:.1f} °C"),
            (
                "maan lämpötila" if fi else "soil temperature",
                f"{se['tsoil_mean_c']:.1f} °C ({se['soil_temperature_source']})",
            ),
            (
                "lämpösumma ilma / maa" if fi else "degree days air / soil",
                f"{se['gdd_air']:.0f} / {se['gdd_soil']:.0f} °Cd (> {se['t_base_air_c']:g} °C)",
            ),
            (
                "säteily / PAR" if fi else "radiation / PAR",
                f"{se['radiation_mj_m2']:.0f} / {se['par_mj_m2']:.0f} MJ/m²",
            ),
        ]
        val = se.get("radiation_validation")
        if val:
            items.append(
                (
                    "säteilymallin tarkistus" if fi else "radiation model check",
                    f"{val['station']}: r {val['r']:.2f}, RMSE {val['rmse_mj_m2']:.1f} MJ/m²/d, "
                    f"k {val['k']:.2f} ({val['days']} d)",
                )
            )
    out += "".join(f"<dt>{e(k)}</dt><dd>{e(str(v))}</dd>" for k, v in items) + "</dl>"
    out += _table(
        ["ennustaja" if fi else "predictor", "CV RMSE t/ha", "r (sato)" if fi else "r (yield)"],
        rows,
    )
    if "season" in figs:
        out += _figure(figs, "season", TEXT[lang], n)
        n += 1
    return out, n


def _html(
    config, qa, manifest, steps, figs, audit, t, lang, notes=None, title=None, extras=None
) -> str:
    extras = extras or {}
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
        f"<p class='muted'>{SOFTWARE} · <span class='badge'>{e(t['prototype'])}</span></p>",
        f"<h1>{e(title or t['title'])}</h1>",
        f"<p class='muted'>{e(t['field'])} {e(str(config.field_id))} · {e(t['year'])} "
        f"{config.year}</p>",
        f"<h2>{e(t['summary'])}</h2><div class='kpis'>",
        "".join(f"<div class='kpi'><span>{e(k)}</span><b>{v}</b></div>" for k, v in kpis),
        "</div>",
    ]
    run_notes = (manifest.get("result") or {}).get("notes") or []
    if run_notes:
        parts.append("<ul>" + "".join(f"<li>{e(n)}</li>" for n in run_notes) + "</ul>")
    if notes:
        parts.append(f"<h2>{e(t['findings'])}</h2><div class='notes'>{_notes_html(notes)}</div>")
    n = 1
    if extras.get("review"):
        block, n = _review_html(extras["review"], figs, lang, n)
        parts.append(block)
    parts.append(f"<h2>{e(t['overview'])}</h2>")
    for key in ("pipeline", "summary"):
        if key in figs:
            parts.append(_figure(figs, key, t, n))
            n += 1
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
            parts.append(_figure(figs, s["level"], t, n))
            n += 1
        parts.append("</section>")
    for lvl in ("Q4",):
        if lvl in figs:
            parts.append(f"<section class='level'><h3>{lvl}</h3>{_figure(figs, lvl, t, n)}")
            parts.append("</section>")
            n += 1

    if "moisture" in figs:
        block, n = _moisture_html(extras.get("qa", {}), figs, lang, n)
        parts.append(block)
    if extras.get("light_use"):
        block, n = _lightuse_html(extras["light_use"], figs, lang, n)
        parts.append(block)
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


SOURCE_LABEL = {
    "fi": {0: "puimuri, mitattu", 1: "GrainSense", 2: "puimuri, kalibroitu GrainSenseen",
           3: "täytetty ajassa (syötetty arvo)", 4: "lohkon mediaani", 5: "valmistajan vakio"},
    "en": {0: "combine, measured", 1: "analyzer", 2: "combine, calibrated to the analyzer",
           3: "filled in time (entered value)", 4: "field median", 5: "vendor reference"},
}


def moisture_timeseries(l3_path, path, lang: str = "fi"):
    """Moisture over harvest time: combine as logged, entered values, and the moisture
    used, coloured by its source."""
    import pandas as pd

    plt = _plt()
    r = pd.read_parquet(l3_path, columns=["t", "machine_id", "moisture_sensor",
                                         "moisture_aligned", "moisture_source"])
    r = r.sort_values("t")
    tt = pd.to_datetime(r["t"], unit="s", utc=True).dt.tz_convert("Europe/Helsinki")
    fig, ax = plt.subplots(figsize=(12, 4.2))
    ax.plot(tt, r["moisture_sensor"], ".", ms=1.5, color="#adb5bd",
            label="puimuri sellaisenaan" if lang == "fi" else "combine as logged")
    colors = {0: "#495057", 1: "#1c7ed6", 2: "#2b8a3e", 3: "#e8590c", 4: "#c92a2a", 5: "#868e96"}
    for code, g in r.groupby("moisture_source"):
        ax.plot(tt[g.index], g["moisture_aligned"], ".", ms=2, color=colors.get(int(code), "k"),
                label=SOURCE_LABEL[lang].get(int(code), str(code)))
    ax.set_ylabel("kosteus (%)" if lang == "fi" else "moisture (%)")
    ax.legend(loc="upper right", fontsize=8, markerscale=5, ncol=2)
    ax.grid(alpha=0.3)
    ax.set_title("Kosteus puinnin aikana" if lang == "fi" else "Moisture during the harvest")
    fig.autofmt_xdate()
    return _save(fig, path)


def _moisture_html(qa: dict, figs: dict, lang: str, n: int) -> tuple[str, int]:
    e = html.escape
    fi = lang == "fi"
    mm = qa.get("moisture_map") or {}
    rows = []
    for mid, m in (qa.get("moisture") or {}).items():
        c = m.get("combine_moisture") or {}
        if c.get("measured") is False and "entered_value_pct" in c:
            ent = (f"vakio {c['entered_value_pct']:g} %: syötetty, ei mitattu" if fi
                   else f"constant {c['entered_value_pct']:g} %: entered, not measured")
        elif c.get("entered_records"):
            vals = ", ".join(f"{k} %" for k in c.get("default_values_pct", {}))
            ent = (f"{c['entered_records']} tietuetta ({100 * c['entered_share']:.0f} %) "
                   f"jumittunut arvo {vals}" if fi
                   else f"{c['entered_records']} records ({100 * c['entered_share']:.0f} %) "
                   f"held at {vals}")
        else:
            ent = "mitattu" if fi else "measured"
        if m.get("method"):
            cal = (f"GrainSense − puimuri {m.get('median_offset_pct', 0):+.1f} %-yks. "
                   f"({m['pairs']} paria, {m['method']})" if fi
                   else f"analyzer − combine {m.get('median_offset_pct', 0):+.1f} points "
                   f"({m['pairs']} pairs, {m['method']})")
        else:
            cal = "–"
        dm = m.get("mass_at_reference_change_pct")
        rows.append([e(str(mid)), e(ent), e(cal), "–" if dm is None else f"{dm:+.1f} %"])
    out = f"<h2>{'Jyvän kosteus' if fi else 'Grain moisture'}</h2>"
    if mm.get("field_mean_pct") is not None:
        lo, hi = mm["p10_p90_pct"]
        tr = mm["time_trend_range_pct"]
        out += "<p>" + e(
            f"Lohkon keskikosteus {mm['field_mean_pct']:.1f} % (10–90 %: {lo:.1f}–{hi:.1f} %). "
            f"Puinti-ajan trendi vaihteli {tr[0]:.1f}–{tr[1]:.1f} %; paikan osuuden hajonta "
            f"{mm['spatial_sd_pct']:.1f} %-yks. Mitatun kosteuden osuus "
            f"{100 * mm['measured_share']:.0f} %."
            if fi else
            f"Field mean moisture {mm['field_mean_pct']:.1f} % (10–90 %: {lo:.1f}–{hi:.1f} %). "
            f"The harvest-time trend ranged {tr[0]:.1f}–{tr[1]:.1f} %; the place part varies by "
            f"{mm['spatial_sd_pct']:.1f} points (SD). Measured moisture covers "
            f"{100 * mm['measured_share']:.0f} %."
        ) + "</p>"
    if rows:
        out += _table(
            ["Kone", "Puimurin kosteus", "Kalibrointi", "Vaikutus kuivasatoon"] if fi
            else ["Machine", "Combine moisture", "Calibration", "Effect on dry yield"],
            rows,
        )
    for key in ("moisture_time", "moisture"):
        if key in figs:
            out += _figure(figs, key, TEXT[lang], n)
            n += 1
    return out, n
