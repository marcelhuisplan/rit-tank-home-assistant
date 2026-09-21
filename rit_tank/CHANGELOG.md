# Changelog

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
