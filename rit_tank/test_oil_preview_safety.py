"""The preview uses only synthetic data and refuses an unrecognized /data.

HTTP tests emulate a local HTTPS reverse proxy header; they DO NOT prove the
security or reachability of an externally deployed HTTPS endpoint.
"""
from contextlib import closing
import http.client
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import app

SCRIPT = Path(__file__).resolve().parents[1] / 'preview' / 'oil' / 'start.py'
spec = importlib.util.spec_from_file_location('oil_preview_fixture', SCRIPT)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
PASSWORD = 'testonly__Unique_Fixture_Password_2026_DoNotUse'


class OilPreviewSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-oil-preview-')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / 'preview'

    def test_only_synthetic_data_and_idempotent_start(self):
        result = preview.prepare_data(self.folder, PASSWORD)
        self.assertFalse(result['reused'])
        self.assertTrue((self.folder / preview.MARKER).exists())
        with sqlite3.connect(self.folder / 'rit_tank.db') as con:
            self.assertEqual(con.execute("SELECT value FROM settings WHERE key='vehicle_name'").fetchone()[0],
                             'Fictieve testauto — Olieproef')
            self.assertEqual(con.execute("SELECT count(*) FROM events").fetchone()[0], 3)
            self.assertEqual(con.execute("SELECT count(*) FROM business_trips").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM trip_stops").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT amount_ml,oil_type,odometer FROM oil_maintenance")
                             .fetchone(), (4200, '0W-20', 82000))
            self.assertEqual(con.execute("SELECT count(*) FROM receipt_archive").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], 'ok')
        options = json.loads((self.folder / 'options.json').read_text())
        self.assertTrue(options['standalone_enabled'])
        self.assertFalse(options['google_drive_enabled'])
        self.assertEqual(options['assistant_enabled'], '0')
        self.assertEqual(options['google_places_api_key'], '')
        self.assertEqual(options['standalone_password'], PASSWORD)
        self.assertTrue((self.folder / 'receipt_archive' / 'fictieve-bon.pdf').read_bytes().startswith(b'%PDF-1.4'))
        before = (self.folder / 'rit_tank.db').stat().st_size
        self.assertTrue(preview.prepare_data(self.folder, PASSWORD)['reused'])
        self.assertEqual((self.folder / 'rit_tank.db').stat().st_size, before)

    def test_existing_unmarked_volume_is_never_modified(self):
        self.folder.mkdir()
        original = self.folder / 'rit_tank.db'
        original.write_bytes(b'pretend-production-db-DO-NOT-TOUCH')
        with self.assertRaisesRegex(RuntimeError, 'NIETS wijzigen'):
            preview.prepare_data(self.folder, PASSWORD)
        self.assertEqual(original.read_bytes(), b'pretend-production-db-DO-NOT-TOUCH')
        self.assertFalse((self.folder / preview.MARKER).exists())

    def test_no_password_no_start_and_mismatched_password_does_not_reset(self):
        with self.assertRaises(ValueError):
            preview.prepare_data(self.folder, '')
        self.assertFalse(self.folder.exists())
        preview.prepare_data(self.folder, PASSWORD)
        with self.assertRaisesRegex(RuntimeError, 'komt niet overeen'):
            preview.prepare_data(self.folder, 'another-very-different-private-test-password')
        with sqlite3.connect(self.folder / 'rit_tank.db') as con:
            self.assertEqual(con.execute("SELECT count(*) FROM events").fetchone()[0], 3)

    def test_authenticated_real_backend_oil_flow_and_odometer_isolation(self):
        preview.prepare_data(self.folder, PASSWORD)
        with patch.object(app, 'DATA_DIR', self.folder), \
             patch.object(app, 'DB_PATH', self.folder / 'rit_tank.db'), \
             patch.object(app, 'OPTIONS_PATH', self.folder / 'options.json'), \
             patch.object(app, 'RECEIPT_DIR', self.folder / 'receipts'):
            with ThreadingHTTPServer(('127.0.0.1', 0), app.Handler) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                self.addCleanup(server.shutdown)
                self.addCleanup(worker.join, 5)
                client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    common = {'X-Forwarded-Proto': 'https',
                              'X-Forwarded-Host': 'oil-preview.test',
                              'Host': 'oil-preview.test',
                              'Origin': 'https://oil-preview.test',
                              'Content-Type': 'application/json'}
                    client.request('GET', '/api/maintenance/oil', headers=common)
                    response = client.getresponse()
                    response.read()
                    self.assertEqual(response.status, 401, 'No anonymous access to preview data')
                    client.request('POST', '/api/auth/login',
                                   body=json.dumps({'password': PASSWORD}), headers=common)
                    response = client.getresponse()
                    response.read()
                    self.assertEqual(response.status, 200)
                    cookie = response.getheader('Set-Cookie').split(';', 1)[0]
                    authorized = {**common, 'Cookie': cookie}
                    client.request('GET', '/api/summary?period=month', headers=authorized)
                    response = client.getresponse()
                    data = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    self.assertEqual(data['current_odometer'], 85000)

                    oil = {'kind': 'top_up', 'performed_on': '2026-10-31',
                           'oil_type': '5W-30', 'amount_ml': 300, 'odometer': 85000}
                    client.request('POST', '/api/maintenance/oil', body=json.dumps(oil),
                                   headers=authorized)
                    response = client.getresponse()
                    saved = json.loads(response.read())
                    self.assertEqual(response.status, 201, saved)
                    self.assertEqual(saved['entry']['amount_ml'], 300)
                    client.request('GET', '/api/maintenance/oil', headers=authorized)
                    response = client.getresponse()
                    entries = json.loads(response.read())['entries']
                    self.assertEqual(len(entries), 2)
                    self.assertEqual(entries[0]['oil_type'], '5W-30')
                    client.request('GET', '/api/summary?period=month', headers=authorized)
                    response = client.getresponse()
                    after = json.loads(response.read())
                    self.assertEqual(after['current_odometer'], 85000)
                    self.assertEqual(after['total_events'], data['total_events'])
                finally:
                    client.close()


if __name__ == '__main__':
    unittest.main()
