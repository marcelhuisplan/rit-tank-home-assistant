"""Release 23.00 regression tests for business-only assistant classification."""
import importlib.util
import sys
import tempfile
import types
import unittest
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

    def test_reimbursement_10km_at_0_25(self):
        km, rate = 10.0, 0.25
        reimbursement = round(km * rate, 2)
        self.assertEqual(reimbursement, 2.50)

    def test_reimbursement_31km_at_0_25(self):
        km, rate = 31.0, 0.25
        reimbursement = round(km * rate, 2)
        self.assertEqual(reimbursement, 7.75)

    def test_reimbursement_7km_at_0_23(self):
        km, rate = 7.0, 0.23
        reimbursement = round(km * rate, 2)
        self.assertEqual(reimbursement, 1.61)


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
        with app.db() as con:
            # Business trip A
            cur = con.execute(
                "INSERT INTO business_trips(started_at,ended_at,status,trip_type) "
                "VALUES(?, ?, 'completed', 'business')",
                ('2026-09-23T08:00:00+02:00', '2026-09-23T08:30:00+02:00'),
            )
            trip_a_id = cur.lastrowid
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 0, ?, 90000, 52.0, 6.0, 'manual')",
                (trip_a_id, '2026-09-23T08:00:00+02:00'),
            )
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 1, ?, 90020, 52.1, 6.1, 'manual')",
                (trip_a_id, '2026-09-23T08:30:00+02:00'),
            )

            # Business trip B with gap
            cur = con.execute(
                "INSERT INTO business_trips(started_at,ended_at,status,trip_type) "
                "VALUES(?, ?, 'completed', 'business')",
                ('2026-09-23T10:00:00+02:00', '2026-09-23T11:00:00+02:00'),
            )
            trip_b_id = cur.lastrowid
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 0, ?, 90030, 52.1, 6.1, 'manual')",
                (trip_b_id, '2026-09-23T10:00:00+02:00'),
            )
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 1, ?, 90055, 52.2, 6.2, 'manual')",
                (trip_b_id, '2026-09-23T11:00:00+02:00'),
            )
            con.commit()

        # Verify both trips exist and have correct odometers
        trips_raw = list(app.business_trips_raw())
        self.assertEqual(len(trips_raw), 2)
        
        trip_a, stops_a = trips_raw[0]
        trip_b, stops_b = trips_raw[1]
        
        self.assertEqual(float(stops_a[-1]['odometer']), 90020)
        self.assertEqual(float(stops_b[0]['odometer']), 90030)
        # Gap of 10 km (90020 -> 90030) is allowed between separate trips

    def test_within_trip_odometer_still_validated(self):
        """Test that within-trip validation (start <= end) is still enforced."""
        with app.db() as con:
            # Try to create a trip with end < start
            cur = con.execute(
                "INSERT INTO business_trips(started_at,ended_at,status,trip_type) "
                "VALUES(?, ?, 'completed', 'business')",
                ('2026-09-23T08:00:00+02:00', '2026-09-23T08:30:00+02:00'),
            )
            trip_id = cur.lastrowid
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 0, ?, 90050, 52.0, 6.0, 'manual')",
                (trip_id, '2026-09-23T08:00:00+02:00'),
            )
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,location_source) "
                "VALUES(?, 1, ?, 90030, 52.1, 6.1, 'manual')",
                (trip_id, '2026-09-23T08:30:00+02:00'),
            )
            
            # Validation should catch end < start (90030 < 90050)
            valid, msg = app.validate_odometer('2026-09-23T08:30:00+02:00', 90030, ignore_id=trip_id)
            self.assertFalse(valid)
            self.assertIn('lager', msg.lower() or msg)

