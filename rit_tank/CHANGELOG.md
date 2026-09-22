# Changelog

## 19.00
- De fiscale PDF toont volledige adressen voor bekende plekken, waaronder Thuis en Ouders.
- Labels van bekende plekken worden niet meer als primair PDF-adres gebruikt wanneer een volledig adres beschikbaar is.
- Alle PDF-pagina's gebruiken dezelfde headerpositie en bovenmarge.
- Het Huisplan-logo staat subtiel hoger, met behoud van de exacte beeldverhouding.
- De per-etappe-export en uniforme typebadges Privé, Zakelijk en Privé/Zakelijk blijven behouden.
- Versietests controleren de actuele release 19.00.
- CI controleert release 19.00 tijdens runtime-imports en de productie-Dockerbuild.

## 18.00
- Bekende plekken bewaren en tonen nu het volledige gekozen adres; oudere records blijven compatibel.
- Volgende locatie ondersteunt adres dicteren via de bestaande adreszoeker; gesproken tekst wordt eerst gevalideerd.
- Een actieve rit toont live GPS-status ongeveer iedere 5 seconden, zonder de volledige dashboardrefresh te versnellen.
- GPS-only bewaart voortaan expliciet de gekozen GPS-coördinaten van de bekende plek.
- Een adresvoorstel voor een legacy bekende plek is selecteerbaar en wordt pas na opslaan persistent.
- De fiscale PDF exporteert iedere daadwerkelijke etappe als eigen regel; tussenstops verdwijnen niet meer.
- PDF-soortbadges Privé, Zakelijk en Privé/Zakelijk hebben exact dezelfde breedte en bedekken de volledige tekst.
- PDF-totalen blijven uit één bron komen, dus het opsplitsen van regels veroorzaakt geen dubbeltelling.

## 17.00
- Controleren bevat nu vertrek- én aankomstadrescontrole.
- Beide adressen kunnen vanuit dezelfde controleflow worden aangepast.
- De bestaande dicteerfunctie is beschikbaar voor beide adresvelden.
- Legacy/pending voorstellen blijven compatibel.
- Routeafstand en tellerstandsvoorstel worden direct bijgewerkt na correctie.
- De bestaande losse Route-knop blijft als shortcut beschikbaar.

## 16.00
- Mobiele bottom-sheet modals scrollen nu zelfstandig.
- De achtergrondpagina wordt vergrendeld zolang een modal open is.
- iPhone safe-area en dynamic viewport worden ondersteund.
- De scrollpositie van de onderliggende pagina wordt na sluiten hersteld.
- Geen functionele wijzigingen aan ritregistratie.

## 15.00
- Pushmeldingen openen voortaan direct `https://rit.huisplanadvies.nl`.
- Te controleren is volledig mobiel responsive met een vaste dismiss-knop.
- Vertrek- en aankomstadres zijn beide handmatig corrigeerbaar.
- Afstand en tellerstandsvoorstel worden opnieuw berekend na routecorrectie.
- Dicteren is toegevoegd aan adreszoekvelden via browser speech recognition.
- Audio wordt niet opgeslagen of naar de backend verzonden.

## 14.00
- Één live GPS-afstand weergegeven tijdens actieve rit; geen concurrerende draft-afstanden meer.
- Tellerstandsuggestie altijd berekend wanneer geldige basisstand en tracked_m > 0 beschikbaar zijn.
- Incompleet GPS toont waarschuwing maar behoudt bruikbare suggestie.
- Weinig GPS-samples: suggestie zichtbaar maar onbetrouwbaar gemarkeerd.
- Volgend adres / Rit afsluiten halen meeste recente GPS-status op vóór modalvulling.
- Bekende plek: altijd "Gebruik huidige locatie" knop beschikbaar.
- Na huidige locatie: maximaal 10 nabijgelegen adressen gesuggereerd.
- Geselecteerd adres bepaalt coördinaten, niet ruwe telefoonpositie.
- Handmatige adreszoekfunctie wanneer juiste adres niet in 10 gevonden.
- Handmatig zoeken werkt ook zonder beschikbare GPS.
- Expliciete GPS-only fallback als niet-automatische laatste optie.
- Bestaande plaats wijzigt alleen locatie na expliciete gebruikerskeuze.

