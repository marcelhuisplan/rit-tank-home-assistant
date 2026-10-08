"""Regression tests for historical fuel entries inserted by date and odometer."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_v2300 import app


class HistoricalFuelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fixed_now = datetime.fromisoformat('2026-10-07T18:00:00+02:00')
        for name, value in {
            'DATA_DIR': root,
            'DB_PATH': root / 'test.db',
            'OPTIONS_PATH': root / 'options.json',
            'RECEIPT_DIR': root / 'receipts',
            'publish_sensors_async': lambda: None,
            'now_local': lambda: fixed_now,
            'google_place_details': lambda *a: {},
        }.items():
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()

    def seed(self):
        with app.db() as con:
            con.execute(
                "INSERT INTO events(created_at,type,odometer,liters,price_per_liter,full_tank) "
                "VALUES('2026-10-05T10:00:00+02:00','fuel',24500,20,2,1)"
            )
            con.execute(
                "INSERT INTO events(created_at,type,odometer) "
                "VALUES('2026-10-05T12:00:00+02:00','odometer',24560)"
            )
            con.execute(
                "INSERT INTO events(created_at,type,odometer,liters,price_per_liter,full_tank) "
                "VALUES('2026-10-06T10:00:00+02:00','fuel',24600,10,2,1)"
            )
            con.execute(
                "INSERT INTO events(created_at,type,odometer) "
                "VALUES('2026-10-07T10:00:00+02:00','odometer',24700)"
            )

    def fuel(self, odometer=24553, created_at='2026-10-05T12:00:00+02:00'):
        return {
            'odometer': odometer,
            'created_at': created_at,
            'liters': 5,
            'price_per_liter': 2,
            'full_tank': False,
            'station': 'Historisch tankstation',
        }

    def test_historical_fuel_is_inserted_between_same_minute_neighbors_and_recalculates(self):
        self.seed()
        before = app.summary()
        self.assertEqual(before['current_odometer'], 24700)
        self.assertEqual(before['latest_full_cycle']['l100'], 10)

        result = app.add_fuel(self.fuel())
        self.assertTrue(result['ok'])

        rows = app.rows_events()
        self.assertEqual([r['odometer'] for r in rows], [24500, 24553, 24560, 24600, 24700])
        self.assertEqual([r['delta_km'] for r in rows], [0, 53, 7, 40, 100])

        after = app.summary()
        self.assertEqual(after['current_odometer'], 24700)
        self.assertEqual(after['latest_full_cycle']['km'], 100)
        self.assertEqual(after['latest_full_cycle']['liters'], 15)
        self.assertEqual(after['latest_full_cycle']['l100'], 15)

    def test_historical_conflict_requires_explicit_confirmation_and_preserves_current_odometer(self):
        self.seed()
        with app.db() as con:
            cur = con.execute(
                "INSERT INTO events(created_at,type,odometer) "
                "VALUES('2026-10-05T11:00:00+02:00','odometer',24763)"
            )
            previous_id = cur.lastrowid
        payload = self.fuel(odometer=24553, created_at='2026-10-05T11:30:00+02:00')
        current = app.summary()['current_odometer']

        with self.assertRaises(app.FuelKilometerConflict) as pending:
            app.add_fuel(payload)
        conflict = pending.exception
        self.assertEqual(conflict.conflicts[0]['direction'], 'eerdere')
        self.assertEqual(conflict.conflicts[0]['id'], previous_id)
        self.assertEqual(conflict.conflicts[0]['odometer'], 24763)
        self.assertIn('2026-10-05T11:00', conflict.conflicts[0]['created_at'])
        self.assertEqual(app.summary()['current_odometer'], current)
        self.assertFalse(any(r.get('kilometer_conflict') for r in app.rows_events()))

        with self.assertRaises(app.FuelKilometerConflict):
            app.add_fuel({**payload, 'confirm_kilometer_conflict': True,
                          'conflict_confirmation_key': 'outdated'})
        saved = app.add_fuel({**payload, 'confirm_kilometer_conflict': True,
                              'conflict_confirmation_key': conflict.confirmation_key})
        self.assertTrue(saved['kilometer_conflict'])
        self.assertEqual(app.summary()['current_odometer'], current)
        rows = app.rows_events()
        disputed = next(r for r in rows if r['id'] == saved['id'])
        self.assertEqual(disputed['kilometer_conflict'], 1)
        self.assertTrue(disputed['distance_unreliable'])
        self.assertEqual(disputed['delta_km'], 0)
        next_row = rows[rows.index(disputed) + 1]
        self.assertEqual(next_row['delta_km'], 0)
        self.assertIsNone(app.summary()['latest_full_cycle'])
        self.assertEqual(next(x for x in app.summary()['recent'] if x['id'] == saved['id'])['kilometer_conflict'], 1)
        with self.assertRaisesRegex(ValueError, 'bestaat al'):
            app.add_fuel({**payload, 'confirm_kilometer_conflict': True,
                          'conflict_confirmation_key': conflict.confirmation_key})

    def test_later_conflict_and_nonhistorical_conflict_remain_guarded(self):
        self.seed()
        payload = self.fuel(odometer=24601, created_at='2026-10-05T13:00:00+02:00')
        with self.assertRaises(app.FuelKilometerConflict) as pending:
            app.add_fuel(payload)
        self.assertEqual(pending.exception.conflicts[0]['direction'], 'latere')
        self.assertEqual(pending.exception.conflicts[0]['odometer'], 24600)
        with self.assertRaisesRegex(ValueError, 'lager dan de vorige registratie'):
            app.add_fuel(self.fuel(odometer=24400, created_at='2026-10-07T11:00:00+02:00'))

    def test_invalid_values_cannot_be_bypassed_by_confirmation(self):
        self.seed()
        payload = self.fuel()
        for bad in ({'odometer': float('nan')}, {'liters': float('inf')},
                    {'price_per_liter': float('nan')}, {'liters': 0},
                    {'price_per_liter': 11}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.add_fuel({**payload, **bad, 'confirm_kilometer_conflict': True,
                              'conflict_confirmation_key': 'arbitrary'})

    def test_current_fuel_entry_still_advances_current_odometer(self):
        self.seed()
        result = app.add_fuel(self.fuel(
            odometer=24720,
            created_at='2026-10-07T11:00:00+02:00',
        ))
        self.assertTrue(result['ok'])
        self.assertEqual(app.summary()['current_odometer'], 24720)

    def test_impossible_historical_odometer_and_duplicate_are_rejected(self):
        self.seed()
        with self.assertRaisesRegex(ValueError, 'lager dan de vorige registratie'):
            app.add_fuel(self.fuel(24499))
        with self.assertRaisesRegex(ValueError, 'hoger dan een latere registratie'):
            app.add_fuel(self.fuel(24601))

        payload = self.fuel()
        app.add_fuel(payload)
        before = [(r['id'], r['created_at'], r['odometer']) for r in app.rows_events()]
        with self.assertRaisesRegex(ValueError, 'bestaat al'):
            app.add_fuel(payload)
        after = [(r['id'], r['created_at'], r['odometer']) for r in app.rows_events()]
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
