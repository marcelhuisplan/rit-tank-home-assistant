from __future__ import annotations

import math
from typing import Any, Callable, Mapping


def _provider(dependencies: Mapping[str, Callable[..., Any]], name: str) -> Callable[..., Any]:
    provider = dependencies.get(name)
    if provider is None:
        raise RuntimeError(f'Routing dependency ontbreekt: {name}')
    return provider


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_route_distance(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    *,
    dependencies: Mapping[str, Callable[..., Any]],
) -> dict[str, Any]:
    """
    Probeer werkelijke routeafstand via Google Routes API.
    Fallback naar GPS-afstand als Routes API niet beschikbaar is.

    Returns: {'type': 'route'|'gps', 'distance_m': float}
    """
    key = _provider(dependencies, 'api_key')()
    if not key:
        gps_m = haversine_m(origin_lat, origin_lon, dest_lat, dest_lon)
        return {'type': 'gps', 'distance_m': gps_m}

    try:
        payload = {
            'origin': {'location': {'latLng': {'latitude': origin_lat, 'longitude': origin_lon}}},
            'destination': {'location': {'latLng': {'latitude': dest_lat, 'longitude': dest_lon}}},
            'travelMode': 'DRIVE',
            'routingPreference': 'TRAFFIC_UNAWARE',
            'computeAlternativeRoutes': False,
        }

        data = _provider(dependencies, 'http_json')(
            'https://routes.googleapis.com/directions/v2:computeRoutes',
            method='POST',
            payload=payload,
            headers={'X-Goog-Api-Key': key, 'X-Goog-FieldMask': 'routes.duration,routes.distanceMeters'},
            timeout=8,
        )

        routes = data.get('routes', []) or []
        if routes and routes[0].get('distanceMeters'):
            distance_m = float(routes[0]['distanceMeters'])
            return {'type': 'route', 'distance_m': distance_m}
    except Exception:
        pass

    gps_m = haversine_m(origin_lat, origin_lon, dest_lat, dest_lon)
    return {'type': 'gps', 'distance_m': gps_m}
