# Suunnitelma: Datakasvukunto, GrainSense Combine, Mavic 3M, Sentinel-2 ja PlanetScope

Miten viisi datalähdettä ajetaan Yield Data Suiten läpi: mikä on valmista, mitä
pitää rakentaa ja mitä tarvitaan lähtötiedoiksi.

> Tila marraskuu 2026. Merkinnät: **valmis** = toteutettu ja testattu
> synteettisellä datalla; **rakennettava** = suunniteltu tässä; **tarvitaan**
> = esimerkkidata tai tieto, jota ilman toteutusta ei voi varmistaa.

## 1. Lähteet yhdellä silmäyksellä

| Lähde | Mittaa | Erotuskyky | Muoto | Tulee ketjuun | Tila |
|---|---|---|---|---|---|
| **Datakasvukunnon satokartoitusdata** (tilojen puimurit, eri merkit) | Sadon määrä: massavirta tai satopisteet, kosteus, leveys, sijainti | ~1 s / 1–2 m kaistalla | ISOXML TaskData, valmistajien shapefile/CSV-viennit | L1B tai L2 → L3 → L4 | Tuonti, ketju ja viiveen arvio **valmis**; oikean datan validointi **tarvitaan** |
| **GrainSense Combine** | Sadon laatu: proteiini (kuiva-aineesta), kosteus, öljy | puinnin aikana, sijainnillinen mittaus | Pilvipalvelu; vientimuoto **tarvitaan** (käsimittarin dashboard: CSV/XLS) | Uusi laatukanava Q1–Q4 | **Rakennettava** |
| **Drone, DJI Mavic 3M** | Heijastus: G 560, R 650, RE 730, NIR 860 nm (+ RGB) | 2–5 cm, lento kerrallaan | Raakakuvat → WebODM → heijastusortokuva | L5-piirteet (20 m) ja tarkka 5 m -taso | Ohje **valmis**; WebODM-integraatio ja yhdenmukaistus **rakennettava** |
| **Sentinel-2 L2A** | Heijastus 13 kanavaa | 10–20 m, 2–5 pv (pilvet) | openEO / STAC, Copernicus Data Space | L5-piirteet, RUE | **Valmis** (oikeaa rajapintaa vasten testaamatta) |
| **PlanetScope (SuperDove)** | Heijastus 8 kanavaa | 3 m, lähes päivittäin | Planet API / Sentinel Hub, kaupallinen lisenssi | L5-piirteet aukkojen ja ajoituksen paikkaajana | **Rakennettava**; käyttöoikeus **tarvitaan** |

Periaate pysyy samana kaikille lähteille: jokainen tulee ketjuun sillä tasolla,
jolla sitä tarvitaan; jokaisella on alkuperätieto, yksiköt ja epävarmuus; ja
kaukokartoitus korjaa ja täydentää mittausta, mutta ei korvaa sitä.

## 2. Datakasvukunnon satokartoitusdata (puimurit)

**Tavoite:** monen tilan, eri merkkisten puimureiden satodata samaan, vertailukelpoiseen muotoon.

| Vaihe | Toteutus | Tila |
|---|---|---|
| Vastaanotto | Tila lataa viennin (ISOXML-zip, shapefile, CSV) → `register_harvest_log` (MCP) tai `yieldsuite inspect` + `field.yaml` | valmis |
| Tunnistus | Sarakkeet, yksiköt ja niiden lähde raportoidaan; epäselvä yksikkö pysäyttää | valmis |
| Massavirta vs. satopisteet | Massavirta → L1B (viive korjataan), satopisteet → L2 (massa = sato × matka × leveys) | valmis |
| Koneprofiili | Yksi profiili per puimuri ja kausi: header-leveys, antennin paikka, viive. Viive arvioidaan datasta (`yieldsuite delay`) ja kuitataan profiiliin | valmis |
| Kalibrointi | Vaakalaput tai siilo-/kuivurikirjanpito → kerroin k per kone; ilman niitä tuoteluokka `absolute_unreconciled` | valmis |
| Lohkoraja | Peltolohkorekisteri tai tilan oma raja → `FIELD_EDGE` | valmis (WFS-osoite **tarvitaan**) |
| Validointi oikealla datalla | Ensimmäiset 3–5 tilaa: viennin summat vs. ketjun summat, viivearvio vs. valmistajan asetus, liputusosuudet | **tarvitaan** |

