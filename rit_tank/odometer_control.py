"""Physical odometer confirmation. No schema changes or historical rewrites."""
import hashlib
import json
import math
import re
import secrets
import time
from decimal import Decimal

ABSOLUTE_KM = 2.0
RELATIVE_FRACTION = 0.05
TOKEN_SECONDS = 600
PENDING = {}


def physical_odometer(value):
    message = 'Vul een tellerstand in hele kilometers in, bijvoorbeeld 64375 of 64.375 (0 t/m 999.999).'
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(message)
    text = str(value).strip()
    if re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{3})+', text):
        text = text.replace('.', '')
    if not re.fullmatch(r'[0-9]+(?:[.,]0)?', text):
        raise ValueError(message)
    number = Decimal(text.replace(',', '.'))
    if not 0 <= number <= 999999:
        raise ValueError(message)
    return int(number)


def thresholds(settings):
    result = {}
    for key, default in [('distance_warning_km', ABSOLUTE_KM),
                         ('distance_warning_fraction', RELATIVE_FRACTION)]:
        try:
            value = float(settings.get(key, default))
        except (ValueError, TypeError):
            value = default
        result[key] = value if math.isfinite(value) and value >= 0 else default
    return result


def compare(gps_km, actual_km, limits):
    if gps_km is None:
        return dict(difference_km=None, absolute_difference_km=None,
                    relative_difference=None, significant=False)
    gps, actual = Decimal(str(gps_km)), Decimal(str(actual_km))
    difference = actual - gps
    absolute = abs(difference)
    denominator = max(gps, actual)
    relative = absolute / denominator if denominator > 0 else Decimal(0)
    return dict(difference_km=float(difference), absolute_difference_km=float(absolute),
                relative_difference=float(relative),
                significant=bool(absolute > Decimal(str(limits['distance_warning_km'])) and
                                 relative > Decimal(str(limits['distance_warning_fraction']))))


def segment_snapshot(backend, trip, end):
    last = trip['stops'][-1]
    state = backend.assistant_state_get('trip_distance_tracking', {}) or {}
    matches = state.get('trip_id') == trip['id'] and state.get('stop_id') == last['id']
    meters = state.get('segment_m') if matches else None
    samples = int(state.get('sample_count') or 0) if matches else 0
    available = isinstance(meters, (int, float)) and math.isfinite(meters) and meters > 0 and samples > 0
    return {'start_stop_id': last['id'], 'start_odometer': last['odometer'],
            'end_odometer': end, 'gps_km': meters / 1000 if available else None,
            'gps_missing': not available,
            'gps_incomplete': bool(matches and state.get('incomplete')),
            'gps_insufficient': available and samples < 3,
            'sample_count': samples}


