"""33.09 recovery checks: rollback without changing persistent receipts or trip records."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_v2300 import app


class Recovery3309Tests(unittest.TestCase):
    def test_day_planning_and_calendar_removed_but_existing_services_remain(self):
        root = Path(__file__).parent
        source = (root / 'app.py').read_text(encoding='utf-8')
        config = (root / 'config.yaml').read_text(encoding='utf-8')
        docker = (root / 'Dockerfile').read_text(encoding='utf-8')
        self.assertEqual(app.APP_VERSION, '33.09')
        for fragment in ('day_planning', '/api/day-planning', 'google_calendar_client_id',
                         'google_calendar_client_secret', 'google_calendar_oauth_json'):
            self.assertNotIn(fragment, source + config + docker)
        self.assertFalse((root / 'day_planning.py').exists())
        self.assertFalse((root / 'day_planning.js').exists())
        self.assertIn('google_drive_oauth_json', config)
        self.assertIn('receipt_archive.py', docker)

    def test_startup_keeps_existing_fuel_rows_and_receipt_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / 'receipt_archive'
            folder.mkdir()
            pdf = folder / 'existing.pdf'
            pdf_bytes = b'%PDF-1.4\nunchanged\n%%EOF'
            pdf.write_bytes(pdf_bytes)
            old_folder = root / 'receipts'
            old_folder.mkdir()
            old = old_folder / 'fuel_7.jpg'
            old.write_bytes(b'existing-fuel-receipt')
            for name, value in {
                'DATA_DIR': root, 'DB_PATH': root / 'rit_tank.db',
                'OPTIONS_PATH': root / 'options.json', 'RECEIPT_DIR': old_folder,
                'publish_sensors_async': lambda: None,
            }.items():
                patched = patch.object(app, name, value)
                patched.start()
                self.addCleanup(patched.stop)
            app.init_db()
            with app.db() as con:
                con.execute("""INSERT INTO events(created_at,type,odometer,receipt_path)
                               VALUES('2026-10-05T10:00:00+02:00','fuel',24553,'fuel_7.jpg')""")
            before = app.rows_events()
            app.init_db()
            self.assertEqual(app.rows_events(), before)
            self.assertEqual(pdf.read_bytes(), pdf_bytes)
            self.assertEqual(old.read_bytes(), b'existing-fuel-receipt')
            self.assertFalse((root / 'google_calendar_oauth.json').exists())


if __name__ == '__main__':
    unittest.main()
