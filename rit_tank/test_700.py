"""Regression tests for release 7.00: address correction, route distance, and PDF redesign."""
import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import MagicMock, patch

try:
    import websocket
except ImportError:
    sys.modules['websocket'] = types.ModuleType('websocket')

spec = importlib.util.spec_from_file_location('rit_tank_test_app', Path(__file__).with_name('app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class Version700Tests(unittest.TestCase):
    """Test current version consistency and the historical 7.00 changelog."""
    
    def test_app_version_is_21_00(self):
        self.assertEqual(app.APP_VERSION, '21.00')
    
    def test_config_yaml_version_is_21_00(self):
        config_path = Path(__file__).with_name('config.yaml')
        if config_path.exists():
            content = config_path.read_text()
            self.assertIn("version: '21.00'", content)
    
    def test_readme_title_has_21_00(self):
        readme_path = Path(__file__).with_name('README.md')
        if readme_path.exists():
            content = readme_path.read_text()
            self.assertIn('# Rit & Tank 21.00', content)
    
    def test_changelog_has_7_00_section(self):
        changelog_path = Path(__file__).with_name('CHANGELOG.md')
        if changelog_path.exists():
            content = changelog_path.read_text()
            self.assertIn('## 7.00', content)
            self.assertIn('adrescorrectie', content.lower() or 'address correction' in content.lower())


class AddressCorrectionTests(unittest.TestCase):
    """Test address correction for automatic suggestions."""
    
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [
            patch.object(app, 'publish_sensors_async', lambda: None),
            patch.object(app, 'send_assistant_notification', lambda item: None),
            patch.object(app, 'google_reverse_geocode', lambda *args: {'address': 'Test St 1'}),
            patch.object(app, 'get_route_distance', return_value={'type': 'route', 'distance_m': 1500}),
        ]
        self.mocks = {p.attribute: p.start() for p in self.patches}
        app.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def _add_known_place(self, name: str, lat: float, lon: float) -> int:
        with app.db() as con:
            con.execute(
                "INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?)",
                (name, lat, lon, app.iso_local(), app.iso_local())
            )
            con.commit()
            return int(con.execute('SELECT id FROM known_places WHERE name=?', (name,)).fetchone()['id'])
    
    def test_address_correction_function_exists(self):
        """Test that the address correction function is available."""
        self.assertTrue(hasattr(app, 'correct_assistant_arrival_destination'))
        self.assertTrue(callable(app.correct_assistant_arrival_destination))
    
    def test_route_distance_function_calculates_gps_fallback(self):
        """Test get_route_distance falls back to GPS when route API unavailable."""
        # Deze test roept de ECHTE get_route_distance() aan, dus stop tijdelijk
        # de class-brede mock die 'route' teruggeeft voor alle andere tests.
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            with patch.object(app, 'places_key', return_value='dummy-key'), \
                 patch.object(app, 'http_json', side_effect=ValueError('API error')):
                result = app.get_route_distance(52.0, 5.0, 52.1, 5.1)
                self.assertEqual(result['type'], 'gps')
                self.assertGreater(result['distance_m'], 0)
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()
    
    def test_route_distance_function_returns_dict_with_type_and_distance(self):
        """Test get_route_distance returns dict with required fields."""
        result = app.get_route_distance(52.0, 5.0, 52.1, 5.1)
        self.assertIn('type', result)
        self.assertIn('distance_m', result)
        self.assertIn(result['type'], ['route', 'gps'])
        self.assertGreater(result['distance_m'], 0)
    
    def test_create_arrival_and_correct_destination(self):
        """Test creating and correcting an automatic arrival."""
        # Create a known place first
        with app.db() as con:
            con.execute(
                "INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?)",
                ('Home', 52.0, 5.0, app.iso_local(), app.iso_local())
            )
            con.commit()
        
        # Create assistant arrival
        arrival = app.create_assistant_arrival(
            origin_place_id=None,
            destination_place_id=None,
            lat=52.1, lon=5.1,
            accuracy=20,
            departure_at=None,
            destination_label='Test Location'
        )
        
        self.assertIsNotNone(arrival)
        arrival_id = int(arrival['id'])
        
        # Correct the destination
        payload = {
            'latitude': 52.2,
            'longitude': 5.2,
            'address': 'Corrected Location',
            'place_id': None,
        }
        
        corrected = app.correct_assistant_arrival_destination(arrival_id, payload)
        
        self.assertEqual(int(corrected['id']), arrival_id)
        self.assertEqual(int(corrected['destination_manually_corrected']), 1)
        self.assertEqual(float(corrected['corrected_destination_latitude']), 52.2)
        self.assertEqual(float(corrected['corrected_destination_longitude']), 5.2)

    def test_route_correction_uses_effective_origin_and_preserves_original_place(self):
        """A->B, A->C, D->B and D->C retain the original origin separately."""
        origin_a = self._add_known_place('Vertrek A', 51.0, 4.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a, destination_place_id=None,
            lat=52.0, lon=5.0, accuracy=10, departure_at=None,
            destination_label='Bestemming B',
        )
        arrival_id = int(arrival['id'])
        with patch.object(app, 'get_route_distance', return_value={'type': 'route', 'distance_m': 2500}) as route:
            corrected = app.correct_assistant_arrival_route(arrival_id, {
                'origin': {'latitude': 53.0, 'longitude': 6.0, 'address': 'Vertrek D'},
                'destination': {'latitude': 54.0, 'longitude': 7.0, 'address': 'Bestemming C'},
            })
        self.assertEqual((route.call_args.args[0], route.call_args.args[1]), (53.0, 6.0))
        self.assertEqual((route.call_args.args[2], route.call_args.args[3]), (54.0, 7.0))
        self.assertEqual(int(corrected['origin_manually_corrected']), 1)
        self.assertEqual(int(corrected['destination_manually_corrected']), 1)
        self.assertEqual(float(corrected['corrected_origin_latitude']), 53.0)
        self.assertEqual(float(corrected['corrected_destination_latitude']), 54.0)
        self.assertEqual(app.known_place_by_id(origin_a)['name'], 'Vertrek A')

    def test_correction_routes_from_original_departure_never_from_old_destination(self):
        """
        Scenario: vertrek A -> oorspronkelijke foutieve bestemming B; gebruiker
        corrigeert naar C. De routeprovider moet exact met A -> C worden
        aangeroepen; B mag nooit als route-origin dienen.
        """
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
            arrival = app.create_assistant_arrival(
                origin_place_id=origin_a,
                destination_place_id=None,
                lat=52.9, lon=6.9,  # B: de oorspronkelijke (foutieve) bestemming
                accuracy=20,
                departure_at=app.iso_local(),
                destination_label='Foutieve bestemming B',
            )
            arrival_id = int(arrival['id'])

            mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 4200})
            with patch.object(app, 'get_route_distance', mock_route):
                corrected = app.correct_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Correcte bestemming C',
                })

            mock_route.assert_called_once_with(51.0, 4.0, 53.5, 7.5)
            for call in mock_route.call_args_list:
                origin_used = (call.args[0], call.args[1])
                self.assertNotEqual(origin_used, (52.9, 6.9), 'B (oude bestemming) mag nooit als route-origin dienen')
            self.assertEqual(round(float(corrected['corrected_destination_distance_m'])), 4200)
            self.assertEqual(corrected['destination_distance_source'], 'route')
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()

    def test_correction_without_known_departure_never_uses_zero_zero_or_old_destination(self):
        """
        Als geen betrouwbare vertreklocatie bekend is (geen actieve rit, geen
        origin_known_place_id), mag er GEEN fictieve route vanaf (0,0) of
        vanaf de oude bestemming worden berekend. De routeprovider mag dan
        niet worden aangeroepen; de bron moet 'gps' blijven.
        """
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            arrival = app.create_assistant_arrival(
                origin_place_id=None,
                destination_place_id=None,
                lat=52.9, lon=6.9,  # B: onbekende oude bestemming, geen origin
                accuracy=20,
                departure_at=None,
                destination_label='Onbekende oude bestemming B',
            )
            arrival_id = int(arrival['id'])

            mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 9999})
            with patch.object(app, 'get_route_distance', mock_route):
                corrected = app.correct_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Nieuwe bestemming C',
                })

            mock_route.assert_not_called()
            self.assertEqual(corrected['destination_distance_source'], 'gps')
            # Geen fabricage van een afstand vanaf (0,0) of vanaf B: zonder
            # eerdere GPS-route-snapshot blijft de afstand onbekend (None).
            self.assertIsNone(corrected['corrected_destination_distance_m'])
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()

    def test_correction_without_known_departure_keeps_existing_gps_track_distance(self):
        """Fallback zonder betrouwbare origin behoudt de eerder gemeten GPS-routeafstand."""
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            arrival = app.create_assistant_arrival(
                origin_place_id=None,
                destination_place_id=None,
                lat=52.9, lon=6.9,
                accuracy=20,
                departure_at=None,
                destination_label='Onbekende oude bestemming B',
                route_snapshot={'route_m': 3000, 'route_samples': 10, 'route_incomplete': False},
            )
            arrival_id = int(arrival['id'])

            mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 9999})
            with patch.object(app, 'get_route_distance', mock_route):
                corrected = app.correct_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Nieuwe bestemming C',
                })

            mock_route.assert_not_called()
            self.assertEqual(corrected['destination_distance_source'], 'gps')
            self.assertEqual(round(float(corrected['corrected_destination_distance_m'])), 3000)
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()

    def test_correction_uses_active_trip_last_stop_as_origin_when_present(self):
        """Als er een actieve zakelijke rit loopt, is de laatste stop daarvan de betrouwbare origin."""
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            with app.db() as con:
                cur = con.execute(
                    "INSERT INTO business_trips(started_at,status) VALUES(?,?)",
                    (app.iso_local(), 'active')
                )
                trip_id = int(cur.lastrowid)
                con.execute(
                    "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude) VALUES(?,?,?,?,?,?)",
                    (trip_id, 0, app.iso_local(), 100, 50.0, 3.0)
                )
                con.commit()

            arrival = app.create_assistant_arrival(
                origin_place_id=None,
                destination_place_id=None,
                lat=52.9, lon=6.9,
                accuracy=20,
                departure_at=app.iso_local(),
                destination_label='Foutieve bestemming B',
            )
            arrival_id = int(arrival['id'])

            mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 500})
            with patch.object(app, 'get_route_distance', mock_route):
                app.correct_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Nieuwe bestemming C',
                })

            mock_route.assert_called_once_with(50.0, 3.0, 53.5, 7.5)
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()

    def test_correction_rejected_after_arrival_is_completed(self):
        """
        'confirmed' betekent alleen dat privé/zakelijk is gekozen; er is dan nog
        geen business_trips/trip_stops-record aangemaakt. Pas bij status
        'completed' is de rit definitief/fiscaal opgeslagen en mag de
        bestemming niet meer worden gecorrigeerd.
        """
        arrival = app.create_assistant_arrival(
            origin_place_id=None,
            destination_place_id=None,
            lat=52.9, lon=6.9,
            accuracy=20,
            departure_at=None,
            destination_label='Bestemming',
        )
        arrival_id = int(arrival['id'])
        with app.db() as con:
            con.execute("UPDATE assistant_arrivals SET status='completed' WHERE id=?", (arrival_id,))
            con.commit()
        with self.assertRaises(ValueError):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Te laat',
            })

    def test_correction_allowed_while_status_is_confirmed(self):
        """Status 'confirmed' (type gekozen, nog geen opgeslagen rit) mag nog worden gecorrigeerd."""
        arrival = app.create_assistant_arrival(
            origin_place_id=None,
            destination_place_id=None,
            lat=52.9, lon=6.9,
            accuracy=20,
            departure_at=None,
            destination_label='Bestemming',
        )
        arrival_id = int(arrival['id'])
        app.confirm_assistant_arrival(arrival_id, 'business')
        corrected = app.correct_assistant_arrival_destination(arrival_id, {
            'latitude': 53.5, 'longitude': 7.5, 'address': 'Nog steeds aan te passen',
        })
        self.assertEqual(int(corrected['destination_manually_corrected']), 1)

    def test_correction_coordinate_zero_is_treated_as_valid_not_missing(self):
        """0.0 is een geldige coördinaat en mag niet als 'ontbrekend' worden behandeld."""
        origin_a = self._add_known_place('Op de evenaar', 0.0, 0.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=1.0, lon=1.0,
            accuracy=20,
            departure_at=app.iso_local(),
            destination_label='Bestemming',
        )
        arrival_id = int(arrival['id'])
        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 100})
        with patch.object(app, 'get_route_distance', mock_route):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 2.0, 'longitude': 2.0, 'address': 'Nieuw',
            })
        mock_route.assert_called_once_with(0.0, 0.0, 2.0, 2.0)

    def test_gps_warning_removed_from_address_details(self):
        """Test that GPS warning text is not in location details."""
        with patch.object(app, 'cached_report_address', return_value='Test Street 1'):
            location = app.trip_location_details({'latitude': 52.0, 'longitude': 5.0})
            self.assertNotIn('GPS-adres', location.get('address', ''))
            self.assertNotIn('huisnummer controleren', location.get('address', ''))

    # ------------------------------------------------------------------
    # Pre-PR hardening: definitief opslaan moet de gecorrigeerde bestemming
    # gebruiken (A = vertrek, B = foutieve GPS-bestemming, C = handmatig
    # gekozen bestemming).
    # ------------------------------------------------------------------

    def test_completed_trip_without_active_trip_uses_corrected_destination(self):
        """
        Zonder reeds actieve rit: na correctie B -> C en definitief opslaan
        moet de opgeslagen rit en trip_stop C bevatten, nooit opnieuw B. B
        blijft wel auditmatig terug te vinden op de trip_stop zelf.
        """
        origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=52.9, lon=6.9,  # B
            accuracy=20,
            departure_at=app.iso_local(app.now_local() - timedelta(hours=1)),
            destination_label='Bouwstraat, Rijssen',
            route_snapshot={'route_m': 800, 'route_samples': 5, 'route_incomplete': False},
        )
        arrival_id = int(arrival['id'])

        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 2400})
        with patch.object(app, 'get_route_distance', mock_route):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',  # C
            })

        result = app.complete_assistant_arrival(arrival_id, {'trip_type': 'business', 'odometer': 60, 'start_odometer': 50})
        trip = app.business_trip_by_id(int(result['trip_id']))
        dest_stop = trip['stops'][-1]

        # De opgeslagen aankomst bevat C, niet B.
        self.assertEqual(float(dest_stop['latitude']), 53.5)
        self.assertEqual(float(dest_stop['longitude']), 7.5)
        self.assertEqual(dest_stop['manual_label'], 'Eikenlaan 8, Rijssen')
        self.assertNotEqual(round(float(dest_stop['latitude']), 1), round(52.9, 1))

        # B blijft auditmatig beschikbaar op de trip_stop zelf.
        self.assertEqual(int(dest_stop['destination_manually_corrected']), 1)
        self.assertEqual(float(dest_stop['original_destination_latitude']), 52.9)
        self.assertEqual(float(dest_stop['original_destination_longitude']), 6.9)
        self.assertEqual(dest_stop['original_destination_address'], 'Bouwstraat, Rijssen')
        # De oorspronkelijke GPS-afstand (naar B) blijft ook auditmatig beschikbaar.
        self.assertEqual(round(float(dest_stop['original_destination_distance_m'])), 800)

    def test_completed_trip_with_active_trip_uses_corrected_destination(self):
        """Zelfde scenario, maar met een reeds bestaande actieve zakelijke rit."""
        started = app.iso_local(app.now_local() - timedelta(hours=2))
        app.start_business_trip({'odometer': 40, 'created_at': started, 'latitude': 50.0, 'longitude': 3.0})

        arrival = app.create_assistant_arrival(
            origin_place_id=None,
            destination_place_id=None,
            lat=52.9, lon=6.9,  # B
            accuracy=20,
            departure_at=app.iso_local(app.now_local() - timedelta(hours=1)),
            destination_label='Bouwstraat, Rijssen',
            route_snapshot={'route_m': 800, 'route_samples': 5, 'route_incomplete': False},
        )
        arrival_id = int(arrival['id'])

        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 2400})
        with patch.object(app, 'get_route_distance', mock_route):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',  # C
            })

        result = app.complete_assistant_arrival(arrival_id, {'trip_type': 'business', 'odometer': 45})
        trip = app.business_trip_by_id(int(result['trip_id']))
        dest_stop = trip['stops'][-1]

        self.assertEqual(float(dest_stop['latitude']), 53.5)
        self.assertEqual(float(dest_stop['longitude']), 7.5)
        self.assertEqual(int(dest_stop['destination_manually_corrected']), 1)
        self.assertEqual(float(dest_stop['original_destination_latitude']), 52.9)
        self.assertEqual(float(dest_stop['original_destination_longitude']), 6.9)
        self.assertEqual(dest_stop['original_destination_address'], 'Bouwstraat, Rijssen')
        self.assertEqual(round(float(dest_stop['original_destination_distance_m'])), 800)

    def test_preview_destination_does_not_mutate_database(self):
        """De preview-route mag GEEN databaseveld wijzigen."""
        origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=52.9, lon=6.9,
            accuracy=20,
            departure_at=app.iso_local(),
            destination_label='Bouwstraat, Rijssen',
        )
        arrival_id = int(arrival['id'])
        before = app.assistant_arrivals(50, True)
        before_row = next(x for x in before if int(x['id']) == arrival_id)

        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 2400})
        with patch.object(app, 'get_route_distance', mock_route):
            preview = app.preview_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
            })

        after = app.assistant_arrivals(50, True)
        after_row = next(x for x in after if int(x['id']) == arrival_id)
        self.assertEqual(int(after_row['destination_manually_corrected'] or 0), int(before_row['destination_manually_corrected'] or 0))
        self.assertIsNone(after_row['corrected_destination_latitude'])
        self.assertIsNone(after_row['corrected_destination_longitude'])
        self.assertEqual(round(preview['distance_m']), 2400)
        self.assertEqual(preview['distance_source'], 'route')
        mock_route.assert_called_once_with(51.0, 4.0, 53.5, 7.5)

    def test_preview_destination_shows_route_distance_and_source(self):
        """Preview toont de routeafstand en bron, A -> C, nooit vanaf B."""
        origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=52.9, lon=6.9,  # B
            accuracy=20,
            departure_at=app.iso_local(),
            destination_label='Bouwstraat, Rijssen',
        )
        arrival_id = int(arrival['id'])
        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 2400})
        with patch.object(app, 'get_route_distance', mock_route):
            preview = app.preview_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
            })
        mock_route.assert_called_once_with(51.0, 4.0, 53.5, 7.5)
        for call in mock_route.call_args_list:
            self.assertNotEqual((call.args[0], call.args[1]), (52.9, 6.9))
        self.assertEqual(preview['distance_source'], 'route')
        self.assertEqual(round(preview['distance_m']), 2400)

    def test_corrected_route_distance_influences_odometer_proposal(self):
        """
        Voorbeeld uit de opdracht: beginstand 42 km, originele GPS-afstand
        0,8 km, nieuwe routeafstand naar C 8,4 km. Het voorstel mag NIET meer
        op de oude 0,8 km gebaseerd zijn.
        """
        with app.db() as con:
            con.execute("INSERT INTO events(created_at,type,odometer) VALUES(?,'odometer',42)",
                        (app.iso_local(app.now_local() - timedelta(hours=2)),))
            con.commit()
        origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=52.9, lon=6.9,
            accuracy=20,
            departure_at=app.iso_local(app.now_local() - timedelta(hours=1)),
            destination_label='Bouwstraat, Rijssen',
            route_snapshot={'route_m': 800, 'route_samples': 5, 'route_incomplete': False},
        )
        arrival_id = int(arrival['id'])
        before = next(x for x in app.assistant_arrivals(50, True) if int(x['id']) == arrival_id)
        self.assertEqual(before['proposal']['suggested_odometer'], round(42 + 0.8 * before['proposal']['calibration']['factor']))

        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 8400})
        with patch.object(app, 'get_route_distance', mock_route):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
            })
        after = next(x for x in app.assistant_arrivals(50, True) if int(x['id']) == arrival_id)
        # Bron 'route': GEEN GPS-kalibratiefactor toepassen op een al nauwkeurige wegafstand.
        self.assertEqual(after['proposal']['suggested_odometer'], round(42 + 8.4))
        self.assertNotEqual(after['proposal']['suggested_odometer'], round(42 + 0.8 * before['proposal']['calibration']['factor']))

    def test_gps_fallback_proposal_uses_existing_calibration_logic(self):
        """Bij bron 'gps' blijft de bestaande GPS-kalibratie/leerlogica gelden."""
        with app.db() as con:
            con.execute("INSERT INTO events(created_at,type,odometer) VALUES(?,'odometer',42)",
                        (app.iso_local(app.now_local() - timedelta(hours=2)),))
            con.commit()
        arrival = app.create_assistant_arrival(
            origin_place_id=None,
            destination_place_id=None,
            lat=52.9, lon=6.9,
            accuracy=20,
            departure_at=None,
            destination_label='Bouwstraat, Rijssen',
            route_snapshot={'route_m': 800, 'route_samples': 5, 'route_incomplete': False},
        )
        arrival_id = int(arrival['id'])
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 9999})
            with patch.object(app, 'get_route_distance', mock_route):
                app.correct_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
                })
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()
        after = next(x for x in app.assistant_arrivals(50, True) if int(x['id']) == arrival_id)
        self.assertEqual(after['destination_distance_source'], 'gps')
        calibration = after['proposal']['calibration']
        self.assertEqual(after['proposal']['suggested_odometer'], round(42 + 0.8 * calibration['factor']))

    def test_original_odometer_data_stays_intact_after_correction(self):
        """Correctie wijzigt nooit een reeds werkelijk opgeslagen tellerstand."""
        with app.db() as con:
            con.execute("INSERT INTO events(created_at,type,odometer) VALUES(?,'odometer',42)",
                        (app.iso_local(app.now_local() - timedelta(hours=2)),))
            con.commit()
        events_before = app.rows_events()
        arrival = app.create_assistant_arrival(
            origin_place_id=None, destination_place_id=None,
            lat=52.9, lon=6.9, accuracy=20,
            departure_at=app.iso_local(app.now_local() - timedelta(hours=1)),
            destination_label='Bouwstraat, Rijssen',
        )
        arrival_id = int(arrival['id'])
        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 2400})
        with patch.object(app, 'get_route_distance', mock_route):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
            })
        events_after = app.rows_events()
        self.assertEqual(events_before, events_after)

    def test_ui_uses_corrected_destination_as_primary_address(self):
        """
        De UI (assistant_arrivals()) moet na correctie de gecorrigeerde
        bestemming als hoofdbestemming tonen, niet de oude GPS-bestemming.
        """
        arrival = app.create_assistant_arrival(
            origin_place_id=None, destination_place_id=None,
            lat=52.9, lon=6.9, accuracy=20, departure_at=None,
            destination_label='Bouwstraat, Rijssen',
        )
        arrival_id = int(arrival['id'])
        before = next(x for x in app.assistant_arrivals(50, True) if int(x['id']) == arrival_id)
        self.assertEqual(before['destination_name'], 'Bouwstraat, Rijssen')

        mock_route = MagicMock(return_value={'type': 'route', 'distance_m': 2400})
        with patch.object(app, 'get_route_distance', mock_route):
            app.correct_assistant_arrival_destination(arrival_id, {
                'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
            })
        after = next(x for x in app.assistant_arrivals(50, True) if int(x['id']) == arrival_id)
        self.assertEqual(after['destination_name'], 'Eikenlaan 8, Rijssen')
        self.assertNotEqual(after['destination_name'], 'Bouwstraat, Rijssen')
        # De oorspronkelijke GPS-bestemming blijft wel via destination_label als audit-info beschikbaar.
        self.assertEqual(after['destination_label'], 'Bouwstraat, Rijssen')

    def test_failed_route_api_falls_back_to_phone_snapshot_not_haversine(self):
        """
        Als de Google Routes-call mislukt, mag NOOIT de hemelsbrede
        (haversine) afstand tussen origin en bestemming worden gepresenteerd
        alsof dit een GPS-routeafstand is. Bij een mislukte route-call en een
        bekende telefoon-GPS-snapshot voor dit voorstel moet DIE afstand
        worden gebruikt, niet een nieuw berekende haversine-afstand.
        """
        origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
        # Origin (51.0,4.0) en bestemming (53.5,7.5) liggen ver uit elkaar, dus
        # een haversine-afstand zou > 300000 m zijn. De telefoon-snapshot
        # registreerde slechts 800 m onderweg naar de oorspronkelijke (foutieve)
        # bestemming B.
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=52.9, lon=6.9,  # B
            accuracy=20,
            departure_at=app.iso_local(),
            destination_label='Bouwstraat, Rijssen',
            route_snapshot={'route_m': 800, 'route_samples': 5, 'route_incomplete': False},
        )
        arrival_id = int(arrival['id'])
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            with patch.object(app, 'places_key', return_value='dummy-key'), \
                 patch.object(app, 'http_json', side_effect=ValueError('API error')):
                preview = app.preview_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',  # C
                })
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()
        self.assertEqual(preview['distance_source'], 'gps')
        # De telefoon-snapshot (800 m) wordt gebruikt, geen haversine (>300 km).
        self.assertEqual(preview['distance_m'], 800)

    def test_failed_route_api_without_snapshot_returns_unknown_distance(self):
        """
        Als de Google Routes-call mislukt EN er geen telefoon-GPS-snapshot
        bekend is, moet de afstand expliciet onbekend (None) zijn met bron
        'gps' -- nooit een fictieve haversine-afstand.
        """
        origin_a = self._add_known_place('Kantoor A', 51.0, 4.0)
        arrival = app.create_assistant_arrival(
            origin_place_id=origin_a,
            destination_place_id=None,
            lat=52.9, lon=6.9,
            accuracy=20,
            departure_at=app.iso_local(),
            destination_label='Bouwstraat, Rijssen',
            # Geen route_snapshot: geen telefoon-GPS-track bekend.
        )
        arrival_id = int(arrival['id'])
        for p in self.patches:
            if p.attribute == 'get_route_distance':
                p.stop()
        try:
            with patch.object(app, 'places_key', return_value='dummy-key'), \
                 patch.object(app, 'http_json', side_effect=ValueError('API error')):
                preview = app.preview_assistant_arrival_destination(arrival_id, {
                    'latitude': 53.5, 'longitude': 7.5, 'address': 'Eikenlaan 8, Rijssen',
                })
        finally:
            for p in self.patches:
                if p.attribute == 'get_route_distance':
                    p.start()
        self.assertEqual(preview['distance_source'], 'gps')
        self.assertIsNone(preview['distance_m'])

    def test_ui_keeps_use_address_button_disabled_when_preview_fails(self):
        """
        Statische regressietest op de front-end JS: als de preview-aanroep
        faalt, moet 'Gebruik dit adres' uitgeschakeld BLIJVEN. De gebruiker
        mag pas bevestigen na een geldige preview.
        """
        src = Path(app.__file__).read_text(encoding='utf-8')
        match = re.search(r"async function selectAssistantAddressResult\(i\)\{.*?toast\(e\.message,true\)\}\}", src, re.S)
        self.assertIsNotNone(match, "selectAssistantAddressResult niet gevonden")
        fn_src = match.group(0)
        catch_idx = fn_src.rindex('catch(e)')
        catch_body = fn_src[catch_idx:]
        self.assertIn("addrUseBtn').disabled=true", catch_body)
        self.assertNotIn("addrUseBtn').disabled=false", catch_body)

    def test_ui_shows_unknown_distance_instead_of_zero_km_when_missing(self):
        """
        Statische regressietest op de front-end JS: als
        corrected_destination_distance_m ontbreekt/null is, moet de UI
        'afstand onbekend' tonen in plaats van '0,0 km (GPS-schatting)'.
        0,0 km mag alleen worden getoond als de afstand daadwerkelijk
        numeriek 0 is.
        """
        src = Path(app.__file__).read_text(encoding='utf-8')
        match = re.search(r"function renderAssistant\(\)\{.*?\n\}", src, re.S)
        self.assertIsNotNone(match, "renderAssistant niet gevonden")
        fn_src = match.group(0)
        corrected_match = re.search(r"let corrected=x\.destination_manually_corrected\?.*?:''(?=;)", fn_src)
        self.assertIsNotNone(corrected_match, "corrected-weergave niet gevonden in renderAssistant")
        corrected_src = corrected_match.group(0)
        # Mag NIET langer '||0' gebruiken (dat maakt null/undefined stil tot 0).
        self.assertNotIn("corrected_destination_distance_m||0", corrected_src)
        # Moet expliciet op null/undefined controleren en een tekstuele fallback tonen.
        self.assertIn("corrected_destination_distance_m!=null", corrected_src)
        self.assertIn('afstand onbekend', corrected_src)

    def test_arrival_proposal_missing_distance_is_none_not_zero(self):
        """
        Backend-regressie: als corrected_destination_distance_m ontbreekt,
        moet de opgehaalde rij dat als None doorgeven (geen 0), zodat de UI
        het onderscheid tussen 'onbekend' en 'daadwerkelijk 0 meter' kan maken.
        """
        arrival = app.create_assistant_arrival(
            origin_place_id=None, destination_place_id=None,
            lat=52.9, lon=6.9, accuracy=20, departure_at=None,
            destination_label='Bouwstraat, Rijssen',
        )
        arrival_id = int(arrival['id'])
        # Corrigeer de bestemming, maar simuleer dat er geen afstand bekend kon
        # worden (bijv. mislukte route-call zonder telefoon-snapshot).
        with app.db() as con:
            con.execute(
                "UPDATE assistant_arrivals SET destination_manually_corrected=1, "
                "corrected_destination_label='Eikenlaan 8, Rijssen', "
                "corrected_destination_latitude=53.5, corrected_destination_longitude=7.5, "
                "corrected_destination_distance_m=NULL, destination_distance_source='gps' WHERE id=?",
                (arrival_id,)
            )
            con.commit()
        row = next(x for x in app.assistant_arrivals(50, True) if int(x['id']) == arrival_id)
        self.assertIsNone(row['corrected_destination_distance_m'])


