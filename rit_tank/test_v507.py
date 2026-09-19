"""Regression coverage for receipt values, persistent settings and Drive retention."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from test_v44 import app


class UpdateTests(unittest.TestCase):
    def test_receipt_price_label_is_not_liters(self):
        r = app.parse_receipt_text('Shell\nPrijs per liter 1,899\n32,45 liter\nTotaal 61,62')
        self.assertEqual((r['liters'], r['price_per_liter']), (32.45, 1.899))

    def test_receipt_multiplication_and_split_labels(self):
        for text in ('32,45 L x 1,899', 'Volume\n32,45\nLiterprijs\n1,899'):
            r = app.parse_receipt_text(text)
            self.assertEqual((r['liters'], r['price_per_liter']), (32.45, 1.899))

    def test_receipt_derives_missing_field(self):
        r = app.parse_receipt_text('Literprijs 2,000\nTotaal 65,00')
        self.assertEqual(r['liters'], 32.5)
        r = app.parse_receipt_text('32,50 liter\nTotaal 65,00')
        self.assertEqual(r['price_per_liter'], 2)

    def test_receipt_no_numeric_values_is_not_success(self):
        r = app.parse_receipt_text('Shell\nBedankt en tot ziens')
        self.assertIsNone(r['liters'])
        self.assertIsNone(r['price_per_liter'])

    def test_fallback_survives_reinitialization_and_unrelated_save(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, 'DATA_DIR', Path(folder)), patch.object(app, 'DB_PATH', Path(folder)/'test.db'), patch.object(app, 'OPTIONS_PATH', Path(folder)/'options.json'), patch.object(app, 'publish_sensors_async'):
                app.init_db()
                app.set_settings({'location_fallback_entity': 'device_tracker.iphone'})
                app.init_db()
                app.set_settings({'vehicle_name': 'Captur'})
                self.assertEqual(app.get_settings()['location_fallback_entity'], 'device_tracker.iphone')
                app.set_settings({'location_fallback_entity': ''})
                self.assertEqual(app.get_settings()['location_fallback_entity'], '')

    def test_address_selection_keeps_real_street_and_deduplicates(self):
        doc = {'openbareruimte_id': '123', 'straatnaam': 'Teststraat', 'weergavenaam': 'Teststraat 7A, Teststad', 'centroide_ll': 'POINT(5.0 52.0)', 'huis_nlt': '7A'}
        other = dict(doc, openbareruimte_id='456', weergavenaam='Andere straat 8')
        with patch.object(app, 'http_json', side_effect=[{'response': {'docs': [doc]}}, {'response': {'docs': [doc, doc, other]}}]) as fetch:
            r = app.nearby_house_numbers(52, 5)
        self.assertEqual([a['address'] for a in r['addresses']], ['Teststraat 7A, Teststad'])
        self.assertIn('openbareruimte_id%3A123', fetch.call_args[0][0])

    def test_confirmed_address_overrides_nearby_zone(self):
        with patch.object(app, 'known_place_by_id', return_value={'name': 'Thuis'}):
            r = app.trip_location_details({'known_place_id': 1, 'manual_label': 'Teststraat 7A'})
        self.assertEqual(r['address'], 'Teststraat 7A')

    def test_zero_retention_never_lists_or_deletes(self):
        with patch.object(app, 'load_options', return_value={'backup_retention_days': 0}):
            cfg = app.backup_config()
        self.assertEqual(cfg['retention_days'], 0)
        service = MagicMock()
        app.prune_drive_backups(service, cfg)
        service.files.assert_not_called()

    def test_cleanup_never_deletes_pdf_or_unrelated_files(self):
        service = MagicMock()
        service.files().list().execute.side_effect = [
            {'files': [{'id': 'pdf', 'name': 'RitTank_report.pdf'}, {'id': 'backup', 'name': 'RitTank_20260101_030000.rtbackup'}], 'nextPageToken': 'next'},
            {'files': []},
        ]
        app.prune_drive_backups(service, {'retention_days': 30, 'folder_id': 'folder'})
        service.files().delete.assert_called_once_with(fileId='backup', supportsAllDrives=True)
        self.assertIn('appProperties', service.files().list.call_args.kwargs['q'])

    def test_archive_rejects_non_pdf_before_connecting(self):
        with patch.object(app, 'drive_service') as drive:
            with self.assertRaises(ValueError):
                app.archive_pdf({'pdf_base64': base64.b64encode(b'not a pdf').decode()})
            drive.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
