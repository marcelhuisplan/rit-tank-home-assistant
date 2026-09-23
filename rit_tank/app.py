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
APP_VERSION = '23.00'
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
    'km_reimbursement_rate': 0.25,
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
            address TEXT,
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
            ('segment_classification_source', 'TEXT'),
            ('original_destination_latitude', 'REAL'),
            ('original_destination_longitude', 'REAL'),
            ('original_destination_address', 'TEXT'),
            ('original_destination_distance_m', 'REAL'),
            ('destination_distance_source', 'TEXT'),
            ('destination_manually_corrected', 'INTEGER DEFAULT 0')
        ):
            if col not in stop_cols:
                con.execute(f'ALTER TABLE trip_stops ADD COLUMN {col} {sql_type}')

        place_cols = {r['name'] for r in con.execute('PRAGMA table_info(known_places)')}
        for col, sql_type in (
            ('address', 'TEXT'),
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

        arrivals_cols = {r['name'] for r in con.execute('PRAGMA table_info(assistant_arrivals)')}
        for col, sql_type in (
            ('corrected_destination_latitude', 'REAL'),
            ('corrected_destination_longitude', 'REAL'),
            ('corrected_destination_label', 'TEXT'),
            ('corrected_destination_distance_m', 'REAL'),
            ('corrected_destination_place_id', 'TEXT'),
            ('destination_distance_source', 'TEXT'),
            ('destination_manually_corrected', 'INTEGER DEFAULT 0'),
            ('corrected_origin_latitude', 'REAL'),
            ('corrected_origin_longitude', 'REAL'),
            ('corrected_origin_label', 'TEXT'),
            ('corrected_origin_place_id', 'TEXT'),
            ('origin_manually_corrected', 'INTEGER DEFAULT 0'),
            ('corrected_route_distance_m', 'REAL')
        ):
            if col not in arrivals_cols:
                con.execute(f'ALTER TABLE assistant_arrivals ADD COLUMN {col} {sql_type}')

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




try:
    from . import google_places
except ImportError:
    import google_places

try:
    from . import routing
except ImportError:
    import routing

try:
    from . import home_assistant
except ImportError:
    import home_assistant

try:
    from . import trips
except ImportError:
    import trips

try:
    from . import assistant
except ImportError:
    import assistant


def _places_dependencies() -> dict[str, Any]:
    return {
        'load_options': load_options,
        'http_json': http_json,
        'haversine_m': haversine_m,
        'to_float': to_float,
        'db': db,
        'DB_LOCK': DB_LOCK,
    }


def places_key() -> str:
    return google_places.places_key(dependencies=_places_dependencies())


def places_radius_m() -> int:
    return google_places.places_radius_m(dependencies=_places_dependencies())


def places_max_results() -> int:
    return google_places.places_max_results(dependencies=_places_dependencies())


def google_nearby(lat: float, lon: float) -> list[dict[str, Any]]:
    return google_places.google_nearby(lat, lon, dependencies=_places_dependencies())


def google_places_text_search(query: str) -> list[dict[str, Any]]:
    return google_places.google_places_text_search(query, dependencies=_places_dependencies())


def google_place_details(place_id: str) -> dict[str, Any] | None:
    return google_places.google_place_details(place_id, dependencies=_places_dependencies())


def google_reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    return google_places.google_reverse_geocode(lat, lon, dependencies=_places_dependencies())


def nearby_house_numbers(lat: float, lon: float) -> dict[str, Any]:
    return google_places.nearby_house_numbers(lat, lon, dependencies=_places_dependencies())


def cached_report_address(lat: float, lon: float) -> str:
    return google_places.cached_report_address(lat, lon, dependencies=_places_dependencies())


def refresh_report_addresses() -> None:
    return google_places.refresh_report_addresses(dependencies=_places_dependencies())


def _report_address_worker() -> None:
    return google_places._report_address_worker(dependencies=_places_dependencies())


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
        return {'label': label, 'address': address, 'google_maps_uri': f'https://www.google.com/maps/search/?api=1&query={lat},{lon}'}
    if kp:
        name = str(kp.get('name') or 'Bekende plek').strip()
        known_address = str(kp.get('address') or '').strip()
        label = f'{name} - {known_address}' if known_address else name
        if lat is not None and lon is not None:
            maps = f'https://www.google.com/maps/search/?api=1&query={lat},{lon}'
        elif known_address:
            maps = 'https://www.google.com/maps/search/?api=1&query=' + quote(known_address)
        else:
            maps = ''
        return {'label': label, 'address': known_address or name, 'google_maps_uri': maps}
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
    return routing.haversine_m(lat1, lon1, lat2, lon2)


def get_route_distance(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> dict[str, Any]:
    return routing.get_route_distance(
        origin_lat,
        origin_lon,
        dest_lat,
        dest_lon,
        dependencies={'api_key': places_key, 'http_json': http_json},
    )


def ha_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 8) -> Any:
    return home_assistant.ha_request(method, path, payload, timeout)


def ha_get(path: str) -> Any:
    return home_assistant.ha_get(path, dependencies={'ha_request': ha_request})


def ha_post(path: str, payload: dict[str, Any]) -> Any:
    return home_assistant.ha_post(path, payload, dependencies={'ha_request': ha_request})


def ha_notify_services() -> list[dict[str, str]]:
    return home_assistant.ha_notify_services(dependencies={'ha_get': ha_get})


def _ha_ws_open(timeout: int = 10):
    return home_assistant._ha_ws_open(timeout)


def ha_ws_command(command: dict[str, Any], timeout: int = 10) -> Any:
    return home_assistant.ha_ws_command(
        command,
        timeout,
        dependencies={'ha_ws_open': _ha_ws_open},
    )


def location_entities() -> list[dict[str, Any]]:
    return home_assistant.location_entities(dependencies={'ha_get': ha_get, 'to_float': to_float})


def location_from_entity(entity_id: str) -> dict[str, Any]:
    return home_assistant.location_from_entity(
        entity_id,
        dependencies={'ha_get': ha_get, 'to_float': to_float},
    )

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
    return trips.snapshot_trip(con, trip_id)

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
    address_provided = 'address' in payload
    address = str(payload.get('address') or '').strip()[:240] if address_provided else None
    stamp = iso_local()
    with DB_LOCK, db() as con:
        if place_id:
            exists = con.execute('SELECT id,address FROM known_places WHERE id=?', (int(place_id),)).fetchone()
            if not exists: raise ValueError('Bekende plek niet gevonden.')
            if not address_provided:
                address = exists['address'] or ''
            con.execute('''UPDATE known_places SET name=?,category=?,latitude=?,longitude=?,radius_m=?,arrival_trip_type=?,unknown_departure_trip_type=?,address=?,updated_at=? WHERE id=?''',
                        (name,category,lat,lon,radius,arrival,depart,address,stamp,int(place_id)))
            pid=int(place_id)
            audit('update','known_place',pid,{'name':name,'category':category},con=con)
        else:
            cur=con.execute('''INSERT INTO known_places(name,category,latitude,longitude,radius_m,arrival_trip_type,unknown_departure_trip_type,address,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                            (name,category,lat,lon,radius,arrival,depart,address or None,stamp,stamp))
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


def _assistant_dependencies() -> dict[str, Any]:
    return {
        'get_settings': get_settings,
        'setting_int': setting_int,
        'DB_LOCK': DB_LOCK,
        'db': db,
        'DIAGNOSTIC_LOCK': DIAGNOSTIC_LOCK,
        'iso_local': iso_local,
        'parse_dt': parse_dt,
        'APP_VERSION': APP_VERSION,
        'setting_bool': setting_bool,
        'latest_odometer_before': latest_odometer_before,
        'to_float': to_float,
        'known_place_by_id': known_place_by_id,
        'trip_type_label': trip_type_label,
        '_route_memory_suggestion': _route_memory_suggestion,
        'audit': audit,
        'google_reverse_geocode': google_reverse_geocode,
        'haversine_m': haversine_m,
        'normalize_segment_type': normalize_segment_type,
        'now_local': now_local,
        'active_business_trip': active_business_trip,
        'get_route_distance': get_route_distance,
        '_insert_trip_stop': _insert_trip_stop,
        'add_business_stop': add_business_stop,
        'publish_sensors_async': publish_sensors_async,
        'remember_segment': remember_segment,
        'validate_odometer': validate_odometer,
        'ha_post': ha_post,
        '_ha_ws_open': _ha_ws_open,
        'match_known_place': match_known_place,
        'location_from_entity': location_from_entity,
        'sync_all_known_place_zones': sync_all_known_place_zones,
        '_assistant_action_listener': _assistant_action_listener,
        '_assistant_arrival_destination_distance': _assistant_arrival_destination_distance,
        '_assistant_arrival_effective_destination': _assistant_arrival_effective_destination,
        '_assistant_arrival_effective_origin': _assistant_arrival_effective_origin,
        '_assistant_arrival_origin_coords': _assistant_arrival_origin_coords,
        '_assistant_location_worker': _assistant_location_worker,
        '_assistant_suggestion': _assistant_suggestion,
        '_complete_assistant_arrival': _complete_assistant_arrival,
        '_process_assistant_location': _process_assistant_location,
        'advance_draft_route': advance_draft_route,
        'arrival_proposal': arrival_proposal,
        'assistant_arrivals': assistant_arrivals,
        'assistant_config': assistant_config,
        'assistant_runtime_public': assistant_runtime_public,
        'assistant_state_get': assistant_state_get,
        'assistant_state_set': assistant_state_set,
        'calibration_vehicle': calibration_vehicle,
        'complete_assistant_arrival': complete_assistant_arrival,
        'confirm_assistant_arrival': confirm_assistant_arrival,
        'correct_assistant_arrival_destination': correct_assistant_arrival_destination,
        'correct_assistant_arrival_route': correct_assistant_arrival_route,
        'create_assistant_arrival': create_assistant_arrival,
        'diagnostic_event': diagnostic_event,
        'diagnostic_report': diagnostic_report,
        'dismiss_assistant_arrival': dismiss_assistant_arrival,
        'distance_calibration': distance_calibration,
        'learn_distance': learn_distance,
        'preview_assistant_arrival_destination': preview_assistant_arrival_destination,
        'preview_assistant_arrival_route': preview_assistant_arrival_route,
        'province_allowed_for_push': province_allowed_for_push,
        'reset_trip_distance_tracking': reset_trip_distance_tracking,
        'send_active_trip_stop_notification': send_active_trip_stop_notification,
        'send_assistant_notification': send_assistant_notification,
        'send_assistant_test_notification': send_assistant_test_notification,
        'track_active_trip_distance': track_active_trip_distance,
        'trip_distance_tracking_public': trip_distance_tracking_public,
    }


def assistant_config() -> dict[str, Any]:
    return assistant.assistant_config(dependencies=_assistant_dependencies())



def assistant_state_get(key: str, default: Any=None) -> Any:
    return assistant.assistant_state_get(key, default, dependencies=_assistant_dependencies())



def assistant_state_set(key: str, value: Any) -> None:
    return assistant.assistant_state_set(key, value, dependencies=_assistant_dependencies())



DIAGNOSTIC_LOCK = threading.Lock()


def diagnostic_event(event: str, **details: Any) -> None:
    return assistant.diagnostic_event(event, **details, dependencies=_assistant_dependencies())



def diagnostic_report() -> dict[str, Any]:
    return assistant.diagnostic_report(dependencies=_assistant_dependencies())



def calibration_vehicle() -> str:
    return assistant.calibration_vehicle(dependencies=_assistant_dependencies())



def distance_calibration() -> dict[str, Any]:
    return assistant.distance_calibration(dependencies=_assistant_dependencies())



def learn_distance(source: str, gps_km: float, actual_km: float, samples: int, checked: bool, *, con: sqlite3.Connection) -> None:
    return assistant.learn_distance(source, gps_km, actual_km, samples, checked, con=con, dependencies=_assistant_dependencies())



def advance_draft_route(runtime: dict[str, Any], lat: float, lon: float, accuracy: float | None, now: datetime) -> None:
    return assistant.advance_draft_route(runtime, lat, lon, accuracy, now, dependencies=_assistant_dependencies())



def arrival_proposal(row: dict[str, Any]) -> dict[str, Any]:
    return assistant.arrival_proposal(row, dependencies=_assistant_dependencies())



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


def assistant_arrivals(limit: int=12, include_done: bool=False) -> list[dict[str, Any]]:
    return assistant.assistant_arrivals(limit, include_done, dependencies=_assistant_dependencies())



def assistant_runtime_public() -> dict[str, Any]:
    return assistant.assistant_runtime_public(dependencies=_assistant_dependencies())



def _assistant_suggestion(origin_place_id: int | None, dest_place_id: int | None, lat: float, lon: float) -> dict[str, Any]:
    return assistant._assistant_suggestion(origin_place_id, dest_place_id, lat, lon, dependencies=_assistant_dependencies())



def create_assistant_arrival(origin_place_id: int | None, destination_place_id: int | None, lat: float, lon: float, accuracy: float | None, departure_at: str | None, destination_label: str='', route_snapshot: dict[str, Any] | None=None) -> dict[str, Any] | None:
    return assistant.create_assistant_arrival(origin_place_id, destination_place_id, lat, lon, accuracy, departure_at, destination_label, route_snapshot, dependencies=_assistant_dependencies())



def confirm_assistant_arrival(arrival_id: int, trip_type: str, source: str='app') -> dict[str, Any]:
    return assistant.confirm_assistant_arrival(arrival_id, trip_type, source, dependencies=_assistant_dependencies())



def dismiss_assistant_arrival(arrival_id: int) -> None:
    return assistant.dismiss_assistant_arrival(arrival_id, dependencies=_assistant_dependencies())



def _assistant_arrival_effective_destination(r: dict[str, Any]) -> dict[str, Any]:
    return assistant._assistant_arrival_effective_destination(r, dependencies=_assistant_dependencies())



def _assistant_arrival_effective_origin(r: dict[str, Any]) -> dict[str, Any]:
    return assistant._assistant_arrival_effective_origin(r, dependencies=_assistant_dependencies())



def _assistant_arrival_origin_coords(r: dict[str, Any]) -> tuple[float, float] | None:
    return assistant._assistant_arrival_origin_coords(r, dependencies=_assistant_dependencies())



def _assistant_arrival_destination_distance(r: dict[str, Any], arrival_id: int, dest_lat: float, dest_lon: float) -> dict[str, Any]:
    return assistant._assistant_arrival_destination_distance(r, arrival_id, dest_lat, dest_lon, dependencies=_assistant_dependencies())



def preview_assistant_arrival_destination(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return assistant.preview_assistant_arrival_destination(arrival_id, payload, dependencies=_assistant_dependencies())



def correct_assistant_arrival_destination(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return assistant.correct_assistant_arrival_destination(arrival_id, payload, dependencies=_assistant_dependencies())



def preview_assistant_arrival_route(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return assistant.preview_assistant_arrival_route(arrival_id, payload, dependencies=_assistant_dependencies())



def correct_assistant_arrival_route(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return assistant.correct_assistant_arrival_route(arrival_id, payload, dependencies=_assistant_dependencies())



def complete_assistant_arrival(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return assistant.complete_assistant_arrival(arrival_id, payload, dependencies=_assistant_dependencies())



def _complete_assistant_arrival(arrival_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return assistant._complete_assistant_arrival(arrival_id, payload, dependencies=_assistant_dependencies())



def province_allowed_for_push(lat: float, lon: float) -> tuple[bool, str, dict[str, Any]]:
    return assistant.province_allowed_for_push(lat, lon, dependencies=_assistant_dependencies())



def trip_distance_tracking_public() -> dict[str, Any]:
    return assistant.trip_distance_tracking_public(dependencies=_assistant_dependencies())



def reset_trip_distance_tracking(trip: dict[str, Any] | None=None) -> None:
    return assistant.reset_trip_distance_tracking(trip, dependencies=_assistant_dependencies())



def track_active_trip_distance(loc: dict[str, Any], cfg: dict[str, Any]) -> None:
    return assistant.track_active_trip_distance(loc, cfg, dependencies=_assistant_dependencies())



def send_active_trip_stop_notification(trip: dict[str, Any], tracked_m: float, province: str, geo: dict[str, Any]) -> bool:
    return assistant.send_active_trip_stop_notification(trip, tracked_m, province, geo, dependencies=_assistant_dependencies())



def send_assistant_notification(item: dict[str, Any]) -> bool:
    return assistant.send_assistant_notification(item, dependencies=_assistant_dependencies())



def send_assistant_test_notification() -> None:
    return assistant.send_assistant_test_notification(dependencies=_assistant_dependencies())



def _assistant_action_listener() -> None:
    return assistant._assistant_action_listener(dependencies=_assistant_dependencies())



def _process_assistant_location(loc: dict[str, Any], cfg: dict[str, Any]) -> None:
    return assistant._process_assistant_location(loc, cfg, dependencies=_assistant_dependencies())



def _assistant_location_worker() -> None:
    return assistant._assistant_location_worker(dependencies=_assistant_dependencies())



def start_assistant_threads() -> None:
    threading.Thread(target=_report_address_worker, daemon=True, name='rit-tank-addresses').start()
    threading.Thread(target=_assistant_location_worker, daemon=True, name='rit-tank-location').start()
    threading.Thread(target=_assistant_action_listener, daemon=True, name='rit-tank-actions').start()
    if setting_bool('assistant_sync_zones', True):
        threading.Thread(target=sync_all_known_place_zones, daemon=True, name='rit-tank-zones').start()
    threading.Thread(target=_backup_worker, daemon=True, name='rit-tank-backup').start()


def _trips_dependencies() -> dict[str, Any]:
    return {
        'db': db,
        'DB_LOCK': DB_LOCK,
        'haversine_m': haversine_m,
        'match_known_place': match_known_place,
        'known_place_by_id': known_place_by_id,
        'to_float': to_float,
        'normalize_segment_type': normalize_segment_type,
        'iso_local': iso_local,
        'parse_dt': parse_dt,
        'now_local': now_local,
        'validate_odometer': validate_odometer,
        'google_reverse_geocode': google_reverse_geocode,
        'audit': audit,
        'reset_trip_distance_tracking': reset_trip_distance_tracking,
        'publish_sensors_async': publish_sensors_async,
        'assistant_state_get': assistant_state_get,
        'assistant_state_set': assistant_state_set,
        'learn_distance': learn_distance,
        'trip_location_details': trip_location_details,
        'dutch_date': dutch_date,
        'period_bounds': period_bounds,
    }


def _route_memory_suggestion(origin_place_id: int | None, dest_lat: float, dest_lon: float) -> dict[str, Any] | None:
    return trips._route_memory_suggestion(origin_place_id, dest_lat, dest_lon, dependencies=_trips_dependencies())

def suggest_segment(origin_stop: dict[str, Any] | None, dest_lat: float, dest_lon: float) -> dict[str, Any]:
    return trips.suggest_segment(origin_stop, dest_lat, dest_lon, dependencies=_trips_dependencies())

def remember_segment(origin_stop: dict[str, Any], destination_point: dict[str, Any], trip_type: str, *, con: sqlite3.Connection | None = None) -> None:
    return trips.remember_segment(origin_stop, destination_point, trip_type, con=con, dependencies=_trips_dependencies())

def trip_type_label(value: str) -> str:
    return trips.trip_type_label(value)

def active_business_trip() -> dict[str, Any] | None:
    return trips.active_business_trip(dependencies=_trips_dependencies())

def _trip_point_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return trips._trip_point_payload(payload, dependencies=_trips_dependencies())

def _insert_trip_stop(con: sqlite3.Connection, trip_id: int, point: dict[str, Any], sequence_no: int, event_note: str,
                      segment_trip_type: str = '', suggestion: dict[str, Any] | None = None,
                      destination_audit: dict[str, Any] | None = None) -> int:
    return trips._insert_trip_stop(
        con, trip_id, point, sequence_no, event_note, segment_trip_type, suggestion,
        destination_audit, dependencies=_trips_dependencies(),
    )

def start_business_trip(payload: dict[str, Any]) -> dict[str, Any]:
    return trips.start_business_trip(payload, dependencies=_trips_dependencies())

def add_business_stop(payload: dict[str, Any], *, finish: bool = False, destination_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    return trips.add_business_stop(
        payload, finish=finish, destination_audit=destination_audit,
        dependencies=_trips_dependencies(),
    )

def business_trip_by_id(trip_id: int) -> dict[str, Any] | None:
    return trips.business_trip_by_id(trip_id, dependencies=_trips_dependencies())

def enrich_business_trip(trip: dict[str, Any], stops: list[dict[str, Any]], resolve: bool = True) -> dict[str, Any]:
    return trips.enrich_business_trip(trip, stops, resolve, dependencies=_trips_dependencies())

def business_trips_raw() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    return trips.business_trips_raw(dependencies=_trips_dependencies())

def business_stats_for_period(period: str) -> dict[str, Any]:
    return trips.business_stats_for_period(period, dependencies=_trips_dependencies())

def recent_business_trips(period: str, limit: int = 12) -> list[dict[str, Any]]:
    return trips.recent_business_trips(period, limit, dependencies=_trips_dependencies())

def business_trips_for_period(period: str) -> list[dict[str, Any]]:
    return trips.business_trips_for_period(period, dependencies=_trips_dependencies())

try:
    from . import pdf_report
except ImportError:
    import pdf_report

_pdf_text = pdf_report._pdf_text
_pdf_escape = pdf_report._pdf_escape
_SimplePdfPage = pdf_report._SimplePdfPage
_build_pdf = pdf_report._build_pdf

def business_pdf(period: str = 'month', year: str | None = None, month: str | None = None, preview: bool = False) -> Any:
    return pdf_report.business_pdf(
        period, year, month, preview,
        dependencies={
            'get_settings': get_settings,
            'now_local': now_local,
            'period_bounds': period_bounds,
            'business_trips_raw': business_trips_raw,
            'enrich_business_trip': enrich_business_trip,
            'parse_dt': parse_dt,
            'period_label': period_label,
            'known_place_by_id': known_place_by_id,
            'cached_report_address': cached_report_address,
        },
    )

def edit_business_trip(trip_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return trips.edit_business_trip(trip_id, payload, dependencies=_trips_dependencies())

def delete_business_trip(trip_id: int) -> None:
    return trips.delete_business_trip(trip_id, dependencies=_trips_dependencies())

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
    return home_assistant.ha_post_state(entity_id, state, attrs)


def publish_sensors() -> None:
    return home_assistant.publish_sensors(dependencies={
        'rows_events': rows_events,
        'get_settings': get_settings,
        'sanitize_prefix': sanitize_prefix,
        'load_options': load_options,
        'current_odometer': current_odometer,
        'stats_for_period': stats_for_period,
        'full_tank_period_average': full_tank_period_average,
        'overall_full_tank_average': overall_full_tank_average,
        'business_stats_for_period': business_stats_for_period,
        'active_business_trip': active_business_trip,
        'ha_post_state': ha_post_state,
    })

def publish_sensors_async() -> None:
    return home_assistant.publish_sensors_async(dependencies={'publish_sensors': publish_sensors})


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
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}body{min-height:100vh;padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}body.modal-open{position:fixed;width:100%;overflow:hidden}button,input,textarea,select{font:inherit}.app{max-width:900px;margin:auto;padding:13px 13px 42px}.topbar{display:flex;align-items:center;justify-content:space-between;padding:7px 3px 13px}.brand{display:flex;gap:10px;align-items:center}.brand-icon{font-size:29px}.brand h1{font-size:21px;margin:0}.brand small{color:var(--muted)}.iconbtn{width:46px;height:46px;border-radius:15px;background:var(--card);border:1px solid var(--line);color:var(--text);font-size:20px}.hero{background:linear-gradient(145deg,#0d3a30,#102921 58%,#141b1a);border:1px solid #277762;border-radius:25px;padding:19px;box-shadow:var(--shadow);display:grid;grid-template-columns:1fr auto;gap:12px}.hero .eyebrow{color:var(--teal);font-weight:900;text-transform:uppercase;font-size:11px;letter-spacing:.08em}.hero h2{font-size:29px;line-height:1.05;margin:4px 0}.odo{font-size:17px;color:#d9e3ea}.hero-stat{text-align:right;align-self:center}.hero-stat strong{font-size:31px;display:block}.hero-stat span{color:var(--muted);font-size:11px}.since-full{margin-top:5px;color:#bad9cc;font-size:11px}.quick{display:grid;grid-template-columns:1.3fr 1fr;gap:9px;margin:11px 0}.quick button{border:0;border-radius:18px;padding:17px 12px;color:white;font-weight:900;font-size:16px}.primary{background:linear-gradient(135deg,#0d78ba,#0ca58d)}.secondary{background:#1b2229;border:1px solid var(--line)!important}.tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;padding:5px;background:#12171b;border:1px solid var(--line);border-radius:17px;margin:13px 0}.tab{border:0;background:transparent;color:var(--muted);padding:11px 3px;border-radius:12px;font-weight:900}.tab.active{background:#25323c;color:white;box-shadow:inset 0 0 0 1px #344552}.period-title{display:flex;align-items:end;justify-content:space-between;margin:17px 2px 8px}.period-title h3{margin:0;font-size:21px}.period-title span{font-size:12px;color:var(--muted)}.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.kpi{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:13px;min-width:0}.kpi .ico{font-size:19px}.kpi b{display:block;font-size:21px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.kpi span{display:block;font-size:11px;color:var(--muted);margin-top:4px}.kpi.em{border-color:#2d6a5b;background:#12251f}.fullavg{margin-top:10px;background:linear-gradient(135deg,#241d10,#1c1812);border:1px solid #725122;border-radius:18px;padding:14px;display:flex;justify-content:space-between;gap:12px}.fullavg b{font-size:25px;color:var(--gold)}.fullavg div:last-child{text-align:right;color:var(--muted);font-size:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:20px;margin-top:12px;padding:15px}.cardhead{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.cardhead h3{margin:0;font-size:18px}.seg{display:flex;background:#10161a;border-radius:10px;padding:3px;overflow:auto}.seg button{border:0;background:transparent;color:var(--muted);padding:6px 8px;border-radius:8px;font-size:11px;white-space:nowrap}.seg button.active{background:#29343e;color:white}.chart-scroll{overflow-x:auto;padding-bottom:5px}.chart{height:190px;display:flex;align-items:flex-end;gap:7px;min-width:100%;padding:15px 4px 0;border-bottom:1px solid #2c343c}.bar-wrap{flex:1;min-width:27px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center}.bar{width:min(28px,80%);min-height:2px;background:linear-gradient(180deg,#63c9ff,#1977bb);border-radius:8px 8px 2px 2px}.bar.orange{background:linear-gradient(180deg,#ffd27e,#d88920)}.bar-val{font-size:9px;color:#aab8c3;margin-bottom:4px;white-space:nowrap}.bar-label{font-size:10px;color:#8e9ba6;margin-top:7px}.station-list,.history{display:flex;flex-direction:column;gap:8px}.station-row{display:grid;grid-template-columns:43px 1fr auto;gap:10px;align-items:center;background:#10161b;border:1px solid #26313a;border-radius:15px;padding:11px}.station-icon{width:42px;height:42px;border-radius:13px;background:#142a26;display:flex;align-items:center;justify-content:center;font-size:21px}.station-row strong{display:block}.station-row small{display:block;color:var(--muted);margin-top:3px;line-height:1.25}.maplink{display:inline-flex;align-items:center;justify-content:center;text-decoration:none;color:#8dd2ff;background:#142635;border:1px solid #25455d;border-radius:11px;padding:8px 9px}.event{display:grid;grid-template-columns:45px 1fr auto;gap:10px;align-items:center;padding:11px;border-radius:14px;background:#10161b;border:1px solid #252e36}.event-icon{width:42px;height:42px;border-radius:13px;display:flex;align-items:center;justify-content:center;background:#1b2b34;font-size:20px}.event strong{display:block;font-size:14px}.event small{display:block;color:var(--muted);margin-top:3px;line-height:1.3}.event .right{text-align:right}.event .right b{display:block}.event-actions{display:flex;justify-content:flex-end;gap:4px;margin-top:3px}.trash{background:none;border:0;color:#84919d;font-size:17px;padding:4px}.empty{text-align:center;color:var(--muted);padding:25px 8px}.toast{position:fixed;left:50%;bottom:calc(25px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(120px);opacity:0;background:#e8f7f1;color:#0b3126;padding:11px 16px;border-radius:14px;font-weight:800;transition:.25s;z-index:80;box-shadow:var(--shadow);max-width:90vw;text-align:center}.toast.show{transform:translateX(-50%) translateY(0);opacity:1}.toast.error{background:#ffe2e5;color:#59131a}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:30;display:none;align-items:flex-end;justify-content:center;overflow:hidden;overscroll-behavior:contain}.modal.show{display:flex}.sheet{width:min(100%,640px);max-height:calc(100dvh - env(safe-area-inset-top) - env(safe-area-inset-bottom) - 12px);overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;touch-action:pan-y;background:#14191e;border:1px solid #303943;border-radius:27px 27px 0 0;padding:12px 16px calc(21px + env(safe-area-inset-bottom));box-shadow:0 -20px 55px rgba(0,0,0,.45)}.grab{width:44px;height:5px;border-radius:5px;background:#4a5259;margin:0 auto 13px}.sheethead{display:flex;align-items:center;justify-content:space-between}.sheethead h2{margin:0;font-size:22px}.close{background:#222a31;border:0;color:white;width:38px;height:38px;border-radius:12px}.field{margin-top:13px}.field label{display:block;color:#afbac3;font-size:12px;font-weight:800;margin:0 0 6px 3px}.field input,.field textarea,.field select{width:100%;border:1px solid #34404a;background:#0e1216;color:white;border-radius:14px;padding:13px;font-size:17px;outline:none}.field input:focus,.field textarea:focus,.field select:focus{border-color:#4faee8}.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}.wheel-title{text-align:center;color:#aeb9c2;font-size:12px;font-weight:900;margin-top:14px}.wheelbox{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:4px;margin-top:5px}.wheelbox.liters{grid-template-columns:minmax(0,1.2fr) auto minmax(0,1fr) minmax(0,1fr)}.wheelbox.liters .wheel{min-width:0}#receiptScanStatus:empty{display:none}.wheelbox.price{grid-template-columns:.8fr auto .7fr .7fr .7fr}.wheel-sep{font-size:30px;color:#71808d}.wheel{height:150px;overflow-y:auto;scroll-snap-type:y mandatory;border-radius:16px;background:#0d1115;border:1px solid #303a43;position:relative;scrollbar-width:none;padding:50px 0}.wheel::-webkit-scrollbar{display:none}.wheel:after{content:"";position:absolute;left:5px;right:5px;top:50px;height:50px;border-top:1px solid #4b5965;border-bottom:1px solid #4b5965;pointer-events:none}.wheel-item{height:50px;scroll-snap-align:center;display:flex;align-items:center;justify-content:center;font-size:22px;color:#7f8d99;transition:.15s}.wheel-item.sel{font-size:29px;font-weight:900;color:white}.live-total{text-align:center;font-size:15px;color:#b7c4cd;margin-top:9px}.live-total b{color:var(--teal);font-size:21px}.station-input{display:grid;grid-template-columns:1fr 54px;gap:8px}.locate{border:1px solid #2f6685;background:#132938;color:#80cfff;border-radius:14px;font-size:23px}.location-status{font-size:11px;color:var(--muted);margin:7px 3px 0}.location-status.ok{color:#75d7b5}.location-status.err{color:#ff9aa4}.station-results{display:flex;flex-direction:column;gap:7px;margin-top:8px}.station-choice{width:100%;text-align:left;background:#10171c;border:1px solid #2d3943;border-radius:14px;padding:11px;color:white}.station-choice b{display:block;font-size:14px}.station-choice small{display:block;color:#9caab5;margin-top:3px;line-height:1.25}.google-attrib{text-align:right;color:#83929e;font-size:10px;margin:7px 4px 0}.google-attrib b{color:#dfe5ea;letter-spacing:.02em}.toggle{display:flex;align-items:center;justify-content:space-between;background:#0f1418;border:1px solid #303943;padding:12px 13px;border-radius:14px;margin-top:13px}.switch{position:relative;width:50px;height:29px}.switch input{display:none}.slider{position:absolute;inset:0;background:#343c44;border-radius:20px}.slider:before{content:"";position:absolute;width:23px;height:23px;left:3px;top:3px;background:white;border-radius:50%;transition:.2s}.switch input:checked + .slider{background:#19a883}.switch input:checked + .slider:before{transform:translateX(21px)}.save{width:100%;margin-top:15px;border:0;border-radius:16px;background:linear-gradient(135deg,#0e78b8,#0aa684);color:white;padding:15px;font-size:17px;font-weight:900}.settings-actions{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-top:14px}.linkbtn{display:block;text-align:center;text-decoration:none;color:white;background:#20272e;border:1px solid #34404a;padding:12px;border-radius:14px;font-weight:800}.tech{background:#10161a;border:1px solid #2a343d;border-radius:14px;padding:11px;margin-top:14px;color:#a7b5bf;font-size:12px;line-height:1.45}.badge-ok{color:#67d7ae}.badge-off{color:#ffbe72}
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

.smart-place-bar{display:flex;gap:8px;margin-top:10px}.smart-place-bar button{flex:1;border:1px solid #385064;background:#13212c;color:#9dd9ff;border-radius:14px;padding:11px;font-weight:900}.known-list{display:flex;flex-direction:column;gap:8px}.known-row{display:grid;grid-template-columns:42px 1fr auto;gap:10px;align-items:center;background:#10171c;border:1px solid #2b3841;border-radius:15px;padding:10px}.known-icon{font-size:24px;text-align:center}.known-row small{display:block;color:var(--muted);margin-top:2px;line-height:1.3}.known-row .known-address{color:#d0e4dc;font-size:12px}.known-actions{display:flex;gap:5px}.known-actions button{border:0;border-radius:10px;padding:7px 9px;background:#1c2b36;color:#a8dbff}.suggest-box{display:none;margin:12px 0;background:linear-gradient(145deg,#122b26,#112027);border:1px solid #2d8069;border-radius:16px;padding:12px}.suggest-box.show{display:block}.suggest-box b{font-size:14px}.suggest-box small{display:block;color:#9db0ba;margin-top:4px;line-height:1.35}.segment-choice{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}.segment-choice button{border:1px solid #35434c;background:#11181d;color:#aab8c2;border-radius:13px;padding:12px;font-weight:900}.segment-choice button.active.business{background:#11382f;border-color:#25866b;color:#74e7bb}.segment-choice button.active.private{background:#3b2028;border-color:#a1465e;color:#ff9bad}.segment-badge{display:inline-flex;margin-left:6px;padding:3px 7px;border-radius:999px;font-size:9px;font-weight:900;background:#1a2a34;color:#8fd5ff}.leg-pill{display:inline-flex;padding:2px 7px;border-radius:999px;font-size:9px;font-weight:900;margin-left:5px}.leg-pill.business{background:#11382f;color:#6ce0b3}.leg-pill.private{background:#3b2028;color:#ff9bad}.place-radius{display:flex;align-items:center;gap:10px}.place-radius input{flex:1}.place-preview{padding:10px;background:#0f1519;border:1px solid #2a3740;border-radius:13px;color:#9fc2d9;font-size:12px;margin-top:8px}
.assistant-panel{display:none;margin:10px 0;border:1px solid #2d8069;background:linear-gradient(145deg,#0e2924,#111c24);border-radius:18px;padding:13px}.assistant-panel.show{display:block}.assistant-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.assistant-head b{font-size:15px}.assistant-head small{display:block;color:#9fb1bc;margin-top:3px;line-height:1.35}.assistant-status{font-size:10px;padding:5px 8px;border-radius:999px;background:#17352d;color:#7ce0b8;font-weight:900;white-space:nowrap}.assistant-status.off{background:#33251a;color:#ffc17a}.assistant-list{display:flex;flex-direction:column;gap:8px;margin-top:10px}.assistant-item{position:relative;min-width:0;background:#0d161b;border:1px solid #2b4246;border-radius:14px;padding:10px 52px 10px 10px}.assistant-route{font-weight:900;font-size:13px;overflow-wrap:anywhere}.assistant-meta{font-size:10px;color:#94a6b2;margin-top:3px;line-height:1.35;overflow-wrap:anywhere}.assistant-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:8px}.assistant-actions button{min-width:0;border:1px solid #34454f;background:#152029;color:#c5d2da;border-radius:10px;padding:9px 7px;font-weight:900;white-space:normal}.assistant-actions .private{background:#342028;border-color:#864052;color:#ff9bad}.assistant-actions .business{background:#11372f;border-color:#27765f;color:#79deb9}.assistant-actions .complete{background:#12304a;border-color:#28638e;color:#8fd1ff}.assistant-actions .edit-address{background:#2e2711;border-color:#8e6f28;color:#ffd479}.assistant-dismiss{position:absolute;right:10px;top:10px;width:36px;height:36px;min-width:36px;border-radius:50%;border:1px solid #52626b;background:#152029;color:#fff;font-size:22px;line-height:1}.assistant-zone-ok{color:#71d9b2}.assistant-zone-err{color:#ff9a9a}.assistant-settings{margin-top:14px;padding:12px;background:#0f1519;border:1px solid #2d3a43;border-radius:15px}.assistant-settings h3{margin:0 0 6px;font-size:15px}.assistant-settings p{margin:0 0 9px;color:#91a2ae;font-size:11px;line-height:1.4}.assistant-route-big{font-size:18px;font-weight:900;text-align:center;padding:11px;background:#0e151a;border:1px solid #2c3942;border-radius:14px;margin-top:10px}.assistant-note{font-size:11px;color:#9fb0ba;line-height:1.4;margin-top:8px}.odo-suggest{display:none;margin:2px 0 10px;padding:18px;border:1px solid rgba(69,240,195,.38);background:linear-gradient(145deg,#103a31,#0b211d);border-radius:21px;text-align:center;box-shadow:0 14px 35px rgba(0,0,0,.22)}.odo-suggest.show{display:block}.odo-suggest-label{color:#78e8ca;font-size:10px;font-weight:900;letter-spacing:.14em;text-transform:uppercase}.odo-suggest-value{font-size:42px;font-weight:900;letter-spacing:-.055em;margin-top:7px;color:#fff}.odo-suggest-value span{font-size:16px;letter-spacing:0;color:#b9cdc7}.odo-suggest-detail{color:#9eb5af;font-size:11px;line-height:1.4;margin-top:8px}.odo-suggest-actions{display:grid;grid-template-columns:1.35fr .65fr;gap:8px;margin-top:15px}.odo-suggest-accept,.odo-suggest-edit{border-radius:15px;padding:13px 8px;font-weight:900}.odo-suggest-accept{border:0;background:linear-gradient(135deg,#50eec7,#19b9a5);color:#05251d}.odo-suggest-edit{border:1px solid rgba(141,211,193,.23);background:#10211e;color:#d7e7e2}.odo-editor[hidden]{display:none}
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
  <input id="tripOdo" type="hidden"><div class="guide-section active" id="tripStepOdo"><div class="guide-head"><span class="step-badge">1</span><b>Kilometerstand</b><small id="tripOdoStepHint">Laatste stand is vooringesteld</small></div><div class="odo-suggest" id="tripOdoSuggestion"><div class="odo-suggest-label">Berekende kilometerstand</div><div class="odo-suggest-value"><b id="tripOdoSuggestedValue">—</b> <span>km</span></div><div class="odo-suggest-detail" id="tripOdoSuggestedDetail"></div><div class="odo-suggest-actions"><button class="odo-suggest-accept" type="button" onclick="acceptTripOdoSuggestion()">✓ Akkoord</button><button class="odo-suggest-edit" type="button" onclick="editTripOdoSuggestion()">Wijzigen</button></div></div><div class="odo-editor" id="tripOdoEditor"><div class="odo-wheelbox" id="tripOdoWheels"></div><div class="odo-live"><b id="tripOdoDisplay">—</b><span>km</span></div><div class="odo-last" id="tripOdoLast"></div><label class="assistant-note"><input type="checkbox" id="tripOdoChecked"> Ik heb de vorige én huidige tellerstand gecontroleerd; gebruik dit traject voor kilometerleren.</label><button class="guide-next" type="button" onclick="guideTo('tripStepLocation')">Kilometerstand bevestigen →</button></div></div><div class="guide-section" id="tripStepLocation"><div class="field" style="margin-top:0"><label>Datum & tijd</label><input id="tripDate" type="datetime-local"></div><div class="trip-location-box"><b id="tripLocationTitle">📍 Nog geen locatie vastgelegd</b><small id="tripLocationDetail">Tik hieronder zodra je op de juiste plek bent.</small><button class="location-big" type="button" onclick="captureTripLocation()">📍 Gebruik huidige locatie</button><div id="tripAddressChoices" class="address-choices"></div><div class="field"><label for="tripManualAddress">Adres uit je afspraak (eventueel corrigeren)</label><input id="tripManualAddress" maxlength="120" placeholder="Straat, huisnummer en plaats"><button class="guide-next" type="button" onclick="confirmManualTripAddress()">Dit adres gebruiken</button><button class="guide-next" type="button" onclick="startAddressDictation('tripManualAddress',searchTripManualAddress)">🎙️ Dicteer adres</button></div><div class="google-attrib" id="tripGoogleAttrib" style="display:none">Adres via <b translate="no">Google Maps</b></div></div></div>
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
  <button class="location-big" type="button" onclick="newKnownPlace()">＋ Nieuwe bekende plek</button>
</div></div>

<div class="modal" id="knownPlaceEditModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2 id="knownEditTitle">📌 Bekende plek</h2><button class="close" onclick="closeModal('knownPlaceEditModal')">✕</button></div>
  <input id="knownId" type="hidden"><input id="knownLat" type="hidden"><input id="knownLon" type="hidden">
  <div class="field"><label>Naam</label><input id="knownName" maxlength="80" placeholder="Bijv. Thuis, School, Kantoor"></div>
  <div class="field"><label>Soort plek</label><select id="knownCategory"><option value="home">🏠 Thuis</option><option value="school">🏫 School</option><option value="work">🏢 Werk</option><option value="client">🤝 Klant</option><option value="family">👨‍👩‍👧 Familie</option><option value="private">❤️ Privé</option><option value="other">📍 Overig</option></select></div>
  <div class="field"><label>Als ik hier aankom, voorstel</label><select id="knownArrival"><option value="ask">❓ Altijd vragen</option><option value="private">🏠 Privé</option><option value="business">💼 Zakelijk</option></select></div>
  <div class="field"><label>Vanaf hier naar een onbekende plek</label><select id="knownDepart"><option value="ask">❓ Geen vaste suggestie</option><option value="private">🏠 Stel Privé voor</option><option value="business">💼 Stel Zakelijk voor</option></select></div>
  <div class="field"><label>Herkenningsradius</label><div class="place-radius"><input id="knownRadius" type="range" min="50" max="600" step="10" value="180" oninput="$('knownRadiusVal').textContent=this.value+' m'"><b id="knownRadiusVal">180 m</b></div></div>
  <div class="field"><label>Locatie</label><div class="place-preview" id="knownAddressPreview"></div><button class="location-big" type="button" onclick="useKnownPlaceCurrentLocation()">📍 Gebruik huidige locatie</button><div class="place-preview" id="knownCoord">Locatie nog niet vastgelegd</div><div class="known-list" id="knownAddressChoices"></div><button class="guide-next" type="button" onclick="showKnownPlaceAddressSearch()">🔎 Ander adres zoeken</button><div id="knownAddressSearch" hidden><div class="field"><label for="knownAddressQuery">Straat, huisnummer en plaats</label><input id="knownAddressQuery" maxlength="200" placeholder="Straat, huisnummer en plaats" onkeydown="if(event.key==='Enter')searchKnownPlaceAddress()"></div><div class="assistant-actions"><button class="odo-suggest-edit" type="button" onclick="searchKnownPlaceAddress()">🔎 Zoeken</button><button class="odo-suggest-edit" type="button" onclick="startAddressDictation('knownAddressQuery',searchKnownPlaceAddress)">🎙️ Dicteren</button></div><div class="known-list" id="knownAddressResults"></div></div><button class="guide-next" id="knownGpsOnly" type="button" hidden onclick="useKnownPlaceGpsOnly()">📍 Alleen huidige GPS-positie gebruiken</button></div>
  <button class="save" onclick="saveKnownPlace()">Bekende plek opslaan</button>
</div></div>

<div class="modal" id="stopPromptModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>Actieve rit opslaan?</h2><button class="close" onclick="closeModal('stopPromptModal')">✕</button></div><p id="stopPromptAddress"></p><p>Je lijkt stil te staan. Wil je de tellerstand controleren en je rit afsluiten?</p><button class="save" onclick="finishFromStopPrompt()">Ja, rit controleren en opslaan</button><button class="guide-next" onclick="closeModal('stopPromptModal')">Nee, ik rijd verder</button></div></div>
<div class="modal" id="assistantModal"><div class="sheet"><div class="grab"></div><div class="sheethead"><h2>📍 Je bent aangekomen</h2><button class="close" onclick="closeModal('assistantModal')">✕</button></div>
  <input id="assistantId" type="hidden"><input id="assistantOdo" type="hidden">
  <div class="assistant-route-big" id="assistantRoute">—</div>
  <div class="assistant-note" id="assistantReason"></div>
  <div class="guide-section" id="assistantRouteSection">
   <div class="guide-head"><span class="step-badge">1</span><b>ROUTE</b><small>Adrescontrole</small></div>
   <div class="assistant-note"><b>VERTREK</b><br><span id="addrCurrentOrigin">—</span><br><br><b>AANKOMST</b><br><span id="addrCurrentLabel">—</span></div>
   <div class="assistant-actions"><button class="edit-address" type="button" onclick="chooseAssistantRouteSide('origin')">✏️ Vertrekadres aanpassen</button><button class="edit-address" type="button" onclick="chooseAssistantRouteSide('destination')">✏️ Aankomstadres aanpassen</button></div>
   <div class="field"><label id="addrQueryLabel">Zoek adres</label><input id="addrQuery" placeholder="Straat, huisnummer, plaats" onkeydown="if(event.key==='Enter')searchAssistantAddress()"></div>
   <div class="assistant-actions"><button class="odo-suggest-edit" type="button" onclick="searchAssistantAddress()">🔍 Zoeken</button><button class="odo-suggest-edit" type="button" onclick="startAddressDictation('addrQuery',searchAssistantAddress)">🎙️ Dicteren</button></div>
   <div class="known-list" id="addrResults"></div>
   <div class="odo-suggest" id="addrPreview">
    <div class="odo-suggest-label">Effectief vertrek</div>
    <div class="assistant-route-big" id="addrPreviewOrigin" style="font-size:14px">—</div>
    <div class="odo-suggest-label" style="margin-top:8px">Effectieve aankomst</div>
    <div class="assistant-route-big" id="addrPreviewLabel" style="font-size:15px">—</div>
    <div class="odo-suggest-detail" id="addrPreviewDetail"></div>
    <div class="odo-suggest-label" style="margin-top:8px">Afstand</div>
    <div class="odo-suggest-detail" id="addrPreviewDistance">—</div>
    <div class="odo-suggest-label" style="margin-top:8px" id="addrPreviewOdoLabel">Voorgestelde eindstand</div>
    <div class="odo-suggest-detail" id="addrPreviewOdo">—</div>
    <button class="save" type="button" id="addrUseBtn" onclick="useAssistantAddressResult()" disabled>✓ Gebruik dit adres</button>
   </div>
  </div>
  <div class="odo-suggest show" id="arrivalProposal" aria-live="polite"><div class="odo-suggest-label">Voorgestelde tellerstand</div><div class="odo-suggest-value"><b id="arrivalOdoValue">—</b> <span>km</span></div><div class="odo-suggest-detail" id="arrivalOdoDetail"></div><button class="odo-suggest-edit" type="button" onclick="editArrivalProposal()">Aanpassen</button></div>
  <div class="field"><label>Ritsoort</label><div class="segment-choice"><button type="button" id="assistantBusiness" class="business" onclick="setAssistantType('business')">💼 Zakelijk</button></div></div>
  <div class="odo-editor" id="arrivalEditor"><div class="field"><label>Kilometerstand bij vertrek</label><input id="assistantStartOdo" type="number" inputmode="numeric" min="0" step="1"><div style="font-size:10px;color:var(--muted);margin-top:5px">Wordt vooringevuld met de laatst bekende stand. Controleer deze vóór opslaan.</div></div>
  <div class="guide-section active" id="assistantStepOdo"><div class="guide-head"><span class="step-badge">2</span><b>Kilometerstand bij aankomst</b><small>Scroll de cijfers</small></div><div class="odo-wheelbox" id="assistantOdoWheels"></div><div class="odo-live"><b id="assistantOdoDisplay">—</b><span>km</span></div><div class="odo-last" id="assistantOdoLast"></div></div>
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
    <p>Herkent bekende plekken op de achtergrond, maakt passieve Home Assistant-zones en stuurt een melding om een zakelijke rit te bevestigen zodra een aankomst wordt gedetecteerd.</p>
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
let modalScrollY=0;function lockModalScroll(){if(document.body.classList.contains('modal-open'))return;modalScrollY=window.scrollY;document.body.style.top=`-${modalScrollY}px`;document.body.classList.add('modal-open')}function unlockModalScroll(){if(document.querySelector('.modal.show'))return;let scrollY=modalScrollY;document.body.classList.remove('modal-open');document.body.style.top='';modalScrollY=0;window.scrollTo(0,scrollY)}function closeModal(id){$(id).classList.remove('show');unlockModalScroll()} function openModal(id){lockModalScroll();$(id).classList.add('show');let sh=$(id).querySelector('.sheet');if(sh)sh.scrollTop=0}
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
function renderBusiness(){let b=DATA.business||{},p=b.period||{},y=b.year||{},a=b.active_trip,primary=$('tripPrimaryLabel');if(primary)primary.textContent=a?'Actieve rit bekijken':'Rit starten';let csv=$('bizCsvLink'),pdf=$('bizPdfLink'),scsv=$('settingsBusinessCsv'),spdf=$('settingsBusinessPdf');if(csv)csv.href=`api/business.csv?period=${PERIOD}`;if(pdf)pdf.href=`api/business.pdf?period=${PERIOD}`;if(scsv)scsv.href=`api/business.csv?period=${PERIOD}`;if(spdf)spdf.href=`api/business.pdf?period=${PERIOD}`;$('bizPeriodLabel').textContent=DATA.period.label;$('bizTripCount').textContent=`${p.trips||0} rit${p.trips===1?'':'ten'}`;$('bizKm').textContent=`${fmt(p.business_km||0,1)} km`;$('bizPrivateKm').textContent=`${fmt(p.private_km||0,1)} km`;$('bizPrivateYear').textContent=`${fmt(y.private_km||0,1)} km`;$('bizTrips').textContent=String(p.trips||0);$('bizStops').textContent=String(p.stops||0);$('bizAvg').textContent=`${fmt(p.avg_km||0,1)} km`;let hero=$('bizHero'),actions=$('bizHeroActions');actions.innerHTML='';if(a){hero.classList.add('active-trip');$('bizHeroTitle').textContent=a.purpose||`${a.trip_type_label||'Rit'} actief`;let os=b.odometer_suggestion||{};if(os.active){$('bizHeroInfo').innerHTML=`${a.trip_type_label||'Rit'}${a.client?' · '+esc(a.client):''} · ${fmt(a.km||0,1)} km · ${a.stop_count||0} locatie${a.stop_count===1?'':'s'}<br>Laatste: ${esc(a.last_location||'—')}<br><br>🛰️ <b>Live GPS-afstand</b><br><span id="liveTripTrackedKm">${fmt(os.tracked_km||0,1)} km</span><br>Laatste tellerstand<br><b id="liveTripBaseOdometer">${os.base_odometer!=null?fmt(os.base_odometer,0)+' km':'—'}</b><br>Voorgestelde eindstand<br><b id="liveTripSuggestedOdometer">${os.suggested_odometer!=null?fmt(os.suggested_odometer,0)+' km':'—'}</b><span id="liveTripWarning">${os.suggestion_reliable===false&&os.distance_warning?`<br>⚠️ ${esc(os.distance_warning)}`:''}</span>`}else $('bizHeroInfo').innerHTML=`${a.trip_type_label||'Rit'} · ${fmt(a.km||0,1)} km · ${a.stop_count||0} locaties`;actions.className='biz-actions';actions.innerHTML='<button class="biz-next" onclick="openTripPoint(\'stop\')">📍 Volgende adres</button><button class="biz-finish" onclick="openTripPoint(\'finish\')">🏁 Rit afsluiten</button>'}else{hero.classList.remove('active-trip');$('bizHeroTitle').textContent='Geen actieve rit';$('bizHeroInfo').textContent='Start een rit en leg vertrek, aankomst, kilometerstanden en ritsoort vast.';actions.className='';actions.innerHTML='<button class="biz-start" onclick="openTripPoint(\'start\')">＋ Nieuwe rit</button>'}renderAssistant();renderBusinessHistory(b.recent_trips||[]);renderAudit(b.audit||[])}
function renderAssistant(){let a=DATA?.business?.assistant||{},cfg=a.config||{},rt=a.runtime||{},pending=a.pending||[],panel=$('assistantPanel'),list=$('assistantList'),badge=$('assistantStatusBadge'),txt=$('assistantStatusText');panel.classList.add('show');badge.textContent=cfg.mode==='autopilot'?'AUTO':cfg.enabled?'AAN':'UIT';badge.className='assistant-status'+(cfg.enabled?'':' off');let parts=[];if(cfg.enabled){parts.push(cfg.mode==='autopilot'?'Autopilot classificeert zekere routes':cfg.mode==='manual'?'Handmatige modus':`Assistent volgt ${esc(cfg.location_entity||'nog geen tracker')}`);if(rt.current_place)parts.push(`nu bij ${esc(rt.current_place)}`);else if(rt.departed_from)parts.push(`vertrokken vanaf ${esc(rt.departed_from)}`);if(rt.draft_active&&!DATA?.business?.active_trip)parts.push(`Concept onderweg · ${fmt(rt.draft_km,1)} GPS-km`);if(rt.last_error)parts.push(`⚠️ ${esc(rt.last_error)}`)}else parts.push('Zet hem aan via Instellingen');txt.innerHTML=parts.join(' · ');list.innerHTML='';if(!pending.length){list.innerHTML='<div class="empty" style="padding:6px 0">Alles is bijgewerkt — geen ritten om te controleren.</div>';return}pending.forEach(x=>{let d=document.createElement('div'),locked=!x.is_next_to_review,disabled=locked?' disabled':'';d.className='assistant-item'+(locked?' locked':'');let chosen=x.confirmed_type==='business'||x.suggested_type==='business'?'business':'',pct=Math.round(Number(x.suggestion_confidence||0)*100),proposal=x.suggested_type==='business'?`Voorstel: ${esc(x.suggested_type_label)} · ${pct}% · ${esc(x.suggestion_reason||'')}`:'Wordt als zakelijke rit opgeslagen';let corrected=x.destination_manually_corrected?`<br>✏️ Handmatig gecorrigeerd · ${x.corrected_destination_distance_m!=null?`${fmt(x.corrected_destination_distance_m/1000,1)} km`:'afstand onbekend'}`:'';if(x.origin_manually_corrected)corrected+='<br>✏️ Vertrek handmatig gecorrigeerd';let queue=locked?`<div class="assistant-meta"><b>${x.queue_position} · DAARNA</b><br>🔒 Eerst eerdere rit afhandelen</div>`:`<div class="assistant-meta"><b>${x.queue_position} · EERST AFHANDELEN</b></div>`;d.innerHTML=`<button class="assistant-dismiss" aria-label="Voorstel sluiten" onclick="dismissAssistant(${x.id})"${disabled}>×</button><div class="assistant-route">${esc(x.origin_name)} → ${esc(x.destination_name)}</div><div class="assistant-meta">${esc(x.date_label)} · ${proposal}${corrected}</div>${queue}<div class="assistant-actions"><button class="business ${chosen==='business'?'active':''}" onclick="confirmAssistant(${x.id})"${disabled}>💼 Zakelijk</button><button class="edit-address" onclick="openAssistantAddressCorrection(${x.id})"${disabled}>✏️ Route</button><button class="complete" onclick="openAssistantComplete(${x.id})"${disabled}>Controleren →</button></div>`;list.appendChild(d)})}
async function confirmAssistant(id){try{await api(`api/assistant/${id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trip_type:'business'})});toast('Zakelijk bevestigd');reloadData()}catch(e){toast(e.message,true)}}
async function dismissAssistant(id){try{await api(`api/assistant/${id}`,{method:'DELETE'});toast('Suggestie gesloten');reloadData()}catch(e){toast(e.message,true)}}
let ADDR_ARRIVAL=null,ADDR_RESULTS=[],ADDR_SELECTED=null,ADDR_SIDE='destination';
function startAddressDictation(inputId,searchCallback){let input=$(inputId),Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;if(!input||!Recognition){toast('Dicteren is hier niet beschikbaar. Typ het adres of gebruik de microfoon van het toetsenbord.',true);return}let recognition=new Recognition(),button=window.event?.currentTarget;recognition.lang='nl-NL';recognition.interimResults=false;recognition.maxAlternatives=1;recognition.onresult=e=>{input.value=e.results[0][0].transcript;searchCallback()};recognition.onerror=()=>toast('Dicteren is hier niet beschikbaar. Typ het adres of gebruik de microfoon van het toetsenbord.',true);recognition.onend=()=>{if(button)button.textContent='🎙️ Dicteren'};if(button)button.textContent='🎙️ Luisteren…';try{recognition.start()}catch(e){toast('Dicteren is hier niet beschikbaar. Typ het adres of gebruik de microfoon van het toetsenbord.',true)}}
function updateAssistantRouteUI(x){if(!x)return;let origin=x.origin_name||'Onbekende vertrekplek',destination=x.destination_name||'Onbekende bestemming';$('addrCurrentOrigin').textContent=origin+(x.origin_manually_corrected?' · ✏️ Handmatig gecorrigeerd':'');$('addrCurrentLabel').textContent=destination+(x.destination_manually_corrected?' · ✏️ Handmatig gecorrigeerd':'');$('assistantRoute').textContent=`${origin} → ${destination}`;$('addrPreview').classList.remove('show');$('addrResults').innerHTML='';$('addrUseBtn').disabled=true}
function chooseAssistantRouteSide(side){ADDR_SIDE=side;$('addrQueryLabel').textContent=side==='origin'?'Zoek nieuw vertrekadres':'Zoek nieuwe aankomst';$('addrQuery').value='';$('addrResults').innerHTML='';$('addrPreview').classList.remove('show');$('addrUseBtn').disabled=true;$('addrQuery').focus()}
async function openAssistantAddressCorrection(id){await openAssistantComplete(id);if(!ADDR_ARRIVAL)return;guideTo('assistantRouteSection');chooseAssistantRouteSide('destination')}
async function searchAssistantAddress(){let q=$('addrQuery').value.trim();if(!q){toast('Vul een adres of zoekterm in.',true);return}try{let r=await api('api/places/search-address',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});ADDR_RESULTS=r.places||[];renderAssistantAddressResults()}catch(e){toast(e.message,true)}}
function renderAssistantAddressResults(){let list=$('addrResults');list.innerHTML='';if(!ADDR_RESULTS.length){list.innerHTML='<div class="empty" style="padding:6px 0">Geen adressen gevonden.</div>';return}ADDR_RESULTS.forEach((p,i)=>{let d=document.createElement('div');d.className='known-row';d.style.cursor='pointer';d.innerHTML=`<b>${esc(p.name||p.address||'Adres')}</b><br><small>${esc(p.address||'')}</small>`;d.onclick=()=>selectAssistantAddressResult(i);list.appendChild(d)})}
function assistantRoutePayload(){let address=ADDR_SELECTED.address||ADDR_SELECTED.name,part={latitude:ADDR_SELECTED.latitude,longitude:ADDR_SELECTED.longitude,address,place_id:ADDR_SELECTED.place_id||null};return {[ADDR_SIDE]:part}}
async function selectAssistantAddressResult(i){ADDR_SELECTED=ADDR_RESULTS[i];if(!ADDR_SELECTED)return;let selected=ADDR_SELECTED.name||ADDR_SELECTED.address||'—';$('addrPreviewOrigin').textContent=ADDR_SIDE==='origin'?selected:ADDR_ARRIVAL?.origin_name||'—';$('addrPreviewLabel').textContent=ADDR_SIDE==='destination'?selected:ADDR_ARRIVAL?.destination_name||'—';$('addrPreviewDetail').textContent=ADDR_SELECTED.address||'';$('addrPreviewDistance').textContent='Bezig met berekenen…';$('addrPreviewOdo').textContent='—';$('addrUseBtn').disabled=true;$('addrPreview').classList.add('show');try{let r=await api(`api/assistant/${ADDR_ARRIVAL.id}/preview-route`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(assistantRoutePayload())});let km=r.distance_m!=null?r.distance_m/1000:null;$('addrPreviewDistance').textContent=km!=null?`${fmt(km,1)} km ${r.distance_source==='route'?'via wegroute':'GPS-schatting'}`:'Afstand nog onbekend';$('addrPreviewOdo').textContent=r.suggested_odometer!=null?`${fmt(r.suggested_odometer,0)} km`:'—';$('addrUseBtn').disabled=false}catch(e){$('addrPreviewDistance').textContent='Kon afstand niet berekenen';$('addrUseBtn').disabled=true;toast(e.message,true)}}
async function useAssistantAddressResult(){if(!ADDR_SELECTED||!ADDR_ARRIVAL){toast('Kies eerst een adres uit de resultaten.',true);return}try{await api(`api/assistant/${ADDR_ARRIVAL.id}/correct-route`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(assistantRoutePayload())});let fresh=await api('api/assistant/arrivals'),updated=(fresh.arrivals||[]).find(v=>Number(v.id)===Number(ADDR_ARRIVAL.id));if(!updated)throw new Error('Bijgewerkt voorstel niet gevonden.');ADDR_ARRIVAL=updated;ASSISTANT_ITEM=updated;updateAssistantRouteUI(updated);ADDR_SELECTED=null;ADDR_RESULTS=[];$('addrQuery').value='';await reloadData();let current=(DATA.business?.assistant?.pending||[]).find(v=>Number(v.id)===Number(updated.id));if(current){ADDR_ARRIVAL=current;ASSISTANT_ITEM=current;updateAssistantRouteUI(current)}guideTo('assistantRouteSection');toast('Route aangepast en controle bijgewerkt')}catch(e){toast(e.message,true)}}
function setAssistantType(type){ASSISTANT_TYPE='business';$('assistantBusiness').classList.toggle('active',true)}
async function openAssistantComplete(id){
  try {
    let fresh=await api('api/assistant/arrivals'),x=(fresh.arrivals||[]).find(v=>Number(v.id)===Number(id));
    if(!x){toast('Dit voorstel is al verwerkt.');reloadData();return}
    if(!x.is_next_to_review){toast('Deze rit is nog niet aan de beurt. Handel eerst de eerdere rit af.',true);return}
    ASSISTANT_ITEM=x;ASSISTANT_TYPE='business';
    let p=x.proposal||{},active=DATA.business?.active_trip,last=active?.stops?.at(-1);
    let usable=p.suggested_odometer!=null&&p.route_complete&&(!last||Number(last.odometer)===Number(p.start_odometer));
    $('assistantId').value=x.id;ADDR_ARRIVAL=x;ADDR_RESULTS=[];ADDR_SELECTED=null;updateAssistantRouteUI(x);
    $('assistantReason').textContent=`${x.date_label} · ${x.suggestion_reason||'Wordt als zakelijke rit opgeslagen.'}${x.suggested_type==='business'?' · Regelzekerheid '+Math.round(Number(x.suggestion_confidence||0)*100)+'%':''}`;
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
  ASSISTANT_TYPE='business';
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
function showTripOdoProposal(sug){let proposed=Number(sug?.suggested_odometer),tracked=Number(sug?.tracked_km||0);if(sug?.suggested_odometer==null||!sug?.active||!Number.isFinite(proposed)||tracked<=0)return false;initOdometerWheel('trip',proposed);$('tripOdoSuggestedValue').textContent=proposed.toLocaleString('nl-NL');let samples=Number(sug?.sample_count||0),sampleText=samples?` · ${samples} GPS-${samples===1?'meting':'metingen'}`:'',warning=sug?.suggestion_reliable===false&&sug?.distance_warning?` ⚠️ ${sug.distance_warning}`:'';$('tripOdoSuggestedDetail').textContent=`+ ${tracked.toLocaleString('nl-NL',{minimumFractionDigits:1,maximumFractionDigits:1})} km sinds het vorige adres${sampleText}. ${sug.calibration?.ready?'Persoonlijke correctie toegepast. ':''}Controleer bij twijfel de teller.${warning}`;$('tripOdoSuggestion').classList.add('show');$('tripOdoEditor').hidden=true;$('tripOdoStepHint').textContent='Voorstel op basis van je gereden route';return true}
function acceptTripOdoSuggestion(){$('tripOdoSuggestion').classList.remove('show');$('tripOdoEditor').hidden=true;guideTo('tripStepLocation',80)}
function editTripOdoSuggestion(){$('tripOdoSuggestion').classList.remove('show');let value=$('tripOdo').value;$('tripOdoEditor').hidden=false;initOdometerWheel('trip',value);$('tripOdoStepHint').textContent='Scroll om de berekende stand te corrigeren';setTimeout(()=>$('tripOdoEditor').scrollIntoView({behavior:'smooth',block:'center'}),80)}
let TRIP_PROPOSAL_REQUEST=0;
async function refreshTripOdoProposal(request){
  let controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  let current=()=>request===TRIP_PROPOSAL_REQUEST;
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
async function openTripPoint(mode){TRIP_MODE=mode;++TRIP_PROPOSAL_REQUEST;++TRIP_ADDRESS_REQUEST;TRIP_GPS=null;$('tripAddressChoices').innerHTML='';$('tripManualAddress').value='';$('tripOdoChecked').checked=false;TRIP_LOCATION=null;TRIP_SEGMENT_TYPE='';TRIP_SUGGESTION=null;let active=DATA.business?.active_trip;$('tripModalTitle').textContent=mode==='start'?'🚗 Ritregistratie starten':mode==='finish'?'🏁 Laatste locatie':'📍 Volgende locatie';$('tripStartFields').style.display=mode==='start'?'block':'none';$('tripFinishFields').style.display=mode==='finish'?'block':'none';$('tripSuggestionBox').classList.remove('show');$('tripDate').value=localInputNow();$('tripStopNote').value='';if(mode==='start'){$('tripPurpose').value='klantbezoek';$('tripClient').value='';$('tripTripNote').value=''};if(mode==='finish'){$('tripDeviatingRoute').value=active?.deviating_route||'';$('tripPrivateDetour').value=Number(active?.private_detour_km||0)};$('tripLocationTitle').textContent='📍 Nog geen locatie vastgelegd';$('tripLocationDetail').textContent='Tik hieronder zodra je op de juiste plek bent.';$('tripGoogleAttrib').style.display='none';$('tripSaveButton').textContent=mode==='start'?'Registratie starten':mode==='finish'?'Ritregistratie afsluiten':'Locatie opslaan';$('tripOdoSuggestion').classList.remove('show');$('tripOdoEditor').hidden=mode!=='start';$('tripOdoStepHint').textContent=mode==='start'?'Laatste stand is vooringesteld':'Berekende stand ophalen…';initOdometerWheel('trip',DATA.current_odometer??0);if(mode!=='start')await refreshTripOdoProposal(TRIP_PROPOSAL_REQUEST);openModal('tripModal');setTimeout(()=>guideTo('tripStepOdo',0),80)}
let TRIP_ADDRESS_REQUEST=0,TRIP_ADDRESSES=[],TRIP_GPS=null;
async function captureTripLocation(){let request=++TRIP_ADDRESS_REQUEST,title=$('tripLocationTitle'),detail=$('tripLocationDetail');TRIP_LOCATION=null;TRIP_GPS=null;TRIP_ADDRESSES=[];$('tripManualAddress').value='';$('tripAddressChoices').innerHTML='';title.textContent='📍 Locatie bepalen…';detail.textContent='Adressen in de buurt ophalen.';try{
 let loc=await resolveLocation();if(request!==TRIP_ADDRESS_REQUEST)return;TRIP_GPS=loc;
 let r=await api('api/location/addresses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(loc)});if(request!==TRIP_ADDRESS_REQUEST)return;TRIP_ADDRESSES=r.addresses||[];
 title.textContent='Kies het huisnummer van je afspraak';detail.textContent=TRIP_ADDRESSES.length?`${r.street} · ${TRIP_ADDRESSES.length} adressen · bron PDOK / BAG. Controleer straat en huisnummer.`:'Geen adressen gevonden. Vul straat, huisnummer en plaats handmatig in.';
 $('tripGoogleAttrib').style.display='none';TRIP_ADDRESSES.forEach((a,i)=>{let b=document.createElement('button');b.type='button';b.className='guide-next';b.textContent=`${a.address} · ${a.distance_m} m`;b.onclick=()=>chooseTripAddress(i);$('tripAddressChoices').appendChild(b)});
 }catch(e){if(request!==TRIP_ADDRESS_REQUEST)return;title.textContent=TRIP_GPS?'Adres handmatig bevestigen':'⚠️ Locatie niet vastgelegd';detail.textContent=e.message;toast(e.message,true)}}
function chooseTripAddress(index){let address=TRIP_ADDRESSES[index];if(!address||!TRIP_GPS)return;$('tripManualAddress').value=address.address;confirmTripAddress(address.address,{...TRIP_GPS,latitude:address.latitude,longitude:address.longitude,source:'pdok_confirmed'})}
async function searchTripManualAddress(){let query=$('tripManualAddress').value.trim();if(!query){toast('Vul een adres of zoekterm in.',true);return}try{let result=await api('api/places/search-address',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});let places=result.places||[],box=$('tripAddressChoices');box.innerHTML='';if(!places.length){box.innerHTML='<div class="empty">Geen adressen gevonden.</div>';return}places.slice(0,10).forEach(p=>{let b=document.createElement('button');b.type='button';b.className='guide-next';b.textContent=p.address||p.name||'Adres';b.onclick=()=>{let label=p.address||p.name||query;$('tripManualAddress').value=label;if(Number.isFinite(Number(p.latitude))&&Number.isFinite(Number(p.longitude))){confirmTripAddress(label,{latitude:Number(p.latitude),longitude:Number(p.longitude),source:'provider_confirmed'})}else{toast('Dit adres heeft geen geldige coördinaten.',true)}};box.appendChild(b)})}catch(e){toast(e.message,true)}}
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
function renderKnownPlaces(){let arr=DATA?.business?.known_places||[],box=$('knownPlacesList');box.innerHTML='';if(!arr.length){box.innerHTML='<div class="empty">Nog geen bekende plekken. Voeg bijvoorbeeld Thuis en School toe.</div>';return}arr.forEach(p=>{let r=document.createElement('div');r.className='known-row';let zone=p.ha_zone_error?`<span class="assistant-zone-err"> · HA-zone fout</span>`:p.ha_zone_id?`<span class="assistant-zone-ok"> · HA-zone ✓</span>`:'',address=p.address?esc(p.address):'Adres nog niet opgeslagen';r.innerHTML=`<div class="known-icon">${placeIcon(p.category)}</div><div><b>${esc(p.name)}</b><small class="known-address">${address}</small><small>Bij aankomst: ${placeRuleLabel(p.arrival_trip_type)} · vanaf hier naar onbekend: ${placeRuleLabel(p.unknown_departure_trip_type)} · ${p.radius_m} m${zone}</small></div><div class="known-actions"><button onclick="editKnownPlace(${p.id})">✏️</button><button onclick="removeKnownPlace(${p.id})">🗑️</button></div>`;box.appendChild(r)})}
function resetKnownPlaceLocation(){KNOWN_EDIT_LOCATION=null;$('knownAddressChoices').innerHTML='';$('knownAddressResults').innerHTML='';$('knownAddressSearch').hidden=true;$('knownAddressQuery').value='';$('knownGpsOnly').hidden=true}
function newKnownPlace(){closeModal('knownPlacesModal');$('knownId').value='';$('knownName').value='';$('knownCategory').value='other';$('knownArrival').value='ask';$('knownDepart').value='ask';$('knownRadius').value=180;$('knownRadiusVal').textContent='180 m';$('knownLat').value='';$('knownLon').value='';$('knownAddressPreview').textContent='';$('knownCoord').textContent='Locatie nog niet vastgelegd';$('knownEditTitle').textContent='📌 Nieuwe bekende plek';resetKnownPlaceLocation();openModal('knownPlaceEditModal')}
function knownPlaceLocationLabel(candidate){return candidate.address||candidate.name||'Geselecteerd adres'}
function selectKnownPlaceLocation(candidate){let lat=Number(candidate?.latitude),lon=Number(candidate?.longitude);if(!Number.isFinite(lat)||!Number.isFinite(lon)){toast('Dit adres heeft geen geldige coördinaten.',true);return}KNOWN_EDIT_LOCATION={latitude:lat,longitude:lon,address:knownPlaceLocationLabel(candidate)};$('knownLat').value=lat;$('knownLon').value=lon;$('knownAddressPreview').textContent=`Gekozen adres: ${KNOWN_EDIT_LOCATION.address}`;$('knownCoord').textContent=`Coördinaten: ${fmt(lat,5)} · ${fmt(lon,5)}`;$('knownAddressChoices').innerHTML='';$('knownAddressResults').innerHTML='';$('knownGpsOnly').hidden=true}
function renderKnownPlaceCandidates(target,candidates){let box=$(target);box.innerHTML='';(candidates||[]).slice(0,10).forEach(candidate=>{let button=document.createElement('button');button.type='button';button.className='known-row';button.innerHTML=`<b>○ ${esc(knownPlaceLocationLabel(candidate))}</b>${candidate.distance_m!=null?`<small>${Math.round(candidate.distance_m)} m</small>`:''}`;button.onclick=()=>selectKnownPlaceLocation(candidate);box.appendChild(button)})}
async function useKnownPlaceCurrentLocation(){let loc;try{toast('Huidige locatie bepalen...');loc=await resolveLocation()}catch(e){$('knownAddressChoices').innerHTML='<div class="empty">Huidige locatie kon niet worden opgehaald.</div>';toast('Huidige locatie kon niet worden opgehaald.',true);return}KNOWN_EDIT_LOCATION={latitude:loc.latitude,longitude:loc.longitude,address:''};$('knownCoord').textContent=`Huidige positie: ${fmt(loc.latitude,5)} · ${fmt(loc.longitude,5)}${loc.accuracy?` · ±${Math.round(loc.accuracy)} m`:''}`;try{let result=await api('api/location/addresses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:loc.latitude,longitude:loc.longitude})});let candidates=(result.addresses||[]).slice(0,10);renderKnownPlaceCandidates('knownAddressChoices',candidates);if(!candidates.length)$('knownAddressChoices').innerHTML='<div class="empty">Geen passend adres gevonden. Zoek handmatig of kies bewust de GPS-positie.</div>';$('knownGpsOnly').hidden=false}catch(e){$('knownAddressChoices').innerHTML='<div class="empty">Adreskandidaten konden niet worden opgehaald. Zoek handmatig of kies bewust de GPS-positie.</div>';$('knownGpsOnly').hidden=false}}
function showKnownPlaceAddressSearch(){$('knownAddressSearch').hidden=false;$('knownAddressQuery').focus()}
async function searchKnownPlaceAddress(){let query=$('knownAddressQuery').value.trim();if(!query){toast('Vul straat, huisnummer en plaats in.',true);return}try{let result=await api('api/places/search-address',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});renderKnownPlaceCandidates('knownAddressResults',result.places||[])}catch(e){toast(e.message,true)}}
function useKnownPlaceGpsOnly(){let lat=Number(KNOWN_EDIT_LOCATION?.latitude),lon=Number(KNOWN_EDIT_LOCATION?.longitude);if(!Number.isFinite(lat)||!Number.isFinite(lon)){toast('Huidige GPS-positie is niet geldig. Bepaal eerst je huidige locatie.',true);return}KNOWN_EDIT_LOCATION={latitude:lat,longitude:lon,address:''};$('knownLat').value=lat;$('knownLon').value=lon;$('knownAddressPreview').textContent='Alleen GPS-positie gekozen';$('knownCoord').textContent=`GPS-positie: ${fmt(lat,5)} · ${fmt(lon,5)}`;$('knownAddressChoices').innerHTML='';$('knownAddressResults').innerHTML='';$('knownGpsOnly').hidden=true}
async function editKnownPlace(id){let p=(DATA?.business?.known_places||[]).find(x=>Number(x.id)===Number(id));if(!p)return;$('knownId').value=p.id;$('knownName').value=p.name||'';$('knownCategory').value=p.category||'other';$('knownArrival').value=p.arrival_trip_type||'ask';$('knownDepart').value=p.unknown_departure_trip_type||'ask';$('knownRadius').value=p.radius_m||180;$('knownRadiusVal').textContent=(p.radius_m||180)+' m';$('knownLat').value=p.latitude;$('knownLon').value=p.longitude;$('knownAddressPreview').textContent=p.address||'Adres nog niet opgeslagen';$('knownCoord').textContent=`Coördinaten: ${fmt(p.latitude,5)} · ${fmt(p.longitude,5)}`;KNOWN_EDIT_LOCATION={latitude:p.latitude,longitude:p.longitude,address:p.address||''};$('knownAddressChoices').innerHTML='';$('knownAddressResults').innerHTML='';$('knownAddressSearch').hidden=true;$('knownAddressQuery').value='';$('knownGpsOnly').hidden=true;$('knownEditTitle').textContent='📌 '+p.name;closeModal('knownPlacesModal');openModal('knownPlaceEditModal');if(!p.address){try{let result=await api('api/location/addresses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:p.latitude,longitude:p.longitude})});let candidate=(result.addresses||[])[0];if(candidate){renderKnownPlaceCandidates('knownAddressChoices',[candidate]);$('knownAddressChoices').insertAdjacentHTML('afterbegin','<div class="empty">Voorstel gevonden (nog niet opgeslagen) — tik erop om te gebruiken:</div>')}}catch(e){console.warn('Adresvoorstel niet beschikbaar',e)}}}
async function saveKnownPlace(){let id=$('knownId').value,payload={name:$('knownName').value,category:$('knownCategory').value,arrival_trip_type:$('knownArrival').value,unknown_departure_trip_type:$('knownDepart').value,radius_m:$('knownRadius').value,latitude:$('knownLat').value,longitude:$('knownLon').value,address:KNOWN_EDIT_LOCATION?.address||''};try{await api(id?`api/known-places/${id}`:'api/known-places',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('knownPlaceEditModal');toast('Bekende plek opgeslagen');await reloadData();openKnownPlaces()}catch(e){toast(e.message,true)}}
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
let LIVE_TRIP_REFRESH_BUSY=false;
async function refreshActiveTripLiveStatus(){if(document.hidden||LIVE_TRIP_REFRESH_BUSY||document.querySelector('.modal.show')||!DATA?.business?.active_trip||!$('bizHero')?.classList.contains('active-trip'))return;LIVE_TRIP_REFRESH_BUSY=true;try{let status=await api('api/business/odometer-suggestion');if(!status.active)return;let tracked=$('liveTripTrackedKm'),base=$('liveTripBaseOdometer'),suggested=$('liveTripSuggestedOdometer'),warning=$('liveTripWarning');if(tracked)tracked.textContent=fmt(status.tracked_km||0,1)+' km';if(base)base.textContent=status.base_odometer!=null?fmt(status.base_odometer,0)+' km':'—';if(suggested)suggested.textContent=status.suggested_odometer!=null?fmt(status.suggested_odometer,0)+' km':'—';if(warning)warning.innerHTML=status.suggestion_reliable===false&&status.distance_warning?`<br>⚠️ ${esc(status.distance_warning)}`:''}catch(e){console.warn('Live ritstatus vernieuwen mislukt',e)}finally{LIVE_TRIP_REFRESH_BUSY=false}}
setInterval(refreshActiveTripLiveStatus,5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshVisibleDashboard();refreshActiveTripLiveStatus()}});
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
            if path == '/api/places/search-address':
                query = str(payload.get('query') or '').strip()[:200]
                if not query:
                    raise ValueError('Vul een adres of zoekterm in.')
                return json_response(self, {'places': google_places_text_search(query)})
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
            md = re.fullmatch(r'/api/assistant/(\d+)/correct-destination', path)
            if md:
                return json_response(self, {'arrival': correct_assistant_arrival_destination(int(md.group(1)), payload)})
            mr = re.fullmatch(r'/api/assistant/(\d+)/correct-route', path)
            if mr:
                return json_response(self, {'arrival': correct_assistant_arrival_route(int(mr.group(1)), payload)})
            mv = re.fullmatch(r'/api/assistant/(\d+)/preview-destination', path)
            if mv:
                return json_response(self, preview_assistant_arrival_destination(int(mv.group(1)), payload))
            mw = re.fullmatch(r'/api/assistant/(\d+)/preview-route', path)
            if mw:
                return json_response(self, preview_assistant_arrival_route(int(mw.group(1)), payload))
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
        except assistant.AssistantArrivalQueueError as e:
            return json_response(self, {
                'error': str(e),
                'code': e.code,
                'blocking_arrival_id': e.blocking_arrival_id,
                'blocking_departure_at': e.blocking_departure_at,
            }, 409)
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
            except assistant.AssistantArrivalQueueError as e:
                return json_response(self, {
                    'error': str(e),
                    'code': e.code,
                    'blocking_arrival_id': e.blocking_arrival_id,
                    'blocking_departure_at': e.blocking_departure_at,
                }, 409)
            except ValueError as e:
                return json_response(self, {'error': str(e)}, 400)
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
        km_rate = float(get_settings().get('km_reimbursement_rate') or 0.25)
        output=io.StringIO(); writer=csv.writer(output,delimiter=';')
        writer.writerow(['rit_id','ritsoort_samenvatting','status','doel','klant','start_datum_tijd','eind_datum_tijd','totaal_km','zakelijk_km','prive_km','prive_omrijkm','afwijkende_route','stop_nr','stop_datum_tijd','kilometerstand','segment_km','segment_ritsoort','classificatie_bron','suggestie','suggestie_reden','latitude','longitude','google_place_id','bekende_plek','locatie_label_live','notitie','km_reimbursement_rate','reimbursement_eur'])
        for enriched in business_trips_for_period(period):
            if enriched.get('trip_type') == 'business':
                reimbursement = round(float(enriched.get('km') or 0) * km_rate, 2)
                for stop in enriched.get('stops',[]):
                    writer.writerow([enriched['id'],enriched.get('trip_type_label') or '',enriched['status'],enriched.get('purpose') or '',enriched.get('client') or '',enriched.get('started_at') or '',enriched.get('ended_at') or '',enriched.get('km') or 0,enriched.get('business_km') or 0,enriched.get('private_km') or 0,enriched.get('private_detour_km') or 0,enriched.get('deviating_route') or '',stop.get('sequence_no'),stop.get('created_at'),stop.get('odometer'),stop.get('segment_km') or 0,stop.get('segment_trip_type_label') or '',stop.get('segment_classification_source') or '',stop.get('segment_suggested_type') or '',stop.get('segment_suggestion_reason') or '',stop.get('latitude') if stop.get('latitude') is not None else '',stop.get('longitude') if stop.get('longitude') is not None else '',stop.get('place_id') or '',stop.get('known_place_name') or '',stop.get('location_label') or '',stop.get('note') or '',f'{km_rate:.2f}',f'{reimbursement:.2f}'])
        data=output.getvalue().encode('utf-8-sig'); self.send_response(200); self.send_header('Content-Type','text/csv; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename="zakelijke_kilometerregistratie_export.csv"'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)

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
