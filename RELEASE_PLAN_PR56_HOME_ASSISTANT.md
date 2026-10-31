# Rit & Tank PR #56 — veilige Home Assistant-releasevoorbereiding

**Datum voorbereiding:** 31 oktober 2026
**Status:** vrijgavevoorbereiding; nog **NIET** mergen, installeren of releasen.
**PR:** https://github.com/marcelhuisplan/rit-tank-home-assistant/pull/56
**Laatst bevestigde bronstand vóór dit besluitvoorstel:** `f9be3f4aae2bdfc7211d9132d434cefe4cc0d756` (volledige CI geslaagd; zie onder).
**Basis main:** `8a4b991bd7a81f59c866bcf46cf188167ddb6acd`; runtimeversie `34.02`.
**CI:** https://github.com/marcelhuisplan/rit-tank-home-assistant/actions/runs/41240527592 (Python, Docker, mobiele regressies geslaagd).

## Scope en autorisatie

- Gebruik uitsluitend de **bestaande lokale Home Assistant-add-on** en de officiële releasebestanden uit `rit_tank/` op de exact vrijgegeven commit van PR #56.
- **Geen** Railway, andere hosting, extra accounts, nieuwe live preview of noodzaak voor Codespaces/VS Code/terminalgebruik door de eigenaar.
- De eerder voorbereide externe hosting-preview is op verzoek van de eigenaar uit de ontwikkelbranch gehaald. De productie-add-on wordt uitsluitend uit `rit_tank/` samengesteld.
- PR #54 en #55 blijven buiten deze release tenzij afzonderlijk goedgekeurd. PR #56 staat nog op Draft en is niet gemerged.
- De huidige 34.02-releaseversie blijft onveranderd tot expliciete toestemming om een releaseversie te kiezen en te publiceren. Het versienummer wordt pas bij releasevrijgave gelijktijdig bijgewerkt in de applicatie, add-onconfiguratie, documentatie, tests, CI en PWA-caches.
- **Geen acties in de echte Home Assistant-installatie zonder nieuwe, afzonderlijke toestemming.**

## Voorstel releasevolgorde (besluit van eigenaar nog vereist)

**Aanbevolen:** olieonderhoud uit Draft PR #56 als **34.03** als de eerstvolgende wijzigingsrelease. De ongepubliceerde PR #55 is alleen in de **titel** aangeduid als 34.03: in zijn eigen `app.py` en `config.yaml` staat nog **34.02**. Er is dus nog geen officieel tweede 34.03-pakket. PR #55 blijft volledig ongemoeid.

- **Route A — voorkeur wegens gewenste snelheid:** eerst olieonderhoud **34.03** uit PR #56; pas later, na afzonderlijke toetsing en toestemming, automatische tankstops uit PR #55 als eerstvolgende vrije release (naar verwachting **34.04**). De WIP-titel en versietoekenning van PR #55 mogen **pas met afzonderlijke toestemming** worden aangepast, niet in deze opdracht.
- **Route B — alternatief:** eerst automatische tankstops uit PR #55 definitief als **34.03**; olieonderhoud uit PR #56 pas daarna als **34.04**. Dit vertraagt de gewenste olieonderhoudsfunctie en vereist een nieuwe integratievergelijking.
- **Geen 34.03-reservering of definitieve nummering zonder akkoord.** De actieve `APP_VERSION`, Home Assistant `config.yaml`, documentatie, tests en CI blijven nu **34.02**. Ook geen PR-merge, tag, release of Home Assistant-update.
- PR #54 (bonviewer) blijft afzonderlijk Draft en mag niet stilzwijgend meeliften.

### Exacte releasevoorbereidingschecklist na nummeringsakkoord

