from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Mapping


def _provider(dependencies: Mapping[str, Any], name: str) -> Any:
    provider = dependencies.get(name)
    if provider is None:
        raise RuntimeError(f'Assistant dependency ontbreekt: {name}')
    return provider


def assistant_config(*, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    st = _provider(dependencies, 'get_settings')()
    mode = str(st.get('assistant_mode') or 'assistant').strip().lower()
    if mode not in {'manual', 'assistant', 'autopilot'}:
        mode = 'assistant'
    return {'enabled': str(st.get('assistant_enabled', '0')).lower() in {'1', 'true', 'yes', 'on', 'aan'}, 'mode': mode, 'auto_confidence': _provider(dependencies, 'setting_int')('assistant_auto_confidence', 95, 70, 100), 'location_entity': str(st.get('assistant_location_entity') or '').strip(), 'notify_service': str(st.get('assistant_notify_service') or '').strip(), 'sync_zones': str(st.get('assistant_sync_zones', '1')).lower() in {'1', 'true', 'yes', 'on', 'aan'}, 'unknown_stops': str(st.get('assistant_unknown_stops', '1')).lower() in {'1', 'true', 'yes', 'on', 'aan'}, 'check_seconds': _provider(dependencies, 'setting_int')('assistant_check_seconds', 20, 10, 300), 'unknown_stop_minutes': _provider(dependencies, 'setting_int')('assistant_unknown_stop_minutes', 4, 2, 30), 'fast_stop_seconds': _provider(dependencies, 'setting_int')('assistant_fast_stop_seconds', 30, 20, 180), 'min_trip_m': _provider(dependencies, 'setting_int')('assistant_min_trip_m', 500, 100, 10000), 'push_provinces': ['Groningen', 'Drenthe']}

def assistant_state_get(key: str, default: Any=None, *, dependencies: Mapping[str, Any]) -> Any:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute('SELECT value FROM assistant_state WHERE key=?', (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row['value'])
    except Exception:
        return row['value']

def assistant_state_set(key: str, value: Any, *, dependencies: Mapping[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        con.execute('INSERT INTO assistant_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, raw))
        con.commit()

def diagnostic_event(event: str, *, dependencies: Mapping[str, Any], **details: Any) -> None:
    """Bounded, persistent support log. Never include raw API data or errors."""
    allowed = {'tracked_m', 'minimum_m', 'accuracy_m', 'speed_m_s', 'stationary_seconds', 'threshold_seconds', 'province_allowed', 'samples', 'position_changed', 'incomplete', 'ha_state_age_seconds'}
    safe = {k: v for k, v in details.items() if k in allowed and (v is None or isinstance(v, (int, float, bool)))}
    try:
        with _provider(dependencies, 'DIAGNOSTIC_LOCK'):
            rows = _provider(dependencies, 'assistant_state_get')('diagnostic_log', []) or []
            now = _provider(dependencies, 'iso_local')()
            if rows and rows[-1]['event'] == event and (rows[-1]['details'] == safe):
                if (_provider(dependencies, 'parse_dt')(now) - _provider(dependencies, 'parse_dt')(rows[-1]['at'])).total_seconds() < 60:
                    return
            rows.append({'at': now, 'event': event, 'details': safe})
            cutoff = time.time() - 48 * 3600
            rows = [r for r in rows[-500:] if _provider(dependencies, 'parse_dt')(r['at']).timestamp() >= cutoff]
            _provider(dependencies, 'assistant_state_set')('diagnostic_log', rows)
    except Exception:
        pass

def diagnostic_report(*, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    cfg = _provider(dependencies, 'assistant_config')()
    rows = _provider(dependencies, 'assistant_state_get')('diagnostic_log', []) or []
    cutoff = time.time() - 48 * 3600
    return {'version': _provider(dependencies, 'APP_VERSION'), 'generated_at': _provider(dependencies, 'iso_local')(), 'note': 'GPS-polls zijn geen bewijs van een nieuwe iPhone-locatiemeting. HA-acceptatie is geen afleverbevestiging.', 'config': {k: cfg.get(k) for k in ('enabled', 'mode', 'min_trip_m', 'fast_stop_seconds')}, 'tracker_configured': bool(cfg.get('location_entity')), 'notification_configured': bool(cfg.get('notify_service')), 'events': [r for r in rows[-500:] if _provider(dependencies, 'parse_dt')(r['at']).timestamp() >= cutoff]}

def calibration_vehicle(*, dependencies: Mapping[str, Any]) -> str:
    settings = _provider(dependencies, 'get_settings')()
    return str(settings.get('license_plate') or settings.get('vehicle_name') or 'default').strip().upper()

def distance_calibration(*, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded median, never trained from simply accepting a proposal."""
    import statistics
    enabled = _provider(dependencies, 'setting_bool')('distance_learning_enabled', True)
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        rows = con.execute('SELECT gps_km,actual_km FROM distance_calibration WHERE vehicle=? ORDER BY id DESC LIMIT 30', (_provider(dependencies, 'calibration_vehicle')(),)).fetchall()
    ratios = [float(r['actual_km']) / float(r['gps_km']) for r in rows]
    stable = len(ratios) >= 5 and statistics.median([abs(x - statistics.median(ratios)) for x in ratios]) <= 0.04
    factor = max(0.9, min(1.1, statistics.median(ratios))) if enabled and stable else 1.0
    return {'enabled': enabled, 'samples': len(ratios), 'ready': bool(enabled and stable), 'factor': round(factor, 4)}

def learn_distance(source: str, gps_km: float, actual_km: float, samples: int, checked: bool, *, con: sqlite3.Connection, dependencies: Mapping[str, Any]) -> None:
    if not checked or not _provider(dependencies, 'setting_bool')('distance_learning_enabled', True):
        return
    if not math.isfinite(gps_km) or not math.isfinite(actual_km) or gps_km < 5 or (samples < 5):
        return
    if not 0.85 <= actual_km / gps_km <= 1.15:
        return
    con.execute('INSERT OR IGNORE INTO distance_calibration(vehicle,source,gps_km,actual_km,created_at) VALUES(?,?,?,?,?)', (_provider(dependencies, 'calibration_vehicle')(), source, gps_km, actual_km, _provider(dependencies, 'iso_local')()))

def advance_draft_route(runtime: dict[str, Any], lat: float, lon: float, accuracy: float | None, now: datetime, *, dependencies: Mapping[str, Any]) -> None:
    """Accumulate a draft without creating any official trip or odometer event."""
    if not runtime.get('departure_at'):
        return
    prev_lat, prev_lon = (_provider(dependencies, 'to_float')(runtime.get('route_lat')), _provider(dependencies, 'to_float')(runtime.get('route_lon')))
    if prev_lat is None or prev_lon is None:
        runtime.update(route_lat=lat, route_lon=lon, route_at=_provider(dependencies, 'iso_local')(now))
        return
    dt = max(1, (now - _provider(dependencies, 'parse_dt')(runtime.get('route_at') or _provider(dependencies, 'iso_local')(now))).total_seconds())
    step = _provider(dependencies, 'haversine_m')(lat, lon, prev_lat, prev_lon)
    if step < 8:
        return
    if dt > 300 or (accuracy is not None and accuracy > 100) or step > 60 * dt + 100:
        runtime['route_incomplete'] = True
    elif step >= 35:
        runtime['route_m'] = float(runtime.get('route_m') or 0) + step
        runtime['route_samples'] = int(runtime.get('route_samples') or 0) + 1
    runtime.update(route_lat=lat, route_lon=lon, route_at=_provider(dependencies, 'iso_local')(now))

def arrival_proposal(row: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """
    Voorstel voor eindtellerstand van dit ritvoorstel.

    Als de bestemming handmatig is gecorrigeerd (destination_manually_corrected)
    en er een corrected_destination_distance_m bekend is, wordt DIE afstand
    gebruikt in plaats van de oorspronkelijke (mogelijk foutieve) GPS-tracking
    naar de oude bestemming:
      - bron 'route' (echte Google-wegafstand): GEEN GPS-kalibratiefactor
        toepassen, die factor is alleen bedoeld om ruwe telefoon-GPS-afstand
        te corrigeren, niet een al nauwkeurige wegafstand;
      - bron 'gps': de bestaande GPS-kalibratie/leerlogica blijft gelden.
    """
    base = _provider(dependencies, 'latest_odometer_before')(row.get('departure_at') or row['detected_at'])
    calibration = _provider(dependencies, 'distance_calibration')()
    corrected_m = _provider(dependencies, 'to_float')(row.get('corrected_route_distance_m'))
    if corrected_m is None:
        corrected_m = _provider(dependencies, 'to_float')(row.get('corrected_destination_distance_m'))
    if (row.get('origin_manually_corrected') or row.get('destination_manually_corrected')) and corrected_m is not None:
        corrected_km = corrected_m / 1000
        source = str(row.get('destination_distance_source') or 'gps')
        usable = bool(base is not None and corrected_km > 0)
        if source == 'route':
            suggested = round(base + corrected_km) if usable else None
        else:
            suggested = round(base + corrected_km * calibration['factor']) if usable else None
        return {'start_odometer': base, 'gps_km': round(corrected_km, 2), 'suggested_odometer': suggested, 'route_complete': usable, 'calibration': calibration, 'distance_source': source}
    snapshot = _provider(dependencies, 'assistant_state_get')(f"arrival_route_{row['id']}", {}) or {}
    raw_km = float(snapshot.get('route_m') or 0) / 1000
    usable = bool(base is not None and raw_km > 0 and (int(snapshot.get('route_samples') or 0) >= 3) and (not snapshot.get('route_incomplete')))
    return {'start_odometer': base, 'gps_km': round(raw_km, 2), 'suggested_odometer': round(base + raw_km * calibration['factor']) if usable else None, 'route_complete': usable, 'calibration': calibration, 'distance_source': 'gps'}


class AssistantArrivalQueueError(ValueError):
    """A mutation targeted an arrival that is not next in the review queue."""

    code = 'earlier_arrival_pending'

    def __init__(self, blocking: Mapping[str, Any]):
        self.blocking_arrival_id = int(blocking['id'])
        self.blocking_departure_at = blocking.get('departure_at') or blocking.get('detected_at')
        super().__init__('Er staat nog een eerdere rit open. Handel die rit eerst af voordat je deze rit controleert.')


def oldest_open_assistant_arrival(*, dependencies: Mapping[str, Any], con: Any=None) -> dict[str, Any] | None:
    """Return the oldest pending/confirmed arrival using parsed timestamps."""
    def select(connection: Any) -> dict[str, Any] | None:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM assistant_arrivals WHERE status IN ('pending','confirmed')"
        )]
        if not rows:
            return None
        return min(
            rows,
            key=lambda row: (
                _provider(dependencies, 'parse_dt')(row.get('departure_at') or row['detected_at']),
                int(row['id']),
            ),
        )

    if con is not None:
        return select(con)
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as connection:
        return select(connection)


def assert_assistant_arrival_is_next(arrival_id: int, *, dependencies: Mapping[str, Any], con: Any=None) -> dict[str, Any]:
    """Validate queue ownership immediately before an arrival mutation."""
    def assert_next(connection: Any) -> dict[str, Any]:
        row = connection.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
        if not row:
            raise ValueError('Ritsuggestie niet gevonden.')
        current = dict(row)
        if current.get('status') not in ('pending', 'confirmed'):
            raise ValueError('Deze ritsuggestie kan niet meer worden aangepast.')
        blocking = oldest_open_assistant_arrival(dependencies=dependencies, con=connection)
        if blocking and int(blocking['id']) != int(arrival_id):
            raise AssistantArrivalQueueError(blocking)
        return current

    if con is not None:
        return assert_next(con)
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as connection:
        return assert_next(connection)


def assistant_arrivals(limit: int=12, include_done: bool=False, *, dependencies: Mapping[str, Any]) -> list[dict[str, Any]]:
    where = '' if include_done else "WHERE status IN ('pending','confirmed')"
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        rows = [dict(r) for r in con.execute(f'SELECT * FROM assistant_arrivals {where}')]
    open_rows = [row for row in rows if row.get('status') in ('pending', 'confirmed')]
    open_rows.sort(key=lambda row: (
        _provider(dependencies, 'parse_dt')(row.get('departure_at') or row['detected_at']),
        int(row['id']),
    ))
    for position, row in enumerate(open_rows, start=1):
        row['queue_position'] = position
        row['is_next_to_review'] = position == 1
        row['blocked_by_arrival_id'] = None if position == 1 else int(open_rows[position - 2]['id'])
    done_rows = [row for row in rows if row.get('status') not in ('pending', 'confirmed')]
    for row in done_rows:
        row['queue_position'] = None
        row['is_next_to_review'] = False
        row['blocked_by_arrival_id'] = None
    rows = (open_rows + done_rows)[:max(1, min(100, int(limit)))]
    out = []
    for r in rows:
        origin = _provider(dependencies, 'known_place_by_id')(r.get('origin_known_place_id'))
        dest = _provider(dependencies, 'known_place_by_id')(r.get('destination_known_place_id'))
        effective_origin = _provider(dependencies, '_assistant_arrival_effective_origin')(r)
        r['origin_name'] = effective_origin['label'] if effective_origin['manually_corrected'] else (origin or {}).get('name') or 'Onbekende vertrekplek'
        effective = _provider(dependencies, '_assistant_arrival_effective_destination')(r)
        r['destination_name'] = effective['label'] if effective['manually_corrected'] else (dest or {}).get('name') or r.get('destination_label') or 'Onbekende bestemming'
        r['suggested_type_label'] = _provider(dependencies, 'trip_type_label')(str(r.get('suggested_type') or '')) if r.get('suggested_type') else ''
        r['confirmed_type_label'] = _provider(dependencies, 'trip_type_label')(str(r.get('confirmed_type') or '')) if r.get('confirmed_type') else ''
        r['date_label'] = _provider(dependencies, 'parse_dt')(r['detected_at']).strftime('%d-%m %H:%M')
        r['start_odometer'] = _provider(dependencies, 'latest_odometer_before')(r.get('departure_at') or r.get('detected_at'))
        r['proposal'] = _provider(dependencies, 'arrival_proposal')(r)
        out.append(r)
    return out

def assistant_runtime_public(*, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _provider(dependencies, 'assistant_state_get')('runtime', {}) or {}
    current = _provider(dependencies, 'known_place_by_id')(runtime.get('current_place_id'))
    departed = _provider(dependencies, 'known_place_by_id')(runtime.get('departed_from_place_id'))
    return {'seeded': bool(runtime.get('seeded')), 'current_place': (current or {}).get('name') or '', 'departed_from': (departed or {}).get('name') or '', 'draft_active': bool(runtime.get('departure_at') and (not runtime.get('unknown_at_stop'))), 'draft_km': round(float(runtime.get('route_m') or 0) / 1000, 1), 'last_seen_at': runtime.get('last_seen_at') or '', 'last_error': _provider(dependencies, 'assistant_state_get')('last_error', '') or '', 'ws_connected': bool(_provider(dependencies, 'assistant_state_get')('ws_connected', False))}

def _assistant_suggestion(origin_place_id: int | None, dest_place_id: int | None, lat: float, lon: float, *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    origin = _provider(dependencies, 'known_place_by_id')(origin_place_id)
    dest = _provider(dependencies, 'known_place_by_id')(dest_place_id)
    if dest and str(dest.get('arrival_trip_type') or 'ask') in {'business', 'private'}:
        t = str(dest['arrival_trip_type'])
        return {'suggested_type': t, 'reason': f"Bestemming {dest['name']} staat als {_provider(dependencies, 'trip_type_label')(t).lower()} ingesteld", 'confidence': 0.98}
    if origin and (not dest) and (str(origin.get('unknown_departure_trip_type') or 'ask') in {'business', 'private'}):
        t = str(origin['unknown_departure_trip_type'])
        return {'suggested_type': t, 'reason': f"Vanaf {origin['name']} naar onbekende bestemming: {_provider(dependencies, 'trip_type_label')(t)}", 'confidence': 0.9}
    memory = _provider(dependencies, '_route_memory_suggestion')(origin_place_id, lat, lon)
    if memory:
        return memory
    return {'suggested_type': '', 'reason': 'Geen vaste regel gevonden — kies zelf', 'confidence': 0.0}

def create_assistant_arrival(origin_place_id: int | None, destination_place_id: int | None, lat: float, lon: float, accuracy: float | None, departure_at: str | None, destination_label: str='', route_snapshot: dict[str, Any] | None=None, *, dependencies: Mapping[str, Any]) -> dict[str, Any] | None:
    now = _provider(dependencies, 'iso_local')()
    cutoff = _provider(dependencies, 'iso_local')(_provider(dependencies, 'now_local')() - timedelta(minutes=20))
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        recent = [dict(r) for r in con.execute("SELECT * FROM assistant_arrivals WHERE detected_at>=? AND status IN ('pending','confirmed') ORDER BY id DESC", (cutoff,))]
    for r in recent:
        if _provider(dependencies, 'haversine_m')(lat, lon, float(r['destination_latitude']), float(r['destination_longitude'])) < 180:
            return r
    dest = _provider(dependencies, 'known_place_by_id')(destination_place_id)
    if dest:
        destination_label = str(dest.get('name') or destination_label)
    elif not destination_label:
        try:
            geo = _provider(dependencies, 'google_reverse_geocode')(lat, lon)
            destination_label = str(geo.get('address') or '')[:180]
        except Exception:
            destination_label = ''
    if not destination_label:
        destination_label = f'{lat:.5f}, {lon:.5f}'
    suggestion = _provider(dependencies, '_assistant_suggestion')(origin_place_id, destination_place_id, lat, lon)
    cfg = _provider(dependencies, 'assistant_config')()
    suggested_type = _provider(dependencies, 'normalize_segment_type')(suggestion.get('suggested_type'))
    auto_confirm = cfg.get('mode') == 'autopilot' and suggested_type and (float(suggestion.get('confidence') or 0) * 100 >= int(cfg.get('auto_confidence') or 95))
    status = 'confirmed' if auto_confirm else 'pending'
    classification_source = 'autopilot' if auto_confirm else None
    handled_at = now if auto_confirm else None
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        cur = con.execute('\n            INSERT INTO assistant_arrivals(\n                detected_at,departure_at,origin_known_place_id,destination_known_place_id,\n                destination_latitude,destination_longitude,destination_accuracy,destination_label,\n                suggested_type,suggestion_reason,suggestion_confidence,status,confirmed_type,\n                classification_source,handled_at\n            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\n        ', (now, departure_at, origin_place_id, destination_place_id, lat, lon, accuracy, destination_label[:180], suggested_type or None, str(suggestion.get('reason') or '')[:240], float(suggestion.get('confidence') or 0), status, suggested_type if auto_confirm else None, classification_source, handled_at))
        arrival_id = int(cur.lastrowid)
        _provider(dependencies, 'audit')('assistant_arrival', 'assistant', arrival_id, {'origin_place_id': origin_place_id, 'destination_place_id': destination_place_id, 'destination': destination_label, 'suggestion': suggestion, 'autopilot': auto_confirm}, con=con)
        con.commit()
    if route_snapshot:
        _provider(dependencies, 'assistant_state_set')(f'arrival_route_{arrival_id}', {key: route_snapshot.get(key) for key in ('route_m', 'route_samples', 'route_incomplete', 'departure_at')})
    item = next((x for x in _provider(dependencies, 'assistant_arrivals')(30) if int(x['id']) == arrival_id), None)
    if item:
        threading.Thread(target=_provider(dependencies, 'send_assistant_notification'), args=(item,), daemon=True).start()
    return item

def confirm_assistant_arrival(arrival_id: int, trip_type: str, source: str='app', *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    t = _provider(dependencies, 'normalize_segment_type')(trip_type)
    if not t:
        raise ValueError('Kies Privé of Zakelijk.')
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        assert_assistant_arrival_is_next(arrival_id, dependencies=dependencies, con=con)
        con.execute("UPDATE assistant_arrivals SET status='confirmed',confirmed_type=?,classification_source=?,handled_at=? WHERE id=?", (t, source[:40], _provider(dependencies, 'iso_local')(), int(arrival_id)))
        _provider(dependencies, 'audit')('assistant_confirm', 'assistant', int(arrival_id), {'trip_type': t, 'source': source}, con=con)
        con.commit()
    return next((x for x in _provider(dependencies, 'assistant_arrivals')(50, True) if int(x['id']) == int(arrival_id)), {})

def dismiss_assistant_arrival(arrival_id: int, *, dependencies: Mapping[str, Any]) -> None:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        assert_assistant_arrival_is_next(arrival_id, dependencies=dependencies, con=con)
        con.execute("UPDATE assistant_arrivals SET status='dismissed',handled_at=? WHERE id=?", (_provider(dependencies, 'iso_local')(), int(arrival_id)))
        _provider(dependencies, 'audit')('assistant_dismiss', 'assistant', int(arrival_id), {}, con=con)
        con.commit()

def _assistant_arrival_effective_destination(r: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """
    Bepaal de EFFECTIEVE bestemming van dit ritvoorstel: de bestemming die
    consequent gebruikt moet worden bij het definitief opslaan (nieuwe
    automatische rit, aankomst aan actieve rit, trip_stop, PDF,
    rittenoverzicht, ...).

    Als de gebruiker de bestemming handmatig heeft gecorrigeerd
    (destination_manually_corrected=1 met geldige corrected_destination_*
    coördinaten), is de gecorrigeerde bestemming leidend. De oorspronkelijke
    (mogelijk foutieve) GPS-bestemming wordt uitsluitend als audit-informatie
    teruggegeven (original_latitude/original_longitude/original_label) en
    NOOIT opnieuw als actuele aankomst opgeslagen.
    """
    corrected_lat = _provider(dependencies, 'to_float')(r.get('corrected_destination_latitude'))
    corrected_lon = _provider(dependencies, 'to_float')(r.get('corrected_destination_longitude'))
    original_lat = _provider(dependencies, 'to_float')(r.get('destination_latitude'))
    original_lon = _provider(dependencies, 'to_float')(r.get('destination_longitude'))
    original_label = str(r.get('destination_label') or '').strip() or None
    manually_corrected = bool(r.get('destination_manually_corrected')) and corrected_lat is not None and (corrected_lon is not None)
    if manually_corrected:
        return {'latitude': corrected_lat, 'longitude': corrected_lon, 'label': str(r.get('corrected_destination_label') or '').strip() or original_label, 'place_id': str(r.get('corrected_destination_place_id') or '').strip() or None, 'distance_m': _provider(dependencies, 'to_float')(r.get('corrected_destination_distance_m')), 'distance_source': str(r.get('destination_distance_source') or 'gps'), 'manually_corrected': True, 'original_latitude': original_lat, 'original_longitude': original_lon, 'original_label': original_label}
    return {'latitude': original_lat, 'longitude': original_lon, 'label': original_label, 'place_id': None, 'distance_m': None, 'distance_source': None, 'manually_corrected': False, 'original_latitude': None, 'original_longitude': None, 'original_label': None}

def _assistant_arrival_effective_origin(r: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """Return the corrected origin when present while preserving its original source."""
    origin_place = _provider(dependencies, 'known_place_by_id')(r.get('origin_known_place_id')) or {}
    original_lat = _provider(dependencies, 'to_float')(origin_place.get('latitude'))
    original_lon = _provider(dependencies, 'to_float')(origin_place.get('longitude'))
    original_label = str(origin_place.get('name') or '').strip() or None
    corrected_lat = _provider(dependencies, 'to_float')(r.get('corrected_origin_latitude'))
    corrected_lon = _provider(dependencies, 'to_float')(r.get('corrected_origin_longitude'))
    manually_corrected = bool(r.get('origin_manually_corrected')) and corrected_lat is not None and corrected_lon is not None
    if manually_corrected:
        return {'latitude': corrected_lat, 'longitude': corrected_lon, 'label': str(r.get('corrected_origin_label') or '').strip() or original_label, 'place_id': str(r.get('corrected_origin_place_id') or '').strip() or None, 'manually_corrected': True, 'original_latitude': original_lat, 'original_longitude': original_lon, 'original_label': original_label}
    return {'latitude': original_lat, 'longitude': original_lon, 'label': original_label, 'place_id': None, 'manually_corrected': False, 'original_latitude': None, 'original_longitude': None, 'original_label': None}

def _assistant_arrival_origin_coords(r: dict[str, Any], *, dependencies: Mapping[str, Any]) -> tuple[float, float] | None:
    """
    Bepaal de meest betrouwbare vertreklocatie die bij dit ritvoorstel hoort.
    Dit is dezelfde bron die _complete_assistant_arrival() gebruikt om het
    startpunt van de rit vast te leggen, zodat de route-oorsprong hier
    consistent is met de uiteindelijk opgeslagen rit:
      1. De laatste stop van een nog actieve zakelijke rit (indien aanwezig).
      2. De bekende vertrekplek (origin_known_place_id) die bij het
         voorstel is vastgelegd toen het werd aangemaakt.
    Er wordt bewust GEEN gebruik gemaakt van een timestamp-match op
    trip_stops.created_at<=departure_at: in echte data valt dat niet
    gegarandeerd samen met de daadwerkelijke vertrekstop.
    Retourneert None als er geen betrouwbare vertreklocatie is; de aanroeper
    mag dan NOOIT (0,0) of de oude bestemming als origin gebruiken.
    """
    effective = _provider(dependencies, '_assistant_arrival_effective_origin')(r)
    if effective['manually_corrected']:
        return (effective['latitude'], effective['longitude'])
    active = _provider(dependencies, 'active_business_trip')()
    if active and active.get('stops'):
        last_stop = active['stops'][-1]
        lat = _provider(dependencies, 'to_float')(last_stop.get('latitude'))
        lon = _provider(dependencies, 'to_float')(last_stop.get('longitude'))
        if lat is not None and lon is not None:
            return (lat, lon)
    origin_place = _provider(dependencies, 'known_place_by_id')(r.get('origin_known_place_id'))
    if origin_place:
        lat = _provider(dependencies, 'to_float')(origin_place.get('latitude'))
        lon = _provider(dependencies, 'to_float')(origin_place.get('longitude'))
        if lat is not None and lon is not None:
            return (lat, lon)
    return None

def _assistant_arrival_destination_distance(r: dict[str, Any], arrival_id: int, dest_lat: float, dest_lon: float, *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """
    Bereken de routeafstand van de betrouwbare vertreklocatie van dit
    ritvoorstel naar de (nieuw gekozen) bestemming. Gedeelde logica voor
    zowel de niet-muterende preview als de daadwerkelijke correctie, zodat
    beide gegarandeerd dezelfde oorsprong (nooit de oude bestemming, nooit
    (0,0)) en dezelfde afstand opleveren.

    Als de Google Routes-call mislukt of niet beschikbaar is, wordt de
    hemelsbrede (haversine) afstand die get_route_distance() dan intern
    berekent NOOIT gebruikt als vervanging voor een werkelijk gereden
    GPS-routeafstand. In plaats daarvan wordt teruggevallen op de al bekende,
    door de telefoon gemeten afstand (arrival_route_<id>.route_m); is die er
    niet, dan is de afstand expliciet onbekend (None) met bron 'gps'.
    """
    origin_coords = _provider(dependencies, '_assistant_arrival_origin_coords')(r)
    if origin_coords is not None:
        origin_lat, origin_lon = origin_coords
        route_info = _provider(dependencies, 'get_route_distance')(origin_lat, origin_lon, dest_lat, dest_lon)
        if route_info.get('type') == 'route':
            distance_m = route_info.get('distance_m')
            distance_source = 'route'
        else:
            snapshot = _provider(dependencies, 'assistant_state_get')(f'arrival_route_{arrival_id}', {}) or {}
            distance_m = _provider(dependencies, 'to_float')(snapshot.get('route_m'))
            distance_source = 'gps'
    else:
        origin_lat = origin_lon = None
        snapshot = _provider(dependencies, 'assistant_state_get')(f'arrival_route_{arrival_id}', {}) or {}
        distance_m = _provider(dependencies, 'to_float')(snapshot.get('route_m'))
        distance_source = 'gps'
    return {'distance_m': distance_m, 'distance_source': distance_source, 'origin_latitude': origin_lat, 'origin_longitude': origin_lon, 'origin_available': origin_coords is not None}

def preview_assistant_arrival_destination(arrival_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """
    Niet-muterende preview van een bestemmingscorrectie. Berekent de
    routeafstand (oorspronkelijke vertreklocatie -> nieuw gekozen bestemming,
    NOOIT vanaf de oude bestemming) en de bijbehorende voorgestelde
    eindtellerstand, zonder enig databaseveld te wijzigen.
    """
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
    if not row:
        raise ValueError('Ritsuggestie niet gevonden.')
    r = dict(row)
    if r.get('status') not in ('pending', 'confirmed'):
        raise ValueError('Deze ritsuggestie kan niet meer worden aangepast.')
    dest_lat = _provider(dependencies, 'to_float')(payload.get('latitude'))
    dest_lon = _provider(dependencies, 'to_float')(payload.get('longitude'))
    if dest_lat is None or dest_lon is None:
        raise ValueError('Ongeldige doelcoördinaten.')
    if not -90 <= dest_lat <= 90 or not -180 <= dest_lon <= 180:
        raise ValueError('Doelcoördinaten buiten bereik.')
    distance = _provider(dependencies, '_assistant_arrival_destination_distance')(r, arrival_id, dest_lat, dest_lon)
    distance_m = distance['distance_m']
    distance_source = distance['distance_source']
    base = _provider(dependencies, 'latest_odometer_before')(r.get('departure_at') or r.get('detected_at'))
    calibration = _provider(dependencies, 'distance_calibration')()
    suggested_odometer = None
    if base is not None and distance_m is not None and (distance_m > 0):
        km = distance_m / 1000
        if distance_source == 'route':
            suggested_odometer = round(base + km)
        else:
            suggested_odometer = round(base + km * calibration['factor'])
    return {'distance_m': round(distance_m) if distance_m is not None else None, 'distance_source': distance_source, 'start_odometer': base, 'suggested_odometer': suggested_odometer, 'origin_available': distance['origin_available']}

def correct_assistant_arrival_destination(arrival_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """
    Handmatig corrigeer de gesuggeerde bestemming van een automatisch voorstel.
    Recalculeer de afstand en bijgewerkte tellerstand.

    BELANGRIJK: de routeafstand loopt ALTIJD van de oorspronkelijke
    vertreklocatie van het ritvoorstel naar de NIEUW gekozen bestemming.
    De oude (foutieve) voorgestelde bestemming wordt nooit als route-origin
    gebruikt — die dient uitsluitend als audit-informatie ('original_destination').
    """
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
    if not row:
        raise ValueError('Ritsuggestie niet gevonden.')
    r = dict(row)
    if r.get('status') not in ('pending', 'confirmed'):
        raise ValueError('Deze ritsuggestie kan niet meer worden aangepast.')
    dest_lat = _provider(dependencies, 'to_float')(payload.get('latitude'))
    dest_lon = _provider(dependencies, 'to_float')(payload.get('longitude'))
    dest_label = str(payload.get('address') or '').strip()[:180]
    dest_place_id = str(payload.get('place_id') or '').strip()[:255] or None
    if dest_lat is None or dest_lon is None:
        raise ValueError('Ongeldige doelcoördinaten.')
    if not -90 <= dest_lat <= 90 or not -180 <= dest_lon <= 180:
        raise ValueError('Doelcoördinaten buiten bereik.')
    old_dest_lat = _provider(dependencies, 'to_float')(r.get('destination_latitude'))
    old_dest_lon = _provider(dependencies, 'to_float')(r.get('destination_longitude'))
    old_dest_label = str(r.get('destination_label') or '')
    original_snapshot = _provider(dependencies, 'assistant_state_get')(f'arrival_route_{arrival_id}', {}) or {}
    original_gps_distance_m = _provider(dependencies, 'to_float')(original_snapshot.get('route_m'))
    distance = _provider(dependencies, '_assistant_arrival_destination_distance')(r, arrival_id, dest_lat, dest_lon)
    distance_m = distance['distance_m']
    distance_source = distance['distance_source']
    origin_lat = distance['origin_latitude']
    origin_lon = distance['origin_longitude']
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        assert_assistant_arrival_is_next(arrival_id, dependencies=dependencies, con=con)
        con.execute('\n            UPDATE assistant_arrivals\n            SET corrected_destination_latitude=?,\n                corrected_destination_longitude=?,\n                corrected_destination_label=?,\n                corrected_destination_distance_m=?,\n                corrected_destination_place_id=?,\n                destination_distance_source=?,\n                destination_manually_corrected=1\n            WHERE id=?\n        ', (dest_lat, dest_lon, dest_label, round(distance_m) if distance_m is not None else None, dest_place_id, distance_source, int(arrival_id)))
        _provider(dependencies, 'audit')('assistant_correct_destination', 'assistant', int(arrival_id), {'original_destination': {'lat': old_dest_lat, 'lon': old_dest_lon, 'label': old_dest_label, 'distance_m': round(original_gps_distance_m) if original_gps_distance_m is not None else None}, 'corrected_destination': {'lat': dest_lat, 'lon': dest_lon, 'label': dest_label, 'place_id': dest_place_id, 'distance_m': round(distance_m) if distance_m is not None else None}, 'route_origin': {'lat': origin_lat, 'lon': origin_lon} if distance['origin_available'] else None, 'distance_source': distance_source, 'destination_manually_corrected': True}, con=con)
        con.commit()
    return next((x for x in _provider(dependencies, 'assistant_arrivals')(50, True) if int(x['id']) == int(arrival_id)), {})

def _route_correction_part(payload: dict[str, Any], side: str, *, dependencies: Mapping[str, Any]) -> dict[str, Any] | None:
    part = payload.get(side)
    if part is None:
        return None
    if not isinstance(part, dict):
        raise ValueError(f'Ongeldige {side}correctie.')
    lat = _provider(dependencies, 'to_float')(part.get('latitude'))
    lon = _provider(dependencies, 'to_float')(part.get('longitude'))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f'Ongeldige {side}coördinaten.')
    return {'latitude': lat, 'longitude': lon, 'label': str(part.get('address') or '').strip()[:180], 'place_id': str(part.get('place_id') or '').strip()[:255] or None}

def preview_assistant_arrival_route(arrival_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
    if not row:
        raise ValueError('Ritsuggestie niet gevonden.')
    r = dict(row)
    if r.get('status') not in ('pending', 'confirmed'):
        raise ValueError('Deze ritsuggestie kan niet meer worden aangepast.')
    origin, destination = (_route_correction_part(payload, 'origin', dependencies=dependencies), _route_correction_part(payload, 'destination', dependencies=dependencies))
    if origin:
        r.update(corrected_origin_latitude=origin['latitude'], corrected_origin_longitude=origin['longitude'], corrected_origin_label=origin['label'], corrected_origin_place_id=origin['place_id'], origin_manually_corrected=1)
    if destination:
        r.update(corrected_destination_latitude=destination['latitude'], corrected_destination_longitude=destination['longitude'], corrected_destination_label=destination['label'], corrected_destination_place_id=destination['place_id'], destination_manually_corrected=1)
    effective_destination = _provider(dependencies, '_assistant_arrival_effective_destination')(r)
    if effective_destination['latitude'] is None or effective_destination['longitude'] is None:
        raise ValueError('Bestemming van deze aankomst is onbekend.')
    distance = _provider(dependencies, '_assistant_arrival_destination_distance')(r, arrival_id, effective_destination['latitude'], effective_destination['longitude'])
    distance_m, source = distance['distance_m'], distance['distance_source']
    base = _provider(dependencies, 'latest_odometer_before')(r.get('departure_at') or r.get('detected_at'))
    suggested = None
    if base is not None and distance_m is not None and distance_m > 0:
        km = distance_m / 1000
        suggested = round(base + km) if source == 'route' else round(base + km * _provider(dependencies, 'distance_calibration')()['factor'])
    return {'distance_m': round(distance_m) if distance_m is not None else None, 'distance_source': source, 'start_odometer': base, 'suggested_odometer': suggested, 'origin_available': distance['origin_available']}

def correct_assistant_arrival_route(arrival_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute('SELECT * FROM assistant_arrivals WHERE id=?', (int(arrival_id),)).fetchone()
    if not row:
        raise ValueError('Ritsuggestie niet gevonden.')
    r = dict(row)
    if r.get('status') not in ('pending', 'confirmed'):
        raise ValueError('Deze ritsuggestie kan niet meer worden aangepast.')
    origin, destination = (_route_correction_part(payload, 'origin', dependencies=dependencies), _route_correction_part(payload, 'destination', dependencies=dependencies))
    if not origin and not destination:
        raise ValueError('Kies een vertrekadres of aankomstadres.')
    preview = preview_assistant_arrival_route(arrival_id, payload, dependencies=dependencies)
    assignments, values = [], []
    if origin:
        assignments.extend(('corrected_origin_latitude=?', 'corrected_origin_longitude=?', 'corrected_origin_label=?', 'corrected_origin_place_id=?', 'origin_manually_corrected=1'))
        values.extend((origin['latitude'], origin['longitude'], origin['label'], origin['place_id']))
    if destination:
        assignments.extend(('corrected_destination_latitude=?', 'corrected_destination_longitude=?', 'corrected_destination_label=?', 'corrected_destination_place_id=?', 'destination_manually_corrected=1'))
        values.extend((destination['latitude'], destination['longitude'], destination['label'], destination['place_id']))
    assignments.extend(('corrected_route_distance_m=?', 'destination_distance_source=?'))
    values.extend((preview['distance_m'], preview['distance_source'], int(arrival_id)))
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        assert_assistant_arrival_is_next(arrival_id, dependencies=dependencies, con=con)
        con.execute(f"UPDATE assistant_arrivals SET {','.join(assignments)} WHERE id=?", values)
        _provider(dependencies, 'audit')('assistant_correct_route', 'assistant', int(arrival_id), {'original_origin': _provider(dependencies, '_assistant_arrival_effective_origin')(r), 'original_destination': _provider(dependencies, '_assistant_arrival_effective_destination')(r), 'corrected_origin': origin, 'corrected_destination': destination, 'distance_m': preview['distance_m'], 'distance_source': preview['distance_source']}, con=con)
        con.commit()
    return next((x for x in _provider(dependencies, 'assistant_arrivals')(50, True) if int(x['id']) == int(arrival_id)), {})

def complete_assistant_arrival(arrival_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    with _provider(dependencies, 'DB_LOCK'):
        return _provider(dependencies, '_complete_assistant_arrival')(arrival_id, payload)

def _complete_assistant_arrival(arrival_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        r = assert_assistant_arrival_is_next(arrival_id, dependencies=dependencies, con=con)
    trip_type = _provider(dependencies, 'normalize_segment_type')(payload.get('trip_type')) or _provider(dependencies, 'normalize_segment_type')(r.get('confirmed_type')) or _provider(dependencies, 'normalize_segment_type')(r.get('suggested_type'))
    if not trip_type:
        raise ValueError('Kies Privé of Zakelijk.')
    end_odo = _provider(dependencies, 'to_float')(payload.get('odometer'))
    if end_odo is None or not math.isfinite(end_odo) or end_odo < 0:
        raise ValueError('Vul de kilometerstand bij aankomst in.')
    active = _provider(dependencies, 'active_business_trip')()
    if active and active.get('stops') and (_provider(dependencies, 'parse_dt')(active['stops'][-1]['created_at']) >= _provider(dependencies, 'parse_dt')(r['detected_at'])):
        raise ValueError('Deze aankomst is ouder dan de laatste opgeslagen stop. Controleer de ritgeschiedenis.')
    if active and active.get('stops') and r.get('departure_at') and (_provider(dependencies, 'parse_dt')(active['stops'][-1]['created_at']) > _provider(dependencies, 'parse_dt')(r['departure_at'])):
        raise ValueError('Een deel van dit voorstel is al geregistreerd. Controleer de ritgeschiedenis.')
    dest = _provider(dependencies, '_assistant_arrival_effective_destination')(r)
    if dest['latitude'] is None or dest['longitude'] is None:
        raise ValueError('Bestemming van deze aankomst is onbekend.')
    point = {'odometer': end_odo, 'created_at': r['detected_at'], 'latitude': dest['latitude'], 'longitude': dest['longitude'], 'location_accuracy': _provider(dependencies, 'to_float')(r.get('destination_accuracy')) if not dest['manually_corrected'] else None, 'location_source': 'background_assistant', 'place_id': dest['place_id'], 'manual_label': str(dest['label'] or '')[:120] or None, 'note': 'Automatisch herkende aankomst' + (' (adres handmatig gecorrigeerd)' if dest['manually_corrected'] else ''), 'known_place_id': r.get('destination_known_place_id') if not dest['manually_corrected'] else None}
    destination_audit = None
    if dest['manually_corrected']:
        orig_snapshot = _provider(dependencies, 'assistant_state_get')(f'arrival_route_{arrival_id}', {}) or {}
        destination_audit = {'original_latitude': dest['original_latitude'], 'original_longitude': dest['original_longitude'], 'original_address': dest['original_label'], 'original_distance_m': _provider(dependencies, 'to_float')(orig_snapshot.get('route_m')), 'distance_source': dest['distance_source'], 'manually_corrected': True}
    if active:
        result = _provider(dependencies, 'add_business_stop')({'odometer': end_odo, 'created_at': r['detected_at'], 'latitude': point['latitude'], 'longitude': point['longitude'], 'location_accuracy': point['location_accuracy'], 'location_source': point['location_source'], 'manual_label': point['manual_label'], 'note': point['note'], 'segment_trip_type': trip_type}, finish=payload.get('finish') is True, destination_audit=destination_audit)
        trip_id = int((result.get('trip') or {}).get('id') or active['id'])
    else:
        origin = _provider(dependencies, '_assistant_arrival_effective_origin')(r)
        if origin['latitude'] is None or origin['longitude'] is None:
            raise ValueError('Startpunt van deze automatische rit is onbekend. Gebruik de gewone ritregistratie.')
        start_odo = _provider(dependencies, 'to_float')(payload.get('start_odometer'))
        if start_odo is None:
            start_odo = _provider(dependencies, 'latest_odometer_before')(r.get('departure_at') or r.get('detected_at'))
        if start_odo is None:
            raise ValueError('Vul ook de kilometerstand bij vertrek in.')
        if not math.isfinite(start_odo) or start_odo < 0:
            raise ValueError('Vul een geldige vertrekstand in.')
        if end_odo < start_odo:
            raise ValueError('Aankomst-kilometerstand is lager dan de vertrekstand.')
        start_at = r.get('departure_at') or _provider(dependencies, 'iso_local')(_provider(dependencies, 'parse_dt')(r['detected_at']) - timedelta(minutes=1))
        with _provider(dependencies, 'db')() as con:
            overlap = con.execute('SELECT id FROM business_trips WHERE started_at<? AND COALESCE(ended_at,?)>? LIMIT 1', (r['detected_at'], r['detected_at'], start_at)).fetchone()
        if overlap:
            raise ValueError('Deze aankomst overlapt een opgeslagen rit. Controleer de ritgeschiedenis om dubbeltelling te voorkomen.')
        for timestamp, odo in ((start_at, start_odo), (r['detected_at'], end_odo)):
            valid, message = _provider(dependencies, 'validate_odometer')(timestamp, odo)
            if not valid:
                raise ValueError(message)
        start_point = {'odometer': start_odo, 'created_at': start_at, 'latitude': origin['latitude'], 'longitude': origin['longitude'], 'location_accuracy': None, 'location_source': 'background_assistant', 'place_id': origin['place_id'], 'manual_label': str(origin['label'] or '')[:120] or None, 'note': 'Automatisch herkend vertrek' + (' (adres handmatig gecorrigeerd)' if origin['manually_corrected'] else ''), 'known_place_id': r.get('origin_known_place_id') if not origin['manually_corrected'] else None}
        suggestion = {'suggested_type': _provider(dependencies, 'normalize_segment_type')(r.get('suggested_type')), 'reason': str(r.get('suggestion_reason') or ''), 'confidence': float(r.get('suggestion_confidence') or 0)}
        with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
            cur = con.execute("\n                INSERT INTO business_trips(started_at,ended_at,status,purpose,client,note,trip_type,private_detour_km,modified_at)\n                VALUES(?,?,'completed',NULL,NULL,?,?,0,?)\n            ", (start_at, r['detected_at'], 'Automatisch herkende rit', trip_type, _provider(dependencies, 'iso_local')()))
            trip_id = int(cur.lastrowid)
            _provider(dependencies, '_insert_trip_stop')(con, trip_id, start_point, 0, 'Automatische rit start')
            _provider(dependencies, '_insert_trip_stop')(con, trip_id, point, 1, f"Automatische rit einde ({_provider(dependencies, 'trip_type_label')(trip_type)})", trip_type, suggestion, destination_audit=destination_audit)
            _provider(dependencies, 'remember_segment')(start_point, point, trip_type, con=con)
            _provider(dependencies, 'audit')('assistant_complete', 'trip', trip_id, {'assistant_arrival_id': int(arrival_id), 'trip_type': trip_type, 'origin_manually_corrected': origin['manually_corrected'], 'effective_origin': {'lat': origin['latitude'], 'lon': origin['longitude'], 'label': origin['label']}, 'original_origin': {'lat': origin['original_latitude'], 'lon': origin['original_longitude'], 'label': origin['original_label']} if origin['manually_corrected'] else None, 'destination_manually_corrected': dest['manually_corrected'], 'effective_destination': {'lat': dest['latitude'], 'lon': dest['longitude'], 'label': dest['label']}, 'original_destination': {'lat': dest['original_latitude'], 'lon': dest['original_longitude'], 'label': dest['original_label']} if dest['manually_corrected'] else None}, con=con)
            con.commit()
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        snapshot = _provider(dependencies, 'assistant_state_get')(f'arrival_route_{arrival_id}', {}) or {}
        if not active and (not snapshot.get('route_incomplete')) and (not dest['manually_corrected']):
            _provider(dependencies, 'learn_distance')(f'arrival:{arrival_id}', float(snapshot.get('route_m') or 0) / 1000, end_odo - start_odo, int(snapshot.get('route_samples') or 0), payload.get('odometer_checked') is True, con=con)
        con.execute("UPDATE assistant_arrivals SET status='completed',confirmed_type=?,classification_source=COALESCE(classification_source,'app'),handled_at=?,odometer=?,trip_id=? WHERE id=?", (trip_type, _provider(dependencies, 'iso_local')(), end_odo, trip_id, int(arrival_id)))
        con.commit()
    _provider(dependencies, 'publish_sensors_async')()
    return {'ok': True, 'trip_id': trip_id}

def province_allowed_for_push(lat: float, lon: float, *, dependencies: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Provincies waar de gebruiker stopmeldingen wil ontvangen."""
    geo = _provider(dependencies, 'google_reverse_geocode')(lat, lon)
    province = str(geo.get('province') or '').strip()
    allowed = province.lower() in {'groningen', 'drenthe', 'overijssel'}
    return (allowed, province, geo)

def trip_distance_tracking_public(*, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """Publieke schatting voor de actieve rit, gebaseerd op achtergrond-GPS.

    Release 14.00:
    - Altijd tellerstandsuggestie berekenen wanneer base_odometer geldig is en tracked_m > 0
    - Onvolledige GPS toont waarschuwing maar behoudt suggestie
    - Weinig samples: suggestie zichtbaar maar onbetrouwbaar gemarkeerd
    """
    trip = _provider(dependencies, 'active_business_trip')()
    if not trip or not trip.get('stops'):
        _provider(dependencies, 'diagnostic_event')('geen_actieve_rit_met_startpunt')
        return {'active': False, 'tracked_km': 0.0, 'suggested_odometer': None, 'sample_count': 0}
    last = trip['stops'][-1]
    state = _provider(dependencies, 'assistant_state_get')('trip_distance_tracking', {}) or {}
    same_trip = int(state.get('trip_id') or -1) == int(trip['id'])
    same_stop = int(state.get('stop_id') or -1) == int(last.get('id') or -2)
    tracked_m = float(state.get('segment_m') or 0.0) if same_trip and same_stop else 0.0
    base = float(last['odometer'])
    calibration = _provider(dependencies, 'distance_calibration')()

    # 14.00: Altijd suggestie berekenen als base geldig is en tracked_m > 0
    suggested = None
    suggestion_reliable = True
    distance_warning = None

    if same_trip and same_stop and tracked_m > 0:
        suggested = round(base + tracked_m / 1000.0 * calibration['factor'])

        # Markeer als onbetrouwbaar wanneer:
        # - incomplete GPS;
        # - sample_count < 3
        if state.get('incomplete'):
            suggestion_reliable = False
            distance_warning = 'GPS-route mogelijk onderbroken — controleer de tellerstand.'
        elif int(state.get('sample_count') or 0) < 3:
            suggestion_reliable = False
            distance_warning = 'Onvoldoende GPS-samples — gelieve te controleren.'

    return {
        'active': True,
        'trip_id': int(trip['id']),
        'stop_id': int(last.get('id') or 0),
        'base_odometer': base,
        'stop_prompt': state.get('stop_prompt') if same_trip and same_stop else None,
        'calibration': calibration,
        'tracked_km': round(tracked_m / 1000.0, 1),
        'suggested_odometer': suggested,
        'suggestion_reliable': suggestion_reliable,
        'distance_warning': distance_warning,
        'sample_count': int(state.get('sample_count') or 0) if same_trip and same_stop else 0,
        'last_update': state.get('last_update') if same_trip and same_stop else None
    }

def reset_trip_distance_tracking(trip: dict[str, Any] | None=None, *, dependencies: Mapping[str, Any]) -> None:
    if not trip or not trip.get('stops'):
        _provider(dependencies, 'assistant_state_set')('trip_distance_tracking', {})
        return
    last = trip['stops'][-1]
    _provider(dependencies, 'assistant_state_set')('trip_distance_tracking', {'trip_id': int(trip['id']), 'stop_id': int(last.get('id') or 0), 'segment_m': 0.0, 'sample_count': 0, 'incomplete': False, 'last_lat': _provider(dependencies, 'to_float')(last.get('latitude')), 'last_lon': _provider(dependencies, 'to_float')(last.get('longitude')), 'last_update': _provider(dependencies, 'iso_local')(), 'stationary_since': None, 'prompted': False, 'last_prompt_at': None})

def track_active_trip_distance(loc: dict[str, Any], cfg: dict[str, Any], *, dependencies: Mapping[str, Any]) -> None:
    """Best-effort routeafstand vanaf de laatste handmatige stop.

    Dit is een suggestie, geen vervanging van de echte kilometerteller.
    """
    trip = _provider(dependencies, 'active_business_trip')()
    if not trip or not trip.get('stops'):
        _provider(dependencies, 'assistant_state_set')('trip_distance_tracking', {})
        return
    last = trip['stops'][-1]
    state = _provider(dependencies, 'assistant_state_get')('trip_distance_tracking', {}) or {}
    trip_id = int(trip['id'])
    stop_id = int(last.get('id') or 0)
    if int(state.get('trip_id') or -1) != trip_id or int(state.get('stop_id') or -1) != stop_id:
        _provider(dependencies, 'reset_trip_distance_tracking')(trip)
        state = _provider(dependencies, 'assistant_state_get')('trip_distance_tracking', {}) or {}
    lat, lon = (_provider(dependencies, 'to_float')(loc.get('latitude')), _provider(dependencies, 'to_float')(loc.get('longitude')))
    if lat is None or lon is None:
        _provider(dependencies, 'diagnostic_event')('gps_coordinaten_ontbreken')
        return
    accuracy = _provider(dependencies, 'to_float')(loc.get('accuracy'))
    if accuracy is not None and accuracy > 250:
        _provider(dependencies, 'diagnostic_event')('gps_te_onnauwkeurig', accuracy_m=round(accuracy))
        return
    now = _provider(dependencies, 'now_local')()
    prev_lat, prev_lon = (_provider(dependencies, 'to_float')(state.get('last_lat')), _provider(dependencies, 'to_float')(state.get('last_lon')))
    prev_dt = _provider(dependencies, 'parse_dt')(state.get('last_update')) if state.get('last_update') else now
    step = _provider(dependencies, 'haversine_m')(float(lat), float(lon), prev_lat, prev_lon) if prev_lat is not None and prev_lon is not None else 0.0
    dt_s = max(1.0, (now - prev_dt).total_seconds())
    speed = _provider(dependencies, 'to_float')(loc.get('speed'))
    if step >= 8 and dt_s > 300:
        state['incomplete'] = True
    max_step = 60.0 * dt_s + 250.0
    moving = speed is not None and speed > 3.0 or step >= 35.0
    if 8.0 <= step <= max_step and moving:
        state['segment_m'] = float(state.get('segment_m') or 0.0) + step
        state['sample_count'] = int(state.get('sample_count') or 0) + 1
    if moving:
        state['stationary_since'] = None
        state['stop_prompt'] = None
        if step > 220:
            state['prompted'] = False
    elif not state.get('stationary_since'):
        state['stationary_since'] = _provider(dependencies, 'iso_local')(now)
    if prev_lat is None or prev_lon is None or step >= 8.0:
        state['last_lat'] = float(lat)
        state['last_lon'] = float(lon)
        state['last_update'] = _provider(dependencies, 'iso_local')(now)
    _provider(dependencies, 'assistant_state_set')('trip_distance_tracking', state)
    tracked_m = float(state.get('segment_m') or 0.0)
    since_raw = state.get('stationary_since')
    ha_age = None
    if loc.get('ha_last_updated'):
        try:
            ha_age = max(0, round((now - _provider(dependencies, 'parse_dt')(loc['ha_last_updated'])).total_seconds()))
        except (ValueError, TypeError):
            pass
    _provider(dependencies, 'diagnostic_event')('achtergrondcontrole', tracked_m=round(tracked_m), minimum_m=cfg.get('min_trip_m'), accuracy_m=accuracy, speed_m_s=speed, position_changed=step >= 8, samples=state.get('sample_count', 0), incomplete=bool(state.get('incomplete')), ha_state_age_seconds=ha_age)
    if state.get('prompted') or tracked_m < float(cfg.get('min_trip_m') or 500) or (not since_raw):
        _provider(dependencies, 'diagnostic_event')('stop_al_aangeboden' if state.get('prompted') else 'afstand_onder_minimum' if tracked_m < float(cfg.get('min_trip_m') or 500) else 'beweging_gedetecteerd')
        return
    since = _provider(dependencies, 'parse_dt')(str(since_raw))
    elapsed = (now - since).total_seconds()
    if elapsed < min(10, int(cfg.get('fast_stop_seconds') or 30)):
        _provider(dependencies, 'diagnostic_event')('wachten_op_stilstand', stationary_seconds=round(elapsed))
        return
    allowed, province, geo = _provider(dependencies, 'province_allowed_for_push')(float(lat), float(lon))
    if not allowed:
        _provider(dependencies, 'diagnostic_event')('provincie_onbekend_of_niet_toegestaan', province_allowed=False)
        return
    threshold = 10 if province.lower() == 'overijssel' else int(cfg.get('fast_stop_seconds') or 30)
    if elapsed < threshold:
        _provider(dependencies, 'diagnostic_event')('wachten_op_stilstand', stationary_seconds=round(elapsed), threshold_seconds=threshold)
        return
    state['stop_prompt'] = {'id': f"{trip['id']}:{last['id']}:{_provider(dependencies, 'iso_local')(now)}", 'trip_id': int(trip['id']), 'province': province, 'address': str(geo.get('address') or province), 'created_at': _provider(dependencies, 'iso_local')(now)}
    state['prompted'] = True
    state['last_prompt_at'] = _provider(dependencies, 'iso_local')(now)
    _provider(dependencies, 'assistant_state_set')('trip_distance_tracking', state)
    _provider(dependencies, 'diagnostic_event')('stop_herkend', stationary_seconds=round(elapsed), threshold_seconds=threshold)
    sent = _provider(dependencies, 'send_active_trip_stop_notification')(trip, tracked_m, province, geo)
    _provider(dependencies, 'diagnostic_event')('stop_push_geaccepteerd_door_ha' if sent else 'stop_push_mislukt')

def send_active_trip_stop_notification(trip: dict[str, Any], tracked_m: float, province: str, geo: dict[str, Any], *, dependencies: Mapping[str, Any]) -> bool:
    cfg = _provider(dependencies, 'assistant_config')()
    service = cfg.get('notify_service') or ''
    if not service.startswith('notify.') or not trip.get('stops'):
        return False
    svc = service.split('.', 1)[1]
    km = tracked_m / 1000.0
    address = str(geo.get('address') or province or 'huidige locatie')
    payload = {'title': f'🏁 Rit & Tank · gestopt in {province}', 'message': f'Wil je je actieve rit opslaan? Je lijkt gestopt bij {address}. Achtergrondroute: ca. {km:.1f} km. Open Rit & Tank om de tellerstand te controleren en de rit af te sluiten.', 'data': {'tag': f"rit_tank_stop_{int(trip['id'])}", 'url': 'https://rit.huisplanadvies.nl', 'actions': [{'action': 'OPEN', 'title': 'Open Rit & Tank', 'uri': 'https://rit.huisplanadvies.nl'}]}}
    try:
        _provider(dependencies, 'ha_post')(f'services/notify/{svc}', payload)
        return True
    except Exception as exc:
        _provider(dependencies, 'assistant_state_set')('last_error', f'Stopmelding: {exc}')
        return False

def send_assistant_notification(item: dict[str, Any], *, dependencies: Mapping[str, Any]) -> bool:
    cfg = _provider(dependencies, 'assistant_config')()
    service = cfg.get('notify_service') or ''
    if not service.startswith('notify.'):
        return False
    lat = _provider(dependencies, 'to_float')(item.get('destination_latitude'))
    lon = _provider(dependencies, 'to_float')(item.get('destination_longitude'))
    if lat is None or lon is None:
        return False
    allowed, province, _geo = _provider(dependencies, 'province_allowed_for_push')(float(lat), float(lon))
    if not allowed:
        return False
    svc = service.split('.', 1)[1]
    suggested = _provider(dependencies, 'normalize_segment_type')(item.get('suggested_type'))
    suggestion_text = f" · voorstel: {_provider(dependencies, 'trip_type_label')(suggested)}" if suggested else ''
    message = f"{item.get('origin_name', 'Vertrek')} → {item.get('destination_name', 'Bestemming')}{suggestion_text}. Bevestig ritsoort; kilometerstand vul je later in Rit & Tank in."
    aid = int(item['id'])
    payload = {'title': f"🚗 Rit & Tank · {item.get('destination_name', 'Aankomst')} · {province}", 'message': message, 'data': {'tag': f'rit_tank_arrival_{aid}', 'url': 'https://rit.huisplanadvies.nl', 'actions': [{'action': f'RITTANK_PRIVATE_{aid}', 'title': 'Privé'}, {'action': f'RITTANK_BUSINESS_{aid}', 'title': 'Zakelijk'}, {'action': 'OPEN', 'title': 'Open Rit & Tank', 'uri': 'https://rit.huisplanadvies.nl'}]}}
    try:
        _provider(dependencies, 'ha_post')(f'services/notify/{svc}', payload)
        with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
            con.execute('UPDATE assistant_arrivals SET notification_sent=1 WHERE id=?', (aid,))
            con.commit()
        return True
    except Exception as exc:
        _provider(dependencies, 'assistant_state_set')('last_error', f'Pushmelding: {exc}')
        return False

def send_assistant_test_notification(*, dependencies: Mapping[str, Any]) -> None:
    cfg = _provider(dependencies, 'assistant_config')()
    service = cfg.get('notify_service') or ''
    if not service.startswith('notify.'):
        raise ValueError('Kies eerst een Home Assistant mobiele meldingsservice.')
    svc = service.split('.', 1)[1]
    _provider(dependencies, 'ha_post')(f'services/notify/{svc}', {'title': '🚗 Rit & Tank', 'message': 'Achtergrond-ritassistent is gekoppeld. Meldingen komen op dit apparaat binnen.', 'data': {'url': 'https://rit.huisplanadvies.nl'}})

def _assistant_action_listener(*, dependencies: Mapping[str, Any]) -> None:
    backoff = 3
    while True:
        try:
            ws = _provider(dependencies, '_ha_ws_open')(20)
            ws.settimeout(300)
            ws.send(json.dumps({'id': 1, 'type': 'subscribe_events', 'event_type': 'mobile_app_notification_action'}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get('id') == 1 and msg.get('type') == 'result':
                    if not msg.get('success'):
                        raise RuntimeError('Kon niet op notification actions abonneren.')
                    break
            backoff = 3
            _provider(dependencies, 'assistant_state_set')('ws_connected', True)
            while True:
                msg = json.loads(ws.recv())
                if msg.get('type') != 'event':
                    continue
                event = msg.get('event') or {}
                data = event.get('data') or {}
                action = str(data.get('action') or '')
                m = re.fullmatch('RITTANK_(PRIVATE|BUSINESS)_(\\d+)', action)
                if m:
                    trip_type = 'private' if m.group(1) == 'PRIVATE' else 'business'
                    try:
                        _provider(dependencies, 'confirm_assistant_arrival')(int(m.group(2)), trip_type, 'notification')
                    except Exception as exc:
                        _provider(dependencies, 'assistant_state_set')('last_error', f'Notificatieactie: {exc}')
        except Exception as exc:
            _provider(dependencies, 'assistant_state_set')('ws_connected', False)
            _provider(dependencies, 'assistant_state_set')('last_error', f'WebSocket: {exc}')
            time.sleep(backoff)
            backoff = min(60, backoff * 2)

def _process_assistant_location(loc: dict[str, Any], cfg: dict[str, Any], *, dependencies: Mapping[str, Any]) -> None:
    lat, lon = (float(loc['latitude']), float(loc['longitude']))
    accuracy = _provider(dependencies, 'to_float')(loc.get('accuracy'))
    if accuracy is not None and accuracy > 500:
        _provider(dependencies, 'assistant_state_set')('last_error', f'Locatie te onnauwkeurig (±{accuracy:.0f} m).')
        return
    now = _provider(dependencies, 'now_local')()
    matched = _provider(dependencies, 'match_known_place')(lat, lon)
    runtime = _provider(dependencies, 'assistant_state_get')('runtime', {}) or {}
    previous_lat = _provider(dependencies, 'to_float')(runtime.get('last_lat'))
    previous_lon = _provider(dependencies, 'to_float')(runtime.get('last_lon'))
    previous_seen = runtime.get('last_seen_at')
    runtime['last_seen_at'] = _provider(dependencies, 'iso_local')(now)
    runtime['last_lat'] = lat
    runtime['last_lon'] = lon
    runtime['last_accuracy'] = accuracy
    if not runtime.get('seeded'):
        runtime.update({'seeded': True, 'current_place_id': int(matched['id']) if matched else None, 'departed_from_place_id': None, 'departure_at': None, 'entry_candidate_id': None, 'entry_candidate_since': None, 'stationary_since': None, 'stationary_lat': lat, 'stationary_lon': lon, 'unknown_at_stop': False})
        _provider(dependencies, 'assistant_state_set')('runtime', runtime)
        return
    current_id = runtime.get('current_place_id')
    matched_id = int(matched['id']) if matched else None
    _provider(dependencies, 'advance_draft_route')(runtime, lat, lon, accuracy, now)
    if current_id and matched_id == int(current_id):
        runtime['entry_candidate_id'] = None
        runtime['entry_candidate_since'] = None
        runtime['stationary_since'] = None
        runtime['unknown_at_stop'] = False
        runtime['stationary_lat'] = lat
        runtime['stationary_lon'] = lon
        _provider(dependencies, 'assistant_state_set')('runtime', runtime)
        return
    if current_id and matched_id != int(current_id):
        runtime.update(route_m=0.0, route_samples=0, route_incomplete=False, route_lat=previous_lat, route_lon=previous_lon, route_at=previous_seen or _provider(dependencies, 'iso_local')(now))
        runtime['departed_from_place_id'] = int(current_id)
        runtime['departure_at'] = _provider(dependencies, 'iso_local')(now)
        _provider(dependencies, 'advance_draft_route')(runtime, lat, lon, accuracy, now)
        runtime['current_place_id'] = None
        runtime['stationary_since'] = None
        runtime['stationary_lat'] = lat
        runtime['stationary_lon'] = lon
        runtime['unknown_at_stop'] = False
        current_id = None
    if matched_id:
        if runtime.get('entry_candidate_id') != matched_id:
            runtime['entry_candidate_id'] = matched_id
            runtime['entry_candidate_since'] = _provider(dependencies, 'iso_local')(now)
        else:
            since = _provider(dependencies, 'parse_dt')(runtime.get('entry_candidate_since') or _provider(dependencies, 'iso_local')(now))
            if (now - since).total_seconds() >= max(10, min(60, cfg['check_seconds'])):
                _provider(dependencies, 'create_assistant_arrival')(int(runtime['departed_from_place_id']) if runtime.get('departed_from_place_id') else None, matched_id, lat, lon, accuracy, runtime.get('departure_at'), route_snapshot=runtime)
                runtime['current_place_id'] = matched_id
                runtime['departed_from_place_id'] = None
                runtime['departure_at'] = None
                runtime['entry_candidate_id'] = None
                runtime['entry_candidate_since'] = None
                runtime['stationary_since'] = None
                runtime['unknown_at_stop'] = False
        _provider(dependencies, 'assistant_state_set')('runtime', runtime)
        return
    runtime['entry_candidate_id'] = None
    runtime['entry_candidate_since'] = None
    if cfg.get('unknown_stops') and runtime.get('departed_from_place_id') and (not runtime.get('unknown_at_stop')):
        origin = _provider(dependencies, 'known_place_by_id')(runtime.get('departed_from_place_id'))
        if origin:
            from_origin = _provider(dependencies, 'haversine_m')(lat, lon, float(origin['latitude']), float(origin['longitude']))
            if from_origin >= float(cfg.get('min_trip_m') or 500):
                stat_lat = _provider(dependencies, 'to_float')(runtime.get('stationary_lat'))
                stat_lon = _provider(dependencies, 'to_float')(runtime.get('stationary_lon'))
                moved = _provider(dependencies, 'haversine_m')(lat, lon, stat_lat, stat_lon) if stat_lat is not None and stat_lon is not None else 9999
                speed = _provider(dependencies, 'to_float')(loc.get('speed'))
                stationary = moved <= 90 and (speed is None or speed <= 3.0)
                if stationary:
                    if not runtime.get('stationary_since'):
                        runtime['stationary_since'] = _provider(dependencies, 'iso_local')(now)
                    else:
                        since = _provider(dependencies, 'parse_dt')(runtime['stationary_since'])
                        if (now - since).total_seconds() >= int(cfg.get('unknown_stop_minutes') or 4) * 60:
                            _provider(dependencies, 'create_assistant_arrival')(int(runtime['departed_from_place_id']), None, lat, lon, accuracy, runtime.get('departure_at'), route_snapshot=runtime)
                            runtime['unknown_at_stop'] = True
                            runtime['stationary_since'] = None
                else:
                    runtime['stationary_since'] = None
                runtime['stationary_lat'] = lat
                runtime['stationary_lon'] = lon
    if runtime.get('unknown_at_stop') and previous_lat is not None and (previous_lon is not None):
        if _provider(dependencies, 'haversine_m')(lat, lon, previous_lat, previous_lon) > 220:
            runtime['unknown_at_stop'] = False
            runtime['departed_from_place_id'] = None
            runtime['departure_at'] = _provider(dependencies, 'iso_local')(now)
            runtime.update(route_m=0.0, route_samples=0, route_incomplete=True, route_lat=lat, route_lon=lon, route_at=_provider(dependencies, 'iso_local')(now))
            runtime['stationary_lat'] = lat
            runtime['stationary_lon'] = lon
    _provider(dependencies, 'assistant_state_set')('runtime', runtime)

def _assistant_location_worker(*, dependencies: Mapping[str, Any]) -> None:
    last_sync = 0.0
    while True:
        cfg = _provider(dependencies, 'assistant_config')()
        delay = min(10, int(cfg.get('check_seconds') or 10))
        if not cfg.get('enabled') or cfg.get('mode') == 'manual' or (not cfg.get('location_entity')):
            _provider(dependencies, 'diagnostic_event')('assistent_uit' if not cfg.get('enabled') else 'handmatige_modus' if cfg.get('mode') == 'manual' else 'tracker_ontbreekt')
            time.sleep(max(10, delay))
            continue
        try:
            if cfg.get('sync_zones') and time.time() - last_sync > 1800:
                _provider(dependencies, 'sync_all_known_place_zones')()
                last_sync = time.time()
            loc = _provider(dependencies, 'location_from_entity')(str(cfg['location_entity']))
            _provider(dependencies, 'track_active_trip_distance')(loc, cfg)
            _provider(dependencies, '_process_assistant_location')(loc, cfg)
            _provider(dependencies, 'assistant_state_set')('last_error', '')
        except Exception as exc:
            _provider(dependencies, 'diagnostic_event')('achtergrondverwerking_mislukt')
            _provider(dependencies, 'assistant_state_set')('last_error', str(exc)[:300])
        time.sleep(max(10, delay))