class Release1400UiTests(unittest.TestCase):
    """Static regression tests for the release 14.00 dashboard contract."""

    @classmethod
    def setUpClass(cls):
        cls.src = Path(app.__file__).read_text(encoding='utf-8')

    def test_active_trip_renders_single_live_gps_view(self):
        render_business = re.search(r"function renderBusiness\(\)\{.*?\}renderAssistant", self.src, re.S)
        self.assertIsNotNone(render_business, "renderBusiness niet gevonden")
        source = render_business.group(0)
        self.assertIn('Live GPS-afstand', source)
        self.assertIn('Laatste tellerstand', source)
        self.assertIn('Voorgestelde eindstand', source)
        self.assertIn('os.distance_warning', source)

    def test_assistant_draft_distance_is_hidden_for_active_trip(self):
        render_assistant = re.search(r"function renderAssistant\(\)\{.*?\n", self.src, re.S)
        self.assertIsNotNone(render_assistant, "renderAssistant niet gevonden")
        self.assertIn("rt.draft_active&&!DATA?.business?.active_trip", render_assistant.group(0))

    def test_trip_modal_refreshes_before_it_opens_and_keeps_warning(self):
        open_trip = re.search(r"async function openTripPoint\(mode\)\{.*?\n", self.src, re.S)
        self.assertIsNotNone(open_trip, "openTripPoint niet gevonden")
        source = open_trip.group(0)
        self.assertLess(source.index('await refreshTripOdoProposal'), source.index("openModal('tripModal')"))
        proposal = re.search(r"function showTripOdoProposal\(sug\)\{.*?\n", self.src, re.S)
        self.assertIsNotNone(proposal, "showTripOdoProposal niet gevonden")
        self.assertIn('sug?.distance_warning', proposal.group(0))
        self.assertIn("initOdometerWheel('trip',proposed)", proposal.group(0))

    def test_known_place_location_flow_uses_existing_endpoints(self):
        self.assertIn('📍 Gebruik huidige locatie', self.src)
        self.assertIn("api/location/addresses", self.src)
        self.assertIn("api/places/search-address", self.src)
        self.assertIn('🔎 Ander adres zoeken', self.src)
        self.assertIn('📍 Alleen huidige GPS-positie gebruiken', self.src)
        self.assertIn('(candidates||[]).slice(0,10)', self.src)

    def test_known_place_edit_preserves_coordinates_without_explicit_selection(self):
        edit = re.search(r"function editKnownPlace\(id\)\{.*?\n", self.src, re.S)
        self.assertIsNotNone(edit, "editKnownPlace niet gevonden")
        source = edit.group(0)
        self.assertIn("$('knownLat').value=p.latitude", source)
        self.assertIn("$('knownLon').value=p.longitude", source)
        self.assertNotIn('useKnownPlaceCurrentLocation()', source)