## 12.00
- GitHub Actions CI toegevoegd voor pull requests, pushes naar main en handmatige runs.
- De volledige Python-testsuite draait automatisch, inclusief compile- en importchecks.
- De productie-Dockerfile wordt daadwerkelijk gebouwd en de gebouwde image wordt gecontroleerd op alle runtime-modules.
- Runtime imports worden vanuit de gebouwde image getest om herhaling van de packagingfout uit de 10.00-modularisatie te voorkomen.
- Geen functionele wijzigingen aan Rit & Tank.

## 11.00
- Home Assistant Docker-image bevat nu alle in 10.00 geëxtraheerde runtime-modules.
- Hierdoor kan app.py de nieuwe modules tijdens add-on startup importeren.
- Geen functionele wijzigingen.

## 10.00
- PDF-generator geëxtraheerd naar `pdf_report.py`.
- Google Places en geocoding geëxtraheerd naar `google_places.py`.
- Route-afstandsberekening geëxtraheerd naar `routing.py`.
- Home Assistant-integratie geëxtraheerd naar `home_assistant.py`.
- Zakelijke ritlogica geëxtraheerd naar `trips.py`.
- Ritassistent geëxtraheerd naar `assistant.py`.
- `app.py` teruggebracht van circa 5.240 naar circa 3.100 regels.
- Bestaande `app.*` compatibility-wrappers behouden.
- Geen functionele, database-, API- of PDF-layoutwijzigingen bedoeld.

## 9.00
- PDF-bovenmarge aangepast naar exact 25 mm vanaf bovenkant pagina.
- Privé- en Zakelijk-badges in kolom Soort hebben nu dezelfde vaste breedte van 65 pt.
- Rapportperiodevak heeft meer verticale ruimte met betere leesbaarheid (drie duidelijke niveaus: label, maand, datumrange).
- Overige PDF-layout, functionaliteit en database-integriteit blijven ongewijzigd.

## 8.00
- Fiscale rittenregistratie-PDF volledig opnieuw opgebouwd volgens compacte tabel-layout.
- Één rit per tabelrij (niet meer grote verticale blokken).
- Vertrek en aankomst compact in dezelfde adreskolom met gekleurde indicatoren (groen voor vertrek, rood voor aankomst).
- Privé/zakelijk als gekleurde badges (blauw voor privé, groen voor zakelijk) met ronde indicator.
- Datum en tijd op twee regels in compacte formattering (b.v. "za 19-09-2026" + "15:05 – 15:52").
- Afstand rechts uitgelijnd met Nederlandse kommanotatie (b.v. "1,0 km").
- Compactere paginering: veel meer ritten per pagina (verwacht 6-10 per pagina in plaats van 3).
- Tabelkop op elke pagina behouden, ook na paginering.
- Header, overzichtskaarten en footer afgestemd op het goedgekeurde Huisplan-referentieontwerp.
- Obsolete verticale rit-card rendering verwijderd.
- Alle onderlying business-logic en database-integriteit behouden.

