---
name: planet
description: PlanetScope-kuvien haku, tilaus ja lataus Beanstalk Yield Suiteen (yieldsuite planet) sekä SkySat-kustannusarvio. Käytä, kun käyttäjä haluaa Planet-kuvia lohkolle (esim. puintia edeltävä päivä), kun Planet-avain tai -tunnus ei toimi (401), kun pitää arvioida kiintiötä, tilauksen kestoa tai SkySat-kuvien hintaa, tai kun ladatut kuvat lisätään satomallin L5-tasolle.
---

# PlanetScope Beanstalk Yield Suitessa

Planet on maksullinen lähde. Kiintiö kuluu **rajatun alan** mukaan (km²). Kuvia ei luovuteta organisaation
ulkopuolelle; raportteihin vain johdetut kartat ja tunnusluvut. Koodi: `src/yieldsuite/public/planet.py`,
komento `yieldsuite planet`.

## 1. Tunnus ensin – tarkista se ennen kaikkea muuta

- Tarvitaan **vain API Key** (Planet: My Settings → API Key). **Account ID ja User ID eivät käy**;
  niiden syöttäminen on tavallisin syy 401-virheeseen.
- Rajapinnat hyväksyvät eri muodot:
  - Data API (haku, lohkon selkeys): `Authorization: Basic base64("<key>:")` (avain käyttäjätunnuksena, salasana tyhjä)
  - Orders API (tilaus, tila, lataus): `Authorization: api-key <key>`
