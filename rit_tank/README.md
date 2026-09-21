# Rit & Tank 7.00

PDF-ritregistratie staat als grote knop op het beginscherm. Kies maand en jaar, of Heel jaar, en druk op OK. Een jaarrapport bevat alleen maanden met ritten, chronologisch en per maand gegroepeerd. Ritten horen bij hun vertrekmaand, ook als de aankomst in de volgende maand valt. Opgeslagen adressen worden gebruikt zonder externe adresopvraging; ontbrekende adressen worden als coördinaten getoond. Openen/afdrukken, downloaden en delen blijven beschikbaar (delen afhankelijk van apparaat/browser).

Versie 5.0.8 plaatst de scanner alleen bij Tanken, zet liters op één rij, onthoudt de gekozen literprijs en scrolt na adresbevestiging automatisch verder. Nieuwe ritten krijgen standaard het aanpasbare doel 'klantbezoek'.

Ritten, tankbeurten, tellerstandsuggesties en een zelfstandige iPhone-PWA met Home Assistant als achtergrondlocatiebron.

Versie 5.0.7 verbetert tankbonscans, voegt huisnummerkeuze toe, bewaart de locatie-fallback blijvend en biedt PDF-delen en permanente Drive-archivering. Het Huisplan-logo blijft op het inlogscherm, in de websitekop, PDF en app-iconen staan. Zie [de installatie- en Drive-handleiding](UPDATE_5.0.7.md).

iOS-achtergrondlocaties komen op wisselende momenten binnen; een stopmelding precies tien seconden na fysieke aankomst is niet gegarandeerd.

Gebruik je al de lokale app? Lees eerst [`DOCS.md`](DOCS.md) voor de installatie- en update-instructies. Gegevens worden niet automatisch overgezet en de meldingslinks vragen nog aanpassing aan de nieuwe appidentiteit.

Zie `DOCS.md` voor de bestaande appinstellingen en `CHANGELOG.md` voor wijzigingen. Er zijn 18 offline backendtests en JavaScript-logicatests; een installatie- en iPhone-test volgen op het doelapparaat.

## Releasebeleid

- Huidige release: **7.00**.
- Elke door de eigenaar aangevraagde wijzigingsrelease gaat één geheel getal omhoog (bijv. 7.00 → 8.00 → 9.00 → 10.00, enz.). Er zijn geen tussenliggende deelversies (geen 7.10, 7.01, e.d.) binnen dit beleid.
- Volgende release: **8.00**, daarna 9.00, 10.00, enzovoort.
- Dit is een **handmatig** releasebeleid: het versienummer (`APP_VERSION` in `app.py`, `version` in `config.yaml`, de titels in `README.md`/`DOCS.md`) wordt alleen door een expliciete, door de eigenaar aangevraagde wijziging opgehoogd. De applicatie verhoogt dit nummer nooit automatisch tijdens runtime.
