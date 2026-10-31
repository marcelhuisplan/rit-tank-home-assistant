# Afzonderlijke iPhone-oliepreview — voorbereide testopstelling

**Status: alleen bestanden in Draft PR #56. Er is geen server, domein of Home Assistant-add-on aangemaakt of gestart.**

## Wat dit voorbereidt

- Gebruikt de echte Rit & Tank-backend en het echte olieonderhoudformulier uit dezelfde featurebranch (dus geen losse HTML-mock-up).
- Een eigen Docker-volume `rit-tank-oil-synthetic-preview-only` op een afzonderlijke testserver; **nooit** het bestaande Home Assistant `/data` mounten.
- Een nieuw, alleen fictief bestand `rit_tank.db`, met testauto, kilometerstanden, één tankbeurt, een fictieve rit met twee stops, een fictieve PDF-tankbon en één oude oliebeurt.
- Het invoerformulier blijft beschikbaar om zelf **vandaag 300 ml 5W-30 bij 85.000 km** in te voeren, te wijzigen naar 350 ml en weer te verwijderen.
- Sensorpublicatie, achtergrond-GPS, zones, meldingen en Drive-back-ups starten **niet** in deze aparte previewserver; geen Home Assistant Supervisor-token of externe API-sleutels.
- Een markercontrole weigert een al gevulde, onbekende datamap. Geen automatische migratie of import van bestaande administratie.

## Beveiliging bij latere plaatsing

De Docker Compose-configuratie bindt de backend alleen aan **127.0.0.1:18099** op de testhost. Dat is **geen openbare URL en nog geen iPhone-URL**.

Voor een echte iPhone-test is een **afzonderlijke, vooraf goedgekeurde testhost** nodig, met:

1. Een unieke HTTPS-hostnaam, anders dan de productie-URL, en een geldig TLS-certificaat.
2. Een HTTPS reverse proxy naar **alleen** de loopbackpoort van de testcontainer; geen directe publieke poort. De proxy moet de echte verbinding beëindigen, eigen `X-Forwarded-Proto: https` en `X-Forwarded-Host` instellen en binnenkomende `X-Ingress-Path`-headers verwijderen.
3. Een apart, lang en uniek testwachtwoord (minimaal 20 tekens), uitsluitend veilig opgegeven bij het starten. Niet in de GitHub-repository opslaan.
4. Toegangsbeperking waar mogelijk, bijvoorbeeld een afzonderlijke private netwerktoegang; zorg dat iPhone Safari en de PWA beide de HTTPS-host kunnen bereiken.
5. Controle dat **geen** echte HA-token, productiegegevens, productie-PDF's, productievolume of productie-domein is gekoppeld.

Deze voorwaarden worden niet vanzelf geregeld door Docker Compose of GitHub Actions. Start de preview niet via HTTP op het publieke internet.

## Veilige acceptatietest op een fysieke iPhone

Open de **nog in te richten HTTPS-test-URL** in Safari, meld je aan met het **aparte** testwachtwoord. Controleer dat er `Fictieve testauto — Olieproef` staat en als voertuigstand 85.000 km. Ga in het overzicht naar **Onderhoud · motorolie**.

1. Kies **＋ Oliebeurt** → **Olie bijvullen**.
2. Kies de huidige datum, `5W-30`, `300` ml, `85000` km en sla op.
3. Heropen de pagina; controleer dat datum, viscositeit, 300 ml en 85.000 km behouden blijven.
4. Open **Wijzigen**, verander naar 350 ml, sla op en controleer opnieuw.
5. Verwijder één keer met **Annuleren**: registratie blijft staan. Verwijder daarna met **Bevestigen**.
6. Controleer de gewone voertuigstand, tankbeurt en testrit: die mogen niet veranderen door olieonderhoud.
7. Controleer sluiten, scrollen en iOS-toetsenbord in Safari. Gebruik daarna **Deel → Zet op beginscherm** voor een **afzonderlijke** test-PWA en herhaal. Gebruik een aparte HTTPS-origin zodat de PWA-cache niet met productie wordt vermengd.

Geen van deze handelingen behoort op de bestaande productie-app te gebeuren.

## Opzet voor een toekomstige goedgekeurde uitvoering via ChatGPT Work

De startbestanden staan onder `preview/oil/`. De technicus/Work-agent kan na jouw toestemming op een **aparte testhost**:

- De bestanden uit exact de goedgekeurde PR-commit gebruiken, geen code van willekeurige branches.
- Alleen het lege, aparte Docker-volume aanmaken en een uniek previewwachtwoord veilig instellen.
- De Compose-app starten, authenticatie en dataseparatie controleren.
- De aparte HTTPS-toegang inrichten en je de beveiligde test-URL geven.
- Na jouw test de testhost/container opruimen, zonder aan de productie-app te komen.

Er bestaat nog **geen** werkend testadres. Er is geen hostingaccount gekozen, geen testserver gestart en geen Home Assistant aangeraakt. Goedkeuring voor een specifieke testhost en HTTPS-expositie is verplicht vóór activering.

## Grenzen en herstel

De testdata zijn verzonnen en mogen weg. Bij het opnieuw starten blijven ze in het eigen preview-volume; de app weigert een ongemarkeerd, niet-leeg volume. De productieversie blijft `34.02`. PR #56 blijft Draft, PR #54 en #55 worden niet aangepast.