1. **Vastpinnen bron**: vergelijk de exact vrij te geven PR #56-HEAD met `main`; bij wijzigingen of een eerdere merge van PR #54/#55 eerst opnieuw toetsen en niet stilzwijgend combineren. Alleen bestanden uit `rit_tank/` vormen de bestaande lokale Home Assistant-add-on.
2. **Versienummer in één wijzigingsset**: zet `APP_VERSION` in `rit_tank/app.py`, `version` in `rit_tank/config.yaml`, de releasekoppen van `rit_tank/README.md` en `rit_tank/DOCS.md`, de nieuwe changelogsectie in `rit_tank/CHANGELOG.md`, en de expliciete versiecontroles in `.github/workflows/ci.yml` op exact dezelfde, door de eigenaar goedgekeurde release.
3. **Tests afstemmen, geen historie herschrijven**: huidige-versieasserties staan onder meer in `test_700.py`, `test_v44.py`, `test_v3300.py`, `test_v3200.py`, `test_pwa_update_3312.py`, `test_release_recovery_3309.py`, `test_receipt_archive.py` en `test_fuel_receipt_link_3311.py`. Pas alleen actuele-versieasserties aan; behoud historische testscenario's en hun labels.
4. **PWA-caches**: bevestig dat de nieuw afgeleide `SERVICE_WORKER`-cachenaam en de versieparameters voor `oil-history.js`, `receipt-scanner.js` en `receipt-archive.js` overeenstemmen met de goedgekeurde runtimeversie. De oude, vastgepinde **34.02-code-only rollbacktest** in `tools/test_oil_legacy_rollback.py` blijft expres op 34.02 gericht.
5. **Volledige officiële CI**: Python, Home Assistant-Docker-build en mobiele Chromium/WebKit-regressies op de laatste releasecommit; elke fout herstellen voordat de owner release toestemming geeft.
6. **Geen verborgen productieacties**: een succesvolle Draft PR-CI is geen installatie; sluit PR #56 niet af, merge niet en publiceer geen GitHub-release zonder nieuwe expliciete toestemming.

## STOP-gate 1: vaststellen wat werkelijk geïnstalleerd is

De eigenaar heeft op 31 oktober 2026 een screenshot gedeeld waarop de Home Assistant-add-on **Rit & Tank 34.02** met status **Wordt uitgevoerd** zichtbaar is. Dat is een momentopname; controleer vlak voor een update opnieuw. Controleer dan ook:

1. Installatietype: Home Assistant OS met Supervisor / lokale apps-map, of een andere installatievariant; bepaal de werkelijk geïnstalleerde Rit & Tank-add-onversie, app-/PWA-versie en huidige add-onstatus.
2. Bevestig dat het pad `/addons/rit_tank` de bestaande lokale add-on is; maak **geen** tweede addon met dezelfde slug aan.
3. Bevestig dat de bestaande administratiedatabase wordt opgeslagen op de add-on-dataopslag (`/data/rit_tank.db`) en dat die opslag **niet** vervangen gaat worden.
4. Noteer voor vergelijking (alleen in de veilige beheeromgeving, zonder gegevens naar publieke diensten te sturen): actuele tellerstand, aantal ritten en stops, aantal tankbeurten, aanwezigheid van recente en historische bonnen, ingestelde kilometervergoeding en kenteken/voertuignaam. Bewaar geen persoonsgegevens in GitHub.

**Als de geïnstalleerde versie anders is dan 34.02, of het pad/de add-on anders werkt dan verwacht: STOP.** Eerst installatie- en upgradeverschil vaststellen.

## STOP-gate 2: herstelbare back-up, VÓÓR release of installatie

De eigenaar heeft op 31 oktober 2026 gemeld dat een Home Assistant-back-up is gemaakt en **op verzoek bevestigd** dat deze Rit & Tank inclusief gegevens, de lokale apps-map en Home Assistant-instellingen bevat, met een externe kopie en benodigde herstelsleutel. **Dit is een eigenaarverklaring; wij hebben de back-upinhoud, leesbaarheid en daadwerkelijke Home Assistant-terugzetting niet zelfstandig geverifieerd.** Een screenshot is niet vereist, maar vóór de werkelijke update moet de herstelsituatie nogmaals gecontroleerd worden.