def trip_measurement(backend, trip, end):
    segment = segment_snapshot(backend, trip, end)
    parts = []
    with backend.db() as con:
        records = con.execute("SELECT details FROM audit_log WHERE action='distance_segment' "
                              "AND entity_type='trip' AND entity_id=? ORDER BY id", (trip['id'],))
        saved = {item['stop_id']: item for row in records
                 if isinstance((item := json.loads(row['details'])), dict) and 'stop_id' in item}
    for previous, stop in zip(trip['stops'], trip['stops'][1:]):
        item = saved.get(stop['id'])
        if not item or item.get('start_odometer') != previous['odometer'] or item.get('end_odometer') != stop['odometer']:
            item = {'gps_km': None, 'gps_missing': True, 'gps_incomplete': False}
        parts.append(item)
    parts.append(segment)
    missing = any(part.get('gps_missing', True) for part in parts)
    partial = sum(part.get('gps_km') or 0 for part in parts)
    return {'segment': segment, 'gps_km': None if missing else partial,
            'gps_partial_km': partial if any(p.get('gps_km') is not None for p in parts) else None,
            'gps_missing': missing,
            'gps_incomplete': any(p.get('gps_incomplete') for p in parts),
            'gps_insufficient': any(p.get('gps_insufficient') for p in parts)}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def prepare(backend, payload, kind, arrival_id=None):
    """Read-only preflight. Payload metadata is never trusted as fiscal input."""
    if not isinstance(payload, dict):
        raise ValueError('Ongeldige ritgegevens.')
    if payload.get('physical_confirmed') is not True:
        raise ValueError('Controleer de kilometerteller van de auto en bevestig de fysieke tellerstand.')
    data = {k: v for k, v in payload.items() if not k.startswith('_') and k != 'confirmation_token'}
    data['odometer'] = physical_odometer(data.get('odometer'))
    trip = backend.active_business_trip()
    arrival = None
    if kind == 'assistant':
        arrival = backend.assistant.assert_assistant_arrival_is_next(
            arrival_id, dependencies=backend._assistant_dependencies())
        if not trip:
            data['start_odometer'] = physical_odometer(data.get('start_odometer'))
    elif kind == 'start':
        if trip:
            raise ValueError('Er is al een actieve rit. Vernieuw de pagina.')
        backend._trip_point_payload(data)
    if kind != 'start' and trip:
        if data.get('trip_id') != trip['id']:
            raise ValueError('De actieve rit is gewijzigd. Open de rit opnieuw.')
        stops = trip['stops']
        values = [float(s['odometer']) for s in stops]
        if any(not math.isfinite(v) or v < 0 or not v.is_integer() for v in values) or any(b < a for a,b in zip(values, values[1:])):
            raise ValueError('De bestaande tellerreeks is ongeldig. Controleer eerst de opgeslagen rit.')
        if data['odometer'] < values[-1]:
            raise ValueError('De eindteller mag niet lager zijn dan de startteller of laatste tussenstop.')
        start = values[0]
        stamp = arrival['detected_at'] if arrival else data.get('created_at')
        if backend.parse_dt(stamp) < backend.parse_dt(stops[-1]['created_at']):
            raise ValueError('Het tijdstip mag niet vóór de vorige stop liggen.')
        valid, message = backend.validate_odometer(backend.iso_local(backend.parse_dt(stamp)), data['odometer'])
        if not valid:
            raise ValueError(message)
        if not arrival:
            backend._trip_point_payload(data)
        measure = trip_measurement(backend, trip, data['odometer'])
        if kind == 'stop' or (arrival and data.get('finish') is not True):
            start = values[-1]
            measure = {**measure['segment'], 'segment': measure['segment']}
    elif kind == 'assistant':
        start = data['start_odometer']
        if data['odometer'] < start:
            raise ValueError('De eindteller mag niet lager zijn dan de startteller.')
        for stamp, odo in [(arrival.get('departure_at') or arrival['detected_at'], start),
                           (arrival['detected_at'], data['odometer'])]:
            valid, message = backend.validate_odometer(stamp, odo)
            if not valid:
                raise ValueError(message)
        snapshot = backend.assistant_state_get(f'arrival_route_{arrival_id}', {}) or {}
        meters = snapshot.get('route_m')
        samples = int(snapshot.get('route_samples') or 0)
        available = isinstance(meters, (int, float)) and math.isfinite(meters) and meters > 0 and samples > 0
        measure = {'gps_km': meters / 1000 if available else None, 'gps_missing': not available,
                   'gps_incomplete': bool(snapshot.get('route_incomplete')),
                   'gps_insufficient': samples < 3}
    elif kind == 'start':
        start = data['odometer']
        measure = {'gps_km': None, 'gps_missing': False, 'gps_incomplete': False}
    else:
        raise ValueError('Er is geen actieve rit. Deze rit is mogelijk al afgesloten.')
    limits = thresholds(backend.get_settings())
    actual = data['odometer'] - start
    comparison = compare(measure['gps_km'], actual, limits)
    # Independent of the numerical threshold: unavailable/unreliable GPS needs attention.
    required = kind != 'start' and (comparison['significant'] or measure['gps_missing'] or
                                  measure['gps_incomplete'] or measure.get('gps_insufficient', False))
    check = {**measure, **comparison, **limits, 'start_odometer': start,
             'end_odometer': data['odometer'], 'odometer_km': actual,
             'trip_start_odometer': trip['stops'][0]['odometer'] if trip else start,
             'trip_odometer_km': data['odometer'] - (trip['stops'][0]['odometer'] if trip else start),
             'physical_confirmed': True, 'confirmation_required': required,
             'confirmation_given': False, 'captured_at': backend.iso_local(), 'version': 31}
    # GPS may keep updating while the user reads the modal. Freeze the measured snapshot,
    # but bind confirmation to all submitted data, the actual stop sequence and settings.
    with backend.db() as con:
        events = [tuple(r) for r in con.execute('SELECT id,created_at,odometer FROM events ORDER BY id')]
    revision = _digest({'kind': kind, 'arrival': arrival, 'data': data, 'trip': trip,
                        'events': events, 'limits': limits,
                        'reset': backend.get_settings().get('administration_reset_id')})
    return data, check, revision


def submit(backend, payload, kind, binding, arrival_id=None):
    """Called only behind authorized POST + JSON + same-origin, under administration lock."""
    data, check, revision = prepare(backend, payload, kind, arrival_id)
    now = time.monotonic()
    for token in list(PENDING):
        if PENDING[token]['expires'] <= now:
            del PENDING[token]
    token = payload.get('confirmation_token')
    if token:
        saved = PENDING.pop(str(token), None)
        if not saved or saved['binding'] != binding or saved['revision'] != revision:
            raise ValueError('Bevestiging verlopen of gegevens gewijzigd. Controleer de teller opnieuw.')
        check = saved['check']
        check['confirmation_given'] = True
    elif check['confirmation_required']:
        # Bound memory even with abandoned dialogs. New request supersedes this session's old one.
        for key in list(PENDING):
            if PENDING[key]['binding'] == binding:
                del PENDING[key]
        if len(PENDING) >= 256:
            raise ValueError('Te veel open bevestigingen. Probeer later opnieuw.')
        token = secrets.token_urlsafe(32)
        PENDING[token] = {'binding': binding, 'revision': revision, 'check': check,
                          'expires': now + TOKEN_SECONDS}
        return {'confirmation_required': True, 'confirmation_token': token, 'check': check}
    check['confirmed_at'] = backend.iso_local()
    data['_distance_control'] = check
    if kind == 'start':
        return backend.start_business_trip(data)
    if kind == 'assistant':
        return backend.complete_assistant_arrival(arrival_id, data)
    return backend.add_business_stop(data, finish=kind == 'finish')
