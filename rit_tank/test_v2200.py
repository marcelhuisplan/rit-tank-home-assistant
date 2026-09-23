"""Release 22.00 regression tests for chronological assistant review."""
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

spec = importlib.util.spec_from_file_location('rit_tank_test_app_v2200', Path(__file__).with_name('app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class ChronologicalReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
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
                ('2026-09-22T08:00:00+02:00', 100),
            )
            con.execute(
                "INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?)",
                ('Thuis', 52.0, 6.0, '2026-09-22T08:00:00+02:00', '2026-09-22T08:00:00+02:00'),
            )
            con.execute(
                "INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?)",
                ('School', 52.1, 6.1, '2026-09-22T08:00:00+02:00', '2026-09-22T08:00:00+02:00'),
            )
        self.home, self.school = (app.known_places_all()[0]['id'], app.known_places_all()[1]['id'])

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def arrival(self, origin, destination, departure_at, detected_at):
        latitude, longitude = (52.1, 6.1) if destination == self.school else (52.2, 6.2)
        with patch.object(app, 'iso_local', return_value=detected_at):
            return app.create_assistant_arrival(
                origin, destination, latitude, longitude, 10, departure_at,
                route_snapshot={'route_m': 10000, 'route_samples': 5, 'route_incomplete': False},
            )

    def test_oldest_departure_wins_over_id_and_later_direct_completion_has_no_side_effects(self):
        later = self.arrival(self.school, self.home, '2026-09-22T08:40:00+02:00', '2026-09-22T08:55:00+02:00')
        earlier = self.arrival(self.home, self.school, '2026-09-22T08:20:00+02:00', '2026-09-22T08:35:00+02:00')

        queue = app.assistant_arrivals()
        self.assertEqual([item['id'] for item in queue], [earlier['id'], later['id']])
        self.assertTrue(queue[0]['is_next_to_review'])
        self.assertEqual(queue[1]['blocked_by_arrival_id'], earlier['id'])
        with app.db() as con:
            before = (
                con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0],
                con.execute('SELECT COUNT(*) FROM events').fetchone()[0],
            )
        with self.assertRaises(app.assistant.AssistantArrivalQueueError) as caught:
            app.complete_assistant_arrival(later['id'], {'trip_type': 'business', 'start_odometer': 110, 'odometer': 120})
        self.assertEqual(caught.exception.blocking_arrival_id, earlier['id'])
        with app.db() as con:
            self.assertEqual(con.execute('SELECT status FROM assistant_arrivals WHERE id=?', (later['id'],)).fetchone()['status'], 'pending')
            self.assertEqual(before[0], con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0])
            self.assertEqual(before[1], con.execute('SELECT COUNT(*) FROM events').fetchone()[0])

        app.complete_assistant_arrival(earlier['id'], {'trip_type': 'business', 'start_odometer': 100, 'odometer': 110})
        next_item = app.assistant_arrivals()[0]
        self.assertEqual(next_item['id'], later['id'])
        self.assertTrue(next_item['is_next_to_review'])
        self.assertEqual(next_item['proposal']['start_odometer'], 110)
        app.complete_assistant_arrival(later['id'], {'trip_type': 'business', 'start_odometer': 110, 'odometer': 120})

    def test_detected_time_fallback_ties_and_confirmed_arrivals_remain_blocking(self):
        first = self.arrival(self.home, self.school, None, '2026-09-22T08:20:00+02:00')
        second = self.arrival(self.school, self.home, None, '2026-09-22T08:20:00+02:00')
        queue = app.assistant_arrivals()
        self.assertEqual([item['id'] for item in queue], [first['id'], second['id']])
        app.confirm_assistant_arrival(first['id'], 'business')
        with self.assertRaises(app.assistant.AssistantArrivalQueueError):
            app.correct_assistant_arrival_route(second['id'], {
                'destination': {'latitude': 52.2, 'longitude': 6.2, 'address': 'Later'},
            })
        app.dismiss_assistant_arrival(first['id'])
        self.assertTrue(app.assistant_arrivals()[0]['is_next_to_review'])
