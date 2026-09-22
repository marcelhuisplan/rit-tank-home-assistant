"""Release 18.00 known-place, dictation, and live-refresh regressions."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('rit_tank_release_1800_app', ROOT / 'app.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
APP = (ROOT / 'app.py').read_text(encoding='utf-8')


class KnownPlaceAddressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'rit_tank.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        app.init_db()
        self.sync_patch = patch.object(app, 'setting_bool', lambda *args, **kwargs: False)
        self.sync_patch.start()

    def tearDown(self):
        self.sync_patch.stop()
        self.tmp.cleanup()

    def test_selected_address_is_stored_and_preserved(self):
        place = app.save_known_place({
            'name': 'Thuis', 'latitude': 52.31554, 'longitude': 6.52766,
            'address': 'Verenlandweg 4, 7461 AP Rijssen',
        })
        self.assertEqual(place['address'], 'Verenlandweg 4, 7461 AP Rijssen')
        updated = app.save_known_place({
            'name': 'Thuis aangepast', 'latitude': 52.31554, 'longitude': 6.52766,
        }, place['id'])
        self.assertEqual(updated['address'], 'Verenlandweg 4, 7461 AP Rijssen')

    def test_gps_only_clears_address_without_changing_coordinates(self):
        place = app.save_known_place({
            'name': 'GPS', 'latitude': 52.0, 'longitude': 6.0,
            'address': 'Oude straat 1, Rijssen',
        })
        updated = app.save_known_place({
            'name': 'GPS', 'latitude': 52.1, 'longitude': 6.1, 'address': '',
        }, place['id'])
        self.assertEqual(updated['address'], '')
        self.assertEqual((updated['latitude'], updated['longitude']), (52.1, 6.1))

    def test_address_column_migration_is_repeatable(self):
        app.init_db()
        with app.db() as con:
            columns = [row['name'] for row in con.execute('PRAGMA table_info(known_places)')]
        self.assertEqual(columns.count('address'), 1)


class Release1800SourceTests(unittest.TestCase):
    def test_ui_keeps_legacy_and_selected_address_distinct(self):
        self.assertIn('Adres nog niet opgeslagen', APP)
        self.assertIn('address:KNOWN_EDIT_LOCATION?.address||\'\'', APP)
        self.assertIn('Voorstel (nog niet opgeslagen)', APP)

    def test_dictation_searches_before_trip_address_selection(self):
        self.assertIn("startAddressDictation('tripManualAddress',searchTripManualAddress)", APP)
        self.assertIn("api('api/places/search-address'", APP)

    def test_live_refresh_is_light_and_separate(self):
        self.assertIn('setInterval(refreshVisibleDashboard,30000)', APP)
        self.assertIn('setInterval(refreshActiveTripLiveStatus,5000)', APP)
        self.assertIn("api('api/business/odometer-suggestion')", APP)
        self.assertIn('LIVE_TRIP_REFRESH_BUSY', APP)
        self.assertIn("document.hidden", APP)


if __name__ == '__main__':
    unittest.main()
