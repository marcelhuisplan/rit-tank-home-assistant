"""34.04 fuel stops: isolated synthetic DB, never production data."""
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

    def test_oil_crud_during_active_trip_never_changes_driving_data(self):
        import oil_history
        self.start_trip()
        app.add_fuel(self.payload())
        def snapshot(con):
            tables = ('events', 'business_trips', 'trip_stops', 'settings', 'receipt_archive')
            return {table: [tuple(row) for row in con.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                    for table in tables}
        with app.db() as con:
            before = snapshot(con)
            oil = dict(performed_on='2026-10-19', kind='top_up', oil_type='5W-30',
                       amount_ml=300, odometer=105, note='Thuis, tijdens actieve testrit')
            entry = oil_history.create_entry(con, oil, app.iso_local(), registered_odometer=105)
            self.assertEqual(snapshot(con), before)
            oil_history.update_entry(con, entry['id'], dict(oil, kind='change', amount_ml=4500), app.iso_local())
            self.assertEqual(snapshot(con), before)
            oil_history.delete_entry(con, entry['id'], confirmed=True)
            self.assertEqual(snapshot(con), before)

    def test_missing_address_is_saved_without_invented_details(self):
        trip_id = self.start_trip()
        created = app.add_fuel(self.payload(fuel_address=''))
        with app.db() as con:
            row = con.execute('SELECT fuel_address FROM events WHERE id=?', (created['id'],)).fetchone()
            stop = con.execute('SELECT manual_label FROM trip_stops WHERE fuel_event_id=? AND trip_id=?',
                               (created['id'], trip_id)).fetchone()
            self.assertEqual(row['fuel_address'], '')
            self.assertEqual(stop['manual_label'], 'Adres tankstop nog niet bevestigd')

    def test_missing_address_and_no_active_trip_keeps_normal_fuel(self):
        saved = app.add_fuel(self.payload(fuel_address=''))
        with app.db() as con:
            self.assertIsNotNone(con.execute('SELECT id FROM events WHERE id=?', (saved['id'],)).fetchone())
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops').fetchone()[0], 0)

    def test_deleting_trip_retains_linked_fuel_event_and_receipt(self):
        trip_id = self.start_trip()
        saved = app.add_fuel(self.payload())
        app.delete_business_trip(trip_id)
        with app.db() as con:
            self.assertIsNotNone(con.execute('SELECT id FROM events WHERE id=?', (saved['id'],)).fetchone())
            self.assertEqual(con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0], 0)

    def test_editing_trip_cannot_overwrite_exact_tank_odometer_or_time(self):
        trip_id = self.start_trip()
        saved = app.add_fuel(self.payload())
        with app.db() as con:
            stop_id = con.execute('SELECT id FROM trip_stops WHERE fuel_event_id=?',
                                  (saved['id'],)).fetchone()['id']
        with self.assertRaisesRegex(ValueError, 'exacte tellerstand'):
            app.edit_business_trip(trip_id, {'stops': [{'id': stop_id, 'odometer': 108}]})
        with app.db() as con:
            self.assertEqual(con.execute('SELECT odometer FROM events WHERE id=?',
                                         (saved['id'],)).fetchone()[0], 105)

    def test_pdf_address_accepts_house_number_without_postcode_but_not_unknown(self):
        from pdf_report import report_stop_address
        correct = report_stop_address(
            {'fuel_event_id': 1, 'manual_label': 'Stationsstraat 12, Rijssen'},
            dependencies={})
        self.assertEqual(correct, 'Stationsstraat 12, Rijssen')
        unknown = report_stop_address(
            {'fuel_event_id': 1, 'manual_label': 'Stationsstraat, Rijssen'},
            dependencies={})
        self.assertEqual(unknown, 'Adres ontbreekt')

    def test_duplicate_tankbeurt_has_no_duplicate_stop(self):
        trip_id = self.start_trip()
        app.add_fuel(self.payload())
        with self.assertRaises(ValueError):
            app.add_fuel(self.payload())
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops WHERE trip_id=?', (trip_id,)).fetchone()[0], 2)

    def test_splitting_business_leg_does_not_add_kilometers_or_reimbursement(self):
        from decimal import Decimal
        from pdf_report import build_business_report_rows
        trip_id = self.start_trip()
        app.add_fuel(self.payload())
        with app.db() as con:
            con.execute(
                "INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label,segment_trip_type) "
                "VALUES(?,2,'2026-10-19T09:45:00+02:00',120,'Eindstraat 20, 7461 AA Rijssen','business')",
                (trip_id,),
            )
        trip, stops = app.business_trips_raw()[0]
        enriched = app.enrich_business_trip(trip, stops, resolve=False)
        self.assertEqual(enriched['km'], 20)
        self.assertEqual(enriched['business_km'], 20)
        rows = build_business_report_rows([enriched], Decimal('0.25'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row['km'] for row in rows), 20)
        self.assertEqual(sum((row['reimbursement'] for row in rows), Decimal(0)), Decimal('5.00'))
        self.assertEqual(rows[0]['destination']['fuel_event_id'], stops[1]['fuel_event_id'])



if __name__ == '__main__':
    unittest.main()
