"""Fictitious and isolated regression cases for the 34.06 oil/finance cards."""
import sqlite3
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import oil_history
import smart_insights


class SmartInsightsTests(unittest.TestCase):
    def test_no_oil_history_does_not_predict(self):
        x = smart_insights.oil_guidance([], [], 10000, date(2026, 11, 1))
        self.assertFalse(x['reminder'])
        self.assertIsNone(x['consumption_l_per_1000'])
        self.assertEqual(x['status'], 'no_history')

    def test_one_topup_requires_more_measurements(self):
        oil = [{'id': 1, 'kind': 'top_up', 'performed_on': '2026-10-01',
                'amount_ml': 300, 'odometer': 10000}]
        x = smart_insights.oil_guidance(oil, [], 10400, date(2026, 10, 15))
        self.assertFalse(x['learned'])
        self.assertEqual(x['interval_km'], 1500)
        self.assertIsNone(x['consumption_l_per_1000'])
        self.assertFalse(x['reminder'])

    def test_learns_from_distance_days_and_volume_and_can_be_due(self):
        oil = [
            {'id': 1, 'kind': 'top_up', 'performed_on': '2026-09-01', 'amount_ml': 300, 'odometer': 10000},
            {'id': 2, 'kind': 'top_up', 'performed_on': '2026-09-22', 'amount_ml': 300, 'odometer': 11500},
            {'id': 3, 'kind': 'top_up', 'performed_on': '2026-10-13', 'amount_ml': 400, 'odometer': 13000},
        ]
        x = smart_insights.oil_guidance(oil, [], 14100, date(2026, 10, 25))
        self.assertTrue(x['learned'])
        self.assertAlmostEqual(x['consumption_l_per_1000'], .233, places=3)
        self.assertTrue(x['reminder'])
        self.assertEqual(x['intervals'], 2)

    def test_oil_change_not_consumption_and_check_resets_reminder(self):
        oil = [
            {'id': 1, 'kind': 'top_up', 'performed_on': '2026-08-01', 'amount_ml': 300, 'odometer': 10000},
            {'id': 2, 'kind': 'top_up', 'performed_on': '2026-09-01', 'amount_ml': 300, 'odometer': 11500},
            {'id': 3, 'kind': 'change', 'performed_on': '2026-10-01', 'amount_ml': 5000, 'odometer': 13000},
        ]
        checks = [{'id': 1, 'checked_on': '2026-10-20', 'odometer': 14100}]
        x = smart_insights.oil_guidance(oil, checks, 14150, date(2026, 10, 21))
        self.assertFalse(x['learned'])
        self.assertIsNone(x['consumption_l_per_1000'])
        self.assertEqual(x['next_km'], 15600)
        self.assertFalse(x['reminder'])

    def test_check_schema_and_isolation_from_existing_tables(self):
        c = sqlite3.connect(':memory:')
        self.addCleanup(c.close)
        c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE events (id INTEGER PRIMARY KEY, odometer INTEGER)')
        c.execute('INSERT INTO events VALUES (1, 11000)')
        oil_history.init_schema(c)
        row = oil_history.add_check(c, {'checked_on': '2026-10-28', 'odometer': 10900,
                                        'note': 'Peil goed'}, '2026-10-28T10:00:00')
        oil_history.init_schema(c)
        self.assertEqual(len(oil_history.list_checks(c)), 1)
        self.assertEqual(row['note'], 'Peil goed')
        self.assertEqual(c.execute('SELECT odometer FROM events').fetchone()[0], 11000)
        self.assertEqual(oil_history.list_entries(c), [])

    def test_financial_month_missing_fuel_cycle_is_unknown_not_zero(self):
        tz = ZoneInfo('Europe/Amsterdam')
        parse = lambda s: datetime.fromisoformat(s).replace(tzinfo=tz)
        start = parse('2026-11-01T00:00:00')
        end = parse('2026-12-01T00:00:00')
        trip = {'trip_type': 'business'}
        stops = [{'id': 1, 'sequence_no': 1, 'created_at': '2026-11-02T09:00:00', 'odometer': 10000},
                 {'id': 2, 'sequence_no': 2, 'created_at': '2026-11-02T10:00:00', 'odometer': 10100}]
        result = smart_insights.monthly_finances([(trip, stops)], [], .25, start, end, parse)
        self.assertEqual(result['business_km'], 100)
        self.assertEqual(result['gross_reimbursement'], 25)
        self.assertIsNone(result['balance_after_fuel'])

    def test_financial_allocates_business_only_and_splits_boundary(self):
        tz = ZoneInfo('Europe/Amsterdam')
        parse = lambda s: datetime.fromisoformat(s).replace(tzinfo=tz)
        start = parse('2026-11-01T00:00:00')
        end = parse('2026-12-01T00:00:00')
        stops = [
            {'id': 1, 'sequence_no': 1, 'created_at': '2026-10-31T23:00:00', 'odometer': 10000},
            {'id': 2, 'sequence_no': 2, 'created_at': '2026-11-01T01:00:00', 'odometer': 10100,
             'segment_trip_type': 'business'},
            {'id': 3, 'sequence_no': 3, 'created_at': '2026-11-01T02:00:00', 'odometer': 10200,
             'segment_trip_type': 'private'},
        ]
        cycles = [{'km': 400, 'cost': 40, 'liters': 20}]
        x = smart_insights.monthly_finances([({'trip_type': 'mixed'}, stops)],
                                              cycles, .25, start, end, parse)
        self.assertEqual(x['business_km'], 50)
        self.assertEqual(x['gross_reimbursement'], 12.5)
        self.assertEqual(x['allocated_fuel_cost'], 5)
        self.assertEqual(x['balance_after_fuel'], 7.5)
        self.assertTrue(x['prorated_month_boundary'])


if __name__ == '__main__':
    unittest.main()
