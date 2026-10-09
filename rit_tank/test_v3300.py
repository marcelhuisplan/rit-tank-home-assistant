"""Release 33 regressions for simplified physical-odometer mobile registration."""
import csv
import io
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from test_v2300 import app
from test_v3000 import Request
import odometer_control as control

HOME = 'Verenlandweg 4, 7461 AP Rijssen'
SCHOOL = 'Van Broekhuizenstraat 4, 7461 VW Rijssen'


class SimplifiedMobile3300Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fixed_now = datetime.fromisoformat('2026-10-07T13:00:00+02:00')
        for name, value in {
            'DATA_DIR': root,
            'DB_PATH': root / 'test.db',
            'OPTIONS_PATH': root / 'options.json',
            'RECEIPT_DIR': root / 'receipts',
            'publish_sensors_async': lambda: None,
            'now_local': lambda: fixed_now,
            'google_reverse_geocode': lambda *a: {},
            'google_place_details': lambda *a: {},
        }.items():
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()
        app.set_settings({'km_reimbursement_rate': '0.25'})

    def point(self, odometer, created_at, address, lat, lon):
        return {
            'odometer': odometer,
            'created_at': created_at,
            'latitude': lat,
            'longitude': lon,
            'manual_label': address,
            'physical_confirmed': True,
            'segment_trip_type': 'business',
        }

    def test_01_release_and_mobile_contract_present(self):
        self.assertEqual(app.APP_VERSION, '33.08')
        html = app.APP_HTML
        for text in [
            'id="physicalTripValue"', 'inputmode="numeric"',
            '✓ Startstand bevestigen', 'GPS-informatie bekijken',
            '🏠 Thuis', '🏫 Beatrixschool', '📍 Gebruik huidige locatie',
            'Rit vastgelegd!', 'Nieuwe rit registreren', 'Naar overzicht',
        ]:
            self.assertIn(text, html)
        self.assertIn('id="physicalTripConfirmed" type="checkbox" hidden', html)
        self.assertNotIn('Ik heb deze fysieke tellerstand gecontroleerd.', html)

    def test_02_dutch_whole_kilometer_input(self):
        self.assertEqual(control.physical_odometer('25.230'), 25230)
        self.assertEqual(control.physical_odometer('25230'), 25230)
        for invalid in ['', '25,230', '25230.5', '25.23.0', '-1', '1e5']:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    control.physical_odometer(invalid)

    def test_03_physical_stops_define_16_25_and_41_km_once(self):
        start = self.point(25230, '2026-10-07T08:00:00+02:00', HOME, 52.315, 6.528)
        middle = self.point(25246, '2026-10-07T09:00:00+02:00', SCHOOL, 52.307, 6.519)
        end = self.point(25271, '2026-10-07T10:00:00+02:00', HOME, 52.315, 6.528)

        trip = app.start_business_trip(start)['trip']
        middle['trip_id'] = trip['id']
        end['trip_id'] = trip['id']
        first = app.add_business_stop(middle)['trip']
        self.assertEqual(first['km'], 16)
        completed = app.add_business_stop(end, finish=True)['trip']

        self.assertEqual(completed['km'], 41)
        self.assertEqual(completed['business_km'], 41)
        self.assertEqual([s['segment_km'] for s in completed['stops']], [0.0, 16.0, 25.0])
        self.assertEqual([s['odometer'] for s in completed['stops']], [25230.0, 25246.0, 25271.0])

        report = app.business_report('all')
        self.assertEqual([r['km'] for r in report['rows']], [16.0, 25.0])
        self.assertEqual(sum((r['km'] for r in report['rows'])), 41)
        self.assertEqual(sum((r['reimbursement'] for r in report['rows']), Decimal('0')), Decimal('10.25'))
        self.assertEqual(app.business_report_validation(report)['status'], 'ok')

        pdf, _ = app.business_pdf(report=report)
        self.assertIn(b'Van Broekhuizenstraat 4, 7461 VW', pdf)

        h = Request('/api/business.csv?period=all')
        h.do_GET()
        self.assertEqual(h.status, 200)
        rows = list(csv.DictReader(io.StringIO(h.wfile.getvalue().decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(sum(float(r['segment_km']) for r in rows), 41)
        self.assertEqual({r['reimbursement_eur'] for r in rows}, {'10.25'})

    def test_04_end_below_last_confirmed_stop_rejected_without_write(self):
        start = self.point(25230, '2026-10-07T08:00:00+02:00', HOME, 52.315, 6.528)
        middle = self.point(25246, '2026-10-07T09:00:00+02:00', SCHOOL, 52.307, 6.519)
        trip = app.start_business_trip(start)['trip']
        middle['trip_id'] = trip['id']
        app.add_business_stop(middle)
        before = app.business_trip_by_id(trip['id'])
        bad = self.point(25245, '2026-10-07T10:00:00+02:00', HOME, 52.315, 6.528)
        bad['trip_id'] = trip['id']
        with self.assertRaisesRegex(ValueError, 'lager dan de vorige (?:stop|registratie)'):
            app.add_business_stop(bad, finish=True)
        after = app.business_trip_by_id(trip['id'])
        self.assertEqual([s['odometer'] for s in after['stops']], [s['odometer'] for s in before['stops']])
        self.assertEqual(after['status'], 'active')

    def test_05_existing_addresses_and_report_priority_remain_unchanged(self):
        self.assertEqual(app.HOME_ADDRESS, HOME)
        self.assertEqual(app.BEATRIXSCHOOL_ADDRESS, SCHOOL)
        self.assertIn('Verenlandweg 10', Path(__file__).with_name('test_v2900.py').read_text(encoding='utf-8'))

    def test_06_version_files_are_consistent(self):
        root = Path(__file__).parent
        self.assertIn("version: '33.08'", (root / 'config.yaml').read_text(encoding='utf-8'))
        self.assertTrue((root / 'README.md').read_text(encoding='utf-8').startswith('# Rit & Tank 33.08'))
        self.assertTrue((root / 'DOCS.md').read_text(encoding='utf-8').startswith('# Rit & Tank 33.08'))
        self.assertTrue((root / 'CHANGELOG.md').read_text(encoding='utf-8').startswith('# Changelog\n\n## 33.08'))


if __name__ == '__main__':
    unittest.main()
