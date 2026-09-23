"""Release 23.00 regression tests for business-only assistant classification."""
import csv
import importlib.util
import io
import sys
import tempfile
import types
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

try:
    import websocket
except ImportError:
    sys.modules['websocket'] = types.ModuleType('websocket')

ROOT = Path(__file__).parent
ASSISTANT = (ROOT / 'assistant.py').read_text(encoding='utf-8')
TRIPS = (ROOT / 'trips.py').read_text(encoding='utf-8')

spec = importlib.util.spec_from_file_location('rit_tank_test_app_v2300', ROOT / 'app.py')
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class Release2300SourceTests(unittest.TestCase):
    def test_private_assistant_actions_and_validation_are_removed(self):
        self.assertNotIn('Kies Privé of Zakelijk.', ASSISTANT)
        self.assertNotIn("'RITTANK_PRIVATE_", ASSISTANT)
        self.assertIn("'RITTANK_BUSINESS_", ASSISTANT)
        self.assertIn("trip_type = 'business'", ASSISTANT)

    def test_trip_suggestions_no_longer_propose_private(self):
        self.assertIn("'suggested_type': 'business'", TRIPS)
        self.assertNotIn("winner = 'business' if business >= private else 'private'", TRIPS)


class Release2300BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [
            patch.object(app, 'publish_sensors_async', lambda: None),
            patch.object(app, 'send_assistant_notification', lambda item: None),
        ]
        for item in self.patches:
            item.start()
        app.init_db()
        with app.db() as con:
            con.execute(
                "INSERT INTO events(created_at,type,odometer) VALUES(?, 'odometer', ?)",
                ('2026-09-23T08:00:00+02:00', 100),
            )
            con.execute(
                "INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?)",
                ('Thuis', 52.0, 6.0, '2026-09-23T08:00:00+02:00', '2026-09-23T08:00:00+02:00'),
            )
            con.execute(
                "INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?)",
                ('Klant', 52.1, 6.1, '2026-09-23T08:00:00+02:00', '2026-09-23T08:00:00+02:00'),
            )
        self.home, self.client = (app.known_places_all()[0]['id'], app.known_places_all()[1]['id'])

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def arrival(self):
        with patch.object(app, 'iso_local', return_value='2026-09-23T08:35:00+02:00'):
            return app.create_assistant_arrival(
                self.home,
                self.client,
                52.1,
                6.1,
                10,
                '2026-09-23T08:20:00+02:00',
                route_snapshot={'route_m': 10000, 'route_samples': 5, 'route_incomplete': False},
            )

    def test_confirm_ignores_private_payload_and_marks_business(self):
        created = self.arrival()
        result = app.confirm_assistant_arrival(created['id'], 'private')
        self.assertEqual(result['confirmed_type'], 'business')

    def test_complete_ignores_private_payload_and_saves_business_trip(self):
        created = self.arrival()
        result = app.complete_assistant_arrival(
            created['id'],
            {'trip_type': 'private', 'start_odometer': 100, 'odometer': 110},
        )
        self.assertTrue(result['ok'])
        with app.db() as con:
            trip = con.execute('SELECT trip_type FROM business_trips WHERE id=?', (result['trip_id'],)).fetchone()
            arrival = con.execute('SELECT confirmed_type FROM assistant_arrivals WHERE id=?', (created['id'],)).fetchone()
        self.assertEqual(trip['trip_type'], 'business')
        self.assertEqual(arrival['confirmed_type'], 'business')


if __name__ == '__main__':
    unittest.main()