- **Paikallisesti**: `export PL_API_KEY=<key>`; koodi tekee molemmat muodot itse.
- **Pilvi-istunnossa**: avain tallennetaan verkkosalaisuudeksi `request_credential`-työkalulla
  (host `api.planet.com`). Välityspalvelin lisää yhdelle palvelimelle vain **yhden** muodon:
  - Tilauksia varten: tyyppi bearer, `prefix: Api-Key` (otsake `Authorization: api-key <key>`)
  - Hakua varten: tyyppi basic. Lomake ei välttämättä hyväksy tyhjää salasanaa ("Näitä tietoja ei
    hyväksytty"); silloin käytä api-key-muotoa ja tee haku paikallisesti tai Planet Explorerissa.
  - Älä aseta `PL_API_KEY`:tä pilvessä (tai aseta `proxy-injected`), jotta koodi ei lähetä omaa otsaketta.
- **Testaa ennen ajoa** (401 = väärä avain tai muoto; 403/aikakatkaisu = verkko):
  ```bash
  curl -sS -o /dev/null -w "%{http_code}\n" https://api.planet.com/compute/ops/orders/v2         # api-key
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" \
    -d '{"item_types":["PSScene"],"filter":{"type":"DateRangeFilter","field_name":"acquired","config":{"gte":"2026-08-21T00:00:00Z","lt":"2026-08-21T00:10:00Z"}}}' \
    https://api.planet.com/data/v1/quick-search?_page_size=1                                     # Basic
  ```
  Älä testaa `/data/v1/`-juurella (vastaa 200 ilman tunnusta) äläkä `item-types`/`basemaps`-osoitteilla
  (vastaavat 401 myös toimivalla api-key-tunnuksella).
- Kahden 401:n jälkeen (minuutin odotus välissä) pyydä käyttäjää tarkistamaan avain omalla koneellaan
  yllä olevalla curlilla; älä jatka arvailua.
- Opittua (12.11.2026): tunnistelomake ei hyväksy Basic-muotoa tyhjällä salasanalla ("Näitä tietoja ei
  hyväksytty"). Jos sama 401 tulee sekä haussa että tilauksissa api-key-muodolla, vika on avaimen arvossa
  (väärä kenttä tai eri tilin avain), ei muodossa: tarkista ympäristön verkkosalaisuuksista, että
  `api.planet.com`:lle on vain yksi salaisuus, ja pyydä käyttäjää testaamaan avain paikallisesti.

## 2. Haku (ei kuluta kiintiötä)

```bash
yieldsuite planet search field.yaml                 # päivä ennen ensimmäistä puintipäivää (L3:sta)
yieldsuite planet search field.yaml --day 2026-08-21
```

Tulostaa päivän kuvat järjestettynä **lohkon** selkeyden mukaan (`coverage?mode=estimate`). Kuvan oma
`clear_percent` koskee ~25 × 12 km aluetta ja voi johtaa harhaan (kuva 59 %, lohko 100 %). Valitse kuva,
jossa lohko on ≥ 95 % selkeä ja jolla on SR-aineisto (merkintä `SR`).

## 3. Tilaus (kuluttaa kiintiötä – kysy käyttäjältä ensin)

Arvioi ensin kiintiö: kuvia × lohkon ala (+ 10 m reunus). Esim. 2 kuvaa × 8 ha ≈ 0,17 km².

```bash
yieldsuite planet order field.yaml --items 20260821_094011_12_24c3 --confirm
yieldsuite planet wait --order-id <id>              # 15 s → 120 s välein, enintään 1 h
yieldsuite planet download --order-id <id> --out data/planet/
```

- Tuote `analytic_sr_udm2` (pintaheijastus + UDM2-laatumaski), rajaus lohkoon + 10 m,
  harmonisointi Sentinel-2:een.
- **Lataus**: tulostiedostojen linkit (`api.planet.com/compute/ops/download`) ohjautuvat osoitteeseen
  `link.planet.com`, jonka verkon pitää sallia (pilviympäristössä lisää se sallittuihin osoitteisiin).
- **Harmonisoitu tuote on 4-kaistainen** (sininen, vihreä, punainen, NIR) myös SuperDovesta: NDRE ei
  ole saatavilla; asetuksissa `sensor: dove4`.
- **Kesto**: minuutteja; mitattu 12.11.2026: neljä yhden lohkon tilausta (1–2 kuvaa) 14–15 min. Mitattu: 20 kuvan rajattu ja harmonisoitu tilaus 18,5 min
  (luotu → "Manifest delivery completed"). Yhden lohkon 1–4 kuvaa tyypillisesti 5–15 min.
- Kiintiö näkyy Planetin tilisivulla; loppunut kiintiö estää tilaukset mutta ei hakua.

## 4. Kuvat satomalliin (L5)

Lisää ladatut kuvat kenttätiedostoon ja aja L5 uudelleen (Sentinel-2-piirteet ovat välimuistissa):

```yaml
imagery:
  - kind: planetscope
    path: data/planet/20260821_094011_12_24c3_3B_AnalyticMS_SR_8b_harmonized_clip.tif
    date: 2026-08-21
    sensor: superdove          # 8-kaistainen (PSB.SD); dove4 = 4-kaistainen
    mask: data/planet/20260821_094011_12_24c3_3B_udm2_clip.tif
```

```bash
yieldsuite run field.yaml --to L5 && yieldsuite report field.yaml --redact
```

Harmonisointi Sentinel-2:een ja lähdekohtainen sovitus näkyvät mallikortissa (`sources`, `ablation_cv_rmse`).
Huom. lohkon reuna: 3 m pikselit sekoittavat vähemmän pientareita kuin 10–20 m Sentinel-2, mutta
satokartan ja kuvan yhteys kannattaa silti arvioida myös ilman 20 m reunavyöhykettä (kriittinen arvio).

## SkySat: kustannusarvio ennen tilausta

- **Minimiala 25 km²** tilausta kohden; pienempi alue laskutetaan 25 km²:nä (Planet; Sentinel Hub, UP42 ja
  SkyWatch samoin). Peltolohko (0,05–0,2 km²) maksaa siis käytännössä minimin.
- **Indikatiivinen hinta** (SkyWatch, kesäkuu 2026): 50 cm arkisto noin 10 USD/km², ohjelmointi (tasking)
  noin 12 USD/km² → arkisto noin 250 USD ja tasking noin 300 USD lohko-päivää kohden. Vanhempi Apollo Mapping
  -tiedote mainitsee taskingille 15 000 USD:n minimitilauksen, joten pyydä aina tarjous.
- **Menneille päiville vain arkisto**, ja SkySat-arkisto on harva (satelliitit ohjelmoidaan). Hae ensin
  (`item_types: ["SkySatCollect"]`, ei kuluta kiintiötä); tilaa vain, jos lohko on selkeä.
- Planet-tilauksen SkySat-kiintiö vähennetään samalla minimialalla.
- Suositus: PlanetScope ensin (3 m, sisältyy kiintiöön); SkySat yhdelle koelohko-päivälle, jos haku löytää
  selkeän kuvan.

## Vianetsintä

| Oire | Syy | Korjaus |
|---|---|---|
| 401 kaikkialla | väärä arvo (Account/User ID) tai vanhentunut avain | syötä pelkkä API Key; testaa paikallisesti curlilla |
| 401 haussa, 200 tilauksissa | tallennettu api-key-muoto | haku paikallisesti tai Basic-muoto |
| 200 haussa, 401 tilauksissa | tallennettu Basic-muoto | tilaus paikallisesti `PL_API_KEY`:llä tai api-key-muoto |
| 400 coverage | runko ilman `geometry`-avainta | `{"geometry": <geojson>}` |
| 500 coverage | `mode` muu kuin `estimate` | käytä `mode=estimate` |
| tilaus `failed` | ei latausoikeutta kuvaan / kiintiö | tarkista `_permissions` ja kiintiö |
