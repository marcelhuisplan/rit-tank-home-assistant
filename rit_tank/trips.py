from __future__ import annotations

import sqlite3
from typing import Any, Callable, Mapping


def _provider(dependencies: Mapping[str, Any], name: str) -> Any:
    provider = dependencies.get(name)
    if provider is None:
        raise RuntimeError(f'Business-trip dependency ontbreekt: {name}')
    return provider


def snapshot_trip(con: sqlite3.Connection, trip_id: int) -> dict[str, Any]:
    row = con.execute('SELECT * FROM business_trips WHERE id=?', (trip_id,)).fetchone()
    if not row:
        return {}
    stops = [dict(r) for r in con.execute(
        'SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no,id', (trip_id,)
    )]
    return {'trip': dict(row), 'stops': stops}


def _route_memory_suggestion(
    origin_place_id: int | None,
    dest_lat: float,
    dest_lon: float,
    *,
    dependencies: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not origin_place_id:
        return None
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        rows = [dict(r) for r in con.execute(
            'SELECT * FROM route_memory WHERE origin_known_place_id=? ORDER BY last_seen_at DESC',
            (int(origin_place_id),),
        )]
    best = None
    best_dist = None
    for row in rows:
        distance = _provider(dependencies, 'haversine_m')(
            dest_lat, dest_lon, float(row['destination_latitude']), float(row['destination_longitude'])
        )
        if distance <= 450 and (best_dist is None or distance < best_dist):
            best, best_dist = row, distance
    if not best:
        return None
    business = int(best.get('business_count') or 0)
    private = int(best.get('private_count') or 0)
    total = business + private
    if total < 2:
        return None
    count = business
    ratio = count / total if total else 0
    if ratio < 0.70:
        return None
    return {
        'suggested_type': 'business',
        'reason': f'Eerder {count}x zo geregistreerd vanaf deze plek',
        'confidence': round(min(.94, .68 + .06 * count), 2),
        'source': 'learned',
    }


def suggest_segment(
    origin_stop: dict[str, Any] | None,
    dest_lat: float,
    dest_lon: float,
    *,
    dependencies: Mapping[str, Any],
) -> dict[str, Any]:
    match_known_place = _provider(dependencies, 'match_known_place')
    known_place_by_id = _provider(dependencies, 'known_place_by_id')
    to_float = _provider(dependencies, 'to_float')
    destination = match_known_place(dest_lat, dest_lon)
    origin = None
    if origin_stop:
        origin = known_place_by_id(origin_stop.get('known_place_id')) or match_known_place(
            to_float(origin_stop.get('latitude')), to_float(origin_stop.get('longitude'))
        )
    if destination and str(destination.get('arrival_trip_type') or 'ask') == 'business':
        trip_type = 'business'
        return {
            'suggested_type': trip_type,
            'reason': f'Bestemming {destination["name"]} staat als {trip_type_label(trip_type).lower()} ingesteld',
            'confidence': .98,
            'source': 'destination_rule',
            'origin_place': origin,
            'destination_place': destination,
        }
    if origin and not destination and str(origin.get('unknown_departure_trip_type') or 'ask') == 'business':
        trip_type = 'business'
        return {
            'suggested_type': trip_type,
            'reason': f'Vanaf {origin["name"]} naar onbekende bestemming: {trip_type_label(trip_type)}',
            'confidence': .90,
            'source': 'origin_rule',
            'origin_place': origin,
            'destination_place': None,
        }
    memory = _route_memory_suggestion(
        int(origin['id']) if origin else None, dest_lat, dest_lon, dependencies=dependencies
    )
    if memory:
        return {**memory, 'origin_place': origin, 'destination_place': destination}
    return {
        'suggested_type': '',
        'reason': 'Geen vaste regel gevonden — kies zelf',
        'confidence': 0.0,
        'source': 'manual',
        'origin_place': origin,
        'destination_place': destination,
    }


def remember_segment(
    origin_stop: dict[str, Any],
    destination_point: dict[str, Any],
    trip_type: str,
    *,
    con: sqlite3.Connection | None = None,
    dependencies: Mapping[str, Any],
) -> None:
    normalize_segment_type = _provider(dependencies, 'normalize_segment_type')
    normalized = normalize_segment_type(trip_type)
    if not normalized:
        return
    known_place_by_id = _provider(dependencies, 'known_place_by_id')
    match_known_place = _provider(dependencies, 'match_known_place')
    to_float = _provider(dependencies, 'to_float')
    origin = known_place_by_id(origin_stop.get('known_place_id')) or match_known_place(
        to_float(origin_stop.get('latitude')), to_float(origin_stop.get('longitude'))
    )
    if not origin:
        return
    latitude = to_float(destination_point.get('latitude'))
    longitude = to_float(destination_point.get('longitude'))
    if latitude is None or longitude is None:
        return
    destination = match_known_place(latitude, longitude)
    owned = con is None
    connection = con or _provider(dependencies, 'db')()
    try:
        rows = [dict(r) for r in connection.execute(
            'SELECT * FROM route_memory WHERE origin_known_place_id=?', (int(origin['id']),)
        )]
        target = None
        for row in rows:
            if _provider(dependencies, 'haversine_m')(
                latitude, longitude, float(row['destination_latitude']), float(row['destination_longitude'])
            ) <= 350:
                target = row
                break
        if target:
            field = 'business_count' if normalized == 'business' else 'private_count'
            connection.execute(
                f'UPDATE route_memory SET {field}={field}+1,destination_known_place_id=?,'
                'destination_latitude=?,destination_longitude=?,last_seen_at=? WHERE id=?',
                (
                    int(destination['id']) if destination else None,
                    latitude,
                    longitude,
                    _provider(dependencies, 'iso_local')(),
                    int(target['id']),
                ),
            )
        else:
            connection.execute(
                'INSERT INTO route_memory(origin_known_place_id,destination_known_place_id,'
                'destination_latitude,destination_longitude,business_count,private_count,last_seen_at) '
                'VALUES(?,?,?,?,?,?,?)',
                (
                    int(origin['id']),
                    int(destination['id']) if destination else None,
                    latitude,
                    longitude,
                    1 if normalized == 'business' else 0,
                    1 if normalized == 'private' else 0,
                    _provider(dependencies, 'iso_local')(),
                ),
            )
        if owned:
            connection.commit()
    finally:
        if owned:
            connection.close()


def trip_type_label(value: str) -> str:
    return {'business': 'Zakelijk', 'private': 'Prive', 'mixed': 'Gemengd'}.get(value, 'Zakelijk')


def active_business_trip(*, dependencies: Mapping[str, Any]) -> dict[str, Any] | None:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute(
            "SELECT * FROM business_trips WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        trip = dict(row)
        stops = [dict(r) for r in con.execute(
            'SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no ASC, id ASC',
            (trip['id'],),
        )]
    return enrich_business_trip(trip, stops, dependencies=dependencies)


def _trip_point_payload(payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    to_float = _provider(dependencies, 'to_float')
    odometer = to_float(payload.get('odometer'))
    if odometer is None or odometer < 0:
        raise ValueError('Vul een geldige kilometerstand in.')
    created_at = _provider(dependencies, 'iso_local')(
        _provider(dependencies, 'parse_dt')(payload.get('created_at'))
    )
    valid, message = _provider(dependencies, 'validate_odometer')(created_at, odometer)
    if not valid:
        raise ValueError(message)
    latitude = to_float(payload.get('latitude'))
    longitude = to_float(payload.get('longitude'))
    accuracy = to_float(payload.get('location_accuracy'))
    source = str(payload.get('location_source') or '').strip()[:40]
    if (
        latitude is None
        or longitude is None
        or not (-90 <= latitude <= 90)
        or not (-180 <= longitude <= 180)
    ):
        raise ValueError('Leg eerst de huidige locatie vast met de 📍-knop.')
    place_id = str(payload.get('place_id') or '').strip()[:255]
    if not place_id and not payload.get('manual_label'):
        geo = _provider(dependencies, 'google_reverse_geocode')(latitude, longitude)
        place_id = str(geo.get('place_id') or '')[:255]
    return {
        'odometer': odometer,
        'created_at': created_at,
        'latitude': latitude,
        'longitude': longitude,
        'location_accuracy': accuracy,
        'location_source': source or 'browser',
        'place_id': place_id or None,
        'manual_label': str(payload.get('manual_label') or '').strip()[:120] or None,
        'note': str(payload.get('note') or '').strip()[:250] or None,
        'known_place_id': (_provider(dependencies, 'match_known_place')(latitude, longitude) or {}).get('id'),
    }


def _insert_trip_stop(
    con: sqlite3.Connection,
    trip_id: int,
    point: dict[str, Any],
    sequence_no: int,
    event_note: str,
    segment_trip_type: str = '',
    suggestion: dict[str, Any] | None = None,
    destination_audit: dict[str, Any] | None = None,
    *,
    dependencies: Mapping[str, Any],
) -> int:
    suggestion = suggestion or {}
    destination_audit = destination_audit or {}
    normalize_segment_type = _provider(dependencies, 'normalize_segment_type')
    segment_type = normalize_segment_type(segment_trip_type)
    suggested = normalize_segment_type(suggestion.get('suggested_type'))
    source = (
        'start'
        if sequence_no == 0
        else 'user-confirmed'
        if suggested and segment_type == suggested
        else 'user-override'
        if suggested
        else 'manual'
    )
    cur = con.execute(
        '''INSERT INTO trip_stops(
            trip_id,sequence_no,created_at,odometer,latitude,longitude,
            location_accuracy,location_source,place_id,manual_label,note,known_place_id,
            segment_trip_type,segment_suggested_type,segment_suggestion_reason,
            segment_suggestion_confidence,segment_classification_source,
            original_destination_latitude,original_destination_longitude,original_destination_address,
            original_destination_distance_m,destination_distance_source,destination_manually_corrected
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            trip_id, sequence_no, point['created_at'], point['odometer'], point['latitude'], point['longitude'],
            point['location_accuracy'], point['location_source'], point['place_id'], point['manual_label'],
            point['note'], point.get('known_place_id'), segment_type or None, suggested or None,
            str(suggestion.get('reason') or '')[:220] or None, float(suggestion.get('confidence') or 0),
            source, destination_audit.get('original_latitude'), destination_audit.get('original_longitude'),
            (str(destination_audit.get('original_address') or '')[:180] or None)
            if destination_audit.get('original_address') else None,
            destination_audit.get('original_distance_m'), destination_audit.get('distance_source'),
            1 if destination_audit.get('manually_corrected') else 0,
        ),
    )
    stop_id = int(cur.lastrowid)
    event = con.execute(
        '''INSERT INTO events(created_at,type,odometer,note,source_kind,business_trip_stop_id)
        VALUES(?,?,?,?,?,?)''',
        (point['created_at'], 'odometer', point['odometer'], event_note[:200], 'business', stop_id),
    )
    con.execute('UPDATE trip_stops SET event_id=? WHERE id=?', (int(event.lastrowid), stop_id))
    return stop_id


def validate_trip_odometer_range(start_odometer: Any, end_odometer: Any) -> tuple[bool, str]:
    try:
        start = float(start_odometer)
        end = float(end_odometer)
    except (TypeError, ValueError):
        return False, 'Vul een geldige kilometerstand in.'
    if end < start:
        return False, f'Kilometerstand is lager dan de vorige stop ({start:.0f} km).'
    return True, ''


def start_business_trip(payload: dict[str, Any], *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    if active_business_trip(dependencies=dependencies) is not None:
        raise ValueError('Er staat al een ritregistratie open. Voeg een volgende locatie toe of sluit de dagrit af.')
    point = _trip_point_payload(payload, dependencies=dependencies)
    purpose = str(payload.get('purpose') or '').strip()[:120]
    client = str(payload.get('client') or '').strip()[:120]
    trip_note = str(payload.get('trip_note') or '').strip()[:250]
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        cur = con.execute(
            '''INSERT INTO business_trips(started_at,status,purpose,client,note,trip_type,private_detour_km,modified_at)
            VALUES(?,'active',?,?,?,'business',0,?)''',
            (
                point['created_at'],
                purpose or None,
                client or None,
                trip_note or None,
                _provider(dependencies, 'iso_local')(),
            ),
        )
        trip_id = int(cur.lastrowid)
        _insert_trip_stop(
            con, trip_id, point, 0, 'Ritregistratie start', dependencies=dependencies
        )
        _provider(dependencies, 'audit')(
            'create',
            'trip',
            trip_id,
            {'mode': 'segment_classification', 'purpose': purpose, 'client': client, 'start': point},
            con=con,
        )
        con.commit()
    trip_now = active_business_trip(dependencies=dependencies)
    _provider(dependencies, 'reset_trip_distance_tracking')(trip_now)
    _provider(dependencies, 'publish_sensors_async')()
    return {'ok': True, 'trip': trip_now}


def add_business_stop(
    payload: dict[str, Any],
    *,
    finish: bool = False,
    destination_audit: dict[str, Any] | None = None,
    dependencies: Mapping[str, Any],
) -> dict[str, Any]:
    trip = active_business_trip(dependencies=dependencies)
    if not trip:
        raise ValueError('Er is geen actieve ritregistratie.')
    point = _trip_point_payload(payload, dependencies=dependencies)
    tracking = _provider(dependencies, 'assistant_state_get')('trip_distance_tracking', {}) or {}
    normalize_segment_type = _provider(dependencies, 'normalize_segment_type')
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        last = con.execute(
            'SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no DESC LIMIT 1',
            (trip['id'],),
        ).fetchone()
        if not last:
            raise ValueError('De actieve rit heeft geen startpunt.')
        last_stop = dict(last)
        if point['odometer'] < float(last['odometer']):
            raise ValueError(f'Kilometerstand is lager dan de vorige stop ({float(last["odometer"]):.0f} km).')
        suggestion = suggest_segment(
            last_stop, float(point['latitude']), float(point['longitude']), dependencies=dependencies
        )
        valid_range, range_message = validate_trip_odometer_range(last['odometer'], point['odometer'])
        if not valid_range:
            raise ValueError(range_message)
        segment_type = 'business'
        sequence_no = int(last['sequence_no']) + 1
        label = 'einde' if finish else 'stop'
        stop_id = _insert_trip_stop(
            con,
            int(trip['id']),
            point,
            sequence_no,
            f'Rit {label} ({trip_type_label(segment_type)})',
            segment_type,
            suggestion,
            destination_audit=destination_audit,
            dependencies=dependencies,
        )
        if (
            int(tracking.get('trip_id') or -1) == int(trip['id'])
            and int(tracking.get('stop_id') or -1) == int(last['id'])
            and not tracking.get('incomplete')
            and abs(
                (
                    _provider(dependencies, 'parse_dt')(point['created_at'])
                    - _provider(dependencies, 'now_local')()
                ).total_seconds()
            ) < 300
        ):
            _provider(dependencies, 'learn_distance')(
                f'stop:{stop_id}',
                float(tracking.get('segment_m') or 0) / 1000,
                float(point['odometer']) - float(last['odometer']),
                int(tracking.get('sample_count') or 0),
                payload.get('odometer_checked') is True,
                con=con,
            )
        remember_segment(last_stop, point, segment_type, con=con, dependencies=dependencies)
        overall = 'business'
        if finish:
            route = str(payload.get('deviating_route') or '').strip()[:300]
            detour = 0.0
            con.execute(
                "UPDATE business_trips SET status='completed', ended_at=?, trip_type=?, "
                'deviating_route=?, private_detour_km=?, modified_at=? WHERE id=?',
                (
                    point['created_at'],
                    overall,
                    route or None,
                    detour,
                    _provider(dependencies, 'iso_local')(),
                    trip['id'],
                ),
            )
            _provider(dependencies, 'audit')(
                'finish',
                'trip',
                int(trip['id']),
                {
                    'stop_id': stop_id,
                    'segment_trip_type': segment_type,
                    'overall_trip_type': overall,
                    'suggestion': suggestion.get('reason'),
                    'end': point,
                },
                con=con,
            )
        else:
            con.execute(
                'UPDATE business_trips SET trip_type=?,modified_at=? WHERE id=?',
                (overall, _provider(dependencies, 'iso_local')(), trip['id']),
            )
            _provider(dependencies, 'audit')(
                'stop',
                'trip',
                int(trip['id']),
                {
                    'stop_id': stop_id,
                    'segment_trip_type': segment_type,
                    'suggestion': suggestion.get('reason'),
                    'point': point,
                },
                con=con,
            )
        con.commit()
    result_trip = (
        active_business_trip(dependencies=dependencies)
        if not finish
        else business_trip_by_id(int(trip['id']), dependencies=dependencies)
    )
    if finish:
        _provider(dependencies, 'assistant_state_set')('trip_distance_tracking', {})
    else:
        _provider(dependencies, 'reset_trip_distance_tracking')(result_trip)
    _provider(dependencies, 'publish_sensors_async')()
    return {'ok': True, 'trip': result_trip}


def business_trip_by_id(
    trip_id: int, *, dependencies: Mapping[str, Any]
) -> dict[str, Any] | None:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        row = con.execute('SELECT * FROM business_trips WHERE id=?', (trip_id,)).fetchone()
        if not row:
            return None
        stops = [dict(item) for item in con.execute(
            'SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no ASC, id ASC',
            (trip_id,),
        )]
    return enrich_business_trip(dict(row), stops, dependencies=dependencies)


def enrich_business_trip(
    trip: dict[str, Any],
    stops: list[dict[str, Any]],
    resolve: bool = True,
    *,
    dependencies: Mapping[str, Any],
) -> dict[str, Any]:
    out = dict(trip)
    enriched = []
    previous_odometer = None
    total = business_km = private_km = 0.0
    segment_types = []
    normalize_segment_type = _provider(dependencies, 'normalize_segment_type')
    parse_dt = _provider(dependencies, 'parse_dt')
    dutch_date = _provider(dependencies, 'dutch_date')
    for stop in stops:
        item = dict(stop)
        date = parse_dt(item['created_at'])
        item['date_label'] = dutch_date(date)
        item['time_label'] = date.strftime('%H:%M')
        odometer = float(item['odometer']) if item.get('odometer') is not None else None
        km = max(0.0, odometer - previous_odometer) if odometer is not None and previous_odometer is not None else None
        item['segment_km'] = round(km, 1) if km is not None else (0.0 if not enriched else None)
        total += km or 0.0
        segment_type = normalize_segment_type(item.get('segment_trip_type'))
        item['segment_trip_type'] = segment_type
        item['segment_trip_type_label'] = trip_type_label(segment_type) if segment_type else ''
        if segment_type:
            segment_types.append(segment_type)
            if segment_type == 'private':
                private_km += km or 0.0
            else:
                business_km += km or 0.0
        known_place = _provider(dependencies, 'known_place_by_id')(item.get('known_place_id'))
        item['known_place_name'] = known_place.get('name') if known_place else ''
        location = _provider(dependencies, 'trip_location_details')(item, resolve=resolve)
        item['location_label'] = location['label']
        item['location_address'] = location['address']
        item['google_maps_uri'] = location['google_maps_uri']
        enriched.append(item)
        previous_odometer = odometer
    out['stops'] = enriched
    out['km'] = round(total, 1)
    if segment_types:
        overall = (
            segment_types[0]
            if all(value == segment_types[0] for value in segment_types)
            else 'mixed'
        )
        out['trip_type'] = overall
        out['business_km'] = round(business_km, 1)
        out['private_km'] = round(private_km, 1)
    else:
        out['trip_type'] = str(out.get('trip_type') or 'business')
        detour = max(0.0, min(total, float(out.get('private_detour_km') or 0)))
        if out['trip_type'] == 'private':
            out['business_km'], out['private_km'] = 0.0, round(total, 1)
        elif out['trip_type'] == 'mixed':
            out['private_km'], out['business_km'] = round(detour, 1), round(max(0.0, total - detour), 1)
        else:
            out['business_km'], out['private_km'] = round(total, 1), 0.0
    out['trip_type_label'] = trip_type_label(out['trip_type'])
    out['start_odometer'] = float(stops[0]['odometer']) if stops and stops[0].get('odometer') is not None else None
    out['last_odometer'] = float(stops[-1]['odometer']) if stops and stops[-1].get('odometer') is not None else None
    out['stop_count'] = len(stops)
    if stops:
        out['start_location'] = enriched[0]['location_label']
        out['last_location'] = enriched[-1]['location_label']
        out['started_label'] = dutch_date(stops[0]['created_at']) + ' ' + parse_dt(
            stops[0]['created_at']
        ).strftime('%H:%M')
        out['ended_label'] = (
            dutch_date(stops[-1]['created_at']) + ' ' + parse_dt(stops[-1]['created_at']).strftime('%H:%M')
            if out.get('status') == 'completed' else None
        )
    else:
        out['start_location'] = out['last_location'] = ''
        out['started_label'] = out['ended_label'] = None
    return out


def business_trips_raw(*, dependencies: Mapping[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        trip_rows = [
            dict(row)
            for row in con.execute(
                'SELECT * FROM business_trips ORDER BY started_at ASC, id ASC'
            )
        ]
        result = []
        for trip in trip_rows:
            stops = [
                dict(row)
                for row in con.execute(
                    'SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no ASC, id ASC',
                    (trip['id'],),
                )
            ]
            result.append((trip, stops))
    return result


def business_stats_for_period(period: str, *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    selected = business_trips_for_period(period, dependencies=dependencies)
    total_km = sum(float(trip.get('km') or 0) for trip in selected)
    business_km = sum(float(trip.get('business_km') or 0) for trip in selected)
    private_km = sum(float(trip.get('private_km') or 0) for trip in selected)
    stop_count = sum(int(trip.get('stop_count') or 0) for trip in selected)
    return {
        'km': round(total_km, 1),
        'business_km': round(business_km, 1),
        'private_km': round(private_km, 1),
        'trips': len(selected),
        'segments': sum(max(0, int(trip.get('stop_count') or 0) - 1) for trip in selected),
        'stops': stop_count,
        'avg_km': round(total_km / len(selected), 1) if selected else 0.0,
    }


def recent_business_trips(
    period: str, limit: int = 12, *, dependencies: Mapping[str, Any]
) -> list[dict[str, Any]]:
    start, end = _provider(dependencies, 'period_bounds')(period)
    selected = []
    for trip, stops in reversed(business_trips_raw(dependencies=dependencies)):
        if not stops:
            continue
        if trip.get('status') == 'active' or any(
            start <= _provider(dependencies, 'parse_dt')(stop['created_at']) < end
            for stop in stops
        ):
            selected.append(enrich_business_trip(trip, stops, dependencies=dependencies))
        if len(selected) >= limit:
            break
    return selected


def business_trips_for_period(
    period: str, *, dependencies: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return complete trips that touch the selected reporting period."""
    if period == 'all':
        return [
            enrich_business_trip(trip, stops, dependencies=dependencies)
            for trip, stops in business_trips_raw(dependencies=dependencies)
            if stops
        ]
    if period not in {'day', 'week', 'month', 'year'}:
        period = 'month'
    start, end = _provider(dependencies, 'period_bounds')(period)
    selected = []
    for trip, stops in business_trips_raw(dependencies=dependencies):
        if not stops:
            continue
        if any(
            start <= _provider(dependencies, 'parse_dt')(stop['created_at']) < end
            for stop in stops
        ):
            selected.append(enrich_business_trip(trip, stops, dependencies=dependencies))
    return selected


def edit_business_trip(
    trip_id: int, payload: dict[str, Any], *, dependencies: Mapping[str, Any]
) -> dict[str, Any]:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        before = snapshot_trip(con, trip_id)
        if not before:
            raise ValueError('Rit niet gevonden.')
        current = before['trip']
        trip_type = str(
            payload.get('trip_type') or current.get('trip_type') or 'business'
        ).lower()
        if trip_type not in {'business', 'private', 'mixed'}:
            trip_type = 'business'
        purpose = str(
            payload.get('purpose')
            if payload.get('purpose') is not None
            else current.get('purpose') or ''
        ).strip()[:120]
        client = str(
            payload.get('client')
            if payload.get('client') is not None
            else current.get('client') or ''
        ).strip()[:120]
        note = str(
            payload.get('note')
            if payload.get('note') is not None
            else current.get('note') or ''
        ).strip()[:250]
        route = str(
            payload.get('deviating_route')
            if payload.get('deviating_route') is not None
            else current.get('deviating_route') or ''
        ).strip()[:300]
        detour = max(
            0.0,
            (_provider(dependencies, 'to_float')(payload.get('private_detour_km')) or 0.0)
            if payload.get('private_detour_km') is not None
            else float(current.get('private_detour_km') or 0),
        )
        con.execute(
            'UPDATE business_trips SET trip_type=?,purpose=?,client=?,note=?,'
            'deviating_route=?,private_detour_km=?,modified_at=? WHERE id=?',
            (
                trip_type,
                purpose or None,
                client or None,
                note or None,
                route or None,
                detour,
                _provider(dependencies, 'iso_local')(),
                trip_id,
            ),
        )
        after = snapshot_trip(con, trip_id)
        _provider(dependencies, 'audit')(
            'update',
            'trip',
            trip_id,
            {'before': before.get('trip', {}), 'after': after.get('trip', {})},
            con=con,
        )
        con.commit()
    _provider(dependencies, 'publish_sensors_async')()
    return {'ok': True, 'trip': business_trip_by_id(trip_id, dependencies=dependencies)}


def delete_business_trip(trip_id: int, *, dependencies: Mapping[str, Any]) -> None:
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
        snapshot = snapshot_trip(con, trip_id)
        event_ids = [
            row['event_id']
            for row in con.execute(
                'SELECT event_id FROM trip_stops WHERE trip_id=? AND event_id IS NOT NULL',
                (trip_id,),
            )
        ]
        for event_id in event_ids:
            con.execute('DELETE FROM events WHERE id=?', (event_id,))
        con.execute('DELETE FROM business_trips WHERE id=?', (trip_id,))
        _provider(dependencies, 'audit')('delete', 'trip', trip_id, snapshot, con=con)
        con.commit()
    _provider(dependencies, 'publish_sensors_async')()
