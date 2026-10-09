# Rit & Tank 33.07

De ritregistratie is in release 33.00 vereenvoudigd voor iPhone en iPad: de fysieke kilometerteller staat centraal in een groot, rustig invoerveld, gevolgd door grote locatieknoppen. De fysieke teller blijft altijd de definitieve bron voor zakelijke kilometers; GPS blijft uitsluitend aanvullende informatie, controle en waarschuwing.

## Vereenvoudigde mobiele ritregistratie (33.00)

- Start met één groot fysiek tellerinvoerveld. De laatst bekende stand is alleen een voorstel en wordt pas betrouwbaar na de expliciete knop **✓ Startstand bevestigen**.
- Nederlandse notatie blijft ondersteund: `25.230` en `25230` betekenen beide 25230 km; alleen hele kilometers worden opgeslagen.
- Na bevestiging verschijnen **🏠 Thuis**, **🏫 Beatrixschool**, **📍 Gebruik huidige locatie** en de bestaande Google Places-adreszoeker.
- Bij afsluiten staan startstand, grote werkelijke eindstand en de definitieve fysieke afstand centraal. De server berekent uitsluitend `eindstand − startstand` / de som van fysiek bevestigde etappes.
- GPS-resultaten staan onder **GPS-informatie bekijken**. Bestaande afwijkingscontroles en de sessiegebonden eenmalige tweestapsbevestiging blijven behouden.
- Een succesvolle ritafsluiting toont **✓ Rit vastgelegd!** met locaties, tellerstanden, definitieve afstand en datum/tijd.
- Mobiele bediening gebruikt grote aanraakvlakken, numerieke invoer, automatische selectie van de voorgestelde stand, toetsenbordveilige vaste actieknoppen en layouts zonder horizontale overflow.
- Tussenstops gebruiken iedere fysiek bevestigde tellerstand; dashboard, historie, vergoeding, PDF en CSV blijven gebaseerd op dezelfde opgeslagen etappes zonder dubbele optelling.
- Bestaande adressen en veiligheidsregels blijven intact, inclusief Thuis, Beatrixschool, release-29-adresprioriteit, release-30-resetcontroles, release-31-GPS/tellercontrole en release-32-snelknoppen.
- Historische ritten en tellerstanden worden niet automatisch gewijzigd of herberekend.

De snelkeuze **🏫 Beatrixschool** gebruikt exact **Van Broekhuizenstraat 4, 7461 VW Rijssen** en staat naast **🏠 Thuis** bij de gedeelde locatiekeuze voor ritstart, tussenstop en ritafsluiting. Beide vaste adressen gebruiken dezelfde bestaande adresvalidatie en Google Places-fallback. De GPS-knop blijft over de volle breedte eronder staan. Een locatiekeuze slaat niets zelfstandig op; de fysieke kilometerteller, zakelijke-rittenregels, kilometervergoeding en historische gegevens blijven ongewijzigd.

## Beatrixschool-snelkeuze (32.00)

- **🏠 Thuis** en **🏫 Beatrixschool** staan als twee gelijkwaardige knoppen op de eerste rij; **📍 Gebruik huidige locatie** staat op de tweede rij over de volle breedte.
- Beatrixschool bevestigt canoniek `Van Broekhuizenstraat 4, 7461 VW Rijssen` via dezelfde vaste-locatieketen als Thuis, zonder aparte opslag- of navigatielogica.
- Een nieuwe vaste keuze maakt een eerdere GPS-/Places-aanvraag ongeldig; vertraagde antwoorden mogen de bewuste keuze niet terug overschrijven.
- Het bevestigde `manual_label` blijft leidend voor historie, rapportcontrole, PDF en CSV, inclusief de release-29-bescherming tegen adresverwisseling.
- Er is geen automatische ritopslag, ritclassificatie, wijziging van fysieke kilometerstanden, GPS-afstand of vergoeding.

De fysieke kilometerteller is bij ritafsluiting de definitieve bron voor de geregistreerde afstand. GPS wordt gebruikt als hulpmiddel en afwijkingscontrole.

## Fysieke tellercontrole (31.00)

- Bevestig bij vertrek en iedere stop de werkelijke teller in hele kilometers: `64375` en `64.375` betekenen beide 64375 km. Fractionele kilometers worden geweigerd.
- GPS gemeten, voorgestelde eindstand en werkelijke teller zijn gescheiden. Een adres-/routeantwoord verandert de fysieke invoer niet.
- Bij afsluiten berekent de server de afstand uit de opgeslagen startteller en bevestigde eindteller. 64334 → 64375 geeft 41 km, ook als GPS 36,4 km aangeeft.
- De afwijkingsmodal verschijnt bij **meer dan 2 km EN meer dan 5%** verschil. De relatieve afwijking is `abs(tellerafstand - GPS) / max(tellerafstand, GPS)`.
- Ontbrekende, onderbroken of onvoldoende GPS vraagt afzonderlijk aandacht. Ontbrekende GPS is geen nulmeting. De fysieke teller blijft leidend.
- De tweede bevestiging geldt 10 minuten, is sessiegebonden en geldt alleen voor dezelfde invoer en ongewijzigde administratie. Annuleren vóór definitief verzenden slaat niets op; een reeds verzonden definitieve opslag is niet terug te draaien via Annuleren.
- Tussenstops bewaren afzonderlijke GPS-controles; ritafsluiting vergelijkt de volledige rit. Bij ontbrekende oudere GPS blijft het totaal onbekend.
- Controlewaarden en bevestiging worden duurzaam in `audit_log.details` opgeslagen. Geen nieuwe tabellen/kolommen, historische herberekening of volledige GPS-puntenroute.
- De centrale settings `distance_warning_km` (standaard 2) en `distance_warning_fraction` (standaard 0.05) zijn via de bestaande geautoriseerde settings-API configureerbaar. Beide voorwaarden moeten gelden. Geen vaste kilometeropslag of nieuwe kalibratie.
- Bestaande CSV-compatibiliteit blijft behouden: rittotalen/ritvergoeding worden per stop herhaald; tel `segment_km` op of dedupliceer op `rit_id`. PDF gebruikt etappes.