## 7.00
- Ritvoorstellen kunnen vóór opslaan een gecorrigeerde bestemming krijgen via handmatige adresselectie (zoekveld, Places-resultaten, preview van vertrek/bestemming/afstand/eindstand, "Gebruik dit adres").
- Routeafstand wordt opnieuw berekend van de oorspronkelijke vertreklocatie naar de nieuw gekozen bestemming (nooit vanaf de oude bestemming of (0,0)); fallback naar de bestaande GPS-afstand wanneer geen betrouwbare vertreklocatie bekend is.
- Definitief opgeslagen ritten, tussenstops, PDF en rittenoverzicht gebruiken bij correctie altijd de gecorrigeerde bestemming; de oorspronkelijke (mogelijk foutieve) GPS-bestemming, coördinaten en afstand blijven aantoonbaar bewaard in trip_stops en het audit-log, maar worden nooit opnieuw als actuele aankomst opgeslagen.
- Voorgestelde eindtellerstand wordt na een adrescorrectie herberekend op basis van de gecorrigeerde afstand: bij een echte wegroute zonder GPS-kalibratiefactor, bij een GPS-schatting met de bestaande kalibratielogica.
- Adrescorrectie is alleen mogelijk zolang een ritvoorstel nog niet definitief (fiscaal) is opgeslagen (status 'pending' of 'confirmed'); na 'completed' is corrigeren niet meer mogelijk.
- PDF-rittenregistratie heeft nieuw modern ontwerp met duidelijke adres-hiërarchie.
- Privéritten: lichtblauwe bullet en accent; zakelijke ritten: lichtgroene bullet en accent.
- Huisplan-logo staat rechtsboven in de PDF-kop; geen Rit & Tank branding meer.
- GPS-huisnummerwaarschuwing verdwijnt uit PDF-uitvoer.
- PDF-footer bevat alleen "Huisplan BV", rapportperiode en paginanummer.
- Alle actuele versievelden (config.yaml, app.py, runtime) zijn exact 7.00.

## 6.0.0
- Nieuwe major release op basis van de reeds gemergede runtime-, database-, ritverwijderings- en versieconsistentieverbeteringen.

## 5.0.11
- SQLite-verbindingen worden correct gecommit of teruggedraaid en daarna gesloten.
- Home Assistant API-foutmeldingen lekken de Supervisor-token niet.
- Dubbele `delete_business_trip()` is geconsolideerd met behoud van snapshot, auditlogging en gekoppelde event-verwijdering.
- Runtime- en documentatieversies zijn gelijkgetrokken naar 5.0.11.

## 5.0.9
- Grote PDF-ritregistratieknop op het beginscherm, met maand- en kalenderjaarkeuze.
- Jaarexport bevat de aanwezige maanden chronologisch, met maandtotalen en een nieuwe pagina per volgende maand.
- Ritten worden op vertrekdatum ingedeeld, zonder dubbeltelling over maandgrenzen.
- PDF-generatie vraagt geen externe geocoding op; opgeslagen adressen blijven behouden, anders worden coördinaten getoond.
- Duidelijke exportfouten en time-out; openen/afdrukken gebruikt rechtstreeks de beveiligde PDF-route.
- Onbeschikbare bestandsdeling blokkeert openen of downloaden niet.

## 5.0.8
- Tankboncamera uitsluitend bovenaan Tanken; verwijderd van het overzicht.
- Vaste uitleg onder de scanner verwijderd; scanresultaten en foutmeldingen blijven zichtbaar.
- Liters en beide decimalen staan op één rij, ook op een smal telefoonscherm.
- Laatst gekozen literprijs wordt op het apparaat onthouden, ook zonder opslaan van de tankbeurt. Een herkende bonprijs heeft voorrang.
- Doel/afspraak staat bij een nieuwe rit standaard op 'klantbezoek' en blijft aanpasbaar.
- Na adresbevestiging direct doorscrollen binnen het formulier: naar Registratie starten of naar de ritsoort bij een tussenstop. Na ritsoortkeuze naar opslaan.
- Een trage locatiesuggestie blokkeert doorscrollen niet en overschrijft geen handmatig gekozen ritsoort.
- JavaScript-regressietests geslaagd voor prijsgeheugen, scanvoorrang en scrollgedrag. Echte iPhone-weergave nog op het apparaat controleren.

## 5.0.7
- Grote tankboncamera bovenaan het overzicht en als eerste onderdeel bij tanken.
- Herstel automatisch invullen van liters/literprijs bij herhaalde scans; liters behouden twee decimalen. Niet herkende waarden worden expliciet gemeld en niet overschreven.
- Verbeterde herkenning van literprijslabels, gesplitste regels en brandstoftabellen.
- Huidige ritlocatie toont maximaal tien bestaande BAG-adressen in de dichtstbijzijnde straat; huisnummerkeuze of handmatige adresbevestiging blijft in de registratie behouden.
- Locatie-fallback staat in de database; bestaande browserkeuze wordt eenmalig overgenomen. Tijdelijk ontbrekende HA-entiteiten blijven geselecteerd.
- PDF-scherm met delen naar andere apps, openen/afdrukken, downloaden en permanent archiveren in Drive.
- Drive ondersteunt gebruikers-OAuth naast service-accounts voor Gedeelde Drives. Bewaartermijn 0 betekent onbeperkt. PDF-archieven worden nooit door back-upopruiming verwijderd.
- Huisplan-logo blijft op website, login en PWA-iconen behouden.


