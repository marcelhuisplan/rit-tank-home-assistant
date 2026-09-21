from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import Any, Callable, Mapping
from urllib.parse import quote

import websocket


def _provider(dependencies: Mapping[str, Callable[..., Any]], name: str) -> Callable[..., Any]:
    provider = dependencies.get(name)
    if provider is None:
        raise RuntimeError(f'Home Assistant dependency ontbreekt: {name}')
    return provider


def ha_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 8) -> Any:
    token = os.getenv('SUPERVISOR_TOKEN', '')
    if not token:
        raise ValueError('Home Assistant API-token is niet beschikbaar.')
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    headers = {'Authorization': 'Bear' + f'er {token}', 'Accept': 'application/json'}
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


def ha_get(path: str, *, dependencies: Mapping[str, Callable[..., Any]]) -> Any:
    return _provider(dependencies, 'ha_request')('GET', path)


def ha_post(path: str, payload: dict[str, Any], *, dependencies: Mapping[str, Callable[..., Any]]) -> Any:
    return _provider(dependencies, 'ha_request')('POST', path, payload)


def ha_notify_services(*, dependencies: Mapping[str, Callable[..., Any]]) -> list[dict[str, str]]:
    try:
        domains = _provider(dependencies, 'ha_get')('services')
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


def ha_ws_command(
    command: dict[str, Any],
    timeout: int = 10,
    *,
    dependencies: Mapping[str, Callable[..., Any]],
) -> Any:
    ws = _provider(dependencies, 'ha_ws_open')(timeout)
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


def location_entities(*, dependencies: Mapping[str, Callable[..., Any]]) -> list[dict[str, Any]]:
    rows = _provider(dependencies, 'ha_get')('states')
    to_float = _provider(dependencies, 'to_float')
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


def location_from_entity(entity_id: str, *, dependencies: Mapping[str, Callable[..., Any]]) -> dict[str, Any]:
    entity_id = (entity_id or '').strip()
    if not (entity_id.startswith('person.') or entity_id.startswith('device_tracker.')):
        raise ValueError('Kies een geldige person- of device_tracker-entiteit.')
    item = _provider(dependencies, 'ha_get')(f'states/{quote(entity_id, safe="._")}')
    attrs = item.get('attributes') or {}
    to_float = _provider(dependencies, 'to_float')
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


def ha_post_state(entity_id: str, state: Any, attrs: dict[str, Any]) -> None:
    token = os.getenv('SUPERVISOR_TOKEN', '')
    if not token:
        return
    url = f'http://supervisor/core/api/states/{entity_id}'
    body = json.dumps({'state': state, 'attributes': attrs}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST', headers={
        'Authorization': 'Bear' + f'er {token}',
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=4) as _:
            pass
    except Exception:
        pass


def publish_sensors(*, dependencies: Mapping[str, Callable[..., Any]]) -> None:
    try:
        rows = _provider(dependencies, 'rows_events')()
        settings = _provider(dependencies, 'get_settings')()
        prefix = _provider(dependencies, 'sanitize_prefix')(
            str(_provider(dependencies, 'load_options')().get('entity_prefix') or 'auto')
        )
        post_state = _provider(dependencies, 'ha_post_state')
        odo = _provider(dependencies, 'current_odometer')(rows)
        if odo is not None:
            post_state(f'sensor.{prefix}_kilometerstand', round(odo, 1), {
                'friendly_name': f"{settings['vehicle_name']} kilometerstand",
                'unit_of_measurement': 'km', 'icon': 'mdi:counter', 'state_class': 'measurement'
            })
        for period, label in [('week', 'week'), ('month', 'maand'), ('year', 'jaar')]:
            stats = _provider(dependencies, 'stats_for_period')(period, rows)
            full_tank = _provider(dependencies, 'full_tank_period_average')(period, rows)
            post_state(f'sensor.{prefix}_kilometers_{label}', stats['km'], {
                'friendly_name': f"{settings['vehicle_name']} kilometers {label}", 'unit_of_measurement': 'km', 'icon': 'mdi:road-variant'
            })
            post_state(f'sensor.{prefix}_verbruik_{label}', full_tank['l100'] if full_tank and full_tank['l100'] is not None else 'unknown', {
                'friendly_name': f"{settings['vehicle_name']} werkelijk verbruik {label}",
                'unit_of_measurement': 'L/100 km', 'icon': 'mdi:gas-station',
                'measurement_method': 'full_tank', 'cycles': full_tank['cycles'] if full_tank else 0
            })
        overall = _provider(dependencies, 'overall_full_tank_average')(rows)
        post_state(f'sensor.{prefix}_verbruik_gemiddeld', overall['l100'] if overall and overall['l100'] is not None else 'unknown', {
            'friendly_name': f"{settings['vehicle_name']} gemiddeld verbruik",
            'unit_of_measurement': 'L/100 km', 'icon': 'mdi:gauge',
            'measurement_method': 'full_tank', 'cycles': overall['cycles'] if overall else 0
        })
        stats = _provider(dependencies, 'stats_for_period')('month', rows)
        post_state(f'sensor.{prefix}_brandstofkosten_maand', stats['cost'], {
            'friendly_name': f"{settings['vehicle_name']} brandstofkosten maand", 'unit_of_measurement': settings['currency'], 'icon': 'mdi:cash'
        })
        for period, label in [('week', 'week'), ('month', 'maand'), ('year', 'jaar')]:
            business = _provider(dependencies, 'business_stats_for_period')(period)
            post_state(f'sensor.{prefix}_zakelijke_km_{label}', business['business_km'], {
                'friendly_name': f"{settings['vehicle_name']} zakelijke kilometers {label}",
                'unit_of_measurement': 'km', 'icon': 'mdi:briefcase-outline'
            })
        active = _provider(dependencies, 'active_business_trip')()
        post_state(f'sensor.{prefix}_zakelijke_rit_status', 'actief' if active else 'geen', {
            'friendly_name': f"{settings['vehicle_name']} zakelijke rit status",
            'icon': 'mdi:car-clock',
            'trip_id': active['id'] if active else None,
            'kilometers': active['km'] if active else 0,
        })
    except Exception:
        pass


def publish_sensors_async(*, dependencies: Mapping[str, Callable[..., Any]]) -> None:
    threading.Thread(target=_provider(dependencies, 'publish_sensors'), daemon=True).start()