Huomioita:
- Eri merkkien viennit poikkeavat toisistaan: osa sisältää massavirran ja ajan, osa vain satopisteet, joissa viive on jo korjattu. Jokaisesta merkistä ja ohjelmistoversiosta kannattaa tallentaa yksi hyväksytty `harvest_logs`-esimerkki (sarakkeet ja yksiköt), jolloin seuraavat tilat menevät läpi ilman käsityötä.
- ISOXML-lukija on kokeellinen: ensimmäinen oikea TaskData verrataan valmistajan ohjelman summiin.

## 3. GrainSense Combine: laatukanava

Laatumittaus ei sovi suoraan nykyiseen ketjuun, koska proteiini ei ole massaa:
sitä ei jaeta pinta-alalle vaan painotetaan massalla. Rakennetaan rinnakkainen
**laatukanava** samoilla periaatteilla.

| Taso | Sisältö | Malli |
|---|---|---|
| **Q1** | Mittaukset SI-yksiköissä: aika, sijainti, proteiini (% kuiva-aineesta), kosteus (% märkäpainosta), öljy | Tuonti samalla kehyksellä kuin satodata (sarake- ja yksikkötunnistus) |
| **Q2** | Mittaus sijoitettuna leikkauskohtaan | Oma viive τ_q (anturin paikka puimurissa), arvioidaan kuten τ_d vastakkaisista kaistoista; mittaus liitetään lähimpiin satotietueisiin ajan perusteella |
| **Q3** | Liputettu ja kalibroitu | Liput: kosteus anturin alueen ulkopuolella, leikkuupöytä ylhäällä, poikkeamat. **Kalibrointi laboratorionäytteisiin** (kuten vaakalaput massalle): bias-korjaus näytteiden perusteella; ilman näytteitä tuoteluokka `relative` |
| **Q4** | Ruudukko | Massalla painotettu keskiarvo solussa: p_g = Σ m_i p_i / Σ m_i (massat L3:sta). Lisäksi **proteiinisato** kg/ha = sato × (1 − kosteus) × proteiini ja **typen otto** ≈ proteiinisato / kerroin (vehnä 5,7; ohra 6,25) |

Käyttö: typpitase lohkoittain (typen otto vs. lannoitus), lohkojen
luokittelu sadon ja laadun mukaan, sekä L5:ssä proteiinin ja kuvien
(red edge -indeksit) välinen malli.

**Tarvitaan:** yksi GS Combine -vienti (tai API-kuvaus): sisältääkö se
sijainnin ja ajan per mittaus, mittaustiheys, yksiköt ja kalibrointitiedot.
Ilman sitä Q1:n tuontia ei voi toteuttaa luotettavasti.

## 4. Drone: DJI Mavic 3M

| Vaihe | Toteutus | Tila |
|---|---|---|
| Lento | RTK, auringonvalosensori, 70–80 % päällekkäisyys, kalibrointipaneeli ennen ja jälkeen; ajoitus samaan ikkunaan hyvien Sentinel-2-päivien kanssa | ohje valmis |
| Raakakuvat L0:ksi | Kuvat zip-pakettina `upload_raw kind=drone_images`; suurille lennoille objektivarasto | valmis (kansiovarasto) |
| Ortokuva | WebODM: monikanavakuvat, radiometrinen kalibrointi (kamera + aurinko), heijastusortokuva + DSM. MCP-työkalut `webodm_create_task`, `webodm_task_status`, `import_orthomosaic` | **rakennettava** |
| Piirteet 20 m | Pinta-alapainotettu keskiarvo suiten ruudukkoon → samaan pitkään piirretaulukkoon kuin Sentinel-2 (`source = drone`) | **rakennettava** |
| Yhdenmukaistus | Kanavittainen lineaarinen sovitus samanaikaiseen Sentinel-2-kuvaan: G→B03, R→B04, NIR→B8A. RE 730 nm on B05:n (705) ja B06:n (740) välissä → red edge -indeksit lasketaan droneille omina piirteinään, ei suoraan vertailukelpoisina | **rakennettava** |
| Tarkka taso 5 m | Drone-kuvasta suhteellinen kasvustokartta 5 m ruudukossa (sama origo); L4-sato samassa ruudukossa vain RTK-datalla ja lyhyellä viiveellä | **rakennettava** |