## 5.0.6
- Huisplan-logo op het inlogscherm en in de kop van de website.
- Huisplan-logo als browsericoon en beginschermicoon voor iPhone/iPad.
- Nieuwe icoonbestandsnamen en PWA-cache zodat oude auto-iconen worden vervangen.
- Staat het oude beginschermicoon er nog? Verwijder de snelkoppeling en voeg de website opnieuw toe.

## 5.0.5
- Het echte Huisplan-logo wordt als beeldmerk in de PDF-kop en voettekst gebruikt.
- Het originele transparante logo en een compacte PDF-versie worden meegeleverd.
- Versienummer en installatie-image bijgewerkt.

## 5.0.4
- Fiscale PDF-uitdraai vernieuwd met een kalenderjaar- en periodekaart linksboven.
- Zilvergrijze Renault Captur-afbeelding toegevoegd aan de PDF-kop.
- Bedrijfsnaam van de instellingen zichtbaar gemaakt in de kop en voettekst.
- PDF-afbeelding wordt als compacte JPEG meegeleverd voor een snelle uitdraai.

## 5.0.3
- Zilvergrijze Renault Captur (2014) als gegenereerde illustratieve afbeelding op het startscherm.
- Op smalle schermen staat de afbeelding onder de kilometerstand; op brede schermen ernaast.
- Afbeelding wordt meegeleverd in de container en opgenomen in de offlinecache.
- Versie en PWA-cache verhoogd; meldingslinks van 5.0.2 behouden.

## 5.0.2
- Meldingslinks voor stopmeldingen, ritassistent en testmelding verwijzen naar de repository-installatie `675b3933_rit_tank`.
- Versienummer en PWA-cache bijgewerkt. Stopdetectiegedrag blijft gelijk.
- Deze link is specifiek voor deze repository; In zijbalk tonen moet aan staan.

## 5.0.1
- Diagnoselog in Instellingen: ophalen, kopiëren en downloaden als tekstbestand.
- Maximaal 500 gebeurtenissen van de laatste 48 uur, bewaard bij herstart.
- Logt afstandsgrens, beweging, stilstand, provinciecontrole en resultaat van stopmelding.
- Registreert ouderdom van HA-entiteitswijziging; dit is geen exacte GPS-meettijd.
- Geen adressen, coördinaten, kenteken, sleutels of ruwe foutmeldingen in de export.
- Actieve rit bekijken scrolt nu direct naar de actieve rit.
- Stopdetectiegedrag is niet gewijzigd; deze versie verzamelt diagnosebewijs.

## 5.0.0
- Heruitgave van 4.4.1 onder versienummer 5.0.0 op verzoek van de gebruiker.
- Versienummer in add-onconfiguratie, backend en PWA-cache bijgewerkt.
- Bestaande functies, add-onidentiteit en gegevensopslag behouden.

## 4.4.1
- Overijssel toegevoegd aan provincies voor stopmeldingen.
- Actieve rit: na 10 seconden gedetecteerde stilstand in Overijssel verschijnt bij geopende app de vraag of je de rit wilt opslaan.
- Bevestigen opent de bestaande controle- en afsluitstappen; geen automatische opslag.
- Achtergrond gebruikt de bestaande HA-meldingsservice. Locatie-updates bepalen de daadwerkelijke reactietijd.
- Groningen en Drenthe behouden de ingestelde vertraging; minimale ritafstand blijft gelden.

