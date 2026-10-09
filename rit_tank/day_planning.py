"""Recognize screenshot visit order and safely import separate all-day Calendar events.

No trip, odometer, fuel or other existing appointments are ever modified.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import subprocess
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

SCOPES = ('https://www.googleapis.com/auth/calendar.events',
          'https://www.googleapis.com/auth/calendar.calendarlist.readonly')
MONTHS = {'januari': 1, 'februari': 2, 'maart': 3, 'april': 4, 'mei': 5,
          'juni': 6, 'juli': 7, 'augustus': 8, 'september': 9,
          'oktober': 10, 'november': 11, 'december': 12}
WEEKDAYS = ('maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag')
ROW = re.compile(r'(?<!\d)(\d{1,2})\s+(ochtend|middag)\b', re.I)
ADDRESS = re.compile(
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ’' .\-]{1,65}?(?:straat|weg|laan|heerd|plein|dijk|kade|singel|hof|pad|gracht|baan|boulevard))"
    r"\s+(\d{1,4}\s?[A-Za-z]?)\s*,\s*([A-Za-zÀ-ÿ’'\-][A-Za-zÀ-ÿ’'\- ]{1,45})", re.I)
PROPERTY = re.compile(r'\b(?:[hl]oekwoni|tussenwo|twee-ond|vrijstaand|vragenlijs|status|km\b|min\b)', re.I)
DRAFTS: dict[str, tuple[float, str, dict]] = {}
LOCK = threading.RLock()


def _date(text: str) -> str:
    candidates = set()
    for m in re.finditer(r'\b(?:(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\s+)?'
                         r'(\d{1,2})\s+(januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\s+(20\d{2})\b', text, re.I):
        try:
            parsed = date(int(m[4]), MONTHS[m[3].lower()], int(m[2]))
            if m[1] and WEEKDAYS[parsed.weekday()] != m[1].lower():
                continue
            candidates.add(parsed.isoformat())
        except ValueError:
            pass
    for m in re.finditer(r'(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)', text):
        try:
            candidates.add(date(int(m[3]), int(m[2]), int(m[1])).isoformat())
        except ValueError:
            pass
    return next(iter(candidates)) if len(candidates) == 1 else ''


def _address(line: str) -> tuple[str, bool]:
    match = ADDRESS.search(line)
    if not match:
        return '', True
    consumed = PROPERTY.split(match[3], 1)[0]
    city = consumed.strip(' ,.-')
    street = re.sub(r'\s+', ' ', match[1]).strip()
    number = re.sub(r'\s+', ' ', match[2]).strip()
    after_city = line[match.start(3) + len(consumed):]
    uncertain = bool(re.match(r'\s*(?:\.+|…)', after_city))
    if len(city) < 3:
        uncertain = True
    # Incomplete place names are never completed from outside sources.
    return f'{street} {number}, {city}'.strip(), uncertain


def parse_ocr(header: str, rows: str) -> dict:
    day = _date(header)
    visits, seen = [], set()
    for line in rows.splitlines():
        match = ROW.search(line)
        if not match:
            continue
        order = int(match[1])
        if order in seen or order > 30:
            continue
        seen.add(order)
        tail = line[match.end():].strip()
        address, uncertain = _address(tail)
        visits.append({'source_id': order, 'section': match[2].lower(),
                       'address': address, 'uncertain': uncertain,
                       'excerpt': tail[:140] if uncertain else ''})
    visits.sort(key=lambda x: x['source_id'])
    gaps = bool(visits and [v['source_id'] for v in visits] != list(range(1, len(visits) + 1)))
    return {'date': day, 'date_uncertain': not bool(day), 'visits': visits,
            'warnings': (['Mogelijk ontbreken bezoeken in de OCR; controleer de screenshot.'] if gaps else [])}


def _run_ocr(image, *, crop: bool) -> str:
    from PIL import ImageOps
    with tempfile.TemporaryDirectory(prefix='rit_day_ocr_') as folder:
        source = image
        if crop:
            w, h = source.size
            source = source.crop((int(w * .205), int(h * .46), int(w * .594), int(h * .976)))
        prepared = source.convert('RGB') if crop else ImageOps.autocontrast(ImageOps.grayscale(source))
        if prepared.width < 1100:
            factor = min(3, max(1, (1100 + prepared.width - 1) // prepared.width))
            prepared = prepared.resize((prepared.width * factor, prepared.height * factor))
        path = Path(folder) / 'plan.png'
        prepared.save(path)
        try:
            result = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'nld+eng', '--psm', '6'],
                                    capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError('OCR is niet beschikbaar; probeer het later opnieuw.') from exc
        if result.returncode:
            raise ValueError('OCR kon de screenshot niet lezen.')
        return result.stdout.decode('utf-8', errors='replace')


def preview_image(data_url: str, binding: str) -> dict:
    from PIL import Image, ImageOps, UnidentifiedImageError
    match = re.fullmatch(r'data:image/(?:jpeg|png|webp);base64,([A-Za-z0-9+/=]+)', str(data_url))
    if not match or len(match[1]) > 16 * 1024 * 1024:
        raise ValueError('Upload een PNG/JPEG/WebP-screenshot van maximaal 12 MB.')
    try:
        raw = base64.b64decode(match[1], validate=True)
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError('Screenshot te groot.')
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > 32_000_000:
                raise ValueError('Screenshot bevat te veel pixels.')
            image = ImageOps.exif_transpose(opened).copy()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError('Geen geldige of leesbare screenshot.') from exc
    try:
        header = _run_ocr(image, crop=False)
        rows = _run_ocr(image, crop=True) if image.width / image.height >= 1.15 else header
        result = parse_ocr(header, rows)
    finally:
        image.close()
    if not result['visits']:
        raise ValueError('Geen bezoeken herkend; gebruik een duidelijke screenshot.')
    token = secrets.token_urlsafe(32)
    with LOCK:
        now = time.monotonic()
        for old in list(DRAFTS):
            if DRAFTS[old][0] < now:
                del DRAFTS[old]
        DRAFTS[token] = (now + 1800, binding, result)
    return {'draft_token': token, **result}


def validated_visit(address: str) -> str:
    value = re.sub(r'\s+', ' ', str(address)).strip()
    if not 5 <= len(value) <= 200 or re.search(r'[\n\r<>]|\.{2,}|…', value) or not ADDRESS.fullmatch(value):
        raise ValueError('Gebruik bij ieder bezoek een volledig adres: straat en huisnummer, plaats.')
    return value


def validate_submission(payload: dict, binding: str) -> tuple[str, list[str], str]:
    if payload.get('confirmed') is not True:
        raise ValueError('Bevestig eerst expliciet de agenda-import.')
    token = str(payload.get('draft_token') or '')
    with LOCK:
        draft = DRAFTS.get(token)
    if not draft or draft[0] < time.monotonic() or draft[1] != binding:
        raise ValueError('Controleoverzicht verlopen. Upload de screenshot opnieuw.')
    reference = draft[2]
    try:
        day = date.fromisoformat(str(payload.get('date'))).isoformat()
    except (ValueError, TypeError):
        raise ValueError('Controleer eerst de datum.') from None
    if not (2000 <= date.fromisoformat(day).year <= 2100):
        raise ValueError('Ongeldige datum.')
    if reference['date_uncertain'] and payload.get('reviewed_date') is not True:
        raise ValueError('Bevestig de handmatig gecontroleerde datum.')
    visits = payload.get('visits')
    if not isinstance(visits, list) or not 1 <= len(visits) <= 30:
        raise ValueError('Kies minimaal één en maximaal dertig bezoeken.')
    available = {v['source_id']: v for v in reference['visits']}
    ids, addresses = set(), []
    for entry in visits:
        if not isinstance(entry, dict) or type(entry.get('source_id')) is not int:
            raise ValueError('Ongeldig bezoek. Upload opnieuw.')
        source = entry['source_id']
        if source not in available or source in ids:
            raise ValueError('Het controleoverzicht is gewijzigd. Upload opnieuw.')
        ids.add(source)
        address = validated_visit(entry.get('address') or '')
        if available[source]['uncertain'] and (entry.get('reviewed') is not True or
                                             address.casefold() == available[source]['address'].casefold()):
            raise ValueError('Controleer de afgekorte adressen en vul ze volledig aan.')
        addresses.append(address)
    calendar_id = str(payload.get('calendar_id') or '').strip()
    if not calendar_id or len(calendar_id) > 250:
        raise ValueError('Selecteer een Google Agenda.')
    return day, addresses, calendar_id


def calendar_service(oauth_json: str):
    if not oauth_json:
        raise ValueError('Google Agenda is niet gekoppeld. Configureer een afzonderlijke OAuth-token in de add-on.')
    try:
        info = json.loads(oauth_json)
        scopes = set(info.get('scopes') or [])
        if not set(SCOPES).issubset(scopes) or not all(info.get(key) for key in ('refresh_token', 'client_id', 'client_secret')):
            raise ValueError('Ontbrekende Calendar-rechten of refresh-token.')
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        credentials = Credentials.from_authorized_user_info(info, scopes=SCOPES)
        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Google Agenda-autorisatie ongeldig of zonder juiste rechten.') from exc


def available_calendars(service) -> list[dict]:
    try:
        rows, page = [], None
        for _ in range(10):
            result = service.calendarList().list(pageToken=page, maxResults=250).execute()
            for item in result.get('items', []):
                if item.get('accessRole') in ('owner', 'writer'):
                    rows.append({'id': item['id'], 'name': item.get('summary') or item['id'],
                                 'primary': bool(item.get('primary'))})
            page = result.get('nextPageToken')
            if not page:
                break
        return rows
    except Exception as exc:
        raise ValueError('Google Agenda niet bereikbaar of autorisatie verlopen. Koppel opnieuw.') from exc


def _event_id(calendar_id: str, day: str, address: str, duplicate_index: int) -> str:
    # Google IDs allow base32hex (0-9a-v); deterministic across retries/reordering.
    digest = hashlib.sha256(f'{calendar_id}\0{day}\0{address.casefold()}\0{duplicate_index}'.encode()).digest()
    return 'rit' + base64.b32hexencode(digest).decode('ascii').lower().rstrip('=')[:48]


def _existing_events(service, calendar_id: str, day: str):
    if calendar_id not in {c['id'] for c in available_calendars(service)}:
        raise ValueError('Geen schrijfrechten voor de geselecteerde Google Agenda.')
    events_resource, existing, page = service.events(), {}, None
    # A generous UTC window covers Dutch local-day offsets.
    for _ in range(12):
        result = events_resource.list(calendarId=calendar_id,
            timeMin=f'{(date.fromisoformat(day) - timedelta(days=1)).isoformat()}T00:00:00Z',
            timeMax=f'{(date.fromisoformat(day) + timedelta(days=2)).isoformat()}T00:00:00Z',
            singleEvents=True, maxResults=250, pageToken=page).execute()
        for item in result.get('items', []):
            if item.get('start', {}).get('date') == day:
                existing.setdefault((item.get('location') or '').strip().casefold(), []).append(item)
        page = result.get('nextPageToken')
        if not page:
            break
    if page:
        raise ValueError('Te veel agenda-afspraken om doublures veilig te controleren.')
    return events_resource, existing


def duplicate_check(service, calendar_id: str, day: str, addresses: list[str]) -> dict:
    try:
        _, existing = _existing_events(service, calendar_id, day)
        counts, duplicates = {}, []
        for i, address in enumerate(addresses, 1):
            key = address.casefold()
            counts[key] = counts.get(key, 0) + 1
            if len(existing.get(key, [])) >= counts[key]:
                duplicates.append(i)
        return {'duplicates': duplicates, 'total': len(addresses)}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Dubbelcontrole via Google Agenda mislukt. Geen afspraken aangemaakt.') from exc


def import_events(service, calendar_id: str, day: str, addresses: list[str]) -> dict:
    # Exact event IDs and a fresh remote read make POST retries idempotent.
    try:
        events_resource, existing = _existing_events(service, calendar_id, day)
        signatures = {}
        created, skipped = 0, 0
        for i, address in enumerate(addresses, 1):
            key = address.casefold()
            signatures[key] = signatures.get(key, 0) + 1
            event_id = _event_id(calendar_id, day, address, signatures[key])
            matching = existing.get(key, [])
            if any(e.get('id') == event_id for e in matching) or len(matching) >= signatures[key]:
                skipped += 1
                continue
            body = {'id': event_id, 'summary': f'{i:02d} · Bezoek {i} van {len(addresses)}',
                    'location': address, 'start': {'date': day},
                    'end': {'date': (date.fromisoformat(day) + timedelta(days=1)).isoformat()}}
            try:
                events_resource.insert(calendarId=calendar_id, body=body, sendUpdates='none').execute()
                existing.setdefault(key, []).append(body)
                created += 1
            except Exception as exc:
                if getattr(getattr(exc, 'resp', None), 'status', None) == 409:
                    skipped += 1
                    continue
                raise
        return {'created': created, 'skipped': skipped, 'total': len(addresses),
                'duplicate': skipped > 0}
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Google Agenda-import onderbroken. Probeer opnieuw; al gemaakte afspraken worden niet verdubbeld.') from exc