Release 30.00 voegt **Nieuwe administratie starten** toe onder Instellingen → Administratie. Een exacte RESET-bevestiging, actuele fysieke tellerstand, beveiligd bevestigingstoken en private lokale back-up zijn verplicht vóór het wissen. Instellingen en bekende locaties blijven behouden. De adrescorrecties uit release 29.00 blijven intact.

## Nieuwe administratie starten (30.00)

1. Open **Instellingen → Administratie → Nieuwe administratie starten**.
2. Lees de waarschuwing en typ exact **RESET**. Spaties of kleine letters worden geweigerd.
3. Vul de **huidige kilometerstand van de auto** in. Bijvoorbeeld `64.603` (= 64603 km); voer hele kilometers in, net als op de bestaande tellerwielen. Het bereik sluit aan op de bestaande zes tellerwielen: 0 t/m 999999 km.
4. Controleer de weergegeven tellerstand en kies **Administratie definitief wissen**. Annuleren is mogelijk tot het definitieve verzoek; een al verzonden reset kan niet ongedaan worden gemaakt via de app.
5. De app herlaadt: geen oude ritten/tankbeurten, gereden sinds start administratie **0 km**, fysieke tellerstand bijvoorbeeld **64.603 km**. De eerste echte rit start op deze tellerstand.

### Datamodel en resetgrenzen

De kilometerteller is een **fysieke odometer**, geen interne cumulatieve teller. `events.odometer` en `trip_stops.odometer` slaan echte standen op. `rows_events()` berekent verschillen; ritafstanden komen uit de stopstanden. Er wordt geen bestaande teller kunstmatig naar nul teruggezet.

| Opslag | Gedrag bij reset |
| --- | --- |
| `business_trips`, `trip_stops` | Alle actieve en afgesloten ritten, tussenstops en adrescorrecties verwijderen |
| `events` | Alle oude kilometerregistraties en tankbeurten verwijderen; één nieuwe fysieke baseline aanmaken |
| `audit_log` | Oude snapshots verwijderen, ook adres-/locatiedetails; één minimale startgebeurtenis met tijd en baseline |
| `assistant_arrivals` | Oude aankomsten, correcties, notificatiestatus en ritverwijzingen verwijderen |
| `route_memory`, `distance_calibration` | Uit testadministratie geleerd gedrag/afstanden verwijderen |
| `assistant_state` | GPS-voortgang, voorstellen, meldingen en diagnoselog verwijderen; technische `backup_status` behouden |
| `report_addresses`, Google-geheugencaches | Afgeleide adrescaches leegmaken |
| `receipts/` | Oude tankbonbestanden uit actieve opslag naar de private herstelback-up verplaatsen |
| Rapporten, statistieken, PDF en CSV | Worden opnieuw opgebouwd uit de database; geen lokale rapportarchieftabel of exportbestanden |
| `settings` | Voertuig, kenteken, bestuurder, tarief, voorkeuren en assistentconfiguratie behouden; reset-id/starttijd/baseline in bestaande key/value-tabel |
| `known_places` | Bewust ingerichte locaties, thuisadres en HA-zonekoppelingen behouden |
| `/data/options.json`, omgevingsvariabelen, sessiesleutel | Ongewijzigd; Google Places, Home Assistant, Drive, PWA en secrets blijven behouden |

De baseline is geen rit of tankbeurt en telt niet mee in historie/aantallen. Hij blijft beschikbaar voor odometerberekeningen en de algemene auto-CSV. De starttijd sluit aan op de minuutprecisie van de bestaande invoerformulieren. Nieuwe registraties vóór de starttijd of onder de baseline worden geweigerd. De baseline kan niet als losse gebeurtenis worden verwijderd. Na herstart overschrijft de oude add-onoptie `initial_odometer` hem niet. Bestaande record-id-reeksen blijven oplopen, zodat oude notificaties niet op nieuwe ritten kunnen slaan.

### Veiligheid, back-up en herstel

- Alleen geautoriseerde **POST `/api/administration/reset`** met JSON, exact `RESET`, geldige tellerstand én een willekeurig bevestigingstoken in `X-Reset-Token`. GET kan niet wissen. Token via geautoriseerde GET `/api/administration/reset-token`, tien minuten geldig, gebonden aan sessie/Ingress-pad; na succes vervallen alle open resetdialogen. Bestaande sessie-/Origin-controle blijft actief; cross-site verzoeken worden geweigerd. Er wordt geen CORS-toegang verleend.
- Eén SQLite-transactie (`BEGIN IMMEDIATE`) omvat verwijdering, baseline en minimale audit. Validatie gebeurt vóór iedere wijziging. Elke fout vóór commit geeft rollback en een Nederlandse melding zonder databasefoutdetails.
- Vlak vóór verwijderen maakt de SQLite backup-API een consistente kopie inclusief WAL-inhoud en controleert de integriteit. Fout bij back-up betekent **niet wissen**.
- Private opslag: **`/data/administration_backups/reset_<uniek>/rit_tank.db`**, maprechten `0700`, database `0600`. Eventuele tankbonnen staan onder dezelfde map in `receipts/`. Geen downloadroute of statische webpublicatie. Deze back-up is lokaal, niet apart versleuteld, en wordt niet naar Drive geüpload; hij valt onder de private add-onopslag. Bewaar/verwijder hem bewust als beheerder; geen automatische retentie voor deze herstelkopieën.
- Een private herstelmarkering en de transactionele reset-id herstellen een onderbroken verplaatsing van tankbonnen bij de volgende add-onstart. Bij rollback gaan de bonnen terug; na commit blijven ze uitsluitend in de herstelback-up. Back-ups bevatten dus bewust nog de oude administratie, de actieve audit niet. Handmatig terugzetten vereist beheerderstoegang en een gestopte add-on; geen herstelknop in deze release.
- HTTP-verzoeken en verwerking van GPS-/notificatieacties worden gesynchroniseerd met de reset, zodat lopende verwerking geen oude ritvoortgang terugschrijft. Open formulieren en PDF-previews in het huidige venster verdwijnen door herladen na succes.
- Eerder gedownloade PDF/CSV-bestanden, reeds verzonden telefoonmeldingen, Home Assistant Recorder-historie en bestaande Google Drive-archieven/back-ups vallen buiten deze lokale reset. Nieuwe rapportages en actuele HA-sensoren gebruiken de nieuwe administratie.
- Geen nieuwe tabellen of kolommen; geen simulatiemodus.

