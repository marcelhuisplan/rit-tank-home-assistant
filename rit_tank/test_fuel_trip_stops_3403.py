"""34.03 fuel stops: isolated synthetic DB, never production data."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_v2300 import app

ADDRESS = 'Stationsstraat 12, 7461 AA Rijssen'


class FuelTripStopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in {
            'DATA_DIR': root,
            'DB_PATH': root / 'test.db',
            'OPTIONS_PATH': root / 'options.json',
            'RECEIPT_DIR': root / 'receipts',
            'publish_sensors_async': lambda: None,
            'now_local': lambda: datetime.fromisoformat('2026-10-19T12:00:00+02:00'),
            'google_place_details': lambda *args: {},
        }.items():
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()

    def payload(self, **kwargs):
        data = {
            'created_at': '2026-10-19T09:15:00+02:00',
            'odometer': 105,
            'liters': 10,
            'price_per_liter': 2.0,
            'station': 'Fictief Tankstation',
            'fuel_address': ADDRESS,
        }
        data.update(kwargs)
        return data

    def start_trip(self):
        with app.db() as con:
            trip_id = con.execute(
                "INSERT INTO business_trips(started_at,status,purpose,trip_type) "
                "VALUES('2026-10-19T09:00:00+02:00','active','Test','business')"
            ).lastrowid
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label) "
                "VALUES(?,0,'2026-10-19T09:00:00+02:00',100,'Startstraat 1, 7461 AA Rijssen')",
                (trip_id,),
            )
        return trip_id

    def test_adds_one_linked_stop_without_second_odometer_event(self):
        trip_id = self.start_trip()
        created = app.add_fuel(self.payload())
        with app.db() as con:
            rows = con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence_no', (trip_id,)).fetchall()
            fuel = con.execute('SELECT * FROM events WHERE id=?', (created['id'],)).fetchone()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]['fuel_event_id'], created['id'])
            self.assertEqual(rows[1]['event_id'], created['id'])
            self.assertEqual(rows[1]['manual_label'], ADDRESS)
            self.assertEqual(fuel['fuel_address'], ADDRESS)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)

    def test_no_active_trip_does_not_create_fictitious_trip(self):
        app.add_fuel(self.payload())
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops').fetchone()[0], 0)

    def test_address_without_house_number_is_rejected_without_changes(self):
        with self.assertRaisesRegex(ValueError, 'tankstationadres'):
            app.add_fuel(self.payload(fuel_address='Stationsstraat, Rijssen'))
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)

    def test_duplicate_tankbeurt_has_no_duplicate_stop(self):
        trip_id = self.start_trip()
        app.add_fuel(self.payload())
        with self.assertRaises(ValueError):
            app.add_fuel(self.payload())
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops WHERE trip_id=?', (trip_id,)).fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
