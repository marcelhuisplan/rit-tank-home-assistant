# Rit & Tank 5.0 — stopmelding in Overijssel

Versie 5.0.0 bevat dezelfde functies als 4.4.1, onder het aangevraagde nieuwe versienummer. Pak de map `rit_tank` uit naar `/addons/rit_tank` en vervang de bestaande bronbestanden. Het configuratiebestand moet direct op `/addons/rit_tank/config.yaml` staan. Vernieuw daarna de lokale app-store met Controleren op updates. Alleen de ZIP downloaden of de add-on herstarten installeert de update niet.

## Overijssel

Bij een actieve rit verschijnt na 10 seconden **gedetecteerde** stilstand de vraag **Actieve rit opslaan?**. De geopende app controleert nieuwe stopvoorstellen iedere 2 seconden en onderbreekt geen open invoerscherm. Bevestigen opent de teller- en locatiecontrole; je slaat daarna zelf de rit op. Nee sluit alleen de vraag.

Als de PWA gesloten is, gebruikt de backend de ingestelde Home Assistant-meldingsservice. Er wordt geen gesloten Safari-app geforceerd geopend. De bestaande koppeling opent via `/local_rit_tank`; open zo nodig zelf je PWA. De pop-up wordt maximaal twee minuten na detectie aangeboden. GPS-updates en de backendcontrole (circa 10 seconden) kunnen extra vertraging veroorzaken.

De ritassistent moet aan staan in Assistent/Autopilot met je tracker geselecteerd. De ingestelde minimale ritafstand blijft gelden. Google Geocoding moet de provincie herkennen. Groningen en Drenthe behouden hun ingestelde stopvertraging. Test Overijssel op een veilige parkeerplek; 10 seconden kan ook bij verkeerslichten voorkomen.

Versie 4.4 voegt het aankomstscherm, GPS-routeconcepten en persoonlijke kilometercorrectie toe. De bestaande SQLite-database, Ingress-weergave, tankbonherkenning, Drive-back-up en Home Assistant-koppeling blijven behouden.

## Nieuwe functies testen

1. Zet de ritassistent aan, kies **Assistent** en je bestaande Home Assistant-locatietracker. Voeg minstens Thuis en een bestemming toe bij Bekende plekken.
2. Rijd zonder handmatig een registratie te starten van een bekende plek naar de bestemming. De backend houdt vertrek en route als concept bij. Je kunt **Concept onderweg** zien in het rittenoverzicht.
3. Na bevestigde aankomst verschijnt een voorstel in **Te controleren**. Kies **Controleren →**. Bij voldoende GPS-metingen toont het aankomstscherm een grote voorgestelde tellerstand.
4. Kies de ritsoort als deze onbekend is. **Alles akkoord** slaat het voorstel op; **Aanpassen** opent vertrekstand en kilometerwiel. Bij een bestaande actieve rit kun je expliciet **Ook mijn actieve ritregistratie afsluiten** aanvinken.
5. Vink alleen na vergelijking met de echte teller **vertrek- én aankomststand gecontroleerd** aan. Dit kan ook bij de handmatige Volgend adres-stap. Enkel een GPS-voorstel accepteren levert geen leervoorbeeld op.

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

## Autopilot instellen

Open **Rit & Tank → Meer → Instellingen**:

1. Zet **Ritassistent actief** aan.
2. Kies de iPhone `person` of `device_tracker` en de mobiele meldingsservice.
3. Kies **Assistent** om alle routes te controleren, of **Autopilot** om zekere routes automatisch als Privé/Zakelijk te classificeren.
4. Begin met een Autopilotgrens van **95%**.

Autopilot vult alleen de classificatie in. De rit blijft in **Te controleren** staan totdat de kilometerstanden compleet zijn. Alle automatische beslissingen komen in het wijzigingslogboek.

## Slimme tankbon

Kies bij een tankbeurt een scherpe foto van de bon. De add-on voert OCR volledig lokaal uit en probeert liters, literprijs, totaalbedrag, datum en tankstation te herkennen. Controleer de voorgestelde waarden vóór opslaan. De foto wordt pas bij **Tankbeurt opslaan** definitief in `/data/receipts` bewaard.

## Automatische Google Drive-back-up

Gebruik bij Google Workspace bij voorkeur een map in een **Gedeelde Drive**. Maak in Google Cloud een afzonderlijk service-account, activeer de Google Drive API, download de JSON-sleutel en voeg het service-account als lid/toegangsgerechtigde toe aan de doelmap.

Stel daarna in de Home Assistant add-onconfiguratie in:

```yaml
google_drive_enabled: true
google_drive_folder_id: "MAP-ID-UIT-DE-DRIVE-URL"
google_drive_service_account_json: '{"type":"service_account", ... }'
backup_encryption_password: "een-uniek-wachtwoord-van-minimaal-12-tekens"
backup_hour: 3
backup_retention_days: 30
```

Herstart de add-on en gebruik onder **Meer → Instellingen** de knop **Nu back-up maken**. Als deze test slaagt, maakt Rit & Tank dagelijks vanaf het gekozen uur één back-up en verwijdert het eigen oudere Drive-bestanden na de bewaartermijn.

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