Release 28.00 voegt Google-adressuggesties toe aan **Rit corrigeren**, voor vertrek, tussenstops en aankomst. Typ minimaal drie tekens en kies zelf een resultaat. Alleen een geselecteerd, door Google Places Details bevestigd volledig adres kan worden opgeslagen. De bestaande Google-configuratie, Text Search met Nederlandse voorkeur, adresnotatie en correctiehistorie worden hergebruikt. Ongewijzigde adressen blijven exact behouden; ook historische onvolledige tekst blijft zichtbaar. Zoekfouten wijzigen niets. Er is geen nieuwe provider, sleutel of databaseschemamigratie. Kilometerstanden, afstanden en vergoeding veranderen niet door alleen een adrescorrectie.

Release 27.00 voegt een maand-/jaarcontrole toe vóór PDF- en CSV-export. De controle gebruikt exact de zichtbare PDF-rit-/etapperegels en de actuele kilometervergoeding. Ontbrekende volledige adressen, ontbrekende/ongeldige tellerstanden, een dalende tellerstand binnen één etappe en een eindtijd vóór de starttijd zijn fouten die export blokkeren. Zichtbare 0-km-ritten blijven behouden als waarschuwing (met onderscheid tussen gelijke en verschillende adressen). Opvallende afstand/tellerafwijkingen, onwaarschijnlijke tijdsduur/snelheid en exact overeenkomende mogelijke dubbele ritten geven een waarschuwing. Waarschuwingen vereisen expliciete bevestiging, ook via de backend voor PDF, preview en CSV. Vanuit een aandachtspunt opent de bestaande ritmodal voor adres-, tellerstand- en tijdcorrecties; na opslaan worden controle en totalen opnieuw berekend.

Tellerstandgaten tussen aparte zakelijke ritten zijn bewust toegestaan: privégebruik kan daartussen plaatsvinden. Doel en opmerking zijn niet verplicht. Dit is een datakwaliteitscontrole, geen fiscale goedkeuring. Er zijn geen externe calls, nieuwe databasekolommen, maandvergrendeling of opgeslagen reviewstatus. De PDF-layout, adressen-/tellerstandweergave, aantallen en vergoedingsberekening uit 26.00 blijven behouden. CSV behoudt zijn bestaande kolommen en stopregels, met dezelfde periodeselectie en validatie als PDF.

Release 26.00 gebruikt bij iedere PDF-export de actuele opgeslagen kilometervergoeding. Een wijziging werkt direct bij de volgende export, zonder herstart. Vergoeding per etappe en totaal gebruiken hetzelfde Decimal-tarief met ROUND_HALF_UP. Aantal ritten, zakelijke kilometers, totaalvergoeding en tabelnummering komen uit dezelfde definitieve geëxporteerde rit-/etapperegels, inclusief zichtbare 0-km-regels. PDF-layout en historische ritdata blijven ongewijzigd.

Release 25.00 voegt **🏠 Thuis** toe aan Nieuwe rit: altijd Verenlandweg 4, 7461 AP Rijssen. Typ een adres en tik direct op een zoekresultaat; de knoppen Dicteer adres en Dit adres gebruiken zijn verwijderd. GPS-adreskandidaten blijven direct selecteerbaar. Een locatie kiezen slaat niets op en start geen rit; bij een eindlocatie worden route, afstand en tellerstandsvoorstel ververst. Onder **Instellingen → Zakelijke kilometervergoeding** stel je het bedrag per km in (standaard € 0,25). Komma en punt worden geaccepteerd; de waarde wordt persistent opgeslagen via `km_reimbursement_rate`. Elke nieuwe PDF en CSV gebruikt het actuele tarief en herberekent de vergoeding per rit en het totaal met Decimal ROUND_HALF_UP. Het tarief is een huidige rapportinstelling, geen historisch tarief per rit.

Vanaf release 24.00 zijn nieuwe ritten zakelijke kilometerdeclaraties met een privéauto. Een actieve handmatige rit blijft open tot je zelf een volgende locatie vastlegt of de rit afsluit. Stopmeldingen herinneren je aan een open rit en slaan niets op. De actuele invoer bevat geen ritsoortkeuze, reviewkaarten of privé-omrijkilometers. **🏠 Thuis** selecteert Verenlandweg 4, 7461 AP Rijssen en ververst de route- en tellerstandsuggestie. Historische privéritten en assistant-data blijven ongewijzigd; er is geen databaseschemamigratie.

## Vorige release: 22.00

