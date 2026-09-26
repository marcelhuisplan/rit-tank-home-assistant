from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Mapping
from urllib.parse import quote, urlencode


_PLACE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PLACE_CACHE_TTL = 300.0
_GEOCODE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_GEOCODE_CACHE_TTL = 300.0


def _provider(dependencies: Mapping[str, Any], name: str) -> Any:
    provider = dependencies.get(name)
    if provider is None:
        raise RuntimeError(f'Google Places dependency ontbreekt: {name}')
    return provider


def places_key(*, dependencies: Mapping[str, Any]) -> str:
    return str(_provider(dependencies, 'load_options')().get('google_places_api_key') or '').strip()


def places_radius_m(*, dependencies: Mapping[str, Any]) -> int:
    try:
        return max(100, min(5000, int(_provider(dependencies, 'load_options')().get('places_radius_m') or 1800)))
    except Exception:
        return 1800


def places_max_results(*, dependencies: Mapping[str, Any]) -> int:
    try:
        return max(1, min(20, int(_provider(dependencies, 'load_options')().get('places_max_results') or 8)))
    except Exception:
        return 8


def google_nearby(lat: float, lon: float, *, dependencies: Mapping[str, Any]) -> list[dict[str, Any]]:
    key = places_key(dependencies=dependencies)
    if not key:
        raise ValueError('Google Places API-key ontbreekt. Vul hem in bij de app-configuratie.')
    payload = {
        'includedTypes': ['gas_station'],
        'maxResultCount': places_max_results(dependencies=dependencies),
        'rankPreference': 'DISTANCE',
        'locationRestriction': {
            'circle': {
                'center': {'latitude': lat, 'longitude': lon},
                'radius': float(places_radius_m(dependencies=dependencies)),
            }
        },
        'languageCode': 'nl',
        'regionCode': 'NL',
    }
    data = _provider(dependencies, 'http_json')(
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
        plat = _provider(dependencies, 'to_float')(loc.get('latitude'))
        plon = _provider(dependencies, 'to_float')(loc.get('longitude'))
        distance = None
        if plat is not None and plon is not None:
            distance = _provider(dependencies, 'haversine_m')(lat, lon, plat, plon)
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


def google_places_text_search(query: str, *, location: dict[str, Any] | None = None, dependencies: Mapping[str, Any]) -> list[dict[str, Any]]:
    """
    Zoek adressen op vrije tekst (voor handmatige adrescorrectie).
    Gebruikt Places API v1 Text Search en levert kandidaten met
    place_id, naam, adres en coördinaten voor een selectielijst in de UI.
    """
    q = (query or '').strip()
    if not q:
        return []
    key = places_key(dependencies=dependencies)
    if not key:
        raise ValueError('Google Places API-key ontbreekt. Vul hem in bij de app-configuratie.')
    payload = {
        'textQuery': q,
        'languageCode': 'nl',
        'regionCode': 'NL',
        'maxResultCount': 8,
        # A geographic preference, never a hardcoded result or country exclusion.
        'locationBias': {'rectangle': {
            'low': {'latitude': 50.7, 'longitude': 3.2},
            'high': {'latitude': 53.7, 'longitude': 7.3},
        }},
    }
    if isinstance(location, dict):
        lat = _provider(dependencies, 'to_float')(location.get('latitude'))
        lon = _provider(dependencies, 'to_float')(location.get('longitude'))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            payload['locationBias'] = {'circle': {
                'center': {'latitude': lat, 'longitude': lon},
                'radius': float(places_radius_m(dependencies=dependencies)),
            }}
    data = _provider(dependencies, 'http_json')(
        'https://places.googleapis.com/v1/places:searchText',
        method='POST', payload=payload,
        headers={
            'X-Goog-Api-Key': key,
            'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress,places.location',
        }, timeout=10,
    )
    out = []
    for p in data.get('places', []) or []:
        loc = p.get('location') or {}
        lat = _provider(dependencies, 'to_float')(loc.get('latitude'))
        lon = _provider(dependencies, 'to_float')(loc.get('longitude'))
        if lat is None or lon is None:
            continue
        out.append({
            'place_id': str(p.get('id') or ''),
            'name': str((p.get('displayName') or {}).get('text') or ''),
            'address': str(p.get('formattedAddress') or ''),
            'latitude': lat,
            'longitude': lon,
        })
    return out


def google_place_details(place_id: str, *, dependencies: Mapping[str, Any]) -> dict[str, Any] | None:
    place_id = (place_id or '').strip()
    key = places_key(dependencies=dependencies)
    if not place_id or not key:
        return None
    cached = _PLACE_CACHE.get(place_id)
    if cached and time.monotonic() - cached[0] < _PLACE_CACHE_TTL:
        return cached[1]
    try:
        data = _provider(dependencies, 'http_json')(
            f'https://places.googleapis.com/v1/places/{quote(place_id, safe="")}?languageCode=nl&regionCode=NL',
            headers={
                'X-Goog-Api-Key': key,
                'X-Goog-FieldMask': 'id,displayName,formattedAddress,location,addressComponents',
            }, timeout=8,
        )
        loc = data.get('location') or {}
        result = {
            'place_id': str(data.get('id') or place_id),
            'name': str((data.get('displayName') or {}).get('text') or 'Tankstation'),
            'address_components': data.get('addressComponents') or [],
            'address': str(data.get('formattedAddress') or ''),
            'latitude': _provider(dependencies, 'to_float')(loc.get('latitude')),
            'longitude': _provider(dependencies, 'to_float')(loc.get('longitude')),
            'google_maps_uri': (f"https://www.google.com/maps/search/?api=1&query={_provider(dependencies, 'to_float')(loc.get('latitude'))},{_provider(dependencies, 'to_float')(loc.get('longitude'))}&query_place_id={quote(str(data.get('id') or place_id), safe='')}" if _provider(dependencies, 'to_float')(loc.get('latitude')) is not None and _provider(dependencies, 'to_float')(loc.get('longitude')) is not None else ''),
        }
        _PLACE_CACHE[place_id] = (time.monotonic(), result)
        return result
    except Exception:
        return None




def google_reverse_geocode(lat: float, lon: float, *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    key = places_key(dependencies=dependencies)
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
        data = _provider(dependencies, 'http_json')(url, timeout=10)
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


def nearby_house_numbers(lat: float, lon: float, *, dependencies: Mapping[str, Any]) -> dict[str, Any]:
    """Use real BAG addresses, never manufacture house numbers from GPS."""
    base = 'https://api.pdok.nl/bzk/locatieserver/search/v3_1/'
    fields = 'id,weergavenaam,straatnaam,woonplaatsnaam,openbareruimte_id,huis_nlt,centroide_ll,afstand'
    try:
        nearest = _provider(dependencies, 'http_json')(base + 'reverse?' + urlencode({
            'lat': lat, 'lon': lon, 'type': 'adres', 'distance': 250,
            'rows': 1, 'fl': fields,
        }), timeout=10).get('response', {}).get('docs', [])
        if not nearest:
            return {'addresses': [], 'street': '', 'source': 'PDOK / BAG'}
        street_id = str(nearest[0].get('openbareruimte_id') or '')
        if not re.fullmatch(r'\d+', street_id):
            return {'addresses': [], 'street': '', 'source': 'PDOK / BAG'}
        docs = _provider(dependencies, 'http_json')(base + 'free?' + urlencode({
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
                              'distance_m': round(_provider(dependencies, 'haversine_m')(lat, lon, latitude, lng))})
        addresses.sort(key=lambda item: item['distance_m'])
        return {'addresses': addresses[:10], 'street': nearest[0].get('straatnaam') or '', 'source': 'PDOK / BAG'}
    except Exception:
        raise ValueError('Huisnummers konden niet worden opgehaald. Probeer opnieuw of vul het adres handmatig in.') from None


def cached_report_address(lat: float, lon: float, *, dependencies: Mapping[str, Any]) -> str:
    try:
        with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
            row = con.execute('SELECT address FROM report_addresses WHERE coordinate_key=?', (f'{lat:.5f},{lon:.5f}',)).fetchone()
        return str(row['address']) if row else ''
    except sqlite3.OperationalError:
        # Offline/unit-test databases created before the migration simply have no cache yet.
        return ''


def refresh_report_addresses(*, dependencies: Mapping[str, Any]) -> None:
    """Resolve old GPS-only records in the background; never delay PDF requests."""
    with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
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
            docs = _provider(dependencies, 'http_json')(url, timeout=6).get('response', {}).get('docs', [])
            if docs and all(docs[0].get(k) for k in ('straatnaam', 'huis_nlt', 'postcode', 'woonplaatsnaam')):
                d = docs[0]
                address = f'{d["straatnaam"]} {d["huis_nlt"]}, {d["postcode"]} {d["woonplaatsnaam"]}'
        except Exception:
            pass
        with _provider(dependencies, 'DB_LOCK'), _provider(dependencies, 'db')() as con:
            con.execute('INSERT OR REPLACE INTO report_addresses(coordinate_key,address,checked_at) VALUES(?,?,?)', (key, address, time.time()))


def _report_address_worker(*, dependencies: Mapping[str, Any]) -> None:
    while True:
        try:
            refresh_report_addresses(dependencies=dependencies)
        except Exception as exc:
            print(f'Adresaanvulling: {type(exc).__name__}', flush=True)
        time.sleep(30)