Drone-lennon arvo on ajoituksessa ja tarkkuudessa: se paikkaa pilvisen kauden
ja näyttää kaistan sisäiset erot. 20 m ruudukossa se on yksi piirrelähde lisää.

## 5. Sentinel-2

Valmis ketju: `search_sentinel2` → openEO (SCL-pilvimaski, indeksit
pikseleittäin, keskiarvo ruuduittain) → piirteet → malli, aukkojen täyttö,
raidat, jäännössiirtymä → RUE ja lohkot.

Laajennettava monilähteiseksi: piirretaulukkoon lisätään sarake `source`
(sentinel2 / planetscope / drone) ja Sentinel-2 toimii **vertailutasona**,
johon muut lähteet yhdenmukaistetaan. Grid kannattaa asettaa UTM 35N:ään origolla
(0, 0), jolloin 20 m solut osuvat Sentinel-2:n pikseleihin.

## 6. PlanetScope

| Vaihe | Toteutus | Tila |
|---|---|---|
| Käyttöoikeus | Kaupallinen (Planet API tai Sentinel Hub); tutkimuskäyttöön mahdollisesti Copernicus Contributing Missions tai Planetin tutkimusohjelmat. Lisenssi kirjataan tuotteen alkuperätietoon; raakakuvia ei jaeta eteenpäin | **tarvitaan** |
| Haku ja lataus | 8-kanavainen pintaheijastus (SR) lohkon rajauksella, UDM2-pilvimaski | **rakennettava** |
| Kanavat | Coastal Blue, Blue, Green, Red, Red Edge ja NIR vastaavat Sentinel-2:n kanavia 1–5 ja 8a; Green I ja Yellow ilman vastinetta | — |
| Yhdenmukaistus | Planetin harmonisointi kattaa käytännössä Blue, Green, Red ja NIR. Siksi lisäksi oma kanavittainen sovitus samanpäiväiseen Sentinel-2-kuvaan; red edge tarkistetaan erikseen | **rakennettava** |
| Piirteet | Pinta-alapainotettu keskiarvo 20 m ruudukkoon (`source = planetscope`); 3 m data mahdollistaa myös 5 m tason | **rakennettava** |
| Käyttö | (1) Pilvisten jaksojen paikkaus aikasarjassa, (2) kasvuvaiheen tarkka ajoitus (lähes päivittäinen), (3) RUE:n fAPAR-käyrä tiheämmäksi | **rakennettava** |

## 7. Fuusio: monta lähdettä yhdessä mallissa

1. **Yhteinen piirretaulukko** `cell, date, index, value, source`.
2. **Yhdenmukaistus Sentinel-2:een**: jokaiselle lähteelle ja kanavalle lineaarinen sovitus päivinä, jolloin molemmilla on pilvetön havainto (±2 päivää). Sovituksen R² ja jäännös kirjataan mallikorttiin; heikko sovitus pudottaa lähteen piirteistä.
3. **Aikasarja**: päivittäiset arvot yhdistetään lähteiden yli (yhdenmukaistettuna), jolloin kauden keskiarvo ja maksimi lasketaan tiheämmästä sarjasta.
4. **Malli ja validointi** kuten nyt (lineaarinen tai random forest, spatiaalinen ristiinvalidointi), mutta mallikortti kertoo lähteittäin, paljonko kukin paransi (ablaatio: ilman dronea, ilman PlanetScopea).
5. **RUE ja lohkot** käyttävät tiheämpää fAPAR-sarjaa automaattisesti.

## 8. Datakasvukunto: monen lohkon mallit ja datamarkkina

Hankkeen tavoite (biomassa-arvioiden kalibrointi kaikille lohkoille ja
satokartoitusdata markkinalle) menee yhden lohkon ketjua pidemmälle:

| Tarve | Ratkaisu | Tila |
|---|---|---|
| Monen tilan data samaan varastoon | Tiladata-MCP:n varasto, tilakohtainen käyttöoikeus, auditloki | valmis (kansiomuoto); palvelinversio **rakennettava** |
| Lohkojen välinen malli (myös lohkoille, joilta satodataa ei ole) | Yhdistetty malli L5-piirteistä + sää (ERA5: PAR, lämpösumma, vesitase) + maaperä; validointi **jättämällä kokonainen lohko pois** (ei satunnaisia soluja); uusi tuoteluokka `modelled` erotukseksi mitatusta | **rakennettava** |
| Biomassan kalibrointi | Puimurin L4/L5-sato ja laatu (Q4) opetusdatana, kuvat ja sää selittäjinä; kalibroinnin epävarmuus solukohtaisena | **rakennettava** |
| Datamarkkina | Jokainen tuote STAC-itemina: taso, tuoteluokka, QA, lisenssi, alkuperä; viljelijän suostumus tallennettuna ja tuotteeseen linkitettynä; jakoon pseudonymisoidut lohkotunnukset | **rakennettava** |

Suunnitteludokumentin periaate säilyy: yhden lohkon kartta korjataan vain saman
lohkon ja vuoden datalla. Lohkojen välinen malli on erillinen tuote omalla
luokallaan, ei osa L5:tä.

## 9. Toteutusjärjestys

1. **Oikean datan validointi**: 3–5 Datakasvukunnon tilan puimuriviennit (eri merkit), vaakalaput ja lohkorajat → hyväksytyt `harvest_logs`-esimerkit merkeittäin, viive- ja raitakynnysten tarkistus.
2. **GrainSense-laatukanava Q1–Q4** heti, kun esimerkkivienti on saatavilla.
3. **Monilähteinen piirretaulukko ja yhdenmukaistus** (`source`-sarake, sovitus Sentinel-2:een).
4. **Drone**: WebODM-työkalut, ortokuvan tuonti, 20 m piirteet, 5 m taso.
5. **PlanetScope**: haku ja lataus käyttöoikeuden mukaan, UDM2, yhdenmukaistus.
6. **Lohkojen välinen malli ja datamarkkinan metatiedot** (STAC, suostumus, lisenssi).

## 10. Tarvitaan lähtötiedoiksi

- GrainSense Combine: esimerkkivienti tai API-dokumentaatio (aika, sijainti, yksiköt, mittaustiheys) ja tieto laboratoriovertailuista.
- Datakasvukunto: muutaman tilan puimuriviennit eri merkeistä, vaakalaput tai varastokirjanpito, lohkorajat sekä tieto siitä, millä ehdoilla dataa saa käsitellä ja jakaa.
- Mavic 3M: yksi lento raakakuvineen ja paneelikuvineen sekä WebODM-instanssin osoite ja tunnus.
- PlanetScope: käyttöoikeuden muoto (Planet API, Sentinel Hub vai tutkimusohjelma).
- Peltolohkorekisterin WFS-osoite, tasonimi ja tunnussarake.

## Lähteet

- Datakasvukunto: [Maaseutuverkosto – Datakasvukunto](https://maaseutuverkosto.fi/hankkeet/datakasvukunto/), [SeAMK – Pellon data käyttöön](https://www.seamk.fi/tapahtuma/pellon-data-kayttoon/)
- GrainSense: [GS Combine Analyzer](https://grainsense.com/blogs/news/boosting-farm-efficiency-with-real-time-data-the-gs-combine-analyzer), [Analyzer A-2 -käyttöohje](https://elevateag.com/wp-content/uploads/2023/06/GS-Analyzer-A-2-User-Manual-2022.pdf)
- PlanetScope: [Planet – PlanetScope](https://docs.planet.com/data/imagery/planetscope/), [Planet Community – harmonized bands](https://community.planet.com/planetscope-76/planetscope-harmonized-bands-with-sentinel-2-469), [Sentinel Hub – PlanetScope](https://docs.sentinel-hub.com/api/latest/data/planet/planet-scope/)
