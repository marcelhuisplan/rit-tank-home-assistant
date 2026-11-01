"""Regression checks: isolated oil history and backwards-compatible schema."""
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location('oil_history', Path(__file__).with_name('oil_history.py'))
oil = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = oil
spec.loader.exec_module(oil)


class OilMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.connection = sqlite3.connect(Path(self.tmp.name) / 'history.db')
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, odometer REAL, type TEXT)')
        self.connection.execute("INSERT INTO events VALUES (7, 64321, 'fuel')")
        self.connection.execute('CREATE TABLE business_trips (id INTEGER PRIMARY KEY, status TEXT)')
        self.connection.execute("INSERT INTO business_trips VALUES (3, 'completed')")
        oil.init_schema(self.connection)

    def tearDown(self):
        self.connection.close()
        self.tmp.cleanup()

    @staticmethod
    def payload(**changes):
        return dict(performed_on='2026-10-31', kind='top_up', oil_type='0W-20',
                    amount_ml=300, odometer=64603, **changes)

    def test_save_view_edit_delete_and_isolation(self):
        first = oil.create_entry(self.connection, self.payload(), '2026-10-31T12:00:00+01:00')
        self.assertEqual(first['amount_ml'], 300)
        self.assertEqual(first['kind'], 'top_up')
        changed = oil.update_entry(self.connection, first['id'], dict(
            performed_on='2026-10-30', kind='change', oil_type='5W-30',
            amount_ml=4400, odometer=64000), '2026-10-31T13:00:00+01:00')
        self.assertEqual(changed['kind'], 'change')
        self.assertEqual(changed['amount_ml'], 4400)
        with self.assertRaises(ValueError):
            oil.delete_entry(self.connection, first['id'], confirmed=False)
        self.assertEqual(len(oil.list_entries(self.connection)), 1)
        oil.delete_entry(self.connection, first['id'], confirmed=True)
        self.assertEqual(oil.list_entries(self.connection), [])
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM events').fetchone()), (7, 64321, 'fuel'))
        self.assertEqual(tuple(self.connection.execute('SELECT * FROM business_trips').fetchone()), (3, 'completed'))

    def test_chronological_order_and_quantity_in_ml(self):
        oil.create_entry(self.connection, self.payload(), '2026-10-31T12:00:00')
        early = dict(performed_on='2026-01-02', kind='change', oil_type='5W-30',
                     amount_ml=4500, odometer=60000)
        oil.create_entry(self.connection, early, '2026-10-31T12:00:00')
        self.assertEqual([x['amount_ml'] for x in oil.list_entries(self.connection)], [300, 4500])

    def test_repeated_migration_preserves_entries(self):
        oil.create_entry(self.connection, self.payload(), '2026-10-31T12:00:00')
        oil.init_schema(self.connection)
        self.assertEqual(len(oil.list_entries(self.connection)), 1)
        self.assertEqual(self.connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_upgrade_3404_oil_rows_and_note_backup(self):
        # Real 34.03/34.04 table shape, containing an existing registration.
        legacy = sqlite3.connect(':memory:')
        self.addCleanup(legacy.close)
        legacy.row_factory = sqlite3.Row
        legacy.execute('CREATE TABLE oil_maintenance (id INTEGER PRIMARY KEY, '
                       'vehicle_key TEXT, performed_on TEXT, kind TEXT, oil_type TEXT, '
                       'amount_ml INTEGER, odometer INTEGER, created_at TEXT, updated_at TEXT)')
        legacy.execute("INSERT INTO oil_maintenance VALUES(41,'primary','2026-01-02',"
                       "'change','5W-30',4500,60000,'original','original')")
        before = dict(legacy.execute('SELECT * FROM oil_maintenance').fetchone())
        oil.init_schema(legacy)
        oil.init_schema(legacy)
        row = dict(legacy.execute('SELECT * FROM oil_maintenance').fetchone())
        self.assertEqual(row.pop('note'), '')
        self.assertEqual(row, before)
        updated = oil.update_entry(legacy, 41, dict(before, note='Thuis bijgewerkt\nFictieve notitie'), 'edited')
        self.assertEqual(updated['note'], 'Thuis bijgewerkt\nFictieve notitie')
        legacy.commit()
        with sqlite3.connect(Path(self.tmp.name) / 'note-backup.db') as backup:
            legacy.backup(backup)
            self.assertEqual(backup.execute('SELECT note FROM oil_maintenance WHERE id=41').fetchone()[0], updated['note'])
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_optional_note_validation_and_edit(self):
        first = oil.create_entry(self.connection, self.payload(), 'created')
        self.assertEqual(first['note'], '')
        edited = oil.update_entry(self.connection, first['id'], self.payload(note='<b>Thuis</b>'), 'edited')
        self.assertEqual(edited['note'], '<b>Thuis</b>')
        for note in (None, 123, 'x' * 1001, 'bad\x00note'):
            with self.subTest(note=repr(note)[:30]), self.assertRaises(ValueError):
                oil.create_entry(self.connection, self.payload(note=note), 'bad')

    def test_validation_rejects_bad_values(self):
        for key, value in [('kind', 'other'), ('performed_on', '2026-02-30'),
                           ('oil_type', '<bad>\n'), ('amount_ml', '1.5'), ('amount_ml', 0),
                           ('amount_ml', 20001), ('odometer', -2), ('odometer', 'NaN')]:
            values = self.payload()
            values[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                oil.create_entry(self.connection, values, '2026-10-31T12:00:00')
        self.assertEqual(oil.list_entries(self.connection), [])

    def test_not_found_does_not_affect_another_entry(self):
        oil.create_entry(self.connection, self.payload(), '2026-10-31T12:00:00')
        with self.assertRaises(oil.OilEntryNotFound):
            oil.get_entry(self.connection, 999)
        with self.assertRaises(oil.OilEntryNotFound):
            oil.delete_entry(self.connection, 999, confirmed=True)
        self.assertEqual(len(oil.list_entries(self.connection)), 1)


    def test_rejects_odometer_outside_six_digit_vehicle_range(self):
        for km in (-1, 1000000, 9999999, '65.500', '', True):
            with self.subTest(km=km), self.assertRaises(ValueError):
                oil.create_entry(self.connection, dict(
                    performed_on='2026-10-31', kind='top_up', oil_type='0W-20',
                    amount_ml=300, odometer=km), '2026-10-31T12:00:00')
        self.assertEqual(oil.list_entries(self.connection), [])

    def test_odometer_above_vehicle_is_confirmed_and_never_updates_vehicle(self):
        payload = dict(performed_on='2026-10-31', kind='top_up', oil_type='0W-20',
                       amount_ml=300, odometer=70000)
        with self.assertRaises(oil.OilOdometerConfirmationRequired):
            oil.create_entry(self.connection, payload, '2026-10-31T12:00:00',
                             registered_odometer=64321)
        self.assertEqual(oil.list_entries(self.connection), [])
        payload['confirm_odometer_anomaly'] = True
        created = oil.create_entry(self.connection, payload, '2026-10-31T12:00:00',
                                   registered_odometer=64321)
        self.assertEqual(created['odometer'], 70000)
        self.assertEqual(self.connection.execute(
            'SELECT odometer FROM events WHERE id=7').fetchone()[0], 64321)

    def test_out_of_order_historical_odometer_needs_confirmation(self):
        oil.create_entry(self.connection, dict(performed_on='2026-01-01', kind='change',
            oil_type='5W-30', amount_ml=4500, odometer=60000), '2026-10-31T12:00:00')
        later = oil.create_entry(self.connection, dict(performed_on='2026-06-01',
            kind='top_up', oil_type='5W-30', amount_ml=250, odometer=62000),
            '2026-10-31T12:00:00')
        anomalous = dict(performed_on='2026-03-01', kind='top_up',
                         oil_type='5W-30', amount_ml=300, odometer=63000)
        with self.assertRaises(oil.OilOdometerConfirmationRequired):
            oil.create_entry(self.connection, anomalous, '2026-10-31T12:00:00')
        self.assertEqual(len(oil.list_entries(self.connection)), 2)
        anomalous['confirm_odometer_anomaly'] = True
        created = oil.create_entry(self.connection, anomalous, '2026-10-31T12:00:00')
        self.assertEqual(created['odometer'], 63000)
        self.assertEqual(oil.get_entry(self.connection, later['id'])['odometer'], 62000)

    def test_edit_delete_only_target_record(self):
        first = oil.create_entry(self.connection, self.payload(), '2026-10-31T12:00:00')
        another = oil.create_entry(self.connection, dict(
            performed_on='2026-10-31', kind='change', oil_type='5W-30',
            amount_ml=4500, odometer=64604), '2026-10-31T12:01:00')
        before = oil.get_entry(self.connection, another['id']).copy()
        oil.update_entry(self.connection, first['id'], dict(
            performed_on='2026-10-31', kind='top_up', oil_type='0W-20',
            amount_ml=500, odometer=64603), '2026-10-31T13:00:00')
        self.assertEqual(oil.get_entry(self.connection, another['id']), before)
        oil.delete_entry(self.connection, first['id'], confirmed=True)
        self.assertEqual(oil.list_entries(self.connection), [before])

    def test_backup_round_trip_and_old_schema_compatibility(self):
        oil.create_entry(self.connection, self.payload(), '2026-10-31T12:00:00')
        self.connection.commit()  # SQLite backup must read a committed source, not its own write transaction.
        target_path = Path(self.tmp.name) / 'backup.db'
        with sqlite3.connect(target_path) as target:
            self.connection.backup(target)
            self.assertEqual(target.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        with sqlite3.connect(target_path) as recovered:
            recovered.row_factory = sqlite3.Row
            self.assertEqual(len(oil.list_entries(recovered)), 1)
            self.assertEqual(tuple(recovered.execute('SELECT * FROM events').fetchone()),
                             (7, 64321, 'fuel'))
            self.assertEqual(tuple(recovered.execute('SELECT * FROM business_trips').fetchone()),
                             (3, 'completed'))
            # Older runtimes can query their original tables; the extra table is ignored.
            self.assertTrue(recovered.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oil_maintenance'"
            ).fetchone())


if __name__ == '__main__':
    unittest.main()
