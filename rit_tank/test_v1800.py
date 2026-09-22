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

    def test_gps_only_new_place_stores_current_position_without_address(self):
        place = app.save_known_place({
            'name': 'Nieuwe plek', 'latitude': 52.31554, 'longitude': 6.52766, 'address': '',
        })
        self.assertEqual(place['latitude'], 52.31554)
        self.assertEqual(place['longitude'], 6.52766)
        self.assertFalse(place['address'])

    def test_gps_only_moves_existing_place_and_clears_address(self):
        place = app.save_known_place({
            'name': 'Thuis', 'latitude': 52.0, 'longitude': 6.0,
            'address': 'Oude straat 1, Rijssen',
        })
        updated = app.save_known_place({
            'name': 'Thuis', 'latitude': 52.31554, 'longitude': 6.52766, 'address': '',
        }, place['id'])
        self.assertEqual((updated['latitude'], updated['longitude']), (52.31554, 6.52766))
        self.assertEqual(updated['address'], '')

    def test_edit_without_location_choice_keeps_coordinates_and_address(self):
        place = app.save_known_place({
            'name': 'Thuis', 'latitude': 52.0, 'longitude': 6.0, 'radius_m': 180,
            'address': 'Verenlandweg 4, 7461 AP Rijssen',
        })
        updated = app.save_known_place({
            'name': 'Thuis hernoemd', 'latitude': 52.0, 'longitude': 6.0, 'radius_m': 250,
        }, place['id'])
        self.assertEqual((updated['latitude'], updated['longitude']), (52.0, 6.0))
        self.assertEqual(updated['address'], 'Verenlandweg 4, 7461 AP Rijssen')
        self.assertEqual(updated['radius_m'], 250)

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
    def _function_body(self, signature):
        start = APP.index(signature)
        return APP[start:APP.index('\n', start)]

    def test_ui_keeps_legacy_and_selected_address_distinct(self):
        self.assertIn('Adres nog niet opgeslagen', APP)
        self.assertIn('address:KNOWN_EDIT_LOCATION?.address||\'\'', APP)
        self.assertIn('Voorstel gevonden (nog niet opgeslagen)', APP)

    def test_gps_only_writes_validated_coordinates_to_the_saved_fields(self):
        body = self._function_body('function useKnownPlaceGpsOnly()')
        self.assertIn('Number.isFinite(lat)', body)
        self.assertIn('Number.isFinite(lon)', body)
        self.assertIn("$('knownLat').value=lat", body)
        self.assertIn("$('knownLon').value=lon", body)
        self.assertIn("address:''", body)
        self.assertIn('Alleen GPS-positie gekozen', body)
        self.assertIn('GPS-positie: ${fmt(lat,5)} · ${fmt(lon,5)}', body)

    def test_legacy_address_proposal_is_selectable_and_never_auto_saved(self):
        body = self._function_body('async function editKnownPlace(id)')
        self.assertIn("renderKnownPlaceCandidates('knownAddressChoices',[candidate])", body)
        self.assertNotIn('api/known-places', body)
        self.assertIn('selectKnownPlaceLocation(candidate)', APP)

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
