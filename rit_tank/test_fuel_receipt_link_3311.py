"""Release 33.11: link PDFs to old tankbeurten without modifying historical fuel data."""
import base64
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_v2300 import app
from test_v3000 import Request
import receipt_archive as archive

PDF = b'%PDF-1.4\n1 0 obj <<>> endobj\n%%EOF'
DATA_URL = 'data:application/pdf;base64,' + base64.b64encode(PDF).decode('ascii')


class FuelReceiptLink3311Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for key, value in {
            'DATA_DIR': self.root,
            'DB_PATH': self.root / 'rit_tank.db',
            'OPTIONS_PATH': self.root / 'options.json',
            'RECEIPT_DIR': self.root / 'receipts',
            'publish_sensors_async': lambda: None,
        }.items():
            p = patch.object(app, key, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()
        with app.db() as con:
            con.execute("""INSERT INTO events(created_at,type,odometer,liters,price_per_liter,station,note)
                           VALUES('2026-10-01T13:45:00+02:00','fuel',24760,31.5,1.839,'Shell','Oude tankbeurt')""")
        self.original = self.fuel()
        p = patch.object(archive, 'pdf_details', return_value=(1, ''))
        p.start()
        self.addCleanup(p.stop)

    def fuel(self):
        with app.db() as con:
            return dict(con.execute('SELECT * FROM events WHERE id=1').fetchone())

    def post(self, payload, id=1):
        req = Request(f'/api/fuel/{id}/receipt', payload)
        req.do_POST()
        return req

    def archive_pdf(self, filename='Oude_bon.pdf'):
        with app.db() as con:
            return archive.save(con, self.root / 'receipt_archive', DATA_URL,
                                filename, confirm_duplicate=True)

    def test_existing_archive_pdf_links_to_old_fuel_without_duplicates(self):
        pdf = self.archive_pdf()
        response = self.post({'archive_id': pdf['id']})
        self.assertEqual(response.status, 201, response.wfile.getvalue())
        row = self.fuel()
        self.assertEqual(row['receipt_archive_id'], pdf['id'])
        for field, value in self.original.items():
            if field != 'receipt_archive_id':
                self.assertEqual(row[field], value, field)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        h = Request('/api/summary')
        h.do_GET()
        self.assertEqual(h.json()['recent'][0]['receipt_archive_id'], pdf['id'])
        for suffix, disposition in [('', 'inline'), ('?download=1', 'attachment')]:
            item = Request('/api/receipt-archive/'+str(pdf['id'])+suffix)
            item.do_GET()
            self.assertEqual(item.status, 200)
            self.assertEqual(item.wfile.getvalue(), PDF)
            self.assertIn(disposition, item.response_headers['Content-Disposition'])
        app.init_db()
        self.assertEqual(self.fuel()['receipt_archive_id'], pdf['id'])

    def test_uploaded_pdf_saved_and_linked_without_changing_any_fuel_facts(self):
        response = self.post({'pdf_data_url': DATA_URL, 'filename': 'Later_ingescand.pdf'})
        self.assertEqual(response.status, 201, response.wfile.getvalue())
        self.assertEqual(self.fuel()['receipt_archive_id'], response.json()['archive_id'])
        for field, value in self.original.items():
            if field != 'receipt_archive_id':
                self.assertEqual(self.fuel()[field], value, field)
        self.assertEqual(len(list((self.root/'receipt_archive').glob('*.pdf'))), 1)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_duplicate_and_existing_link_require_separate_confirmation(self):
        first = self.archive_pdf()
        duplicated = self.post({'pdf_data_url':DATA_URL, 'filename':'Oude_bon.pdf'})
        self.assertEqual(duplicated.status,409)
        self.assertEqual(duplicated.json()['code'],'RECEIPT_DUPLICATE')
        self.assertIsNone(self.fuel()['receipt_archive_id'])
        second = self.archive_pdf('Tweede_bon.pdf')
        self.assertEqual(self.post({'archive_id':first['id']}).status,201)
        denied = self.post({'archive_id':second['id']})
        self.assertEqual(denied.status,409)
        self.assertEqual(denied.json()['code'],'FUEL_RECEIPT_EXISTS')
        self.assertEqual(self.fuel()['receipt_archive_id'],first['id'])
        approved = self.post({'archive_id':second['id'],'confirm_replace':True})
        self.assertEqual(approved.status,201)
        self.assertEqual(self.fuel()['receipt_archive_id'],second['id'])
        self.assertEqual(len(list((self.root/'receipt_archive').glob('*.pdf'))),2)
        self.assertEqual(self.post({'archive_id':second['id'],'confirm_replace':True}).status,400)

    def test_legacy_image_stays_untouched_after_replacement(self):
        self.root.joinpath('receipts').mkdir()
        self.root.joinpath('receipts/original.jpg').write_bytes(b'original')
        with app.db() as con:
            con.execute("UPDATE events SET receipt_path='original.jpg' WHERE id=1")
        pdf = self.archive_pdf()
        self.assertEqual(self.post({'archive_id':pdf['id']}).status,409)
        self.assertEqual(self.post({'archive_id':pdf['id'],'confirm_replace':True}).status,201)
        self.assertEqual(self.fuel()['receipt_path'],'original.jpg')
        self.assertEqual(self.root.joinpath('receipts/original.jpg').read_bytes(),b'original')

    def test_reject_missing_nonfuel_and_invalid_archive_input(self):
        with app.db() as con:
            con.execute("INSERT INTO events(created_at,type,odometer) VALUES('2026-10-02T09:00:00+02:00','odometer',24765)")
        for id, payload in [(77, {'archive_id':1}), (2, {'archive_id':1}),
                            (1,{'archive_id':1}), (1,{'archive_id':'1'}),
                            (1,{'archive_id':True}), (1,{}),
                            (1,{'archive_id':1,'pdf_data_url':DATA_URL})]:
            with self.subTest(id=id,payload=payload):
                self.assertEqual(self.post(payload,id).status,400)
                self.assertIsNone(self.fuel()['receipt_archive_id'])

    def test_new_pdf_and_link_are_in_encrypted_backup(self):
        response = self.post({'pdf_data_url':DATA_URL, 'filename':'Back-up.pdf'})
        self.assertEqual(response.status,201)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        binary=app._encrypted_backup_file('very-long-test-password').read_bytes()
        salt,nonce=binary[8:24],binary[24:36]
        key=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=310000).derive(b'very-long-test-password')
        with zipfile.ZipFile(io.BytesIO(AESGCM(key).decrypt(nonce,binary[36:],b'RIT_TANK_BACKUP_V1'))) as z:
            self.assertIn('rit_tank.db',z.namelist())
            self.assertEqual(len([n for n in z.namelist() if n.startswith('receipt_archive/')]),1)
            restored=self.root/'restored.db'
            restored.write_bytes(z.read('rit_tank.db'))
        with sqlite3.connect(restored) as con:
            self.assertEqual(con.execute('SELECT receipt_archive_id FROM events WHERE id=1').fetchone()[0],
                             response.json()['archive_id'])

    def test_ui_contract(self):
        self.assertEqual(app.APP_VERSION,'33.11')
        self.assertIn('📎 Bon toevoegen', app.APP_HTML)
        self.assertIn('openFuelReceiptLink(', app.APP_HTML)
        self.assertIn('Bon bekijken', app.APP_HTML)
        self.assertIn('Downloaden', app.APP_HTML)
        ui=(Path(__file__).parent/'receipt_archive.js').read_text()
        for token in ('fuelReceiptCamera','fuelReceiptExistingSelect','Bon koppelen',
                      'confirm_replace','confirm_duplicate'):
            self.assertIn(token,ui)