De fiscale PDF toont nu de opgeslagen vertrek- en aankomsttellerstand per etappe
en uitsluitend volledige fysieke adressen. Zie **Fiscale rittenregistratie-PDF**
voor adresprioriteit en ontbrekende gegevens. Deze release verandert geen
databaseschema, API of ritassistent-wachtrij.

## Historische installatie-informatie: 5.0 — stopmelding in Overijssel

Versie 5.0.0 bevat dezelfde functies als 4.4.1, onder het aangevraagde nieuwe versienummer. Pak de map `rit_tank` uit naar `/addons/rit_tank` en vervang de bestaande bronbestanden. Het configuratiebestand moet direct op `/addons/rit_tank/config.yaml` staan. Vernieuw daarna de lokale app-store met Controleren op updates. Alleen de ZIP downloaden of de add-on herstarten installeert de update niet.

## Overijssel

Bij bevestigde stilstand tijdens een actieve handmatige rit stuurt Rit & Tank alleen een reminder. De melding vraagt niet om de rit op te slaan en sluit de rit niet af. Als het adres bekend is, wordt het informatief genoemd; tikken op de melding opent `https://rit.huisplanadvies.nl`.

Als de PWA gesloten is, gebruikt de backend de ingestelde Home Assistant-meldingsservice. Safari wordt niet geforceerd geopend. De bestaande debounce en cooldown blijven gelden. GPS-updates en de backendcontrole kunnen de melding vertragen.

De ritassistent, Home Assistant-tracker en minimale ritafstand moeten zijn ingesteld. Google Geocoding moet de provincie herkennen. De bestaande provincieafhankelijke stopvertraging blijft gelden; 10 seconden stilstand kan ook bij verkeerslichten voorkomen.

Versie 4.4 voegt het aankomstscherm, GPS-routeconcepten en persoonlijke kilometercorrectie toe. De bestaande SQLite-database, Ingress-weergave, tankbonherkenning, Drive-back-up en Home Assistant-koppeling blijven behouden.

## Nieuwe functies testen

1. Start zelf een zakelijke ritregistratie en leg de startlocatie en tellerstand vast.
2. Kies bij **Volgende adres** of **Rit afsluiten** tussen **🏠 Thuis** en **📍 Gebruik huidige locatie**. Handmatig een adres kiezen blijft ook mogelijk.
3. Controleer het volledige adres, de routeafstand en de tellerstandsuggestie. De Thuis-knop gebruikt Verenlandweg 4, 7461 AP Rijssen; bevestig daarna zelf de locatie en sla de stop op of sluit de rit af.
4. Een aankomst/stilstand tijdens een actieve rit laat de rit open. De reminder slaat niets op en maakt geen reviewitem.
5. Test ook een notitie en een afwijkende route. Privé-omrijkilometers worden niet meer ingevoerd.

Persoonlijke correctie begint pas na vijf stabiele gecontroleerde trajecten van minstens 5 km met minstens vijf GPS-metingen. Uitschieters buiten ±15% worden niet geleerd. De mediaan van de laatste 30 geldige voorbeelden wordt gebruikt, met maximaal ±10% correctie. De instelling is uitschakelbaar. Opgeslagen kilometerstanden worden nooit achteraf aangepast. De leerhistorie hoort bij het kenteken, of bij de voertuignaam als het kenteken leeg is. Vul daarom eerst het juiste kenteken in.

### Grenzen van automatische herkenning

- Achtergrondlocaties komen van Home Assistant; er is geen nieuwe CarPlay- of Bluetooth-koppeling toegevoegd.
- Deze versie herkent vertrek vanaf bekende plekken en aankomst op bekende plekken of een langere onbekende stop. Vertrek vanaf een onbekende plek levert niet automatisch een volledig bruikbare rit op; gebruik dan handmatige registratie.
- GPS-afstand is een schatting tussen metingen, geen navigatie-routeafstand. Bij meer dan vijf minuten tussen verplaatste meetpunten, onbetrouwbare routepunten of minder dan drie routepunten wordt geen automatische aankomststand aangeboden.
- De app bepaalt uit locatiebeweging niet zeker of je zelf in de auto rijdt. Controleer het voorstel; wandelingen of passagiersritten kunnen ook beweging veroorzaken.
- De daadwerkelijke teller blijft leidend. Concepten worden niet definitief zonder bevestiging. Classificatiepercentages beschrijven regels, niet de nauwkeurigheid van GPS.
- Oude voorstellen die overlappen met geregistreerde ritten worden geweigerd. Controleer de historie en verwijder zo nodig alleen het overbodige voorstel via ×.
- Tankbonherkenning en Drive-back-up zijn behouden, niet in deze update opnieuw aan jouw externe accounts getest.

## Updaten

1. Maak eerst een Home Assistant-back-up.
2. Stop **Rit & Tank**.
3. Vervang `/addons/rit_tank` door de map `rit_tank` uit deze ZIP.
4. Vernieuw de lokale app-store en update/herinstalleer de add-on.
5. Start de add-on en controleer via Ingress bij **Instellingen** dat versie **5.0.0** actief is. Sluit de PWA volledig en open hem opnieuw als nog een oudere interface zichtbaar is.

De database staat in `/data` en wordt niet vervangen.

Bewaar de back-up en versie 4.3.1 totdat 4.4 op jouw iPhone goed werkt. Deze update voegt een leertabel toe en bewaart bestaande ritten. De geautomatiseerde backend- en schermlogicatests zijn geslaagd; de echte Safari-weergave, Home Assistant-tracker en externe accounts zijn niet in deze omgeving getest.

## Eenmalig: standalone-modus inschakelen

Open **Instellingen → Add-ons → Rit & Tank → Configuratie** en stel in:

```yaml
standalone_enabled: true
standalone_password: "kies-hier-een-uniek-wachtwoord-van-minimaal-12-tekens"
standalone_session_days: 30
```

Ga daarna op dezelfde add-onpagina naar **Netwerk** en publiceer containerpoort `8099` op een vrije hostpoort, bijvoorbeeld `8099`. Start de add-on opnieuw.

Publiceer deze poort vervolgens via een HTTPS reverse proxy op een eigen hostnaam, bijvoorbeeld:

```text
https://rit-tank.jouwdomein.nl  →  http://IP-VAN-HOME-ASSISTANT:8099
```

De proxy moet `Host` en `X-Forwarded-Proto: https` doorgeven. Gebruik een geldig TLS-certificaat. Stel poort 8099 **niet rechtstreeks via internet** open; Rit & Tank weigert standalone-aanvragen zonder de HTTPS-proxyheader.

De Nabu Casa-URL met `/local_rit_tank` is een Home Assistant-dashboardroute en daardoor geen stabiele zelfstandige app-origin. Gebruik die route alleen binnen Home Assistant; gebruik voor de PWA de nieuwe eigen HTTPS-hostnaam.

## Installeren op de iPhone

1. Open de nieuwe eigen HTTPS-URL in **Safari**.
2. Log in met het standalone-wachtwoord.
3. Tik op **Deel** (vierkant met pijl omhoog).
4. Kies **Zet op beginscherm** en daarna **Voeg toe**.
5. Verwijder eventueel de oude Rit & Tank-snelkoppelingen en open het nieuwe icoon.

De PWA opent fullscreen met een eigen icoon en blijft op haar eigen URL. De app-interface wordt lokaal gecachet; actuele gegevens, opslaan, exports, synchronisatie en GPS-fallback vereisen verbinding met de backend.

## Achtergrondlocatie en stopherinneringen

Open **Rit & Tank → Meer → Instellingen**:

1. Zet **Ritassistent actief** aan.
2. Kies de iPhone `person` of `device_tracker` en de mobiele meldingsservice.
3. Stel de minimale ritafstand en stilstandsduur in.

Tijdens een actieve handmatige rit houdt de assistent GPS-afstand en locatie bij. Bij een herkende stop stuurt hij alleen een reminder. Je sluit de rit zelf af.

## Fiscale rittenregistratie-PDF

Elke etappe toont vertrek- en aankomstadres met daarnaast de bijbehorende
opgeslagen stop-tellerstanden in de kolom **Tellerstand**. De standen staan
rechts uitgelijnd, op dezelfde regel als het adres, met Nederlandse
duizendtallen en hele kilometers (zoals in de app). Een ontbrekende stand
wordt weergegeven als **—**, niet als nul. **Afstand** blijft de afzonderlijke
etappeafstand; de PDF berekent die niet opnieuw uit de getoonde tellerstanden.

Voor PDF-adressen geldt: volledig opgeslagen stopadres, daarna het volledige
adres van de gekoppelde bekende plek, daarna een volledig handmatig opgeslagen
adres en tenslotte het reeds opgeslagen adres uit de coördinatencache.
De export doet hiervoor geen netwerkverzoeken en wijzigt geen gegevens.
Bekende-pleklabels zoals Thuis of Kantoor zijn geen adres. Alleen een fysiek
adres met straat, huisnummer, postcode en plaats wordt afgedrukt; ontbrekende
of niet als volledig herkenbare adressen worden **Adres ontbreekt**.
Lange adressen lopen door op een volgende tekstregel; de rij groeit mee en
blijft bij een paginaovergang als één etappe bijeen.

## Slimme tankbon

Kies bij een tankbeurt een scherpe foto van de bon. De add-on voert OCR volledig lokaal uit en probeert liters, literprijs, totaalbedrag, datum en tankstation te herkennen. Controleer de voorgestelde waarden vóór opslaan. De foto wordt pas bij **Tankbeurt opslaan** definitief in `/data/receipts` bewaard.

## Automatische Google Drive-back-up

Zie [de actuele handleiding voor 5.0.7](UPDATE_5.0.7.md) voor PDF-delen, permanente archivering en de volledige stappen voor Google Drive.

Voor een gewone Google Drive gebruik je gebruikers-OAuth via `google_drive_oauth_json`. Een service-account vereist een map in een **Gedeelde Drive** van Google Workspace. Maak daarvoor in Google Cloud een afzonderlijk service-account, activeer de Google Drive API, download de JSON-sleutel en geef het service-account toegang tot de doelmap.

Stel daarna in de Home Assistant add-onconfiguratie in:

```yaml
google_drive_enabled: true
google_drive_folder_id: "MAP-ID-UIT-DE-DRIVE-URL"
google_drive_service_account_json: '{"type":"service_account", ... }'
backup_encryption_password: "een-uniek-wachtwoord-van-minimaal-12-tekens"
backup_hour: 3
backup_retention_days: 0
```

Herstart de add-on en gebruik onder **Meer → Instellingen** de knop **Nu back-up maken**. Als deze test slaagt, maakt Rit & Tank dagelijks vanaf het gekozen uur één back-up. Bij bewaartermijn `0` wordt niets automatisch verwijderd; bij 1–365 dagen worden oudere, door de app gemarkeerde back-ups opgeruimd. PDF-archieven blijven altijd behouden.

De bestanden hebben extensie `.rtbackup` en bevatten een consistente kopie van de database, tankbonnen en herstelmetadata. Ze zijn versleuteld met AES-256-GCM. Bewaar het back-upwachtwoord buiten Home Assistant: zonder dat wachtwoord kan de back-up niet worden hersteld. Gebruik geen service-account met brede Workspace-beheerdersrechten en zet de JSON-sleutel nooit in e-mail of documentatie.

## Beveiliging

