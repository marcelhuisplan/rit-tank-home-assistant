# Rit & Tank 26.00

Release 26.00 gebruikt bij iedere PDF-export de actuele opgeslagen kilometervergoeding. Een wijziging werkt direct bij de volgende export, zonder herstart. Vergoeding per etappe en totaal gebruiken hetzelfde Decimal-tarief met ROUND_HALF_UP. Aantal ritten, zakelijke kilometers, totaalvergoeding en tabelnummering komen uit dezelfde definitieve geëxporteerde rit-/etapperegels, inclusief zichtbare 0-km-regels. PDF-layout en historische ritdata blijven ongewijzigd.

Release 25.00 voegt **🏠 Thuis** toe aan Nieuwe rit: altijd Verenlandweg 4, 7461 AP Rijssen. Typ een adres en tik direct op een zoekresultaat; de knoppen Dicteer adres en Dit adres gebruiken zijn verwijderd. GPS-adreskandidaten blijven direct selecteerbaar. Een locatie kiezen slaat niets op en start geen rit; bij een eindlocatie worden route, afstand en tellerstandsvoorstel ververst. Onder **Instellingen → Zakelijke kilometervergoeding** stel je het bedrag per km in (standaard € 0,25). Komma en punt worden geaccepteerd; de waarde wordt persistent opgeslagen via `km_reimbursement_rate`. Elke nieuwe PDF en CSV gebruikt het actuele tarief en herberekent de vergoeding per rit en het totaal met Decimal ROUND_HALF_UP. Het tarief is een huidige rapportinstelling, geen historisch tarief per rit.

Release 24.00 vereenvoudigt de handmatige zakelijke ritflow. Een actieve rit blijft open totdat je zelf een volgende locatie kiest of de rit afsluit. Een stopmelding is alleen een reminder; er wordt geen rit opgeslagen of reviewitem aangemaakt voor een actieve handmatige rit. De locatiekeuze biedt ook **🏠 Thuis** voor Verenlandweg 4, 7461 AP Rijssen, met route- en tellerstandsuggestie. De reviewsectie, ritsoortkeuze en invoer voor privé-omrijkilometers zijn uit de actuele interface verwijderd. Historische privéritten en assistant-data blijven behouden; er is geen databaseschemamigratie.

Release 22.00 verwerkt ritvoorstellen onder **Te controleren** strikt van oud naar nieuw. Alleen de oudste openstaande suggestie kan worden geclassificeerd, gecorrigeerd, opgeslagen of gesloten; latere voorstellen blijven zichtbaar maar zijn vergrendeld. De server bewaakt die volgorde ook voor oude pagina's, pushmeldingen en deeplinks. Na opslaan of sluiten wordt de volgende kaart direct actief en wordt de tellerstandsuggestie opnieuw uit de actuele geschiedenis berekend. De bestaande overlapcontrole blijft als extra veiligheidsnet behouden; historische ritten worden niet aangepast.

Release 21.00 voegt de kolom **Tellerstand** toe aan de fiscale PDF: per vertrek-
en aankomstadres de opgeslagen `trip_stops.odometer`, met **—** bij een ontbrekende
stand. De afzonderlijke etappeafstand blijft ongewijzigd. Alleen volledige fysieke
adressen worden afgedrukt; anders staat er **Adres ontbreekt**, nooit een
bekende-pleklabel. Lange adressen lopen zonder overlap door op meerdere regels.
De goedgekeurde v3-layout, badges, header en footer blijven behouden.

De applicatie is intern opgesplitst in featuremodules voor PDF, Google Places, routing, Home Assistant, zakelijke ritten en ritassistent.

PDF-ritregistratie staat als grote knop op het beginscherm. Kies maand en jaar, of Heel jaar, en druk op OK. Een jaarrapport bevat de ritten chronologisch, per etappe in één doorlopende tabel. Ritten horen bij hun vertrekmaand, ook als de aankomst in de volgende maand valt. Opgeslagen adressen worden gebruikt zonder externe adresopvraging; ontbrekende volledige adressen worden expliciet als **Adres ontbreekt** getoond. Openen/afdrukken, downloaden en delen blijven beschikbaar (delen afhankelijk van apparaat/browser).

De rittenregistratie-PDF is in release 8.00 volledig opnieuw opgebouwd. Het PDF bevat nu een compacte, efficiënte tabel met één rit per rij, gekleurde badges, Nederlandse notatie en veel meer ritten per pagina. De hele PDF is afgestemd op het goedgekeurde Huisplan-referentieontwerp.

Versie 5.0.8 plaatst de scanner alleen bij Tanken, zet liters op één rij, onthoudt de gekozen literprijs en scrolt na adresbevestiging automatisch verder. Nieuwe ritten krijgen standaard het aanpasbare doel 'klantbezoek'.

Ritten, tankbeurten, tellerstandsuggesties en een zelfstandige iPhone-PWA met Home Assistant als achtergrondlocatiebron.

Versie 5.0.7 verbetert tankbonscans, voegt huisnummerkeuze toe, bewaart de locatie-fallback blijvend en biedt PDF-delen en permanente Drive-archivering. Het Huisplan-logo blijft op het inlogscherm, in de websitekop, PDF en app-iconen staan. Zie [de installatie- en Drive-handleiding](UPDATE_5.0.7.md).

iOS-achtergrondlocaties komen op wisselende momenten binnen; een stopmelding precies tien seconden na fysieke aankomst is niet gegarandeerd.

Gebruik je al de lokale app? Lees eerst [`DOCS.md`](DOCS.md) voor de installatie- en update-instructies. Gegevens worden niet automatisch overgezet en de meldingslinks vragen nog aanpassing aan de nieuwe appidentiteit.

Zie `DOCS.md` voor de bestaande appinstellingen en `CHANGELOG.md` voor wijzigingen. Er zijn 18 offline backendtests en JavaScript-logicatests; een installatie- en iPhone-test volgen op het doelapparaat. Iedere pull request doorloopt automatisch Python-tests, compile- en importchecks, een productie-Docker-build en runtime packaging/importvalidatie in die image.

## Releasebeleid

- Huidige release: **26.00**.
- Elke door de eigenaar aangevraagde wijzigingsrelease gaat één geheel getal omhoog (bijv. 7.00 → 8.00 → 9.00 → 10.00 → 11.00 → 12.00, enz.). Er zijn geen tussenliggende deelversies (geen 7.10, 7.01, e.d.) binnen dit beleid.
- Release 13.00 is bewust overgeslagen.
- Volgende release: **27.00**.
- Dit is een **handmatig** releasebeleid: het versienummer (`APP_VERSION` in `app.py`, `version` in `config.yaml`, de titels in `README.md`/`DOCS.md`) wordt alleen door een expliciete, door de eigenaar aangevraagde wijziging opgehoogd. De applicatie verhoogt dit nummer nooit automatisch tijdens runtime.