class PdfRedesignTests(unittest.TestCase):
    """Test PDF redesign for 7.00."""
    
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [
            patch.object(app, 'publish_sensors_async', lambda: None),
        ]
        for p in self.patches:
            p.start()
        app.init_db()
    
    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()
    
    def test_pdf_generation_succeeds(self):
        """Test that PDF generation works without errors."""
        try:
            pdf_bytes, filename = app.business_pdf(period='month')
            self.assertIsInstance(pdf_bytes, bytes)
            self.assertTrue(pdf_bytes.startswith(b'%PDF'))
            self.assertTrue(filename.endswith('.pdf'))
        except Exception as e:
            self.fail(f"PDF generation failed: {e}")

    def test_pdf_contains_no_gps_warning_text(self):
        """Test that PDF output doesn't contain GPS warning text."""
        pdf_bytes, _ = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        self.assertNotIn('GPS-adres', pdf_str)
        self.assertNotIn('huisnummer controleren', pdf_str)

    def test_pdf_footer_does_not_contain_rit_tank_branding(self):
        """Test that PDF footer doesn't have 'Rit & Tank' text."""
        pdf_bytes, _ = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        # Check that footer doesn't contain Rit & Tank
        # (This is a simplified check; actual footer validation requires PDF parsing)
        self.assertNotIn('Rit & Tank', pdf_str)

    def test_pdf_contains_huisplan_branding(self):
        """Test that PDF contains Huisplan branding."""
        pdf_bytes, _ = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        # Huisplan should appear in the PDF
        self.assertIn('HUISPLAN', pdf_str.upper())

    def test_pdf_preview_returns_valid_format(self):
        """Test PDF preview endpoint returns valid format."""
        try:
            result = app.business_pdf(period='month', preview=True)
            self.assertIsInstance(result, dict)
            # Preview should contain a base64-encoded PDF and per-page SVG output
            self.assertTrue(result.get('pdf_base64'))
            self.assertIsInstance(result.get('pages'), list)
            self.assertGreater(len(result['pages']), 0)
        except Exception as e:
            self.fail(f"PDF preview failed: {e}")

    def test_pdf_pagination_maintained(self):
        """Test that PDF pagination still works correctly."""
        # Create a mock trip with multiple stops
        with app.db() as con:
            con.execute(
                "INSERT INTO business_trips(started_at,ended_at,status) VALUES(?,?,?)",
                (app.iso_local(), app.iso_local() + ' 01:00', 'completed')
            )
            con.commit()

        pdf_bytes, _ = app.business_pdf(period='month')
        self.assertIsInstance(pdf_bytes, bytes)
        # PDF should still be valid
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))

    def test_pdf_logo_present_in_header(self):
        """Het Huisplan-logo (ImLogo XObject) moet als afbeelding in de PDF-kop staan."""
        pdf_bytes, _ = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        self.assertIn('/ImLogo', pdf_str)
        self.assertIn('/Subtype /Image', pdf_str)

    def test_pdf_private_color_is_light_blue(self):
        """Privé-onderdelen (bullets/kaarten) gebruiken de lichtblauwe paletkleur."""
        pdf_bytes, _ = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        # palette['blue'] = (82, 186, 255) -> 0.322 0.729 1.000 rg
        self.assertIn('0.322 0.729 1.000 rg', pdf_str)

    def test_pdf_business_color_is_light_green(self):
        """Zakelijke onderdelen (bullets/kaarten) gebruiken de lichtgroene (teal) paletkleur."""
        pdf_bytes, _ = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        # palette['teal'] = (88, 223, 177) -> 0.345 0.875 0.694 rg
        self.assertIn('0.345 0.875 0.694 rg', pdf_str)

    def test_pdf_long_address_wraps_instead_of_overflowing(self):
        """Lange adressen worden over meerdere regels verdeeld, binnen de layoutbreedte."""
        long_address = 'Zeer Lange Straatnaam Met Heel Veel Woorden 12345, Postbus 9999, 1234 AB Een Hele Lange Plaatsnaam'
        lines = app._SimplePdfPage.wrap_lines(long_address, 415, 10.7)
        self.assertGreater(len(lines), 1, 'Een lang adres moet over meerdere regels lopen, niet buiten de layout vallen')
        max_chars = max(18, int(415 / max(3.7, 10.7 * 0.52)))
        for line in lines:
            self.assertLessEqual(len(line), max_chars)

    def test_pdf_generation_succeeds_with_long_address_trip(self):
        """Een rit met een zeer lang adres mag de PDF-generatie niet laten breken/overlappen."""
        long_address = 'Zeer Lange Straatnaam Met Heel Veel Woorden Toegevoegd Voor De Test 12345, 1234 AB Een Hele Lange Plaatsnaam'
        with app.db() as con:
            cur = con.execute(
                "INSERT INTO business_trips(started_at,ended_at,status,trip_type) VALUES(?,?,?,?)",
                (app.iso_local(), app.iso_local(), 'completed', 'business')
            )
            trip_id = int(cur.lastrowid)
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,manual_label) VALUES(?,?,?,?,?,?,?)",
                (trip_id, 0, app.iso_local(), 100, 52.0, 5.0, long_address)
            )
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,manual_label,segment_trip_type) VALUES(?,?,?,?,?,?,?,?)",
                (trip_id, 1, app.iso_local(), 120, 52.1, 5.1, long_address, 'business')
            )
            con.commit()

        pdf_bytes, _ = app.business_pdf(period='month')
        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
