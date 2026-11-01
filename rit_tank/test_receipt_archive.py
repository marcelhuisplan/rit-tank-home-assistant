"""Targeted regressions for the PDF-only tankbon archive (33.06)."""
import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from test_v2300 import app
from test_v3000 import Request
import receipt_archive as archive


class ReceiptArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for key, value in {
            'DATA_DIR': root,
            'DB_PATH': root / 'rit_tank.db',
            'OPTIONS_PATH': root / 'options.json',
            'RECEIPT_DIR': root / 'receipts',
            'publish_sensors_async': lambda: None,
        }.items():
            p = patch.object(app, key, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()
        self.root = root
        self.ocr = patch.object(archive, '_ocr', return_value=(
            'Shell\n1234 AB Rijssen\n05-10-2026\nTOTAAL € 52,34\n'
            '30,15 liter\nprijs per liter 1,735\n'))
        self.ocr.start()
        self.addCleanup(self.ocr.stop)

    def image_data(self):
        out = io.BytesIO()
        Image.new('RGB', (450, 820), '#ededed').save(out, format='JPEG')
        return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode('ascii')

    def prepare(self):
        with app.db() as con:
            return archive.prepare([{'data_url': self.image_data()}], con)

    def test_photo_becomes_readable_pdf_with_only_filename_fields(self):
        result = self.prepare()
        self.assertEqual(result['suggested_filename'], '2026-10-05_Shell_Rijssen_52-34.pdf')
        self.assertEqual(result['recognized'], {
            'date': '2026-10-05', 'station': 'Shell', 'place': 'Rijssen', 'total': '52,34'})
        self.assertFalse(any(x in result['recognized'] for x in
                             ('liters', 'odometer', 'price_per_liter')))
        raw = archive.decode_pdf(result['pdf_data_url'])
        self.assertTrue(raw.startswith(b'%PDF-'))
        self.assertGreater(len(raw), 1000)

    def test_tango_maarsbergen_receipt_and_ocr_spacing(self):
        samples = (
            'Tango Maarsbergen\nWoudenbergseweg 44\n3953 MH Maarsbergen\n'
            'Datum 10-09-2019 09:57\nTOTAAL € 63.80\n',
            'T a n g o Maarsbergen\n3 9 5 3 M H M a a r s b e r g e n\n'
            'Da tum 1 0 - 0 9 - 2 0 1 9 09:57\nTO TAAL € 6 3 . 8 0\n',
            'Tango Maarsbergen\n3953 M H Maarsbergen\n'
            'Datum 10-G9-2019 09:57\nTOTAAL € 63.80\n',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                fields = archive.recognize_filename_fields(sample)
                self.assertEqual(fields, {'date': '2019-09-10', 'station': 'Tango',
                                          'place': 'Maarsbergen', 'total': '63,80'})
                self.assertEqual(archive.suggested_filename(fields),
                                 '2019-09-10_Tango_Maarsbergen_63-80.pdf')

    def test_ocr_conflicts_and_illegible_figures_stay_unknown(self):
        fields = archive.recognize_filename_fields(
            'Tango\n3953 MH Maarsbergen\n'
            'Datum 10-09-2019\nDatum 19-09-2019\n'
            'TOTAAL € 63.80\nTOTAAL € 63.88')
        self.assertEqual(fields['date'], '')
        self.assertEqual(fields['total'], '')
        self.assertEqual(fields['station'], 'Tango')
        self.assertEqual(fields['place'], 'Maarsbergen')
        invalid = archive.recognize_filename_fields(
            'Tango\nDatum 10-49-2019\nTOTAAL € 63.8?\n')
        self.assertEqual(invalid['date'], '')
        self.assertEqual(invalid['total'], '')

    def test_ocr_preprocessing_upscales_narrow_scans(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'narrow.png'
            Image.new('L', (350, 700), 'white').save(path)
            seen = []
            def fake_run(command, timeout=15):
                with Image.open(command[1]) as image:
                    seen.append((image.size, set(image.getdata()), command[4]))
                return type('Result', (), {'returncode': 0,
                                           'stdout': b'Tango\n10-G9-2019\nTOTAAL 63.80'})()
            with patch.object(archive, '_run', side_effect=fake_run):
                self.ocr.stop()  # This test needs the real OCR preprocessing.
                try:
                    text = archive._ocr(path)
                finally:
                    self.ocr.start()
            self.assertIn('Tango', text)
            self.assertEqual(len(seen), 1)
            self.assertGreaterEqual(seen[0][0][0], 1400)
            self.assertTrue(seen[0][1] <= {0, 255})
            self.assertEqual(seen[0][2], 'eng')

    def test_unknown_ocr_values_are_not_invented(self):
        fields = archive.recognize_filename_fields('30,10 L\n1,899 per liter\nBetaald\n')
        self.assertEqual(fields, {'date': '', 'station': '', 'place': '', 'total': ''})
        name = archive.suggested_filename(fields)
        self.assertEqual(name, 'Datum-onbekend_Tankstation-onbekend_Plaats-onbekend_Bedrag-onbekend.pdf')
        self.assertEqual(archive.recognize_filename_fields('Shell\n01-01-2026\n02-01-2026')['date'], '')

    def test_save_duplicate_confirmation_search_download_zip_and_preserve_events(self):
        with app.db() as con:
            con.execute("""INSERT INTO events(created_at,type,odometer,liters,price_per_liter,station)
                           VALUES('2026-10-04T10:00:00+02:00','fuel',24763,30,1.8,'Oud')""")
        original_rows = app.rows_events()
        result = self.prepare()
        self.assertFalse((self.root/'receipt_archive').exists())
        with patch.object(archive, 'pdf_details', return_value=(1, '')):
            save = Request('/api/receipt-archive/save', {
                'pdf_data_url':result['pdf_data_url'],
                'filename':result['suggested_filename']})
            save.do_POST()
            self.assertEqual(save.status, 201, save.wfile.getvalue())
            receipt_id = save.json()['id']
            self.assertEqual(app.rows_events(), original_rows)
            with app.db() as con:
                matches = archive.duplicates(con, archive.hashlib.sha256(
                    archive.decode_pdf(result['pdf_data_url'])).hexdigest(),
                    result['suggested_filename'])
            self.assertEqual(len(matches), 1)
            conflict = Request('/api/receipt-archive/save', {
                'pdf_data_url':result['pdf_data_url'],
                'filename':result['suggested_filename']})
            conflict.do_POST()
            self.assertEqual(conflict.status, 409)
            self.assertEqual(conflict.json()['code'], 'RECEIPT_DUPLICATE')
            self.assertEqual(len(list((self.root/'receipt_archive').glob('*.pdf'))), 1)
            affirmed = Request('/api/receipt-archive/save', {
                'pdf_data_url':result['pdf_data_url'],
                'filename':result['suggested_filename'],
                'confirm_duplicate':True})
            affirmed.do_POST()
            self.assertEqual(affirmed.status, 201)
        listing = Request('/api/receipt-archive')
        listing.do_GET()
        self.assertEqual(listing.status, 200)
        self.assertEqual(len(listing.json()['receipts']), 2)
        item = Request('/api/receipt-archive/'+str(receipt_id)+'?download=1')
        item.do_GET()
        self.assertEqual(item.status, 200)
        self.assertTrue(item.wfile.getvalue().startswith(b'%PDF-'))
        self.assertIn('attachment', item.response_headers['Content-Disposition'])
        exported = Request('/api/receipt-archive/export.zip')
        exported.do_GET()
        self.assertEqual(exported.status, 200)
        with zipfile.ZipFile(io.BytesIO(exported.wfile.getvalue())) as z:
            self.assertEqual(len(z.namelist()), 2)
        app.init_db()
        self.assertEqual(app.rows_events(), original_rows)
        self.assertEqual(len(list((self.root/'receipt_archive').glob('*.pdf'))), 2)

    def test_confirmed_ocr_http_save_is_validated_and_preserves_existing_data(self):
        app.RECEIPT_DIR.mkdir()
        historical_receipt = app.RECEIPT_DIR / 'fuel_1.jpg'
        historical_receipt.write_bytes(b'synthetic historical receipt')
        with app.db() as con:
            con.execute(
                """INSERT INTO events
                   (created_at,type,odometer,liters,price_per_liter,station,receipt_path)
                   VALUES(?, 'fuel', 24763, 30, 1.8, 'Oud', 'fuel_1.jpg')""",
                ('2026-10-04T10:00:00+02:00',))
            con.execute(
                """INSERT INTO events(created_at,type,odometer)
                   VALUES(?, 'odometer', 24763)""",
                ('2026-10-04T10:01:00+02:00',))
            con.execute(
                """INSERT INTO business_trips(started_at,ended_at,status,purpose,client)
                   VALUES(?, ?, 'completed', 'Zakelijk', 'Testklant')""",
                ('2026-10-04T09:00:00+02:00', '2026-10-04T09:30:00+02:00'))

        def preserved_rows():
            with app.db() as con:
                return {
                    table: [tuple(row) for row in con.execute(
                        f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('events', 'business_trips', 'trip_stops')
                }

        original_rows = preserved_rows()
        original_receipt = historical_receipt.read_bytes()
        ocr_text = (
            'Shop\nKlik Services BV, ARGOS\n'
            'Reggesingel 40, 7461 AK Rijssen\n'
            'Brandstofleverancier\nVaro Energy Retail BV\n'
            'Wilhelminakade 919, 3072 AP Rotterdam\n'
            'KLANTENBON\n*95 Ongelood\n'
            '(POMP 4; 24.70 l * € 2.429/l)\n'
            'TOTAAL € 60.00\nBTW 21% € 10.41\n'
            '10-10-2026 11:22:59')
        with patch.object(archive, '_ocr', return_value=ocr_text):
            prepared = Request(
                '/api/receipt-archive/prepare',
                {'files': [{'data_url': self.image_data()}]})
            prepared.do_POST()

        self.assertEqual(prepared.status, 200, prepared.wfile.getvalue())
        result = prepared.json()
        self.assertIn('_Rijssen_', result['suggested_filename'])
        self.assertNotIn('Rotterdam', result['suggested_filename'])
        self.assertEqual(result['recognized_details']['place'], 'Rijssen')
        self.assertEqual(preserved_rows(), original_rows)

        reviewed_fields = {
            'station': 'Argos',
            'place': 'Rijssen',
            'address': 'Reggesingel 40, 7461 AK Rijssen',
            'postcode': '7461 AK',
            'date': '2026-10-10',
            'fuel_type': '95 ongelood',
            'pump': '4',
            'liters': '24,70',
            'price_per_liter': '2,500',
            'total': '60,00',
            'vat_rate': '21%',
            'vat_amount': '10,41',
        }
        payload = {
            'pdf_data_url': result['pdf_data_url'],
            'filename': '2026-10-10_Argos_Rijssen_gewijzigd.pdf',
            'reviewed_fields': reviewed_fields,
        }

        unconfirmed = Request('/api/receipt-archive/save', payload)
        unconfirmed.do_POST()
        self.assertEqual(unconfirmed.status, 400)
        self.assertIn('bevestig', unconfirmed.json()['error'].lower())
        self.assertEqual(preserved_rows(), original_rows)
        self.assertFalse((self.root / 'receipt_archive').exists())

        invalid_fields = dict(payload, review_confirmed=True)
        invalid_fields['reviewed_fields'] = dict(reviewed_fields, liters='24,7')
        invalid = Request('/api/receipt-archive/save', invalid_fields)
        invalid.do_POST()
        self.assertEqual(invalid.status, 400)
        self.assertIn('liters', invalid.json()['error'])
        self.assertEqual(preserved_rows(), original_rows)
        self.assertFalse((self.root / 'receipt_archive').exists())

        confirmed = Request(
            '/api/receipt-archive/save',
            dict(payload, review_confirmed=True))
        with patch.object(archive, 'pdf_details', return_value=(1, '')):
            confirmed.do_POST()
        self.assertEqual(confirmed.status, 201, confirmed.wfile.getvalue())
        receipt_id = confirmed.json()['id']

        with app.db() as con:
            row = con.execute(
                """SELECT a.filename, r.fields_json
                   FROM receipt_archive AS a
                   JOIN receipt_archive_review AS r ON r.receipt_id = a.id
                   WHERE a.id = ?""",
                (receipt_id,)).fetchone()
        self.assertEqual(row['filename'], payload['filename'])
        self.assertEqual(json.loads(row['fields_json']), reviewed_fields)
        self.assertEqual(preserved_rows(), original_rows)
        self.assertEqual(historical_receipt.read_bytes(), original_receipt)
        self.assertEqual(
            len(list((self.root / 'receipt_archive').glob('*.pdf'))), 1)

    def test_existing_image_receipts_remain_available_and_in_zip(self):
        app.RECEIPT_DIR.mkdir()
        (app.RECEIPT_DIR/'fuel_1.jpg').write_bytes(b'original')
        with app.db() as con:
            con.execute("""INSERT INTO events(created_at,type,odometer,receipt_path)
                           VALUES('2026-10-05T12:00:00+02:00','fuel',24553,'fuel_1.jpg')""")
        listing = Request('/api/receipt-archive')
        listing.do_GET()
        self.assertEqual(listing.json()['receipts'][0]['id'], 'legacy-1')
        old = Request('/api/receipt-archive/legacy-1')
        old.do_GET()
        self.assertEqual(old.wfile.getvalue(), b'original')
        z = Request('/api/receipt-archive/export.zip')
        z.do_GET()
        with zipfile.ZipFile(io.BytesIO(z.wfile.getvalue())) as exported:
            self.assertEqual(exported.namelist(), ['bestaande_bonnen/legacy-1_fuel_1.jpg'])
        self.assertEqual((app.RECEIPT_DIR/'fuel_1.jpg').read_bytes(), b'original')

    def test_reject_unsafe_names_files_and_requests_without_changes(self):
        for name in ('../bon.pdf', 'C:\\bon.pdf', 'bon.jpg', 'bon?.pdf', '.pdf'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                archive.clean_filename(name)
        for url in ('data:application/pdf;base64,ZmFrZQ==',
                    'data:image/png;base64,ZmFrZQ=='):
            with self.subTest(url=url), self.assertRaises(ValueError):
                archive.decode_file({'data_url': url})
        with app.db() as con:
            with self.assertRaises(ValueError):
                archive.prepare([], con)
        self.assertEqual(app.rows_events(), [])
        self.assertFalse((self.root/'receipt_archive').exists())

    def test_encrypted_backup_carries_new_pdfs(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        folder = self.root / 'receipt_archive'
        folder.mkdir()
        (folder / 'persisted.pdf').write_bytes(b'%PDF-1.4\\n%%EOF')
        password = 'test-password-long-enough'
        backup = app._encrypted_backup_file(password)
        encrypted = backup.read_bytes()
        self.assertEqual(encrypted[:8], b'RITTANK1')
        salt, nonce = encrypted[8:24], encrypted[24:36]
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                         iterations=310000).derive(password.encode('utf-8'))
        raw = AESGCM(key).decrypt(nonce, encrypted[36:], b'RIT_TANK_BACKUP_V1')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn('receipt_archive/persisted.pdf', z.namelist())
            self.assertEqual(z.read('receipt_archive/persisted.pdf'), b'%PDF-1.4\\n%%EOF')

    def test_route_not_public_without_ingress_or_session(self):
        for endpoint in ['/api/receipt-archive', '/api/receipt-archive/export.zip',
                         '/api/receipt-archive/1']:
            h = Request(endpoint, ingress=False)
            h.do_GET()
            self.assertEqual(h.status, 426)
        post = Request('/api/receipt-archive/prepare', {'files': []}, ingress=False)
        post.do_POST()
        self.assertEqual(post.status, 426)

    def test_ui_has_archive_and_no_auto_fuel_scanning(self):
        self.assertEqual(app.APP_VERSION, '34.06')
        self.assertIn('onclick="openReceiptArchive()"', app.APP_HTML)
        self.assertIn('receipt-archive.js', app.APP_HTML)
        self.assertNotIn('receipt_data_url=await readReceiptFile()', app.APP_HTML)
        self.assertNotIn('scanSelectedReceipt', app.APP_HTML)
        archive_ui = (Path(archive.__file__).with_name('receipt_archive.js')
                      .read_text(encoding='utf-8'))
        self.assertIn(
            '<input id="archiveFilename" maxlength="200">', archive_ui)
        self.assertIn(
            "filename:document.getElementById('archiveFilename').value",
            archive_ui)


if __name__ == '__main__':
    unittest.main()