In Home Assistant **Instellingen → Systeem → Back-ups → Back-up nu → Handmatige back-up**:

1. Selecteer een volledige back-up, of controleer expliciet dat minimaal **Home Assistant-instellingen**, **Lokale apps-map** (broncode onder `/addons`) en **Apps → Rit & Tank inclusief gegevens** geselecteerd zijn. Neem waar nodig Share/Media of andere lokaal afhankelijke onderdelen mee. De standaard aanbevolen automatische back-up kan de lokale apps-map uitsluiten.
2. Controleer na voltooiing in het back-updetail dat de juiste add-on en lokale apps-map **daadwerkelijk zijn opgenomen**, zonder fouten of ontbrekende onderdelen. Een screenshot is niet nodig: de eigenaar kan dit desgewenst met één korte tekstbevestiging melden.
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
6. Test op fysieke iPhone **Safari** en bestaande **PWA** eerst read-only: zichtbaarheid, toetsenbord, terugnavigatie en bestaande gegevens. **Voer geen fictieve 85.000 km of fictieve 300 ml oliebeurt in op de echte productieadministratie.** Indien er een echte oliebeurt heeft plaatsgevonden, leg die met de echte gegevens vast, lees terug, wijzig alleen na verificatie, en verwijder niet zomaar echte onderhoudshistorie. Alleen in een afzonderlijke testomgeving kunnen de fictieve 300→350 ml en verwijderen-scenario's worden uitgevoerd. De gewone voertuigstand mag niet veranderen door olieonderhoud.
7. Bij elke fout: stop verder registreren en leg eerst de actuele toestand en terugvalstrategie vast.

## Terugval zonder verlies

- **Voorkeursroute bij alleen UI-/codeprobleem:** stop de add-on, herstel de exact bewaarde vorige **app-code** en de vorige appversie, maar **behoud de actuele SQLite-database en bonbestanden**. De geïsoleerde test bevestigde dat de oude 34.02-runtime de nieuwe olietabel kan negeren zonder gegevens te wissen.
- **Alleen als database/gegevens beschadigd raken:** herstel de vooraf gecontroleerde Home Assistant-back-up met de juiste onderdelen; dit **overschrijft** de geselecteerde huidige onderdelen. Alle wijzigingen sinds die back-up gaan verloren tenzij ze vooraf afzonderlijk veiliggesteld zijn. Niet automatisch herstellen.
- Back-ups en broncode bewaren totdat de iPhone- en administratieacceptatie voltooid zijn.

Officiële herstelrichtlijn: https://www.home-assistant.io/common-tasks/general/backups/

## Huidige uitvoeringsstatus

- **GitHub-analyse:** gedaan.
- **Fictieve migratie-, versleutelde herstel-, oude-runtime-, Docker- en mobiele regressietests:** afgerond en groen op eerdere commits.
- **Back-up in de echte HA-installatie:** door eigenaar gemeld als afgerond met bevestigde selectie, externe kopie en herstelsleutel; technische inhouds- en herstelcontrole niet uitgevoerd.
- **HA-versie:** 34.02 en draaiend vastgesteld op eigenaarsscreenshot (31 oktober); back-upinhoud, actuele administratie en versie direct vóór installatie nog te controleren.
- **Nieuwe releaseversie, merge, installatie, HA-reset en rollback:** NIET uitgevoerd.
- **Volgende noodzakelijke stap:** eigenaar kiest expliciet tussen releasevolgorde A (olieonderhoud eerst, voorgesteld 34.03) of B (automatische tankstops eerst, olieonderhoud later 34.04). Pas na dat nummeringsbesluit een releasekandidaat met versieophoging maken; voor merge, releasepublicatie en daadwerkelijke Home Assistant-update is daarna afzonderlijk akkoord vereist.