- Buiten Ingress zijn alle gegevens, mutaties, bonnen en exports afgeschermd.
- De login gebruikt een ondertekende `Secure`, `HttpOnly`, `SameSite=Strict` sessiecookie.
- Na vijf mislukte pogingen wordt inloggen tijdelijk geblokkeerd.
- Een wachtwoordwijziging maakt bestaande sessies direct ongeldig.
- De sessieduur is instelbaar van 1 tot 90 dagen.
- Home Assistant Ingress blijft door Home Assistant zelf geauthenticeerd.

## Architectuur en iOS-achtergrondlocatie

- **Safari-PWA:** zelfstandige mobiele interface, app-shell, rit/tank-invoer en exports.
- **Eigen backend/API:** login, SQLite, ritten, stops, tankbeurten, statistieken en PDF/CSV.
- **Home Assistant:** `person.*`/`device_tracker.*`, passieve zones, automatiseringen en mobiele meldingen.

iOS laat een gesloten webapp niet betrouwbaar continu GPS verzamelen. Daarom blijft de Home Assistant Companion-app de achtergrondlocatie leveren; Rit & Tank bewaart de volledige rit- en tankhistorie zelf.

## API-routes

Naast de bestaande routes zijn beschikbaar:
- `POST /api/trips/start`
- `POST /api/trips/location`
- `POST /api/trips/finish`
- `GET /api/stats/day|week|month|year`
- `GET /api/export/pdf?period=month`

Buiten Ingress vereisen deze routes altijd een geldige ingelogde sessie.

---

# Rit & Tank 3.6 — Slimme eindstand + snelle stopmelding

Versie 3.6 bouwt voort op de achtergrond-ritassistent uit 3.5. Tijdens een actieve rit wordt nu best-effort de afgelegde GPS-route bijgehouden via de gekozen Home Assistant `person.*` / `device_tracker.*`.

## Wat is nieuw
- Bij **🏁 Rit afsluiten** staat het kilometer-scrollwheel automatisch op een voorgestelde eindstand.
- Formule: **laatste handmatig vastgelegde kilometerstand + achtergrond-GPS-afstand sinds die stop**.
- De echte kilometerteller blijft leidend: controleer en corrigeer de voorgestelde stand voordat je opslaat.
- Tijdens een actieve rit zie je in de groene ritkaart hoeveel kilometer op de achtergrond is gevolgd en welke eindstand daaruit volgt.
- Bij stilstand kan vrijwel direct een pushmelding komen met de voorgestelde eindstand.
- Deze stopmeldingen worden bewust alleen verstuurd voor stops die Google Geocoding als **provincie Groningen** of **provincie Drenthe** herkent.

## Snelle stopmelding instellen
Open **Rit & Tank → ⚙️ Instellingen → Automatische ritassistent**.

Aanbevolen:
1. **Ritassistent actief** = aan.
2. Kies de iPhone `device_tracker` met GPS-coördinaten.
3. Kies de juiste `notify.mobile_app_*`.
4. **Snelle stopmelding na** = 30 seconden.
5. Minimale ritafstand = bijvoorbeeld 500 meter.
6. Google Geocoding API moet werken; zonder provincieherkenning wordt géén provinciegebonden push gestuurd.

De app controleert de Home Assistant tracker maximaal iedere 10 seconden. iOS bepaalt echter zelf wanneer de Companion-app een nieuwe achtergrondlocatie aan Home Assistant doorgeeft. '30 seconden' is dus een doelwaarde vanaf het moment waarop actuele locatie-updates beschikbaar zijn, geen harde realtime-garantie.

## Kilometerstand-suggestie
De routeafstand is een hulpmiddel. GPS-afstand kan afwijken door tunnels, slechte ontvangst, vertraagde iOS-updates of GPS-sprongen. Daarom:
- de schatting wordt afgerond naar hele kilometers;
- het scrollwheel blijft volledig aanpasbaar;
- de daadwerkelijk afgelezen kilometerteller blijft de fiscale bron.

Bij een handmatig opgeslagen **Volgende adres** wordt de schatting opnieuw vanaf die stop opgebouwd. Daardoor blijft de suggestie voor de volgende etappe zo dicht mogelijk bij je laatste echte tellerstand.

## Provinciefilter
Automatische aankomstpushes en snelle stopmeldingen worden alleen verstuurd wanneer de stoplocatie door Google wordt herkend als:
- Groningen
- Drenthe

Buiten deze provincies blijft de ritassistent en afstandsregistratie werken, maar wordt geen automatische stop-push verstuurd.

---

# Rit & Tank 3.5 — Achtergrond-ritassistent

Versie 3.5 bouwt verder op **Slimme plekken** uit 3.4. Bekende plekken kunnen nu als passieve Home Assistant-zones worden aangemaakt. De app volgt op de achtergrond een gekozen `person.*` of `device_tracker.*`, herkent aankomst/vertrek en kan een pushmelding sturen met **Privé** en **Zakelijk** als snelle keuze.

## Updaten vanaf 3.4
1. Maak eerst een Home Assistant-back-up.
2. Stop **Rit & Tank**.
3. Vervang `/addons/rit_tank` door de map `rit_tank` uit deze ZIP.
4. Vernieuw de lokale app-store en update/herinstalleer de app.
5. Controleer dat versie **3.5.0** actief is.
6. Start Rit & Tank opnieuw.

De bestaande database wordt automatisch uitgebreid. Tankbeurten, ritten, bekende plekken, bonfoto's en historie blijven behouden.

## Eenmalige iPhone-instelling
Voor betrouwbare achtergrondherkenning moet de Home Assistant Companion-app locatie mogen bijwerken terwijl deze niet geopend is.