class ReimbursementTests(unittest.TestCase):
    """Test reimbursement rate and calculations for release 23.00."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [
            patch.object(app, 'publish_sensors_async', lambda: None),
        ]
        for item in self.patches:
            item.start()
        app.init_db()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_km_reimbursement_rate_defaults_to_0_25(self):
        settings = app.get_settings()
        rate = float(settings.get('km_reimbursement_rate') or 0.25)
        self.assertEqual(rate, 0.25)

    def test_km_reimbursement_rate_can_be_set(self):
        app.set_settings({'km_reimbursement_rate': '0.35'})
        settings = app.get_settings()
        rate = float(settings.get('km_reimbursement_rate') or 0.25)
        self.assertEqual(rate, 0.35)

    def test_km_reimbursement_rate_accepts_comma_input(self):
        app.set_settings({'km_reimbursement_rate': '0,25'})
        settings = app.get_settings()
        rate = float(settings.get('km_reimbursement_rate') or 0.25)
        self.assertEqual(rate, 0.25)

    def test_km_reimbursement_rate_rejects_negative_values(self):
        with self.assertRaisesRegex(ValueError, 'niet negatief'):
            app.set_settings({'km_reimbursement_rate': '-0.01'})

    def test_km_reimbursement_rate_rejects_text_values(self):
        with self.assertRaisesRegex(ValueError, 'geldige kilometervergoeding'):
            app.set_settings({'km_reimbursement_rate': 'abc'})

    def test_reimbursement_10km_at_0_25(self):
        reimbursement = app.pdf_report.calculate_km_reimbursement(10, '0.25')
        self.assertEqual(reimbursement, Decimal('2.50'))

    def test_reimbursement_31km_at_0_25(self):
        reimbursement = app.pdf_report.calculate_km_reimbursement(31, '0.25')
        self.assertEqual(reimbursement, Decimal('7.75'))

    def test_reimbursement_7km_at_0_23(self):
        reimbursement = app.pdf_report.calculate_km_reimbursement(7, '0.23')
        self.assertEqual(reimbursement, Decimal('1.61'))

    def test_reimbursement_10_5km_at_0_25(self):
        reimbursement = app.pdf_report.calculate_km_reimbursement('10.5', '0.25')
        self.assertEqual(reimbursement, Decimal('2.63'))

    def test_pdf_and_csv_use_same_decimal_reimbursement_helper(self):
        amount = app.pdf_report.calculate_km_reimbursement('31', '0.25')
        self.assertEqual(app.pdf_report.format_decimal_plain(amount), '7.75')

        class DummyHandler:
            def __init__(self):
                self.wfile = io.BytesIO()
                self.status = None
                self.headers = {}

            def send_response(self, status):
                self.status = status

            def send_header(self, key, value):
                self.headers[key] = value

            def end_headers(self):
                pass

        with app.db() as con:
            cur = con.execute(
                "INSERT INTO business_trips(started_at,ended_at,status,trip_type) "
                "VALUES(?, ?, 'completed', 'business')",
                ('2026-09-23T08:00:00+02:00', '2026-09-23T08:30:00+02:00'),
            )
            trip_id = cur.lastrowid
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 0, ?, 90000, 52.0, 6.0, 'manual')",
                (trip_id, '2026-09-23T08:00:00+02:00'),
            )
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 1, ?, 90031, 52.1, 6.1, 'manual')",
                (trip_id, '2026-09-23T08:30:00+02:00'),
            )
            con.commit()

        handler = DummyHandler()
        app.Handler.export_business_csv(handler, 'all')
        data = handler.wfile.getvalue().decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(data), delimiter=';'))
        self.assertTrue(rows)
        self.assertEqual(rows[0]['reimbursement_eur'], app.pdf_report.format_decimal_plain(amount))


class OdometerGapTests(unittest.TestCase):
    """Test that odometer gaps between separate business trips are allowed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [
            patch.object(app, 'publish_sensors_async', lambda: None),
        ]
        for item in self.patches:
            item.start()
        app.init_db()
        with app.db() as con:
            con.execute(
                "INSERT INTO events(created_at,type,odometer) VALUES(?, 'odometer', ?)",
                ('2026-09-23T08:00:00+02:00', 90000),
            )
            con.commit()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_odometer_gap_scenario_90000_90020_gap_90030_90055_allowed(self):
        """
        Critical scenario: allow odometer gap between separate business trips.
        Business trip A: 90000 -> 90020
        Private ride (unregistered): 90020 -> 90030
        Business trip B: 90030 -> 90055
        """
        app.start_business_trip({
            'created_at': '2026-09-23T08:00:00+02:00',
            'odometer': 90000,
            'latitude': 52.0,
            'longitude': 6.0,
            'manual_label': 'Start A',
        })
        app.add_business_stop({
            'created_at': '2026-09-23T08:30:00+02:00',
            'odometer': 90020,
            'latitude': 52.1,
            'longitude': 6.1,
            'manual_label': 'Einde A',
            'segment_trip_type': 'private',
        }, finish=True)

        app.start_business_trip({
            'created_at': '2026-09-23T10:00:00+02:00',
            'odometer': 90030,
            'latitude': 52.1,
            'longitude': 6.1,
            'manual_label': 'Start B',
        })
        app.add_business_stop({
            'created_at': '2026-09-23T11:00:00+02:00',
            'odometer': 90055,
            'latitude': 52.2,
            'longitude': 6.2,
            'manual_label': 'Einde B',
        }, finish=True)

        # Verify both trips exist and have correct odometers
        trips_raw = list(app.business_trips_raw())
        self.assertEqual(len(trips_raw), 2)

        trip_a, stops_a = trips_raw[0]
        trip_b, stops_b = trips_raw[1]

        self.assertEqual(float(stops_a[-1]['odometer']), 90020)
        self.assertEqual(float(stops_b[0]['odometer']), 90030)
        self.assertEqual(trip_a['trip_type'], 'business')
        self.assertEqual(trip_b['trip_type'], 'business')
        self.assertTrue(all((stop.get('segment_trip_type') in (None, 'business')) for stop in stops_a + stops_b))
        # Gap of 10 km (90020 -> 90030) is allowed between separate trips

    def test_within_trip_odometer_still_validated(self):
        """Test that within-trip validation (start <= end) is still enforced."""
        app.start_business_trip({
            'created_at': '2026-09-23T08:00:00+02:00',
            'odometer': 90030,
            'latitude': 52.0,
            'longitude': 6.0,
            'manual_label': 'Start',
        })
        with self.assertRaisesRegex(ValueError, 'lager'):
            app.add_business_stop({
                'created_at': '2026-09-23T08:30:00+02:00',
                'odometer': 90020,
                'latitude': 52.1,
                'longitude': 6.1,
                'manual_label': 'Einde',
            }, finish=True)