## 4.4.0
- Aankomstscherm met route, grote voorgestelde tellerstand, ritsoort, Alles akkoord en Aanpassen.
- Bekend vertrek en aankomst bouwen nu een GPS-routeconcept op zonder officiële registratie; bevestiging blijft nodig.
- Concept onderweg wordt op het dashboard getoond; zichtbare dashboards verversen iedere 30 seconden, niet tijdens invoer.
- Routevoorstellen worden bij aankomst vastgelegd; onderbroken/schaarse GPS-data geven handmatige controle.
- Kilometerleren per kenteken (of voertuignaam) op basis van expliciet gecontroleerde vertrek- en eindstanden.
- Minimaal vijf stabiele trajecten van minimaal 5 km; mediaan van maximaal 30 voorbeelden, correctie begrensd op ±10%.
- Gewoon Akkoord drukken traint het model niet; kilometerleren kan worden uitgezet zonder historie te wissen.
- Controle op dubbele bevestiging, overlappende ritten en oudere voorstellen bij een nieuwere actieve rit.
- Actieve rit kan bij aankomst expliciet worden afgesloten; bestaande tankbon- en Drive-functies blijven behouden.

## 4.3.1
- **Volgend adres** en **Rit afsluiten** tonen nu eerst groot de actueel berekende kilometerstand.
- Het voorstel wordt bij iedere klik opnieuw opgehaald uit de meest recente GPS-route.
- Met **Akkoord** ga je direct door; met **Wijzigen** verschijnt het kilometerwiel om de stand te corrigeren.
- Bij onvoldoende GPS-data schakelt de app duidelijk terug naar handmatige invoer.

## 4.3.0
- Drie autonomieniveaus toegevoegd: Handmatig, Assistent en Autopilot.
- Autopilot classificeert bekende routes automatisch vanaf een instelbare betrouwbaarheid.
- Nieuwe **Te controleren**-inbox toont route, voorstel, betrouwbaarheid en classificatiebron.
- Slimme tankbon leest lokaal liters, prijs per liter, totaalbedrag, datum en tankstation uit een foto.
- Herkende bongegevens worden vooringevuld maar blijven altijd corrigeerbaar vóór opslaan.
- Dagelijkse versleutelde Google Drive-back-up toegevoegd met handmatige testknop en bewaartermijn.
- Back-ups bevatten de consistente SQLite-database, tankbonnen en herstelmetadata.
- Drive-back-ups gebruiken AES-256-GCM en een apart back-upwachtwoord; service-accountgegevens worden niet meegekopieerd.

## 4.2.0
- Volledig vernieuwd premium iPhone-dashboard met donkere automotive-uitstraling.
- Grote kilometerkaart met voertuig, verbruik en afstand sinds de laatste volle tank.
- **Rit starten** is de centrale hoofdactie en verandert bij een actieve rit in **Actieve rit bekijken**.
- Nieuwe vaste ondernavigatie voor Overzicht, Ritten, Tanken en Meer.
- Snelle acties voor tankbeurt en kilometerstand opnieuw ontworpen met duidelijke pictogrammen.
- Statistieken, grafieken, kaarten, modals en invoervelden visueel geharmoniseerd.
- Safe-area, Dynamic Island, kleine iPhones en standalone-weergave blijven ondersteund.
- Alle bestaande tank-, rit-, GPS-, Home Assistant-, export- en beveiligingsfuncties blijven behouden.

## 4.1.0
- Echte standalone-modus toegevoegd naast Home Assistant Ingress.
- Eigen wachtwoordlogin met ondertekende, Secure, HttpOnly en SameSite=Strict sessiecookie.
- Sessies verlopen instelbaar na 1–90 dagen en worden ongeldig zodra het wachtwoord verandert.
- Rate limiting toegevoegd na herhaalde foutieve inlogpogingen.
- Alle gegevens-, mutatie- en exportroutes vereisen authenticatie buiten Ingress.
- Same-origin-controle toegevoegd aan alle wijzigende standalone-aanvragen.
- Directe standalone-toegang zonder HTTPS wordt geweigerd.
- Poort 8099 kan optioneel in Home Assistant worden gepubliceerd; standaard blijft hij gesloten.
- Uitlogknop en sessieverloopafhandeling toegevoegd aan de PWA.
- Bestaande SQLite-database en Home Assistant-functies blijven behouden.

