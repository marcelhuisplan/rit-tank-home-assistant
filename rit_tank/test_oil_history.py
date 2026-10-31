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


if __name__ == '__main__':
    unittest.main()
