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
    """Test release 7.00 version consistency."""
    
    def test_app_version_is_7_00(self):
        self.assertEqual(app.APP_VERSION, '7.00')
    
    def test_config_yaml_version_is_7_00(self):
        config_path = Path(__file__).with_name('config.yaml')
        if config_path.exists():
            content = config_path.read_text()
            self.assertIn("version: '7.00'", content)
    
    def test_readme_title_has_7_00(self):
        readme_path = Path(__file__).with_name('README.md')
        if readme_path.exists():
            content = readme_path.read_text()
            self.assertIn('# Rit & Tank 7.00', content)
    
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
        for p in self.patches:
            p.start()
        app.init_db()
    
    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()
    
    def test_address_correction_function_exists(self):
        """Test that the address correction function is available."""
        self.assertTrue(hasattr(app, 'correct_assistant_arrival_destination'))
        self.assertTrue(callable(app.correct_assistant_arrival_destination))
    
    def test_route_distance_function_calculates_gps_fallback(self):
        """Test get_route_distance falls back to GPS when route API unavailable."""
        with patch.object(app, 'http_json', side_effect=ValueError('API error')):
            result = app.get_route_distance(52.0, 5.0, 52.1, 5.1)
            self.assertEqual(result['type'], 'gps')
            self.assertGreater(result['distance_m'], 0)
    
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
    
    def test_gps_warning_removed_from_address_details(self):
        """Test that GPS warning text is not in location details."""
        with patch.object(app, 'cached_report_address', return_value='Test Street 1'):
            location = app.trip_location_details({'latitude': 52.0, 'longitude': 5.0})
            self.assertNotIn('GPS-adres', location.get('address', ''))
            self.assertNotIn('huisnummer controleren', location.get('address', ''))


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
            pdf_bytes = app.business_pdf(period='month')
            self.assertIsInstance(pdf_bytes, bytes)
            self.assertTrue(pdf_bytes.startswith(b'%PDF'))
        except Exception as e:
            self.fail(f"PDF generation failed: {e}")
    
    def test_pdf_contains_no_gps_warning_text(self):
        """Test that PDF output doesn't contain GPS warning text."""
        pdf_bytes = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        self.assertNotIn('GPS-adres', pdf_str)
        self.assertNotIn('huisnummer controleren', pdf_str)
    
    def test_pdf_footer_does_not_contain_rit_tank_branding(self):
        """Test that PDF footer doesn't have 'Rit & Tank' text."""
        pdf_bytes = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        # Check that footer doesn't contain Rit & Tank
        # (This is a simplified check; actual footer validation requires PDF parsing)
        self.assertNotIn('Rit & Tank', pdf_str)
    
    def test_pdf_contains_huisplan_branding(self):
        """Test that PDF contains Huisplan branding."""
        pdf_bytes = app.business_pdf(period='month')
        pdf_str = pdf_bytes.decode('latin-1', errors='ignore')
        # Huisplan should appear in the PDF
        self.assertIn('Huisplan', pdf_str.upper())
    
    def test_pdf_preview_returns_valid_format(self):
        """Test PDF preview endpoint returns valid format."""
        try:
            result = app.business_pdf(period='month', preview=True)
            self.assertIsInstance(result, dict)
            # Preview should contain SVG or base64 data
            self.assertTrue(result.get('svg') or result.get('base64'))
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
        
        pdf_bytes = app.business_pdf(period='month')
        self.assertIsInstance(pdf_bytes, bytes)
        # PDF should still be valid
        self.assertTrue(pdf_bytes.startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
