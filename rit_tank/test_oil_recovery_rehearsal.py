"""Isolated disaster-recovery rehearsal for PR #56; no Home Assistant or real /data.

Creates a legacy 34.02-style SQLite database with fictional bookkeeping, upgrades
it, creates an encrypted .rtbackup with fake receipt files, decrypts and restores
the archived data in another temporary directory, and models rollback to an old
database snapshot. This is NOT a Home Assistant Supervisor restore test.
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from unittest.mock import patch

import app
import oil_history

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PASSWORD = 'FICTIEF-testwachtwoord-voor-herstel-2026!'
OLD_FUEL_RECEIPT = b'FICTIONAL-IMAGE-BYTES-FOR-RECEIPT'
OLD_PDF_RECEIPT = b'%PDF-1.4\n% FICTIONAL PDF RECEIPT BYTES\n'
OIL = {
    'performed_on': '2026-09-20', 'kind': 'top_up', 'oil_type': '5W-30',
    'amount_ml': 300, 'odometer': 85000,
}


@contextmanager
def use_directory(directory):
    with patch.object(app, 'DATA_DIR', directory), \
         patch.object(app, 'DB_PATH', directory / 'rit_tank.db'), \
         patch.object(app, 'OPTIONS_PATH', directory / 'options.json'), \
         patch.object(app, 'RECEIPT_DIR', directory / 'receipts'):
        yield


def legacy_rows(connection):
    """Read only columns already known to the older release."""
    return {
        'settings': [tuple(r) for r in connection.execute(
            'SELECT key,value FROM settings ORDER BY key')],
        'events': [tuple(r) for r in connection.execute(
            'SELECT id,created_at,type,odometer,liters,price_per_liter,station FROM events ORDER BY id')],
        'trips': [tuple(r) for r in connection.execute(
            'SELECT id,started_at,ended_at,status,purpose FROM business_trips ORDER BY id')],
        'stops': [tuple(r) for r in connection.execute(
            'SELECT id,trip_id,sequence_no,created_at,odometer FROM trip_stops ORDER BY id')],
        'receipts': [tuple(r) for r in connection.execute(
            'SELECT id,stored_name,filename,sha256 FROM receipt_archive ORDER BY id')],
    }


def restore_sqlite_backup(source, destination):
    """Test-only, consistent snapshot of the SQLite DB, including committed WAL."""
    with sqlite3.connect(source) as old, sqlite3.connect(destination) as new:
        old.backup(new)
        assert new.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


class IsolatedOilRecoveryRehearsal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rit-tank-oil-recovery-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original'
        self.original.mkdir()
        self.paths = use_directory(self.original)
        self.paths.__enter__()
        self.addCleanup(self.paths.__exit__, None, None, None)
        self.db_file = self.original / 'rit_tank.db'

        # Deliberately a legacy schema: no oil_maintenance table or newer ALTERs.
        with sqlite3.connect(self.db_file) as con:
            con.executescript("""
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('odometer','fuel')),
                    odometer REAL NOT NULL, liters REAL, price_per_liter REAL,
                    station TEXT, full_tank INTEGER NOT NULL DEFAULT 0, note TEXT
                );
                CREATE TABLE business_trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
                    ended_at TEXT, status TEXT NOT NULL DEFAULT 'active',
                    purpose TEXT, client TEXT, note TEXT
                );
                CREATE TABLE trip_stops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER NOT NULL,
                    sequence_no INTEGER NOT NULL, created_at TEXT NOT NULL,
                    odometer REAL NOT NULL, latitude REAL, longitude REAL,
                    location_accuracy REAL, location_source TEXT, place_id TEXT,
                    manual_label TEXT, note TEXT, event_id INTEGER,
                    FOREIGN KEY(trip_id) REFERENCES business_trips(id) ON DELETE CASCADE
                );
                CREATE TABLE receipt_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stored_name TEXT NOT NULL UNIQUE, filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL, created_at TEXT NOT NULL
                );
                INSERT INTO settings VALUES('vehicle_name','Fictieve testauto');
                INSERT INTO settings VALUES('km_reimbursement_rate','0.25');
                INSERT INTO settings VALUES('license_plate','TEST-123');
                INSERT INTO events(id,created_at,type,odometer) VALUES
                    (1,'2026-07-01T09:00:00+02:00','odometer',84400);
                INSERT INTO events(id,created_at,type,odometer,liters,price_per_liter,station)
                    VALUES(2,'2026-08-01T09:00:00+02:00','fuel',85000,38.50,1.859,'Testpomp');
                INSERT INTO events(id,created_at,type,odometer) VALUES
                    (3,'2026-10-01T09:00:00+02:00','odometer',85200);
                INSERT INTO business_trips(id,started_at,ended_at,status,purpose)
                    VALUES(4,'2026-08-03T08:00:00+02:00','2026-08-03T10:00:00+02:00',
                           'completed','Fictieve afspraak');
                INSERT INTO trip_stops(id,trip_id,sequence_no,created_at,odometer)
                    VALUES(10,4,1,'2026-08-03T08:00:00+02:00',85020);
                INSERT INTO trip_stops(id,trip_id,sequence_no,created_at,odometer)
                    VALUES(11,4,2,'2026-08-03T10:00:00+02:00',85065);
            """)
            con.execute('INSERT INTO receipt_archive '
                        '(id,stored_name,filename,sha256,created_at) VALUES(?,?,?,?,?)',
                        (8, 'fictief-bon.pdf', 'Fictieve tankbon.pdf',
                         hashlib.sha256(OLD_PDF_RECEIPT).hexdigest(),
                         '2026-08-01T09:00:00+02:00'))
        (self.original / 'options.json').write_text(json.dumps({
            'vehicle_name': 'Fictieve testauto', 'timezone': 'Europe/Amsterdam',
            'standalone_password': 'not-a-real-password',
        }), encoding='utf-8')
        (self.original / 'receipts').mkdir()
        (self.original / 'receipt_archive').mkdir()
        (self.original / 'receipts' / 'fictief-bon.jpg').write_bytes(OLD_FUEL_RECEIPT)
        (self.original / 'receipt_archive' / 'fictief-bon.pdf').write_bytes(OLD_PDF_RECEIPT)
        with sqlite3.connect(self.db_file) as con:
            self.legacy = legacy_rows(con)
            self.assertFalse(con.execute("SELECT 1 FROM sqlite_master "
                                         "WHERE type='table' AND name='oil_maintenance'").fetchone())

    def assert_legacy_unchanged(self, db_file):
        with sqlite3.connect(db_file) as con:
            self.assertEqual(legacy_rows(con), self.legacy)
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNone(con.execute('PRAGMA foreign_key_check').fetchone())

    def migrate_and_add_oil(self):
        app.init_db()  # actual runtime additive migration, not a hand-written mock
        self.assert_legacy_unchanged(self.db_file)
        with app.db() as con:
            created = oil_history.create_entry(con, OIL, app.iso_local(),
                                               registered_odometer=85200)
        self.assertEqual(created['amount_ml'], 300)
        self.assertEqual(created['oil_type'], '5W-30')

    def test_existing_database_upgrade_repeated_is_non_destructive(self):
        self.migrate_and_add_oil()
        app.init_db()
        self.assert_legacy_unchanged(self.db_file)
        with app.db() as con:
            self.assertEqual(len(oil_history.list_entries(con)), 1)
        self.assertEqual(app.current_odometer(app.rows_events()), 85200)
        self.assertEqual(app.get_settings()['km_reimbursement_rate'], '0.25')

    def test_encrypted_rtbackup_decrypt_and_restore_to_separate_directory(self):
        self.migrate_and_add_oil()
        encrypted_file = app._encrypted_backup_file(PASSWORD)  # actual production writer
        raw = encrypted_file.read_bytes()
        self.assertEqual(raw[:8], b'RITTANK1')
        salt, nonce, ciphertext = raw[8:24], raw[24:36], raw[36:]
        def derive(password):
            return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                             salt=salt, iterations=310000).derive(password.encode())
        with self.assertRaises(InvalidTag):
            AESGCM(derive('ongeldig testwachtwoord')).decrypt(
                nonce, ciphertext, b'RIT_TANK_BACKUP_V1')
        decrypted = AESGCM(derive(PASSWORD)).decrypt(
            nonce, ciphertext, b'RIT_TANK_BACKUP_V1')
        restored = self.root / 'restored_from_rtbackup'
        restored.mkdir()
        with zipfile.ZipFile(io.BytesIO(decrypted)) as archive:
            self.assertEqual(archive.testzip(), None)
            names = set(archive.namelist())
            self.assertTrue({'rit_tank.db', 'receipts/fictief-bon.jpg',
                             'receipt_archive/fictief-bon.pdf', 'backup.json'} <= names)
            # Important limitation: the app backup is NOT a full HA config backup.
            self.assertNotIn('options.json', names)
            self.assertEqual(archive.read('receipts/fictief-bon.jpg'), OLD_FUEL_RECEIPT)
            self.assertEqual(archive.read('receipt_archive/fictief-bon.pdf'), OLD_PDF_RECEIPT)
            for name in ('rit_tank.db', 'receipts/fictief-bon.jpg',
                         'receipt_archive/fictief-bon.pdf'):
                target = restored / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        with use_directory(restored):
            app.init_db()
            with app.db() as con:
                oil = oil_history.list_entries(con)
                self.assertEqual(len(oil), 1)
                self.assertEqual((oil[0]['amount_ml'], oil[0]['oil_type'],oil[0]['odometer']),
                                 (300, '5W-30', 85000))
            self.assert_legacy_unchanged(restored / 'rit_tank.db')
            self.assertEqual(app.current_odometer(app.rows_events()), 85200)
            self.assertEqual(app.get_settings()['license_plate'], 'TEST-123')
            self.assertEqual((restored/'receipts'/'fictief-bon.jpg').read_bytes(),
                             OLD_FUEL_RECEIPT)
            self.assertEqual((restored/'receipt_archive'/'fictief-bon.pdf').read_bytes(),
                             OLD_PDF_RECEIPT)
        self.assertFalse((restored/'options.json').exists())

    def test_pre_upgrade_database_restore_loses_new_oil_but_preserves_old_entries(self):
        # Snapshots are immutable archives; do not copy a live SQLite database with shutil.
        before = self.root / 'pre_upgrade.db'
        restore_sqlite_backup(self.db_file, before)
        self.migrate_and_add_oil()
        with app.db() as con:
            self.assertEqual(len(oil_history.list_entries(con)), 1)
        rollback = self.root / 'rollback_from_old_snapshot'
        rollback.mkdir()
        shutil.copy2(before, rollback / 'rit_tank.db')
        with use_directory(rollback):
            # Existing legacy tables are readable by an old-style SQL consumer.
            self.assert_legacy_unchanged(rollback / 'rit_tank.db')
            with sqlite3.connect(rollback / 'rit_tank.db') as con:
                self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='oil_maintenance'").fetchone())
            # Later updating this rollback copy creates an empty oil table.
            app.init_db()
            with app.db() as con:
                self.assertEqual(oil_history.list_entries(con), [])
            self.assert_legacy_unchanged(rollback / 'rit_tank.db')
        # Original data with the new oil entry still exists independently.
        with app.db() as con:
            self.assertEqual(len(oil_history.list_entries(con)), 1)

    def test_complete_test_data_directory_copy_retains_options_too(self):
        # Simulates *data-folder* retention, not the Supervisor backup file format.
        self.migrate_and_add_oil()
        target = self.root / 'complete_test_data_dir'
        target.mkdir()
        restore_sqlite_backup(self.db_file, target / 'rit_tank.db')
        shutil.copy2(self.original / 'options.json', target / 'options.json')
        shutil.copytree(self.original / 'receipts', target / 'receipts')
        shutil.copytree(self.original / 'receipt_archive', target / 'receipt_archive')
        with use_directory(target):
            app.init_db()
            self.assert_legacy_unchanged(target / 'rit_tank.db')
            self.assertEqual(app.get_settings()['vehicle_name'], 'Fictieve testauto')
            self.assertEqual(app.load_options()['standalone_password'], 'not-a-real-password')
            with app.db() as con:
                self.assertEqual(len(oil_history.list_entries(con)), 1)


if __name__ == '__main__':
    unittest.main()
