"""34.07: fictitious finance navigation and oil-level choice regressions."""
import sqlite3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import oil_history
import smart_insights


TZ = ZoneInfo("Europe/Amsterdam")


def dt(x):
    return datetime.fromisoformat(x).replace(tzinfo=TZ)


class FinanceNavigation3407Tests(unittest.TestCase):
    def trip(self, start, end, km_start, km_end, classification="business"):
        return ({"trip_type": classification}, [
            {"id": 1, "sequence_no": 1, "created_at": start, "odometer": km_start},
            {"id": 2, "sequence_no": 2, "created_at": end, "odometer": km_end},
        ])

    def test_navigate_previous_and_annual_from_first_registration(self):
        trips = [
            self.trip("2026-02-04T09:00:00", "2026-02-04T10:00:00", 1000, 1100),
            self.trip("2026-08-03T09:00:00", "2026-08-03T10:00:00", 1200, 1400),
        ]
        cycles = [{"km": 1000, "cost": 100}]
        overview = smart_insights.finance_overview(
            trips, cycles, .25, dt("2026-11-01T14:00:00"), "2026-02", dt)
        self.assertEqual(overview["navigation"]["selected_month"], "2026-02")
        self.assertEqual(overview["navigation"]["earliest_month"], "2026-02")
        self.assertFalse(overview["navigation"]["has_previous"])
        self.assertTrue(overview["navigation"]["has_next"])
        self.assertEqual(overview["monthly"]["business_km"], 100)
        self.assertEqual(overview["monthly"]["balance_after_fuel"], 15)
        self.assertEqual(overview["year"]["business_km"], 300)
        self.assertEqual(overview["year"]["balance_after_fuel"], 45)
        self.assertEqual(overview["year"]["year"], 2026)

    def test_future_month_and_malformed_month_rejected(self):
        trips = [self.trip("2026-07-01T10:00:00", "2026-07-01T11:00:00", 100, 200)]
        for requested in ("2026-12", "2026-00", "abc", "2026-02"):
            with self.subTest(requested=requested):
                x = smart_insights.finance_overview(trips, [], .25,
                      dt("2026-11-01T14:00:00"), requested, dt)
                self.assertEqual(x["navigation"]["selected_month"], "2026-11")
                self.assertIsNone(x["monthly"]["balance_after_fuel"])

    def test_calendar_boundary_prorates_once_and_annual_is_not_double_counted(self):
        trips = [self.trip("2026-10-31T23:00:00", "2026-11-01T01:00:00", 1000, 1100)]
        c = [{"km": 400, "cost": 40}]
        oct = smart_insights.finance_overview(trips, c, .25, dt("2026-11-01T14:00:00"), "2026-10", dt)
        nov = smart_insights.finance_overview(trips, c, .25, dt("2026-11-01T14:00:00"), "2026-11", dt)
        self.assertEqual(oct["monthly"]["business_km"], 50)
        self.assertEqual(nov["monthly"]["business_km"], 50)
        self.assertEqual(nov["year"]["business_km"], 100)
        self.assertEqual(nov["year"]["balance_after_fuel"], 15)

    def test_current_year_excludes_last_year_and_future_stops(self):
        trips = [
            self.trip("2025-11-01T10:00:00", "2025-11-01T11:00:00", 1000, 1100),
            self.trip("2026-10-20T10:00:00", "2026-10-20T11:00:00", 1100, 1200),
            self.trip("2026-11-10T10:00:00", "2026-11-10T11:00:00", 1200, 1300),
        ]
        result = smart_insights.finance_overview(trips, [{"km": 100, "cost": 10}],
                 .25, dt("2026-11-01T14:00:00"), "2026-10", dt)
        self.assertEqual(result["year"]["business_km"], 100)
        self.assertEqual(result["year"]["balance_after_fuel"], 15)
        self.assertEqual(result["navigation"]["earliest_month"], "2025-11")


class OilChoice3407Tests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

    def test_old_checks_preserved_during_repeated_additive_migration(self):
        self.db.execute("""CREATE TABLE oil_level_checks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_key TEXT DEFAULT 'primary',
            checked_on TEXT, odometer INTEGER, note TEXT, created_at TEXT)""")
        self.db.execute("INSERT INTO oil_level_checks(id, checked_on, odometer, note, created_at) "
                        "VALUES (7, '2026-10-01', 12345, 'Oude eigen notitie', '2026-10-01')")
        oil_history.init_schema(self.db)
        oil_history.init_schema(self.db)
        old = oil_history.list_checks(self.db)[0]
        self.assertEqual(old["id"], 7)
        self.assertEqual(old["note"], "Oude eigen notitie")
        self.assertEqual(old["level_status"], "unspecified")
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_standard_options_saved_without_touching_vehicle_data(self):
        self.db.execute("CREATE TABLE events(id INTEGER, odometer INTEGER)")
        self.db.execute("INSERT INTO events VALUES(1, 12345)")
        oil_history.init_schema(self.db)
        for status in oil_history.OIL_LEVEL_CHOICES:
            row = oil_history.add_check(self.db, {
                "checked_on": "2026-10-30", "odometer": 12345,
                "level_status": status, "note": "Thuis gecontroleerd"
            }, "2026-10-30T12:00:00")
            self.assertEqual(row["level_status"], status)
        self.assertEqual(self.db.execute("SELECT odometer FROM events").fetchone()[0], 12345)
        self.assertEqual(len(oil_history.list_entries(self.db)), 0)
        self.assertEqual(len(oil_history.list_checks(self.db)), len(oil_history.OIL_LEVEL_CHOICES))

    def test_unsafe_peil_keeps_warning_until_normal_check(self):
        oil = [{"id": 1, "kind": "top_up", "performed_on": "2026-10-20",
                "amount_ml": 200, "odometer": 10000}]
        bad = [{"id": 2, "checked_on": "2026-10-31", "odometer": 10020,
                "level_status": "below_min"}]
        due = smart_insights.oil_guidance(oil, bad, 10021, dt("2026-11-01T12:00:00").date())
        self.assertTrue(due["reminder"])
        self.assertTrue(due["level_attention"])
        good = bad + [{"id": 3, "checked_on": "2026-11-01", "odometer": 10021,
                      "level_status": "normal"}]
        new = smart_insights.oil_guidance(oil, good, 10021, dt("2026-11-01T12:00:00").date())
        self.assertFalse(new["reminder"])
        self.assertFalse(new["level_attention"])

    def test_missing_or_unknown_choice_cannot_be_saved(self):
        oil_history.init_schema(self.db)
        for value in (None, "", "magic"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                oil_history.add_check(self.db, {
                  "checked_on": "2026-10-30", "odometer": 12345,
                  "note": "", "level_status": value
                }, "2026-10-30T12:00:00")
        self.assertEqual(len(oil_history.list_checks(self.db)), 0)


if __name__ == "__main__":
    unittest.main()
