"""What is known, what was filled in, how sure the results are, and what to ask for.

The chain never stops because an input is missing: it fills the gap as well as it can.
This module makes that visible. From the config and the outputs of a field-year it
lists every input and assumption as an *item*:

- ``status``: ``known`` (given or measured), ``estimated`` (derived from the data),
  ``assumed`` (a default or a guess) or ``missing`` (nothing to go on);
- ``certainty``: ``high``, ``medium`` or ``low``;
- ``how_filled``: what the chain did instead;
- ``effect``: what it means for the results, with numbers where the data give them;
- ``ask``: the question to the user, and the command or config key that takes the answer.

and grades the products (yield level, yield pattern, filled area, moisture, protein) by the
weakest item they depend on. Written as ``<field>_<year>_certainty.json`` after every run;
``yieldsuite gaps`` prints the open questions; the report starts with it.
"""

from __future__ import annotations

import json
from pathlib import Path

ORDER = {"low": 0, "medium": 1, "high": 2}
LABEL_FI = {"high": "hyvä", "medium": "kohtalainen", "low": "heikko",
            "known": "tiedossa", "estimated": "arvioitu datasta", "assumed": "oletettu",
            "missing": "puuttuu"}


def _item(id_, topic_fi, topic_en, status, certainty, how_fi, how_en, effect_fi="", effect_en="",
          ask_fi="", ask_en="", command="", affects=()):
    return {
        "id": id_,
        "topic": {"fi": topic_fi, "en": topic_en},
        "status": status,
        "certainty": certainty,
        "how_filled": {"fi": how_fi, "en": how_en},
        "effect": {"fi": effect_fi, "en": effect_en},
        "ask": {"fi": ask_fi, "en": ask_en} if ask_fi else None,
        "command": command,
        "affects": list(affects),
    }


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def assess(config, out_dir=None) -> dict:
    """Items, product grades and open questions for one field-year (reads the outputs)."""
    from .inputs import read_log

    out = Path(out_dir or config.output_dir)
    base = f"{config.field_id}_{config.year}"
    qa = _load(out / f"L4_{base}_qa.json") or {}
    q4 = _load(out / f"Q4_{base}_qa.json")
    card = _load(out / f"L5_{base}_model_card.json")
    review = _load(out / f"L5_{base}_review.json") or {}
    checks = {c["id"]: c for c in review.get("checks", [])}
    cfg_name = Path(config.source_path).name if config.source_path else "field.yaml"
    log = read_log(config.source_path) if config.source_path else []
    items = []

    # 1 scale tickets -> yield level
    k = qa.get("k_factors") or {}
    if config.scale_tickets is not None and Path(config.scale_tickets).exists():
        ks = ", ".join(f"{m} {v:.3f}" for m, v in k.items())
        items.append(_item(
            "scale_tickets", "Vaakakuitit", "Scale tickets", "known", "high",
            f"Satotaso täsmäytetty punnittuihin kuormiin (k: {ks}).",
            f"Yield level reconciled to the weighed loads (k: {ks}).", affects=["yield_level"]))
    else:
        items.append(_item(
            "scale_tickets", "Vaakakuitit", "Scale tickets", "missing", "low",
            "Käytetty satokartoittimen omaa tasoa (k = 1).",
            "The yield monitor's own level is used (k = 1).",
            "Satotasoa ei ole varmistettu; kalibroimaton kartoitin poikkeaa tyypillisesti 5–15 %. "
            "Lohkon sisäinen vaihtelu ei tästä muutu.",
            "The yield level is unverified; an uncalibrated monitor is typically 5-15 % off. "
            "The within-field pattern does not change.",
            "Paljonko lohkolta punnittiin (kg, kosteus)? Vaakakuitit, kuivurin vastaanotto tai "
            "kuormakirjanpito.",
            "How much was weighed from the field (kg, moisture)? Weighbridge tickets, dryer "
            "intake or load records.",
            f"yieldsuite tickets add {cfg_name} --machine <id> --mass-kg <kg> --moisture <%>",
            ["yield_level"]))

    # 2 antenna position
    direction = checks.get("direction")
    measured = {e["machine_id"] for e in log if e.get("action") == "set_machine"
                and "antenna_offset_m" in (e.get("new") or {})}
    for mid, prof in config.machines.items():
        if mid in measured:
            items.append(_item(
                f"antenna:{mid}", f"Antennin sijainti ({mid})", f"Antenna position ({mid})",
                "known", "high",
                f"Syötetty koneprofiiliin: {list(prof.antenna_offset_m)} m.",
                f"Entered in the machine profile: {list(prof.antenna_offset_m)} m.",
                affects=["yield_pattern"]))
            continue
        bad = direction is not None and direction["severity"] != "ok"
        val = f"{direction['value']:+.0f} %" if direction and direction["value"] is not None else "–"
        items.append(_item(
            f"antenna:{mid}", f"Antennin sijainti ({mid})", f"Antenna position ({mid})",
            "assumed", "low" if bad else "medium",
            f"Oletus {list(prof.antenna_offset_m)} m (ei mitattu).",
            f"Default {list(prof.antenna_offset_m)} m (not measured).",
            (f"Vastakkaisiin suuntiin ajettujen kaistojen tasoero {val}: viittaa sivuttaissiirtoon. "
             "Kaistojen väliset erot näkyvät kartassa raitoina." if bad
             else f"Suuntaero {val}: ei merkkejä virheestä."),
            (f"Passes driven in opposite directions differ by {val}: points to a lateral offset. "
             "It shows as stripes in the map." if bad
             else f"Direction difference {val}: no sign of an error."),
            "Missä GNSS-antenni on leikkuupöydän keskikohtaan nähden (m eteen, m sivulle)?",
            "Where is the GNSS antenna relative to the header centre (m forward, m sideways)?",
            f"yieldsuite machine estimate-offset {cfg_name}  |  yieldsuite machine set {cfg_name} "
            f"--machine {mid} --antenna-right <m> --antenna-forward <m> --reason \"mitattu\"",
            ["yield_pattern"]))

    # 3 flow delay
    for mid, d in (qa.get("delays") or {}).items():
        src = "vendor" if d.get("vendor_applied") else d.get("source", "")
        if src == "default":
            items.append(_item(
                f"delay:{mid}", f"Virtausviive ({mid})", f"Flow delay ({mid})", "assumed", "low",
                f"Oletusviive {d['tau_d_s']:g} s; datasta ei tunnistettavissa.",
                f"Default delay {d['tau_d_s']:g} s; not identifiable from the data.",
                "Sato voi olla siirtynyt ajosuunnassa.", "Yield may be shifted along the track.",
                "Mikä on puimurin virtausviive (s)?", "What is the combine's flow delay (s)?",
                f"yieldsuite delay {cfg_name}", ["yield_pattern"]))
        else:
            how = {"vendor": ("Valmistajan vienti on jo siirtänyt sadon leikkuupaikkaan.",
                              "The vendor export already placed the yield where it was cut."),
                   "machine profile": (f"Koneprofiilista {d['tau_d_s']:g} s.",
                                       f"From the machine profile, {d['tau_d_s']:g} s."),
                   }.get(src, (f"Arvioitu vastakkaisista kaistoista {d['tau_d_s']:g} s.",
                               f"Estimated from opposite passes, {d['tau_d_s']:g} s."))
            st = "known" if src in ("vendor", "machine profile") else "estimated"
            items.append(_item(f"delay:{mid}", f"Virtausviive ({mid})", f"Flow delay ({mid})",
                               st, "medium" if src == "vendor" else "high" if st == "known"
                               else "medium", how[0], how[1], affects=["yield_pattern"]))

    # 4 moisture
    for mid, m in (qa.get("moisture") or {}).items():
        c = m.get("combine_moisture") or {}
        ent = c.get("entered_share") or (1.0 if c.get("measured") is False else 0.0)
        off = m.get("median_offset_pct")
        dm = m.get("mass_at_reference_change_pct")
        if off is not None:
            big = abs(off) > 2
            items.append(_item(
                f"moisture:{mid}", f"Kosteus ({mid})", f"Moisture ({mid})", "estimated",
                "low" if big else "medium",
                f"Puimurin kosteus kalibroitu analysaattoriin ({off:+.1f} %-yks., {m.get('pairs')} "
                "paria).",
                f"Combine moisture calibrated to the analyzer ({off:+.1f} points, {m.get('pairs')} "
                "pairs).",
                (f"Ero on suuri: kumpi mittari on oikeassa, ei tiedetä. Vaikutus kuivasatoon "
                 f"{dm:+.1f} %." if big and dm is not None else
                 f"Vaikutus kuivasatoon {dm:+.1f} %." if dm is not None else ""),
                (f"The difference is large: which meter is right is not known. Effect on dry yield "
                 f"{dm:+.1f} %." if big and dm is not None else
                 f"Effect on dry yield {dm:+.1f} %." if dm is not None else ""),
                "Mikä oli erän kosteus kuivurin vastaanotossa tai laboratoriossa?" if big else "",
                "What was the moisture at dryer intake or in the lab?" if big else "",
                "tickets add ... --moisture <%>  (tai/or quality.lab_samples)" if big else "",
                ["moisture", "yield_level"]))
        elif ent > 0.5:
            items.append(_item(
                f"moisture:{mid}", f"Kosteus ({mid})", f"Moisture ({mid})", "assumed", "low",
                "Puimurin kosteus on vakio (syötetty, ei mitattu); käytetty sellaisenaan.",
                "The combine moisture is constant (entered, not measured); used as it is.",
                "Yksi kosteusprosenttiyksikkö on noin 1,2 % kuivasatoa.",
                "One point of moisture is about 1.2 % of dry yield.",
                "Mikä oli viljan todellinen kosteus puitaessa?",
                "What was the real grain moisture at harvest?",
                "tickets add ... --moisture <%>", ["moisture", "yield_level"]))
        else:
            items.append(_item(
                f"moisture:{mid}", f"Kosteus ({mid})", f"Moisture ({mid})", "known", "medium",
                "Puimurin anturi" + (f"; {100 * ent:.0f} % tietueista oli syötettyjä tai jumissa ja "
                                     "korvattiin naapuriarvoilla." if ent > 0 else "."),
                "Combine sensor" + (f"; {100 * ent:.0f} % of the records were entered or stuck "
                                    "values and were replaced from neighbours." if ent > 0 else "."),
                "Anturia ei ole verrattu vertailumittaukseen.",
                "The sensor has not been checked against a reference.",
                affects=["moisture", "yield_level"]))

    # 5 analyzer: timing, coverage, other machine, lab calibration
    if q4:
        n_rec = qa.get("harvesting_records") or qa.get("records") or 0
        for sidx, s in enumerate(q4.get("sources", [])):
            tm = s.get("timing") or {}
            gate = s.get("position_gate") or {}
            kept, dropped = gate.get("records_kept", 0), gate.get("records_dropped", 0)
            share = kept / n_rec if n_rec else 0.0
            if tm.get("clock_source", "").startswith("delay_s") or tm.get("transit_s") is None:
                items.append(_item(
                    f"analyzer_timing:{sidx}", "Analysaattorin ajoitus", "Analyzer timing",
                    "estimated" if tm.get("clock_offset_s") is not None else "assumed", "medium",
                    f"Viive {tm.get('delay_s', s.get('delay_s'))} s ({tm.get('clock_source', '')}); "
                    "viljan matka-aikaa leikkuupöydältä analysaattoriin ei ole annettu (0 s).",
                    f"Delay {tm.get('delay_s', s.get('delay_s'))} s ({tm.get('clock_source', '')}); "
                    "grain transit time from header to analyzer not given (0 s).",
                    "Laatulukemat voivat olla 10–15 m ajosuunnassa sivussa.",
                    "Quality readings may be 10-15 m off along the track.",
                    "Kuinka monta sekuntia vilja kulkee leikkuupöydältä analysaattorille?",
                    "How many seconds does grain take from the header to the analyzer?",
                    "quality.sources[].transit_s  (clock_offset_s: auto)", ["protein"]))
            cert = "high" if share > 0.6 else "medium" if share > 0.25 else "low"
            items.append(_item(
                f"analyzer_coverage:{sidx}", "Analysaattorin kattavuus", "Analyzer coverage",
                "known", cert,
                f"Laatulukema {kept} tietueella ({100 * share:.0f} % puinnista).",
                f"A quality reading on {kept} records ({100 * share:.0f} % of the harvest).",
                "Proteiinikartta kattaa vain tämän osan; lohkon keskiarvo on otos." if share < 0.6
                else "",
                "The protein map covers this part only; the field mean is a sample."
                if share < 0.6 else "",
                "Oliko analysaattori päällä koko puinnin ajan? Onko muita sessioita?"
                if share < 0.6 else "",
                "Was the analyzer on for the whole harvest? Are there other sessions?"
                if share < 0.6 else "",
                "quality.sources (lisää sessio / add a session)" if share < 0.6 else "",
                ["protein"]))
            if dropped and dropped > 0.2 * max(kept, 1):
                items.append(_item(
                    f"analyzer_machine:{sidx}", "Analysaattori eri koneella?",
                    "Analyzer on another machine?", "estimated", "low",
                    f"{dropped} tietuetta sai ajan perusteella lukeman, jonka oma GPS oli yli "
                    f"{gate.get('max_dist_m'):g} m päässä: lukemat jätettiin käyttämättä.",
                    f"{dropped} records matched in time a reading whose own GPS was more than "
                    f"{gate.get('max_dist_m'):g} m away: those readings were not used.",
                    "Lohkolla on voinut toimia toinen puimuri, jonka loki puuttuu.",
                    "A second combine without a log may have worked on the field.",
                    "Puiko lohkoa toinenkin puimuri? Millä koneella analysaattori oli ja milloin?",
                    "Did a second combine work on the field? Which machine carried the analyzer, "
                    "and when?",
                    "harvest_logs (lisää toisen koneen loki / add the other machine's log)",
                    ["protein", "filled_area"]))
        lab = q4.get("lab_calibration") or {}
        if not lab.get("applied"):
            items.append(_item(
                "protein_lab", "Proteiinin laboratoriovertailu", "Protein lab reference",
                "missing", "medium",
                "Analysaattorin omat arvot; taso varmistamatta, vaihtelu käyttökelpoinen.",
                "The analyzer's own values; level unverified, pattern usable.",
                ask_fi="Onko erästä laboratorion proteiinitulos?",
                ask_en="Is there a lab protein result for the lot?",
                command="quality.lab_samples", affects=["protein"]))

    # 6 unlogged area
    unl = checks.get("unlogged")
    hs = review.get("harvest_check") or {}
    if unl and unl["severity"] != "ok":
        a = hs.get("area_ha_by_verdict") or {}
        told = ", ".join(f"{k} {v:.2f} ha" for k, v in a.items() if v) or "ei tarkistettu"
        standing = a.get("not_harvested", 0) > 0.1
        items.append(_item(
            "unlogged", "Ala ilman puintitietueita", "Area without harvest records",
            "estimated", "low",
            f"{unl['value']:.0f} % lohkosta ilman tietueita; satelliittikuvista: {told}. Sato "
            "täytetty mallilla.",
            f"{unl['value']:.0f} % of the field has no records; from satellite images: {told}. "
            "Yield filled by the model.",
            "Täytetty sato on arvio, ei mittaus." + (" Osa näyttää puimattomalta." if standing
                                                      else ""),
            "The filled yield is an estimate, not a measurement."
            + (" Part of it looks unharvested." if standing else ""),
            "Miksi alueelta puuttuu loki: toinen puimuri, kartoitin pois päältä vai puimatta?",
            "Why is the log missing there: another combine, logging off, or not harvested?",
            f"yieldsuite harvest-check {cfg_name}", ["filled_area"]))

    # 7 sowing date, weather, prediction model
    crop = config.crop
    if crop is None or not crop.sowing_date:
        items.append(_item(
            "sowing_date", "Kylvöpäivä", "Sowing date", "missing", "low",
            "Valonkäyttömalleja (IPAR, GPP, RUE) ei voitu laskea; käytetty tilastollista "
            "kuvamallia.",
            "Light-use models (IPAR, GPP, RUE) could not be computed; the statistical image "
            "model was used.",
            ask_fi="Milloin lohko kylvettiin?", ask_en="When was the field sown?",
            command="crop.sowing_date", affects=["filled_area"]))
    elif getattr(crop, "sowing_date_assumed", False):
        items.append(_item(
            "sowing_date", "Kylvöpäivä", "Sowing date", "assumed", "medium",
            f"Oletus {crop.sowing_date}.", f"Assumed {crop.sowing_date}.",
            "Muuttaa IPAR- ja GPP-summia ja lämpösummaa, ei suhteisiin perustuvaa täyttöä.",
            "Changes the IPAR and GPP sums and degree days, not the ratio-based fill.",
            "Milloin lohko kylvettiin?", "When was the field sown?", "crop.sowing_date",
            ["filled_area"]))
    lu = (card or {}).get("light_use") or {}
    if card:
        cells = card.get("cells") or {}
        fshare = cells.get("filled_share") or 0.0
        ishare = cells.get("interpolated_share") or 0.0
        cand = lu.get("fill_candidates") or {}
        used = card.get("prediction_model", lu.get("fill_used", "image"))
        cv = (cand.get(used) or {}).get("cv_rmse")
        mean_cv = (cand.get("mean") or {}).get("cv_rmse")
        skill = None if cv is None or not mean_cv else 1 - (cv / mean_cv) ** 2
        cert = ("high" if fshare < 0.05 else "low" if skill is None or skill < 0.1
                else "medium" if skill < 0.4 else "high")
        table = ", ".join(f"{n} {c['cv_rmse']:.2f}" for n, c in cand.items()
                          if c.get("cv_rmse") is not None)
        no_skill = skill is not None and skill < 0.1
        items.append(_item(
            "prediction_model", "Satoennuste ja täyttö", "Yield prediction and gap fill",
            "estimated", cert,
            f"Malli: {used}. {100 * fshare:.0f} % lohkon ruuduista on täytetty mallilla ja "
            f"{100 * ishare:.0f} % kapeina aukkoina mitatuista naapureista. Ristiinvalidoitu "
            f"virhe (t/ha): {table}.",
            f"Model: {used}. {100 * fshare:.0f} % of the field's cells are filled by the model and "
            f"{100 * ishare:.0f} % as narrow gaps from measured neighbours. Cross-validated "
            f"error (t/ha): {table}.",
            ("Malli ei ennusta lohkon sisäistä vaihtelua keskiarvoa paremmin: täytetyt arvot ovat "
             "käytännössä lähialueen keskiarvoja." if no_skill else
             f"Malli selittää noin {100 * skill:.0f} % vaihtelusta." if skill is not None else "")
            + ("" if lu.get("clear_winner", True) else " Mallien välillä ei ole selvää eroa."),
            ("The model does not predict the within-field variation better than the mean: the "
             "filled values are in practice local averages." if no_skill else
             f"The model explains about {100 * skill:.0f} % of the variation."
             if skill is not None else "")
            + ("" if lu.get("clear_winner", True) else " No clear difference between the models."),
            "Mitä mallia käytetään: ipar, gpp, rue vai image?"
            if (no_skill or not lu.get("clear_winner", True)) else "",
            "Which model to use: ipar, gpp, rue or image?"
            if (no_skill or not lu.get("clear_winner", True)) else "",
            "light_use.fill: ipar | gpp | rue | image"
            if (no_skill or not lu.get("clear_winner", True)) else "",
            ["filled_area"]))
        if "clear-sky" in str(lu.get("par_source", "")):
            items.append(_item(
                "weather", "Säätiedot", "Weather", "assumed", "low",
                "Säätä ei saatu: käytetty pilvettömän taivaan säteilyä.",
                "No weather data: clear-sky radiation used.", affects=["filled_area"]))
        for pr in card.get("problems", []):
            items.append(_item(
                f"failed:{pr['step']}", f"Vaihe epäonnistui: {pr['step']}",
                f"Step failed: {pr['step']}", "missing", "low", pr["consequence"],
                pr["consequence"], pr["error"], pr["error"], affects=["filled_area"]))
    if config.boundary is None or not Path(config.boundary).exists():
        items.append(_item(
            "boundary", "Lohkon raja", "Field boundary", "missing", "medium",
            "Raja puuttuu: ruudukko kattaa vain kirjatun alan, reunaa ja puuttuvaa alaa ei "
            "tunnisteta.",
            "No boundary: the grid covers the logged area only; edges and unlogged area are not "
            "detected.",
            ask_fi="Mikä on peruslohkotunnus?", ask_en="What is the parcel id?",
            command="yieldsuite parcel <id>", affects=["filled_area", "yield_pattern"]))

    cs = _load(out / f"{base}_cell_size.json")
    if cs and cs.get("reliability") is not None:
        rel, se, size = cs["reliability"], cs["se_t_ha"], cs["resolution_m"]
        work = next((t for t in cs["table"] if t["resolution_m"] == cs["working_resolution_m"]), {})
        wrel = work.get("reliability") if work.get("assessable") else None
        wtxt_fi = f" Työruudukon {cs['working_resolution_m']:g} m ruuduissa toistuvuus on " \
                  f"{wrel:.2f}." if wrel is not None else ""
        wtxt_en = f" On the {cs['working_resolution_m']:g} m working grid it is {wrel:.2f}." \
            if wrel is not None else ""
        items.append(_item(
            "cell_size", "Ruutukoko, jossa sato toistuu", "Cell size at which yield repeats",
            "estimated", "high" if rel >= 0.8 else "medium" if rel >= 0.5 else "low",
            f"Mitattu ajokaistojen puolikkaista: {size:g} m ruuduissa toistuvuus {rel:.2f}.",
            f"Measured from interleaved passes: repeatability {rel:.2f} in {size:g} m cells.",
            (f"Yhden {size:g} m ruudun sato on epävarma noin ±{se:.2f} t/ha.{wtxt_fi}"
             + ("" if cs["met_target"] else " Lohkon sisäiset erot ovat lähellä mittauskohinaa.")),
            (f"One {size:g} m cell is uncertain by about ±{se:.2f} t/ha.{wtxt_en}"
             + ("" if cs["met_target"] else " Within-field differences are close to noise.")),
            affects=["yield_pattern"]))

    products = {}
    names = {"yield_level": ("Satotaso (t/ha)", "Yield level (t/ha)"),
             "yield_pattern": ("Sadon vaihtelu mitatulla alalla", "Yield pattern, measured area"),
             "filled_area": ("Täytetty ala", "Filled area"),
             "moisture": ("Kosteus", "Moisture"),
             "protein": ("Proteiini", "Protein")}
    for key, (fi, en) in names.items():
        rel = [i for i in items if key in i["affects"]]
        if key == "protein" and not q4:
            continue
        if not rel:
            products[key] = {"name": {"fi": fi, "en": en}, "certainty": "medium", "because": []}
            continue
        worst = min(rel, key=lambda i: ORDER[i["certainty"]])
        products[key] = {
            "name": {"fi": fi, "en": en},
            "certainty": worst["certainty"],
            "because": [i["id"] for i in rel if i["certainty"] == worst["certainty"]
                        and i["certainty"] != "high"],
        }
    questions = [
        {"id": i["id"], "ask": i["ask"], "command": i["command"], "certainty": i["certainty"]}
        for i in sorted(items, key=lambda i: ORDER[i["certainty"]]) if i["ask"]
    ]
    return {
        "field_id": str(config.field_id),
        "year": config.year,
        "product_class": qa.get("product_class"),
        "products": products,
        "items": items,
        "questions": questions,
        "counts": {s: sum(i["status"] == s for i in items)
                   for s in ("known", "estimated", "assumed", "missing")},
    }


def write(config, out_dir=None) -> Path:
    out = Path(out_dir or config.output_dir)
    res = assess(config, out)
    path = out / f"{config.field_id}_{config.year}_certainty.json"
    path.write_text(json.dumps(res, indent=2, ensure_ascii=False))
    return path


def text(res: dict, lang: str = "fi") -> str:
    """Plain-text summary: product grades, then the open questions."""
    fi = lang == "fi"
    lab = LABEL_FI if fi else {k: k for k in LABEL_FI}
    lines = [("Varmuus" if fi else "Certainty") + f" – {res['field_id']} / {res['year']}"]
    for p in res["products"].values():
        why = f"  ({', '.join(p['because'])})" if p["because"] else ""
        lines.append(f"  {p['name'][lang]:34s} {lab[p['certainty']]}{why}")
    lines.append("")
    lines.append("Avoimet kysymykset:" if fi else "Open questions:")
    if not res["questions"]:
        lines.append("  –")
    for n, q in enumerate(res["questions"], 1):
        lines.append(f"  {n}. {q['ask'][lang]}")
        if q["command"]:
            lines.append(f"       -> {q['command']}")
    return "\n".join(lines)
