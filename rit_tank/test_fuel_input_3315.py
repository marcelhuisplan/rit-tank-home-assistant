"""Release 33.15: numeric fuel values are persisted without mutating earlier events."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app


class FuelInput3315Tests(unittest.TestCase):
    def test_precise_values_and_existing_events_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(app, 'DATA_DIR', root), \
                 patch.object(app, 'DB_PATH', root / 'test.db'), \
                 patch.object(app, 'OPTIONS_PATH', root / 'options.json'), \
                 patch.object(app, 'RECEIPT_DIR', root / 'receipts'), \
                 patch.object(app, 'publish_sensors_async', lambda: None):
                app.init_db()
                first = app.add_fuel({
                    'odometer': 26199, 'liters': 24.80,
                    'price_per_liter': 1.899, 'created_at': '2026-10-09T12:00:00+02:00',
                    'station': 'Bestaand', 'full_tank': True
                })
                with app.db() as con:
                    before = tuple(con.execute(
                        'SELECT odometer,liters,price_per_liter,station FROM events WHERE id=?',
                        (first['id'],)).fetchone())
                second = app.add_fuel({
                    'odometer': 26200, 'liters': 43.00,
                    'price_per_liter': 2.400, 'created_at': '2026-10-10T15:30:00+02:00',
                    'station': 'Nieuwe pomp', 'full_tank': True
                })
                self.assertEqual(second['cost'], 103.20)
                with app.db() as con:
                    original = tuple(con.execute(
                        'SELECT odometer,liters,price_per_liter,station FROM events WHERE id=?',
                        (first['id'],)).fetchone())
                    recent = tuple(con.execute(
                        'SELECT odometer,liters,price_per_liter,station FROM events WHERE id=?',
                        (second['id'],)).fetchone())
                self.assertEqual(original, before)
                self.assertEqual(recent, (26200, 43, 2.4, 'Nieuwe pomp'))


if __name__ == '__main__':
    unittest.main()