## 4.0.0
- Rit & Tank is nu een installeerbare PWA met manifest, eigen app-icoon en standalone/fullscreen-weergave.
- iPhone safe-area-ondersteuning toegevoegd voor notch en Dynamic Island.
- Service worker toegevoegd voor een offline beschikbare app-interface en beheerste cache-updates.
- Installatiehulp en PWA-status toegevoegd aan Instellingen.
- Online/offline-status zichtbaar gemaakt; na herstel worden actuele gegevens opnieuw geladen.
- App-snelkoppelingen toegevoegd voor een nieuwe rit en tankbeurt.
- Nieuwe toekomstvaste API-aliases toegevoegd: `/api/trips/*`, `/api/stats/*` en `/api/export/pdf`.
- Home Assistant blijft de achtergrondlocatie en mobiele meldingen verzorgen; de eigen backend blijft eigenaar van ritten, tankbeurten en historie.
- Bestaande 3.6-database en alle geregistreerde gegevens blijven behouden.

## 3.6.0
- Achtergrond-GPS telt tijdens een actieve rit de afgelegde route sinds de laatste vastgelegde stop op.
- Bij **Rit afsluiten** wordt de eind-kilometerstand automatisch voorgesteld als: laatste echte tellerstand + geschatte achtergrondroute.
- De schatting is zichtbaar in de ritkaart én in het afsluitvenster, maar blijft bewust corrigeerbaar met het kilometer-scrollwheel.
- Nieuwe snelle stopmelding na standaard **30 seconden stilstand** (best-effort, afhankelijk van iPhone/Companion locatie-updates).
- Snelle stopmeldingen én automatische aankomstpushes worden uitsluitend verstuurd als Google Geocoding de stop in **Groningen** of **Drenthe** plaatst.
- Pushmelding toont geschatte afstand en voorgestelde eind-kilometerstand en opent Rit & Tank direct.
- GPS-jitter wordt gefilterd; onrealistische sprongen en onnauwkeurige locaties tellen niet mee voor de afstandsschatting.
- Na een handmatig opgeslagen tussenstop wordt de achtergrondafstand opnieuw vanaf dat kilometerpunt opgebouwd.
- Polling van de Home Assistant tracker gebeurt maximaal iedere 10 seconden; de werkelijke reactiesnelheid blijft afhankelijk van hoe snel iOS/Companion nieuwe GPS-data publiceert.
- Bestaande 3.5 database en historie blijven behouden.

## 3.5.0
- Nieuwe achtergrond-ritassistent via Home Assistant `person` / `device_tracker`.
- Bekende plekken kunnen automatisch als **passieve Home Assistant-zones** worden gesynchroniseerd.
- Pushmelding bij gedetecteerde aankomst met acties **Privé** en **Zakelijk**.
- Luistert via de Home Assistant WebSocket API naar `mobile_app_notification_action`.
- Nieuwe inbox met nog te verwerken automatisch herkende ritten.
- Kilometerstand blijft een expliciete controle voordat een gedetecteerde rit definitief wordt opgeslagen.
- Experimentele herkenning van onbekende stops na minimale ritafstand + instelbare stilstandtijd.
- Statusweergave voor huidige plek, laatste achtergrondlocatie, zonesynchronisatie en WebSocket-verbinding.
- Testknop voor mobiele melding en knop om bekende plekken opnieuw met HA-zones te synchroniseren.
- Bestaande 3.4 database en historie blijven behouden.

## 3.4.0
- Bekende plekken met GPS, herkenningsradius en eigen regels.
- Classificatie gebeurt per traject/etappe in plaats van alleen per complete rit.
- Slim voorstel Zakelijk/Privé op basis van bestemming, vertrekplek en geleerde routes.
- Terugkerende routes worden onthouden na bevestiging.
- CSV en fiscale PDF bevatten ritsoort per etappe.