Controleer op de iPhone bij **Instellingen → Privacy en beveiliging → Locatievoorzieningen → Home Assistant** dat locatiegebruik geschikt is voor achtergrondlocatie en dat **Exacte locatie** aan staat. Zorg daarnaast dat de betreffende `device_tracker` of `person` in Home Assistant actuele GPS-coördinaten heeft.

## Ritassistent instellen
Open **Rit & Tank → ⚙️ Instellingen → Automatische ritassistent**.

1. Zet **Ritassistent actief** aan.
2. Kies bij **Persoon / iPhone voor achtergrondlocatie** de juiste `person.*` of `device_tracker.*`.
3. Kies bij **Mobiele meldingsservice** de juiste `notify.mobile_app_*` van die iPhone.
4. Laat **Bekende plekken als HA-zones** aan staan.
5. Tik eenmaal op **📍 Zones synchroniseren**.
6. Tik op **🔔 Test melding** om te controleren of de pushmelding aankomt.

### Bekende plekken
Open **Ritten → Bekende plekken & slimme regels**. Voeg bijvoorbeeld `Thuis` en `School` toe.

Per plek kun je instellen:
- **Als ik hier aankom**: Privé / Zakelijk / vragen.
- **Vanaf hier naar een onbekende plek**: Privé / Zakelijk / geen vaste suggestie.
- **Herkenningsradius**: bijvoorbeeld 150–250 meter.

Als zonesynchronisatie actief is, maakt Rit & Tank hiervan passieve Home Assistant-zones met een naam als `RT School`. In Rit & Tank zie je bij de plek **HA-zone ✓** als synchronisatie gelukt is.

## Voorbeeld: Thuis → School → Groningen
Stel in:
- **School**: aankomst = **Privé**.
- **School**: vertrek naar onbekend = **Zakelijk**.

Dan kan de flow worden:

1. Je vertrekt van Thuis en rijdt naar School.
2. Bij aankomst herkent Home Assistant de School-zone en Rit & Tank stelt **Privé** voor.
3. Je krijgt een melding met de knoppen **Privé** en **Zakelijk**.
4. Je rijdt daarna van School naar Groningen.
5. Groningen hoeft geen bekende plek te zijn. Wanneer de app daar een stop detecteert, gebruikt hij de regel van School en stelt **Zakelijk** voor.

Een tik op Privé/Zakelijk in de melding bevestigt de **ritsoort**. De fysieke kilometerstand kan Home Assistant niet zelf weten; daarom blijft de aankomst in de app klaarstaan totdat jij de kilometerstand controleert/invult. Zo wordt de rit niet stilzwijgend met een verzonnen kilometerstand opgeslagen.

## Onbekende stops herkennen
**Onbekende stops herkennen** is bedoeld voor bestemmingen die nog geen bekende plek zijn, zoals een klant in Groningen.

De app kijkt of je minimaal de ingestelde afstand van de bekende vertrekplek bent gekomen en daarna enige minuten op ongeveer dezelfde GPS-positie blijft. Standaard:
- minimale ritafstand: **500 m**
- stilstand voor herkenning: **4 minuten**

Dit is bewust gemarkeerd als experimenteel/best-effort. iOS bepaalt zelf hoe vaak het in de achtergrond een nieuwe locatie doorgeeft. Bekende HA-zones zijn daardoor betrouwbaarder dan volledig onbekende bestemmingen.

## Meldingen en privacy
De GPS-gegevens worden door Home Assistant en Rit & Tank lokaal gebruikt. Voor bekende plekken gebruikt Rit & Tank alleen de ingestelde coördinaten/radius. Google wordt alleen gebruikt voor de bestaande Places/Geocoding-functies als je zelf een Google API-key hebt ingesteld.

De melding bevat een suggestie, geen definitieve fiscale beslissing. Jij kunt die met één tik corrigeren. De bevestiging en bron van de classificatie worden in de app bewaard.

## Problemen oplossen
**Geen zones zichtbaar / HA-zone fout**
- controleer of de app draait met `homeassistant_api: true` (dit is al in deze versie ingesteld);
- tik opnieuw op **Zones synchroniseren**;
- open ⚙️ Instellingen en kijk bij de technische status naar de laatste foutmelding.

**Geen pushmelding**
- kies de juiste `notify.mobile_app_*`;
- gebruik **Test melding**;
- controleer of meldingen voor Home Assistant op de iPhone toegestaan zijn.

**Bekende plek wordt niet herkend**
- controleer de gekozen tracker;
- controleer of die entiteit latitude/longitude heeft;
- verhoog eventueel de herkenningsradius iets;
- controleer of Exacte locatie op de iPhone aan staat.

**Onbekende bestemming wordt niet gedetecteerd**
- dit hangt af van achtergrondlocatie-updates van iOS;
- voeg veelgebruikte bestemmingen bij voorkeur als bekende plek toe voor betrouwbaardere herkenning.

### Controle vóór export (27.00)

Kies PDF of Ritten CSV, maand/jaar en periode. De controle start automatisch. Corrigeer rode aandachtspunten via Corrigeren. Oranje aandachtspunten kunnen na controle expliciet worden geaccepteerd voor deze export. Iedere export wordt opnieuw live gecontroleerd.

`GET /api/business/validate?period=month&year=2026&month=9` levert status, samenvatting en issues. Dezelfde parameters gelden voor PDF en CSV. Bij fouten antwoorden exportendpoints met 422; bij waarschuwingen zonder `allow_warnings=true` met 409. Ook `/api/business/pdf-preview` en de PDF-alias zijn beschermd.

