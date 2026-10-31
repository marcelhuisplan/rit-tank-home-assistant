# Rit & Tank PR #56 — veilige Home Assistant-releasevoorbereiding

**Datum voorbereiding:** 31 oktober 2026
**Status:** alleen voorbereidingsplan, **NIET installeren of uitvoeren**.
**PR:** https://github.com/marcelhuisplan/rit-tank-home-assistant/pull/56
**Gecontroleerde feature-HEAD:** `b7afd736849aa07d5dbb74386a5bd4f8e1135ee7` (vóór toevoeging van deze checklist)
**Basis main:** `8a4b991bd7a81f59c866bcf46cf188167ddb6acd`; runtimeversie `34.02`.
**CI:** https://github.com/marcelhuisplan/rit-tank-home-assistant/actions/runs/41233421390 (Python, Docker, mobiele regressies geslaagd).

## Scope en autorisatie

- Gebruik uitsluitend de **bestaande lokale Home Assistant-add-on** en de officiële releasebestanden uit `rit_tank/` op de exact vrijgegeven commit van PR #56.
- **Geen** Railway, andere hosting, extra accounts, nieuwe live preview of noodzaak voor Codespaces/VS Code/terminalgebruik door de eigenaar.
- De aparte `preview/oil/`-testbestanden zijn **geen** onderdeel van de productie-add-on: `rit_tank/Dockerfile` kopieert deze map niet. Installeer nooit een preview-Dockerfile in productie.
- PR #54 en #55 blijven buiten deze release tenzij afzonderlijk goedgekeurd. PR #56 staat nog op Draft en is niet gemerged.
- De huidige 34.02-releaseversie blijft onveranderd tot expliciete toestemming om een releaseversie te kiezen en te publiceren. Het versienummer wordt pas bij releasevrijgave gelijktijdig bijgewerkt in de applicatie, add-onconfiguratie, documentatie, tests, CI en PWA-caches.
- **Geen acties in de echte Home Assistant-installatie zonder nieuwe, afzonderlijke toestemming.**

## STOP-gate 1: vaststellen wat werkelijk geïnstalleerd is

De gekoppelde GitHub-repository zegt niets over de versie die momenteel in Home Assistant draait. **Controleer vóór verdere uitvoering daadwerkelijk**:

1. Installatietype: Home Assistant OS met Supervisor / lokale apps-map, of een andere installatievariant; bepaal de werkelijk geïnstalleerde Rit & Tank-add-onversie, app-/PWA-versie en huidige add-onstatus.
2. Bevestig dat het pad `/addons/rit_tank` de bestaande lokale add-on is; maak **geen** tweede addon met dezelfde slug aan.
3. Bevestig dat de bestaande administratiedatabase wordt opgeslagen op de add-on-dataopslag (`/data/rit_tank.db`) en dat die opslag **niet** vervangen gaat worden.
4. Noteer voor vergelijking (alleen in de veilige beheeromgeving, zonder gegevens naar publieke diensten te sturen): actuele tellerstand, aantal ritten en stops, aantal tankbeurten, aanwezigheid van recente en historische bonnen, ingestelde kilometervergoeding en kenteken/voertuignaam. Bewaar geen persoonsgegevens in GitHub.

**Als de geïnstalleerde versie anders is dan 34.02, of het pad/de add-on anders werkt dan verwacht: STOP.** Eerst installatie- en upgradeverschil vaststellen.

## STOP-gate 2: herstelbare back-up, VÓÓR release of installatie

In Home Assistant **Instellingen → Systeem → Back-ups → Back-up nu → Handmatige back-up**:

1. Selecteer een volledige back-up, of controleer expliciet dat minimaal **Home Assistant-instellingen**, **Lokale apps-map** (broncode onder `/addons`) en **Apps → Rit & Tank inclusief gegevens** geselecteerd zijn. Neem waar nodig Share/Media of andere lokaal afhankelijke onderdelen mee. De standaard aanbevolen automatische back-up kan de lokale apps-map uitsluiten.
2. Controleer na voltooiing in het back-updetail dat de juiste add-on en lokale apps-map **daadwerkelijk zijn opgenomen**, zonder fouten of ontbrekende onderdelen. Een begonnen back-up is niet automatisch een geslaagde back-up.
3. Bewaar een kopie **buiten dezelfde Home Assistant-host**, plus de benodigde Home Assistant-back-up-encryptiesleutel / emergency kit. Niet naar GitHub uploaden.
4. Alleen wanneer reeds geconfigureerd: maak aanvullend een Rit & Tank `.rtbackup` op de bekende Drive-locatie. Dit archief bevat database en tankbonbestanden, maar **niet** het volledige HA-systeem en **niet** `options.json`: dus nooit als enige terugvalroute beschouwen.
5. Controleer dat de belangrijkste paden onder de bestaande add-on-dataopslag worden meegenomen: `rit_tank.db` (inclusief SQLite WAL), `options.json`, `receipts/`, `receipt_archive/`, `administration_backups/`, `backups/` en benodigde sessie-/configuratiebestanden. Bevestig ook de broncode in de lokale apps-map.

