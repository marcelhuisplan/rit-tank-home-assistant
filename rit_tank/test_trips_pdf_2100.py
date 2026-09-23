"""Release 21.00: narrow missing-odometer compatibility in trip enrichment."""
from copy import deepcopy
from datetime import datetime
import unittest

import trips


def parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


DEPS = {
    'normalize_segment_type': lambda value: value if value in ('business', 'private') else '',
    'parse_dt': parse,
    'dutch_date': lambda value: parse(value).strftime('%d-%m-%Y'),
    'known_place_by_id': lambda value: None,
    'trip_location_details': lambda stop, resolve=True: {
        'label': stop['manual_label'], 'address': stop['manual_label'], 'google_maps_uri': '',
    },
}


def stops(readings, classified=True):
    return [
        dict(id=i + 1, sequence_no=i, created_at=f'2026-09-19T{10+i:02d}:00:00+02:00',
             odometer=reading, manual_label=f'Straat {i+1}, 1234 AB Stad',
             segment_trip_type=('business' if i == 1 else 'private') if i and classified else None)
        for i, reading in enumerate(readings)
    ]


class CompleteTripCompatibilityTests(unittest.TestCase):
    def test_entire_normal_ui_api_dictionary_is_unchanged(self):
        source = stops([63845, 63849, 63859])
        trip = dict(id=7, status='completed', purpose='Bezoek', trip_type='mixed')
        saved = deepcopy((trip, source))
        actual = trips.enrich_business_trip(trip, source, dependencies=DEPS)
        expected_stops = []
        for i, (stop, km, kind) in enumerate(zip(source, [0.0, 4.0, 10.0], ['', 'business', 'private'])):
            expected_stops.append({
                **stop, 'date_label': '19-09-2026', 'time_label': f'{10+i:02d}:00',
                'segment_km': km, 'segment_trip_type': kind,
                'segment_trip_type_label': ['', 'Zakelijk', 'Prive'][i],
                'known_place_name': '', 'location_label': stop['manual_label'],
                'location_address': stop['manual_label'], 'google_maps_uri': '',
            })
        self.assertEqual(actual, {
            **trip, 'stops': expected_stops, 'km': 14.0, 'business_km': 4.0, 'private_km': 10.0,
            'trip_type_label': 'Gemengd', 'start_odometer': 63845.0, 'last_odometer': 63859.0,
            'stop_count': 3, 'start_location': source[0]['manual_label'],
            'last_location': source[-1]['manual_label'], 'started_label': '19-09-2026 10:00',
            'ended_label': '19-09-2026 12:00',
        })
        self.assertEqual((trip, source), saved)

    def test_complete_zero_decimal_and_active_trips_keep_numeric_distances(self):
        for readings, expected in [([0, 0, 0], [0.0, 0.0, 0.0]),
                                   ([100.1, 104.3, 108.8], [0.0, 4.2, 4.5]),
                                   ([100, 104, 103], [0.0, 4.0, 0.0])]:
            with self.subTest(readings=readings):
                out = trips.enrich_business_trip({'status': 'active'}, stops(readings), dependencies=DEPS)
                self.assertEqual([s['segment_km'] for s in out['stops']], expected)
                self.assertEqual(out['km'], round(sum(expected), 1))
                self.assertEqual(out['business_km'], expected[1])
                self.assertEqual(out['private_km'], expected[2])
                self.assertIsNone(out['ended_label'])
                self.assertIsNotNone(out['start_odometer'])
                self.assertIsNotNone(out['last_odometer'])

    def test_unclassified_complete_trip_totals_are_unchanged(self):
        for kind, business, private in [('business', 14.0, 0.0), ('private', 0.0, 14.0),
                                        ('mixed', 10.0, 4.0)]:
            with self.subTest(kind=kind):
                out = trips.enrich_business_trip(
                    {'status': 'completed', 'trip_type': kind, 'private_detour_km': 4},
                    stops([100, 104, 114], classified=False), dependencies=DEPS,
                )
                self.assertEqual((out['km'], out['business_km'], out['private_km']),
                                 (14.0, business, private))
                self.assertEqual([s['segment_km'] for s in out['stops']], [0.0, 4.0, 10.0])


class MissingTripReadingTests(unittest.TestCase):
    def test_only_legs_touching_missing_reading_are_unknown(self):
        out = trips.enrich_business_trip(
            {'status': 'completed'}, stops([100, None, 120, 125]), dependencies=DEPS,
        )
        self.assertEqual([s['segment_km'] for s in out['stops']], [0.0, None, None, 5.0])
        self.assertEqual((out['km'], out['business_km'], out['private_km']), (5.0, 0.0, 5.0))
        self.assertEqual((out['start_odometer'], out['last_odometer']), (100.0, 125.0))
        self.assertIsNone(out['stops'][1]['odometer'])

    def test_missing_endpoint_or_key_remains_missing_not_zero(self):
        for missing in (0, 1):
            for omit_key in (False, True):
                with self.subTest(missing=missing, omit_key=omit_key):
                    source = stops([100, 104])
                    if omit_key:
                        source[missing].pop('odometer')
                    else:
                        source[missing]['odometer'] = None
                    out = trips.enrich_business_trip({'status': 'completed'}, source, dependencies=DEPS)
                    self.assertIsNone(out['start_odometer'] if missing == 0 else out['last_odometer'])
                    self.assertEqual(out['last_odometer'] if missing == 0 else out['start_odometer'],
                                     104.0 if missing == 0 else 100.0)
                    self.assertIsNone(out['stops'][1]['segment_km'])
                    self.assertEqual((out['km'], out['business_km'], out['private_km']), (0.0, 0.0, 0.0))


if __name__ == '__main__':
    unittest.main()
