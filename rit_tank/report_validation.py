"""Offline data-quality checks for the exact visible declaration rows.

No continuity check: private driving between business trips is expected.
No fiscal approval, persisted review state, routing or data correction.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

try:
    from .pdf_report import _full_address
except ImportError:
    from pdf_report import _full_address


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _time(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def validate_business_report(rows):
    issues, seen = [], {}
    business_km = Decimal('0')
    for number, row in enumerate(rows, 1):
        origin, destination = row['origin'], row['destination']
        start_address = str(origin.get('report_address') or '').strip()
        end_address = str(destination.get('report_address') or '').strip()
        km = _number(row.get('km'))
        start, end = _number(origin.get('odometer')), _number(destination.get('odometer'))
        business_km += km or 0

        def issue(severity, code, title, explanation, stop=None):
            issues.append({'severity': severity, 'code': code, 'row_number': number,
                           'trip_id': row.get('trip_id'), 'segment_number': row.get('segment_number'),
                           'origin_stop_id': origin.get('id'), 'destination_stop_id': destination.get('id'),
                           'stop_id': (stop or {}).get('id'), 'title': title, 'explanation': explanation,
                           'fixable': row.get('trip_id') is not None,
                           'start_address': start_address, 'end_address': end_address,
                           'km': float(km) if km is not None else None})

        for stop, address, side, label in [(origin, start_address, 'start', 'Vertrek'),
                                            (destination, end_address, 'end', 'Aankomst')]:
            if not _full_address(address):
                issue('error', side + '_address_missing', label + 'adres ontbreekt',
                      'Vul een volledig ' + label.lower() + 'adres in voordat je de definitieve declaratie exporteert.', stop)
        for stop, value, side, label in [(origin, start, 'start', 'Begin'), (destination, end, 'end', 'Eind')]:
            if value is None or value < 0:
                issue('error', side + '_odometer_missing', label + 'tellerstand ontbreekt of is ongeldig',
                      'Vul een geldige opgeslagen tellerstand in.', stop)
        if start is not None and end is not None and 0 <= end < start:
            issue('error', 'odometer_reversed', 'Eindtellerstand lager dan beginstand',
                  'Controleer de begin- en eindtellerstand van deze rit.')
        if km is not None and km == 0:
            same = ' '.join(start_address.casefold().split()) == ' '.join(end_address.casefold().split())
            issue('warning', 'zero_distance_same_address' if same else 'zero_distance_different_addresses',
                  'Afstand is 0,0 km' if same else 'Verschillende adressen maar 0,0 km afstand',
                  'Start en eindadres zijn gelijk en afstand is 0,0 km. Controleer of deze rit bewust in de declaratie hoort.'
                  if same else 'Dit is mogelijk een invoerfout. Controleer de adressen en tellerstanden.')
        if km is not None and km > 0 and start is not None and end is not None and end >= start:
            delta = abs((end - start) - km)
            # Both thresholds must hold; the larger distance is the conservative denominator.
            if delta >= 3 and delta / max(km, end - start) >= Decimal('0.25'):
                issue('warning', 'distance_mismatch', 'Ritafstand wijkt af van tellerstanden',
                      'De afwijking is minimaal 3 km en 25%. Controleer afstand en tellerstanden.')
        start_time, end_time = _time(origin.get('created_at')), _time(destination.get('created_at'))
        duration = None
        if start_time is not None and end_time is not None:
            try:
                duration = (end_time - start_time).total_seconds()
            except TypeError:
                pass  # A mixed timezone pair cannot be compared reliably.
        if duration is not None:
            if duration < 0:
                issue('error', 'time_reversed', 'Eindtijd ligt vóór starttijd', 'Corrigeer de opgeslagen datum en tijd van deze rit.')
            elif km is not None and km > 0:
                if duration == 0:
                    issue('warning', 'zero_duration', 'Afstand gereden zonder tijdsduur', 'Controleer de start- en eindtijd.')
                elif float(km) / (duration / 3600) > 180:
                    issue('warning', 'extreme_speed', 'Zeer hoge gemiddelde snelheid',
                          'De berekende gemiddelde snelheid is hoger dan 180 km/u. Controleer afstand en tijden.')
        if (start_time is not None and start is not None and end is not None and km is not None
                and _full_address(start_address) and _full_address(end_address)):
            key = (start_time, start_address.casefold(), end_address.casefold(), start, end, km)
            if key in seen:
                issue('warning', 'possible_duplicate', 'Mogelijk dubbele rit',
                      f'Starttijd, adressen, tellerstanden en afstand zijn gelijk aan ritregel {seen[key]}. Controleer beide ritten.')
            else:
                seen[key] = number
    errors = sum(i['severity'] == 'error' for i in issues)
    warnings = sum(i['severity'] == 'warning' for i in issues)
    return {'status': 'error' if errors else 'warning' if warnings else 'ok',
            'row_count': len(rows), 'business_km': float(business_km),
            'errors': errors, 'warnings': warnings,
            'clean_rows': len(rows) - len({i['row_number'] for i in issues}), 'issues': issues}
