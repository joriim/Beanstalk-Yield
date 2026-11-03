# Ohje: työnkulku QGIS, WebODM, openEO ja tiladata-MCP

Tämä ohje kertoo, mitä työkalua käytetään missäkin prosessointitason vaiheessa
(L0–L5 + lohkot), ja kuvaa MCP-palvelimen, jonka kautta maatilan
datavarastoa luetaan ja päivitetään sekä julkisia aineistoja haetaan.

> **Tila (marraskuu 2026).** Toteutettu: ydinkirjasto L1A–L4 (CSV-vienti),
> L5 Sentinel-2 openEO:n kautta, komentorivi, Slack-ilmoitukset.
> Ei vielä toteutettu: MCP-palvelin, WebODM-integraatio, QGIS-algoritmien
> kytkentä, ISOXML-luku. Ne on merkitty alla tunnuksella **[suunnitelma]**.

## 1. Roolijako yhdellä silmäyksellä

| Työkalu | Rooli | Ei käytetä |
|---|---|---|
| **yieldsuite** (Python-ydin) | Kaikki laskenta L0–L5; ainoa paikka, jossa malleja ajetaan | — |
| **QGIS** | Katselu, tarkastus, ihmisen päätökset (viive, liput, hyväksyntä), lohkojen viimeistely | Laskentaa ei tehdä käsin QGISissä, ettei tulos riipu klikkauksista |
| **WebODM** | Drone-kuvista heijastusortokuva (Mavic 3M), DSM | Satodatan käsittely |
| **openEO** (Copernicus Data Space) | Sentinel-2-aikasarjat palvelimella: pilvimaski, indeksit, keskiarvo ruuduittain | Satodata ei koskaan lähde openEO:hon |
| **Tiladata-MCP** **[suunnitelma]** | Yksi rajapinta datavarastoon ja julkisiin hakuihin agenteille ja työkaluille | Ei laske itse; kutsuu ydintä |
| **Slack** (#yield-data-suite) | Ajojen tila, QA-yhteenveto, hyväksyntäpyynnöt | Karttatiedostot eivät mene Slackiin |

Periaate: **laskenta yhdessä paikassa, ihmisen päätökset tallennetaan
parametreiksi.** Kun käyttäjä korjaa QGISissä viiveen, korjaus tallentuu
koneprofiilin uudeksi versioksi ja ketju ajetaan uudelleen – karttaa ei
muokata käsin.

## 2. Vaihe vaiheelta

### L0 – Raakadata talteen

| Mitä | Miten |
|---|---|
| Puimurin lokit (CSV / ISOXML), vaakalaput, drone-kuvat | Tallennetaan muuttumattomina. MCP: `upload_raw` **[suunnitelma]**; nyt: kansio `data/<lohko>/<vuosi>/` |
| QGIS | Ei tarvita |
| WebODM | Drone-kuvien L0 on WebODM-projektin alkuperäiset kuvat; ne säilytetään myös varastossa |
| openEO | Ei käytetä |
| Julkinen data | Peltolohkon raja (Ruokavirasto, peltolohkorekisteri) → `boundary`. MCP: `get_parcel` **[suunnitelma]** |

Tarkistuslista: kuvien ja lokien tarkistussumma, koneen tunnus, sadonkorjuupäivä.

### L1A–L1B – Purku ja massavirta

| Mitä | Miten |
|---|---|
| Laskenta | `yieldsuite run lohko.yaml --to L1B` (CSV-viennit tulevat valmiiksi L1B-tasolla) |
| QGIS | Raakapisteiden nopea silmäily: *Tason lisäys → Erotinmerkein eroteltu teksti*, x/y-sarakkeet, tyyli massavirran mukaan. Etsi: puuttuvat jaksot, GNSS-hypyt, väärät yksiköt |
| MCP | `get_machine_profile`, `update_machine_profile` (header-leveys, antennin siirtymä, kalibrointikäyrä) **[suunnitelma]** |
| Kalibrointikuormat | Vaakalaput CSV:nä (`mass_kg`, `machine_id`, `moisture_pct`); MCP: `add_scale_ticket` **[suunnitelma]** |

### L2 – Viive, sijainti, tehollinen ala

| Mitä | Miten |
|---|---|
| Laskenta | `--to L2`: massavirta siirretään viiveellä τ_d, kosteus τ_m:llä, antenni → leikkuupöytä, päällekkäisyys poistetaan |
| QGIS – viiveen tarkastus | Avaa `L3_..._records.parquet` (GeoParquet, QGIS 3.28+ GDAL Arrow -tuella). Tyylitä `yield` jatkuvalla väriasteikolla ja zoomaa kaistanpäihin. **Oikea viive:** vierekkäiset, vastakkaisiin suuntiin ajetut kaistat näyttävät saman kuvion. **Väärä viive:** kuvio "sahaa" kaistojen välillä |
| Päätös | Uusi viive kirjataan koneprofiiliin (`tau_d_s`), ei karttaan. MCP: `update_machine_profile` luo uuden version ja käynnistää uudelleenajon **[suunnitelma]** |
| Julkinen data | — |

### L3 – Kalibrointi vaakaan ja liputus

| Mitä | Miten |
|---|---|
| Laskenta | `--to L3`: liput (leikkuupöytä ylhäällä, kaistan päät, nopeus, kosteus, leveys, GNSS, poikkeamat) ja kerroin k konekohtaisesti |
| QGIS – lippujen tarkastus | Tyylitä `flags` luokiteltuna (0 = puhdas). Tarkista, että liputettu osuus on järkevä (tyypillisesti 20–40 %, synteettisellä pellolla ~10 %) ja että poikkeamat eivät kasaudu yhteen kohtaan (silloin kyse voi olla todellisesta ilmiöstä) |
| QA | `L4_..._qa.json`: k-kertoimet, lippujen määrät, varoitukset. Slack-viesti näyttää samat luvut |
| Päätös | Hyväksy k tai pyydä lisää vaakalappuja. MCP: `approve_qa` **[suunnitelma]**; Slack-painike vaiheessa 2 |
| Julkinen data | Peltolohkon raja reunapuskurin liputukseen (`FIELD_EDGE`) **[suunnitelma]** |

### L4 – Ruudukko

| Mitä | Miten |
|---|---|
| Laskenta | `--to L4`: massa jaetaan kiinteään ruudukkoon (EPSG:3067 tai UTM 35N, 20 m), massan säilyminen tarkistetaan |
| QGIS | Avaa `L4_..._yield.tif`. Kaistat: `yield`, `coverage`, `n_records`, `spread`, `flagged_share`, `passes`. Näytä `yield` vain, kun `coverage` ≥ 0,5 (Raster Calculator tai tyylin läpinäkyvyys toisen kaistan mukaan) |
| MCP | `get_product(lohko, vuosi, taso="L4")`, `list_products` **[suunnitelma]** |

### L5 – Fuusio: Sentinel-2 ja drone

**Sentinel-2 (openEO) – toteutettu**

| Mitä | Miten |
|---|---|
| Haku | Ensin katsotaan, mitä pilvettömiä päiviä on: Copernicus Data Space STAC -haku lohkon alueelle ja kasvukaudelle. MCP: `search_sentinel2` **[suunnitelma]**; QGISissä STAC-selain (QGIS 3.40+ tai STAC API Browser -lisäosa) |
| Laskenta | `yieldsuite fuse lohko.yaml --l4 L4.tif` tai `run --to L5`. openEO-palvelimella: pilvimaski SCL:llä, NDVI/NDRE/CIre/NDMI pikseleittäin, keskiarvo ruuduittain. Paikallisesti: malli ja aukkojen täyttö |
| QGIS | Vertaa `prediction`- ja `yield`-kaistoja; `source` = 2 näyttää täytetyt solut; `sigma` niiden epävarmuuden |
| Tunnukset | Ilmainen Copernicus Data Space -tili; ensimmäisellä ajolla kirjautuminen selaimessa |

**Drone (WebODM) – [suunnitelma]**

1. **Lento:** Mavic 3M, RTK päällä, auringonvalosensori, 70–80 % päällekkäisyys. Ajoita samaan ikkunaan kuin hyvät Sentinel-2-päivät. Kalibrointipaneeli ennen ja jälkeen lennon, jos käytössä.
2. **WebODM-tehtävä** monikanavakuvista. Keskeiset asetukset: `radiometric-calibration: camera+sun`, `orthophoto-resolution` esim. 5 cm, `dsm: true`. Tarkista käyttämäsi ODM-version Mavic 3M -tuki ja asetusten nimet ODM:n dokumentaatiosta.
3. **Tuonti varastoon:** heijastusortokuva (G, R, RE, NIR) ja DSM. MCP: `webodm_create_task`, `webodm_task_status`, `import_orthomosaic` **[suunnitelma]**.
4. **Yhdenmukaistus Sentinel-2:een:** ortokuva aggregoidaan samaan 20 m ruudukkoon pinta-alapainotetulla keskiarvolla, ja kanavakohtainen lineaarinen sovitus tehdään samanaikaiseen Sentinel-2-kuvaan. Kaistavastaavuus: G 560 → B03, R 650 → B04, NIR 860 → B8A. RE 730 on B05:n (705) ja B06:n (740) välissä, joten red edge -indeksit eivät ole suoraan vertailukelpoisia ilman sovitusta.
5. **Käyttö:** drone-indeksit lisäpiirteiksi samaan malliin; 20 m ruudukossa lisäarvo on lähinnä pilvisen kauden paikkaamisessa ja ajankohdan tarkkuudessa. Tarkempi (esim. 5 m) drone-ruudukko vaatii erillisen L4-ajon pienemmällä solukoolla.

### Lohkot (monivuotinen) – [suunnitelma]

| Mitä | Miten |
|---|---|
| Laskenta | Usean vuoden L5-kartat suhteelliseksi sadoksi, pinottuna kuvahistorian, korkeusmallin ja maaperätiedon kanssa → KM-sPC-klusterointi |
| Julkinen data | Maanmittauslaitoksen korkeusmalli 2 m (rinne, kosteusindeksi), GTK:n maaperäkartta, Ilmatieteen laitoksen säädata (lämpösumma, sademäärä vuosien vertailuun). MCP: `get_elevation`, `get_soil_map`, `get_weather` |
| QGIS | Lohkorajojen viimeistely ja yhdistely, levityskarttojen pohja |

## 3. Tiladata-MCP-palvelin [suunnitelma]

### Miksi

Sama data tarvitaan monessa paikassa: QGISissä, komentorivillä, Slackissa ja
agenteilla (esim. Claude, joka kokoaa QA-yhteenvedon tai hakee pilvettömät
päivät). MCP-palvelin antaa yhden, oikeuksiltaan rajatun rajapinnan, jotta
kenenkään ei tarvitse tuntea tallennusrakennetta tai rajapinta-avaimia.

### Arkkitehtuuri

```
 Claude / agentit ──┐
 QGIS (myöhemmin) ──┼──►  tiladata-MCP  ──►  yieldsuite-ydin (laskenta)
 Slack-botti ───────┘          │
                               ├──► Tilan datavarasto
                               │      paikallinen: kansio + GeoPackage + staattinen STAC
                               │      palvelin:    objektivarasto + PostGIS + pgSTAC
                               ├──► WebODM REST API (drone-tehtävät)
                               ├──► openEO / CDSE STAC (Sentinel)
                               └──► Julkiset suomalaiset rajapinnat (MML, Ruokavirasto, GTK, IL)
```

Aloitus paikallisena (stdio-MCP, Python MCP SDK) samaan repoon
`server/mcp/`-kansioon. Palvelinversio käyttää samoja työkaluja
PostGIS/pgSTAC-taustalla.

### Työkalut

**Datavarasto – luku**

| Työkalu | Palauttaa |
|---|---|
| `list_fields(farm_id)` | Lohkot, rajat, vuodet joilta dataa on |
| `list_products(field_id, year?, level?)` | STAC-tuotteet: taso, tuoteluokka (absolute / relative), parametrihash, luontiaika |
| `get_product(product_id)` | Polku/URL, kaistat, QA-yhteenveto tai mallikortti |
| `get_qa(field_id, year)` | QA-raportti: k, liput, massan säilyminen, varoitukset |
| `get_machine_profile(machine_id, version?)` | Viiveet, offsetit, kalibrointi; versiohistoria |

**Datavarasto – päivitys** (vaatii vahvistuksen, kirjataan auditlokiin)

| Työkalu | Tekee |
|---|---|
| `upload_raw(field_id, year, kind, file)` | Tallentaa L0:n muuttumattomana, laskee tarkistussumman |
| `add_scale_ticket(field_id, year, machine_id, mass_kg, moisture_pct)` | Lisää vaakalapun |
| `update_machine_profile(machine_id, changes, reason)` | Luo **uuden version**, ei ylikirjoita |
| `run_chain(field_id, year, to_level)` | Ajaa ytimen, palauttaa ajon yhteenvedon, postaa Slackiin |
| `approve_qa(field_id, year, decision, note)` | Kirjaa ihmisen hyväksynnän |

**Julkiset haut** (vain luku, tulokset välimuistiin alkuperätietoineen)

| Työkalu | Lähde | Käyttö |
|---|---|---|
| `search_sentinel2(field_id, start, end, max_cloud)` | Copernicus Data Space STAC | Pilvettömät päivät ennen openEO-ajoa |
| `fetch_s2_features(field_id, year)` | openEO (toteutettu ytimessä) | L5-piirteet |
| `get_parcel(parcel_id \| bbox)` | Ruokavirasto, peltolohkorekisteri (avoin data, WFS) | Lohkorajat |
| `get_elevation(field_id)` | Maanmittauslaitos, korkeusmalli 2 m (avoin, API-avain) | Lohkot, maaston kovariaatit |
| `get_soil_map(field_id)` | GTK, maaperäkartta | Lohkot |
| `get_weather(field_id, start, end)` | Ilmatieteen laitos, avoin data | Lämpösumma, sade, ajoitus |
| `webodm_create_task`, `webodm_task_status`, `import_orthomosaic` | Oma WebODM-instanssi | Drone-ortokuvat |

Rajapintojen osoitteet, käyttöehdot ja avainten tarve tarkistetaan
toteutusvaiheessa; ne muuttuvat useammin kuin tämä ohje.

### Säännöt

1. **Tilan data kuuluu viljelijälle.** Jokainen kutsu on rajattu tilaan, jolle käyttäjällä on oikeus. Satodataa ei lähetetä julkisiin palveluihin; openEO:lle menee vain ruudukko.
2. **L0 on muuttumaton.** Päivitystyökalut lisäävät tai versioivat, eivät poista.
3. **Ihmisen päätös on parametri.** Viive, k ja hyväksynnät tallentuvat versioituina, ja jokainen tuote kertoo, millä versiolla se on tehty.
4. **Kirjoittavat työkalut vaativat vahvistuksen** ja kirjataan auditlokiin (kuka, milloin, miksi).
5. **Salaisuudet ympäristömuuttujissa:** CDSE-kirjautuminen, MML-avain, WebODM-token, Slack-token. Ei koskaan repoon.
6. **Julkisen datan alkuperä tallennetaan:** lähde, hakuaika, versio. Tuote on toistettavissa.

## 4. Toteutusjärjestys

1. MCP:n lukutyökalut paikallista kansiovarastoa vasten (`list_products`, `get_qa`, `get_product`).
2. `run_chain` + Slack-yhteenveto + `update_machine_profile` (viivekorjauksen kierros QGIS → profiili → uudelleenajo).
3. `search_sentinel2` ja `get_parcel`.
4. WebODM-työkalut ja drone-yhdenmukaistus L5:een.
5. MML-, GTK- ja IL-haut lohkojaon tueksi.
6. QGIS-lisäosan algoritmit kutsumaan samaa ydintä (ja tarvittaessa MCP:tä).