**Geen back-up, geen bevestigde inhoud, geen ontsleutelingssleutel of onvoldoende opslagruimte: STOP, niet installeren.**
Een daadwerkelijke hersteltest op de echte Home Assistant-installatie of een tweede HA OS-apparaat is nog **niet** uitgevoerd. Fictieve SQLite-/rtbackup-tests zijn eerder geslaagd, maar vormen geen bewijs van een succesvolle Supervisor-herstelprocedure.

Officiële richtlijn: https://www.home-assistant.io/common-tasks/general/backups/

## STOP-gate 3: expliciete releasevrijgave

1. Vergelijk de actuele PR #56-head opnieuw met `main`; geen onverwachte commits of conflicten; bepaal hoe PR #54/#55 door een andere merge de basis eventueel hebben veranderd.
2. Kies pas dan, met expliciete goedkeuring, een uniek nieuwe versienummer. `main` is nu 34.02; PR #55 beoogt 34.03 maar is nog niet gemerged. **Geen nummer automatisch reserveren.**
3. Vóór vrijgave: update en controleer versieconstanten in app, `config.yaml`, README, changelog, tests, CI en PWA-cache; officiële CI volledig groen op de definitieve releasecommit.
4. Maak het vrijgavepakket **uitsluitend** uit `rit_tank/`. Bij de daadwerkelijke lokale add-onupdate moeten zowel code als de door Home Assistant herkende `config.yaml`-versie consistent zijn; alleen bestanden in GitHub publiceren installeert niets op het apparaat.
5. Merge, release of pakketoverdracht pas na **afzonderlijke** toestemming.

## Na afzonderlijke toestemming: gecontroleerde lokale update

1. Een laatste gecontroleerde back-up maken en de bestaande add-on stoppen; zorg dat een actieve rit afgerond is.
2. Bewaar de actuele oude add-onbroncode en de herstelmogelijkheid. Werk uitsluitend `/addons/rit_tank` bij met het goedgekeurde releasepakket; **raak de bestaande add-on-dataopslag `/data` niet aan**.
3. Laat Home Assistant de lokale app-repository opnieuw controleren en **update de bestaande add-on**, niet verwijderen of als nieuwe administratie installeren. Herstart en controleer via de add-on UI en het appgezondheidseindpunt de herkenbare nieuwe versie.
4. Verifieer de migratie naar `oil_maintenance` zonder oude tabellen te herschrijven. Geen `Nieuwe administratie starten` / RESET.
5. Vergelijk read-only met de vastgelegde beginsituatie: ritten, stops, liters, tankbonnen, odometer, kilometervergoeding, instellingen en oudere bonnen. Nieuwe oliebeurten mogen alleen in de afzonderlijke onderhoudstabel voorkomen.
6. Test op fysieke iPhone **Safari** en bestaande **PWA**: olie bijvullen, huidige datum, 5W-30, 300 ml; teruglezen, wijzigen naar 350 ml, verwijderen na bevestiging; gewone actuele tellerstand mag niet veranderen. Bij geïnstalleerde PWA controleren dat de nieuwe versie zichtbaar is (serviceworker-cache/verversing).
7. Bij elke fout: stop verder registreren en leg eerst de actuele toestand en terugvalstrategie vast.

## Terugval zonder verlies

- **Voorkeursroute bij alleen UI-/codeprobleem:** stop de add-on, herstel de exact bewaarde vorige **app-code** en de vorige appversie, maar **behoud de actuele SQLite-database en bonbestanden**. De geïsoleerde test bevestigde dat de oude 34.02-runtime de nieuwe olietabel kan negeren zonder gegevens te wissen.
- **Alleen als database/gegevens beschadigd raken:** herstel de vooraf gecontroleerde Home Assistant-back-up met de juiste onderdelen; dit **overschrijft** de geselecteerde huidige onderdelen. Alle wijzigingen sinds die back-up gaan verloren tenzij ze vooraf afzonderlijk veiliggesteld zijn. Niet automatisch herstellen.
- Back-ups en broncode bewaren totdat de iPhone- en administratieacceptatie voltooid zijn.

Officiële herstelrichtlijn: https://www.home-assistant.io/common-tasks/general/backups/

## Huidige uitvoeringsstatus

- **GitHub-analyse:** gedaan.
- **Fictieve migratie-, versleutelde herstel-, oude-runtime-, Docker- en mobiele regressietests:** afgerond en groen op eerdere commits.
- **Back-up in de echte HA-installatie:** NIET uitgevoerd.
- **HA-versie en actuele administratie direct geverifieerd:** NIET mogelijk met alleen GitHub; nog te controleren.
- **Nieuwe releaseversie, merge, installatie, HA-reset en rollback:** NIET uitgevoerd.
- **Volgende noodzakelijke stap:** toegang tot de bestaande Home Assistant UI voor een **alleen-lezen** inventarisatie of één eenvoudige statusbevestiging via de eigenaar. Pas later een afzonderlijke toestemming om back-ups en de update uit te voeren.
