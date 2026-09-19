# Rit & Tank 5.0.7 instellen

Werk de GitHub-versie van Rit & Tank bij via de Home Assistant app-store. Heropen de website/PWA en controleer de versie onder Meer → Instellingen. De database in `/data` blijft behouden. Het Huisplan-logo blijft staan.

## Tankbon en adres

De grote camera bovenaan opent de tankboncamera. Ook het tankformulier begint met de scan. Wacht op de herkenning; liters (twee decimalen) en literprijs (drie decimalen) worden ingevuld. Niet herkende velden blijven op hun bestaande waarde staan met een expliciete melding. Controleer altijd de bon voordat je opslaat. De bonfoto wordt pas samen met de tankbeurt opgeslagen.

Bij een rit of tussenstop haalt **Gebruik huidige locatie** maximaal tien bestaande huisnummers op in de dichtstbijzijnde straat. Kies het adres uit je afspraak. De bron is PDOK/BAG voor Nederland; GPS kan een verkeerde straat aanwijzen en is geen agenda-koppeling. Staat het juiste adres er niet tussen, vul straat, huisnummer en plaats in en tik op **Dit adres gebruiken**. Een handmatig adres behoudt de gemeten GPS-positie; een gekozen BAG-adres gebruikt de adrescoördinaten. Zonder verbinding kun je geen adreslijst ophalen.

De locatie-fallback wordt voortaan in de appdatabase bewaard en geldt voor deze installatie. Een bestaande browserkeuze wordt bij openen eenmalig overgenomen als er nog geen serverkeuze is. Bij wisselen van domein of apparaat vóór die overdracht moet je één keer opnieuw kiezen. Normale updates wissen de keuze niet. Ook een tijdelijk onbereikbare HA-locatielijst wist de opgeslagen keuze niet.

## PDF delen en afdrukken

Tik op **PDF** bij de gewenste periode. Zodra de PDF gereed is:

- **Delen / andere app** opent het deelmenu als de browser bestanden delen ondersteunt.
- **Openen / afdrukken** opent de PDF-viewer. Gebruik daar het printmenu of op iPhone/iPad het deelmenu → Druk af.
- **Downloaden** bewaart een kopie als delen niet wordt ondersteund.
- **Bewaren in Google Drive** archiveert exact deze PDF na het instellen van Drive. De PDF blijft leesbaar en wordt niet automatisch opgeruimd. De appback-up is afzonderlijk versleuteld.

## Dagelijkse back-up onbeperkt bewaren

Gebruik een eigen privémap voor Rit & Tank. Kies één van de Google-aanmeldmethoden hieronder; alleen een Gmail-adres invullen is niet voldoende.

### Gewone Google Drive / Mijn Drive

Hiervoor gebruikt de app gebruikers-OAuth. Service-accounts kunnen geen bestanden bezitten in Mijn Drive. Maak een Google Cloud-project voor eigen gebruik, activeer de Drive API en configureer Google Auth Platform met jezelf als gebruiker. Maak een OAuth-client van het type Webapp met `https://developers.google.com/oauthplayground` als toegestane redirect-URI.

Open [Google OAuth Playground](https://developers.google.com/oauthplayground), kies via het tandwiel **Use your own OAuth credentials**, vul je client-ID en client-secret in en kies offline toegang. Autoriseer `https://www.googleapis.com/auth/drive` met je eigen account. Deze scope geeft brede Drive-toegang; deze bestaande-mapconfiguratie gebruikt geen Google Picker. Wissel de code om voor tokens en gebruik het refresh-token. Gebruik voor langdurige eigen toegang geen OAuth-project dat in externe testmodus blijft staan: zulke refresh-tokens verlopen doorgaans na zeven dagen. Google kan afhankelijk van je account en project aanvullende verificatie vragen.

Vul uitsluitend in Home Assistant → Rit & Tank → Configuratie deze waarde in:

```yaml
google_drive_oauth_json: '{"type":"authorized_user","client_id":"JE_CLIENT_ID","client_secret":"JE_CLIENT_SECRET","refresh_token":"JE_REFRESH_TOKEN","scopes":["https://www.googleapis.com/auth/drive"]}'
google_drive_service_account_json: ''
```

De Google-clientbibliotheek vernieuwt toegangstokens met dit refresh-token. Als je toegang intrekt, moet je opnieuw autoriseren. Deel deze JSON niet in GitHub of screenshots.

### Google Workspace met Gedeelde Drive

Maak een service-account in een Cloud-project met Drive API ingeschakeld. Geef dit account toegang tot de doelmap in een **Gedeelde Drive**, met rechten om bestanden aan te maken (en te verwijderen als je een beperkte bewaartermijn kiest). Vul de gedownloade sleutel in bij `google_drive_service_account_json` en laat `google_drive_oauth_json` leeg. Een gewone map die met het service-account is gedeeld, is geen Gedeelde Drive.

### Instellingen voor beide methoden

Behoud je andere configuratievelden en stel in:

```yaml
google_drive_enabled: true
google_drive_folder_id: "MAP-ID-NA-folders-IN-DE-DRIVE-URL"
backup_encryption_password: "EIGEN-WACHTWOORD-VAN-MINIMAAL-12-TEKENS"
backup_hour: 3
backup_retention_days: 0
```

Sla op, herstart Rit & Tank en kies **Meer → Instellingen → Nu back-up maken**. Controleer zowel de succesmelding als het nieuwe `.rtbackup`-bestand in Drive. Daarna maakt de app dagelijks vanaf 03:00 een back-up; na uitval wordt op de volgende controle opnieuw geprobeerd. `0` betekent dat de app niets automatisch verwijdert. Bestaande installaties met `30` moeten dit zelf eenmaal wijzigen. Drive blijft afhankelijk van beschikbare opslagruimte en geldige autorisatie.

De versleutelde back-up bevat de database (inclusief instellingen), tankbonnen en herstelmetadata. Bewaar het back-upwachtwoord apart: zonder dit wachtwoord is herstel niet mogelijk. Deze appback-up vervangt geen volledige Home Assistant-back-up en bevat niet de add-onopties met Google-inloggegevens. De app biedt nog geen herstelknop; bewaar daarom ook je Home Assistant-back-ups. Eerder gemaakte, ongetagde back-ups worden vanaf deze versie niet automatisch opgeruimd.

## Verificatie en grenzen

28 backendtests en de JavaScript-interactietests zijn geslaagd voor scanwaarden, adresbevestiging, instellingbehoud en bewaartermijnen. De aparte Playwright-browsercontrole kon niet starten: de benodigde browser kon niet worden gedownload. Er is geen toegang tot jouw telefoon, echte tankbon of Google-account. De PDOK-adresopvraag is apart live gecontroleerd. Controleer na installatie één echte bon, een tussenstop, delen/afdrukken op je eigen apparaat en de eerste Drive-back-up.

Tests uitvoeren: `python3 rit_tank/test_v44.py`, `python3 rit_tank/test_v507.py`, `node rit_tank/test_ui_v44.cjs` en `node rit_tank/test_ui_v507.cjs`. Met Playwright en Chromium geïnstalleerd kun je ook `node tools/test_browser_v507.cjs` uitvoeren; daarin zijn externe diensten gesimuleerd.

Bronnen: [Google Drive en service-accounts](https://developers.google.com/workspace/drive/api/guides/about-shareddrives), [Google OAuth](https://developers.google.com/identity/protocols/oauth2), [PDOK API](https://api.pdok.nl/bzk/locatieserver/search/v3_1/ui/).
