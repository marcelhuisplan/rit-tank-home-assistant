"""Release 24.00 regressions for the manual business-trip workflow."""
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
spec = importlib.util.spec_from_file_location('rit_tank_test_app_v2400', ROOT / 'app.py')
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class Release2400BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [
            patch.object(app, 'publish_sensors_async', lambda: None),
            patch.object(app, 'send_assistant_notification', lambda item: None),
            patch.object(app, 'google_reverse_geocode', lambda *args: {}),
        ]
        for item in self.patches:
            item.start()
        app.init_db()
        with app.db() as con:
            con.execute(
                "INSERT INTO events(created_at,type,odometer) VALUES(?,'odometer',?)",
                (app.iso_local(app.now_local().replace(hour=0, minute=0, second=0, microsecond=0)), 10000),
            )

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def start_trip(self):
        return app.start_business_trip({
            'odometer': 10000,
            'created_at': app.iso_local(),
            'latitude': 52.0,
            'longitude': 6.0,
            'manual_label': 'Startadres',
        })

    def test_manual_trip_suppresses_assistant_arrival_without_creating_a_record(self):
        trip = self.start_trip()['trip']
        result = app.create_assistant_arrival(
            None, None, 52.1, 6.1, 10, app.iso_local(), 'Automatische locatie',
            route_snapshot={'route_m': 1000, 'route_samples': 2, 'route_incomplete': False},
        )
        self.assertIsNone(result)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM assistant_arrivals').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT status FROM business_trips WHERE id=?", (trip['id'],)).fetchone()[0], 'active')

    def test_stop_notification_is_a_reminder_and_does_not_change_the_active_trip(self):
        trip = self.start_trip()['trip']
        payloads = []
        with patch.object(app, 'assistant_config', return_value={'notify_service': 'notify.mobile_app_phone'}), \
                patch.object(app, 'ha_post', side_effect=lambda path, payload: payloads.append((path, payload))):
            self.assertTrue(app.send_active_trip_stop_notification(
                trip, 2500, 'Overijssel', {'address': 'Diekjanweg 2, 7462 JD Rijssen'}
            ))
        path, payload = payloads[0]
        self.assertEqual(path, 'services/notify/mobile_app_phone')
        self.assertEqual(payload['title'], '🚗 Rit & Tank · actieve rit')
        message = payload['message']
        self.assertIn('Je bent mogelijk gestopt bij Diekjanweg 2, 7462 JD Rijssen.', message)
        self.assertIn('Er staat nog een actieve zakelijke rit open.', message)
        self.assertIn('Open Rit & Tank', message)
        self.assertNotIn('opslaan', message.casefold())
        self.assertNotIn('wil je', message.casefold())
        self.assertEqual(payload['data']['actions'], [{
            'action': 'OPEN', 'title': 'Open Rit & Tank', 'uri': 'https://rit.huisplanadvies.nl'
        }])
        with app.db() as con:
            self.assertEqual(con.execute("SELECT status FROM business_trips WHERE id=?", (trip['id'],)).fetchone()[0], 'active')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops WHERE trip_id=?', (trip['id'],)).fetchone()[0], 1)

    def test_home_reuses_only_an_exact_valid_thuis_entry(self):
        with app.db() as con:
            con.execute(
                'INSERT INTO known_places(name,category,latitude,longitude,address,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                ('Thuis', 'home', 52.3, 6.2, app.HOME_ADDRESS, app.iso_local(), app.iso_local()),
            )
        with patch.object(app, 'google_places_text_search') as search:
            result = app.home_destination()
        search.assert_not_called()
        self.assertEqual(result['address'], app.HOME_ADDRESS)
        self.assertEqual((result['latitude'], result['longitude']), (52.3, 6.2))
        self.assertEqual(result['source'], 'known_place')
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM known_places').fetchone()[0], 1)

    def test_home_uses_places_fallback_without_saving_a_known_place(self):
        place = {
            'place_id': 'home-place-123',
            'name': 'Verenlandweg 4',
            'address': app.HOME_ADDRESS + ', Netherlands',
            'latitude': 52.3,
            'longitude': 6.2,
        }
        with patch.object(app, 'google_places_text_search', return_value=[place]) as search:
            result = app.home_destination()
        search.assert_called_once_with(app.HOME_ADDRESS)
        self.assertEqual(result['address'], app.HOME_ADDRESS)
        self.assertEqual(result['place_id'], 'home-place-123')
        self.assertIsNone(result['known_place_id'])
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM known_places').fetchone()[0], 0)

    def test_home_rejects_wrong_or_invalid_places_results(self):
        candidates = [
            {'address': 'Verenlandweg 40, 7461 AP Rijssen', 'latitude': 52.3, 'longitude': 6.2},
            {'address': app.HOME_ADDRESS, 'latitude': 999, 'longitude': 6.2},
        ]
        with patch.object(app, 'google_places_text_search', return_value=candidates):
            with self.assertRaisesRegex(ValueError, 'niet met geldige coördinaten'):
                app.home_destination()
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM known_places').fetchone()[0], 0)

    def test_route_preview_uses_existing_route_and_does_not_save_a_stop(self):
        self.start_trip()
        with patch.object(app, 'get_route_distance', return_value={'distance_m': 5000, 'type': 'route'}) as route, \
                patch.object(app, 'distance_calibration', return_value={'factor': 1.2, 'samples': 7}):
            result = app.preview_business_destination({'latitude': 52.1, 'longitude': 6.1})
        route.assert_called_once_with(52.0, 6.0, 52.1, 6.1)
        self.assertEqual(result['distance_source'], 'route')
        self.assertEqual(result['suggested_odometer'], 10005)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops').fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT status FROM business_trips").fetchone()[0], 'active')


class Release2400CurrentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = app.APP_HTML if isinstance(app.APP_HTML, str) else app.APP_HTML.decode('utf-8')

    def test_manual_trip_ui_has_home_and_current_location_but_no_review_or_type_picker(self):
        for text in (
            'id="tripHomeButton"', '🏠 Thuis', '📍 Gebruik huidige locatie',
            'Verenlandweg 4, 7461 AP Rijssen', 'async function selectTripHome()',
            "api('api/places/home')", "api('api/business/route-preview'",
            'manual_label:label', "place_id:location.place_id||''",
        ):
            with self.subTest(text=text):
                self.assertIn(text, self.html)
        for text in (
            'assistantPanel', 'assistantList', 'Kies dit traject',
            'Privé-omrijkilometers', 'tripPrivateDetour', 'editTripDetour',
            'editTripType', 'Te controleren',
        ):
            with self.subTest(text=text):
                self.assertNotIn(text, self.html)

    def test_home_button_only_selects_destination_until_explicit_save(self):
        start = self.html.index('async function selectTripHome()')
        end = self.html.index("$('tripManualAddress').addEventListener", start)
        select_home = self.html[start:end]
        self.assertIn("api('api/places/home')", select_home)
        self.assertIn('await confirmTripAddress', select_home)
        self.assertNotIn('saveTripPoint', select_home)
        self.assertNotIn('closeModal', select_home)
        confirm_start = self.html.index('async function confirmTripAddress(')
        confirm_end = self.html.index('async function selectTripHome()', confirm_start)
        confirm_address = self.html[confirm_start:confirm_end]
        self.assertIn("api('api/business/route-preview'", confirm_address)
        self.assertNotIn('saveTripPoint', confirm_address)
        self.assertNotIn('closeModal', confirm_address)


if __name__ == '__main__':
    unittest.main()
