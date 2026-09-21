from __future__ import annotations

import base64
import csv
from contextlib import contextmanager
import hashlib
import html
import hmac
import io
import json
import math
import os
import re
import secrets
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import textwrap
import urllib.error
import urllib.request
import websocket
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, quote, urlencode
from zoneinfo import ZoneInfo

DATA_DIR = Path('/data')
DB_PATH = DATA_DIR / 'rit_tank.db'
OPTIONS_PATH = DATA_DIR / 'options.json'
PORT = 8099
DB_LOCK = threading.RLock()
APP_VERSION = '5.0.11'
SESSION_COOKIE = 'rit_tank_session'
LOGIN_LOCK = threading.RLock()
BACKUP_LOCK = threading.Lock()
LOGIN_FAILURES: dict[str, list[float]] = {}

DEFAULT_OPTIONS = {
    'vehicle_name': 'Mijn auto',
    'fuel_type': 'Benzine',
    'currency': '€',
    'timezone': 'Europe/Amsterdam',
    'entity_prefix': 'auto',
    'initial_odometer': 0,
    'google_places_api_key': '',
    'places_radius_m': 1800,
    'places_max_results': 8,
    'standalone_enabled': False,
    'standalone_password': '',
    'standalone_session_days': 30,
    'google_drive_enabled': False,
    'google_drive_folder_id': '',
    'google_drive_service_account_json': '',
    'google_drive_oauth_json': '',
    'backup_encryption_password': '',
    'backup_hour': 3,
    'backup_retention_days': 30,
}


def load_options() -> dict[str, Any]:
    opts = dict(DEFAULT_OPTIONS)
    try:
        if OPTIONS_PATH.exists():
            raw = json.loads(OPTIONS_PATH.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                opts.update(raw)
    except Exception:
        pass
    return opts


def option_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def standalone_config() -> dict[str, Any]:
    opts = load_options()
    password = str(opts.get('standalone_password') or '')
    try:
        days = max(1, min(90, int(opts.get('standalone_session_days') or 30)))
    except Exception:
        days = 30
    enabled = option_bool(opts.get('standalone_enabled'))
    return {
        'enabled': enabled,
        'ready': enabled and len(password) >= 12,
        'password': password,
        'session_days': days,
    }


def _session_key() -> bytes:
    path = DATA_DIR / 'standalone_session.key'
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOGIN_LOCK:
        try:
            key = path.read_bytes()
            if len(key) >= 32:
                return key
        except Exception:
            pass
        key = secrets.token_bytes(48)
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return key


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def make_session_token(password: str, days: int) -> str:
    now = int(time.time())
    payload = {
        'iat': now,
        'exp': now + max(1, min(90, days)) * 86400,
        'nonce': secrets.token_urlsafe(12),
        'pv': hashlib.sha256(password.encode('utf-8')).hexdigest()[:20],
    }
    encoded = _b64url_encode(json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8'))
    signature = _b64url_encode(hmac.new(_session_key(), encoded.encode('ascii'), hashlib.sha256).digest())
    return f'{encoded}.{signature}'


def verify_session_token(token: str, password: str) -> bool:
    try:
        encoded, supplied = token.split('.', 1)
        expected = _b64url_encode(hmac.new(_session_key(), encoded.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            return False
        payload = json.loads(_b64url_decode(encoded).decode('utf-8'))
        if int(payload.get('exp') or 0) < int(time.time()):
            return False
        revision = hashlib.sha256(password.encode('utf-8')).hexdigest()[:20]
        return hmac.compare_digest(str(payload.get('pv') or ''), revision)
    except Exception:
        return False


def cookie_value(header: str, name: str) -> str:
    for part in str(header or '').split(';'):
        key, sep, value = part.strip().partition('=')
        if sep and key == name:
            return value.strip()
    return ''


def login_retry_after(client_ip: str) -> int:
    now = time.time()
    with LOGIN_LOCK:
        attempts = [stamp for stamp in LOGIN_FAILURES.get(client_ip, []) if stamp > now - 600]
        LOGIN_FAILURES[client_ip] = attempts
        if len(attempts) < 5:
            return 0
        return max(1, int(600 - (now - attempts[0])))


def record_login_failure(client_ip: str) -> None:
    with LOGIN_LOCK:
        if len(LOGIN_FAILURES) > 2048:
            cutoff = time.time() - 600
            stale = [ip for ip, stamps in LOGIN_FAILURES.items() if not any(stamp > cutoff for stamp in stamps)]
            for ip in stale:
                LOGIN_FAILURES.pop(ip, None)
        LOGIN_FAILURES.setdefault(client_ip, []).append(time.time())


def clear_login_failures(client_ip: str) -> None:
    with LOGIN_LOCK:
        LOGIN_FAILURES.pop(client_ip, None)


def tz() -> ZoneInfo:
    name = str(load_options().get('timezone') or 'Europe/Amsterdam')
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo('Europe/Amsterdam')


def now_local() -> datetime:
    return datetime.now(tz())


def iso_local(dt: datetime | None = None) -> str:
    return (dt or now_local()).replace(microsecond=0).isoformat()


@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with DB_LOCK, db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('odometer','fuel')),
            odometer REAL NOT NULL,
            liters REAL,
            price_per_liter REAL,
            station TEXT,
            full_tank INTEGER NOT NULL DEFAULT 0,
            note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at, id);

        CREATE TABLE IF NOT EXISTS business_trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','completed')),
            purpose TEXT,
            client TEXT,
            note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_business_trips_started ON business_trips(started_at, id);

        CREATE TABLE IF NOT EXISTS trip_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL,
            sequence_no INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            odometer REAL NOT NULL,
            latitude REAL,
            longitude REAL,
            location_accuracy REAL,
            location_source TEXT,
            place_id TEXT,
            manual_label TEXT,
            note TEXT,
            event_id INTEGER,
            FOREIGN KEY(trip_id) REFERENCES business_trips(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trip_stops_seq ON trip_stops(trip_id, sequence_no);
        CREATE INDEX IF NOT EXISTS idx_trip_stops_created ON trip_stops(created_at, id);

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            details TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at, id);

        CREATE TABLE IF NOT EXISTS known_places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'other',
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_m INTEGER NOT NULL DEFAULT 180,
            arrival_trip_type TEXT NOT NULL DEFAULT 'ask',
            unknown_departure_trip_type TEXT NOT NULL DEFAULT 'ask',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_known_places_name ON known_places(name);

        CREATE TABLE IF NOT EXISTS route_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin_known_place_id INTEGER,
            destination_known_place_id INTEGER,
            destination_latitude REAL NOT NULL,
            destination_longitude REAL NOT NULL,
            business_count INTEGER NOT NULL DEFAULT 0,
            private_count INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_route_memory_origin ON route_memory(origin_known_place_id);

        CREATE TABLE IF NOT EXISTS assistant_arrivals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT NOT NULL,
            departure_at TEXT,
            origin_known_place_id INTEGER,
            destination_known_place_id INTEGER,
            destination_latitude REAL NOT NULL,
            destination_longitude REAL NOT NULL,
            destination_accuracy REAL,
            destination_label TEXT,
            suggested_type TEXT,
            suggestion_reason TEXT,
            suggestion_confidence REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            confirmed_type TEXT,
            classification_source TEXT,
            notification_sent INTEGER NOT NULL DEFAULT 0,
            handled_at TEXT,
            odometer REAL,
            trip_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_assistant_arrivals_status ON assistant_arrivals(status, detected_at);

        CREATE TABLE IF NOT EXISTS assistant_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS distance_calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle TEXT NOT NULL,
            source TEXT NOT NULL UNIQUE,
            gps_km REAL NOT NULL,
            actual_km REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        ''')
        # V2 database migration. Existing V1 data remains untouched.
        cols = {r['name'] for r in con.execute('PRAGMA table_info(events)')}
        for col, sql_type in (
            ('place_id', 'TEXT'),
            ('latitude', 'REAL'),
            ('longitude', 'REAL'),
            ('location_accuracy', 'REAL'),
            ('location_source', 'TEXT'),
            ('source_kind', 'TEXT'),
            ('business_trip_stop_id', 'INTEGER'),
            ('receipt_path', 'TEXT')
        ):
            if col not in cols:
                con.execute(f'ALTER TABLE events ADD COLUMN {col} {sql_type}')

        stop_cols = {r['name'] for r in con.execute('PRAGMA table_info(trip_stops)')}
        con.execute('''CREATE TABLE IF NOT EXISTS report_addresses (
            coordinate_key TEXT PRIMARY KEY, address TEXT NOT NULL DEFAULT '',
            checked_at REAL NOT NULL, source TEXT NOT NULL DEFAULT 'PDOK / BAG'
        )''')
        for col, sql_type in (
            ('known_place_id', 'INTEGER'),
            ('segment_trip_type', 'TEXT'),
            ('segment_suggested_type', 'TEXT'),
            ('segment_suggestion_reason', 'TEXT'),
            ('segment_suggestion_confidence', 'REAL'),
            ('segment_classification_source', 'TEXT')
        ):
            if col not in stop_cols:
                con.execute(f'ALTER TABLE trip_stops ADD COLUMN {col} {sql_type}')

        place_cols = {r['name'] for r in con.execute('PRAGMA table_info(known_places)')}
        for col, sql_type in (
            ('ha_zone_id', 'TEXT'),
            ('ha_zone_synced_at', 'TEXT'),
            ('ha_zone_error', 'TEXT')
        ):
            if col not in place_cols:
                con.execute(f'ALTER TABLE known_places ADD COLUMN {col} {sql_type}')

        trip_cols = {r['name'] for r in con.execute('PRAGMA table_info(business_trips)')}
        for col, sql_type in (
            ('trip_type', "TEXT NOT NULL DEFAULT 'business'"),
            ('deviating_route', 'TEXT'),
            ('private_detour_km', 'REAL NOT NULL DEFAULT 0'),
            ('modified_at', 'TEXT')
        ):
            if col not in trip_cols:
                con.execute(f'ALTER TABLE business_trips ADD COLUMN {col} {sql_type}')

        opts = load_options()
        for key in ('vehicle_name', 'fuel_type', 'currency'):
            con.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (key, str(opts.get(key, DEFAULT_OPTIONS[key]))))
        count = con.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        initial = float(opts.get('initial_odometer') or 0)
        if count == 0 and initial > 0:
            con.execute(
                "INSERT INTO events(created_at,type,odometer,note) VALUES(?,?,?,?)",
                (iso_local(), 'odometer', initial, 'Begin-kilometerstand')
            )
        con.commit()


def get_settings() -> dict[str, Any]:
    opts = load_options()
    out = {
        'vehicle_name': str(opts.get('vehicle_name') or 'Mijn auto'),
        'fuel_type': str(opts.get('fuel_type') or 'Benzine'),
        'currency': str(opts.get('currency') or '€'),
        'timezone': str(opts.get('timezone') or 'Europe/Amsterdam'),
        'entity_prefix': sanitize_prefix(str(opts.get('entity_prefix') or 'auto')),
        'driver_name': '',
        'company_name': '',
        'vehicle_make': '',
        'vehicle_model': '',
        'license_plate': '',
        'vehicle_period_from': '',
        'vehicle_period_to': '',
        'assistant_enabled': '0',
        'assistant_mode': 'assistant',
        'assistant_auto_confidence': '95',
        'distance_learning_enabled': '1',
        'assistant_location_entity': '',
        'assistant_notify_service': '',
        'assistant_sync_zones': '1',
        'assistant_unknown_stops': '1',
        'assistant_check_seconds': '10',
        'assistant_unknown_stop_minutes': '4',
        'assistant_fast_stop_seconds': '30',
        'assistant_min_trip_m': '500',
    }
    with DB_LOCK, db() as con:
        for row in con.execute('SELECT key,value FROM settings'):
            out[row['key']] = row['value']
    return out


def set_settings(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        'vehicle_name', 'fuel_type', 'currency', 'driver_name', 'company_name',
        'vehicle_make', 'vehicle_model', 'license_plate', 'vehicle_period_from',
        'vehicle_period_to', 'assistant_enabled', 'assistant_location_entity', 'location_fallback_entity',
        'assistant_mode', 'assistant_auto_confidence', 'distance_learning_enabled',
        'assistant_notify_service', 'assistant_sync_zones', 'assistant_unknown_stops',
        'assistant_check_seconds', 'assistant_unknown_stop_minutes', 'assistant_fast_stop_seconds', 'assistant_min_trip_m'
    )
    allow_empty = {'assistant_location_entity', 'assistant_notify_service', 'location_fallback_entity'}
    with DB_LOCK, db() as con:
        for key in allowed:
            if key in payload:
                value = str(payload[key]).strip()[:160]
                if value or key in allow_empty:
                    con.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
        count = con.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        if count == 0 and payload.get('initial_odometer') not in (None, ''):
            initial = to_float(payload.get('initial_odometer'))
            if initial is not None and initial >= 0:
                con.execute("INSERT INTO events(created_at,type,odometer,note) VALUES(?,?,?,?)", (iso_local(), 'odometer', initial, 'Begin-kilometerstand'))
        con.commit()
    publish_sensors_async()
    return get_settings()


def sanitize_prefix(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r'[^a-z0-9_]+', '_', value)
    value = re.sub(r'_+', '_', value).strip('_')
    return value or 'auto'


def to_float(v: Any) -> float | None:
    try:
        if v is None or v == '':
            return None
        return float(str(v).replace(',', '.'))
    except Exception:
        return None


def parse_dt(value: str | None) -> datetime:
    if not value:
        return now_local()
    s = value.strip()
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz())
        return dt.astimezone(tz())
    except Exception:
        return now_local()



_PLACE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PLACE_CACHE_TTL = 300.0


def places_key() -> str:
    return str(load_options().get('google_places_api_key') or '').strip()


def places_radius_m() -> int:
    try:
        return max(100, min(5000, int(load_options().get('places_radius_m') or 1800)))
    except Exception:
        return 1800


def places_max_results() -> int:
    try:
        return max(1, min(20, int(load_options().get('places_max_results') or 8)))
    except Exception:
        return 8


def http_json(url: str, *, method: str = 'GET', payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 10) -> dict[str, Any]:
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req_headers = {'Accept': 'application/json'}
    if payload is not None:
        req_headers['Content-Type'] = 'application/json'
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode('utf-8')) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            data = json.loads(exc.read().decode('utf-8'))
            detail = data.get('error', {}).get('message') or data.get('error') or ''
        except Exception:
            detail = ''
        raise ValueError(f'Externe dienst gaf fout {exc.code}' + (f': {detail}' if detail else '.'))
    except urllib.error.URLError as exc:
        raise ValueError(f'Externe dienst niet bereikbaar: {exc.reason}')


def google_nearby(lat: float, lon: float) -> list[dict[str, Any]]:
    key = places_key()
    if not key:
        raise ValueError('Google Places API-key ontbreekt. Vul hem in bij de app-configuratie.')
    payload = {
        'includedTypes': ['gas_station'],
        'maxResultCount': places_max_results(),
        'rankPreference': 'DISTANCE',
        'locationRestriction': {
            'circle': {
                'center': {'latitude': lat, 'longitude': lon},
                'radius': float(places_radius_m()),
            }
        },
        'languageCode': 'nl',
        'regionCode': 'NL',
    }
    data = http_json(
        'https://places.googleapis.com/v1/places:searchNearby',
        method='POST', payload=payload,
        headers={
            'X-Goog-Api-Key': key,
            'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress,places.location',
        }, timeout=12,
    )
    out = []
    for p in data.get('places', []) or []:
        loc = p.get('location') or {}
        plat = to_float(loc.get('latitude'))
        plon = to_float(loc.get('longitude'))
        distance = None
        if plat is not None and plon is not None:
            distance = haversine_m(lat, lon, plat, plon)
        out.append({
            'place_id': str(p.get('id') or ''),
            'name': str((p.get('displayName') or {}).get('text') or 'Tankstation'),
            'address': str(p.get('formattedAddress') or ''),
            'latitude': plat,
            'longitude': plon,
            'distance_m': round(distance) if distance is not None else None,
            'google_maps_uri': (f"https://www.google.com/maps/search/?api=1&query={plat},{plon}&query_place_id={quote(str(p.get('id') or ''), safe='')}" if plat is not None and plon is not None else ''),
        })
    return out


def google_place_details(place_id: str) -> dict[str, Any] | None:
    place_id = (place_id or '').strip()
    key = places_key()
    if not place_id or not key:
        return None
    cached = _PLACE_CACHE.get(place_id)
    if cached and time.monotonic() - cached[0] < _PLACE_CACHE_TTL:
        return cached[1]
    try:
        data = http_json(
            f'https://places.googleapis.com/v1/places/{quote(place_id, safe="")}',
            headers={
                'X-Goog-Api-Key': key,
                'X-Goog-FieldMask': 'id,displayName,formattedAddress,location',
            }, timeout=8,
        )
        loc = data.get('location') or {}
        result = {
            'place_id': str(data.get('id') or place_id),
            'name': str((data.get('displayName') or {}).get('text') or 'Tankstation'),
            'address': str(data.get('formattedAddress') or ''),
            'latitude': to_float(loc.get('latitude')),
            'longitude': to_float(loc.get('longitude')),
            'google_maps_uri': (f"https://www.google.com/maps/search/?api=1&query={to_float(loc.get('latitude'))},{to_float(loc.get('longitude'))}&query_place_id={quote(str(data.get('id') or place_id), safe='')}" if to_float(loc.get('latitude')) is not None and to_float(loc.get('longitude')) is not None else ''),
        }
        _PLACE_CACHE[place_id] = (time.monotonic(), result)
        return result
    except Exception:
        return None


_GEOCODE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_GEOCODE_CACHE_TTL = 300.0


def google_reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    key = places_key()
    if not key:
        return {
            'place_id': '',
            'address': f'{lat:.6f}, {lon:.6f}',
            'province': '',
            'latitude': lat,
            'longitude': lon,
            'google_maps_uri': f'https://www.google.com/maps/search/?api=1&query={lat},{lon}',
            'source': 'coordinates',
        }
    cache_key = f'{lat:.5f},{lon:.5f}'
    cached = _GEOCODE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _GEOCODE_CACHE_TTL:
        return cached[1]
    try:
        url = (
            'https://maps.googleapis.com/maps/api/geocode/json?'
            f'latlng={quote(f"{lat},{lon}", safe=",")}&language=nl&region=nl&key={quote(key, safe="")}'
        )
        data = http_json(url, timeout=10)
        if str(data.get('status') or '') not in ('OK', 'ZERO_RESULTS'):
            raise ValueError(str(data.get('error_message') or data.get('status') or 'Geocoding mislukt'))
        results = data.get('results') or []
        province = ''
        if results:
            first = results[0]
            place_id = str(first.get('place_id') or '')
            address = str(first.get('formatted_address') or f'{lat:.6f}, {lon:.6f}')
            for comp in first.get('address_components') or []:
                types = set(comp.get('types') or [])
                if 'administrative_area_level_1' in types:
                    province = str(comp.get('long_name') or comp.get('short_name') or '').strip()
                    break
        else:
            place_id = ''
            address = f'{lat:.6f}, {lon:.6f}'
        result = {
            'place_id': place_id,
            'address': address,
            'province': province,
            'latitude': lat,
            'longitude': lon,
            'google_maps_uri': (
                f'https://www.google.com/maps/search/?api=1&query={lat},{lon}'
                + (f'&query_place_id={quote(place_id, safe="")}' if place_id else '')
            ),
            'source': 'google' if results else 'coordinates',
        }
        _GEOCODE_CACHE[cache_key] = (time.monotonic(), result)
        return result
    except Exception:
        return {
            'place_id': '',
            'address': f'{lat:.6f}, {lon:.6f}',
            'province': '',
            'latitude': lat,
            'longitude': lon,
            'google_maps_uri': f'https://www.google.com/maps/search/?api=1&query={lat},{lon}',
            'source': 'coordinates',
        }


def nearby_house_numbers(lat: float, lon: float) -> dict[str, Any]:
    """Use real BAG addresses, never manufacture house numbers from GPS."""
    base = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/'
    fields = 'id,weergavenaam,straatnaam,woonplaatsnaam,openbareruimte_id,huis_nlt,centroide_ll,afstand'
    try:
        nearest = http_json(base + 'reverse?' + urlencode({
            'lat': lat, 'lon': lon, 'type': 'adres', 'distance': 250,
            'rows': 1, 'fl': fields,
        }), timeout=10).get('response', {}).get('docs', [])
        if not nearest:
            return {'addresses': [], 'street': '', 'source': 'PDOK / BAG'}
        street_id = str(nearest[0].get('openbareruimte_id') or '')
        if not re.fullmatch(r'\d+', street_id):
            return {'addresses': [], 'street': '', 'source': 'PDOK / BAG'}
        docs = http_json(base + 'free?' + urlencode({
            'q': '*:*', 'fq': f'type:adres AND openbareruimte_id:{street_id}',
            'lat': lat, 'lon': lon, 'rows': 10, 'fl': fields,
        }), timeout=10).get('response', {}).get('docs', [])
        addresses, seen = [], set()
        for doc in docs:
            if str(doc.get('openbareruimte_id')) != street_id:
                continue
            point = re.fullmatch(r'POINT\(([\d.\-]+) ([\d.\-]+)\)', str(doc.get('centroide_ll') or ''))
            label = str(doc.get('weergavenaam') or '')
            if not point or not label or label in seen:
                continue
            lng, latitude = map(float, point.groups())
            seen.add(label)
            addresses.append({'address': label, 'house_number': doc.get('huis_nlt') or '',
                              'latitude': latitude, 'longitude': lng,
                              'distance_m': round(haversine_m(lat, lon, latitude, lng))})
        addresses.sort(key=lambda item: item['distance_m'])
        return {'addresses': addresses[:10], 'street': nearest[0].get('straatnaam') or '', 'source': 'PDOK / BAG'}
    except Exception:
        raise ValueError('Huisnummers konden niet worden opgehaald. Probeer opnieuw of vul het adres handmatig in.') from None


def cached_report_address(lat: float, lon: float) -> str:
    try:
        with DB_LOCK, db() as con:
            row = con.execute('SELECT address FROM report_addresses WHERE coordinate_key=?', (f'{lat:.5f},{lon:.5f}',)).fetchone()
        return str(row['address']) if row else ''
    except sqlite3.OperationalError:
        # Offline/unit-test databases created before the migration simply have no cache yet.
        return ''


def refresh_report_addresses() -> None:
    """Resolve old GPS-only records in the background; never delay PDF requests."""
    with DB_LOCK, db() as con:
        stops = [dict(r) for r in con.execute('SELECT latitude,longitude FROM trip_stops WHERE latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY id DESC')]
        cached = {r['coordinate_key']: dict(r) for r in con.execute('SELECT * FROM report_addresses')}
    pending = {}
    for stop in stops:
        lat, lon = float(stop['latitude']), float(stop['longitude'])
        key = f'{lat:.5f},{lon:.5f}'
        prior = cached.get(key)
        if prior and (prior['address'] or time.time() - prior['checked_at'] < 86400):
            continue
        pending[key] = (lat, lon)
    for key, (lat, lon) in list(pending.items())[:50]:
        address = ''
        try:
            url = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/reverse?' + urlencode({
                'lat': lat, 'lon': lon, 'type': 'adres', 'distance': 100, 'rows': 1,
                'fl': 'straatnaam,huis_nlt,postcode,woonplaatsnaam',
            })
            docs = http_json(url, timeout=6).get('response', {}).get('docs', [])
            if docs and all(docs[0].get(k) for k in ('straatnaam', 'huis_nlt', 'postcode', 'woonplaatsnaam')):
                d = docs[0]
                address = f'{d["straatnaam"]} {d["huis_nlt"]}, {d["postcode"]} {d["woonplaatsnaam"]}'
        except Exception:
            pass
        with DB_LOCK, db() as con:
            con.execute('INSERT OR REPLACE INTO report_addresses(coordinate_key,address,checked_at) VALUES(?,?,?)', (key, address, time.time()))


def _report_address_worker() -> None:
    while True:
        try:
            refresh_report_addresses()
        except Exception as exc:
            print(f'Adresaanvulling: {type(exc).__name__}', flush=True)
        time.sleep(30)


def dutch_date(value: Any) -> str:
    dt = parse_dt(value) if not isinstance(value, datetime) else value.astimezone(tz())
    days = ('maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag')
    months = ('januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december')
    return f'{days[dt.weekday()]} {dt.day} {months[dt.month - 1]} {dt.year}'


def trip_location_details(stop: dict[str, Any], resolve: bool = True) -> dict[str, Any]:
    # A confirmed address must not be replaced by a nearby HA zone or geocoder.
    manual = str(stop.get('manual_label') or '').strip()
    if manual:
        return {'label': manual, 'address': manual, 'google_maps_uri': 'https://www.google.com/maps/search/?api=1&query=' + quote(manual)}
    kp = known_place_by_id(stop.get('known_place_id')) if stop.get('known_place_id') else None
    lat, lon = to_float(stop.get('latitude')), to_float(stop.get('longitude'))
    address = cached_report_address(lat, lon) if lat is not None and lon is not None else ''
    if address:
        name = str(kp.get('name') or '') if kp else ''
        label = (name + ' - ' if name else '') + address
        return {'label': label, 'address': label + ' (GPS-adres; huisnummer controleren)', 'google_maps_uri': f'https://www.google.com/maps/search/?api=1&query={lat},{lon}'}
    if kp:
        maps = f'https://www.google.com/maps/search/?api=1&query={lat},{lon}' if lat is not None and lon is not None else ''
        return {'label': str(kp.get('name') or 'Bekende plek'), 'address': str(kp.get('name') or ''), 'google_maps_uri': maps}
    place_id = str(stop.get('place_id') or '').strip()
    if place_id and resolve:
        details = google_place_details(place_id)
        if details:
            label = details.get('address') or details.get('name') or 'Locatie'
            return {'label': label, 'address': details.get('address') or '', 'google_maps_uri': details.get('google_maps_uri') or ''}
    if lat is not None and lon is not None:
        details = google_reverse_geocode(lat, lon) if resolve else {}
        return {'label': details.get('address') or f'{lat:.5f}, {lon:.5f}', 'address': details.get('address') or '', 'google_maps_uri': details.get('google_maps_uri') or ''}
    return {'label': 'Locatie onbekend', 'address': '', 'google_maps_uri': ''}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ha_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 8) -> Any:
    token = os.getenv('SUPERVISOR_TOKEN', '')
    if not token:
        raise ValueError('Home Assistant API-token is niet beschikbaar.')
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(
        f'http://supervisor/core/api/{path.lstrip("/")}',
        data=body,
        method=method.upper(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return {}
            return json.loads(raw.decode('utf-8'))
    except Exception as exc:
        detail = str(exc)
        if token:
            detail = detail.replace(token, '[redacted]')
        raise ValueError(f'Home Assistant API niet beschikbaar: {detail}')


def ha_get(path: str) -> Any:
    return ha_request('GET', path)


def ha_post(path: str, payload: dict[str, Any]) -> Any:
    return ha_request('POST', path, payload)


def ha_notify_services() -> list[dict[str, str]]:
    try:
        domains = ha_get('services')
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for domain in domains if isinstance(domains, list) else []:
        if str(domain.get('domain') or '') != 'notify':
            continue
        services = domain.get('services') or {}
        for service_name in services:
            if str(service_name).startswith('mobile_app_'):
                out.append({
                    'service': f'notify.{service_name}',
                    'name': str(service_name).replace('mobile_app_', '').replace('_', ' ').title(),
                })
    out.sort(key=lambda x: x['name'].lower())
    return out


def _ha_ws_open(timeout: int = 10):
    token = os.getenv('SUPERVISOR_TOKEN', '')
    if not token:
        raise ValueError('Home Assistant API-token is niet beschikbaar.')
    ws = websocket.create_connection('ws://supervisor/core/websocket', timeout=timeout)
    first = json.loads(ws.recv())
    if first.get('type') != 'auth_required':
        ws.close()
        raise ValueError('Onverwachte Home Assistant WebSocket-handshake.')
    ws.send(json.dumps({'type': 'auth', 'access_token': token}))
    auth = json.loads(ws.recv())
    if auth.get('type') != 'auth_ok':
        ws.close()
        raise ValueError('Home Assistant WebSocket-authenticatie mislukt.')
    return ws


def ha_ws_command(command: dict[str, Any], timeout: int = 10) -> Any:
    ws = _ha_ws_open(timeout)
    try:
        payload = dict(command)
        payload['id'] = 1
        ws.send(json.dumps(payload))
        while True:
            msg = json.loads(ws.recv())
            if msg.get('id') != 1:
                continue
            if msg.get('type') == 'result':
                if not msg.get('success'):
                    err = msg.get('error') or {}
                    raise ValueError(str(err.get('message') or 'WebSocket-opdracht mislukt.'))
                return msg.get('result')
    finally:
        try:
            ws.close()
        except Exception:
            pass


def location_entities() -> list[dict[str, Any]]:
    rows = ha_get('states')
    out = []
    for item in rows if isinstance(rows, list) else []:
        entity_id = str(item.get('entity_id') or '')
        if not (entity_id.startswith('person.') or entity_id.startswith('device_tracker.')):
            continue
        attrs = item.get('attributes') or {}
        lat, lon = to_float(attrs.get('latitude')), to_float(attrs.get('longitude'))
        if lat is None or lon is None:
            continue
        out.append({
            'entity_id': entity_id,
            'name': str(attrs.get('friendly_name') or entity_id),
            'state': str(item.get('state') or ''),
            'latitude': lat,
            'longitude': lon,
            'gps_accuracy': to_float(attrs.get('gps_accuracy')),
        })
    out.sort(key=lambda x: (0 if x['entity_id'].startswith('person.') else 1, x['name'].lower()))
    return out


def location_from_entity(entity_id: str) -> dict[str, Any]:
    entity_id = (entity_id or '').strip()
    if not (entity_id.startswith('person.') or entity_id.startswith('device_tracker.')):
        raise ValueError('Kies een geldige person- of device_tracker-entiteit.')
    item = ha_get(f'states/{quote(entity_id, safe="._")}')
    attrs = item.get('attributes') or {}
    lat, lon = to_float(attrs.get('latitude')), to_float(attrs.get('longitude'))
    if lat is None or lon is None:
        raise ValueError('Deze Home Assistant-entiteit heeft geen GPS-coördinaten.')
    return {
        'entity_id': entity_id,
        'name': str(attrs.get('friendly_name') or entity_id),
        'latitude': lat,
        'longitude': lon,
        'accuracy': to_float(attrs.get('gps_accuracy')),
        'speed': to_float(attrs.get('speed')),
        'course': to_float(attrs.get('course')),
        'state': str(item.get('state') or ''),
        'source': 'home_assistant',
        'ha_last_updated': item.get('last_updated'),
    }

def rows_events() -> list[dict[str, Any]]:
    with DB_LOCK, db() as con:
        rows = [dict(r) for r in con.execute('SELECT * FROM events ORDER BY created_at ASC, id ASC')]
    prev = None
    for row in rows:
        odo = float(row['odometer'])
        row['delta_km'] = max(0.0, odo - prev) if prev is not None else 0.0
        row['cost'] = (float(row['liters'] or 0) * float(row['price_per_liter'] or 0)) if row['type'] == 'fuel' else 0.0
        prev = odo
    return rows


def validate_odometer(created_at: str, odometer: float, ignore_id: int | None = None) -> tuple[bool, str]:
    params_prev: list[Any] = [created_at]
    params_next: list[Any] = [created_at]
    extra = ''
    if ignore_id is not None:
        extra = ' AND id != ?'
        params_prev.append(ignore_id)
        params_next.append(ignore_id)
    with DB_LOCK, db() as con:
        prev = con.execute(f'SELECT odometer FROM events WHERE created_at <= ?{extra} ORDER BY created_at DESC, id DESC LIMIT 1', params_prev).fetchone()
        nxt = con.execute(f'SELECT odometer FROM events WHERE created_at > ?{extra} ORDER BY created_at ASC, id ASC LIMIT 1', params_next).fetchone()
    if prev and odometer < float(prev['odometer']):
        return False, f'Kilometerstand is lager dan de vorige registratie ({float(prev["odometer"]):.0f} km).'
    if nxt and odometer > float(nxt['odometer']):
        return False, f'Kilometerstand is hoger dan een latere registratie ({float(nxt["odometer"]):.0f} km).'
    return True, ''


def add_odometer(payload: dict[str, Any]) -> dict[str, Any]:
    odo = to_float(payload.get('odometer'))
    if odo is None or odo < 0:
        raise ValueError('Vul een geldige kilometerstand in.')
    dt = parse_dt(payload.get('created_at'))
    created = iso_local(dt)
    ok, msg = validate_odometer(created, odo)
    if not ok:
        raise ValueError(msg)
    note = str(payload.get('note') or '').strip()[:200]
    with DB_LOCK, db() as con:
        cur = con.execute('INSERT INTO events(created_at,type,odometer,note) VALUES(?,?,?,?)', (created, 'odometer', odo, note))
        rid = int(cur.lastrowid)
        audit('create', 'odometer', rid, {'odometer': odo, 'note': note}, con=con)
        con.commit()
    publish_sensors_async()
    return {'ok': True, 'id': rid}


def add_fuel(payload: dict[str, Any]) -> dict[str, Any]:
    odo = to_float(payload.get('odometer'))
    liters = to_float(payload.get('liters'))
    price = to_float(payload.get('price_per_liter'))
    if odo is None or odo < 0:
        raise ValueError('Vul een geldige kilometerstand in.')
    if liters is None or liters <= 0 or liters > 250:
        raise ValueError('Vul een geldig aantal liters in.')
    if price is None or price <= 0 or price > 10:
        raise ValueError('Vul een geldige prijs per liter in.')
    dt = parse_dt(payload.get('created_at'))
    created = iso_local(dt)
    ok, msg = validate_odometer(created, odo)
    if not ok:
        raise ValueError(msg)
    station = str(payload.get('station') or '').strip()[:100]
    place_id = str(payload.get('place_id') or '').strip()[:255]
    lat = to_float(payload.get('latitude'))
    lon = to_float(payload.get('longitude'))
    accuracy = to_float(payload.get('location_accuracy'))
    location_source = str(payload.get('location_source') or '').strip()[:40]
    if lat is not None and not (-90 <= lat <= 90):
        lat = None
    if lon is not None and not (-180 <= lon <= 180):
        lon = None
    note = str(payload.get('note') or '').strip()[:200]
    full_tank = 1 if bool(payload.get('full_tank', True)) else 0
    with DB_LOCK, db() as con:
        cur = con.execute('''
            INSERT INTO events(
                created_at,type,odometer,liters,price_per_liter,station,full_tank,note,
                place_id,latitude,longitude,location_accuracy,location_source
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (created, 'fuel', odo, liters, price, station, full_tank, note,
              place_id or None, lat, lon, accuracy, location_source or None))
        rid = int(cur.lastrowid)
        receipt_name = save_receipt_data(str(payload.get('receipt_data_url') or ''), rid)
        if receipt_name:
            con.execute('UPDATE events SET receipt_path=? WHERE id=?', (receipt_name, rid))
        audit('create', 'fuel', rid, {'odometer': odo, 'liters': liters, 'price_per_liter': price, 'station': station or place_id}, con=con)
        if receipt_name:
            audit('receipt', 'fuel', rid, {'file': receipt_name}, con=con)
        con.commit()
    publish_sensors_async()
    return {'ok': True, 'id': rid, 'cost': round(liters * price, 2), 'receipt': bool(receipt_name)}

RECEIPT_DIR = DATA_DIR / 'receipts'


def backup_config() -> dict[str, Any]:
    opts = load_options()
    return {
        'enabled': option_bool(opts.get('google_drive_enabled')),
        'folder_id': str(opts.get('google_drive_folder_id') or '').strip(),
        'credentials': str(opts.get('google_drive_service_account_json') or '').strip(),
        'oauth': str(opts.get('google_drive_oauth_json') or '').strip(),
        'password': str(opts.get('backup_encryption_password') or ''),
        'hour': max(0, min(23, int(opts.get('backup_hour') or 3))),
        'retention_days': max(0, min(365, int(opts.get('backup_retention_days', 30)))),
    }


def backup_status() -> dict[str, Any]:
    cfg = backup_config()
    state = assistant_state_get('backup_status', {}) or {}
    return {
        'enabled': cfg['enabled'],
        'configured': bool(cfg['enabled'] and cfg['folder_id'] and (cfg['credentials'] or cfg['oauth']) and len(cfg['password']) >= 12),
        'hour': cfg['hour'],
        'retention_days': cfg['retention_days'],
        'last_ok_at': state.get('last_ok_at') or '',
        'last_file': state.get('last_file') or '',
        'last_error': state.get('last_error') or '',
    }


def _encrypted_backup_file(password: str) -> Path:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    backup_dir = DATA_DIR / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_local().strftime('%Y%m%d_%H%M%S')
    db_copy = backup_dir / f'.rit_tank_{stamp}.db'
    zip_path = backup_dir / f'.rit_tank_{stamp}.zip'
    out_path = backup_dir / f'RitTank_{stamp}.rtbackup'
    try:
        with DB_LOCK, db() as source, sqlite3.connect(db_copy) as target:
            source.backup(target)
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_copy, 'rit_tank.db')
            if RECEIPT_DIR.exists():
                for receipt in RECEIPT_DIR.iterdir():
                    if receipt.is_file():
                        archive.write(receipt, f'receipts/{receipt.name}')
            archive.writestr('backup.json', json.dumps({
                'app': 'Rit & Tank', 'version': APP_VERSION, 'created_at': iso_local(), 'format': 1,
            }, ensure_ascii=False, indent=2))
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=310000).derive(password.encode('utf-8'))
        encrypted = AESGCM(key).encrypt(nonce, zip_path.read_bytes(), b'RIT_TANK_BACKUP_V1')
        out_path.write_bytes(b'RITTANK1' + salt + nonce + encrypted)
        os.chmod(out_path, 0o600)
        return out_path
    finally:
        db_copy.unlink(missing_ok=True)
        zip_path.unlink(missing_ok=True)


def drive_service(cfg: dict[str, Any]):
    from googleapiclient.discovery import build
    if not cfg['enabled'] or not cfg['folder_id']:
        raise ValueError('Schakel Google Drive in en vul de doelmap in de add-onconfiguratie in.')
    try:
        if cfg['oauth']:
            from google.oauth2.credentials import Credentials
            credentials = Credentials.from_authorized_user_info(json.loads(cfg['oauth']))
        else:
            from google.oauth2.service_account import Credentials
            credentials = Credentials.from_service_account_info(json.loads(cfg['credentials']), scopes=['https://www.googleapis.com/auth/drive'])
    except Exception:
        raise ValueError('Google-inloggegevens ontbreken of zijn ongeldig. Controleer de add-onconfiguratie.') from None
    service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
    # Service accounts cannot own files in a personal My Drive.
    folder = service.files().get(fileId=cfg['folder_id'], fields='id,mimeType,driveId,trashed', supportsAllDrives=True).execute()
    if folder.get('trashed') or folder.get('mimeType') != 'application/vnd.google-apps.folder':
        raise ValueError('De gekozen Drive-map bestaat niet of is verwijderd.')
    if not cfg['oauth'] and not folder.get('driveId'):
        raise ValueError('Een service-account vereist een Gedeelde Drive. Gebruik OAuth voor een gewone Google Drive.')
    return service


def archive_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    """Upload the exact PDF the user previewed; archives never enter backup cleanup."""
    try:
        raw = base64.b64decode(str(payload.get('pdf_base64') or ''), validate=True)
    except Exception:
        raise ValueError('Ongeldig PDF-bestand.') from None
    if not raw.startswith(b'%PDF-') or len(raw) > 8 * 1024 * 1024:
        raise ValueError('PDF ontbreekt of is groter dan 8 MB.')
    name = re.sub(r'[^A-Za-z0-9_.-]', '_', str(payload.get('filename') or 'rittenregistratie.pdf'))[:160]
    if not name.lower().endswith('.pdf'):
        name += '.pdf'
    cfg = backup_config()
    try:
        service = drive_service(cfg)
        from googleapiclient.http import MediaIoBaseUpload
        uploaded = service.files().create(
            body={'name': name, 'parents': [cfg['folder_id']],
                  'appProperties': {'rit_tank_kind': 'pdf_archive'},
                  'description': 'Permanent PDF-archief Rit & Tank'},
            media_body=MediaIoBaseUpload(io.BytesIO(raw), mimetype='application/pdf', resumable=True),
            fields='id,name', supportsAllDrives=True,
        ).execute()
        return {'ok': True, 'name': uploaded['name']}
    except ValueError:
        raise
    except Exception:
        raise ValueError('Opslaan in Google Drive mislukt. Controleer de verbinding, toegang en beschikbare ruimte.') from None


def prune_drive_backups(service, cfg: dict[str, Any]) -> None:
    if cfg['retention_days'] == 0:
        return
    cutoff = (datetime.utcnow() - timedelta(days=cfg['retention_days'])).isoformat(timespec='seconds') + 'Z'
    folder = cfg['folder_id'].replace('\\', '\\\\').replace("'", "\\'")
    query = f"'{folder}' in parents and appProperties has {{ key='rit_tank_kind' and value='backup' }} and createdTime < '{cutoff}' and trashed = false"
    token = None
    while True:
        response = service.files().list(q=query, fields='nextPageToken,files(id,name)', pageToken=token,
                                        supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        for item in response.get('files', []):
            if re.fullmatch(r'RitTank_\d{8}_\d{6}\.rtbackup', item.get('name', '')):
                service.files().delete(fileId=item['id'], supportsAllDrives=True).execute()
        token = response.get('nextPageToken')
        if not token:
            break


def run_drive_backup() -> dict[str, Any]:
    if not BACKUP_LOCK.acquire(blocking=False):
        raise ValueError('Er loopt al een back-up.')
    path = None
    try:
        cfg = backup_config()
        if not cfg['enabled']:
            raise ValueError('Google Drive-back-up staat niet aan.')
        if not cfg['folder_id'] or not (cfg['credentials'] or cfg['oauth']):
            raise ValueError('Google Drive-map of inloggegevens ontbreken.')
        if len(cfg['password']) < 12:
            raise ValueError('Kies een back-upwachtwoord van minimaal 12 tekens.')
        from googleapiclient.http import MediaFileUpload
        service = drive_service(cfg)
        path = _encrypted_backup_file(cfg['password'])
        uploaded = service.files().create(
            body={'name': path.name, 'parents': [cfg['folder_id']], 'description': 'Versleutelde Rit & Tank-back-up', 'appProperties': {'rit_tank_kind': 'backup'}},
            media_body=MediaFileUpload(str(path), mimetype='application/octet-stream', resumable=True),
            fields='id,name,createdTime', supportsAllDrives=True,
        ).execute()
        prune_drive_backups(service, cfg)
        state = {'last_ok_at': iso_local(), 'last_file': uploaded.get('name') or path.name, 'last_error': ''}
        assistant_state_set('backup_status', state)
        return {'ok': True, **state}
    except ValueError:
        raise
    except Exception as exc:
        state = assistant_state_get('backup_status', {}) or {}
        state['last_error'] = 'Google Drive-back-up mislukt. Controleer toegang, verbinding en opslagruimte.'
        assistant_state_set('backup_status', state)
        raise ValueError(state['last_error']) from None
    finally:
        if path:
            path.unlink(missing_ok=True)
        BACKUP_LOCK.release()


def _backup_worker() -> None:
    while True:
        try:
            cfg = backup_config()
            status = backup_status()
            today = now_local().date().isoformat()
            last_day = str(status.get('last_ok_at') or '')[:10]
            if cfg['enabled'] and status['configured'] and now_local().hour >= cfg['hour'] and last_day != today:
                run_drive_backup()
        except Exception as exc:
            current = assistant_state_get('backup_status', {}) or {}
            current['last_error'] = str(exc)[:500]
            assistant_state_set('backup_status', current)
        time.sleep(900)


def audit(action: str, entity_type: str, entity_id: int | None, details: Any = None, *, con: sqlite3.Connection | None = None) -> None:
    payload = json.dumps(details if details is not None else {}, ensure_ascii=False, default=str)
    owned = con is None
    c = con or db()
    try:
        c.execute('INSERT INTO audit_log(created_at,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)',
                  (iso_local(), action[:40], entity_type[:40], entity_id, payload[:12000]))
        if owned:
            c.commit()
    finally:
        if owned:
            c.close()


def recent_audit(limit: int = 30) -> list[dict[str, Any]]:
    with DB_LOCK, db() as con:
        rows = [dict(r) for r in con.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (max(1, min(limit, 100)),))]
    labels = {'create':'Aangemaakt','update':'Gewijzigd','delete':'Verwijderd','stop':'Stop toegevoegd','finish':'Rit afgesloten','receipt':'Tankbon toegevoegd'}
    out=[]
    for r in rows:
        try: details=json.loads(r.get('details') or '{}')
        except Exception: details={}
        dt=parse_dt(r['created_at'])
        r['label']=labels.get(r['action'],r['action'])
        r['date_label']=dt.strftime('%d-%m-%Y %H:%M')
        r['details_obj']=details
        out.append(r)
    return out


def _snapshot_trip(con: sqlite3.Connection, trip_id: int) -> dict[str, Any]:
    row=con.execute('SELECT * FROM business_trips WHERE id=?',(trip_id,)).fetchone()
    if not row:
        return {}
    stops=[dict(r) for r in con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no,id',(trip_id,))]
    return {'trip':dict(row),'stops':stops}


def save_receipt_data(data_url: str, event_id: int) -> str | None:
    if not data_url:
        return None
    m=re.match(r'^data:(image/(?:jpeg|png|webp|heic|heif));base64,(.+)$',data_url,re.S|re.I)
    if not m:
        raise ValueError('Tankbon is geen ondersteunde afbeelding.')
    mime=m.group(1).lower()
    try:
        raw=base64.b64decode(m.group(2),validate=True)
    except Exception:
        raise ValueError('Tankbon kon niet worden gelezen.')
    if len(raw)>7*1024*1024:
        raise ValueError('Tankbon is te groot (maximaal 7 MB).')
    ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/heic':'.heic','image/heif':'.heif'}[mime]
    RECEIPT_DIR.mkdir(parents=True,exist_ok=True)
    path=RECEIPT_DIR/f'fuel_{event_id}{ext}'
    path.write_bytes(raw)
    return path.name


def _image_data_url(data_url: str, max_bytes: int = 7 * 1024 * 1024) -> tuple[bytes, str]:
    m = re.match(r'^data:(image/(?:jpeg|png|webp));base64,(.+)$', str(data_url or ''), re.S | re.I)
    if not m:
        raise ValueError('Gebruik een JPG-, PNG- of WebP-afbeelding van de tankbon.')
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception:
        raise ValueError('Tankbon kon niet worden gelezen.')
    if not raw or len(raw) > max_bytes:
        raise ValueError('Tankbon is leeg of groter dan 7 MB.')
    return raw, m.group(1).lower()


def parse_receipt_text(text: str) -> dict[str, Any]:
    lines = [re.sub(r'\s+', ' ', x).strip() for x in str(text or '').splitlines() if x.strip()]
    decimal = re.compile(r'(?<!\d)(\d{1,3}(?:[.,]\d{1,3}))(?!\d)')

    def numbers(line: str) -> list[float]:
        out = []
        for value in decimal.findall(line):
            try:
                out.append(float(value.replace(',', '.')))
            except Exception:
                pass
        return out

    liters = price = total = None
    for line in lines:
        low = line.casefold()
        vals = numbers(line)
        price_label = bool(re.search(r'/\s*l|per\s+liter|literprijs|eenheidsprijs|prijs\s*/', low))
        if liters is None and not price_label and vals and (re.search(r'\b(liter|liters|litres|ltr|volume|hoeveelheid)\b', low) or re.search(r'\d\s*l\b', low)):
            liters = next((v for v in vals if 1 <= v <= 250), None)
        if price is None and vals and price_label:
            price = next((v for v in vals if .5 <= v <= 5), None)
        if vals and any(word in low for word in ('totaal', 'total', 'te betalen', 'bedrag', 'amount')):
            plausible = [v for v in vals if 1 <= v <= 1000]
            if plausible:
                total = plausible[-1]
    # Common pump receipts: "32,45 L x 1,899" or a numeric row below headings.
    for index, line in enumerate(lines):
        row_values = numbers(line)
        # Numeric fuel table row: quantity, unit price, amount (labels may be above).
        if len(row_values) == 3:
            amount, unit_price, row_total = row_values
            if 1 <= amount <= 250 and .5 <= unit_price <= 5 and abs(amount * unit_price - row_total) <= .06:
                liters, price, total = liters or amount, price or unit_price, total or row_total
        match = re.search(r'(\d{1,3}[.,]\d{1,3})\s*(?:l(?:tr|iter)?\.?\s*)?[x×@]\s*(?:€\s*)?(\d[.,]\d{2,3})', line, re.I)
        if match:
            amount, unit_price = [float(v.replace(',', '.')) for v in match.groups()]
            if 1 <= amount <= 250 and .5 <= unit_price <= 5:
                liters = liters or amount
                price = price or unit_price
        if index + 1 < len(lines):
            low, following = line.casefold(), numbers(lines[index + 1])
            if not numbers(line) and following:
                if re.search(r'\b(liters?|ltr|volume|hoeveelheid)\b', low) and not re.search(r'per liter|literprijs|/\s*l', low):
                    liters = liters or next((v for v in following if 1 <= v <= 250), None)
                if re.search(r'literprijs|per liter|/\s*l|eenheidsprijs', low):
                    price = price or next((v for v in following if .5 <= v <= 5), None)
    if price and total and not liters:
        candidate = total / price
        if 1 <= candidate <= 250:
            liters = candidate
    if liters and total and not price:
        candidate = total / liters
        if .5 <= candidate <= 5:
            price = candidate
    if liters and price and not total:
        total = liters * price

    station = ''
    ignored = ('tankbon', 'receipt', 'factuur', 'betaalbewijs', 'transactie')
    for line in lines[:8]:
        if len(line) >= 3 and re.search(r'[A-Za-zÀ-ÿ]{3}', line) and not any(x in line.casefold() for x in ignored):
            station = line[:100]
            break
    date_value = ''
    for line in lines:
        match = re.search(r'\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b', line)
        if match:
            day, month, year = [int(x) for x in match.groups()]
            if year < 100:
                year += 2000
            try:
                date_value = datetime(year, month, day).date().isoformat()
                break
            except ValueError:
                pass
    found = sum(v is not None and v != '' for v in (liters, price, total, station, date_value))
    confidence = min(99, 28 + found * 14 + (15 if liters and price else 0))
    return {
        'liters': round(liters, 2) if liters is not None else None,
        'price_per_liter': round(price, 3) if price is not None else None,
        'total': round(total, 2) if total is not None else None,
        'station': station,
        'date': date_value,
        'confidence': confidence,
        'recognized_fields': found,
    }


def scan_receipt(data_url: str) -> dict[str, Any]:
    raw, mime = _image_data_url(data_url)
    suffix = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}[mime]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix='receipt_scan_', suffix=suffix, dir=DATA_DIR, delete=False) as handle:
            handle.write(raw)
            path = Path(handle.name)
        proc = subprocess.run(
            ['tesseract', str(path), 'stdout', '-l', 'nld+eng', '--psm', '6'],
            capture_output=True, text=True, timeout=25, check=False,
        )
        if proc.returncode != 0:
            raise ValueError('De tankbon kon niet worden herkend. Vul de waarden handmatig in.')
        result = parse_receipt_text(proc.stdout)
        if not result['recognized_fields']:
            raise ValueError('Geen bruikbare waarden op de tankbon gevonden.')
        return result
    except subprocess.TimeoutExpired:
        raise ValueError('Bonherkenning duurde te lang. Probeer een scherpere foto.')
    finally:
        if path:
            path.unlink(missing_ok=True)


def normalize_segment_type(value: Any) -> str:
    v = str(value or '').strip().lower()
    return v if v in {'business','private'} else ''


def known_places_all() -> list[dict[str, Any]]:
    with DB_LOCK, db() as con:
        return [dict(r) for r in con.execute('SELECT * FROM known_places ORDER BY name COLLATE NOCASE, id')]


def known_place_by_id(place_id: int | None) -> dict[str, Any] | None:
    if not place_id:
        return None
    with DB_LOCK, db() as con:
        row = con.execute('SELECT * FROM known_places WHERE id=?', (int(place_id),)).fetchone()
    return dict(row) if row else None


def match_known_place(lat: float | None, lon: float | None) -> dict[str, Any] | None:
    if lat is None or lon is None:
        return None
    best = None
    best_dist = None
    for p in known_places_all():
        d = haversine_m(float(lat), float(lon), float(p['latitude']), float(p['longitude']))
        if d <= max(30, int(p.get('radius_m') or 180)) and (best_dist is None or d < best_dist):
            best, best_dist = dict(p), d
    if best is not None:
        best['distance_m'] = round(float(best_dist or 0))
    return best


def save_known_place(payload: dict[str, Any], place_id: int | None = None) -> dict[str, Any]:
    name = str(payload.get('name') or '').strip()[:80]
    if not name:
        raise ValueError('Geef de bekende plek een naam, bijvoorbeeld Thuis of School.')
    lat, lon = to_float(payload.get('latitude')), to_float(payload.get('longitude'))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError('Leg eerst een geldige locatie vast.')
    category = str(payload.get('category') or 'other').strip().lower()
    if category not in {'home','school','work','client','family','private','other'}:
        category = 'other'
    radius = int(max(40, min(1000, to_float(payload.get('radius_m')) or 180)))
    arrival = str(payload.get('arrival_trip_type') or 'ask').strip().lower()
    depart = str(payload.get('unknown_departure_trip_type') or 'ask').strip().lower()
    if arrival not in {'ask','business','private'}: arrival = 'ask'
    if depart not in {'ask','business','private'}: depart = 'ask'
    stamp = iso_local()
    with DB_LOCK, db() as con:
        if place_id:
            exists = con.execute('SELECT id FROM known_places WHERE id=?', (int(place_id),)).fetchone()
            if not exists: raise ValueError('Bekende plek niet gevonden.')
            con.execute('''UPDATE known_places SET name=?,category=?,latitude=?,longitude=?,radius_m=?,arrival_trip_type=?,unknown_departure_trip_type=?,updated_at=? WHERE id=?''',
                        (name,category,lat,lon,radius,arrival,depart,stamp,int(place_id)))
            pid=int(place_id)
            audit('update','known_place',pid,{'name':name,'category':category},con=con)
        else:
            cur=con.execute('''INSERT INTO known_places(name,category,latitude,longitude,radius_m,arrival_trip_type,unknown_departure_trip_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                            (name,category,lat,lon,radius,arrival,depart,stamp,stamp))
            pid=int(cur.lastrowid)
            audit('create','known_place',pid,{'name':name,'category':category},con=con)
        con.commit()
    place = known_place_by_id(pid) or {}
    if setting_bool('assistant_sync_zones', True):
        threading.Thread(target=sync_known_place_zone, args=(pid,), daemon=True).start()
    return place


def delete_known_place(place_id: int) -> None:
    zone_id = None
    with DB_LOCK, db() as con:
        row=con.execute('SELECT * FROM known_places WHERE id=?',(int(place_id),)).fetchone()
        if not row: return
        zone_id = row['ha_zone_id'] if 'ha_zone_id' in row.keys() else None
        con.execute('UPDATE trip_stops SET known_place_id=NULL WHERE known_place_id=?',(int(place_id),))
        con.execute('DELETE FROM known_places WHERE id=?',(int(place_id),))
        audit('delete','known_place',int(place_id),dict(row),con=con)
        con.commit()
    if zone_id and setting_bool('assistant_sync_zones', True):
        threading.Thread(target=delete_assistant_zone, args=(str(zone_id),), daemon=True).start()



def setting_bool(key: str, default: bool = False) -> bool:
    value = str(get_settings().get(key, '1' if default else '0')).strip().lower()
    return value in {'1', 'true', 'yes', 'on', 'aan'}


def setting_int(key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(get_settings().get(key, default)))))
    except Exception:
        return default


def assistant_config() -> dict[str, Any]:
    st = get_settings()
    mode = str(st.get('assistant_mode') or 'assistant').strip().lower()
    if mode not in {'manual', 'assistant', 'autopilot'}:
        mode = 'assistant'
    return {
        'enabled': str(st.get('assistant_enabled', '0')).lower() in {'1','true','yes','on','aan'},
        'mode': mode,
        'auto_confidence': setting_int('assistant_auto_confidence', 95, 70, 100),
        'location_entity': str(st.get('assistant_location_entity') or '').strip(),
        'notify_service': str(st.get('assistant_notify_service') or '').strip(),
        'sync_zones': str(st.get('assistant_sync_zones', '1')).lower() in {'1','true','yes','on','aan'},
        'unknown_stops': str(st.get('assistant_unknown_stops', '1')).lower() in {'1','true','yes','on','aan'},
        'check_seconds': setting_int('assistant_check_seconds', 20, 10, 300),
        'unknown_stop_minutes': setting_int('assistant_unknown_stop_minutes', 4, 2, 30),
        'fast_stop_seconds': setting_int('assistant_fast_stop_seconds', 30, 20, 180),
        'min_trip_m': setting_int('assistant_min_trip_m', 500, 100, 10000),
        'push_provinces': ['Groningen', 'Drenthe'],
    }


def assistant_state_get(key: str, default: Any = None) -> Any:
    with DB_LOCK, db() as con:
        row = con.execute('SELECT value FROM assistant_state WHERE key=?', (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row['value'])
    except Exception:
        return row['value']


def assistant_state_set(key: str, value: Any) -> None:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    with DB_LOCK, db() as con:
        con.execute(
            'INSERT INTO assistant_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, raw)
        )
        con.commit()


DIAGNOSTIC_LOCK = threading.Lock()


def diagnostic_event(event: str, **details: Any) -> None:
    """Bounded, persistent support log. Never include raw API data or errors."""
    allowed = {'tracked_m', 'minimum_m', 'accuracy_m', 'speed_m_s',
               'stationary_seconds', 'threshold_seconds', 'province_allowed',
               'samples', 'position_changed', 'incomplete', 'ha_state_age_seconds'}
    safe = {k: v for k, v in details.items() if k in allowed and
            (v is None or isinstance(v, (int, float, bool)))}
    try:
        with DIAGNOSTIC_LOCK:
            rows = assistant_state_get('diagnostic_log', []) or []
            now = iso_local()
            if rows and rows[-1]['event'] == event and rows[-1]['details'] == safe:
                if (parse_dt(now) - parse_dt(rows[-1]['at'])).total_seconds() < 60:
                    return
            rows.append({'at': now, 'event': event, 'details': safe})
            cutoff = time.time() - 48 * 3600
            rows = [r for r in rows[-500:] if parse_dt(r['at']).timestamp() >= cutoff]
            assistant_state_set('diagnostic_log', rows)
    except Exception:
        pass  # Diagnostics must not interrupt tracking or sending.


def diagnostic_report() -> dict[str, Any]:
    cfg = assistant_config()
    rows = assistant_state_get('diagnostic_log', []) or []
    cutoff = time.time() - 48 * 3600
    return {'version': APP_VERSION, 'generated_at': iso_local(),
            'note': 'GPS-polls zijn geen bewijs van een nieuwe iPhone-locatiemeting. HA-acceptatie is geen afleverbevestiging.',
            'config': {k: cfg.get(k) for k in ('enabled', 'mode', 'min_trip_m', 'fast_stop_seconds')},
            'tracker_configured': bool(cfg.get('location_entity')),
            'notification_configured': bool(cfg.get('notify_service')),
            'events': [r for r in rows[-500:] if parse_dt(r['at']).timestamp() >= cutoff]}


def calibration_vehicle() -> str:
    settings = get_settings()
    return str(settings.get('license_plate') or settings.get('vehicle_name') or 'default').strip().upper()


def distance_calibration() -> dict[str, Any]:
    """Bounded median, never trained from simply accepting a proposal."""
    import statistics
    enabled = setting_bool('distance_learning_enabled', True)
    with DB_LOCK, db() as con:
        rows = con.execute('SELECT gps_km,actual_km FROM distance_calibration WHERE vehicle=? ORDER BY id DESC LIMIT 30', (calibration_vehicle(),)).fetchall()
    ratios = [float(r['actual_km']) / float(r['gps_km']) for r in rows]
    stable = len(ratios) >= 5 and statistics.median([abs(x - statistics.median(ratios)) for x in ratios]) <= .04
    factor = max(.90, min(1.10, statistics.median(ratios))) if enabled and stable else 1.0
    return {'enabled': enabled, 'samples': len(ratios), 'ready': bool(enabled and stable), 'factor': round(factor, 4)}


def learn_distance(source: str, gps_km: float, actual_km: float, samples: int, checked: bool, *, con: sqlite3.Connection) -> None:
    # Both endpoint odometers must have been checked by the user. Reject short
    # segments, sparse tracks and large discrepancies rather than hiding gaps.
    if not checked or not setting_bool('distance_learning_enabled', True):
        return
    if not math.isfinite(gps_km) or not math.isfinite(actual_km) or gps_km < 5 or samples < 5:
        return
    if not .85 <= actual_km / gps_km <= 1.15:
        return
    con.execute('INSERT OR IGNORE INTO distance_calibration(vehicle,source,gps_km,actual_km,created_at) VALUES(?,?,?,?,?)',
                (calibration_vehicle(), source, gps_km, actual_km, iso_local()))


def advance_draft_route(runtime: dict[str, Any], lat: float, lon: float, accuracy: float | None, now: datetime) -> None:
    """Accumulate a draft without creating any official trip or odometer event."""
    if not runtime.get('departure_at'):
        return
    prev_lat, prev_lon = to_float(runtime.get('route_lat')), to_float(runtime.get('route_lon'))
    if prev_lat is None or prev_lon is None:
        runtime.update(route_lat=lat, route_lon=lon, route_at=iso_local(now))
        return
    dt = max(1, (now - parse_dt(runtime.get('route_at') or iso_local(now))).total_seconds())
    step = haversine_m(lat, lon, prev_lat, prev_lon)
    if step < 8:
        return
    if dt > 300 or (accuracy is not None and accuracy > 100) or step > 60 * dt + 100:
        runtime['route_incomplete'] = True
    elif step >= 35:
        runtime['route_m'] = float(runtime.get('route_m') or 0) + step
        runtime['route_samples'] = int(runtime.get('route_samples') or 0) + 1
    runtime.update(route_lat=lat, route_lon=lon, route_at=iso_local(now))


def arrival_proposal(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = assistant_state_get(f'arrival_route_{row["id"]}', {}) or {}
    base = latest_odometer_before(row.get('departure_at') or row['detected_at'])
    raw_km = float(snapshot.get('route_m') or 0) / 1000
    calibration = distance_calibration()
    usable = bool(base is not None and raw_km > 0 and int(snapshot.get('route_samples') or 0) >= 3 and not snapshot.get('route_incomplete'))
    return {'start_odometer': base, 'gps_km': round(raw_km, 2),
            'suggested_odometer': round(base + raw_km * calibration['factor']) if usable else None,
            'route_complete': usable, 'calibration': calibration}


def zone_icon(category: str) -> str:
    return {
        'home': 'mdi:home',
        'school': 'mdi:school',
        'work': 'mdi:briefcase',
        'client': 'mdi:account-tie',
        'family': 'mdi:account-group',
        'private': 'mdi:heart',
        'other': 'mdi:map-marker',
    }.get(str(category or 'other'), 'mdi:map-marker')


def assistant_zone_name(place: dict[str, Any]) -> str:
    return f"RT {str(place.get('name') or 'Plek')[:70]}"


def sync_known_place_zone(place_id: int) -> None:
    place = known_place_by_id(place_id)
    if not place:
        return
    try:
        zones = ha_ws_command({'type': 'zone/list'}) or []
        stored_id = str(place.get('ha_zone_id') or '')
        existing = next((z for z in zones if str(z.get('id') or '') == stored_id), None) if stored_id else None
        if not existing:
            wanted_name = assistant_zone_name(place)
            existing = next((z for z in zones if str(z.get('name') or '') == wanted_name), None)
        fields = {
            'name': assistant_zone_name(place),
            'latitude': float(place['latitude']),
            'longitude': float(place['longitude']),
            'radius': float(max(50, int(place.get('radius_m') or 180))),
            'passive': True,
            'icon': zone_icon(str(place.get('category') or 'other')),
        }
        if existing:
            zone_id = str(existing.get('id') or '')
            result = ha_ws_command({'type': 'zone/update', 'zone_id': zone_id, **fields}) or existing
        else:
            result = ha_ws_command({'type': 'zone/create', **fields}) or {}
            zone_id = str(result.get('id') or '')
        if not zone_id:
            zone_id = str(result.get('id') or '')
        with DB_LOCK, db() as con:
            con.execute(
                'UPDATE known_places SET ha_zone_id=?,ha_zone_synced_at=?,ha_zone_error=NULL WHERE id=?',
                (zone_id or None, iso_local(), int(place_id))
            )
            con.commit()
    except Exception as exc:
        with DB_LOCK, db() as con:
            con.execute(
                'UPDATE known_places SET ha_zone_error=? WHERE id=?',
                (str(exc)[:300], int(place_id))
            )
            con.commit()


def sync_all_known_place_zones() -> dict[str, int]:
    ok = 0
    failed = 0
    for place in known_places_all():
        sync_known_place_zone(int(place['id']))
        refreshed = known_place_by_id(int(place['id'])) or {}
        if refreshed.get('ha_zone_error'):
            failed += 1
        else:
            ok += 1
    return {'ok': ok, 'failed': failed}


def delete_assistant_zone(zone_id: str) -> None:
    try:
        ha_ws_command({'type': 'zone/delete', 'zone_id': str(zone_id)})
    except Exception:
        pass


def latest_odometer_before(created_at: str | None) -> float | None:
    if not created_at:
        return current_odometer(rows_events())
    with DB_LOCK, db() as con:
        row = con.execute(
            'SELECT odometer FROM events WHERE created_at<=? ORDER BY created_at DESC,id DESC LIMIT 1',
            (created_at,)
        ).fetchone()
    return float(row['odometer']) if row else None


def assistant_arrivals(limit: int = 12, include_done: bool = False) -> list[dict[str, Any]]:
    where = '' if include_done else "WHERE status IN ('pending','confirmed')"
    with DB_LOCK, db() as con:
        rows = [dict(r) for r in con.execute(
            f'SELECT * FROM assistant_arrivals {where} ORDER BY id DESC LIMIT ?',
            (max(1, min(100, int(limit))),)
        )]
    out = []
    for r in rows:
        origin = known_place_by_id(r.get('origin_known_place_id'))
        dest = known_place_by_id(r.get('destination_known_place_id'))
        r['origin_name'] = (origin or {}).get('name') or 'Onbekende vertrekplek'
        r['destination_name'] = (dest or {}).get('name') or r.get('destination_label') or 'Onbekende bestemming'
        r['suggested_type_label'] = trip_type_label(str(r.get('suggested_type') or '')) if r.get('suggested_type') else ''
        r['confirmed_type_label'] = trip_type_label(str(r.get('confirmed_type') or '')) if r.get('confirmed_type') else ''
        r['date_label'] = parse_dt(r['detected_at']).strftime('%d-%m %H:%M')
        r['start_odometer'] = latest_odometer_before(r.get('departure_at') or r.get('detected_at'))
        r['proposal'] = arrival_proposal(r)
        out.append(r)
    return out


def assistant_runtime_public() -> dict[str, Any]:
    runtime = assistant_state_get('runtime', {}) or {}
    current = known_place_by_id(runtime.get('current_place_id'))
    departed = known_place_by_id(runtime.get('departed_from_place_id'))
    return {
        'seeded': bool(runtime.get('seeded')),
        'current_place': (current or {}).get('name') or '',
        'departed_from': (departed or {}).get('name') or '',
        'draft_active': bool(runtime.get('departure_at') and not runtime.get('unknown_at_stop')),
        'draft_km': round(float(runtime.get('route_m') or 0) / 1000, 1),
        'last_seen_at': runtime.get('last_seen_at') or '',
        'last_error': assistant_state_get('last_error', '') or '',
        'ws_connected': bool(assistant_state_get('ws_connected', False)),
    }


def _assistant_suggestion(origin_place_id: int | None, dest_place_id: int | None, lat: float, lon: float) -> dict[str, Any]:
    origin = known_place_by_id(origin_place_id)
    dest = known_place_by_id(dest_place_id)
    if dest and str(dest.get('arrival_trip_type') or 'ask') in {'business','private'}:
        t = str(dest['arrival_trip_type'])
        return {'suggested_type': t, 'reason': f'Bestemming {dest["name"]} staat als {trip_type_label(t).lower()} ingesteld', 'confidence': .98}
    if origin and not dest and str(origin.get('unknown_departure_trip_type') or 'ask') in {'business','private'}:
        t = str(origin['unknown_departure_trip_type'])
        return {'suggested_type': t, 'reason': f'Vanaf {origin["name"]} naar onbekende bestemming: {trip_type_label(t)}', 'confidence': .90}
    memory = _route_memory_suggestion(origin_place_id, lat, lon)
    if memory:
        return memory
    return {'suggested_type': '', 'reason': 'Geen vaste regel gevonden — kies zelf', 'confidence': 0.0}


def create_assistant_arrival(
    origin_place_id: int | None,
    destination_place_id: int | None,
    lat: float,
    lon: float,
    accuracy: float | None,
    departure_at: str | None,
    destination_label: str = '',
    route_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now = iso_local()
    cutoff = iso_local(now_local() - timedelta(minutes=20))
    with DB_LOCK, db() as con:
        recent = [dict(r) for r in con.execute(
            "SELECT * FROM assistant_arrivals WHERE detected_at>=? AND status IN ('pending','confirmed') ORDER BY id DESC",
            (cutoff,)
        )]
    for r in recent:
        if haversine_m(lat, lon, float(r['destination_latitude']), float(r['destination_longitude'])) < 180:
            return r
    dest = known_place_by_id(destination_place_id)
    if dest:
        destination_label = str(dest.get('name') or destination_label)
    elif not destination_label:
        try:
            geo = google_reverse_geocode(lat, lon)
            destination_label = str(geo.get('address') or '')[:180]
        except Exception:
            destination_label = ''
    if not destination_label:
        destination_label = f'{lat:.5f}, {lon:.5f}'
    suggestion = _assistant_suggestion(origin_place_id, destination_place_id, lat, lon)
    cfg = assistant_config()
    suggested_type = normalize_segment_type(suggestion.get('suggested_type'))
    auto_confirm = (
        cfg.get('mode') == 'autopilot' and suggested_type and
        float(suggestion.get('confidence') or 0) * 100 >= int(cfg.get('auto_confidence') or 95)
    )
    status = 'confirmed' if auto_confirm else 'pending'
    classification_source = 'autopilot' if auto_confirm else None
    handled_at = now if auto_confirm else None
    with DB_LOCK, db() as con:
        cur = con.execute('''
            INSERT INTO assistant_arrivals(
                detected_at,departure_at,origin_known_place_id,destination_known_place_id,
                destination_latitude,destination_longitude,destination_accuracy,destination_label,
                suggested_type,suggestion_reason,suggestion_confidence,status,confirmed_type,
                classification_source,handled_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            now, departure_at, origin_place_id, destination_place_id,
            lat, lon, accuracy, destination_label[:180],
            suggested_type or None,
            str(suggestion.get('reason') or '')[:240],
            float(suggestion.get('confidence') or 0),
            status, suggested_type if auto_confirm else None,
            classification_source, handled_at,
        ))
        arrival_id = int(cur.lastrowid)
        audit('assistant_arrival', 'assistant', arrival_id, {
            'origin_place_id': origin_place_id, 'destination_place_id': destination_place_id,
            'destination': destination_label, 'suggestion': suggestion, 'autopilot': auto_confirm,
        }, con=con)
        con.commit()
    if route_snapshot:
        assistant_state_set(f'arrival_route_{arrival_id}', {key: route_snapshot.get(key) for key in ('route_m', 'route_samples', 'route_incomplete', 'departure_at')})
    item = next((x for x in assistant_arrivals(30) if int(x['id']) == arrival_id), None)
    if item:
        threading.Thread(target=send_assistant_notification, args=(item,), daemon=True).start()
    return item


def confirm_assistant_arrival(arrival_id: int, trip_type: str, source: str = 'app') -> dict[str, Any]:
    t = normalize_segment_type(trip_type)
    if not t:
        raise ValueError('Kies Privé of Zakelijk.')
    with DB_LOCK, db() as con:
        row = con.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
        if not row:
            raise ValueError('Ritsuggestie niet gevonden.')
        if str(row['status']) in {'completed','dismissed'}:
            return dict(row)
        con.execute(
            "UPDATE assistant_arrivals SET status='confirmed',confirmed_type=?,classification_source=?,handled_at=? WHERE id=?",
            (t, source[:40], iso_local(), int(arrival_id))
        )
        audit('assistant_confirm', 'assistant', int(arrival_id), {'trip_type': t, 'source': source}, con=con)
        con.commit()
    return next((x for x in assistant_arrivals(50, True) if int(x['id']) == int(arrival_id)), {})


def dismiss_assistant_arrival(arrival_id: int) -> None:
    with DB_LOCK, db() as con:
        con.execute(
            "UPDATE assistant_arrivals SET status='dismissed',handled_at=? WHERE id=?",
            (iso_local(), int(arrival_id))
        )
        audit('assistant_dismiss', 'assistant', int(arrival_id), {}, con=con)
        con.commit()


def complete_assistant_arrival(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with DB_LOCK:
        return _complete_assistant_arrival(arrival_id, payload)


def _complete_assistant_arrival(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with DB_LOCK, db() as con:
        row = con.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
    if not row:
        raise ValueError('Ritsuggestie niet gevonden.')
    r = dict(row)
    if r.get('status') not in ('pending', 'confirmed'):
        raise ValueError('Deze ritsuggestie is al verwerkt.')
    trip_type = normalize_segment_type(payload.get('trip_type')) or normalize_segment_type(r.get('confirmed_type')) or normalize_segment_type(r.get('suggested_type'))
    if not trip_type:
        raise ValueError('Kies Privé of Zakelijk.')
    end_odo = to_float(payload.get('odometer'))
    if end_odo is None or not math.isfinite(end_odo) or end_odo < 0:
        raise ValueError('Vul de kilometerstand bij aankomst in.')
    active = active_business_trip()
    if active and active.get('stops') and parse_dt(active['stops'][-1]['created_at']) >= parse_dt(r['detected_at']):
        raise ValueError('Deze aankomst is ouder dan de laatste opgeslagen stop. Controleer de ritgeschiedenis.')
    if active and active.get('stops') and r.get('departure_at') and parse_dt(active['stops'][-1]['created_at']) > parse_dt(r['departure_at']):
        raise ValueError('Een deel van dit voorstel is al geregistreerd. Controleer de ritgeschiedenis.')
    point = {
        'odometer': end_odo,
        'created_at': r['detected_at'],
        'latitude': float(r['destination_latitude']),
        'longitude': float(r['destination_longitude']),
        'location_accuracy': to_float(r.get('destination_accuracy')),
        'location_source': 'background_assistant',
        'place_id': None,
        'manual_label': str(r.get('destination_label') or '')[:120] or None,
        'note': 'Automatisch herkende aankomst',
        'known_place_id': r.get('destination_known_place_id'),
    }
    if active:
        result = add_business_stop({
            'odometer': end_odo,
            'created_at': r['detected_at'],
            'latitude': point['latitude'],
            'longitude': point['longitude'],
            'location_accuracy': point['location_accuracy'],
            'location_source': point['location_source'],
            'manual_label': point['manual_label'],
            'note': point['note'],
            'segment_trip_type': trip_type,
        }, finish=payload.get('finish') is True)
        trip_id = int((result.get('trip') or {}).get('id') or active['id'])
    else:
        origin = known_place_by_id(r.get('origin_known_place_id'))
        if not origin:
            raise ValueError('Startpunt van deze automatische rit is onbekend. Gebruik de gewone ritregistratie.')
        start_odo = to_float(payload.get('start_odometer'))
        if start_odo is None:
            start_odo = latest_odometer_before(r.get('departure_at') or r.get('detected_at'))
        if start_odo is None:
            raise ValueError('Vul ook de kilometerstand bij vertrek in.')
        if not math.isfinite(start_odo) or start_odo < 0:
            raise ValueError('Vul een geldige vertrekstand in.')
        if end_odo < start_odo:
            raise ValueError('Aankomst-kilometerstand is lager dan de vertrekstand.')
        start_at = r.get('departure_at') or iso_local(parse_dt(r['detected_at']) - timedelta(minutes=1))
        with db() as con:
            overlap = con.execute('SELECT id FROM business_trips WHERE started_at<? AND COALESCE(ended_at,?)>? LIMIT 1',
                                  (r['detected_at'], r['detected_at'], start_at)).fetchone()
        if overlap:
            raise ValueError('Deze aankomst overlapt een opgeslagen rit. Controleer de ritgeschiedenis om dubbeltelling te voorkomen.')
        for timestamp, odo in ((start_at, start_odo), (r['detected_at'], end_odo)):
            valid, message = validate_odometer(timestamp, odo)
            if not valid:
                raise ValueError(message)
        start_point = {
            'odometer': start_odo,
            'created_at': start_at,
            'latitude': float(origin['latitude']),
            'longitude': float(origin['longitude']),
            'location_accuracy': None,
            'location_source': 'background_assistant',
            'place_id': None,
            'manual_label': str(origin.get('name') or '')[:120] or None,
            'note': 'Automatisch herkend vertrek',
            'known_place_id': int(origin['id']),
        }
        suggestion = {
            'suggested_type': normalize_segment_type(r.get('suggested_type')),
            'reason': str(r.get('suggestion_reason') or ''),
            'confidence': float(r.get('suggestion_confidence') or 0),
        }
        with DB_LOCK, db() as con:
            cur = con.execute('''
                INSERT INTO business_trips(started_at,ended_at,status,purpose,client,note,trip_type,private_detour_km,modified_at)
                VALUES(?,?,'completed',NULL,NULL,?,?,0,?)
            ''', (start_at, r['detected_at'], 'Automatisch herkende rit', trip_type, iso_local()))
            trip_id = int(cur.lastrowid)
            _insert_trip_stop(con, trip_id, start_point, 0, 'Automatische rit start')
            _insert_trip_stop(con, trip_id, point, 1, f'Automatische rit einde ({trip_type_label(trip_type)})', trip_type, suggestion)
            remember_segment(start_point, point, trip_type, con=con)
            audit('assistant_complete', 'trip', trip_id, {'assistant_arrival_id': int(arrival_id), 'trip_type': trip_type}, con=con)
            con.commit()
    with DB_LOCK, db() as con:
        snapshot = assistant_state_get(f'arrival_route_{arrival_id}', {}) or {}
        if not active and not snapshot.get('route_incomplete'):
            learn_distance(f'arrival:{arrival_id}', float(snapshot.get('route_m') or 0) / 1000,
                           end_odo - start_odo, int(snapshot.get('route_samples') or 0),
                           payload.get('odometer_checked') is True, con=con)
        con.execute(
            "UPDATE assistant_arrivals SET status='completed',confirmed_type=?,classification_source=COALESCE(classification_source,'app'),handled_at=?,odometer=?,trip_id=? WHERE id=?",
            (trip_type, iso_local(), end_odo, trip_id, int(arrival_id))
        )
        con.commit()
    publish_sensors_async()
    return {'ok': True, 'trip_id': trip_id}


def province_allowed_for_push(lat: float, lon: float) -> tuple[bool, str, dict[str, Any]]:
    """Provincies waar de gebruiker stopmeldingen wil ontvangen."""
    geo = google_reverse_geocode(lat, lon)
    province = str(geo.get('province') or '').strip()
    allowed = province.lower() in {'groningen', 'drenthe', 'overijssel'}
    return allowed, province, geo


def trip_distance_tracking_public() -> dict[str, Any]:
    """Publieke schatting voor de actieve rit, gebaseerd op achtergrond-GPS."""
    trip = active_business_trip()
    if not trip or not trip.get('stops'):
        diagnostic_event('geen_actieve_rit_met_startpunt')
        return {'active': False, 'tracked_km': 0.0, 'suggested_odometer': None, 'sample_count': 0}
    last = trip['stops'][-1]
    state = assistant_state_get('trip_distance_tracking', {}) or {}
    same_trip = int(state.get('trip_id') or -1) == int(trip['id'])
    same_stop = int(state.get('stop_id') or -1) == int(last.get('id') or -2)
    tracked_m = float(state.get('segment_m') or 0.0) if same_trip and same_stop else 0.0
    base = float(last['odometer'])
    calibration = distance_calibration()
    suggested = round(base + tracked_m / 1000.0 * calibration['factor'])
    return {
        'active': True,
        'trip_id': int(trip['id']),
        'stop_id': int(last.get('id') or 0),
        'base_odometer': base,
        'stop_prompt': state.get('stop_prompt') if same_trip and same_stop else None,
        'calibration': calibration,
        'tracked_km': round(tracked_m / 1000.0, 1),
        'suggested_odometer': suggested if not state.get('incomplete') and same_trip and same_stop and int(state.get('sample_count') or 0) >= 3 else None,
        'sample_count': int(state.get('sample_count') or 0) if same_trip and same_stop else 0,
        'last_update': state.get('last_update') if same_trip and same_stop else None,
    }


def reset_trip_distance_tracking(trip: dict[str, Any] | None = None) -> None:
    if not trip or not trip.get('stops'):
        assistant_state_set('trip_distance_tracking', {})
        return
    last = trip['stops'][-1]
    assistant_state_set('trip_distance_tracking', {
        'trip_id': int(trip['id']),
        'stop_id': int(last.get('id') or 0),
        'segment_m': 0.0,
        'sample_count': 0,
        'incomplete': False,
        'last_lat': to_float(last.get('latitude')),
        'last_lon': to_float(last.get('longitude')),
        'last_update': iso_local(),
        'stationary_since': None,
        'prompted': False,
        'last_prompt_at': None,
    })


def track_active_trip_distance(loc: dict[str, Any], cfg: dict[str, Any]) -> None:
    """Best-effort routeafstand vanaf de laatste handmatige stop.

    Dit is een suggestie, geen vervanging van de echte kilometerteller.
    """
    trip = active_business_trip()
    if not trip or not trip.get('stops'):
        assistant_state_set('trip_distance_tracking', {})
        return
    last = trip['stops'][-1]
    state = assistant_state_get('trip_distance_tracking', {}) or {}
    trip_id = int(trip['id'])
    stop_id = int(last.get('id') or 0)
    if int(state.get('trip_id') or -1) != trip_id or int(state.get('stop_id') or -1) != stop_id:
        reset_trip_distance_tracking(trip)
        state = assistant_state_get('trip_distance_tracking', {}) or {}

    lat, lon = to_float(loc.get('latitude')), to_float(loc.get('longitude'))
    if lat is None or lon is None:
        diagnostic_event('gps_coordinaten_ontbreken')
        return
    accuracy = to_float(loc.get('accuracy'))
    if accuracy is not None and accuracy > 250:
        diagnostic_event('gps_te_onnauwkeurig', accuracy_m=round(accuracy))
        return
    now = now_local()
    prev_lat, prev_lon = to_float(state.get('last_lat')), to_float(state.get('last_lon'))
    prev_dt = parse_dt(state.get('last_update')) if state.get('last_update') else now
    step = haversine_m(float(lat), float(lon), prev_lat, prev_lon) if prev_lat is not None and prev_lon is not None else 0.0
    dt_s = max(1.0, (now - prev_dt).total_seconds())
    speed = to_float(loc.get('speed'))
    if step >= 8 and dt_s > 300:
        state['incomplete'] = True

    # GPS-jitter bij stilstand niet als routeafstand tellen; onrealistische sprongen ook niet.
    max_step = 60.0 * dt_s + 250.0  # ruim < 216 km/h + GPS-marge
    moving = (speed is not None and speed > 3.0) or step >= 35.0
    if 8.0 <= step <= max_step and moving:
        state['segment_m'] = float(state.get('segment_m') or 0.0) + step
        state['sample_count'] = int(state.get('sample_count') or 0) + 1

    if moving:
        state['stationary_since'] = None
        state['stop_prompt'] = None
        if step > 220:
            state['prompted'] = False
    else:
        if not state.get('stationary_since'):
            state['stationary_since'] = iso_local(now)

    # Bewaar het tijdstip van de laatst werkelijk gewijzigde GPS-positie.
    # De HA-worker pollt vaker dan iOS vaak nieuwe GPS-data publiceert; als we
    # bij identieke coördinaten de klok zouden opschuiven, zou een latere grote
    # maar geldige sprong ten onrechte als 'onrealistisch' worden weggefilterd.
    if prev_lat is None or prev_lon is None or step >= 8.0:
        state['last_lat'] = float(lat)
        state['last_lon'] = float(lon)
        state['last_update'] = iso_local(now)
    assistant_state_set('trip_distance_tracking', state)

    # Overijssel: 10 seconden gedetecteerde stilstand; andere provincies
    # behouden de ingestelde wachttijd. Geen definitieve opslag zonder akkoord.
    tracked_m = float(state.get('segment_m') or 0.0)
    since_raw = state.get('stationary_since')
    ha_age = None
    if loc.get('ha_last_updated'):
        try:
            ha_age = max(0, round((now - parse_dt(loc['ha_last_updated'])).total_seconds()))
        except (ValueError, TypeError):
            pass
    diagnostic_event('achtergrondcontrole', tracked_m=round(tracked_m),
                     minimum_m=cfg.get('min_trip_m'), accuracy_m=accuracy,
                     speed_m_s=speed, position_changed=step >= 8,
                     samples=state.get('sample_count', 0), incomplete=bool(state.get('incomplete')),
                     ha_state_age_seconds=ha_age)
    if state.get('prompted') or tracked_m < float(cfg.get('min_trip_m') or 500) or not since_raw:
        diagnostic_event('stop_al_aangeboden' if state.get('prompted') else
                         'afstand_onder_minimum' if tracked_m < float(cfg.get('min_trip_m') or 500) else 'beweging_gedetecteerd')
        return
    since = parse_dt(str(since_raw))
    elapsed = (now - since).total_seconds()
    if elapsed < min(10, int(cfg.get('fast_stop_seconds') or 30)):
        diagnostic_event('wachten_op_stilstand', stationary_seconds=round(elapsed))
        return
    allowed, province, geo = province_allowed_for_push(float(lat), float(lon))
    if not allowed:
        diagnostic_event('provincie_onbekend_of_niet_toegestaan', province_allowed=False)
        return
    threshold = 10 if province.lower() == 'overijssel' else int(cfg.get('fast_stop_seconds') or 30)
    if elapsed < threshold:
        diagnostic_event('wachten_op_stilstand', stationary_seconds=round(elapsed), threshold_seconds=threshold)
        return
    state['stop_prompt'] = {'id': f'{trip["id"]}:{last["id"]}:{iso_local(now)}',
                            'trip_id': int(trip['id']), 'province': province,
                            'address': str(geo.get('address') or province), 'created_at': iso_local(now)}
    state['prompted'] = True
    state['last_prompt_at'] = iso_local(now)
    assistant_state_set('trip_distance_tracking', state)
    diagnostic_event('stop_herkend', stationary_seconds=round(elapsed), threshold_seconds=threshold)
    sent = send_active_trip_stop_notification(trip, tracked_m, province, geo)
    diagnostic_event('stop_push_geaccepteerd_door_ha' if sent else 'stop_push_mislukt')


def send_active_trip_stop_notification(trip: dict[str, Any], tracked_m: float, province: str, geo: dict[str, Any]) -> bool:
    cfg = assistant_config()
    service = cfg.get('notify_service') or ''
    if not service.startswith('notify.') or not trip.get('stops'):
        return False
    svc = service.split('.', 1)[1]
    km = tracked_m / 1000.0
    address = str(geo.get('address') or province or 'huidige locatie')
    payload = {
        'title': f'🏁 Rit & Tank · gestopt in {province}',
        'message': f'Wil je je actieve rit opslaan? Je lijkt gestopt bij {address}. Achtergrondroute: ca. {km:.1f} km. Open Rit & Tank om de tellerstand te controleren en de rit af te sluiten.',
        'data': {
            'tag': f'rit_tank_stop_{int(trip["id"])}',
            'url': '/675b3933_rit_tank',
            'actions': [
                {'action': 'URI', 'title': 'Open Rit & Tank', 'uri': '/675b3933_rit_tank'},
            ],
        },
    }
    try:
        ha_post(f'services/notify/{svc}', payload)
        return True
    except Exception as exc:
        assistant_state_set('last_error', f'Stopmelding: {exc}')
        return False


def send_assistant_notification(item: dict[str, Any]) -> bool:
    cfg = assistant_config()
    service = cfg.get('notify_service') or ''
    if not service.startswith('notify.'):
        return False
    lat = to_float(item.get('destination_latitude'))
    lon = to_float(item.get('destination_longitude'))
    if lat is None or lon is None:
        return False
    allowed, province, _geo = province_allowed_for_push(float(lat), float(lon))
    if not allowed:
        return False
    svc = service.split('.', 1)[1]
    suggested = normalize_segment_type(item.get('suggested_type'))
    suggestion_text = f" · voorstel: {trip_type_label(suggested)}" if suggested else ''
    message = f"{item.get('origin_name','Vertrek')} → {item.get('destination_name','Bestemming')}{suggestion_text}. Bevestig ritsoort; kilometerstand vul je later in Rit & Tank in."
    aid = int(item['id'])
    payload = {
        'title': f"🚗 Rit & Tank · {item.get('destination_name','Aankomst')} · {province}",
        'message': message,
        'data': {
            'tag': f'rit_tank_arrival_{aid}',
            'url': '/675b3933_rit_tank',
            'actions': [
                {'action': f'RITTANK_PRIVATE_{aid}', 'title': 'Privé'},
                {'action': f'RITTANK_BUSINESS_{aid}', 'title': 'Zakelijk'},
                {'action': 'URI', 'title': 'Open Rit & Tank', 'uri': '/675b3933_rit_tank'},
            ],
        },
    }
    try:
        ha_post(f'services/notify/{svc}', payload)
        with DB_LOCK, db() as con:
            con.execute('UPDATE assistant_arrivals SET notification_sent=1 WHERE id=?', (aid,))
            con.commit()
        return True
    except Exception as exc:
        assistant_state_set('last_error', f'Pushmelding: {exc}')
        return False


def send_assistant_test_notification() -> None:
    cfg = assistant_config()
    service = cfg.get('notify_service') or ''
    if not service.startswith('notify.'):
        raise ValueError('Kies eerst een Home Assistant mobiele meldingsservice.')
    svc = service.split('.', 1)[1]
    ha_post(f'services/notify/{svc}', {
        'title': '🚗 Rit & Tank',
        'message': 'Achtergrond-ritassistent is gekoppeld. Meldingen komen op dit apparaat binnen.',
        'data': {'url': '/675b3933_rit_tank'},
    })


def _assistant_action_listener() -> None:
    backoff = 3
    while True:
        try:
            ws = _ha_ws_open(20)
            ws.settimeout(300)
            ws.send(json.dumps({'id': 1, 'type': 'subscribe_events', 'event_type': 'mobile_app_notification_action'}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get('id') == 1 and msg.get('type') == 'result':
                    if not msg.get('success'):
                        raise RuntimeError('Kon niet op notification actions abonneren.')
                    break
            backoff = 3
            assistant_state_set('ws_connected', True)
            while True:
                msg = json.loads(ws.recv())
                if msg.get('type') != 'event':
                    continue
                event = msg.get('event') or {}
                data = event.get('data') or {}
                action = str(data.get('action') or '')
                m = re.fullmatch(r'RITTANK_(PRIVATE|BUSINESS)_(\d+)', action)
                if m:
                    trip_type = 'private' if m.group(1) == 'PRIVATE' else 'business'
                    try:
                        confirm_assistant_arrival(int(m.group(2)), trip_type, 'notification')
                    except Exception as exc:
                        assistant_state_set('last_error', f'Notificatieactie: {exc}')
        except Exception as exc:
            assistant_state_set('ws_connected', False)
            assistant_state_set('last_error', f'WebSocket: {exc}')
            time.sleep(backoff)
            backoff = min(60, backoff * 2)


def _process_assistant_location(loc: dict[str, Any], cfg: dict[str, Any]) -> None:
    lat, lon = float(loc['latitude']), float(loc['longitude'])
    accuracy = to_float(loc.get('accuracy'))
    if accuracy is not None and accuracy > 500:
        assistant_state_set('last_error', f'Locatie te onnauwkeurig (±{accuracy:.0f} m).')
        return
    now = now_local()
    matched = match_known_place(lat, lon)
    runtime = assistant_state_get('runtime', {}) or {}
    previous_lat = to_float(runtime.get('last_lat'))
    previous_lon = to_float(runtime.get('last_lon'))
    previous_seen = runtime.get('last_seen_at')
    runtime['last_seen_at'] = iso_local(now)
    runtime['last_lat'] = lat
    runtime['last_lon'] = lon
    runtime['last_accuracy'] = accuracy
    if not runtime.get('seeded'):
        runtime.update({
            'seeded': True,
            'current_place_id': int(matched['id']) if matched else None,
            'departed_from_place_id': None,
            'departure_at': None,
            'entry_candidate_id': None,
            'entry_candidate_since': None,
            'stationary_since': None,
            'stationary_lat': lat,
            'stationary_lon': lon,
            'unknown_at_stop': False,
        })
        assistant_state_set('runtime', runtime)
        return

    current_id = runtime.get('current_place_id')
    matched_id = int(matched['id']) if matched else None
    advance_draft_route(runtime, lat, lon, accuracy, now)

    if current_id and matched_id == int(current_id):
        runtime['entry_candidate_id'] = None
        runtime['entry_candidate_since'] = None
        runtime['stationary_since'] = None
        runtime['unknown_at_stop'] = False
        runtime['stationary_lat'] = lat
        runtime['stationary_lon'] = lon
        assistant_state_set('runtime', runtime)
        return

    if current_id and matched_id != int(current_id):
        runtime.update(route_m=0.0, route_samples=0, route_incomplete=False,
                       route_lat=previous_lat, route_lon=previous_lon,
                       route_at=previous_seen or iso_local(now))
        runtime['departed_from_place_id'] = int(current_id)
        runtime['departure_at'] = iso_local(now)
        advance_draft_route(runtime, lat, lon, accuracy, now)
        runtime['current_place_id'] = None
        runtime['stationary_since'] = None
        runtime['stationary_lat'] = lat
        runtime['stationary_lon'] = lon
        runtime['unknown_at_stop'] = False
        current_id = None

    if matched_id:
        if runtime.get('entry_candidate_id') != matched_id:
            runtime['entry_candidate_id'] = matched_id
            runtime['entry_candidate_since'] = iso_local(now)
        else:
            since = parse_dt(runtime.get('entry_candidate_since') or iso_local(now))
            if (now - since).total_seconds() >= max(10, min(60, cfg['check_seconds'])):
                create_assistant_arrival(
                    int(runtime['departed_from_place_id']) if runtime.get('departed_from_place_id') else None,
                    matched_id, lat, lon, accuracy, runtime.get('departure_at'), route_snapshot=runtime
                )
                runtime['current_place_id'] = matched_id
                runtime['departed_from_place_id'] = None
                runtime['departure_at'] = None
                runtime['entry_candidate_id'] = None
                runtime['entry_candidate_since'] = None
                runtime['stationary_since'] = None
                runtime['unknown_at_stop'] = False
        assistant_state_set('runtime', runtime)
        return

    runtime['entry_candidate_id'] = None
    runtime['entry_candidate_since'] = None

    if cfg.get('unknown_stops') and runtime.get('departed_from_place_id') and not runtime.get('unknown_at_stop'):
        origin = known_place_by_id(runtime.get('departed_from_place_id'))
        if origin:
            from_origin = haversine_m(lat, lon, float(origin['latitude']), float(origin['longitude']))
            if from_origin >= float(cfg.get('min_trip_m') or 500):
                stat_lat = to_float(runtime.get('stationary_lat'))
                stat_lon = to_float(runtime.get('stationary_lon'))
                moved = haversine_m(lat, lon, stat_lat, stat_lon) if stat_lat is not None and stat_lon is not None else 9999
                speed = to_float(loc.get('speed'))
                stationary = moved <= 90 and (speed is None or speed <= 3.0)
                if stationary:
                    if not runtime.get('stationary_since'):
                        runtime['stationary_since'] = iso_local(now)
                    else:
                        since = parse_dt(runtime['stationary_since'])
                        if (now - since).total_seconds() >= int(cfg.get('unknown_stop_minutes') or 4) * 60:
                            create_assistant_arrival(
                                int(runtime['departed_from_place_id']), None, lat, lon, accuracy,
                                runtime.get('departure_at'), route_snapshot=runtime
                            )
                            runtime['unknown_at_stop'] = True
                            runtime['stationary_since'] = None
                else:
                    runtime['stationary_since'] = None
                runtime['stationary_lat'] = lat
                runtime['stationary_lon'] = lon

    if runtime.get('unknown_at_stop') and previous_lat is not None and previous_lon is not None:
        if haversine_m(lat, lon, previous_lat, previous_lon) > 220:
            runtime['unknown_at_stop'] = False
            runtime['departed_from_place_id'] = None
            runtime['departure_at'] = iso_local(now)
            runtime.update(route_m=0.0, route_samples=0, route_incomplete=True,
                           route_lat=lat, route_lon=lon, route_at=iso_local(now))
            runtime['stationary_lat'] = lat
            runtime['stationary_lon'] = lon

    assistant_state_set('runtime', runtime)


def _assistant_location_worker() -> None:
    last_sync = 0.0
    while True:
        cfg = assistant_config()
        delay = min(10, int(cfg.get('check_seconds') or 10))
        if not cfg.get('enabled') or cfg.get('mode') == 'manual' or not cfg.get('location_entity'):
            diagnostic_event('assistent_uit' if not cfg.get('enabled') else
                             'handmatige_modus' if cfg.get('mode') == 'manual' else 'tracker_ontbreekt')
            time.sleep(max(10, delay))
            continue
        try:
            if cfg.get('sync_zones') and time.time() - last_sync > 1800:
                sync_all_known_place_zones()
                last_sync = time.time()
            loc = location_from_entity(str(cfg['location_entity']))
            track_active_trip_distance(loc, cfg)
            _process_assistant_location(loc, cfg)
            assistant_state_set('last_error', '')
        except Exception as exc:
            diagnostic_event('achtergrondverwerking_mislukt')
            assistant_state_set('last_error', str(exc)[:300])
        time.sleep(max(10, delay))


def start_assistant_threads() -> None:
    threading.Thread(target=_report_address_worker, daemon=True, name='rit-tank-addresses').start()
    threading.Thread(target=_assistant_location_worker, daemon=True, name='rit-tank-location').start()
    threading.Thread(target=_assistant_action_listener, daemon=True, name='rit-tank-actions').start()
    if setting_bool('assistant_sync_zones', True):
        threading.Thread(target=sync_all_known_place_zones, daemon=True, name='rit-tank-zones').start()
    threading.Thread(target=_backup_worker, daemon=True, name='rit-tank-backup').start()


def _route_memory_suggestion(origin_place_id: int | None, dest_lat: float, dest_lon: float) -> dict[str, Any] | None:
    if not origin_place_id:
        return None
    with DB_LOCK, db() as con:
        rows=[dict(r) for r in con.execute('SELECT * FROM route_memory WHERE origin_known_place_id=? ORDER BY last_seen_at DESC',(int(origin_place_id),))]
    best=None; best_dist=None
    for r in rows:
        d=haversine_m(dest_lat,dest_lon,float(r['destination_latitude']),float(r['destination_longitude']))
        if d<=450 and (best_dist is None or d<best_dist): best,best_dist=r,d
    if not best: return None
    b,p=int(best.get('business_count') or 0),int(best.get('private_count') or 0); total=b+p
    if total<2: return None
    winner='business' if b>=p else 'private'; count=max(b,p); ratio=count/total
    if ratio<0.70: return None
    return {'suggested_type':winner,'reason':f'Eerder {count}x zo geregistreerd vanaf deze plek','confidence':round(min(.94,.68+.06*count),2),'source':'learned'}


def suggest_segment(origin_stop: dict[str, Any] | None, dest_lat: float, dest_lon: float) -> dict[str, Any]:
    dest=match_known_place(dest_lat,dest_lon)
    origin=None
    if origin_stop:
        origin=known_place_by_id(origin_stop.get('known_place_id')) or match_known_place(to_float(origin_stop.get('latitude')),to_float(origin_stop.get('longitude')))
    if dest and str(dest.get('arrival_trip_type') or 'ask') in {'business','private'}:
        t=str(dest['arrival_trip_type'])
        return {'suggested_type':t,'reason':f'Bestemming {dest["name"]} staat als {trip_type_label(t).lower()} ingesteld','confidence':.98,'source':'destination_rule','origin_place':origin,'destination_place':dest}
    if origin and not dest and str(origin.get('unknown_departure_trip_type') or 'ask') in {'business','private'}:
        t=str(origin['unknown_departure_trip_type'])
        return {'suggested_type':t,'reason':f'Vanaf {origin["name"]} naar onbekende bestemming: {trip_type_label(t)}','confidence':.90,'source':'origin_rule','origin_place':origin,'destination_place':None}
    memory=_route_memory_suggestion(int(origin['id']) if origin else None,dest_lat,dest_lon)
    if memory:
        return {**memory,'origin_place':origin,'destination_place':dest}
    return {'suggested_type':'','reason':'Geen vaste regel gevonden — kies zelf','confidence':0.0,'source':'manual','origin_place':origin,'destination_place':dest}


def remember_segment(origin_stop: dict[str, Any], destination_point: dict[str, Any], trip_type: str, *, con: sqlite3.Connection | None = None) -> None:
    t=normalize_segment_type(trip_type)
    if not t: return
    origin=known_place_by_id(origin_stop.get('known_place_id')) or match_known_place(to_float(origin_stop.get('latitude')),to_float(origin_stop.get('longitude')))
    if not origin: return
    lat,lon=to_float(destination_point.get('latitude')),to_float(destination_point.get('longitude'))
    if lat is None or lon is None: return
    dest=match_known_place(lat,lon)
    owned = con is None
    c = con or db()
    try:
        rows=[dict(r) for r in c.execute('SELECT * FROM route_memory WHERE origin_known_place_id=?',(int(origin['id']),))]
        target=None
        for r in rows:
            if haversine_m(lat,lon,float(r['destination_latitude']),float(r['destination_longitude']))<=350:
                target=r; break
        if target:
            field='business_count' if t=='business' else 'private_count'
            c.execute(f'UPDATE route_memory SET {field}={field}+1,destination_known_place_id=?,destination_latitude=?,destination_longitude=?,last_seen_at=? WHERE id=?',
                      (int(dest['id']) if dest else None,lat,lon,iso_local(),int(target['id'])))
        else:
            c.execute('''INSERT INTO route_memory(origin_known_place_id,destination_known_place_id,destination_latitude,destination_longitude,business_count,private_count,last_seen_at) VALUES(?,?,?,?,?,?,?)''',
                      (int(origin['id']),int(dest['id']) if dest else None,lat,lon,1 if t=='business' else 0,1 if t=='private' else 0,iso_local()))
        if owned: c.commit()
    finally:
        if owned: c.close()


def trip_type_label(value: str) -> str:
    return {'business':'Zakelijk','private':'Prive','mixed':'Gemengd'}.get(value,'Zakelijk')


def active_business_trip() -> dict[str, Any] | None:
    with DB_LOCK, db() as con:
        row = con.execute("SELECT * FROM business_trips WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        trip = dict(row)
        stops = [dict(r) for r in con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no ASC, id ASC', (trip['id'],))]
    return enrich_business_trip(trip, stops)


def _trip_point_payload(payload: dict[str, Any]) -> dict[str, Any]:
    odo = to_float(payload.get('odometer'))
    if odo is None or odo < 0:
        raise ValueError('Vul een geldige kilometerstand in.')
    dt = parse_dt(payload.get('created_at'))
    created = iso_local(dt)
    ok, msg = validate_odometer(created, odo)
    if not ok:
        raise ValueError(msg)
    lat = to_float(payload.get('latitude'))
    lon = to_float(payload.get('longitude'))
    accuracy = to_float(payload.get('location_accuracy'))
    source = str(payload.get('location_source') or '').strip()[:40]
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError('Leg eerst de huidige locatie vast met de 📍-knop.')
    place_id = str(payload.get('place_id') or '').strip()[:255]
    if not place_id and not payload.get('manual_label'):
        geo = google_reverse_geocode(lat, lon)
        place_id = str(geo.get('place_id') or '')[:255]
    return {
        'odometer': odo,
        'created_at': created,
        'latitude': lat,
        'longitude': lon,
        'location_accuracy': accuracy,
        'location_source': source or 'browser',
        'place_id': place_id or None,
        'manual_label': str(payload.get('manual_label') or '').strip()[:120] or None,
        'note': str(payload.get('note') or '').strip()[:250] or None,
        'known_place_id': (match_known_place(lat, lon) or {}).get('id'),
    }


def _insert_trip_stop(con: sqlite3.Connection, trip_id: int, point: dict[str, Any], sequence_no: int, event_note: str,
                      segment_trip_type: str = '', suggestion: dict[str, Any] | None = None) -> int:
    suggestion = suggestion or {}
    seg_type = normalize_segment_type(segment_trip_type)
    suggested = normalize_segment_type(suggestion.get('suggested_type'))
    source = 'start' if sequence_no == 0 else ('user-confirmed' if suggested and seg_type == suggested else 'user-override' if suggested else 'manual')
    cur = con.execute('''
        INSERT INTO trip_stops(
            trip_id,sequence_no,created_at,odometer,latitude,longitude,
            location_accuracy,location_source,place_id,manual_label,note,known_place_id,
            segment_trip_type,segment_suggested_type,segment_suggestion_reason,
            segment_suggestion_confidence,segment_classification_source
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (
        trip_id, sequence_no, point['created_at'], point['odometer'], point['latitude'], point['longitude'],
        point['location_accuracy'], point['location_source'], point['place_id'], point['manual_label'], point['note'], point.get('known_place_id'),
        seg_type or None, suggested or None, str(suggestion.get('reason') or '')[:220] or None,
        float(suggestion.get('confidence') or 0), source
    ))
    stop_id = int(cur.lastrowid)
    ev = con.execute('''
        INSERT INTO events(created_at,type,odometer,note,source_kind,business_trip_stop_id)
        VALUES(?,?,?,?,?,?)
    ''', (point['created_at'], 'odometer', point['odometer'], event_note[:200], 'business', stop_id))
    con.execute('UPDATE trip_stops SET event_id=? WHERE id=?', (int(ev.lastrowid), stop_id))
    return stop_id


def start_business_trip(payload: dict[str, Any]) -> dict[str, Any]:
    if active_business_trip() is not None:
        raise ValueError('Er staat al een ritregistratie open. Voeg een volgende locatie toe of sluit de dagrit af.')
    point = _trip_point_payload(payload)
    purpose = str(payload.get('purpose') or '').strip()[:120]
    client = str(payload.get('client') or '').strip()[:120]
    trip_note = str(payload.get('trip_note') or '').strip()[:250]
    with DB_LOCK, db() as con:
        cur = con.execute('''
            INSERT INTO business_trips(started_at,status,purpose,client,note,trip_type,private_detour_km,modified_at)
            VALUES(?,'active',?,?,?,'mixed',0,?)
        ''', (point['created_at'], purpose or None, client or None, trip_note or None, iso_local()))
        trip_id = int(cur.lastrowid)
        _insert_trip_stop(con, trip_id, point, 0, 'Ritregistratie start')
        audit('create', 'trip', trip_id, {'mode': 'segment_classification', 'purpose': purpose, 'client': client, 'start': point}, con=con)
        con.commit()
    trip_now = active_business_trip()
    reset_trip_distance_tracking(trip_now)
    publish_sensors_async()
    return {'ok': True, 'trip': trip_now}

def add_business_stop(payload: dict[str, Any], *, finish: bool = False) -> dict[str, Any]:
    trip = active_business_trip()
    if not trip:
        raise ValueError('Er is geen actieve ritregistratie.')
    point = _trip_point_payload(payload)
    tracking = assistant_state_get('trip_distance_tracking', {}) or {}
    with DB_LOCK, db() as con:
        last = con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no DESC LIMIT 1', (trip['id'],)).fetchone()
        if not last:
            raise ValueError('De actieve rit heeft geen startpunt.')
        lastd=dict(last)
        if point['odometer'] < float(last['odometer']):
            raise ValueError(f'Kilometerstand is lager dan de vorige stop ({float(last["odometer"]):.0f} km).')
        suggestion=suggest_segment(lastd,float(point['latitude']),float(point['longitude']))
        seg_type=normalize_segment_type(payload.get('segment_trip_type'))
        if not seg_type:
            seg_type=normalize_segment_type(suggestion.get('suggested_type'))
        if not seg_type:
            raise ValueError('Kies of dit traject zakelijk of prive was.')
        seq = int(last['sequence_no']) + 1
        label = 'einde' if finish else 'stop'
        stop_id = _insert_trip_stop(con, int(trip['id']), point, seq, f'Rit {label} ({trip_type_label(seg_type)})', seg_type, suggestion)
        if (int(tracking.get('trip_id') or -1) == int(trip['id']) and
                int(tracking.get('stop_id') or -1) == int(last['id']) and not tracking.get('incomplete') and
                abs((parse_dt(point['created_at']) - now_local()).total_seconds()) < 300):
            learn_distance(f'stop:{stop_id}', float(tracking.get('segment_m') or 0) / 1000,
                           float(point['odometer']) - float(last['odometer']), int(tracking.get('sample_count') or 0),
                           payload.get('odometer_checked') is True, con=con)
        remember_segment(lastd, point, seg_type, con=con)
        # Overall trip type is derived from all classified legs.
        types=[str(r['segment_trip_type'] or '') for r in con.execute('SELECT segment_trip_type FROM trip_stops WHERE trip_id=? AND sequence_no>0',(trip['id'],))]
        types=[t for t in types if t in {'business','private'}]
        overall = types[0] if types and all(t==types[0] for t in types) else 'mixed'
        if finish:
            route = str(payload.get('deviating_route') or '').strip()[:300]
            detour = max(0.0, to_float(payload.get('private_detour_km')) or 0.0)
            con.execute("UPDATE business_trips SET status='completed', ended_at=?, trip_type=?, deviating_route=?, private_detour_km=?, modified_at=? WHERE id=?",
                        (point['created_at'], overall, route or None, detour, iso_local(), trip['id']))
            audit('finish','trip',int(trip['id']),{'stop_id':stop_id,'segment_trip_type':seg_type,'overall_trip_type':overall,'suggestion':suggestion.get('reason'),'end':point},con=con)
        else:
            con.execute('UPDATE business_trips SET trip_type=?,modified_at=? WHERE id=?',(overall,iso_local(),trip['id']))
            audit('stop','trip',int(trip['id']),{'stop_id':stop_id,'segment_trip_type':seg_type,'suggestion':suggestion.get('reason'),'point':point},con=con)
        con.commit()
    result_trip = active_business_trip() if not finish else business_trip_by_id(int(trip['id']))
    if finish:
        assistant_state_set('trip_distance_tracking', {})
    else:
        reset_trip_distance_tracking(result_trip)
    publish_sensors_async()
    return {'ok': True, 'trip': result_trip}

def business_trip_by_id(trip_id: int) -> dict[str, Any] | None:
    with DB_LOCK, db() as con:
        row = con.execute('SELECT * FROM business_trips WHERE id=?', (trip_id,)).fetchone()
        if not row:
            return None
        stops = [dict(r) for r in con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no ASC, id ASC', (trip_id,))]
    return enrich_business_trip(dict(row), stops)


def enrich_business_trip(trip: dict[str, Any], stops: list[dict[str, Any]], resolve: bool = True) -> dict[str, Any]:
    out = dict(trip)
    enriched = []
    prev_odo = None
    total = business_km = private_km = 0.0
    segment_types=[]
    for stop in stops:
        x = dict(stop)
        dt = parse_dt(x['created_at'])
        x['date_label'] = dutch_date(dt)
        x['time_label'] = dt.strftime('%H:%M')
        km = max(0.0, float(x['odometer']) - prev_odo) if prev_odo is not None else 0.0
        x['segment_km'] = round(km, 1)
        total += km
        seg=normalize_segment_type(x.get('segment_trip_type'))
        x['segment_trip_type']=seg
        x['segment_trip_type_label']=trip_type_label(seg) if seg else ''
        if seg:
            segment_types.append(seg)
            if seg=='private': private_km += km
            else: business_km += km
        kp=known_place_by_id(x.get('known_place_id'))
        x['known_place_name']=kp.get('name') if kp else ''
        loc = trip_location_details(x, resolve=resolve)
        x['location_label'] = loc['label']
        x['location_address'] = loc['address']
        x['google_maps_uri'] = loc['google_maps_uri']
        enriched.append(x)
        prev_odo = float(x['odometer'])
    out['stops'] = enriched
    out['km'] = round(total, 1)
    # New V3.4 records use per-leg classifications. Legacy records fall back to old trip-level logic.
    if segment_types:
        overall=segment_types[0] if all(t==segment_types[0] for t in segment_types) else 'mixed'
        out['trip_type']=overall
        out['business_km']=round(business_km,1)
        out['private_km']=round(private_km,1)
    else:
        out['trip_type'] = str(out.get('trip_type') or 'business')
        detour = max(0.0, min(total, float(out.get('private_detour_km') or 0)))
        if out['trip_type'] == 'private': out['business_km'],out['private_km']=0.0,round(total,1)
        elif out['trip_type'] == 'mixed': out['private_km'],out['business_km']=round(detour,1),round(max(0.0,total-detour),1)
        else: out['business_km'],out['private_km']=round(total,1),0.0
    out['trip_type_label'] = trip_type_label(out['trip_type'])
    out['start_odometer'] = float(stops[0]['odometer']) if stops else None
    out['last_odometer'] = float(stops[-1]['odometer']) if stops else None
    out['stop_count'] = len(stops)
    if stops:
        out['start_location'] = enriched[0]['location_label']
        out['last_location'] = enriched[-1]['location_label']
        out['started_label'] = dutch_date(stops[0]['created_at']) + ' ' + parse_dt(stops[0]['created_at']).strftime('%H:%M')
        out['ended_label'] = dutch_date(stops[-1]['created_at']) + ' ' + parse_dt(stops[-1]['created_at']).strftime('%H:%M') if out.get('status') == 'completed' else None
    else:
        out['start_location'] = out['last_location'] = ''
        out['started_label'] = out['ended_label'] = None
    return out


def business_trips_raw() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    with DB_LOCK, db() as con:
        trips = [dict(r) for r in con.execute('SELECT * FROM business_trips ORDER BY started_at ASC, id ASC')]
        result = []
        for t in trips:
            stops = [dict(r) for r in con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no ASC, id ASC', (t['id'],))]
            result.append((t, stops))
    return result


def business_stats_for_period(period: str) -> dict[str, Any]:
    trips = business_trips_for_period(period)
    total_km = sum(float(t.get('km') or 0) for t in trips)
    business_km = sum(float(t.get('business_km') or 0) for t in trips)
    private_km = sum(float(t.get('private_km') or 0) for t in trips)
    stop_count = sum(int(t.get('stop_count') or 0) for t in trips)
    return {
        'km': round(total_km,1), 'business_km': round(business_km,1), 'private_km': round(private_km,1),
        'trips': len(trips), 'segments': sum(max(0,int(t.get('stop_count') or 0)-1) for t in trips),
        'stops': stop_count, 'avg_km': round(total_km/len(trips),1) if trips else 0.0,
    }

def recent_business_trips(period: str, limit: int = 12) -> list[dict[str, Any]]:
    start, end = period_bounds(period)
    selected = []
    for trip, stops in reversed(business_trips_raw()):
        if not stops:
            continue
        if trip.get('status') == 'active' or any(start <= parse_dt(s['created_at']) < end for s in stops):
            selected.append(enrich_business_trip(trip, stops))
        if len(selected) >= limit:
            break
    return selected



def business_trips_for_period(period: str) -> list[dict[str, Any]]:
    """Return complete trips that touch the selected reporting period."""
    if period == 'all':
        return [enrich_business_trip(t, stops) for t, stops in business_trips_raw() if stops]
    if period not in {'day', 'week', 'month', 'year'}:
        period = 'month'
    start, end = period_bounds(period)
    out = []
    for trip, stops in business_trips_raw():
        if not stops:
            continue
        if any(start <= parse_dt(stop['created_at']) < end for stop in stops):
            out.append(enrich_business_trip(trip, stops))
    return out


def _pdf_text(value: Any) -> str:
    """Text safe for the built-in PDF WinAnsi fonts."""
    s = str(value if value is not None else '')
    return s.replace('\u2013', '-').replace('\u2014', '-').replace('\u2192', '->').replace('\u2022', '-').replace('\u00a0', ' ')


def _pdf_escape(value: Any) -> bytes:
    raw = _pdf_text(value).encode('cp1252', 'replace')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


class _SimplePdfPage:
    def __init__(self, title: str):
        self.commands: list[bytes] = []
        self.preview: list[str] = []
        self.images: set[str] = set()
        self.y = 806.0
        self.title = title
        self.text(title, 36, self.y, 15, bold=True)
        self.y -= 12
        self.line(36, self.y, 559, self.y, 0.75)
        self.y -= 18

    def text(self, value: Any, x: float, y: float, size: float = 9, bold: bool = False, gray: float = 0.08):
        color = round(gray * 255)
        self.preview.append(f'<text x="{x}" y="{842-y}" font-size="{size}" font-weight="{700 if bold else 400}" fill="rgb({color},{color},{color})">{html.escape(_pdf_text(value))}</text>')
        font = 'F2' if bold else 'F1'
        esc = _pdf_escape(value)
        self.commands.append(
            f'{gray:.3f} g BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm '.encode('ascii')
            + b'(' + esc + b') Tj ET\n'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5, gray: float = 0.75):
        color = round(gray * 255)
        self.preview.append(f'<line x1="{x1}" y1="{842-y1}" x2="{x2}" y2="{842-y2}" stroke="rgb({color},{color},{color})" stroke-width="{width}"/>')
        self.commands.append(
            f'{gray:.3f} G {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S\n'.encode('ascii')
        )

    def rect(self, x: float, y: float, width: float, height: float, gray: float = 0.94):
        """Draw a filled, lightly shaded rectangle used for report cards."""
        color = round(gray * 255)
        self.preview.append(f'<rect x="{x}" y="{842-y-height}" width="{width}" height="{height}" fill="rgb({color},{color},{color})"/>')
        self.commands.append(
            f'{gray:.3f} g {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f\n'.encode('ascii')
        )

    def image(self, name: str, x: float, y: float, width: float, height: float):
        """Place a registered PDF image XObject at the given position."""
        self.images.add(name)
        self.preview.append(f'<image x="{x}" y="{842-y-height}" width="{width}" height="{height}" preserveAspectRatio="none" href="IMAGE_{name}"/>')
        self.commands.append(
            f'q {width:.2f} 0 0 {height:.2f} {x:.2f} {y:.2f} cm /{name} Do Q\n'.encode('ascii')
        )

    def wrapped(self, value: Any, x: float, width: float, size: float = 8.5, bold: bool = False, leading: float | None = None, indent: float = 0):
        leading = leading or (size + 3)
        chars = max(18, int(width / max(3.7, size * 0.52)))
        lines = textwrap.wrap(_pdf_text(value), width=chars, break_long_words=False, break_on_hyphens=False) or ['']
        for line in lines:
            self.text(line, x + indent, self.y, size, bold=bold)
            self.y -= leading
        return len(lines)

    def need(self, height: float) -> bool:
        return self.y - height < 42

    def stream(self) -> bytes:
        return b''.join(self.commands)

    def svg(self, images: dict[str, tuple[int, int, bytes]]) -> str:
        markup = ''.join(self.preview)
        for name, (_, _, data) in images.items():
            markup = markup.replace(f'IMAGE_{name}', 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii'))
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 595 842" role="img" aria-label="Pagina rittenregistratie" style="font-family:Arial,Helvetica,sans-serif;background:white">' + markup + '</svg>'


def _build_pdf(pages: list[_SimplePdfPage], images: dict[str, tuple[int, int, bytes]] | None = None) -> bytes:
    """Minimal dependency-free PDF writer using core Helvetica fonts and JPEGs."""
    objects: dict[int, bytes] = {}
    objects[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[3] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'
    objects[4] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>'
    image_ids: dict[str, int] = {}
    next_id = 5
    for name, (width, height, data) in (images or {}).items():
        image_ids[name] = next_id
        objects[next_id] = (
            f'<< /Type /XObject /Subtype /Image /Width {int(width)} /Height {int(height)} '
            f'/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\n'
        ).encode('ascii') + b'stream\n' + data + b'\nendstream'
        next_id += 1
    kids = []
    for page in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        kids.append(f'{page_id} 0 R')
        stream = page.stream()
        objects[content_id] = b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'endstream'
        xobjects = ' '.join(
            f'/{name} {image_ids[name]} 0 R'
            for name in sorted(page.images)
            if name in image_ids
        )
        xobject_resource = f' /XObject << {xobjects} >>' if xobjects else ''
        objects[page_id] = (
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
            f'/Resources << /Font << /F1 3 0 R /F2 4 0 R >>{xobject_resource} >> /Contents {content_id} 0 R >>'
        ).encode('ascii')
    objects[2] = f'<< /Type /Pages /Count {len(pages)} /Kids [{" ".join(kids)}] >>'.encode('ascii')

    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = {0: 0}
    max_id = max(objects)
    for obj_id in range(1, max_id + 1):
        offsets[obj_id] = len(out)
        out.extend(f'{obj_id} 0 obj\n'.encode('ascii'))
        out.extend(objects[obj_id])
        out.extend(b'\nendobj\n')
    xref = len(out)
    out.extend(f'xref\n0 {max_id + 1}\n'.encode('ascii'))
    out.extend(b'0000000000 65535 f \n')
    for obj_id in range(1, max_id + 1):
        out.extend(f'{offsets[obj_id]:010d} 00000 n \n'.encode('ascii'))
    out.extend(f'trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode('ascii'))
    return bytes(out)


def business_pdf(period: str = 'month', year: str | None = None, month: str | None = None, preview: bool = False) -> Any:
    settings = get_settings()
    ref = now_local()
    try:
        if year is not None:
            if period not in {'month', 'year'}:
                raise ValueError()
            ref = ref.replace(year=int(year), month=int(month or 1), day=1)
        elif month is not None:
            raise ValueError()
        if not 1900 <= ref.year <= 9998:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Kies een geldig jaar (1900-9998) en een maand (1-12).') from None
    safe_period = period if period in {'day', 'week', 'month', 'year'} else 'month'
    selection_start, selection_end = period_bounds(safe_period, ref)
    trips = []
    for trip, stops in business_trips_raw():
        if stops and (period == 'all' or selection_start <= parse_dt(stops[0]['created_at']) < selection_end):
            trips.append(enrich_business_trip(trip, stops, resolve=False))
    trips.sort(key=lambda trip: parse_dt(trip['stops'][0]['created_at']))
    if period == 'all':
        label = 'Alle geregistreerde ritten'
        filename_label = 'alles'
        period_stops = [
            parse_dt(stop.get('created_at'))
            for trip in trips
            for stop in (trip.get('stops') or [])
            if stop.get('created_at')
        ]
        period_start = min(period_stops) if period_stops else None
        period_end = max(period_stops) if period_stops else None
    else:
        safe_period = period if period in {'day', 'week', 'month', 'year'} else 'month'
        period_start, period_end_exclusive = selection_start, selection_end
        label = period_label(safe_period, period_start)
        filename_label = period_start.strftime('%Y') if safe_period == 'year' else period_start.strftime('%Y-%m') if safe_period == 'month' else safe_period
        period_end = period_end_exclusive - timedelta(days=1)
    if period_start and period_end:
        year_label = str(period_start.year) if period_start.year == period_end.year else f'{period_start.year}-{period_end.year}'
        date_range = f'{period_start:%d-%m-%Y} - {period_end:%d-%m-%Y}'
    else:
        year_label = '-'
        date_range = 'Geen geregistreerde datums'
    total_km = round(sum(float(t.get('km') or 0) for t in trips), 1)
    business_km = round(sum(float(t.get('business_km') or 0) for t in trips), 1)
    private_km = round(sum(float(t.get('private_km') or 0) for t in trips), 1)
    pages = []

    company_name = str(settings.get('company_name') or 'Huisplan B.V.').strip() or 'Huisplan B.V.'
    vehicle_bits = [x for x in [settings.get('vehicle_make'), settings.get('vehicle_model')] if x]
    vehicle_name = ' '.join(vehicle_bits) or settings.get('vehicle_name') or '-'
    used_period = ' - '.join(x for x in [settings.get('vehicle_period_from'), settings.get('vehicle_period_to')] if x) or '-'

    pdf_images: dict[str, tuple[int, int, bytes]] = {}
    captur_path = Path(__file__).with_name('captur-2014.jpg')
    try:
        if captur_path.exists():
            pdf_images['ImCaptur'] = (1200, 800, captur_path.read_bytes())
    except OSError:
        pass
    logo_path = Path(__file__).with_name('huisplan-logo.jpg')
    try:
        if logo_path.exists():
            pdf_images['ImLogo'] = (900, 827, logo_path.read_bytes())
    except OSError:
        pass

    def column_labels(page):
        # Repeat semantic column labels on every page, including continuations.
        page.text('RIT / DATUM / ADRES EN TELLERSTAND', 36, page.y, 7.5, bold=True, gray=.35)
        page.text('RITAFSTAND (KM)', 463, page.y, 7.5, bold=True, gray=.35)
        page.y -= 20

    def new_page():
        page = _SimplePdfPage('Fiscale rittenregistratie')
        if pages:
            column_labels(page)
        pages.append(page)
        return page

    page = new_page()
    if 'ImLogo' in pdf_images:
        page.rect(34, 738, 38, 38, gray=.96)
        page.image('ImLogo', 36, 740, 34, 34)
        page.text(company_name.upper(), 82, 764, 8.5, bold=True, gray=.35)
        page.text('Rittenregistratie', 82, 751, 7.3, gray=.45)
        page.y = 728.0
    else:
        page.text(company_name.upper(), 36, page.y, 8.5, bold=True, gray=.35)
        page.y -= 16
    card_top = page.y
    page.rect(36, card_top - 65, 335, 65, gray=.94)
    page.text('KALENDERJAAR', 48, card_top - 17, 7.2, bold=True, gray=.42)
    page.text(year_label, 48, card_top - 40, 16, bold=True)
    page.text('RAPPORTPERIODE', 160, card_top - 17, 7.2, bold=True, gray=.42)
    page.text(label, 160, card_top - 36, 10.2, bold=True)
    page.text(date_range, 160, card_top - 52, 7.3, gray=.30)
    if 'ImCaptur' in pdf_images:
        page.rect(385, card_top - 105, 174, 105, gray=1)
        page.image('ImCaptur', 387, card_top - 103, 170, 102)
    page.y = card_top - 122
    metadata_rows = [
        (('Bestuurder', settings.get('driver_name') or '-'), ('Auto', vehicle_name)),
        (('Bedrijf', company_name), ('Kenteken', settings.get('license_plate') or '-')),
        (('Beschikkingsperiode', used_period), ('Gegenereerd', now_local().strftime('%d-%m-%Y %H:%M'))),
    ]
    for (left_key, left_value), (right_key, right_value) in metadata_rows:
        page.text(f'{left_key}:', 36, page.y, 8, bold=True)
        page.text(left_value, 122, page.y, 8)
        page.text(f'{right_key}:', 304, page.y, 8, bold=True)
        page.text(right_value, 386, page.y, 8)
        page.y -= 14
    page.y -= 2
    page.line(36, page.y, 559, page.y, .5)
    page.y -= 16
    page.text(f'Totaal: {total_km:.1f} km', 36, page.y, 9.5, bold=True)
    page.text(f'Zakelijk: {business_km:.1f} km', 190, page.y, 9.5, bold=True)
    page.text(f'Prive: {private_km:.1f} km', 370, page.y, 9.5, bold=True)
    page.y -= 18
    page.wrapped('Rit & Tank legt datum, begin- en eindstand, vertrek- en aankomstlocatie, ritsoort, afwijkende route en prive-omrijkilometers vast. Controleer dit rapport voor gebruik in uw administratie.', 36, 523, 7.5)
    page.y -= 6
    page.wrapped('Ritten zijn ingedeeld op vertrekdatum. Een rit over een maandgrens staat volledig in de vertrekmaand.', 36, 523, 7.5)
    page.y -= 6
    column_labels(page)
    if not trips:
        page.text('Geen ritten in deze periode.', 36, page.y, 10)
    day_counts = {}
    for trip in trips:
        stops = trip.get('stops') or []
        if not stops:
            continue
        dkey = parse_dt(stops[0].get('created_at')).strftime('%Y-%m-%d')
        day_counts[dkey] = day_counts.get(dkey, 0) + 1
        trip['day_trip_no'] = day_counts[dkey]
    previous_month = None
    for idx, trip in enumerate(trips, start=1):
        stops = trip.get('stops') or []
        start_dt = parse_dt(stops[0]['created_at'])
        month_key = start_dt.strftime('%Y-%m')
        if period == 'year' and month_key != previous_month:
            if previous_month is not None or page.need(160):
                page = new_page()
            month_trips = [t for t in trips if parse_dt(t['stops'][0]['created_at']).strftime('%Y-%m') == month_key]
            page.text(period_label('month', start_dt).capitalize(), 36, page.y, 13, bold=True)
            page.y -= 17
            page.text(f'{len(month_trips)} ritten | Totaal: {sum(t["km"] for t in month_trips):.1f} km | Zakelijk: {sum(t["business_km"] for t in month_trips):.1f} km | Prive: {sum(t["private_km"] for t in month_trips):.1f} km', 36, page.y, 8)
            page.y -= 24
            previous_month = month_key
        estimated = 118 + 20 * max(1, len(stops))
        if page.need(estimated):
            page = new_page()
        start_dt = parse_dt(stops[0]['created_at']) if stops else parse_dt(trip.get('started_at'))
        page.text(f'Rit {trip.get("day_trip_no", idx):02d} - {dutch_date(start_dt)}', 36, page.y, 10.5, bold=True)
        page.text(f'{float(trip.get("km") or 0):.1f} km', 500, page.y, 9, bold=True)
        page.y -= 14
        page.text(f'Ritsoort: {trip.get("trip_type_label") or "Zakelijk"}', 48, page.y, 8.5, bold=True)
        page.text(f'Beginstand: {float(trip.get("start_odometer") or 0):.0f} km', 220, page.y, 8.2)
        page.text(f'Eindstand: {float(trip.get("last_odometer") or 0):.0f} km', 390, page.y, 8.2)
        page.y -= 12
        page.text(f'Zakelijk: {float(trip.get("business_km") or 0):.1f} km', 48, page.y, 8)
        page.text(f'Prive: {float(trip.get("private_km") or 0):.1f} km', 220, page.y, 8)
        page.text(f'Prive omrij: {float(trip.get("private_detour_km") or 0):.1f} km', 390, page.y, 8)
        page.y -= 12
        purpose = trip.get('purpose') or '-'
        client = trip.get('client') or ''
        page.wrapped(f'Doel: {purpose}' + (f' | Klant/opdracht: {client}' if client else ''), 48, 500, 8.2)
        if trip.get('deviating_route'):
            page.wrapped(f'Afwijkende route: {trip.get("deviating_route")}', 48, 500, 8)
        if trip.get('note'):
            page.wrapped(f'Toelichting: {trip.get("note")}', 48, 500, 8)
        prev_odo = None
        for j,stop in enumerate(stops):
            dt = parse_dt(stop.get('created_at'))
            odo = float(stop.get('odometer') or 0)
            seg = 0.0 if prev_odo is None else max(0.0, odo - prev_odo)
            role = 'Vertrek' if j == 0 else ('Aankomst' if j == len(stops) - 1 and trip.get('status') == 'completed' else f'Tussenstop {j}')
            loc = stop.get('location_address') or stop.get('location_label') or stop.get('manual_label') or 'Locatie onbekend'
            seg_type = stop.get('segment_trip_type_label') or ''
            header = f'{role}: {dt.strftime("%H:%M")} | Tellerstand: {odo:.0f} km' + (f' | Etappe: {seg:.1f} km' if j else '') + (f' | {seg_type}' if j and seg_type else '')
            if page.need(65):
                page = new_page()
                page.text(f'Rit {trip.get("day_trip_no", idx):02d} - {dutch_date(start_dt)} - vervolg', 36, page.y, 9, bold=True)
                page.y -= 15
            page.text(dutch_date(dt), 54, page.y, 7.5, gray=.35)
            page.y -= 11
            page.text(header, 54, page.y, 8.2, bold=True)
            page.y -= 11
            page.wrapped(loc, 66, 475, 8)
            if stop.get('note'):
                page.wrapped(f'Notitie: {stop.get("note")}', 66, 475, 7.5)
            prev_odo = odo
        page.y -= 4
        page.line(36, page.y, 559, page.y, .35, .82)
        page.y -= 14
    total_pages = len(pages)
    footer_label = f'{company_name} · Rit & Tank · {label}'
    for n, pg in enumerate(pages, start=1):
        pg.line(36, 30, 559, 30, .35, .85)
        footer_x = 56 if 'ImLogo' in pdf_images else 36
        if 'ImLogo' in pdf_images:
            pg.image('ImLogo', 38, 7, 13, 13)
        pg.text(footer_label, footer_x, 18, 7, gray=.45)
        pg.text(f'Pagina {n} van {total_pages}', 493, 18, 7, gray=.45)
    data, filename = _build_pdf(pages, pdf_images), f'rittenregistratie_{filename_label}.pdf'
    if preview:
        return {'filename': filename, 'pdf_base64': base64.b64encode(data).decode('ascii'), 'pages': [p.svg(pdf_images) for p in pages]}
    return data, filename


def edit_business_trip(trip_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    with DB_LOCK, db() as con:
        before=_snapshot_trip(con,trip_id)
        if not before: raise ValueError('Rit niet gevonden.')
        current=before['trip']; trip_type=str(payload.get('trip_type') or current.get('trip_type') or 'business').lower()
        if trip_type not in {'business','private','mixed'}: trip_type='business'
        purpose=str(payload.get('purpose') if payload.get('purpose') is not None else current.get('purpose') or '').strip()[:120]
        client=str(payload.get('client') if payload.get('client') is not None else current.get('client') or '').strip()[:120]
        note=str(payload.get('note') if payload.get('note') is not None else current.get('note') or '').strip()[:250]
        route=str(payload.get('deviating_route') if payload.get('deviating_route') is not None else current.get('deviating_route') or '').strip()[:300]
        detour=max(0.0,(to_float(payload.get('private_detour_km')) or 0.0) if payload.get('private_detour_km') is not None else float(current.get('private_detour_km') or 0))
        con.execute('UPDATE business_trips SET trip_type=?,purpose=?,client=?,note=?,deviating_route=?,private_detour_km=?,modified_at=? WHERE id=?',(trip_type,purpose or None,client or None,note or None,route or None,detour,iso_local(),trip_id))
        after=_snapshot_trip(con,trip_id); audit('update','trip',trip_id,{'before':before.get('trip',{}),'after':after.get('trip',{})},con=con); con.commit()
    publish_sensors_async(); return {'ok':True,'trip':business_trip_by_id(trip_id)}


def delete_business_trip(trip_id: int) -> None:
    with DB_LOCK, db() as con:
        snapshot = _snapshot_trip(con, trip_id)
        ids = [r['event_id'] for r in con.execute(
            'SELECT event_id FROM trip_stops WHERE trip_id=? AND event_id IS NOT NULL',
            (trip_id,)
        )]
        for event_id in ids:
            con.execute('DELETE FROM events WHERE id=?', (event_id,))
        con.execute('DELETE FROM business_trips WHERE id=?', (trip_id,))
        audit('delete', 'trip', trip_id, snapshot, con=con)
        con.commit()
    publish_sensors_async()


def delete_event(event_id: int) -> None:
    with DB_LOCK, db() as con:
        row=con.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
        if row:
            snapshot=dict(row); receipt=str(snapshot.get('receipt_path') or '')
            con.execute('DELETE FROM events WHERE id=?',(event_id,)); audit('delete','event',event_id,snapshot,con=con); con.commit()
            if receipt:
                try: (RECEIPT_DIR/Path(receipt).name).unlink(missing_ok=True)
                except Exception: pass
    publish_sensors_async()

def period_bounds(period: str, ref: datetime | None = None) -> tuple[datetime, datetime]:
    n = (ref or now_local()).astimezone(tz())
    if period == 'day':
        start = n.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == 'week':
        start = (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
    elif period == 'year':
        start = n.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
    else:
        start = n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
    return start, end


def event_dt(row: dict[str, Any]) -> datetime:
    return parse_dt(row['created_at'])


def stats_for_period(period: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = rows if rows is not None else rows_events()
    start, end = period_bounds(period)
    selected = [r for r in rows if start <= event_dt(r) < end]
    km = sum(float(r.get('delta_km') or 0) for r in selected)
    fuels = [r for r in selected if r['type'] == 'fuel']
    liters = sum(float(r.get('liters') or 0) for r in fuels)
    cost = sum(float(r.get('cost') or 0) for r in fuels)
    avg_price = cost / liters if liters > 0 else None
    l100 = liters / km * 100 if km > 0 and liters > 0 else None
    cost100 = cost / km * 100 if km > 0 and cost > 0 else None
    return {
        'period': period,
        'label': period_label(period, start),
        'start': start.isoformat(),
        'end': end.isoformat(),
        'km': round(km, 1),
        'liters': round(liters, 2),
        'cost': round(cost, 2),
        'avg_price': round(avg_price, 3) if avg_price is not None else None,
        'l100': round(l100, 2) if l100 is not None else None,
        'cost100': round(cost100, 2) if cost100 is not None else None,
        'fuel_count': len(fuels),
    }


def period_label(period: str, start: datetime) -> str:
    if period == 'day':
        return start.strftime('%d-%m-%Y')
    if period == 'week':
        return f'Week {start.isocalendar().week}'
    if period == 'year':
        return str(start.year)
    months = ['januari','februari','maart','april','mei','juni','juli','augustus','september','oktober','november','december']
    return f'{months[start.month-1]} {start.year}'


def full_tank_cycles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fuel_rows = [r for r in rows if r['type'] == 'fuel']
    cycles: list[dict[str, Any]] = []
    previous_full_idx = None
    for idx, row in enumerate(fuel_rows):
        if not int(row.get('full_tank') or 0):
            continue
        if previous_full_idx is not None:
            prev = fuel_rows[previous_full_idx]
            km = float(row['odometer']) - float(prev['odometer'])
            liters = sum(float(x.get('liters') or 0) for x in fuel_rows[previous_full_idx + 1: idx + 1])
            cost = sum(float(x.get('cost') or 0) for x in fuel_rows[previous_full_idx + 1: idx + 1])
            if km > 0 and liters > 0:
                cycles.append({
                    'start': prev['created_at'],
                    'end': row['created_at'],
                    'km': round(km, 1),
                    'liters': round(liters, 2),
                    'cost': round(cost, 2),
                    'l100': round(liters / km * 100, 2),
                    'cost100': round(cost / km * 100, 2) if cost > 0 else None,
                })
        previous_full_idx = idx
    return cycles


def full_tank_period_average(period: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    start, end = period_bounds(period)
    cycles = [c for c in full_tank_cycles(rows) if start <= parse_dt(c['end']) < end]
    if not cycles:
        return None
    km = sum(c['km'] for c in cycles)
    liters = sum(c['liters'] for c in cycles)
    cost = sum(c['cost'] for c in cycles)
    return {
        'km': round(km, 1),
        'liters': round(liters, 2),
        'l100': round(liters / km * 100, 2) if km > 0 else None,
        'cost100': round(cost / km * 100, 2) if km > 0 else None,
        'cycles': len(cycles),
    }


def chart_for_period(period: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start, end = period_bounds(period)
    selected = [r for r in rows if start <= event_dt(r) < end]
    cycles = full_tank_cycles(rows)
    buckets: list[dict[str, Any]] = []

    def add_bucket(label: str, bs: datetime, be: datetime):
        subset = [r for r in selected if bs <= event_dt(r) < be]
        km = sum(float(r.get('delta_km') or 0) for r in subset)
        fuels = [r for r in subset if r['type'] == 'fuel']
        liters = sum(float(r.get('liters') or 0) for r in fuels)
        cost = sum(float(r.get('cost') or 0) for r in fuels)
        bc = [c for c in cycles if bs <= parse_dt(c['end']) < be]
        ckm = sum(float(c['km']) for c in bc)
        cl = sum(float(c['liters']) for c in bc)
        l100 = cl / ckm * 100 if ckm > 0 and cl > 0 else None
        buckets.append({
            'label': label, 'km': round(km,1), 'liters': round(liters,2),
            'cost': round(cost,2), 'l100': round(l100,2) if l100 is not None else None
        })

    if period == 'day':
        for h in range(0, 24, 4):
            bs = start + timedelta(hours=h)
            add_bucket(f'{h:02d}', bs, bs + timedelta(hours=4))
    elif period == 'week':
        names = ['Ma','Di','Wo','Do','Vr','Za','Zo']
        for i in range(7):
            bs = start + timedelta(days=i)
            add_bucket(names[i], bs, bs + timedelta(days=1))
    elif period == 'year':
        names = ['Jan','Feb','Mrt','Apr','Mei','Jun','Jul','Aug','Sep','Okt','Nov','Dec']
        for month in range(1, 13):
            bs = start.replace(month=month, day=1)
            be = (bs.replace(year=bs.year+1, month=1) if month == 12 else bs.replace(month=month+1))
            add_bucket(names[month-1], bs, be)
    else:
        days = (end - start).days
        for i in range(days):
            bs = start + timedelta(days=i)
            add_bucket(str(i+1), bs, bs + timedelta(days=1))
    return buckets

def current_odometer(rows: list[dict[str, Any]]) -> float | None:
    return float(rows[-1]['odometer']) if rows else None


def recent_stations(rows: list[dict[str, Any]]) -> list[str]:
    seen = set()
    out = []
    for r in reversed(rows):
        s = str(r.get('station') or '').strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
        if len(out) >= 8:
            break
    return out



def station_details_for_row(row: dict[str, Any]) -> dict[str, Any]:
    place_id = str(row.get('place_id') or '').strip()
    manual = str(row.get('station') or '').strip()
    lat, lon = to_float(row.get('latitude')), to_float(row.get('longitude'))
    if place_id:
        details = google_place_details(place_id)
        if details:
            return {
                'name': details.get('name') or 'Tankstation',
                'address': details.get('address') or '',
                'google_maps_uri': details.get('google_maps_uri') or '',
                'source': 'google',
            }
        maps = f'https://www.google.com/maps/search/?api=1&query={lat},{lon}&query_place_id={quote(place_id, safe="")}' if lat is not None and lon is not None else ''
        return {'name': 'Google tankstation', 'address': '', 'google_maps_uri': maps, 'source': 'google'}
    maps = ''
    if lat is not None and lon is not None:
        maps = f'https://www.google.com/maps/search/?api=1&query={lat},{lon}'
    return {'name': manual or ('Tanklocatie' if maps else ''), 'address': '', 'google_maps_uri': maps, 'source': 'manual'}


def enrich_recent(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    out = serialize_recent(rows, limit)
    for item in out:
        if item['type'] == 'fuel':
            d = station_details_for_row(item)
            item['station_display'] = d['name']
            item['station_address'] = d['address']
            item['google_maps_uri'] = d['google_maps_uri']
    return out


def station_stats_for_period(period: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start, end = period_bounds(period)
    fuels = [r for r in rows if r['type'] == 'fuel' and start <= event_dt(r) < end]
    groups: dict[str, dict[str, Any]] = {}
    for r in fuels:
        place_id = str(r.get('place_id') or '').strip()
        manual = str(r.get('station') or '').strip()
        lat, lon = to_float(r.get('latitude')), to_float(r.get('longitude'))
        if place_id:
            key = 'g:' + place_id
        elif manual:
            key = 'm:' + manual.casefold()
        elif lat is not None and lon is not None:
            key = f'c:{lat:.4f},{lon:.4f}'
        else:
            key = 'unknown'
        g = groups.setdefault(key, {
            'place_id': place_id, 'manual': manual, 'count': 0, 'liters': 0.0,
            'cost': 0.0, 'latitude': lat, 'longitude': lon, 'last': r['created_at']
        })
        g['count'] += 1
        g['liters'] += float(r.get('liters') or 0)
        g['cost'] += float(r.get('cost') or 0)
        if r['created_at'] > g['last']:
            g['last'] = r['created_at']
    out = []
    for g in groups.values():
        fake = {'place_id': g['place_id'], 'station': g['manual'], 'latitude': g['latitude'], 'longitude': g['longitude']}
        d = station_details_for_row(fake)
        liters = g['liters']
        out.append({
            'name': d['name'] or 'Onbekend tankstation', 'address': d['address'],
            'google_maps_uri': d['google_maps_uri'], 'count': g['count'],
            'liters': round(liters,2), 'cost': round(g['cost'],2),
            'avg_price': round(g['cost']/liters,3) if liters > 0 else None,
            'last': g['last'],
        })
    out.sort(key=lambda x: (-x['count'], -x['cost']))
    return out[:12]


def overall_full_tank_average(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    cycles = full_tank_cycles(rows)
    if not cycles:
        return None
    km = sum(float(c['km']) for c in cycles)
    liters = sum(float(c['liters']) for c in cycles)
    cost = sum(float(c['cost']) for c in cycles)
    return {
        'km': round(km,1), 'liters': round(liters,2), 'cycles': len(cycles),
        'l100': round(liters/km*100,2) if km > 0 else None,
        'cost100': round(cost/km*100,2) if km > 0 else None,
    }

def serialize_recent(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    out = []
    visible = [r for r in rows if str(r.get('source_kind') or '') != 'business']
    for r in reversed(visible[-limit:]):
        dt = event_dt(r)
        item = dict(r)
        item['date_label'] = dt.strftime('%d-%m-%Y')
        item['time_label'] = dt.strftime('%H:%M')
        item['delta_km'] = round(float(item.get('delta_km') or 0), 1)
        item['cost'] = round(float(item.get('cost') or 0), 2)
        out.append(item)
    return out


def summary(period: str = 'month') -> dict[str, Any]:
    if period not in {'day','week','month','year'}:
        period = 'month'
    rows = rows_events()
    settings = get_settings()
    latest_fuel = next((r for r in reversed(rows) if r['type'] == 'fuel'), None)
    cycles = full_tank_cycles(rows)
    full_avg = full_tank_period_average(period, rows)
    odo = current_odometer(rows)
    latest_full = next((r for r in reversed(rows) if r['type'] == 'fuel' and int(r.get('full_tank') or 0)), None)
    standalone = standalone_config()
    since_full_km = None
    if odo is not None and latest_full is not None:
        since_full_km = max(0.0, odo - float(latest_full['odometer']))
    return {
        'settings': settings,
        'app': {
            'version': APP_VERSION,
            'places_enabled': bool(places_key()),
            'places_radius_m': places_radius_m(),
            'places_max_results': places_max_results(),
            'standalone_enabled': standalone['enabled'],
            'standalone_ready': standalone['ready'],
            'backup': backup_status(),
        },
        'period': stats_for_period(period, rows),
        'period_full_tank': full_avg,
        'overall_full_tank': overall_full_tank_average(rows),
        'current_odometer': odo,
        'since_full_km': round(since_full_km,1) if since_full_km is not None else None,
        'chart': chart_for_period(period, rows),
        'recent': enrich_recent(rows),
        'station_stats': station_stats_for_period(period, rows),
        'recent_stations': recent_stations(rows),
        'latest_fuel': latest_fuel,
        'latest_full_cycle': cycles[-1] if cycles else None,
        'total_events': len([r for r in rows if str(r.get('source_kind') or '') != 'business']),
        'has_events': bool(rows),
        'business': {
            'period': business_stats_for_period(period),
            'year': business_stats_for_period('year'),
            'active_trip': active_business_trip(),
            'odometer_suggestion': trip_distance_tracking_public(),
            'calibration': distance_calibration(),
            'recent_trips': recent_business_trips(period),
            'audit': recent_audit(24),
            'known_places': known_places_all(),
            'assistant': {
                'config': assistant_config(),
                'runtime': assistant_runtime_public(),
                'pending': assistant_arrivals(8),
            },
        },
    }

def ha_post_state(entity_id: str, state: Any, attrs: dict[str, Any]) -> None:
    token = os.getenv('SUPERVISOR_TOKEN', '')
    if not token:
        return
    url = f'http://supervisor/core/api/states/{entity_id}'
    body = json.dumps({'state': state, 'attributes': attrs}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=4) as _:
            pass
    except Exception:
        pass


def publish_sensors() -> None:
    try:
        rows = rows_events()
        settings = get_settings()
        prefix = sanitize_prefix(str(load_options().get('entity_prefix') or 'auto'))
        odo = current_odometer(rows)
        if odo is not None:
            ha_post_state(f'sensor.{prefix}_kilometerstand', round(odo,1), {
                'friendly_name': f"{settings['vehicle_name']} kilometerstand",
                'unit_of_measurement': 'km', 'icon': 'mdi:counter', 'state_class': 'measurement'
            })
        for p, label in [('week','week'),('month','maand'),('year','jaar')]:
            st = stats_for_period(p, rows)
            ft = full_tank_period_average(p, rows)
            ha_post_state(f'sensor.{prefix}_kilometers_{label}', st['km'], {
                'friendly_name': f"{settings['vehicle_name']} kilometers {label}", 'unit_of_measurement': 'km', 'icon': 'mdi:road-variant'
            })
            ha_post_state(f'sensor.{prefix}_verbruik_{label}', ft['l100'] if ft and ft['l100'] is not None else 'unknown', {
                'friendly_name': f"{settings['vehicle_name']} werkelijk verbruik {label}",
                'unit_of_measurement': 'L/100 km', 'icon': 'mdi:gas-station',
                'measurement_method': 'full_tank', 'cycles': ft['cycles'] if ft else 0
            })
        overall = overall_full_tank_average(rows)
        ha_post_state(f'sensor.{prefix}_verbruik_gemiddeld', overall['l100'] if overall and overall['l100'] is not None else 'unknown', {
            'friendly_name': f"{settings['vehicle_name']} gemiddeld verbruik",
            'unit_of_measurement': 'L/100 km', 'icon': 'mdi:gauge',
            'measurement_method': 'full_tank', 'cycles': overall['cycles'] if overall else 0
        })
        st = stats_for_period('month', rows)
        ha_post_state(f'sensor.{prefix}_brandstofkosten_maand', st['cost'], {
            'friendly_name': f"{settings['vehicle_name']} brandstofkosten maand", 'unit_of_measurement': settings['currency'], 'icon': 'mdi:cash'
        })
        for p, label in [('week','week'),('month','maand'),('year','jaar')]:
            bs = business_stats_for_period(p)
            ha_post_state(f'sensor.{prefix}_zakelijke_km_{label}', bs['business_km'], {
                'friendly_name': f"{settings['vehicle_name']} zakelijke kilometers {label}",
                'unit_of_measurement': 'km', 'icon': 'mdi:briefcase-outline'
            })
        active = active_business_trip()
        ha_post_state(f'sensor.{prefix}_zakelijke_rit_status', 'actief' if active else 'geen', {
            'friendly_name': f"{settings['vehicle_name']} zakelijke rit status",
            'icon': 'mdi:car-clock',
            'trip_id': active['id'] if active else None,
            'kilometers': active['km'] if active else 0,
        })
    except Exception:
        pass

def publish_sensors_async() -> None:
    threading.Thread(target=publish_sensors, daemon=True).start()


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Referrer-Policy', 'no-referrer')
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(data)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = max(0, min(int(handler.headers.get('Content-Length', '0') or 0), 12 * 1024 * 1024))
    except Exception:
        length = 0
    raw = handler.rfile.read(length) if length else b'{}'
    try:
        data = json.loads(raw.decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


LOGIN_HTML = f'''<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0d3a30"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Rit & Tank"><link rel="manifest" href="manifest.webmanifest"><link rel="apple-touch-icon" href="huisplan-icon-180.png"><link rel="icon" type="image/png" href="huisplan-icon-192.png"><title>Inloggen · Rit & Tank</title>
<style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at top,#153d34,#0c0f12 55%);color:#f5faf8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;padding:calc(24px + env(safe-area-inset-top)) 20px calc(24px + env(safe-area-inset-bottom))}}main{{width:min(100%,420px);background:#151c20;border:1px solid #2f6658;border-radius:28px;padding:26px;box-shadow:0 24px 70px #0008}}.icon{{width:76px;height:76px;border-radius:22px;display:block;margin:0 auto 18px}}h1{{margin:0;text-align:center;font-size:30px}}p{{color:#aebdb8;text-align:center;line-height:1.45}}label{{font-size:12px;font-weight:800;color:#b8c7c2}}input{{width:100%;margin-top:7px;border:1px solid #3a4b50;background:#0d1215;color:white;border-radius:15px;padding:14px;font-size:17px}}button{{width:100%;margin-top:15px;border:0;border-radius:15px;background:linear-gradient(135deg,#0e78b8,#0aa684);color:white;padding:14px;font-size:17px;font-weight:900}}#message{{min-height:20px;margin-top:12px;color:#ff9ba5;text-align:center;font-size:13px}}small{{display:block;color:#778883;text-align:center;margin-top:18px}}</style></head>
<body><main><img class="icon" src="huisplan-icon-192.png" alt="Huisplan-logo"><h1>Rit & Tank</h1><p>Log in op je zelfstandige ritten-app.</p><form id="login"><label for="password">Wachtwoord</label><input id="password" type="password" autocomplete="current-password" required autofocus><button type="submit">Inloggen</button><div id="message" role="alert"></div></form><small>Versie {APP_VERSION} · beveiligde standalone-modus</small></main>
<script>document.getElementById('login').addEventListener('submit',async e=>{{e.preventDefault();let m=document.getElementById('message'),b=e.currentTarget.querySelector('button');m.textContent='';b.disabled=true;try{{let r=await fetch('api/auth/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{password:document.getElementById('password').value}})}}),d=await r.json();if(!r.ok)throw new Error(d.error||'Inloggen mislukt');location.replace('./')}}catch(err){{m.textContent=err.message}}finally{{b.disabled=false}}}});</script></body></html>'''.encode('utf-8')


SETUP_HTML = f'''<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0d3a30"><title>Standalone instellen · Rit & Tank</title><style>:root{{color-scheme:dark}}body{{margin:0;min-height:100vh;background:#0c0f12;color:#f5faf8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;padding:24px}}main{{max-width:520px;background:#171c21;border:1px solid #6f542f;border-radius:24px;padding:24px}}h1{{margin-top:0}}p,li{{color:#b7c1c7;line-height:1.5}}code{{color:#88e6c2}}</style></head><body><main><h1>Standalone-modus nog niet gereed</h1><p>Stel in de Home Assistant add-onconfiguratie het volgende in:</p><ol><li><code>standalone_enabled</code> = aan</li><li><code>standalone_password</code> = minimaal 12 tekens</li><li>Publiceer poort 8099 uitsluitend achter een HTTPS reverse proxy.</li></ol><p>Start Rit & Tank daarna opnieuw. Ingress blijft ondertussen beschikbaar.</p><small>Rit & Tank {APP_VERSION}</small></main></body></html>'''.encode('utf-8')


def pwa_manifest() -> bytes:
    return json.dumps({
        'id': './',
        'name': 'Rit & Tank',
        'short_name': 'Rit & Tank',
        'description': 'Rit-, kilometer- en tankregistratie met Home Assistant-koppeling.',
        'lang': 'nl-NL',
        'dir': 'ltr',
        'start_url': './',
        'scope': './',
        'display': 'standalone',
        'display_override': ['window-controls-overlay', 'standalone', 'minimal-ui'],
        'orientation': 'portrait-primary',
        'background_color': '#0c0f12',
        'theme_color': '#0d3a30',
        'categories': ['auto', 'productivity', 'finance'],
        'icons': [
            {'src': 'huisplan-icon-180.png', 'sizes': '180x180', 'type': 'image/png'},
            {'src': 'huisplan-icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
            {'src': 'huisplan-icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
        'shortcuts': [
            {'name': 'Nieuwe rit', 'short_name': 'Rit', 'url': './?open=trip', 'icons': [{'src': 'huisplan-icon-192.png', 'sizes': '192x192'}]},
            {'name': 'Tankbeurt', 'short_name': 'Tanken', 'url': './?open=fuel', 'icons': [{'src': 'huisplan-icon-192.png', 'sizes': '192x192'}]},
        ],
    }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


SERVICE_WORKER = f'''const CACHE = "rit-tank-shell-{APP_VERSION}";
const SHELL = ["./", "manifest.webmanifest", "huisplan-icon-180.png", "huisplan-icon-192.png", "huisplan-icon-512.png", "captur-2014.png"];
self.addEventListener("install", event => {{
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
}});
self.addEventListener("activate", event => {{
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("rit-tank-shell-") && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
}});
self.addEventListener("fetch", event => {{
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.includes("/api/") || url.pathname.endsWith("/health")) return;
  if (request.mode === "navigate") {{
    event.respondWith(fetch(request).then(response => {{
      if (response.ok) caches.open(CACHE).then(cache => cache.put("./", response.clone()));
      return response;
    }}).catch(() => caches.match("./")));
    return;
  }}
  event.respondWith(caches.match(request).then(cached => cached || fetch(request).then(response => {{
    if (response.ok) caches.open(CACHE).then(cache => cache.put(request, response.clone()));
    return response;
  }})));
}});
self.addEventListener("push", event => {{
  let data = {{title: "Rit & Tank", body: "Open de app voor de laatste ritstatus."}};
  try {{ data = Object.assign(data, event.data.json()); }} catch (_) {{}}
  event.waitUntil(self.registration.showNotification(data.title, {{
    body: data.body,
    icon: "huisplan-icon-192.png",
    badge: "huisplan-icon-192.png",
    tag: data.tag || "rit-tank",
    data: {{url: data.url || "./"}},
  }}));
}});
self.addEventListener("notificationclick", event => {{
  event.notification.close();
  const target = new URL(event.notification.data?.url || "./", self.registration.scope).href;
  event.waitUntil(clients.matchAll({{type: "window", includeUncontrolled: true}}).then(items => {{
    for (const client of items) {{ if (client.url.startsWith(self.registration.scope) && "focus" in client) return client.focus(); }}
    return clients.openWindow ? clients.openWindow(target) : undefined;
  }}));
}});
'''.encode('utf-8')


_ICON_CACHE: dict[int, bytes] = {}


def app_icon_png(size: int) -> bytes:
    """Load the bundled Huisplan icon; keep legacy icon URLs compatible."""
    if size not in (180, 192, 512):
        raise ValueError('Unsupported icon size')
    if size not in _ICON_CACHE:
        _ICON_CACHE[size] = Path(__file__).with_name(f'huisplan-icon-{size}.png').read_bytes()
    return _ICON_CACHE[size]


APP_HTML = r'''<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#0d1115">
<meta name="application-name" content="Rit & Tank">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Rit & Tank">
<meta name="format-detection" content="telephone=no">
<meta name="color-scheme" content="dark">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" sizes="180x180" href="huisplan-icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="huisplan-icon-192.png">
<title>Rit & Tank</title>
<style>

#pdfModal{z-index:90;align-items:stretch;background:#e4e8e6;padding-top:env(safe-area-inset-top)}#pdfModal .sheet{width:100%;max-width:100%;height:100%;max-height:100%;border:0;border-radius:0;padding:0;display:flex;flex-direction:column;overflow:hidden}.pdf-toolbar{flex-shrink:0;padding:10px 14px;background:#102921;color:white;border-bottom:1px solid #40836d}.pdf-toolbar .settings-actions{margin-top:8px;grid-template-columns:repeat(4,minmax(0,1fr))}.pdf-toolbar .linkbtn{font-size:14px;min-height:44px;padding:9px 6px}.pdf-toolbar h2{font-size:19px}.pdf-toolbar p{margin:8px 0 0;font-size:12px}#pdfPages{flex:1;min-height:0;overflow:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;background:#e4e8e6;padding:14px 8px calc(14px + env(safe-area-inset-bottom))}.pdf-page{max-width:850px;margin:0 auto 16px;background:white;box-shadow:0 2px 8px #0002}.pdf-page svg{display:block;width:100%;height:auto}body.pdf-preview-open{overflow:hidden}
@media(max-width:480px){.pdf-toolbar .settings-actions{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media print{@page{size:A4;margin:0}html,body{margin:0!important;padding:0!important;background:white!important;height:auto!important;overflow:visible!important}body.printing-pdf>*:not(#pdfModal){display:none!important}body.printing-pdf #pdfModal{display:block!important;position:static!important;padding:0!important;background:white!important}body.printing-pdf #pdfModal .sheet{display:block!important;overflow:visible!important;height:auto!important;max-height:none!important;padding:0!important;background:white!important}body.printing-pdf .pdf-toolbar{display:none!important}body.printing-pdf #pdfPages{overflow:visible!important;padding:0!important;display:block!important;background:white!important}body.printing-pdf .pdf-page{width:210mm;height:297mm;margin:0!important;max-width:none;box-shadow:none;break-after:page;page-break-after:always}body.printing-pdf .pdf-page:last-child{break-after:auto;page-break-after:auto}body.printing-pdf .pdf-page svg{width:210mm;height:297mm}}

#pdfPeriodModal label{display:block;margin-top:16px;color:var(--muted)}#pdfPeriodModal input,#pdfPeriodModal select{display:block;width:100%;margin-top:6px;padding:14px;border:1px solid #34404a;border-radius:14px;background:#0e1216;color:white;font-size:17px}#pdfPeriodModal p{line-height:1.5}
:root{--bg:#0c0f12;--card:#171c21;--card2:#10161b;--line:#2b3540;--text:#f4f7fa;--muted:#97a7b4;--blue:#52baff;--teal:#58dfb1;--orange:#ffb75d;--red:#ff6d7d;--gold:#ffc35f;--shadow:0 12px 34px rgba(0,0,0,.3)}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}body{min-height:100vh;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}button,input,textarea,select{font:inherit}.app{max-width:900px;margin:auto;padding:13px 13px 42px}.topbar{display:flex;align-items:center;justify-content:space-between;padding:7px 3px 13px}.brand{display:flex;gap:10px;align-items:center}.brand-icon{font-size:29px}.brand h1{font-size:21px;margin:0}.brand small{color:var(--muted)}.iconbtn{width:46px;height:46px;border-radius:15px;background:var(--card);border:1px solid var(--line);color:var(--text);font-size:20px}.hero{background:linear-gradient(145deg,#0d3a30,#102921 58%,#141b1a);border:1px solid #277762;border-radius:25px;padding:19px;box-shadow:var(--shadow);display:grid;grid-template-columns:1fr auto;gap:12px}.hero .eyebrow{color:var(--teal);font-weight:900;text-transform:uppercase;font-size:11px;letter-spacing:.08em}.hero h2{font-size:29px;line-height:1.05;margin:4px 0}.odo{font-size:17px;color:#d9e3ea}.hero-stat{text-align:right;align-self:center}.hero-stat strong{font-size:31px;display:block}.hero-stat span{color:var(--muted);font-size:11px}.since-full{margin-top:5px;color:#bad9cc;font-size:11px}.quick{display:grid;grid-template-columns:1.3fr 1fr;gap:9px;margin:11px 0}.quick button{border:0;border-radius:18px;padding:17px 12px;color:white;font-weight:900;font-size:16px}.primary{background:linear-gradient(135deg,#0d78ba,#0ca58d)}.secondary{background:#1b2229;border:1px solid var(--line)!important}.tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:5px;background:#12171b;border:1px solid var(--line);border-radius:17px;margin:13px 0}.tab{border:0;background:transparent;color:var(--muted);padding:11px 3px;border-radius:12px;font-weight:900}.tab.active{background:#25323c;color:white;box-shadow:inset 0 0 0 1px #344552}.period-title{display:flex;align-items:end;justify-content:space-between;margin:17px 2px 8px}.period-title h3{margin:0;font-size:21px}.period-title span{font-size:12px;color:var(--muted)}.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.kpi{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:13px;min-width:0}.kpi .ico{font-size:19px}.kpi b{display:block;font-size:21px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.kpi span{display:block;font-size:11px;color:var(--muted);margin-top:4px}.kpi.em{border-color:#2d6a5b;background:#12251f}.fullavg{margin-top:10px;background:linear-gradient(135deg,#241d10,#1c1812);border:1px solid #725122;border-radius:18px;padding:14px;display:flex;justify-content:space-between;gap:12px}.fullavg b{font-size:25px;color:var(--gold)}.fullavg div:last-child{text-align:right;color:var(--muted);font-size:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:20px;margin-top:12px;padding:15px}.cardhead{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.cardhead h3{margin:0;font-size:18px}.seg{display:flex;background:#10161a;border-radius:10px;padding:3px;overflow:auto}.seg button{border:0;background:transparent;color:var(--muted);padding:6px 8px;border-radius:8px;font-size:11px;white-space:nowrap}.seg button.active{background:#29343e;color:white}.chart-scroll{overflow-x:auto;padding-bottom:5px}.chart{height:190px;display:flex;align-items:flex-end;gap:7px;min-width:100%;padding:15px 4px 0;border-bottom:1px solid #2c343c}.bar-wrap{flex:1;min-width:27px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center}.bar{width:min(28px,80%);min-height:2px;background:linear-gradient(180deg,#63c9ff,#1977bb);border-radius:8px 8px 2px 2px}.bar.orange{background:linear-gradient(180deg,#ffd27e,#d88920)}.bar-val{font-size:9px;color:#aab8c3;margin-bottom:4px;white-space:nowrap}.bar-label{font-size:10px;color:#8e9ba6;margin-top:7px}.station-list,.history{display:flex;flex-direction:column;gap:8px}.station-row{display:grid;grid-template-columns:43px 1fr auto;gap:10px;align-items:center;background:#10161b;border:1px solid #26313a;border-radius:15px;padding:11px}.station-icon{width:42px;height:42px;border-radius:13px;background:#142a26;display:flex;align-items:center;justify-content:center;font-size:21px}.station-row strong{display:block}.station-row small{display:block;color:var(--muted);margin-top:3px;line-height:1.25}.maplink{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;color:#8dd2ff;background:#142635;border:1px solid #25455d;border-radius:11px;padding:8px 9px}.event{display:grid;grid-template-columns:45px 1fr auto;gap:10px;align-items:center;padding:11px;border-radius:14px;background:#10161b;border:1px solid #252e36}.event-icon{width:42px;height:42px;border-radius:13px;display:flex;align-items:center;justify-content:center;background:#1b2b34;font-size:20px}.event strong{display:block;font-size:14px}.event small{display:block;color:var(--muted);margin-top:3px;line-height:1.3}.event .right{text-align:right}.event .right b{display:block}.event-actions{display:flex;justify-content:flex-end;gap:4px;margin-top:3px}.trash{background:none;border:0;color:#84919d;font-size:17px;padding:4px}.empty{text-align:center;color:var(--muted);padding:25px 8px}.toast{position:fixed;left:50%;bottom:calc(25px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(120px);opacity:0;background:#e8f7f1;color:#0b3126;padding:11px 16px;border-radius:14px;font-weight:800;transition:.25s;z-index:80;box-shadow:var(--shadow);max-width:90vw;text-align:center}.toast.show{transform:translateX(-50%) translateY(0);opacity:1}.toast.error{background:#ffe2e5;color:#59131a}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:30;display:none;align-items:flex-end;justify-content:center}.modal.show{display:flex}.sheet{width:min(100%,640px);max-height:94vh;overflow:auto;background:#14191e;border:1px solid #303943;border-radius:27px 27px 0 0;padding:12px 16px calc(21px + env(safe-area-inset-bottom));box-shadow:0 -20px 55px rgba(0,0,0,.45)}.grab{width:44px;height:5px;border-radius:5px;background:#4a5259;margin:0 auto 13px}.sheethead{display:flex;align-items:center;justify-content:space-between}.sheethead h2{margin:0;font-size:22px}.close{background:#222a31;border:0;color:white;width:38px;height:38px;border-radius:12px}.field{margin-top:13px}.field label{display:block;color:#afbac3;font-size:12px;font-weight:800;margin:0 0 6px 3px}.field input,.field textarea,.field select{width:100%;border:1px solid #34404a;background:#0e1216;color:white;border-radius:14px;padding:13px;font-size:17px;outline:none}.field input:focus,.field textarea:focus,.field select:focus{border-color:#4faee8}.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}.wheel-title{text-align:center;color:#aeb9c2;font-size:12px;font-weight:900;margin-top:14px}.wheelbox{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:4px;margin-top:5px}.wheelbox.liters{grid-template-columns:minmax(0,1.2fr) auto minmax(0,1fr) minmax(0,1fr)}.wheelbox.liters .wheel{min-width:0}#receiptScanStatus:empty{display:none}.wheelbox.price{grid-template-columns:.8fr auto .7fr .7fr .7fr}.wheel-sep{font-size:30px;color:#71808d}.wheel{height:150px;overflow-y:auto;scroll-snap-type:y mandatory;border-radius:16px;background:#0d1115;border:1px solid #303a43;position:relative;scrollbar-width:none;padding:50px 0}.wheel::-webkit-scrollbar{display:none}.wheel:after{content:"";position:absolute;left:5px;right:5px;top:50px;height:50px;border-top:1px solid #4b5965;border-bottom:1px solid #4b5965;pointer-events:none}.wheel-item{height:50px;scroll-snap-align:center;display:flex;align-items:center;justify-content:center;font-size:22px;color:#7f8d99;transition:.15s}.wheel-item.sel{font-size:29px;font-weight:900;color:white}.live-total{text-align:center;font-size:15px;color:#b7c4cd;margin-top:9px}.live-total b{color:var(--teal);font-size:21px}.station-input{display:grid;grid-template-columns:1fr 54px;gap:8px}.locate{border:1px solid #2f6685;background:#132938;color:#80cfff;border-radius:14px;font-size:23px}.location-status{font-size:11px;color:var(--muted);margin:7px 3px 0}.location-status.ok{color:#75d7b5}.location-status.err{color:#ff9aa4}.station-results{display:flex;flex-direction:column;gap:7px;margin-top:8px}.station-choice{width:100%;text-align:left;background:#10171c;border:1px solid #2d3943;border-radius:14px;padding:11px;color:white}.station-choice b{display:block;font-size:14px}.station-choice small{display:block;color:#9caab5;margin-top:3px;line-height:1.25}.google-attrib{text-align:right;color:#83929e;font-size:10px;margin:7px 4px 0}.google-attrib b{color:#dfe5ea;letter-spacing:.02em}.toggle{display:flex;align-items:center;justify-content:space-between;background:#0f1418;border:1px solid #303943;padding:12px 13px;border-radius:14px;margin-top:13px}.switch{position:relative;width:50px;height:29px}.switch input{display:none}.slider{position:absolute;inset:0;background:#343c44;border-radius:20px}.slider:before{content:"";position:absolute;width:23px;height:23px;left:3px;top:3px;background:white;border-radius:50%;transition:.2s}.switch input:checked + .slider{background:#19a883}.switch input:checked + .slider:before{transform:translateX(21px)}.save{width:100%;margin-top:15px;border:0;border-radius:16px;background:linear-gradient(135deg,#0e78b8,#0aa684);color:white;padding:15px;font-size:17px;font-weight:900}.settings-actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-top:14px}.linkbtn{display:block;text-align:center;text-decoration:none;color:white;background:#20272e;border:1px solid #34404a;padding:12px;border-radius:14px;font-weight:800}.tech{background:#10161a;border:1px solid #2a343d;border-radius:14px;padding:11px;margin-top:14px;color:#a7b5bf;font-size:12px;line-height:1.45}.badge-ok{color:#67d7ae}.badge-off{color:#ffbe72}
.view-tabs{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:12px 0;background:#10161b;border:1px solid var(--line);padding:5px;border-radius:17px}.view-tab{border:0;border-radius:12px;background:transparent;color:var(--muted);font-weight:900;padding:11px}.view-tab.active{background:#24323d;color:white}.view{display:none}.view.active{display:block}.business-launch{width:100%;margin-top:-2px;margin-bottom:11px;border:1px solid #5a4729;background:linear-gradient(135deg,#342715,#241d12);color:#ffd38a;border-radius:18px;padding:15px;font-weight:900;font-size:16px}.biz-hero{background:linear-gradient(145deg,#251b0f,#1b1712 60%,#11171a);border:1px solid #6d4f27;border-radius:22px;padding:16px;margin-top:11px}.biz-hero.active-trip{border-color:#2b8c73;background:linear-gradient(145deg,#0f3029,#13251f 65%,#11171a)}.biz-hero h3{margin:3px 0 7px;font-size:21px}.biz-hero p{margin:4px 0;color:var(--muted);font-size:13px;line-height:1.35}.biz-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.biz-actions button{border:0;border-radius:14px;padding:13px 9px;font-weight:900;color:white}.biz-next{background:#176d91}.biz-finish{background:#996b21}.biz-start{background:linear-gradient(135deg,#0e7d69,#1274a6);width:100%;border:0;border-radius:15px;padding:14px;color:white;font-weight:900;margin-top:12px}.biz-kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:10px}.trip-card{background:#10161b;border:1px solid #2a343c;border-radius:16px;padding:12px;margin-bottom:9px}.trip-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.trip-top strong{font-size:14px}.trip-top small{color:var(--muted)}.trip-km{font-weight:900;color:var(--teal);white-space:nowrap}.trip-route{margin-top:9px;display:flex;flex-direction:column;gap:6px}.trip-stop{display:grid;grid-template-columns:20px 1fr auto;gap:7px;align-items:start;font-size:12px}.trip-stop .dot{color:#62c9ff}.trip-stop small{display:block;color:var(--muted);margin-top:2px}.trip-stop a{text-decoration:none}.trip-actions{display:flex;justify-content:flex-end;margin-top:8px}.trip-location-box{background:#0e1519;border:1px solid #2d3a43;border-radius:14px;padding:12px;margin-top:12px}.trip-location-box b{display:block}.trip-location-box small{display:block;color:var(--muted);margin-top:4px;line-height:1.3}.location-big{width:100%;border:1px solid #2e6d8e;background:#132b3a;color:#84d2ff;border-radius:15px;padding:14px;font-weight:900;margin-top:8px}.purpose-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}
@media(max-width:560px){.app{padding:10px 10px 34px}.hero{padding:17px;border-radius:22px;grid-template-columns:1fr}.hero h2{font-size:25px}.hero-stat{text-align:left;display:flex;align-items:end;gap:8px}.hero-stat strong{font-size:27px}.hero-stat span{padding-bottom:3px}.kpis{grid-template-columns:repeat(2,1fr)}.kpi b{font-size:20px}.chart{min-width:520px}.quick button{font-size:15px;padding:16px 10px}.event{grid-template-columns:42px 1fr auto}.row2{grid-template-columns:1fr}.fullavg{align-items:center}.fullavg b{font-size:23px}.station-row{grid-template-columns:42px 1fr auto}.cardhead{align-items:flex-start;flex-direction:column}.seg{width:100%;justify-content:space-between}.seg button{flex:1}.sheet{padding-left:13px;padding-right:13px}}

/* V3.1 guided input */
.guide-section{margin-top:12px;padding:12px;border:1px solid #27323b;border-radius:18px;background:#10151a;scroll-margin:14px;transition:border-color .2s,background .2s,box-shadow .2s}
.guide-section.active{border-color:#2d7aa3;background:#111d24;box-shadow:0 0 0 2px rgba(82,186,255,.08)}
.guide-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}.guide-head b{font-size:14px}.guide-head small{margin-left:auto;color:var(--muted);font-size:10px;text-align:right}
.step-badge{width:25px;height:25px;border-radius:9px;background:#17354a;color:#7fd0ff;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:12px;flex:0 0 auto}
.guide-next{width:100%;margin-top:9px;border:1px solid #2d5368;background:#132632;color:#9cdcff;border-radius:13px;padding:11px;font-weight:900}
.odo-wheelbox{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:4px;align-items:center}.odo-wheelbox .wheel{height:140px;padding:45px 0;border-radius:14px}.odo-wheelbox .wheel:after{top:45px}.odo-wheelbox .wheel-item{font-size:20px}.odo-wheelbox .wheel-item.sel{font-size:28px}.odo-live{text-align:center;margin-top:8px}.odo-live b{font-size:28px;color:#fff}.odo-live span{color:var(--muted);margin-left:5px}.odo-last{text-align:center;color:#81909c;font-size:10px;margin-top:4px}.guide-date input{font-size:16px}.guide-tip{font-size:10px;color:#8fa0ac;line-height:1.35;margin-top:7px;text-align:center}
@media(max-width:420px){.guide-section{padding:10px}.odo-wheelbox{gap:3px}.odo-wheelbox .wheel-item{font-size:18px}.odo-wheelbox .wheel-item.sel{font-size:25px}}

[hidden]{display:none!important}.scan-card{display:flex;flex-direction:column;align-items:center;gap:8px;padding:20px;background:#19382f;border:1px solid #40836d;border-radius:18px;cursor:pointer;text-align:center}.scan-card>span{font-size:52px}.scan-card>b{font-size:20px}.scan-card small{color:var(--muted)}.address-choices .selected{outline:2px solid #65d9b0}.address-choices{display:grid;gap:6px;margin-top:12px}#receiptScanStatus{font-size:13px;line-height:1.5}#pdfModal .linkbtn{font:inherit} .export-actions{display:flex;gap:8px;align-items:center}.pdf-link{background:#17251f;border-color:#2d7158;color:#7de0b4}.export-actions .maplink{min-width:42px;text-align:center}.trip-type-pill{display:inline-flex;padding:4px 8px;border-radius:999px;font-size:10px;font-weight:900;margin-top:4px}.trip-type-pill.business{background:#11382f;color:#6ce0b3}.trip-type-pill.private{background:#3b2028;color:#ff9bad}.trip-type-pill.mixed{background:#3b2f15;color:#ffd27a}.trip-actions{gap:7px}.editbtn{background:#172635;border:1px solid #2d5069;color:#8dd2ff;border-radius:10px;padding:6px 9px}.audit-list{display:flex;flex-direction:column;gap:7px}.audit-row{background:#10161b;border:1px solid #27323b;border-radius:13px;padding:10px}.audit-row b{font-size:12px}.audit-row small{display:block;color:var(--muted);font-size:10px;margin-top:3px}.tax-note{font-size:11px;line-height:1.35;color:#a8b5bf;background:#13191e;border:1px solid #2b3540;border-radius:13px;padding:10px;margin-top:9px}.receipt-link{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;background:#2a2117;border:1px solid #624821;color:#ffd28b;border-radius:11px;padding:8px 9px}.filepick{display:block;background:#0f1418;border:1px dashed #3b4a55;border-radius:14px;padding:12px}.filepick input{padding:0;border:0;background:transparent;font-size:13px}.fiscal-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}

.smart-place-bar{display:flex;gap:8px;margin-top:10px}.smart-place-bar button{flex:1;border:1px solid #385064;background:#13212c;color:#9dd9ff;border-radius:14px;padding:11px;font-weight:900}.known-list{display:flex;flex-direction:column;gap:8px}.known-row{display:grid;grid-template-columns:42px 1fr auto;gap:10px;align-items:center;background:#10171c;border:1px solid #2b3841;border-radius:15px;padding:10px}.known-icon{font-size:24px;text-align:center}.known-row small{display:block;color:var(--muted);margin-top:2px;line-height:1.3}.known-actions{display:flex;gap:5px}.known-actions button{border:0;border-radius:10px;padding:7px 9px;background:#1c2b36;color:#a8dbff}.suggest-box{display:none;margin:12px 0;background:linear-gradient(145deg,#122b26,#112027);border:1px solid #2d8069;border-radius:16px;padding:12px}.suggest-box.show{display:block}.suggest-box b{font-size:14px}.suggest-box small{display:block;color:#9db0ba;margin-top:4px;line-height:1.35}.segment-choice{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}.segment-choice button{border:1px solid #35434c;background:#11181d;color:#aab8c2;border-radius:13px;padding:12px;font-weight:900}.segment-choice button.active.business{background:#11382f;border-color:#25866b;color:#74e7bb}.segment-choice button.active.private{background:#3b2028;border-color:#a1465e;color:#ff9bad}.segment-badge{display:inline-flex;margin-left:6px;padding:3px 7px;border-radius:999px;font-size:9px;font-weight:900;background:#1a2a34;color:#8fd5ff}.leg-pill{display:inline-flex;padding:2px 7px;border-radius:999px;font-size:9px;font-weight:900;margin-left:5px}.leg-pill.business{background:#11382f;color:#6ce0b3}.leg-pill.private{background:#3b2028;color:#ff9bad}.place-radius{display:flex;align-items:center;gap:10px}.place-radius input{flex:1}.place-preview{padding:10px;background:#0f1519;border:1px solid #2a3740;border-radius:13px;color:#9fc2d9;font-size:12px;margin-top:8px}
.assistant-panel{display:none;margin:10px 0;border:1px solid #2d8069;background:linear-gradient(145deg,#0e2924,#111c24);border-radius:18px;padding:13px}.assistant-panel.show{display:block}.assistant-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.assistant-head b{font-size:15px}.assistant-head small{display:block;color:#9fb1bc;margin-top:3px;line-height:1.35}.assistant-status{font-size:10px;padding:5px 8px;border-radius:999px;background:#17352d;color:#7ce0b8;font-weight:900;white-space:nowrap}.assistant-status.off{background:#33251a;color:#ffc17a}.assistant-list{display:flex;flex-direction:column;gap:8px;margin-top:10px}.assistant-item{background:#0d161b;border:1px solid #2b4246;border-radius:14px;padding:10px}.assistant-route{font-weight:900;font-size:13px}.assistant-meta{font-size:10px;color:#94a6b2;margin-top:3px;line-height:1.35}.assistant-actions{display:grid;grid-template-columns:1fr 1fr auto auto;gap:6px;margin-top:8px}.assistant-actions button{border:1px solid #34454f;background:#152029;color:#c5d2da;border-radius:10px;padding:9px 7px;font-weight:900}.assistant-actions .private{background:#342028;border-color:#864052;color:#ff9bad}.assistant-actions .business{background:#11372f;border-color:#27765f;color:#79deb9}.assistant-actions .complete{background:#12304a;border-color:#28638e;color:#8fd1ff}.assistant-actions .dismiss{min-width:38px}.assistant-zone-ok{color:#71d9b2}.assistant-zone-err{color:#ff9a9a}.assistant-settings{margin-top:14px;padding:12px;background:#0f1519;border:1px solid #2d3a43;border-radius:15px}.assistant-settings h3{margin:0 0 6px;font-size:15px}.assistant-settings p{margin:0 0 9px;color:#91a2ae;font-size:11px;line-height:1.4}.assistant-route-big{font-size:18px;font-weight:900;text-align:center;padding:11px;background:#0e151a;border:1px solid #2c3942;border-radius:14px;margin-top:10px}.assistant-note{font-size:11px;color:#9fb0ba;line-height:1.4;margin-top:8px}.odo-suggest{display:none;margin:2px 0 10px;padding:18px;border:1px solid rgba(69,240,195,.38);background:linear-gradient(145deg,#103a31,#0b211d);border-radius:21px;text-align:center;box-shadow:0 14px 35px rgba(0,0,0,.22)}.odo-suggest.show{display:block}.odo-suggest-label{color:#78e8ca;font-size:10px;font-weight:900;letter-spacing:.14em;text-transform:uppercase}.odo-suggest-value{font-size:42px;font-weight:900;letter-spacing:-.055em;margin-top:7px;color:#fff}.odo-suggest-value span{font-size:16px;letter-spacing:0;color:#b9cdc7}.odo-suggest-detail{color:#9eb5af;font-size:11px;line-height:1.4;margin-top:8px}.odo-suggest-actions{display:grid;grid-template-columns:1.35fr .65fr;gap:8px;margin-top:15px}.odo-suggest-accept,.odo-suggest-edit{border-radius:15px;padding:13px 8px;font-weight:900}.odo-suggest-accept{border:0;background:linear-gradient(135deg,#50eec7,#19b9a5);color:#05251d}.odo-suggest-edit{border:1px solid rgba(141,211,193,.23);background:#10211e;color:#d7e7e2}.odo-editor[hidden]{display:none}
.offline-banner{display:none;position:sticky;top:calc(env(safe-area-inset-top) + 4px);z-index:25;margin:0 auto 10px;max-width:620px;padding:9px 12px;border-radius:13px;background:#3a2917;border:1px solid #80602f;color:#ffd491;text-align:center;font-size:12px;font-weight:800}.offline-banner.show{display:block}.pwa-card{margin-top:14px;padding:13px;border-radius:17px;background:linear-gradient(145deg,#102a25,#111c22);border:1px solid #2b6f5d}.pwa-card b{display:block}.pwa-card small{display:block;color:#a6bab4;line-height:1.4;margin:4px 0 10px}.pwa-install{width:100%;border:1px solid #34866f;background:#123b31;color:#82e7c2;border-radius:13px;padding:12px;font-weight:900}.pwa-install:disabled{opacity:.6}

/* V4.2 premium mobile interface */
:root{--bg:#07100f;--card:#0d1a18;--card2:#0a1413;--line:rgba(122,222,194,.16);--text:#f6fbf9;--muted:#8da6a0;--blue:#43d9cd;--teal:#45f0c3;--orange:#ffc66e;--red:#ff778a;--gold:#ffc66e;--shadow:0 22px 60px rgba(0,0,0,.34)}
html{background:#050b0a}body{background:radial-gradient(circle at 50% -12%,rgba(32,137,113,.19),transparent 34%),linear-gradient(180deg,#08110f 0%,#07100f 55%,#050b0a 100%);background-attachment:fixed}.app{max-width:680px;padding:10px 16px calc(112px + env(safe-area-inset-bottom))}.topbar{padding:9px 2px 17px}.brand{gap:12px}.brand-mark{width:48px;height:48px;border-radius:17px;display:grid;place-items:center;color:var(--teal);background:linear-gradient(145deg,rgba(42,235,188,.16),rgba(16,89,75,.12));border:1px solid rgba(69,240,195,.25);box-shadow:inset 0 1px 0 rgba(255,255,255,.07)}.brand-mark svg{width:29px;height:29px}.brand h1{font-size:25px;letter-spacing:-.035em}.brand small{font-size:13px;margin-top:2px;display:block}.iconbtn{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;background:rgba(14,31,29,.82);border:1px solid rgba(132,207,187,.2);color:#dff8f1;box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}.iconbtn svg{width:21px;height:21px}
.hero{position:relative;overflow:hidden;min-height:224px;padding:24px;border:1px solid rgba(69,240,195,.34);border-radius:28px;background:radial-gradient(circle at 82% 26%,rgba(64,205,171,.18),transparent 30%),linear-gradient(145deg,#10372f,#0b241f 58%,#091614);box-shadow:0 24px 70px rgba(0,0,0,.38),inset 0 1px 0 rgba(255,255,255,.05);grid-template-columns:minmax(0,1.25fr) minmax(130px,.75fr);align-items:center}.hero:after{content:"";position:absolute;inset:auto -18% -55% 22%;height:180px;border-radius:50%;border-top:1px solid rgba(100,255,211,.2);transform:rotate(-8deg);box-shadow:0 -25px 70px rgba(23,171,137,.08);pointer-events:none}.hero-copy{position:relative;z-index:2}.hero .eyebrow{color:#67f1cb;font-size:10px;letter-spacing:.16em}.hero h2{font-size:20px;font-weight:650;color:#bcd0ca;margin:8px 0 16px}.odo{display:flex;align-items:baseline;gap:7px;color:white}.odo strong{font-size:43px;line-height:1;font-weight:850;letter-spacing:-.055em}.odo span{font-size:18px;font-weight:750;color:#d9e8e4}.hero-consumption{display:flex;align-items:center;gap:8px;margin-top:18px;color:#dcebe7;font-size:13px}.hero-consumption svg{width:18px;height:18px;color:var(--teal)}.hero-consumption strong{font-size:16px}.since-full{margin-top:8px;color:#8facA4;font-size:11px}.hero-car{position:relative;z-index:1;color:rgba(100,255,214,.88);align-self:center}.hero-car svg{width:100%;height:auto;filter:drop-shadow(0 14px 16px rgba(0,0,0,.45))}.hero-car .car-body{fill:rgba(7,25,22,.74);stroke:currentColor;stroke-width:3}.hero-car .car-glass{fill:rgba(80,215,190,.13);stroke:rgba(138,255,227,.42);stroke-width:2}.hero-car .wheel{fill:#07100f;stroke:#5de7c1;stroke-width:3}
.dashboard-actions{margin:15px 0 13px}.trip-primary{width:100%;min-height:66px;border:1px solid rgba(119,255,222,.52);border-radius:22px;color:#05241c;background:linear-gradient(135deg,#55f2ca 0%,#22d5bd 50%,#18af9e 100%);box-shadow:0 17px 38px rgba(22,201,164,.2),inset 0 1px 0 rgba(255,255,255,.52);font-size:18px;font-weight:900;display:flex;align-items:center;justify-content:center;gap:11px}.trip-primary svg{width:23px;height:23px}.action-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}.action-secondary{min-height:64px;border:1px solid rgba(133,205,187,.2);border-radius:20px;background:linear-gradient(145deg,rgba(19,39,36,.96),rgba(9,22,20,.96));color:#eaf5f2;font-weight:800;display:flex;align-items:center;justify-content:center;gap:10px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}.action-secondary svg{width:21px;height:21px;color:var(--teal)}
.tabs{margin:15px 0;padding:4px;border-radius:17px;background:rgba(5,14,13,.7);border-color:rgba(136,213,193,.15)}.tab{padding:10px 3px;font-size:12px}.tab.active{background:linear-gradient(145deg,#17342e,#102721);box-shadow:inset 0 0 0 1px rgba(87,236,198,.23),0 7px 16px rgba(0,0,0,.2)}.period-title{margin:22px 3px 11px}.period-title h3{font-size:24px;letter-spacing:-.035em}.kpis{gap:10px}.kpi{background:linear-gradient(145deg,rgba(17,36,33,.95),rgba(8,20,18,.96));border-color:rgba(130,213,191,.15);border-radius:20px;padding:15px;box-shadow:inset 0 1px 0 rgba(255,255,255,.025)}.kpi.em{background:linear-gradient(145deg,#103129,#0b211c);border-color:rgba(69,240,195,.28)}.kpi b{font-size:22px;letter-spacing:-.035em}.kpi span{color:#86a39b}.fullavg{border-radius:20px;background:linear-gradient(135deg,rgba(69,45,13,.72),rgba(27,24,15,.94));border-color:rgba(255,190,88,.28)}.card{border-radius:23px;background:linear-gradient(145deg,rgba(15,31,28,.96),rgba(7,17,16,.97));border-color:rgba(129,211,189,.14);box-shadow:inset 0 1px 0 rgba(255,255,255,.025)}.cardhead h3{font-size:19px}.bar{background:linear-gradient(180deg,#55f0cb,#159982);box-shadow:0 0 14px rgba(45,224,185,.18)}.seg{background:#071210}.seg button.active{background:#17352f;color:#e9fff9}.station-row,.event,.trip-card,.audit-row,.known-row,.assistant-item{background:rgba(5,16,14,.74);border-color:rgba(127,203,183,.14)}
.bottom-nav{position:fixed;z-index:24;left:50%;bottom:0;transform:translateX(-50%);width:min(680px,100%);display:grid;grid-template-columns:repeat(4,1fr);padding:9px 12px calc(8px + env(safe-area-inset-bottom));background:linear-gradient(180deg,rgba(8,19,17,.92),rgba(4,10,9,.98));border-top:1px solid rgba(125,213,188,.16);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);box-shadow:0 -18px 45px rgba(0,0,0,.34)}.nav-item{position:relative;border:0;background:transparent;color:#7f9992;min-height:58px;border-radius:17px;padding:7px 4px 5px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;font-size:11px;font-weight:750}.nav-item svg{width:23px;height:23px}.nav-item.active{color:var(--teal);background:rgba(46,223,182,.08)}.nav-item.active:after{content:"";position:absolute;bottom:2px;width:22px;height:3px;border-radius:4px;background:var(--teal);box-shadow:0 0 12px rgba(69,240,195,.5)}
.modal{background:rgba(1,8,7,.76);backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px)}.sheet{background:linear-gradient(180deg,#101c1a,#08110f);border-color:rgba(133,220,196,.18);border-radius:31px 31px 0 0;box-shadow:0 -30px 80px rgba(0,0,0,.55)}.grab{background:#415a54}.close{border-radius:50%;background:#172724;border:1px solid rgba(133,211,190,.14)}.field label{color:#acc3bd}.field input,.field textarea,.field select{background:#07100f;border-color:rgba(137,205,187,.22);border-radius:16px}.field input:focus,.field textarea:focus,.field select:focus{border-color:#43d9bd;box-shadow:0 0 0 3px rgba(67,217,189,.1)}.guide-section{background:rgba(7,18,16,.8);border-color:rgba(121,196,177,.15);border-radius:20px}.guide-section.active{background:linear-gradient(145deg,#0d2923,#0a1d1a);border-color:rgba(69,240,195,.34);box-shadow:0 0 0 3px rgba(69,240,195,.05)}.step-badge{background:#153c33;color:#65f0ca;border-radius:50%}.guide-next,.location-big{background:#102d28;border-color:#276e5e;color:#81efd1}.save,.biz-start{background:linear-gradient(135deg,#50eec7,#17b9a5);color:#05251d;box-shadow:0 14px 30px rgba(27,203,166,.16)}.toggle{background:#081311;border-color:rgba(133,206,187,.18);border-radius:17px}.slider,.switch input:checked + .slider{transition:.2s}.switch input:checked + .slider{background:#20bea2}
.toast{bottom:calc(94px + env(safe-area-inset-bottom));background:#dffbf3;color:#09281f}.biz-hero{border-radius:26px;background:linear-gradient(145deg,#231a0d,#121710);border-color:rgba(255,190,91,.28)}.biz-hero.active-trip{background:linear-gradient(145deg,#0e342a,#0a211c);border-color:rgba(69,240,195,.34)}
@media(max-width:560px){.app{padding:8px 13px calc(108px + env(safe-area-inset-bottom))}.hero{padding:21px;min-height:215px;grid-template-columns:minmax(0,1.35fr) minmax(110px,.65fr)}.hero h2{font-size:18px}.odo strong{font-size:38px}.hero-stat{display:block}.kpis{grid-template-columns:repeat(3,1fr)}.kpi{padding:13px 11px}.kpi .ico{font-size:16px}.kpi b{font-size:18px}.kpi span{font-size:10px}.chart{min-width:500px}.sheet{padding-left:15px;padding-right:15px}}
@media(max-width:390px){.hero{grid-template-columns:1fr 105px;padding:18px}.odo strong{font-size:34px}.action-secondary{font-size:13px}.kpis{grid-template-columns:repeat(2,1fr)}.brand h1{font-size:23px}}
.hero-car img{display:block;width:100%;height:auto;object-fit:contain;border-radius:20px}.hero-car{min-width:0}.hero{grid-template-columns:minmax(0,1fr) minmax(200px,1.1fr);gap:18px}
@media(max-width:560px){.hero{grid-template-columns:minmax(0,1fr);gap:12px}.hero-car{width:100%;max-width:360px;justify-self:center}.hero-car img{border-radius:16px}}
</style>
</head>
<body>
<div class="app">
  <div class="offline-banner" id="offlineBanner">Geen verbinding — de app-interface blijft beschikbaar; opslaan en synchroniseren hervatten zodra je weer online bent.</div>
  <div class="topbar">
    <div class="brand"><div class="brand-mark"><img src="huisplan-icon-192.png" alt="Huisplan-logo" width="48" height="48" style="display:block;width:100%;height:100%;border-radius:16px"></div><div><h1>Rit & Tank</h1><small id="fuelType">Benzine</small></div></div>
    <button class="iconbtn" onclick="openSettings()" aria-label="Instellingen"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></svg></button>
  </div>


  <section class="hero">
    <div class="hero-copy"><div class="eyebrow">Kilometerstand</div><h2 id="vehicleName">Mijn auto</h2><div class="odo"><strong id="odometer">—</strong><span>km</span></div><div class="hero-consumption"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 21V4h10v17M3 21h14M7 7h6v5H7zM15 8h2l2 2v7a2 2 0 0 0 4 0v-5l-2-2"/></svg><span><strong id="heroConsumption">—</strong> L/100 km <small>laatste volle tank</small></span></div><div class="since-full" id="sinceFull">—</div></div>
    <div class="hero-car"><img src="captur-2014.png" alt="Zilvergrijze Renault Captur uit 2014, illustratieve afbeelding" width="1536" height="1024" fetchpriority="high"></div>
  </section>

  <div class="dashboard-actions">
    <button class="trip-primary" style="margin-bottom:10px" onclick="openPdfSelector()">📄 PDF-ritregistratie</button>
    <button class="trip-primary" onclick="smartTripAction()"><svg viewBox="0 0 24 24" fill="currentColor"><path d="m9 6 9 6-9 6V6Z"/></svg><span id="tripPrimaryLabel">Rit starten</span></button>
    <div class="action-row"><button class="action-secondary" onclick="openFuel()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 21V4h10v17M3 21h14M7 7h6v5H7zM15 8h2l2 2v7a2 2 0 0 0 4 0v-5l-2-2"/></svg><span>Tankbeurt</span></button><button class="action-secondary" onclick="openKm()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 19a8 8 0 1 1 16 0H4Z"/><path d="m12 17 4-5M7 16l-1-1m11-1 1-1m-6-2V9"/></svg><span>KM bijwerken</span></button></div>
  </div>

  <div class="tabs"><button class="tab" data-period="day">Dag</button><button class="tab" data-period="week">Week</button><button class="tab active" data-period="month">Maand</button><button class="tab" data-period="year">Jaar</button></div>

  <div class="view active" id="view-auto">
    <div class="period-title"><h3 id="periodLabel">Deze maand</h3><span id="fuelCount"></span></div>
    <section class="kpis">
      <div class="kpi em"><div class="ico">🛣️</div><b id="kpiKm">—</b><span>Kilometers</span></div>
      <div class="kpi"><div class="ico">⛽</div><b id="kpiLiters">—</b><span>Getankt</span></div>
      <div class="kpi"><div class="ico">📉</div><b id="kpiL100">—</b><span>Werkelijk L/100 km</span></div>
      <div class="kpi"><div class="ico">💶</div><b id="kpiCost">—</b><span>Brandstofkosten</span></div>
      <div class="kpi"><div class="ico">🏷️</div><b id="kpiPrice">—</b><span>Gem. prijs/liter</span></div>
      <div class="kpi"><div class="ico">📊</div><b id="kpiCost100">—</b><span>Werkelijk €/100 km</span></div>
    </section>
    <section class="fullavg"><div><small>VOLLE-TANK GEMIDDELDE</small><br><b id="fullAvgVal">—</b></div><div id="fullAvgInfo">Nog geen complete cyclus</div></section>
    <section class="card"><div class="cardhead"><h3>Verloop</h3><div class="seg"><button class="active" data-metric="km">km</button><button data-metric="liters">L</button><button data-metric="cost">€</button><button data-metric="l100">L/100</button></div></div><div class="chart-scroll"><div class="chart" id="chart"></div></div></section>
    <section class="card"><div class="cardhead"><h3>📍 Tankstations</h3><span style="color:var(--muted);font-size:12px" id="stationCount"></span></div><div class="station-list" id="stationStats"></div></section>
    <section class="card"><div class="cardhead"><h3>Recente registraties</h3><span style="color:var(--muted);font-size:12px" id="eventCount"></span></div><div class="history" id="history"></div></section>
  </div>

  <div class="view" id="view-business">
    <section class="biz-hero" id="bizHero"><div class="eyebrow">RITTENREGISTRATIE · FISCALE MODUS</div><h3 id="bizHeroTitle">Geen actieve rit</h3><p id="bizHeroInfo">Start een rit en leg bij ieder adres je actuele locatie en kilometerstand vast.</p><div id="bizHeroActions"></div></section>
    <section class="assistant-panel" id="assistantPanel"><div class="assistant-head"><div><b>Te controleren</b><small id="assistantStatusText"></small></div><span class="assistant-status" id="assistantStatusBadge">AAN</span></div><div class="assistant-list" id="assistantList"></div></section>
    <div class="period-title"><h3>Zakelijk · <span id="bizPeriodLabel">deze maand</span></h3><span id="bizTripCount"></span></div>
    <section class="biz-kpis fiscal-grid">
      <div class="kpi em"><div class="ico">💼</div><b id="bizKm">—</b><span>Zakelijke km</span></div>
      <div class="kpi"><div class="ico">🏠</div><b id="bizPrivateKm">—</b><span>Privé km</span></div>
      <div class="kpi"><div class="ico">🚗</div><b id="bizTrips">—</b><span>Ritten</span></div>
      <div class="kpi"><div class="ico">📍</div><b id="bizStops">—</b><span>Locatiemomenten</span></div>
      <div class="kpi"><div class="ico">↔️</div><b id="bizAvg">—</b><span>Gem. km/rit</span></div>
      <div class="kpi"><div class="ico">📅</div><b id="bizPrivateYear">—</b><span>Privé km dit jaar</span></div>
    </section>
    <div class="smart-place-bar"><button onclick="openKnownPlaces()">📌 Bekende plekken & slimme regels</button></div>
    <div class="tax-note">Slimme modus: iedere etappe tussen twee locaties krijgt apart een voorstel Zakelijk/Privé. Je bevestigt met één tik; de app onthoudt terugkerende routes. Voor een volledige rittenregistratie zijn o.a. datum, begin/eindstand, vertrek- en aankomstadres, ritsoort, eventuele afwijkende route en privé-omrijkilometers relevant. Controleer altijd of dit past bij jouw fiscale situatie.</div>
    <section class="card"><div class="cardhead"><h3>Ritten in deze periode</h3><div class="export-actions"><a class="maplink" id="bizCsvLink" href="api/business.csv">CSV</a><a class="maplink pdf-link" id="bizPdfLink" onclick="openPdfSelector(event)" href="api/business.pdf?period=month">PDF</a></div></div><div id="businessHistory"></div></section>
    <section class="card"><div class="cardhead"><h3>🧾 Wijzigingslogboek</h3><span style="color:var(--muted);font-size:11px">laatste 24</span></div><div class="audit-list" id="auditHistory"></div></section>
  </div>
</div>
<nav class="bottom-nav" aria-label="Hoofdnavigatie">
  <button class="nav-item view-tab active" data-view="auto"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 11.5 12 4l9 7.5V21h-6v-6H9v6H3v-9.5Z"/></svg><span>Overzicht</span></button>
  <button class="nav-item view-tab" data-view="business"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 3h14v18H5zM8 7h8M8 11h8M8 15h5"/></svg><span>Ritten</span></button>
  <button class="nav-item" onclick="openFuel()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M5 21V4h10v17M3 21h14M7 7h6v5H7zM15 8h2l2 2v7a2 2 0 0 0 4 0v-5l-2-2"/></svg><span>Tanken</span></button>
  <button class="nav-item" onclick="openSettings()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM12 2v3m0 14v3M2 12h3m14 0h3"/></svg><span>Meer</span></button>
</nav>
<div class="toast" id="toast"></div>
<div class="modal" id="pdfPeriodModal"><div class="sheet"><div class="sheethead"><h2>PDF-ritregistratie</h2><button class="close" onclick="closeModal('pdfPeriodModal')">✕</button></div><label>Periode<select id="pdfRange" onchange="$('pdfMonthLabel').hidden=this.value==='year'"><option value="month">Eén maand</option><option value="year">Heel jaar</option></select></label><label>Jaar<input id="pdfYear" type="number" min="1900" max="9998" step="1" list="pdfYears"></label><datalist id="pdfYears"></datalist><label id="pdfMonthLabel">Maand<select id="pdfMonth"></select></label><p>Bij een heel jaar worden alle maanden met ritten op volgorde in één PDF opgenomen. Ritten worden ingedeeld op vertrekdatum.</p><button class="save" onclick="confirmPdfPeriod()">OK — PDF maken</button><p id="pdfPeriodError" role="alert"></p></div></div>
<div class="modal" id="pdfModal"><div class="sheet"><div class="pdf-toolbar"><div class="sheethead"><h2>PDF-ritregistratie</h2><button class="linkbtn" onclick="closePdfPreview()">Sluiten ✕</button></div><div class="settings-actions"><button class="linkbtn" id="pdfShare" onclick="sharePdf()" disabled>Delen / andere app</button><button class="linkbtn" id="pdfPrint" onclick="printPdfPreview()" disabled>Afdrukken</button><a class="linkbtn" id="pdfDownload" hidden>Download PDF</a><button class="linkbtn" id="pdfDrive" onclick="archivePdf()" disabled>Google Drive</button></div><p id="pdfStatus" role="status">PDF voorbereiden…</p></div><div id="pdfPages" aria-label="Voorbeeld rittenregistratie"></div></div></div>

<div class="modal" id="fuelModal"><div class="sheet" id="fuelSheet"><div class="grab"></div><div class="sheethead"><h2>⛽ Tankbeurt</h2><button class="close" onclick="closeModal('fuelModal')">✕</button></div>
  <div class="guide-section" id="fuelStepScan"><label class="scan-card" for="fuelReceipt"><span aria-hidden="true">📷</span><b>Tankbon scannen</b></label><input id="fuelReceipt" type="file" accept="image/*" capture="environment"><p id="receiptScanStatus" role="status"></p></div>
  <input id="fuelOdo" type="hidden">
  <div class="guide-section active" id="fuelStepOdo"><div class="guide-head"><span class="step-badge">1</span><b>Kilometerstand</b><small>Scroll de cijfers</small></div><div class="odo-wheelbox" id="fuelOdoWheels"></div><div class="odo-live"><b id="fuelOdoDisplay">—</b><span>km</span></div><div class="odo-last" id="fuelOdoLast"></div><button class="guide-next" type="button" onclick="guideTo('fuelStepDate')">Kilometerstand staat goed →</button></div>
  <div class="guide-section guide-date" id="fuelStepDate"><div class="guide-head"><span class="step-badge">2</span><b>Datum & tijd</b><small>Staat standaard op nu</small></div><div class="field" style="margin-top:0"><input id="fuelDate" type="datetime-local"></div><button class="guide-next" type="button" onclick="guideTo('fuelStepLiters')">Verder naar liters →</button></div>
  <div class="guide-section" id="fuelStepLiters"><div class="guide-head"><span class="step-badge">3</span><b>Liters</b><small>Liters met 2 decimalen</small></div><div class="wheelbox liters"><div class="wheel" id="literWhole"></div><div class="wheel-sep">,</div><div class="wheel" id="literDec"></div><div class="wheel" id="literDec2"></div></div><button class="guide-next" type="button" onclick="guideTo('fuelStepPrice')">Verder naar prijs →</button></div>
  <div class="guide-section" id="fuelStepPrice"><div class="guide-head"><span class="step-badge">4</span><b>Prijs per liter</b><small>3 decimalen</small></div><div class="wheelbox price"><div class="wheel" id="priceWhole"></div><div class="wheel-sep">,</div><div class="wheel" id="priceD1"></div><div class="wheel" id="priceD2"></div><div class="wheel" id="priceD3"></div></div><div class="live-total">Totaal: <b id="fuelTotal">€ 0,00</b></div><button class="guide-next" type="button" onclick="guideTo('fuelStepLocation')">Verder naar tankstation →</button></div>
  <div class="guide-section" id="fuelStepLocation"><div class="guide-head"><span class="step-badge">5</span><b>Tankstation / locatie</b><small>GPS of handmatig</small></div><div class="station-input"><input id="fuelStation" list="stations" placeholder="Typ handmatig of gebruik 📍"><button class="locate" type="button" onclick="findStations()" aria-label="Gebruik huidige locatie">📍</button></div><datalist id="stations"></datalist><div class="location-status" id="locationStatus">Tik op 📍 om tankstations in de buurt te zoeken.</div><div class="station-results" id="stationResults"></div><div class="google-attrib" id="googleAttrib" style="display:none">Resultaten via <b translate="no">Google Maps</b></div><button class="guide-next" type="button" onclick="guideTo('fuelStepFinish')">Verder →</button></div>
  <div class="guide-section" id="fuelStepFinish"><div class="guide-head"><span class="step-badge">6</span><b>Afronden</b><small>Controleer en sla op</small></div><div class="toggle" style="margin-top:0"><div><b>Volgetankt</b><div style="color:var(--muted);font-size:11px">Nodig voor betrouwbaar werkelijk verbruik</div></div><label class="switch"><input id="fuelFull" type="checkbox" checked><span class="slider"></span></label></div><div class="field"><label>Notitie (optioneel)</label><input id="fuelNote" maxlength="200" placeholder="Bijv. snelweg, vakantie..."></div><button class="save" id="fuelSaveButton" onclick="saveFuel()">Tankbeurt opslaan</button></div>
</div></div>

<div class="modal" id="kmModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>🛣️ Kilometerstand</h2><button class="close" onclick="closeModal('kmModal')">✕</button></div><input id="kmOdo" type="hidden"><div class="guide-section active" id="kmStepOdo"><div class="guide-head"><span class="step-badge">1</span><b>Nieuwe kilometerstand</b><small>Laatste stand is vooringesteld</small></div><div class="odo-wheelbox" id="kmOdoWheels"></div><div class="odo-live"><b id="kmOdoDisplay">—</b><span>km</span></div><div class="odo-last" id="kmOdoLast"></div><button class="guide-next" type="button" onclick="guideTo('kmStepRest')">Verder →</button></div><div class="guide-section" id="kmStepRest"><div class="field" style="margin-top:0"><label>Datum & tijd</label><input id="kmDate" type="datetime-local"></div><div class="field"><label>Notitie (optioneel)</label><input id="kmNote" maxlength="200" placeholder="Bijv. thuiskomst, zakelijke rit..."></div><button class="save" onclick="saveKm()">Kilometerstand opslaan</button></div></div></div>

<div class="modal" id="tripModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2 id="tripModalTitle">💼 Zakelijke rit</h2><button class="close" onclick="closeModal('tripModal')">✕</button></div>
  <div id="tripStartFields"><div class="tax-note">Start alleen je ritregistratie. Vanaf de eerstvolgende locatie classificeert de app <b>iedere etappe apart</b> als zakelijk of privé.</div><div class="purpose-grid"><div class="field"><label>Doel / afspraak</label><input id="tripPurpose" maxlength="120" placeholder="Bijv. klantbezoek"></div><div class="field"><label>Klant / project</label><input id="tripClient" maxlength="120" placeholder="Optioneel"></div></div><div class="field"><label>Ritnotitie (optioneel)</label><input id="tripTripNote" maxlength="250" placeholder="Bijv. offertebespreking"></div></div>
  <input id="tripOdo" type="hidden"><div class="guide-section active" id="tripStepOdo"><div class="guide-head"><span class="step-badge">1</span><b>Kilometerstand</b><small id="tripOdoStepHint">Laatste stand is vooringesteld</small></div><div class="odo-suggest" id="tripOdoSuggestion"><div class="odo-suggest-label">Berekende kilometerstand</div><div class="odo-suggest-value"><b id="tripOdoSuggestedValue">—</b> <span>km</span></div><div class="odo-suggest-detail" id="tripOdoSuggestedDetail"></div><div class="odo-suggest-actions"><button class="odo-suggest-accept" type="button" onclick="acceptTripOdoSuggestion()">✓ Akkoord</button><button class="odo-suggest-edit" type="button" onclick="editTripOdoSuggestion()">Wijzigen</button></div></div><div class="odo-editor" id="tripOdoEditor"><div class="odo-wheelbox" id="tripOdoWheels"></div><div class="odo-live"><b id="tripOdoDisplay">—</b><span>km</span></div><div class="odo-last" id="tripOdoLast"></div><label class="assistant-note"><input type="checkbox" id="tripOdoChecked"> Ik heb de vorige én huidige tellerstand gecontroleerd; gebruik dit traject voor kilometerleren.</label><button class="guide-next" type="button" onclick="guideTo('tripStepLocation')">Kilometerstand bevestigen →</button></div></div><div class="guide-section" id="tripStepLocation"><div class="field" style="margin-top:0"><label>Datum & tijd</label><input id="tripDate" type="datetime-local"></div><div class="trip-location-box"><b id="tripLocationTitle">📍 Nog geen locatie vastgelegd</b><small id="tripLocationDetail">Tik hieronder zodra je op de juiste plek bent.</small><button class="location-big" type="button" onclick="captureTripLocation()">📍 Gebruik huidige locatie</button><div id="tripAddressChoices" class="address-choices"></div><div class="field"><label for="tripManualAddress">Adres uit je afspraak (eventueel corrigeren)</label><input id="tripManualAddress" maxlength="120" placeholder="Straat, huisnummer en plaats"><button class="guide-next" type="button" onclick="confirmManualTripAddress()">Dit adres gebruiken</button></div><div class="google-attrib" id="tripGoogleAttrib" style="display:none">Adres via <b translate="no">Google Maps</b></div></div></div>
  <div class="suggest-box" id="tripSuggestionBox"><b id="tripSuggestionTitle">Slim voorstel</b><small id="tripSuggestionReason"></small><div class="segment-choice"><button type="button" id="segmentBusiness" class="business" onclick="setSegmentType('business');advanceTripAfterLocation()">💼 Zakelijk</button><button type="button" id="segmentPrivate" class="private" onclick="setSegmentType('private');advanceTripAfterLocation()">🏠 Privé</button></div></div>
  <div class="field"><label>Notitie bij deze stop (optioneel)</label><input id="tripStopNote" maxlength="250" placeholder="Bijv. bezoek afgerond"></div><div id="tripFinishFields" style="display:none"><div class="field"><label>Afwijkende route (alleen indien van toepassing)</label><input id="tripDeviatingRoute" maxlength="300" placeholder="Bijv. omleiding via A1 wegens afsluiting"></div><div class="field"><label>Privé-omrijkilometers</label><input id="tripPrivateDetour" type="number" min="0" step="0.1" inputmode="decimal" value="0"></div></div>
  <button class="save" id="tripSaveButton" onclick="saveTripPoint()">Opslaan</button>
</div></div>

<div class="modal" id="tripEditModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>✏️ Rit corrigeren</h2><button class="close" onclick="closeModal('tripEditModal')">✕</button></div>
  <input id="editTripId" type="hidden"><div class="field"><label>Ritsoort</label><select id="editTripType"><option value="business">💼 Zakelijk</option><option value="private">🏠 Privé</option><option value="mixed">🔀 Gemengd</option></select></div><div class="field"><label>Doel / afspraak</label><input id="editTripPurpose" maxlength="120"></div><div class="field"><label>Klant / project</label><input id="editTripClient" maxlength="120"></div><div class="field"><label>Afwijkende route</label><input id="editTripRoute" maxlength="300" placeholder="Alleen invullen indien van toepassing"></div><div class="field"><label>Privé-omrijkilometers</label><input id="editTripDetour" type="number" min="0" step="0.1" inputmode="decimal"></div><div class="field"><label>Toelichting</label><input id="editTripNote" maxlength="250"></div><div class="tax-note">Een correctie wordt vastgelegd in het wijzigingslogboek.</div><button class="save" onclick="saveTripEdit()">Correctie opslaan</button>
</div></div>

<div class="modal" id="knownPlacesModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>📌 Bekende plekken</h2><button class="close" onclick="closeModal('knownPlacesModal')">✕</button></div>
  <div class="tax-note">Voorbeeld: <b>School</b> → aankomst Privé, vertrek naar onbekende bestemming → Zakelijk. Dan wordt Thuis → School als privé voorgesteld en School → Groningen als zakelijk. Met de automatische ritassistent wordt voor iedere bekende plek ook een <b>passieve Home Assistant-zone</b> bijgehouden, zodat je iPhone bij aankomst sneller een locatie-update kan sturen.</div>
  <div class="known-list" id="knownPlacesList"></div>
  <button class="location-big" type="button" onclick="newKnownPlaceAtCurrentLocation()">＋ Huidige locatie als bekende plek</button>
</div></div>

<div class="modal" id="knownPlaceEditModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2 id="knownEditTitle">📌 Bekende plek</h2><button class="close" onclick="closeModal('knownPlaceEditModal')">✕</button></div>
  <input id="knownId" type="hidden"><input id="knownLat" type="hidden"><input id="knownLon" type="hidden">
  <div class="field"><label>Naam</label><input id="knownName" maxlength="80" placeholder="Bijv. Thuis, School, Kantoor"></div>
  <div class="field"><label>Soort plek</label><select id="knownCategory"><option value="home">🏠 Thuis</option><option value="school">🏫 School</option><option value="work">🏢 Werk</option><option value="client">🤝 Klant</option><option value="family">👨‍👩‍👧 Familie</option><option value="private">❤️ Privé</option><option value="other">📍 Overig</option></select></div>
  <div class="field"><label>Als ik hier aankom, voorstel</label><select id="knownArrival"><option value="ask">❓ Altijd vragen</option><option value="private">🏠 Privé</option><option value="business">💼 Zakelijk</option></select></div>
  <div class="field"><label>Vanaf hier naar een onbekende plek</label><select id="knownDepart"><option value="ask">❓ Geen vaste suggestie</option><option value="private">🏠 Stel Privé voor</option><option value="business">💼 Stel Zakelijk voor</option></select></div>
  <div class="field"><label>Herkenningsradius</label><div class="place-radius"><input id="knownRadius" type="range" min="50" max="600" step="10" value="180" oninput="$('knownRadiusVal').textContent=this.value+' m'"><b id="knownRadiusVal">180 m</b></div></div>
  <div class="place-preview" id="knownCoord">Locatie nog niet vastgelegd</div>
  <button class="save" onclick="saveKnownPlace()">Bekende plek opslaan</button>
</div></div>

<div class="modal" id="stopPromptModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>Actieve rit opslaan?</h2><button class="close" onclick="closeModal('stopPromptModal')">✕</button></div><p id="stopPromptAddress"></p><p>Je lijkt stil te staan. Wil je de tellerstand controleren en je rit afsluiten?</p><button class="save" onclick="finishFromStopPrompt()">Ja, rit controleren en opslaan</button><button class="guide-next" onclick="closeModal('stopPromptModal')">Nee, ik rijd verder</button></div></div>
<div class="modal" id="assistantModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>📍 Je bent aangekomen</h2><button class="close" onclick="closeModal('assistantModal')">✕</button></div>
  <input id="assistantId" type="hidden"><input id="assistantOdo" type="hidden">
  <div class="assistant-route-big" id="assistantRoute">—</div>
  <div class="assistant-note" id="assistantReason"></div>
  <div class="odo-suggest show" id="arrivalProposal" aria-live="polite"><div class="odo-suggest-label">Voorgestelde tellerstand</div><div class="odo-suggest-value"><b id="arrivalOdoValue">—</b> <span>km</span></div><div class="odo-suggest-detail" id="arrivalOdoDetail"></div><button class="odo-suggest-edit" type="button" onclick="editArrivalProposal()">Aanpassen</button></div>
  <div class="field"><label>Ritsoort</label><div class="segment-choice"><button type="button" id="assistantBusiness" class="business" onclick="setAssistantType('business')">💼 Zakelijk</button><button type="button" id="assistantPrivate" class="private" onclick="setAssistantType('private')">🏠 Privé</button></div></div>
  <div class="odo-editor" id="arrivalEditor"><div class="field"><label>Kilometerstand bij vertrek</label><input id="assistantStartOdo" type="number" inputmode="numeric" min="0" step="1"><div style="font-size:10px;color:var(--muted);margin-top:5px">Wordt vooringevuld met de laatst bekende stand. Controleer deze vóór opslaan.</div></div>
  <div class="guide-section active" id="assistantStepOdo"><div class="guide-head"><span class="step-badge">1</span><b>Kilometerstand bij aankomst</b><small>Scroll de cijfers</small></div><div class="odo-wheelbox" id="assistantOdoWheels"></div><div class="odo-live"><b id="assistantOdoDisplay">—</b><span>km</span></div><div class="odo-last" id="assistantOdoLast"></div></div>
  <label class="assistant-note"><input type="checkbox" id="arrivalOdoChecked"> Ik heb de vertrek- én aankomststand op de echte teller gecontroleerd. Gebruik dit traject voor kilometerleren.</label></div>
  <label class="assistant-note" id="arrivalFinishWrap"><input type="checkbox" id="arrivalFinish"> Ook mijn actieve ritregistratie afsluiten</label>
  <button class="save" id="arrivalSave" onclick="saveAssistantArrival()">✓ Alles akkoord</button>
</div></div>

<div class="modal" id="settingsModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>⚙️ Instellingen</h2><button class="close" onclick="closeModal('settingsModal')">✕</button></div>
  <div class="field"><label>Naam auto</label><input id="setVehicle"></div><div class="field"><label>Brandstof</label><input id="setFuel"></div><div class="field"><label>Valuta</label><input id="setCurrency" maxlength="3"></div>
  <div class="field"><label>Bestuurder (voor ritten-PDF)</label><input id="setDriver" maxlength="80" placeholder="Naam bestuurder"></div>
  <div class="field"><label>Bedrijf (voor ritten-PDF)</label><input id="setCompany" maxlength="80" placeholder="Bedrijfsnaam"></div>
  <div class="field"><label>Merk auto</label><input id="setMake" maxlength="80" placeholder="Bijv. Volkswagen"></div>
  <div class="field"><label>Type / model</label><input id="setModel" maxlength="80" placeholder="Bijv. Golf Variant"></div>
  <div class="field"><label>Kenteken</label><input id="setPlate" maxlength="20" placeholder="Bijv. AB-12-CD"></div><div class="row2"><div class="field"><label>Auto beschikbaar vanaf</label><input id="setPeriodFrom" type="date"></div><div class="field"><label>Auto beschikbaar t/m</label><input id="setPeriodTo" type="date"></div></div>
  <div class="field"><label>Locatie-fallback (blijvend opgeslagen)</label><select id="setLocationEntity"><option value="">Geen — alleen GPS van browser</option></select><div style="font-size:11px;color:var(--muted);margin-top:5px">Als iOS/Ingress geen browser-GPS toestaat, gebruikt 📍 deze Home Assistant person/device_tracker.</div></div>
  <div class="assistant-settings">
    <h3>🤖 Automatische ritassistent</h3>
    <p>Herkent bekende plekken op de achtergrond, maakt passieve Home Assistant-zones en stuurt een melding met Privé/Zakelijk zodra een aankomst wordt gedetecteerd.</p>
    <div class="toggle"><div><b>Ritassistent actief</b><div style="font-size:10px;color:var(--muted)">Volgt de gekozen tracker ook als deze app niet open staat.</div></div><label class="switch"><input id="setAssistantEnabled" type="checkbox"><span class="slider"></span></label></div>
    <div class="field"><label>Autonomieniveau</label><select id="setAssistantMode"><option value="manual">Handmatig</option><option value="assistant">Assistent — altijd controleren</option><option value="autopilot">Autopilot — zekere routes classificeren</option></select></div>
    <div class="field"><label>Autopilot vanaf betrouwbaarheid (%)</label><input id="setAssistantConfidence" type="number" min="70" max="100" step="1"><div style="font-size:10px;color:var(--muted);margin-top:5px">Autopilot vult alleen de ritsoort in; kilometerstanden blijven zichtbaar in Te controleren.</div></div>
    <div class="toggle"><div><b>Persoonlijke kilometercorrectie</b><div class="assistant-note" id="calibrationStatus"></div><div class="assistant-note">Leert alleen van expliciet gecontroleerde tellerstanden. Minimaal 5 stabiele trajecten; maximaal 10% correctie. Uitzetten behoudt je historie.</div></div><label class="switch"><input id="setDistanceLearning" type="checkbox"><span class="slider"></span></label></div>
    <div class="field"><label>Persoon / iPhone voor achtergrondlocatie</label><select id="setAssistantLocation"><option value="">Kies tracker</option></select></div>
    <div class="field"><label>Mobiele meldingsservice</label><select id="setAssistantNotify"><option value="">Kies iPhone-melding</option></select></div>
    <div class="toggle"><div><b>Bekende plekken als HA-zones</b><div style="font-size:10px;color:var(--muted)">Passieve zones geven de Companion-app snellere geofence-updates.</div></div><label class="switch"><input id="setAssistantSyncZones" type="checkbox"><span class="slider"></span></label></div>
    <div class="toggle"><div><b>Onbekende stops herkennen</b><div style="font-size:10px;color:var(--muted)">Experimenteel: probeert ook een stop zoals Groningen te herkennen.</div></div><label class="switch"><input id="setAssistantUnknown" type="checkbox"><span class="slider"></span></label></div>
    <div class="row2"><div class="field"><label>Snelle stopmelding na (sec)</label><input id="setAssistantFastStop" type="number" min="20" max="180" step="5"><div style="font-size:10px;color:var(--muted);margin-top:5px">Groningen en Drenthe: ingestelde tijd. Overijssel: altijd 10 seconden.</div></div><div class="field"><label>Minimale ritafstand (m)</label><input id="setAssistantMinTrip" type="number" min="100" max="10000" step="100"></div></div>
    <div class="field"><label>Onbekende stop fallback (min)</label><input id="setAssistantStopMin" type="number" min="2" max="30" step="1"><div style="font-size:10px;color:var(--muted);margin-top:5px">Blijft gebruikt voor algemene ritassistent-herkenning; de snelle push is apart en provinciegebonden.</div></div>
    <div class="settings-actions"><button class="linkbtn" style="font:inherit" onclick="testAssistantNotification()">🔔 Test melding</button><button class="linkbtn" style="font:inherit" onclick="syncAssistantZones()">📍 Zones synchroniseren</button></div>
  </div>
  <div class="field" id="initialWrap"><label>Begin-kilometerstand</label><input id="setInitial" type="number" inputmode="decimal" step="0.1"><div style="font-size:11px;color:var(--muted);margin-top:5px">Alleen van toepassing als er nog geen registraties zijn.</div></div>
  <div class="pwa-card"><b>☁️ Versleutelde Google Drive-back-up</b><small id="backupStatusText">Configureer Google Drive in de add-onconfiguratie. Stel backup_retention_days in op 0 om back-ups onbeperkt te bewaren.</small><div class="settings-actions"><button class="pwa-install" type="button" onclick="runBackupNow()">Nu back-up maken</button></div></div>
  <div class="pwa-card"><b>📲 Zelfstandige iPhone-app</b><small id="pwaInstallText">Installeer Rit & Tank op je beginscherm voor een eigen icoon, fullscreen weergave en een offline beschikbare app-interface.</small><div class="settings-actions"><button class="pwa-install" id="pwaInstallButton" type="button" onclick="installPwa()">Zet op beginscherm</button><button class="linkbtn" id="standaloneLogoutButton" style="display:none;font:inherit" type="button" onclick="logoutStandalone()">Uitloggen</button></div></div>
  <div class="tech" id="techStatus"></div>
  <section class="assistant-panel show">
    <b>Diagnoselog voor ondersteuning</b>
    <p>Laatste 500 gebeurtenissen, maximaal 48 uur. Zonder adressen, exacte GPS-coördinaten of sleutels. Registratie begint na deze update.</p>
    <button type="button" class="btn" onclick="loadDiagnosticLog()">Log ophalen / vernieuwen</button>
    <p id="diagnosticStatus" role="status">Haal na je test de nieuwste log op.</p>
    <textarea id="diagnosticText" readonly rows="7" aria-label="Diagnoselog" style="width:100%;box-sizing:border-box" placeholder="Je diagnoselog verschijnt hier"></textarea>
    <div class="action-row">
      <button type="button" class="btn" onclick="copyDiagnosticLog()">Kopieer log</button>
      <button type="button" class="btn" onclick="downloadDiagnosticLog()">Download log</button>
    </div>
  </section>
  <button class="save" onclick="saveSettings()">Instellingen opslaan</button><div class="settings-actions"><a class="linkbtn" href="api/export.csv">⬇️ Tank/auto CSV</a><a class="linkbtn" id="settingsBusinessCsv" href="api/business.csv">🧾 Ritten CSV</a><a class="linkbtn" id="settingsBusinessPdf" onclick="openPdfSelector(event)" href="api/business.pdf?period=month">📄 Fiscale PDF</a><button class="linkbtn" style="font:inherit" onclick="reloadData()">↻ Vernieuwen</button></div>
</div></div>

<script>
let DATA=null, PERIOD='month', METRIC='km', VIEW='auto', FUEL_LOCATION=null, FUEL_PLACE=null, PLACE_RESULTS=[], TRIP_LOCATION=null, TRIP_MODE='start', TRIP_SEGMENT_TYPE='', TRIP_SUGGESTION=null, KNOWN_EDIT_LOCATION=null, ASSISTANT_ITEM=null, ASSISTANT_TYPE='';
let DEFERRED_INSTALL_PROMPT=null, LAUNCH_ACTION_DONE=false, PWA_WORKER_READY=false;
const $=id=>document.getElementById(id);
function api(path,opts={}){return fetch(path,{cache:'no-store',credentials:'same-origin',...opts}).then(async r=>{let d=await r.json().catch(()=>({error:'Onbekende fout'}));if(r.status===401){setTimeout(()=>location.reload(),150);throw new Error('Je sessie is verlopen. Log opnieuw in.')}if(!r.ok)throw new Error(d.error||'Fout');return d})}
function isStandalone(){return window.matchMedia('(display-mode: standalone)').matches||window.navigator.standalone===true}
function isIos(){return /iphone|ipad|ipod/i.test(navigator.userAgent)}
function updateOnlineState(){let banner=$('offlineBanner');if(banner)banner.classList.toggle('show',!navigator.onLine)}
function updatePwaInstallUi(){let button=$('pwaInstallButton'),text=$('pwaInstallText');if(!button||!text)return;if(isStandalone()){button.textContent='✓ App is geïnstalleerd';button.disabled=true;text.textContent='Rit & Tank draait als zelfstandige app vanaf je beginscherm.';return}button.disabled=false;button.textContent=DEFERRED_INSTALL_PROMPT?'App installeren':'Zet op beginscherm';text.textContent=isIos()?'Open deze pagina in Safari en kies Deel → Zet op beginscherm. De app opent daarna fullscreen met een eigen icoon.':'Installeer Rit & Tank voor een eigen icoon, appvenster en offline beschikbare interface.'}
async function installPwa(){if(isStandalone()){toast('Rit & Tank is al als app geopend');return}if(DEFERRED_INSTALL_PROMPT){DEFERRED_INSTALL_PROMPT.prompt();let choice=await DEFERRED_INSTALL_PROMPT.userChoice;DEFERRED_INSTALL_PROMPT=null;updatePwaInstallUi();toast(choice.outcome==='accepted'?'App wordt geïnstalleerd':'Installatie geannuleerd');return}if(isIos()){alert('Zo installeer je Rit & Tank op je iPhone:\n\n1. Open deze pagina in Safari.\n2. Tik op Deel (vierkant met pijl omhoog).\n3. Kies “Zet op beginscherm”.\n4. Tik op “Voeg toe”.\n\nOpen daarna het Rit & Tank-icoon vanaf je beginscherm.');return}alert('Open het browsermenu en kies “App installeren” of “Toevoegen aan startscherm”.')}
async function initPwa(){updateOnlineState();updatePwaInstallUi();window.addEventListener('online',()=>{updateOnlineState();toast('Verbinding hersteld');reloadData()});window.addEventListener('offline',()=>{updateOnlineState();toast('Je bent offline',true)});window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();DEFERRED_INSTALL_PROMPT=e;updatePwaInstallUi()});window.addEventListener('appinstalled',()=>{DEFERRED_INSTALL_PROMPT=null;updatePwaInstallUi();toast('Rit & Tank is geïnstalleerd')});if(!('serviceWorker' in navigator))return;let secure=location.protocol==='https:'||['localhost','127.0.0.1'].includes(location.hostname);if(!secure)return;try{await navigator.serviceWorker.register('service-worker.js');await navigator.serviceWorker.ready;PWA_WORKER_READY=true}catch(e){console.warn('PWA-serviceworker niet beschikbaar',e)}}
async function loadAuthStatus(){try{let r=await fetch('api/auth/status',{cache:'no-store',credentials:'same-origin'}).then(x=>x.json());let b=$('standaloneLogoutButton');if(b)b.style.display=r.mode==='standalone'&&r.authorized?'inline-flex':'none'}catch(e){}}
async function logoutStandalone(){if(!confirm('Uitloggen uit de zelfstandige Rit & Tank-app?'))return;try{await fetch('api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});location.reload()}catch(e){toast('Uitloggen mislukt',true)}}
function handleLaunchAction(){if(LAUNCH_ACTION_DONE||!DATA)return;LAUNCH_ACTION_DONE=true;let action=new URLSearchParams(location.search).get('open');if(action==='fuel')setTimeout(()=>openFuel(),100);if(action==='trip')setTimeout(()=>{switchView('business');openTripPoint('start')},100)}
function fmt(n,d=1){if(n===null||n===undefined||Number.isNaN(Number(n)))return '—';return Number(n).toLocaleString('nl-NL',{minimumFractionDigits:d,maximumFractionDigits:d})}
function money(n){let c=DATA?.settings?.currency||'€';return n===null||n===undefined?'—':`${c} ${fmt(n,2)}`}
function localInputNow(){let d=new Date();d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16)}
function toast(msg,error=false){let t=$('toast');t.textContent=msg;t.className='toast show'+(error?' error':'');setTimeout(()=>t.className='toast',3300)}
function closeModal(id){$(id).classList.remove('show')} function openModal(id){$(id).classList.add('show');let sh=$(id).querySelector('.sheet');if(sh)sh.scrollTop=0}
let GUIDE_TIMER=null;
function guideTo(id,delay=80){clearTimeout(GUIDE_TIMER);GUIDE_TIMER=setTimeout(()=>{let el=$(id);if(!el)return;let modal=el.closest('.modal');if(modal&&!modal.classList.contains('show'))return;if(modal)modal.querySelectorAll('.guide-section').forEach(x=>x.classList.toggle('active',x===el));let sheet=el.closest('.sheet');if(sheet){let top=el.getBoundingClientRect().top-sheet.getBoundingClientRect().top+sheet.scrollTop-24;sheet.scrollTo({top:Math.max(0,top),behavior:'smooth'})}else el.scrollIntoView({behavior:'smooth',block:'center'})},delay)}
async function reloadData(){try{DATA=await api(`api/summary?period=${PERIOD}`);await migrateLocationFallback();render();handleLaunchAction()}catch(e){toast(e.message,true)}}
function render(){let s=DATA.settings,p=DATA.period,f=DATA.period_full_tank;$('vehicleName').textContent=s.vehicle_name;$('fuelType').textContent=s.fuel_type;$('odometer').textContent=DATA.current_odometer==null?'—':fmt(DATA.current_odometer,0);$('heroConsumption').textContent=DATA.latest_full_cycle?.l100!=null?fmt(DATA.latest_full_cycle.l100,2):'—';$('sinceFull').textContent=DATA.since_full_km==null?'Nog geen volle-tank startpunt':`${fmt(DATA.since_full_km,0)} km sinds laatste volle tank`;$('periodLabel').textContent=p.label;$('fuelCount').textContent=`${p.fuel_count} tankbeurt${p.fuel_count===1?'':'en'}`;$('kpiKm').textContent=`${fmt(p.km,1)} km`;$('kpiLiters').textContent=`${fmt(p.liters,2)} L`;$('kpiL100').textContent=f?.l100==null?'—':fmt(f.l100,2);$('kpiCost').textContent=money(p.cost);$('kpiPrice').textContent=p.avg_price==null?'—':`${s.currency} ${fmt(p.avg_price,3)}`;$('kpiCost100').textContent=f?.cost100==null?'—':money(f.cost100);if(f){$('fullAvgVal').textContent=`${fmt(f.l100,2)} L/100 km`;$('fullAvgInfo').innerHTML=`${f.cycles} complete cyclus${f.cycles===1?'':'sen'}<br>${fmt(f.km,0)} km · ${fmt(f.liters,1)} L`}else{$('fullAvgVal').textContent='—';$('fullAvgInfo').textContent='Nog geen complete volle-tank cyclus in deze periode'};renderChart();renderStations();renderHistory();renderBusiness();$('eventCount').textContent=`${DATA.total_events} totaal`;document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.period===PERIOD));document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('active',b.dataset.metric===METRIC));switchView(VIEW,false)}
function renderChart(){let c=$('chart');c.innerHTML='';let vals=DATA.chart.map(x=>Number(x[METRIC]??0)||0),max=Math.max(...vals,1);DATA.chart.forEach(x=>{let w=document.createElement('div');w.className='bar-wrap';let value=Number(x[METRIC]??0)||0,pct=value<=0?1:Math.max(3,value/max*88);let label=METRIC==='cost'?`${DATA.settings.currency}${fmt(value,0)}`:METRIC==='liters'?`${fmt(value,1)}L`:METRIC==='l100'?`${fmt(value,1)}`:`${fmt(value,0)}`;w.innerHTML=`<div class="bar-val">${value?label:''}</div><div class="bar ${METRIC==='l100'?'orange':''}" style="height:${pct}%"></div><div class="bar-label">${x.label}</div>`;c.appendChild(w)})}
function renderStations(){let box=$('stationStats'),arr=DATA.station_stats||[];$('stationCount').textContent=arr.length?`${arr.length} locatie${arr.length===1?'':'s'}`:'';box.innerHTML='';if(!arr.length){box.innerHTML='<div class="empty">In deze periode zijn nog geen tanklocaties opgeslagen.</div>';return}arr.forEach(x=>{let r=document.createElement('div');r.className='station-row';let map=x.google_maps_uri?`<a class="maplink" target="_blank" rel="noopener" href="${escAttr(x.google_maps_uri)}">📍</a>`:'';r.innerHTML=`<div class="station-icon">⛽</div><div><strong>${esc(x.name)}</strong><small>${x.count}× getankt · ${fmt(x.liters,1)} L · ${money(x.cost)}<br>gem. ${x.avg_price==null?'—':DATA.settings.currency+' '+fmt(x.avg_price,3)}/L${x.address?'<br>'+esc(x.address):''}</small></div>${map}`;box.appendChild(r)})}
function renderHistory(){let h=$('history');h.innerHTML='';if(!DATA.recent.length){h.innerHTML='<div class="empty">Nog geen registraties. Voeg je eerste kilometerstand of tankbeurt toe.</div>';return}DATA.recent.forEach(e=>{let d=document.createElement('div');d.className='event';let fuel=e.type==='fuel',station=e.station_display||e.station||'',desc=fuel?`${fmt(e.liters,2)} L · ${DATA.settings.currency} ${fmt(e.price_per_liter,3)}/L${station?' · '+esc(station):''}`:`+${fmt(e.delta_km,1)} km`,map=e.google_maps_uri?`<a class="maplink" target="_blank" rel="noopener" href="${escAttr(e.google_maps_uri)}">📍</a>`:'',receipt=e.receipt_path?`<a class="receipt-link" target="_blank" href="api/receipt/${e.id}">📷</a>`:'';d.innerHTML=`<div class="event-icon">${fuel?'⛽':'🛣️'}</div><div><strong>${fuel?'Tankbeurt':'Kilometerstand'} · ${fmt(e.odometer,0)} km</strong><small>${e.date_label} ${e.time_label} · ${desc}${e.station_address?'<br>'+esc(e.station_address):''}${e.note?'<br>'+esc(e.note):''}</small></div><div class="right">${fuel?`<b>${money(e.cost)}</b>`:`<b>${fmt(e.delta_km,1)} km</b>`}<div class="event-actions">${map}${receipt}<button class="trash" onclick="removeEvent(${e.id})">🗑️</button></div></div>`;h.appendChild(d)})}
function switchView(view,remember=true){VIEW=view==='business'?'business':'auto';document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${VIEW}`));document.querySelectorAll('.view-tab').forEach(b=>b.classList.toggle('active',b.dataset.view===VIEW));if(remember)localStorage.setItem('rit_tank_view',VIEW)}
document.querySelectorAll('.view-tab').forEach(b=>b.onclick=()=>{switchView(b.dataset.view);window.scrollTo({top:0,behavior:'smooth'})});VIEW=localStorage.getItem('rit_tank_view')||'auto';
function smartTripAction(){if(!DATA)return;let active=DATA.business?.active_trip;if(active){switchView('business');$('bizHero').scrollIntoView({block:'start',behavior:'smooth'})}else openTripPoint('start')}
function renderBusiness(){let b=DATA.business||{},p=b.period||{},y=b.year||{},a=b.active_trip,primary=$('tripPrimaryLabel');if(primary)primary.textContent=a?'Actieve rit bekijken':'Rit starten';let csv=$('bizCsvLink'),pdf=$('bizPdfLink'),scsv=$('settingsBusinessCsv'),spdf=$('settingsBusinessPdf');if(csv)csv.href=`api/business.csv?period=${PERIOD}`;if(pdf)pdf.href=`api/business.pdf?period=${PERIOD}`;if(scsv)scsv.href=`api/business.csv?period=${PERIOD}`;if(spdf)spdf.href=`api/business.pdf?period=${PERIOD}`;$('bizPeriodLabel').textContent=DATA.period.label;$('bizTripCount').textContent=`${p.trips||0} rit${p.trips===1?'':'ten'}`;$('bizKm').textContent=`${fmt(p.business_km||0,1)} km`;$('bizPrivateKm').textContent=`${fmt(p.private_km||0,1)} km`;$('bizPrivateYear').textContent=`${fmt(y.private_km||0,1)} km`;$('bizTrips').textContent=String(p.trips||0);$('bizStops').textContent=String(p.stops||0);$('bizAvg').textContent=`${fmt(p.avg_km||0,1)} km`;let hero=$('bizHero'),actions=$('bizHeroActions');actions.innerHTML='';if(a){hero.classList.add('active-trip');$('bizHeroTitle').textContent=a.purpose||`${a.trip_type_label||'Rit'} actief`;let os=b.odometer_suggestion||{},track=os.active&&Number(os.tracked_km||0)>0?`<br>🛰️ Achtergrondroute sinds laatste stop: <b>${fmt(os.tracked_km,1)} km</b> · voorstel eindstand <b>${fmt(os.suggested_odometer,0)} km</b>`:'';$('bizHeroInfo').innerHTML=`${a.trip_type_label||'Rit'}${a.client?' · '+esc(a.client):''} · ${fmt(a.km||0,1)} km · ${a.stop_count||0} locatie${a.stop_count===1?'':'s'}<br>Laatste: ${esc(a.last_location||'—')}${track}`;actions.className='biz-actions';actions.innerHTML='<button class="biz-next" onclick="openTripPoint(\'stop\')">📍 Volgende adres</button><button class="biz-finish" onclick="openTripPoint(\'finish\')">🏁 Rit afsluiten</button>'}else{hero.classList.remove('active-trip');$('bizHeroTitle').textContent='Geen actieve rit';$('bizHeroInfo').textContent='Start een rit en leg vertrek, aankomst, kilometerstanden en ritsoort vast.';actions.className='';actions.innerHTML='<button class="biz-start" onclick="openTripPoint(\'start\')">＋ Nieuwe rit</button>'}renderAssistant();renderBusinessHistory(b.recent_trips||[]);renderAudit(b.audit||[])}
function renderAssistant(){let a=DATA?.business?.assistant||{},cfg=a.config||{},rt=a.runtime||{},pending=a.pending||[],panel=$('assistantPanel'),list=$('assistantList'),badge=$('assistantStatusBadge'),txt=$('assistantStatusText');panel.classList.add('show');badge.textContent=cfg.mode==='autopilot'?'AUTO':cfg.enabled?'AAN':'UIT';badge.className='assistant-status'+(cfg.enabled?'':' off');let parts=[];if(cfg.enabled){parts.push(cfg.mode==='autopilot'?'Autopilot classificeert zekere routes':cfg.mode==='manual'?'Handmatige modus':`Assistent volgt ${esc(cfg.location_entity||'nog geen tracker')}`);if(rt.current_place)parts.push(`nu bij ${esc(rt.current_place)}`);else if(rt.departed_from)parts.push(`vertrokken vanaf ${esc(rt.departed_from)}`);if(rt.draft_active)parts.push(`Concept onderweg · ${fmt(rt.draft_km,1)} GPS-km`);if(rt.last_error)parts.push(`⚠️ ${esc(rt.last_error)}`)}else parts.push('Zet hem aan via Instellingen');txt.innerHTML=parts.join(' · ');list.innerHTML='';if(!pending.length){list.innerHTML='<div class="empty" style="padding:6px 0">Alles is bijgewerkt — geen ritten om te controleren.</div>';return}pending.forEach(x=>{let d=document.createElement('div');d.className='assistant-item';let chosen=x.confirmed_type||x.suggested_type||'',pct=Math.round(Number(x.suggestion_confidence||0)*100),proposal=x.suggested_type?`Voorstel: ${esc(x.suggested_type_label)} · ${pct}% · ${esc(x.suggestion_reason||'')}`:'Geen zekere classificatie — kies zelf';d.innerHTML=`<div class="assistant-route">${esc(x.origin_name)} → ${esc(x.destination_name)}</div><div class="assistant-meta">${esc(x.date_label)} · ${proposal}${x.status==='confirmed'?`<br>✓ ${x.classification_source==='autopilot'?'Door Autopilot':'Via melding'} geclassificeerd als ${esc(x.confirmed_type_label)}`:''}</div><div class="assistant-actions"><button class="private ${chosen==='private'?'active':''}" onclick="confirmAssistant(${x.id},'private')">🏠 Privé</button><button class="business ${chosen==='business'?'active':''}" onclick="confirmAssistant(${x.id},'business')">💼 Zakelijk</button><button class="complete" onclick="openAssistantComplete(${x.id})">Controleren →</button><button class="dismiss" onclick="dismissAssistant(${x.id})">×</button></div>`;list.appendChild(d)})}
async function confirmAssistant(id,type){try{await api(`api/assistant/${id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trip_type:type})});toast(type==='business'?'Zakelijk bevestigd':'Privé bevestigd');reloadData()}catch(e){toast(e.message,true)}}
async function dismissAssistant(id){try{await api(`api/assistant/${id}`,{method:'DELETE'});toast('Suggestie gesloten');reloadData()}catch(e){toast(e.message,true)}}
function setAssistantType(type){ASSISTANT_TYPE=type;$('assistantBusiness').classList.toggle('active',type==='business');$('assistantPrivate').classList.toggle('active',type==='private')}
async function openAssistantComplete(id){
  try {
    let fresh=await api('api/assistant/arrivals'),x=(fresh.arrivals||[]).find(v=>Number(v.id)===Number(id));
    if(!x){toast('Dit voorstel is al verwerkt.');reloadData();return}
    ASSISTANT_ITEM=x;ASSISTANT_TYPE=x.confirmed_type||x.suggested_type||'';
    let p=x.proposal||{},active=DATA.business?.active_trip,last=active?.stops?.at(-1);
    let usable=p.suggested_odometer!=null&&p.route_complete&&(!last||Number(last.odometer)===Number(p.start_odometer));
    $('assistantId').value=x.id;$('assistantRoute').textContent=`${x.origin_name} → ${x.destination_name}`;
    $('assistantReason').textContent=`${x.date_label} · ${x.suggestion_reason||'Kies zelf de ritsoort.'}${x.suggested_type?' · Regelzekerheid '+Math.round(Number(x.suggestion_confidence||0)*100)+'%':''}`;
    $('assistantStartOdo').value=last?.odometer??p.start_odometer??'';
    $('assistantStartOdo').disabled=!!active;$('arrivalOdoChecked').checked=false;
    $('arrivalFinish').checked=false;$('arrivalFinishWrap').style.display=active?'block':'none';
    $('arrivalEditor').hidden=usable;
    $('arrivalProposal').classList.toggle('show',usable);
    $('arrivalOdoValue').textContent=usable?fmt(p.suggested_odometer,0):'—';
    $('arrivalOdoDetail').textContent=`+ ${fmt(p.gps_km,1)} GPS-km · vertrek ${fmt(p.start_odometer,0)} km${p.calibration?.ready?' · persoonlijke correctie toegepast':''}. Controleer de echte teller.`;
    $('arrivalSave').textContent=usable?'✓ Alles akkoord':'Gecontroleerde rit opslaan';
    if(!usable)$('assistantReason').textContent+=' · Geen volledig GPS-voorstel: controleer beide tellerstanden.';
    setAssistantType(ASSISTANT_TYPE);openModal('assistantModal');
    initOdometerWheel('assistant',usable?p.suggested_odometer:Number(last?.odometer??p.start_odometer??DATA.current_odometer??0));
  } catch(e){toast(e.message,true)}
}
function editArrivalProposal(){
  let value=$('assistantOdo').value;$('arrivalEditor').hidden=false;
  initOdometerWheel('assistant',value);
  $('arrivalProposal').classList.remove('show');$('arrivalSave').textContent='Gecontroleerde rit opslaan';
}
async function saveAssistantArrival(){
  if(!ASSISTANT_ITEM||$('arrivalSave').disabled)return;
  if(!ASSISTANT_TYPE){toast('Kies Privé of Zakelijk.',true);return}
  if($('assistantStartOdo').value===''){toast('Vul de vertrekstand in.',true);editArrivalProposal();return}
  let payload={trip_type:ASSISTANT_TYPE,start_odometer:$('assistantStartOdo').value,odometer:$('assistantOdo').value,
    odometer_checked:$('arrivalOdoChecked').checked,finish:$('arrivalFinish').checked};
  $('arrivalSave').disabled=true;
  try{await api(`api/assistant/${ASSISTANT_ITEM.id}/complete`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    closeModal('assistantModal');toast('Gecontroleerde aankomst opgeslagen');ASSISTANT_ITEM=null;await reloadData();
  }catch(e){toast(e.message,true)}finally{$('arrivalSave').disabled=false}
}
function renderBusinessHistory(arr){let box=$('businessHistory');box.innerHTML='';if(!arr.length){box.innerHTML='<div class="empty">Nog geen ritten in deze periode.</div>';return}arr.forEach(t=>{let d=document.createElement('div');d.className='trip-card';let stops=(t.stops||[]).map((x,i)=>{let icon=i===0?'●':(i===t.stops.length-1&&t.status==='completed'?'🏁':'•');let map=x.google_maps_uri?`<a target="_blank" rel="noopener" href="${escAttr(x.google_maps_uri)}">📍</a>`:'';return `<div class="trip-stop"><div class="dot">${icon}</div><div><b>${esc(x.location_address||x.location_label||'Adres nog niet beschikbaar')}</b><small>${x.date_label} ${x.time_label} · ${fmt(x.odometer,0)} km${i?` · +${fmt(x.segment_km,1)} km`:''}${i&&x.segment_trip_type_label?` <span class="leg-pill ${x.segment_trip_type}">${esc(x.segment_trip_type_label)}</span>`:''}${x.note?'<br>'+esc(x.note):''}</small></div>${map}</div>`}).join(''),type=t.trip_type||'business';d.innerHTML=`<div class="trip-top"><div><strong>${esc(t.purpose||t.trip_type_label||'Rit')}${t.client?' · '+esc(t.client):''}</strong><small>${t.started_label||''}${t.status==='active'?' · ACTIEF':''}</small><span class="trip-type-pill ${type}">${esc(t.trip_type_label||'Zakelijk')}</span></div><div class="trip-km">${fmt(t.km||0,1)} km</div></div>${t.deviating_route?`<div class="tax-note">Afwijkende route: ${esc(t.deviating_route)}${Number(t.private_detour_km||0)>0?` · Privé omrij: ${fmt(t.private_detour_km,1)} km`:''}</div>`:''}<div class="trip-route">${stops}</div><div class="trip-actions"><button class="editbtn" onclick="openTripEdit(${t.id})">✏️</button><button class="trash" onclick="removeBusinessTrip(${t.id})">🗑️</button></div>`;box.appendChild(d)})}
function renderAudit(arr){let box=$('auditHistory');if(!box)return;box.innerHTML='';if(!arr.length){box.innerHTML='<div class="empty">Nog geen wijzigingen.</div>';return}arr.forEach(a=>{let d=document.createElement('div');d.className='audit-row';d.innerHTML=`<b>${esc(a.label||a.action)} · ${esc(a.entity_type||'')}</b><small>${esc(a.date_label||'')} ${a.entity_id?`· #${a.entity_id}`:''}</small>`;box.appendChild(d)})}
async function removeBusinessTrip(id){if(!confirm('Deze rit en de gekoppelde kilometerpunten verwijderen? De verwijdering wordt in het logboek vastgelegd.'))return;try{await api(`api/business/${id}`,{method:'DELETE'});toast('Rit verwijderd');reloadData()}catch(e){toast(e.message,true)}}
function openTripEdit(id){let t=(DATA.business?.recent_trips||[]).find(x=>Number(x.id)===Number(id));if(!t){toast('Rit niet gevonden',true);return}$('editTripId').value=id;$('editTripType').value=t.trip_type||'business';$('editTripPurpose').value=t.purpose||'';$('editTripClient').value=t.client||'';$('editTripRoute').value=t.deviating_route||'';$('editTripDetour').value=Number(t.private_detour_km||0);$('editTripNote').value=t.note||'';openModal('tripEditModal')}
async function saveTripEdit(){let id=$('editTripId').value,payload={trip_type:$('editTripType').value,purpose:$('editTripPurpose').value,client:$('editTripClient').value,deviating_route:$('editTripRoute').value,private_detour_km:$('editTripDetour').value,note:$('editTripNote').value};try{await api(`api/business/${id}/edit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('tripEditModal');toast('Correctie opgeslagen en gelogd');reloadData()}catch(e){toast(e.message,true)}}
function setSegmentType(type){TRIP_SEGMENT_TYPE=type;['segmentBusiness','segmentPrivate'].forEach(id=>{let b=$(id);if(b)b.classList.toggle('active',(id==='segmentBusiness'&&type==='business')||(id==='segmentPrivate'&&type==='private'))})}
function showTripSuggestion(r){TRIP_SUGGESTION=r||null;let box=$('tripSuggestionBox');if(!box)return;if(TRIP_MODE==='start'){box.classList.remove('show');return}box.classList.add('show');let t=r?.suggested_type||'';let origin=r?.origin_place?.name||'vorige locatie',dest=r?.destination_place?.name||'deze locatie';$('tripSuggestionTitle').textContent=t?`${t==='business'?'💼 Zakelijk':'🏠 Privé'} voorgesteld`:'❓ Kies dit traject';$('tripSuggestionReason').textContent=r?.reason||`${origin} → ${dest}`;setSegmentType(t||'')}
function showTripOdoProposal(sug){let proposed=Number(sug?.suggested_odometer),tracked=Number(sug?.tracked_km||0);if(sug?.suggested_odometer==null||!sug?.active||!Number.isFinite(proposed)||tracked<=0)return false;initOdometerWheel('trip',proposed);$('tripOdoSuggestedValue').textContent=proposed.toLocaleString('nl-NL');let samples=Number(sug?.sample_count||0),sampleText=samples?` · ${samples} GPS-${samples===1?'meting':'metingen'}`:'';$('tripOdoSuggestedDetail').textContent=`+ ${tracked.toLocaleString('nl-NL',{minimumFractionDigits:1,maximumFractionDigits:1})} km sinds het vorige adres${sampleText}. ${sug.calibration?.ready?'Persoonlijke correctie toegepast. ':''}Controleer bij twijfel de teller.`;$('tripOdoSuggestion').classList.add('show');$('tripOdoEditor').hidden=true;$('tripOdoStepHint').textContent='Voorstel op basis van je gereden route';return true}
function acceptTripOdoSuggestion(){$('tripOdoSuggestion').classList.remove('show');$('tripOdoEditor').hidden=true;guideTo('tripStepLocation',80)}
function editTripOdoSuggestion(){$('tripOdoSuggestion').classList.remove('show');let value=$('tripOdo').value;$('tripOdoEditor').hidden=false;initOdometerWheel('trip',value);$('tripOdoStepHint').textContent='Scroll om de berekende stand te corrigeren';setTimeout(()=>$('tripOdoEditor').scrollIntoView({behavior:'smooth',block:'center'}),80)}
let TRIP_PROPOSAL_REQUEST=0;
async function refreshTripOdoProposal(request){
  let controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  let current=()=>request===TRIP_PROPOSAL_REQUEST&&$('tripModal').classList.contains('show');
  try{
    let sug=await api('api/business/odometer-suggestion',{signal:controller.signal});
    if(!current())return;
    if(!showTripOdoProposal(sug)){
      $('tripOdoEditor').hidden=false;initOdometerWheel('trip',$('tripOdo').value);
      $('tripOdoStepHint').textContent='Onvoldoende of onderbroken GPS-data — controleer de teller';
    }
  }catch(e){
    if(!current())return;
    $('tripOdoEditor').hidden=false;initOdometerWheel('trip',$('tripOdo').value);
    $('tripOdoStepHint').textContent='Voorstel niet beschikbaar — controleer de teller';
  }finally{clearTimeout(timer)}
}
function openTripPoint(mode){TRIP_MODE=mode;++TRIP_PROPOSAL_REQUEST;++TRIP_ADDRESS_REQUEST;TRIP_GPS=null;$('tripAddressChoices').innerHTML='';$('tripManualAddress').value='';$('tripOdoChecked').checked=false;TRIP_LOCATION=null;TRIP_SEGMENT_TYPE='';TRIP_SUGGESTION=null;let active=DATA.business?.active_trip;$('tripModalTitle').textContent=mode==='start'?'🚗 Ritregistratie starten':mode==='finish'?'🏁 Laatste locatie':'📍 Volgende locatie';$('tripStartFields').style.display=mode==='start'?'block':'none';$('tripFinishFields').style.display=mode==='finish'?'block':'none';$('tripSuggestionBox').classList.remove('show');$('tripDate').value=localInputNow();$('tripStopNote').value='';if(mode==='start'){$('tripPurpose').value='klantbezoek';$('tripClient').value='';$('tripTripNote').value=''};if(mode==='finish'){$('tripDeviatingRoute').value=active?.deviating_route||'';$('tripPrivateDetour').value=Number(active?.private_detour_km||0)};$('tripLocationTitle').textContent='📍 Nog geen locatie vastgelegd';$('tripLocationDetail').textContent='Tik hieronder zodra je op de juiste plek bent.';$('tripGoogleAttrib').style.display='none';$('tripSaveButton').textContent=mode==='start'?'Registratie starten':mode==='finish'?'Ritregistratie afsluiten':'Locatie opslaan';$('tripOdoSuggestion').classList.remove('show');$('tripOdoEditor').hidden=mode!=='start';$('tripOdoStepHint').textContent=mode==='start'?'Laatste stand is vooringesteld':'Berekende stand ophalen…';initOdometerWheel('trip',DATA.current_odometer??0);openModal('tripModal');setTimeout(()=>guideTo('tripStepOdo',0),80);if(mode!=='start')refreshTripOdoProposal(TRIP_PROPOSAL_REQUEST)}
let TRIP_ADDRESS_REQUEST=0,TRIP_ADDRESSES=[],TRIP_GPS=null;
async function captureTripLocation(){let request=++TRIP_ADDRESS_REQUEST,title=$('tripLocationTitle'),detail=$('tripLocationDetail');TRIP_LOCATION=null;TRIP_GPS=null;TRIP_ADDRESSES=[];$('tripManualAddress').value='';$('tripAddressChoices').innerHTML='';title.textContent='📍 Locatie bepalen…';detail.textContent='Adressen in de buurt ophalen.';try{
 let loc=await resolveLocation();if(request!==TRIP_ADDRESS_REQUEST)return;TRIP_GPS=loc;
 let r=await api('api/location/addresses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(loc)});if(request!==TRIP_ADDRESS_REQUEST)return;TRIP_ADDRESSES=r.addresses||[];
 title.textContent='Kies het huisnummer van je afspraak';detail.textContent=TRIP_ADDRESSES.length?`${r.street} · ${TRIP_ADDRESSES.length} adressen · bron PDOK / BAG. Controleer straat en huisnummer.`:'Geen adressen gevonden. Vul straat, huisnummer en plaats handmatig in.';
 $('tripGoogleAttrib').style.display='none';TRIP_ADDRESSES.forEach((a,i)=>{let b=document.createElement('button');b.type='button';b.className='guide-next';b.textContent=`${a.address} · ${a.distance_m} m`;b.onclick=()=>chooseTripAddress(i);$('tripAddressChoices').appendChild(b)});
 }catch(e){if(request!==TRIP_ADDRESS_REQUEST)return;title.textContent=TRIP_GPS?'Adres handmatig bevestigen':'⚠️ Locatie niet vastgelegd';detail.textContent=e.message;toast(e.message,true)}}
function chooseTripAddress(index){let address=TRIP_ADDRESSES[index];if(!address||!TRIP_GPS)return;$('tripManualAddress').value=address.address;confirmTripAddress(address.address,{...TRIP_GPS,latitude:address.latitude,longitude:address.longitude,source:'pdok_confirmed'})}
function confirmManualTripAddress(){let label=$('tripManualAddress').value.trim();if(!TRIP_GPS){toast('Bepaal eerst je huidige locatie.',true);return}if(!label){toast('Vul straat, huisnummer en plaats in.',true);return}confirmTripAddress(label,{...TRIP_GPS,source:'manual_address'})}
function advanceTripAfterLocation(){if(!TRIP_LOCATION)return;let target=TRIP_MODE==='start'||TRIP_SEGMENT_TYPE?'tripSaveButton':'tripSuggestionBox';guideTo(target,80)}
async function confirmTripAddress(label,location){TRIP_LOCATION={...location,place_id:'',address:label,manual_label:label};let selected=TRIP_LOCATION;$('tripLocationTitle').textContent='✓ Adres bevestigd';$('tripLocationDetail').textContent=label;for(let b of $('tripAddressChoices').children)b.classList.toggle('selected',b.textContent.startsWith(label+' ·'));if(TRIP_MODE!=='start')showTripSuggestion({reason:'Kies Zakelijk of Privé voor dit traject.'});advanceTripAfterLocation();if(TRIP_MODE!=='start'){try{let sug=await api('api/business/suggest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(location)});if(TRIP_LOCATION===selected&&!TRIP_SEGMENT_TYPE)showTripSuggestion(sug)}catch(e){if(TRIP_LOCATION===selected&&!TRIP_SEGMENT_TYPE)showTripSuggestion({reason:'Kies zelf of dit traject zakelijk of privé was.'})}}}
$('tripManualAddress').addEventListener('input',()=>{TRIP_LOCATION=null;$('tripLocationTitle').textContent='Bevestig het aangepaste adres'});
async function saveTripPoint(){if(!TRIP_LOCATION){toast('Leg eerst de huidige locatie vast met 📍.',true);return}if(TRIP_MODE!=='start'&&!TRIP_SEGMENT_TYPE){toast('Kies Zakelijk of Privé voor dit traject.',true);return}let path=TRIP_MODE==='start'?'api/business/start':TRIP_MODE==='finish'?'api/business/finish':'api/business/stop';let payload={odometer_checked:$('tripOdoChecked').checked,odometer:$('tripOdo').value,created_at:$('tripDate').value,latitude:TRIP_LOCATION.latitude,longitude:TRIP_LOCATION.longitude,location_accuracy:TRIP_LOCATION.accuracy??null,location_source:TRIP_LOCATION.source||'',place_id:TRIP_LOCATION.place_id||'',manual_label:TRIP_LOCATION.manual_label||'',note:$('tripStopNote').value,segment_trip_type:TRIP_SEGMENT_TYPE};if(TRIP_MODE==='start'){payload.purpose=$('tripPurpose').value;payload.client=$('tripClient').value;payload.trip_note=$('tripTripNote').value}if(TRIP_MODE==='finish'){payload.deviating_route=$('tripDeviatingRoute').value;payload.private_detour_km=$('tripPrivateDetour').value}try{await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('tripModal');toast(TRIP_MODE==='start'?'Ritregistratie gestart':TRIP_MODE==='finish'?'Ritregistratie afgesloten':`${TRIP_SEGMENT_TYPE==='business'?'Zakelijke':'Privé'} etappe opgeslagen`);switchView('business');reloadData()}catch(e){toast(e.message,true)}}
function esc(s){let d=document.createElement('div');d.textContent=s||'';return d.innerHTML} function escAttr(s){return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')}
async function removeEvent(id){if(!confirm('Deze registratie verwijderen?'))return;try{await api(`api/events/${id}`,{method:'DELETE'});toast('Registratie verwijderd');reloadData()}catch(e){toast(e.message,true)}}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{PERIOD=b.dataset.period;reloadData()});document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{METRIC=b.dataset.metric;renderChart();document.querySelectorAll('.seg button').forEach(x=>x.classList.toggle('active',x===b))});document.querySelectorAll('.modal').forEach(m=>m.addEventListener('click',e=>{if(e.target===m)closeModal(m.id)}));
let PDF_EXPORT=null,PDF_REQUEST=0;
function openPdfSelector(event){event?.preventDefault();let now=new Date();$('pdfRange').value='month';$('pdfMonthLabel').hidden=false;$('pdfYear').value=now.getFullYear();$('pdfYears').innerHTML=Array.from({length:12},(_,i)=>`<option value="${now.getFullYear()-5+i}"></option>`).join('');$('pdfMonth').innerHTML=['Januari','Februari','Maart','April','Mei','Juni','Juli','Augustus','September','Oktober','November','December'].map((m,i)=>`<option value="${i+1}">${m}</option>`).join('');$('pdfMonth').value=now.getMonth()+1;$('pdfPeriodError').textContent='';openModal('pdfPeriodModal')}
function confirmPdfPeriod(){let year=Number($('pdfYear').value),month=Number($('pdfMonth').value),period=$('pdfRange').value;if(!Number.isInteger(year)||year<1900||year>9998){$('pdfPeriodError').textContent='Kies een geldig jaar.';return}let url=`api/business.pdf?period=${period}&year=${year}`+(period==='month'?`&month=${month}`:'');closeModal('pdfPeriodModal');openPdfExport(url)}
function closePdfPreview(){++PDF_REQUEST;closeModal('pdfModal');document.body.classList.remove('pdf-preview-open','printing-pdf');$('pdfPages').innerHTML='';if(PDF_EXPORT)URL.revokeObjectURL(PDF_EXPORT.url);PDF_EXPORT=null}
function printPdfPreview(){if(!PDF_EXPORT)return;document.body.classList.add('printing-pdf');try{window.print()}catch(e){document.body.classList.remove('printing-pdf');$('pdfStatus').textContent='Afdrukken niet beschikbaar. Gebruik Delen om de PDF naar een print-app te sturen.'}}
window.addEventListener('afterprint',()=>document.body.classList.remove('printing-pdf'));
async function openPdfExport(source){let url;if(typeof source==='string')url=source;else{source.preventDefault();url=source.currentTarget.href}let request=++PDF_REQUEST;if(PDF_EXPORT)URL.revokeObjectURL(PDF_EXPORT.url);PDF_EXPORT=null;openModal('pdfModal');document.body.classList.add('pdf-preview-open');$('pdfStatus').textContent='PDF voorbereiden…';$('pdfPages').innerHTML='';$('pdfShare').disabled=true;$('pdfPrint').disabled=true;$('pdfDrive').disabled=true;$('pdfDownload').hidden=true;let controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);try{let previewUrl=url.replace('api/business.pdf','api/business/pdf-preview'),response=await fetch(previewUrl,{cache:'no-store',signal:controller.signal}),report=await response.json();if(!response.ok)throw new Error(report.error||'PDF maken mislukt. Log zo nodig opnieuw in.');if(request!==PDF_REQUEST)return;if(!Array.isArray(report.pages)||!report.pdf_base64)throw new Error('Geen rapport ontvangen.');let bytes=Uint8Array.from(atob(report.pdf_base64),c=>c.charCodeAt(0)),file=new File([bytes],report.filename,{type:'application/pdf'}),objectUrl=URL.createObjectURL(file);PDF_EXPORT={file,url:objectUrl};$('pdfPages').innerHTML=report.pages.map(svg=>'<div class="pdf-page">'+svg+'</div>').join('');$('pdfDownload').href=objectUrl;$('pdfDownload').download=report.filename;$('pdfDownload').hidden=false;$('pdfPrint').disabled=false;try{$('pdfShare').disabled=!(navigator.canShare&&navigator.canShare({files:[file]}))}catch(e){$('pdfShare').disabled=true}$('pdfDrive').disabled=!DATA.app?.backup?.configured;$('pdfStatus').textContent=report.filename+' · '+report.pages.length+' pagina’s'+($('pdfShare').disabled?' · Bestandsdeling is hier niet beschikbaar; afdrukken en downloaden wel.':'')}catch(e){if(request===PDF_REQUEST)$('pdfStatus').textContent=e.name==='AbortError'?'PDF maken duurt te lang. Probeer één maand of controleer het logboek.':e.message}finally{clearTimeout(timer)}}
async function sharePdf(){if(!PDF_EXPORT)return;try{await navigator.share({files:[PDF_EXPORT.file],title:'Rittenregistratie'})}catch(e){if(e.name!=='AbortError')toast('Delen niet beschikbaar. Gebruik Openen of Downloaden.',true)}}
async function archivePdf(){if(!PDF_EXPORT)return;let exported=PDF_EXPORT;$('pdfDrive').disabled=true;try{let encoded=await new Promise((resolve,reject)=>{let reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('PDF kon niet worden gelezen.'));reader.readAsDataURL(exported.file)});let result=await api('api/backup/pdf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pdf_base64:encoded,filename:exported.file.name})});if(PDF_EXPORT===exported)$('pdfStatus').textContent='Permanent in Google Drive bewaard: '+result.name}catch(e){toast(e.message,true)}finally{if(PDF_EXPORT===exported)$('pdfDrive').disabled=false}}
let wheels={};
function createWheel(id,values,initial,onchange){
 let el=$(id),previous=wheels[id];if(previous?.timer)clearTimeout(previous.timer);el.onscroll=null;el.innerHTML='';
 values.forEach((v,i)=>{let x=document.createElement('div');x.className='wheel-item';x.textContent=v;x.dataset.idx=i;el.appendChild(x)});
 let w={el,values,index:Math.max(0,values.indexOf(initial)),timer:null};wheels[id]=w;
 w.select=(idx,fromScroll=false)=>{if(wheels[id]!==w)return;let previousIndex=w.index;w.index=Math.max(0,Math.min(values.length-1,idx));[...el.children].forEach((x,i)=>x.classList.toggle('sel',i===w.index));onchange?.(fromScroll&&w.index!==previousIndex)};
 requestAnimationFrame(()=>{if(wheels[id]!==w)return;el.scrollTop=w.index*50;w.select(w.index);el.onscroll=()=>{clearTimeout(w.timer);w.timer=setTimeout(()=>{if(wheels[id]===w)w.select(Math.round(el.scrollTop/50),true)},70)}});return w
}
function wheelVal(id){return wheels[id]?.values[wheels[id].index]}
function initOdometerWheel(prefix,initial){let n=Math.max(0,Math.min(999999,Math.round(Number(initial)||0))),digits=String(n).padStart(6,'0').split('').map(Number),box=$(prefix+'OdoWheels');box.innerHTML='';for(let i=0;i<6;i++){let w=document.createElement('div');w.className='wheel odo-wheel';w.id=prefix+'OdoD'+i;box.appendChild(w);createWheel(w.id,Array.from({length:10},(_,x)=>x),digits[i],()=>updateOdometerWheel(prefix))}updateOdometerWheel(prefix);let last=DATA?.current_odometer;let lastEl=$(prefix+'OdoLast');if(lastEl)lastEl.textContent=last==null?'Nog geen vorige kilometerstand':`Laatste geregistreerde stand: ${fmt(last,0)} km`}
function updateOdometerWheel(prefix){let raw='';for(let i=0;i<6;i++)raw+=String(wheelVal(prefix+'OdoD'+i)??0);let value=Number(raw),hidden=$(prefix+'Odo'),display=$(prefix+'OdoDisplay');if(hidden)hidden.value=value;if(display)display.textContent=value.toLocaleString('nl-NL')}

let LAST_FUEL_PRICE=null;
function preferredFuelPrice(){if(LAST_FUEL_PRICE!==null)return LAST_FUEL_PRICE;try{let raw=localStorage.getItem('rit_tank_last_fuel_price'),value=Number(raw);if(raw!==null&&Number.isFinite(value)&&value>0&&value<=5.999)return value}catch(e){}return DATA.latest_fuel?.price_per_liter??1.899}
function rememberFuelPrice(){let value=fuelValues().price;if(!Number.isFinite(value)||value<=0)return;LAST_FUEL_PRICE=value;try{localStorage.setItem('rit_tank_last_fuel_price',String(value))}catch(e){}}
function initFuelWheels(liters=null,price=null){let last=DATA.latest_fuel||{},L=Math.max(0,Math.min(25000,Math.round(Number(liters??last.liters??40)*100))),P=Math.max(0,Math.min(5999,Math.round(Number(price??preferredFuelPrice())*1000))),upd=(changed=false)=>{updateFuelTotal();if(changed)rememberFuelPrice()};createWheel('literWhole',Array.from({length:251},(_,i)=>i),Math.floor(L/100),upd);createWheel('literDec',Array.from({length:10},(_,i)=>i),Math.floor(L/10)%10,upd);createWheel('literDec2',Array.from({length:10},(_,i)=>i),L%10,upd);createWheel('priceWhole',Array.from({length:6},(_,i)=>i),Math.floor(P/1000),upd);createWheel('priceD1',Array.from({length:10},(_,i)=>i),Math.floor(P/100)%10,upd);createWheel('priceD2',Array.from({length:10},(_,i)=>i),Math.floor(P/10)%10,upd);createWheel('priceD3',Array.from({length:10},(_,i)=>i),P%10,upd);updateFuelTotal()}
function fuelValues(){let liters=Number(wheelVal('literWhole')||0)+Number(wheelVal('literDec')||0)/10+Number(wheelVal('literDec2')||0)/100,price=Number(wheelVal('priceWhole')||0)+Number(wheelVal('priceD1')||0)/10+Number(wheelVal('priceD2')||0)/100+Number(wheelVal('priceD3')||0)/1000;return{liters:Number(liters.toFixed(2)),price:Number(price.toFixed(3))}}
function updateFuelTotal(){if(!DATA)return;let v=fuelValues();$('fuelTotal').textContent=`${DATA.settings.currency} ${fmt(v.liters*v.price,2)}`}
function openFuel(){++RECEIPT_REQUEST;RECEIPT_SCANNING=false;$('fuelSaveButton').disabled=false;FUEL_LOCATION=null;FUEL_PLACE=null;PLACE_RESULTS=[];$('fuelDate').value=localInputNow();$('fuelNote').value='';$('fuelReceipt').value='';$('receiptScanStatus').textContent='';$('fuelFull').checked=true;$('fuelStation').value=DATA.latest_fuel?.station||'';$('stations').innerHTML=(DATA.recent_stations||[]).map(s=>`<option value="${escAttr(s)}">`).join('');$('stationResults').innerHTML='';$('googleAttrib').style.display='none';setLocationStatus('Tik op 📍 om tankstations in de buurt te zoeken.');initOdometerWheel('fuel',DATA.current_odometer??0);initFuelWheels();openModal('fuelModal');setTimeout(()=>guideTo('fuelStepScan',0),80)}
$('fuelStation').addEventListener('input',()=>{if(FUEL_PLACE){FUEL_PLACE=null;PLACE_RESULTS=[];$('stationResults').innerHTML='';$('googleAttrib').style.display='none';setLocationStatus(FUEL_LOCATION?'GPS-locatie blijft opgeslagen; tankstation wordt handmatig ingevoerd.':'Tankstation wordt handmatig ingevoerd.','ok')}});
function setLocationStatus(msg,kind=''){$('locationStatus').textContent=msg;$('locationStatus').className='location-status '+kind}
function browserLocation(){return new Promise((resolve,reject)=>{if(!navigator.geolocation)return reject(new Error('Browser-GPS wordt hier niet ondersteund.'));navigator.geolocation.getCurrentPosition(p=>resolve({latitude:p.coords.latitude,longitude:p.coords.longitude,accuracy:p.coords.accuracy,source:'browser'}),e=>reject(new Error(e.message||'Locatie niet beschikbaar.')),{enableHighAccuracy:true,timeout:9000,maximumAge:30000})})}
function savedLocationFallback(){if(DATA?.settings&&Object.prototype.hasOwnProperty.call(DATA.settings,'location_fallback_entity'))return DATA.settings.location_fallback_entity||'';try{return localStorage.getItem('rit_tank_location_entity')||''}catch(e){return ''}}
async function migrateLocationFallback(){if(Object.prototype.hasOwnProperty.call(DATA.settings,'location_fallback_entity'))return;let value=savedLocationFallback();if(!value)return;try{await api('api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location_fallback_entity:value})});DATA.settings.location_fallback_entity=value}catch(e){/* Retry on next load; preserve local selection. */}}
async function resolveLocation(){try{return await browserLocation()}catch(first){let entity=savedLocationFallback();if(!entity)throw new Error('GPS kon niet worden gebruikt. Kies in ⚙️ een Home Assistant locatie-fallback.');let d=await api(`api/location/entity?entity_id=${encodeURIComponent(entity)}`);return d}}
async function findStations(){setLocationStatus('📍 Huidige locatie bepalen...');$('stationResults').innerHTML='';$('googleAttrib').style.display='none';try{let loc=await resolveLocation();FUEL_LOCATION=loc;setLocationStatus(`Locatie gevonden${loc.accuracy?` · ±${Math.round(loc.accuracy)} m`:''}. Tankstations zoeken...`,'ok');let r=await api('api/places/nearby',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:loc.latitude,longitude:loc.longitude})});PLACE_RESULTS=r.places||[];renderPlaceChoices();setLocationStatus(`${PLACE_RESULTS.length} tankstation${PLACE_RESULTS.length===1?'':'s'} gevonden binnen ${r.radius_m} m.`,'ok')}catch(e){setLocationStatus(e.message,'err');toast(e.message,true)}}
function renderPlaceChoices(){let box=$('stationResults');box.innerHTML='';PLACE_RESULTS.forEach((p,i)=>{let b=document.createElement('button');b.type='button';b.className='station-choice';b.onclick=()=>selectPlace(i);let dist=p.distance_m==null?'':` · ${p.distance_m<1000?p.distance_m+' m':fmt(p.distance_m/1000,1)+' km'}`;b.innerHTML=`<b>${esc(p.name)}${dist}</b><small>${esc(p.address||'')}</small>`;box.appendChild(b)});$('googleAttrib').style.display=PLACE_RESULTS.length?'block':'none'}
function selectPlace(i){let p=PLACE_RESULTS[i];if(!p)return;FUEL_PLACE=p;$('fuelStation').value=p.name;$('stationResults').innerHTML='';$('googleAttrib').style.display='block';setLocationStatus(`✓ ${p.name} geselecteerd`,'ok');guideTo('fuelStepFinish',350)}
function readReceiptFile(){return new Promise((resolve,reject)=>{let f=$('fuelReceipt')?.files?.[0];if(!f)return resolve('');if(f.size>7*1024*1024)return reject(new Error('Tankbon is groter dan 7 MB.'));let r=new FileReader();r.onload=()=>resolve(String(r.result||''));r.onerror=()=>reject(new Error('Tankbon kon niet worden gelezen.'));r.readAsDataURL(f)})}
function receiptScanImage(){return new Promise((resolve,reject)=>{let file=$('fuelReceipt')?.files?.[0];if(!file)return resolve('');let url=URL.createObjectURL(file),img=new Image();img.onload=()=>{try{let scale=Math.min(1,1800/Math.max(img.naturalWidth,img.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.naturalWidth*scale));canvas.height=Math.max(1,Math.round(img.naturalHeight*scale));let ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(img,0,0,canvas.width,canvas.height);URL.revokeObjectURL(url);resolve(canvas.toDataURL('image/jpeg',.88))}catch(e){URL.revokeObjectURL(url);reject(e)}};img.onerror=()=>{URL.revokeObjectURL(url);reject(new Error('Foto kon niet worden voorbereid.'))};img.src=url})}
let RECEIPT_REQUEST=0,RECEIPT_SCANNING=false;
async function scanSelectedReceipt(){let status=$('receiptScanStatus'),file=$('fuelReceipt')?.files?.[0];if(!file)return;let request=++RECEIPT_REQUEST;RECEIPT_SCANNING=true;$('fuelSaveButton').disabled=true;status.textContent='Bon lokaal analyseren…';status.style.color='var(--teal)';try{
 let image=await receiptScanImage(),r=await api('api/receipt/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_data_url:image})});if(request!==RECEIPT_REQUEST)return;let x=r.receipt||{},current=fuelValues(),found=[];
 if(x.liters!=null||x.price_per_liter!=null)initFuelWheels(x.liters??current.liters,x.price_per_liter??current.price);
 if(x.price_per_liter!=null)rememberFuelPrice();
 if(x.station){FUEL_PLACE=null;$('fuelStation').value=x.station}
 if(x.date){let value=$('fuelDate').value,time=value.includes('T')?value.split('T')[1]:'12:00';$('fuelDate').value=`${x.date}T${time}`}
 if(x.liters!=null)found.push(`${fmt(x.liters,2)} L`);if(x.price_per_liter!=null)found.push(`${DATA.settings.currency} ${fmt(x.price_per_liter,3)}/L`);if(x.total!=null)found.push(`bonbedrag ${money(x.total)}`);
 let missing=[];if(x.liters==null)missing.push('liters');if(x.price_per_liter==null)missing.push('literprijs');
 status.textContent=(found.length?'Ingevuld: '+found.join(' · ')+'. ':'')+(missing.length?'Niet herkend: '+missing.join(' en ')+'. Vul deze zelf in.':'Controleer de waarden vóór opslaan.');status.style.color=missing.length?'var(--orange)':'var(--teal)';toast(missing.length?'Controleer de bon: niet alle waarden zijn herkend.':'Liters en literprijs ingevuld');
 }catch(e){if(request===RECEIPT_REQUEST){status.textContent=e.message+' Je kunt alles handmatig invullen.';status.style.color='var(--orange)'}}finally{if(request===RECEIPT_REQUEST){RECEIPT_SCANNING=false;$('fuelSaveButton').disabled=false}}}
$('fuelReceipt').addEventListener('change',scanSelectedReceipt);
async function saveFuel(){if(RECEIPT_SCANNING){toast('Wacht tot de bon is gescand.',true);return}let v=fuelValues(),payload={odometer:$('fuelOdo').value,created_at:$('fuelDate').value,liters:v.liters,price_per_liter:v.price,station:FUEL_PLACE?'':$('fuelStation').value,place_id:FUEL_PLACE?.place_id||'',latitude:FUEL_LOCATION?.latitude??null,longitude:FUEL_LOCATION?.longitude??null,location_accuracy:FUEL_LOCATION?.accuracy??null,location_source:FUEL_LOCATION?.source||'',full_tank:$('fuelFull').checked,note:$('fuelNote').value};try{payload.receipt_data_url=await readReceiptFile();let r=await api('api/fuel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('fuelModal');toast(`Tankbeurt opgeslagen · ${money(r.cost)}${r.receipt?' · bon bewaard':''}`);reloadData()}catch(e){toast(e.message,true)}}
function openKm(){$('kmDate').value=localInputNow();$('kmNote').value='';initOdometerWheel('km',DATA.current_odometer??0);openModal('kmModal');setTimeout(()=>guideTo('kmStepOdo',0),80)}
async function saveKm(){let payload={odometer:$('kmOdo').value,created_at:$('kmDate').value,note:$('kmNote').value};try{await api('api/odometer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('kmModal');toast('Kilometerstand opgeslagen');reloadData()}catch(e){toast(e.message,true)}}
function placeIcon(cat){return {home:'🏠',school:'🏫',work:'🏢',client:'🤝',family:'👨‍👩‍👧',private:'❤️',other:'📍'}[cat]||'📍'}
function placeRuleLabel(v){return v==='business'?'Zakelijk':v==='private'?'Privé':'vragen'}
async function openKnownPlaces(){renderKnownPlaces();openModal('knownPlacesModal')}
function renderKnownPlaces(){let arr=DATA?.business?.known_places||[],box=$('knownPlacesList');box.innerHTML='';if(!arr.length){box.innerHTML='<div class="empty">Nog geen bekende plekken. Voeg bijvoorbeeld Thuis en School toe.</div>';return}arr.forEach(p=>{let r=document.createElement('div');r.className='known-row';let zone=p.ha_zone_error?`<span class="assistant-zone-err"> · HA-zone fout</span>`:p.ha_zone_id?`<span class="assistant-zone-ok"> · HA-zone ✓</span>`:'';r.innerHTML=`<div class="known-icon">${placeIcon(p.category)}</div><div><b>${esc(p.name)}</b><small>Bij aankomst: ${placeRuleLabel(p.arrival_trip_type)} · vanaf hier naar onbekend: ${placeRuleLabel(p.unknown_departure_trip_type)} · ${p.radius_m} m${zone}</small></div><div class="known-actions"><button onclick="editKnownPlace(${p.id})">✏️</button><button onclick="removeKnownPlace(${p.id})">🗑️</button></div>`;box.appendChild(r)})}
async function newKnownPlaceAtCurrentLocation(){try{toast('Huidige locatie bepalen...');let loc=await resolveLocation();closeModal('knownPlacesModal');$('knownId').value='';$('knownName').value='';$('knownCategory').value='other';$('knownArrival').value='ask';$('knownDepart').value='ask';$('knownRadius').value=180;$('knownRadiusVal').textContent='180 m';$('knownLat').value=loc.latitude;$('knownLon').value=loc.longitude;$('knownCoord').textContent=`📍 ${fmt(loc.latitude,5)}, ${fmt(loc.longitude,5)}${loc.accuracy?` · ±${Math.round(loc.accuracy)} m`:''}`;$('knownEditTitle').textContent='📌 Nieuwe bekende plek';openModal('knownPlaceEditModal')}catch(e){toast(e.message,true)}}
function editKnownPlace(id){let p=(DATA?.business?.known_places||[]).find(x=>Number(x.id)===Number(id));if(!p)return;$('knownId').value=p.id;$('knownName').value=p.name||'';$('knownCategory').value=p.category||'other';$('knownArrival').value=p.arrival_trip_type||'ask';$('knownDepart').value=p.unknown_departure_trip_type||'ask';$('knownRadius').value=p.radius_m||180;$('knownRadiusVal').textContent=(p.radius_m||180)+' m';$('knownLat').value=p.latitude;$('knownLon').value=p.longitude;$('knownCoord').textContent=`📍 ${fmt(p.latitude,5)}, ${fmt(p.longitude,5)}`;$('knownEditTitle').textContent='📌 '+p.name;closeModal('knownPlacesModal');openModal('knownPlaceEditModal')}
async function saveKnownPlace(){let id=$('knownId').value,payload={name:$('knownName').value,category:$('knownCategory').value,arrival_trip_type:$('knownArrival').value,unknown_departure_trip_type:$('knownDepart').value,radius_m:$('knownRadius').value,latitude:$('knownLat').value,longitude:$('knownLon').value};try{await api(id?`api/known-places/${id}`:'api/known-places',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('knownPlaceEditModal');toast('Bekende plek opgeslagen');await reloadData();openKnownPlaces()}catch(e){toast(e.message,true)}}
async function removeKnownPlace(id){if(!confirm('Deze bekende plek verwijderen? Bestaande ritten blijven bewaard.'))return;try{await api(`api/known-places/${id}`,{method:'DELETE'});toast('Bekende plek verwijderd');await reloadData();renderKnownPlaces()}catch(e){toast(e.message,true)}}

async function loadLocationEntities(){let sel=$('setLocationEntity'),asel=$('setAssistantLocation'),saved=savedLocationFallback(),asaved=DATA?.settings?.assistant_location_entity||'';sel.innerHTML='<option value="">Geen — alleen GPS van browser</option>';asel.innerHTML='<option value="">Kies person/device_tracker</option>';let entities=[];try{entities=(await api('api/location/entities')).entities||[]}catch(e){toast('Locatielijst niet beschikbaar; opgeslagen keuzes blijven behouden.',true)}for(let [target,value] of [[sel,saved],[asel,asaved]]){for(let x of entities){let o=document.createElement('option');o.value=x.entity_id;o.textContent=`${x.name} (${x.entity_id})`;target.appendChild(o)}if(value&&!entities.some(x=>x.entity_id===value)){let o=document.createElement('option');o.value=value;o.textContent=value+' (opgeslagen; nu niet beschikbaar)';target.appendChild(o)}target.value=value}}
async function loadNotifyServices(){let sel=$('setAssistantNotify'),saved=DATA?.settings?.assistant_notify_service||'';sel.innerHTML='<option value="">Kies mobiele meldingsservice</option>';try{let r=await api('api/notify/services');(r.services||[]).forEach(x=>{let o=document.createElement('option');o.value=x.service;o.textContent=`${x.name} (${x.service})`;sel.appendChild(o)});sel.value=saved}catch(e){let o=document.createElement('option');o.textContent='Meldingsservices niet beschikbaar';o.disabled=true;sel.appendChild(o)}}
async function openSettings(){$('setVehicle').value=DATA.settings.vehicle_name;$('setFuel').value=DATA.settings.fuel_type;$('setCurrency').value=DATA.settings.currency;$('setDriver').value=DATA.settings.driver_name||'';$('setCompany').value=DATA.settings.company_name||'';$('setMake').value=DATA.settings.vehicle_make||'';$('setModel').value=DATA.settings.vehicle_model||'';$('setPlate').value=DATA.settings.license_plate||'';$('setPeriodFrom').value=DATA.settings.vehicle_period_from||'';$('setPeriodTo').value=DATA.settings.vehicle_period_to||'';$('setInitial').value=DATA.current_odometer??'';$('initialWrap').style.display=DATA.has_events?'none':'block';$('setAssistantEnabled').checked=String(DATA.settings.assistant_enabled||'0')==='1';$('setAssistantMode').value=DATA.settings.assistant_mode||'assistant';$('setAssistantConfidence').value=DATA.settings.assistant_auto_confidence||95;$('setAssistantSyncZones').checked=String(DATA.settings.assistant_sync_zones||'1')!=='0';$('setAssistantUnknown').checked=String(DATA.settings.assistant_unknown_stops||'1')!=='0';$('setAssistantStopMin').value=DATA.settings.assistant_unknown_stop_minutes||4;$('setAssistantFastStop').value=DATA.settings.assistant_fast_stop_seconds||30;$('setAssistantMinTrip').value=DATA.settings.assistant_min_trip_m||500;$('setDistanceLearning').checked=String(DATA.settings.distance_learning_enabled||'1')==='1';let calibration=DATA.business?.calibration||{};$('calibrationStatus').textContent=`${calibration.samples||0} gecontroleerde trajecten · ${calibration.ready?'correctie '+((calibration.factor-1)*100).toFixed(1)+'%':'nog geen stabiele correctie (minimaal 5 trajecten)'}`;let ar=DATA.business?.assistant?.runtime||{},ac=DATA.business?.assistant?.config||{},bs=DATA.app?.backup||{};$('backupStatusText').textContent=!bs.enabled?'Back-up staat uit in de add-onconfiguratie.':!bs.configured?'Vul Drive-map, Google-inloggegevens en een back-upwachtwoord van minimaal 12 tekens in.':bs.last_error?`Laatste fout: ${bs.last_error}`:bs.last_ok_at?`Laatste back-up: ${bs.last_ok_at} · ${bs.last_file}`:`Gereed · dagelijks vanaf ${String(bs.hour).padStart(2,'0')}:00 · ${bs.retention_days===0?'onbeperkt bewaren':bs.retention_days+' dagen bewaren'}.`;$('techStatus').innerHTML=`Google Places + ritlocaties: <b class="${DATA.app.places_enabled?'badge-ok':'badge-off'}">${DATA.app.places_enabled?'API-key actief':'API-key ontbreekt'}</b><br>Voor zakelijke adressen ook Geocoding API inschakelen.<br>Zoekradius: ${DATA.app.places_radius_m} m · maximaal ${DATA.app.places_max_results} resultaten<br>Bekende plekken: ${(DATA.business?.known_places||[]).length}<br>Autonomieniveau: <b>${esc(ac.mode||'assistant')}</b> · grens ${ac.auto_confidence||95}%<br>Ritassistent WebSocket: <b class="${ar.ws_connected?'badge-ok':'badge-off'}">${ar.ws_connected?'verbonden':'niet verbonden'}</b>${ar.last_error?`<br>Laatste melding: ${esc(ar.last_error)}`:''}<br>PWA: <b class="${PWA_WORKER_READY?'badge-ok':'badge-off'}">${isStandalone()?'geïnstalleerd':PWA_WORKER_READY?'offline gereed':'HTTPS vereist'}</b><br>Versie ${DATA.app.version}`;updatePwaInstallUi();await loadLocationEntities();await loadNotifyServices();openModal('settingsModal')}
async function saveSettings(){let payload={location_fallback_entity:$('setLocationEntity').value||'',distance_learning_enabled:$('setDistanceLearning').checked?'1':'0',vehicle_name:$('setVehicle').value,fuel_type:$('setFuel').value,currency:$('setCurrency').value,driver_name:$('setDriver').value,company_name:$('setCompany').value,vehicle_make:$('setMake').value,vehicle_model:$('setModel').value,license_plate:$('setPlate').value,vehicle_period_from:$('setPeriodFrom').value,vehicle_period_to:$('setPeriodTo').value,initial_odometer:$('setInitial').value,assistant_enabled:$('setAssistantEnabled').checked?'1':'0',assistant_mode:$('setAssistantMode').value,assistant_auto_confidence:$('setAssistantConfidence').value||95,assistant_location_entity:$('setAssistantLocation').value||'',assistant_notify_service:$('setAssistantNotify').value||'',assistant_sync_zones:$('setAssistantSyncZones').checked?'1':'0',assistant_unknown_stops:$('setAssistantUnknown').checked?'1':'0',assistant_unknown_stop_minutes:$('setAssistantStopMin').value||4,assistant_fast_stop_seconds:$('setAssistantFastStop').value||30,assistant_check_seconds:'10',assistant_min_trip_m:$('setAssistantMinTrip').value||500};try{await api('api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('settingsModal');toast('Instellingen opgeslagen');reloadData()}catch(e){toast(e.message,true)}}
async function runBackupNow(){toast('Versleutelde Drive-back-up maken…');try{let r=await api('api/backup/run',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast(`Back-up gemaakt · ${r.last_file}`);await reloadData();openSettings()}catch(e){toast(e.message,true)}}
async function loadDiagnosticLog(){try{let report=await api('api/assistant/diagnostics');$('diagnosticText').value=JSON.stringify(report,null,2);$('diagnosticStatus').textContent=`Opgehaald: ${report.generated_at} · ${report.events.length} gebeurtenissen. Je kunt nu kopiëren of downloaden.`}catch(e){$('diagnosticStatus').textContent='Ophalen mislukt. Controleer je verbinding en probeer opnieuw.'}}
async function copyDiagnosticLog(){let el=$('diagnosticText');if(!el.value){toast('Klik eerst op Log ophalen / vernieuwen',true);return}try{await navigator.clipboard.writeText(el.value);$('diagnosticStatus').textContent='Log gekopieerd. Je kunt hem nu in het gesprek plakken.'}catch(e){el.focus();el.select();el.setSelectionRange(0,el.value.length);$('diagnosticStatus').textContent='Automatisch kopiëren lukt niet. Kopieer de geselecteerde tekst of kies Download log.'}}
function downloadDiagnosticLog(){let value=$('diagnosticText').value;if(!value){toast('Klik eerst op Log ophalen / vernieuwen',true);return}let url=URL.createObjectURL(new Blob([value],{type:'text/plain;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='rit-tank-diagnose-'+new Date().toISOString().replace(/[:.]/g,'-')+'.txt';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000)}
async function testAssistantNotification(){try{await api('api/assistant/test-notification',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast('Testmelding verzonden')}catch(e){toast(e.message,true)}}
async function syncAssistantZones(){try{toast('Home Assistant-zones synchroniseren...');let r=await api('api/assistant/sync-zones',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast(`Zones klaar · ${r.ok||0} goed${r.failed?` · ${r.failed} fout`:''}`);reloadData()}catch(e){toast(e.message,true)}}
// Begeleide invoer: actieve sectie volgt wat je aanraakt, zonder onverwacht te springen tijdens scrollen.
[['fuelDate','fuelStepDate'],['fuelStation','fuelStepLocation'],['fuelNote','fuelStepFinish'],['kmDate','kmStepRest'],['kmNote','kmStepRest'],['tripDate','tripStepLocation'],['tripStopNote','tripStepLocation']].forEach(([input,section])=>{let el=$(input);if(el){el.addEventListener('focus',()=>guideTo(section,0));el.addEventListener('change',()=>{if(input==='fuelDate')guideTo('fuelStepLiters',220)})}});
[['literWhole','fuelStepLiters'],['literDec','fuelStepLiters'],['priceWhole','fuelStepPrice'],['priceD1','fuelStepPrice'],['priceD2','fuelStepPrice'],['priceD3','fuelStepPrice']].forEach(([id,section])=>{let el=$(id);if(el){el.addEventListener('touchstart',()=>guideTo(section,0),{passive:true});el.addEventListener('pointerdown',()=>guideTo(section,0),{passive:true})}});
initPwa();
loadAuthStatus();
reloadData();
let AUTO_REFRESH_BUSY=false;
let STOP_PROMPT_BUSY=false,STOP_PROMPT_TRIP=null;
const SEEN_STOP_PROMPTS=new Set();
async function checkStopPrompt(){
  if(document.hidden||STOP_PROMPT_BUSY||document.querySelector('.modal.show'))return;
  STOP_PROMPT_BUSY=true;
  try{
    let result=await api('api/business/odometer-suggestion'),prompt=result.stop_prompt;
    if(document.hidden||document.querySelector('.modal.show')||!prompt||!result.active||SEEN_STOP_PROMPTS.has(prompt.id))return;
    if(Date.now()-Date.parse(prompt.created_at)>120000)return;
    SEEN_STOP_PROMPTS.add(prompt.id);STOP_PROMPT_TRIP=prompt.trip_id;
    $('stopPromptAddress').textContent=prompt.address||prompt.province;
    openModal('stopPromptModal');
  }catch(e){/* Tijdelijke netwerkfouten onderbreken geen invoer. */}
  finally{STOP_PROMPT_BUSY=false}
}
async function finishFromStopPrompt(){
  try{
    let current=await api('api/business/odometer-suggestion');
    if(!current.active||current.trip_id!==STOP_PROMPT_TRIP){closeModal('stopPromptModal');toast('Deze rit is niet meer actief.');return}
    closeModal('stopPromptModal');await reloadData();openTripPoint('finish');
  }catch(e){toast(e.message,true)}
}
setInterval(checkStopPrompt,2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkStopPrompt()});
async function refreshVisibleDashboard(){if(document.hidden||AUTO_REFRESH_BUSY||document.querySelector('.modal.show'))return;AUTO_REFRESH_BUSY=true;try{await reloadData()}finally{AUTO_REFRESH_BUSY=false}}
setInterval(refreshVisibleDashboard,30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshVisibleDashboard()});
</script>
</body></html>'''



class Handler(BaseHTTPRequestHandler):
    server_version = f'RitTank/{APP_VERSION}'

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}")

    def _path(self) -> tuple[str, dict[str, list[str]]]:
        u = urlparse(self.path)
        return u.path, parse_qs(u.query)

    def _is_ingress(self) -> bool:
        # Home Assistant Supervisor proxies add-on Ingress from this fixed address.
        return self.client_address[0] == '172.30.32.2' and bool(self.headers.get('X-Ingress-Path'))

    def _is_secure(self) -> bool:
        if self._is_ingress():
            return True
        forwarded_proto = str(self.headers.get('X-Forwarded-Proto') or '').split(',', 1)[0].strip().lower()
        forwarded = str(self.headers.get('Forwarded') or '').lower()
        return forwarded_proto == 'https' or bool(re.search(r'(?:^|;)\s*proto=https(?:;|$)', forwarded))

    def _valid_session(self) -> bool:
        cfg = standalone_config()
        token = cookie_value(self.headers.get('Cookie', ''), SESSION_COOKIE)
        return bool(cfg['ready'] and token and verify_session_token(token, cfg['password']))

    def _same_origin(self) -> bool:
        if self._is_ingress():
            return True
        origin = str(self.headers.get('Origin') or '').strip()
        if not origin:
            return False
        parsed = urlparse(origin)
        host = str(self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or '').split(',', 1)[0].strip().lower()
        return parsed.scheme.lower() == 'https' and parsed.netloc.lower() == host

    def _send_html(self, data: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Permissions-Policy', 'geolocation=(self), camera=(self), microphone=()')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'")
        if self._is_secure():
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        self.end_headers()
        self.wfile.write(data)

    def _authorize_api(self) -> bool:
        if self._is_ingress():
            return True
        cfg = standalone_config()
        if not self._is_secure():
            json_response(self, {'error': 'De zelfstandige app is alleen via HTTPS beschikbaar.'}, 426)
            return False
        if not cfg['ready']:
            json_response(self, {'error': 'De zelfstandige modus is nog niet geconfigureerd.'}, 503)
            return False
        if not self._valid_session():
            json_response(self, {'error': 'Log opnieuw in.'}, 401)
            return False
        return True

    def _authorize_mutation(self) -> bool:
        if not self._authorize_api():
            return False
        if not self._same_origin():
            json_response(self, {'error': 'Ongeldige aanvraagbron.'}, 403)
            return False
        return True

    def _serve_root(self) -> None:
        if self._is_ingress():
            return self._send_html(APP_HTML.encode('utf-8'))
        cfg = standalone_config()
        if not cfg['ready']:
            return self._send_html(SETUP_HTML, 503)
        if not self._is_secure():
            return self._send_html(SETUP_HTML, 426)
        if not self._valid_session():
            return self._send_html(LOGIN_HTML)
        return self._send_html(APP_HTML.encode('utf-8'))

    def _auth_status(self) -> None:
        cfg = standalone_config()
        mode = 'ingress' if self._is_ingress() else 'standalone'
        authorized = self._is_ingress() or (self._is_secure() and self._valid_session())
        return json_response(self, {
            'mode': mode,
            'authorized': authorized,
            'configured': bool(cfg['ready']),
            'secure': bool(self._is_secure()),
        })

    def _login(self) -> None:
        if self._is_ingress():
            return json_response(self, {'ok': True, 'mode': 'ingress'})
        if not self._is_secure():
            return json_response(self, {'error': 'Open Rit & Tank via de HTTPS-URL.'}, 426)
        if not self._same_origin():
            return json_response(self, {'error': 'Ongeldige aanvraagbron.'}, 403)
        cfg = standalone_config()
        if not cfg['ready']:
            return json_response(self, {'error': 'De zelfstandige modus is nog niet geconfigureerd.'}, 503)
        retry_after = login_retry_after(self.client_address[0])
        if retry_after:
            return json_response(self, {'error': 'Te veel pogingen. Probeer later opnieuw.'}, 429, {'Retry-After': str(retry_after)})
        supplied = str(read_json(self).get('password') or '')
        if not hmac.compare_digest(supplied.encode('utf-8'), str(cfg['password']).encode('utf-8')):
            record_login_failure(self.client_address[0])
            return json_response(self, {'error': 'Onjuist wachtwoord.'}, 401)
        clear_login_failures(self.client_address[0])
        token = make_session_token(cfg['password'], cfg['session_days'])
        cookie = f'{SESSION_COOKIE}={token}; Path=/; Max-Age={cfg["session_days"] * 86400}; HttpOnly; Secure; SameSite=Strict'
        return json_response(self, {'ok': True}, 200, {'Set-Cookie': cookie})

    def _logout(self) -> None:
        if not self._is_ingress() and (not self._is_secure() or not self._same_origin()):
            return json_response(self, {'error': 'Ongeldige aanvraagbron.'}, 403)
        cookie = f'{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict'
        return json_response(self, {'ok': True}, 200, {'Set-Cookie': cookie})

    def do_GET(self) -> None:
        path, q = self._path()
        if path in ('/', '', '/login'):
            return self._serve_root()
        if path == '/manifest.webmanifest':
            data = pwa_manifest()
            self.send_response(200)
            self.send_header('Content-Type', 'application/manifest+json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/service-worker.js':
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(SERVICE_WORKER)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(SERVICE_WORKER)
            return
        if path == '/captur-2014.png':
            image_path = Path(__file__).with_name('captur-2014.png')
            if not image_path.is_file():
                self.send_error(404, 'Afbeelding ontbreekt')
                return
            data = image_path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(data)
            return
        mi = re.fullmatch(r'/(?:huisplan-)?icon-(180|192|512)\.png', path)
        if mi:
            data = app_icon_png(int(mi.group(1)))
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=604800, immutable')
            self.end_headers()
            self.wfile.write(data)
            return
        if path == '/health':
            return json_response(self, {'ok': True, 'version': APP_VERSION, 'pwa': True})
        if path == '/api/auth/status':
            return self._auth_status()
        if not self._authorize_api():
            return
        if path == '/api/summary':
            period = (q.get('period') or ['month'])[0]
            return json_response(self, summary(period))
        ms = re.fullmatch(r'/api/stats/(day|week|month|year)', path)
        if ms:
            return json_response(self, summary(ms.group(1)))
        if path == '/api/settings':
            return json_response(self, get_settings())
        if path == '/api/location/entities':
            try:
                return json_response(self, {'entities': location_entities()})
            except ValueError as e:
                return json_response(self, {'error': str(e)}, 400)
        if path == '/api/location/entity':
            try:
                entity_id = (q.get('entity_id') or [''])[0]
                return json_response(self, location_from_entity(entity_id))
            except ValueError as e:
                return json_response(self, {'error': str(e)}, 400)
        if path == '/api/notify/services':
            return json_response(self, {'services': ha_notify_services()})
        if path == '/api/assistant/arrivals':
            return json_response(self, {'arrivals': assistant_arrivals(30), 'runtime': assistant_runtime_public(), 'config': assistant_config()})
        if path == '/api/assistant/diagnostics':
            return json_response(self, diagnostic_report())
        if path == '/api/business/active':
            return json_response(self, {'trip': active_business_trip(), 'odometer_suggestion': trip_distance_tracking_public()})
        if path == '/api/business/odometer-suggestion':
            return json_response(self, trip_distance_tracking_public())
        if path == '/api/known-places':
            return json_response(self, {'places': known_places_all()})
        if path == '/api/export.csv':
            return self.export_csv()
        if path == '/api/business.csv':
            period = (q.get('period') or ['all'])[0]
            return self.export_business_csv(period)
        if path == '/api/business.pdf':
            period = (q.get('period') or ['month'])[0]
            return self.export_business_pdf(period, (q.get('year') or [None])[0], (q.get('month') or [None])[0])
        if path == '/api/business/pdf-preview':
            try:
                result = business_pdf((q.get('period') or ['month'])[0], (q.get('year') or [None])[0], (q.get('month') or [None])[0], preview=True)
                return json_response(self, result)
            except ValueError as exc:
                return json_response(self, {'error': str(exc)}, 400)
            except Exception as exc:
                print(f'PDF voorbeeld mislukt: {type(exc).__name__}', flush=True)
                return json_response(self, {'error': 'PDF maken mislukt. Controleer het app-logboek.'}, 500)
        if path == '/api/export/pdf':
            period = (q.get('period') or ['month'])[0]
            return self.export_business_pdf(period, (q.get('year') or [None])[0], (q.get('month') or [None])[0])
        mr = re.fullmatch(r'/api/receipt/(\d+)', path)
        if mr:
            return self.serve_receipt(int(mr.group(1)))
        return json_response(self, {'error': 'Niet gevonden'}, 404)

    def do_POST(self) -> None:
        path, _ = self._path()
        if path == '/api/auth/login':
            return self._login()
        if path == '/api/auth/logout':
            return self._logout()
        if not self._authorize_mutation():
            return
        payload = read_json(self)
        try:
            if path == '/api/places/nearby':
                lat = to_float(payload.get('latitude'))
                lon = to_float(payload.get('longitude'))
                if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    raise ValueError('Geen geldige huidige locatie ontvangen.')
                return json_response(self, {'places': google_nearby(lat, lon), 'radius_m': places_radius_m()})
            if path in ('/api/location/reverse', '/api/location/addresses'):
                lat = to_float(payload.get('latitude'))
                lon = to_float(payload.get('longitude'))
                if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    raise ValueError('Geen geldige huidige locatie ontvangen.')
                return json_response(self, nearby_house_numbers(lat, lon) if path.endswith('/addresses') else google_reverse_geocode(lat, lon))
            if path == '/api/business/suggest':
                trip=active_business_trip()
                if not trip or not trip.get('stops'):
                    raise ValueError('Er is geen actieve ritregistratie.')
                lat,lon=to_float(payload.get('latitude')),to_float(payload.get('longitude'))
                if lat is None or lon is None: raise ValueError('Geen geldige locatie ontvangen.')
                last=trip['stops'][-1]
                suggestion=suggest_segment(last,float(lat),float(lon))
                # Keep payload compact and JSON-safe.
                return json_response(self, {
                    'suggested_type': suggestion.get('suggested_type') or '',
                    'reason': suggestion.get('reason') or '',
                    'confidence': suggestion.get('confidence') or 0,
                    'source': suggestion.get('source') or '',
                    'origin_place': suggestion.get('origin_place'),
                    'destination_place': suggestion.get('destination_place'),
                })
            if path == '/api/assistant/test-notification':
                diagnostic_event('test_push_aangevraagd')
                try:
                    send_assistant_test_notification()
                    diagnostic_event('test_push_geaccepteerd_door_ha')
                except Exception:
                    diagnostic_event('test_push_mislukt')
                    raise
                return json_response(self, {'ok': True})
            if path == '/api/assistant/sync-zones':
                return json_response(self, sync_all_known_place_zones())
            if path == '/api/receipt/scan':
                return json_response(self, {'receipt': scan_receipt(str(payload.get('image_data_url') or ''))})
            if path == '/api/backup/pdf':
                return json_response(self, archive_pdf(payload))
            if path == '/api/backup/run':
                return json_response(self, run_drive_backup())
            ma = re.fullmatch(r'/api/assistant/(\d+)/confirm', path)
            if ma:
                return json_response(self, {'arrival': confirm_assistant_arrival(int(ma.group(1)), str(payload.get('trip_type') or ''), 'app')})
            mc = re.fullmatch(r'/api/assistant/(\d+)/complete', path)
            if mc:
                return json_response(self, complete_assistant_arrival(int(mc.group(1)), payload), 201)
            if path == '/api/known-places':
                return json_response(self, {'place': save_known_place(payload)}, 201)
            mp=re.fullmatch(r'/api/known-places/(\d+)', path)
            if mp:
                return json_response(self, {'place': save_known_place(payload, int(mp.group(1)))})
            if path in ('/api/business/start', '/api/trips/start'):
                return json_response(self, start_business_trip(payload), 201)
            if path in ('/api/business/stop', '/api/trips/location'):
                return json_response(self, add_business_stop(payload, finish=False), 201)
            if path in ('/api/business/finish', '/api/trips/finish'):
                return json_response(self, add_business_stop(payload, finish=True), 201)
            if path == '/api/fuel':
                return json_response(self, add_fuel(payload), 201)
            if path == '/api/odometer':
                return json_response(self, add_odometer(payload), 201)
            if path == '/api/settings':
                return json_response(self, {'ok': True, 'settings': set_settings(payload)})
            me = re.fullmatch(r'/api/business/(\d+)/edit', path)
            if me:
                return json_response(self, edit_business_trip(int(me.group(1)), payload))
            return json_response(self, {'error': 'Niet gevonden'}, 404)
        except ValueError as e:
            return json_response(self, {'error': str(e)}, 400)
        except Exception as e:
            print('POST error:', repr(e))
            return json_response(self, {'error': 'Opslaan mislukt.'}, 500)

    def do_DELETE(self) -> None:
        path, _ = self._path()
        if not self._authorize_mutation():
            return
        ma = re.fullmatch(r'/api/assistant/(\d+)', path)
        if ma:
            try:
                dismiss_assistant_arrival(int(ma.group(1)))
                return json_response(self, {'ok': True})
            except Exception:
                return json_response(self, {'error': 'Ritsuggestie verwijderen mislukt.'}, 500)
        mkp = re.fullmatch(r'/api/known-places/(\d+)', path)
        if mkp:
            try:
                delete_known_place(int(mkp.group(1)))
                return json_response(self, {'ok': True})
            except Exception:
                return json_response(self, {'error': 'Bekende plek verwijderen mislukt.'}, 500)
        mt = re.fullmatch(r'/api/business/(\d+)', path)
        if mt:
            try:
                delete_business_trip(int(mt.group(1)))
                return json_response(self, {'ok': True})
            except Exception:
                return json_response(self, {'error': 'Zakelijke rit verwijderen mislukt.'}, 500)
        m = re.fullmatch(r'/api/events/(\d+)', path)
        if not m:
            return json_response(self, {'error': 'Niet gevonden'}, 404)
        try:
            delete_event(int(m.group(1)))
            return json_response(self, {'ok': True})
        except Exception:
            return json_response(self, {'error': 'Verwijderen mislukt.'}, 500)

    def serve_receipt(self, event_id: int) -> None:
        with DB_LOCK, db() as con:
            row=con.execute('SELECT receipt_path FROM events WHERE id=?',(event_id,)).fetchone()
        if not row or not row['receipt_path']:
            return json_response(self, {'error':'Geen tankbon gevonden.'},404)
        name=Path(str(row['receipt_path'])).name; path=RECEIPT_DIR/name
        if not path.exists(): return json_response(self, {'error':'Tankbonbestand ontbreekt.'},404)
        ctype={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.webp':'image/webp','.heic':'image/heic','.heif':'image/heif'}.get(path.suffix.lower(),'application/octet-stream')
        data=path.read_bytes(); self.send_response(200); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','private, max-age=300'); self.end_headers(); self.wfile.write(data)

    def export_csv(self) -> None:
        rows = rows_events()
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(['datum_tijd','type','kilometerstand','delta_km','liters','prijs_per_liter','kosten','tankstation_handmatig','google_place_id','latitude','longitude','locatie_nauwkeurigheid_m','locatie_bron','volgetankt','notitie'])
        for r in rows:
            writer.writerow([
                r['created_at'], r['type'], r['odometer'], r.get('delta_km',0), r.get('liters','') or '', r.get('price_per_liter','') or '',
                round(float(r.get('cost') or 0),2) if r['type']=='fuel' else '', r.get('station','') or '',
                r.get('place_id','') or '', r.get('latitude','') if r.get('latitude') is not None else '',
                r.get('longitude','') if r.get('longitude') is not None else '',
                r.get('location_accuracy','') if r.get('location_accuracy') is not None else '', r.get('location_source','') or '',
                'ja' if int(r.get('full_tank') or 0) else 'nee', r.get('note','') or ''
            ])
        data = output.getvalue().encode('utf-8-sig')
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="rit_tank_export.csv"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


    def export_business_csv(self, period: str = 'all') -> None:
        output=io.StringIO(); writer=csv.writer(output,delimiter=';')
        writer.writerow(['rit_id','ritsoort_samenvatting','status','doel','klant','start_datum_tijd','eind_datum_tijd','totaal_km','zakelijk_km','prive_km','prive_omrijkm','afwijkende_route','stop_nr','stop_datum_tijd','kilometerstand','segment_km','segment_ritsoort','classificatie_bron','suggestie','suggestie_reden','latitude','longitude','google_place_id','bekende_plek','locatie_label_live','notitie'])
        for enriched in business_trips_for_period(period):
            for stop in enriched.get('stops',[]):
                writer.writerow([enriched['id'],enriched.get('trip_type_label') or '',enriched['status'],enriched.get('purpose') or '',enriched.get('client') or '',enriched.get('started_at') or '',enriched.get('ended_at') or '',enriched.get('km') or 0,enriched.get('business_km') or 0,enriched.get('private_km') or 0,enriched.get('private_detour_km') or 0,enriched.get('deviating_route') or '',stop.get('sequence_no'),stop.get('created_at'),stop.get('odometer'),stop.get('segment_km') or 0,stop.get('segment_trip_type_label') or '',stop.get('segment_classification_source') or '',stop.get('segment_suggested_type') or '',stop.get('segment_suggestion_reason') or '',stop.get('latitude') if stop.get('latitude') is not None else '',stop.get('longitude') if stop.get('longitude') is not None else '',stop.get('place_id') or '',stop.get('known_place_name') or '',stop.get('location_label') or '',stop.get('note') or ''])
        data=output.getvalue().encode('utf-8-sig'); self.send_response(200); self.send_header('Content-Type','text/csv; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename="rittenregistratie_export.csv"'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

    def export_business_pdf(self, period: str = 'month', year: str | None = None, month: str | None = None) -> None:
        try:
            data, filename = business_pdf(period, year, month)
        except ValueError as exc:
            return json_response(self, {'error': str(exc)}, 400)
        except Exception as exc:
            print(f'PDF export mislukt: {type(exc).__name__}', flush=True)
            return json_response(self, {'error': 'PDF maken mislukt. Controleer het app-logboek.'}, 500)
        self.send_response(200)
        self.send_header('Content-Type', 'application/pdf')
        self.send_header('Content-Disposition', f'inline; filename="{filename}"')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    init_db()
    publish_sensors_async()
    start_assistant_threads()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Rit & Tank {APP_VERSION} gestart op poort {PORT}')
    server.serve_forever()


if __name__ == '__main__':
    main()