Afstandswaarschuwing: absoluut verschil minimaal 3 km én minimaal 25% van de grootste van ritafstand/tellerverschil. In de huidige opgeslagen ritdata is de etappeafstand afgeleid van de tellerstanden; de helper vergelijkt de aangeleverde rapportafstand zonder extra routeberekening. Ontbrekende onafhankelijke GPS-/routeafstanden worden overgeslagen. Tijden worden alleen vergeleken als beide geldig en onderling vergelijkbaar zijn; >180 km/u is een waarschuwing. Duplicaten vereisen exact dezelfde starttijd, adressen, begin-/eindstand en afstand; er wordt niets verwijderd.

## Los PDF-tankbonnenarchief (33.06)

De bestaande knop **Tankbon scannen** opent het zelfstandige bonnenarchief. Maak een iPhone-foto, selecteer tot vier afbeeldingen voor een meervoudige PDF of upload een bestaande PDF. Foto's worden op een witte A4-pagina geplaatst, gedraaid volgens EXIF en voor leesbaarheid aangescherpt. De server ondersteunt JPEG/PNG/WebP; iOS zet ondersteunde andere afbeeldingen in de browser om naar JPEG. PDF's mogen maximaal 30 pagina's bevatten. De namen volgen indien herkend het patroon `JJJJ-MM-DD_Tankstation_Plaats_Bedrag.pdf`. OCR geeft uitsluitend die vier naamgegevens terug. Ontbrekende of tegenstrijdige waarden blijven gemarkeerd als onbekend en zijn vóór opslaan aan te passen. Bij identieke PDF-inhoud of naam volgt een dubbelebonwaarschuwing met verplichte expliciete bevestiging.

Nieuwe PDF's en index leven onder `/data/receipt_archive` respectievelijk in de bestaande `/data/rit_tank.db`; de oorspronkelijke bonbestanden in `/data/receipts` blijven onaangeroerd. Bestaande bonnen zijn in het archief te bekijken en worden als originele bestanden opgenomen in de ZIP-export onder `bestaande_bonnen/`. Er vindt geen koppeling of automatische overdracht plaats naar kilometerstand, liters, literprijs, datum van tankregistraties of ritten. Voeg voor extra zekerheid een externe ZIP-kopie toe aan de reguliere Home Assistant back-up.


## Dagplanning naar Google Agenda (33.08)

De knop **Dagplanning importeren** staat op het dashboard. Upload een screenshot met de expliciete datum en bezoeksnummers. De OCR herkent (waar mogelijk) de adressen en behoudt de originele bezoekvolgorde: ochtend gevolgd door middag, zonder route-optimalisatie. Controleer de datum, pas onduidelijke of afgekorte adressen aan, verwijder bezoeken of verplaats ze met omhoog/omlaag. Er is een extra controle en handmatige aanvulling vereist voor onzeker herkende adressen. De screenshot wordt niet opgeslagen.

Selecteer een schrijfbare doelagenda en vink de expliciete bevestiging aan. De app voert eerst een doublurecontrole uit bij Google en vraagt bij bestaande dagafspraken op dezelfde locatie en datum een extra bevestiging. Elk nieuw bezoek wordt één aparte hele-dag-afspraak zonder tijden, met titel **01 · Bezoek 1 van N**, enzovoort en het gecontroleerde adres in het locatieveld. Bestaande afspraken worden niet gewijzigd. Stabiele Google event-ID's en een Google-voorcontrole voorkomen duplicaten bij herhaald verzenden.

### Google Agenda eenmalig veilig koppelen

De bestaande Google Drive-koppeling is alleen bruikbaar als haar OAuth-token al expliciet Calendar-rechten heeft. Anders is er een aparte beperkte autorisatie nodig in de add-onoptie `google_calendar_oauth_json` (een afgeschermd wachtwoordveld). Dit is een *authorized user* OAuth-JSON met refresh-token, géén service-account-JSON.

1. Activeer **Google Calendar API** in je Google Cloud-project, configureer het OAuth-toestemmingsscherm en maak een OAuth-client van het type **Desktop-app**. Download het client-secret-bestand op je eigen computer als `client_secret.json`. Bij een testapp moet je eigen account als testgebruiker zijn toegevoegd; voor langdurige toegang kan publicatie van het toestemmingsscherm nodig zijn.
2. Voer op je eigen computer `python -m pip install google-auth-oauthlib` uit. Bewaar het volgende script naast `client_secret.json` en voer het uit. Meld je met je Google-account aan, kies de beperkte Calendar-machtigingen en bewaar de JSON uitsluitend lokaal.

    ~~~python
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [
        'https://www.googleapis.com/auth/calendar.events',
        'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
    ]
    flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', scopes)
    creds = flow.run_local_server(port=0, access_type='offline', prompt='consent')
    with open('calendar_credentials.json', 'w', encoding='utf-8') as handle:
        handle.write(creds.to_json())
    print('Google Agenda-autorisatie gereed')
    ~~~

3. Kopieer de volledige inhoud van `calendar_credentials.json` naar **Home Assistant → Add-ons → Rit & Tank → Configuratie → google_calendar_oauth_json**. Sla op en herstart de add-on. Deel deze JSON nooit in GitHub, chat, screenshots of logs.
4. Open **Dagplanning importeren**, selecteer een agenda en controleer de voorgestelde afspraken. Als de autorisatie wordt ingetrokken of vervalt, genereer een nieuw refresh-token op je eigen computer.

De Calendar-aanroepen worden veilig door de add-onserver uitgevoerd; ook via Home Assistant Ingress is geen Google-redirect nodig. Het originele screenshot van 9 oktober 2026 met persoonsgegevens wordt niet in de openbare repository opgenomen; de regressietest gebruikt geanonimiseerde OCR-tekst met dezelfde vijfbezoekstructuur. Ritten, kilometerstanden, tankbeurten en bestaande agenda-afspraken blijven onaangeroerd.
