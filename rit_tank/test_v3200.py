"""Release 32 regressions for the Beatrixschool fixed-address shortcut."""
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

SCHOOL = 'Van Broekhuizenstraat 4, 7461 VW Rijssen'
HOME = 'Verenlandweg 4, 7461 AP Rijssen'


class Beatrixschool3200Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fixed_now = datetime.fromisoformat('2026-10-07T12:00:00+02:00')
        for name, value in {
            'DATA_DIR': root, 'DB_PATH': root / 'test.db', 'OPTIONS_PATH': root / 'options.json',
            'RECEIPT_DIR': root / 'receipts', 'publish_sensors_async': lambda: None,
            'now_local': lambda: fixed_now,
            'google_reverse_geocode': lambda *a: {},
            'google_place_details': lambda *a: {},
        }.items():
            p = patch.object(app, name, value); p.start(); self.addCleanup(p.stop)
        app.init_db()
        app.set_settings({'km_reimbursement_rate': '0.25'})

    def test_01_release_and_canonical_school_address(self):
        self.assertEqual(app.APP_VERSION, '33.03')
        self.assertEqual(app.BEATRIXSCHOOL_NAME, 'Beatrixschool Rijssen')
        self.assertEqual(app.BEATRIXSCHOOL_ADDRESS, SCHOOL)

    def test_02_school_reuses_exact_known_place_without_writing(self):
        with app.db() as con:
            con.execute(
                'INSERT INTO known_places(name,category,latitude,longitude,address,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (app.BEATRIXSCHOOL_NAME, 'other', 52.307, 6.519, SCHOOL, app.iso_local(), app.iso_local()),
            )
        with patch.object(app, 'google_places_text_search') as search:
            result = app.beatrixschool_destination()
        search.assert_not_called()
        self.assertEqual(result['address'], SCHOOL)
        self.assertEqual((result['latitude'], result['longitude']), (52.307, 6.519))
        self.assertEqual(result['source'], 'known_place')
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM known_places').fetchone()[0], 1)

    def test_03_school_uses_existing_places_fallback_without_saving(self):
        place = {'place_id': 'school-place', 'name': app.BEATRIXSCHOOL_NAME,
                 'address': SCHOOL + ', Nederland', 'latitude': 52.307, 'longitude': 6.519}
        with patch.object(app, 'google_places_text_search', return_value=[place]) as search:
            result = app.beatrixschool_destination()
        search.assert_called_once_with(SCHOOL)
        self.assertEqual(result['address'], SCHOOL)
        self.assertEqual(result['place_id'], 'school-place')
        self.assertIsNone(result['known_place_id'])
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM known_places').fetchone()[0], 0)

    def test_04_fixed_resolver_rejects_wrong_school_address(self):
        candidates = [
            {'address': 'Van Broekhuizenstraat 40, 7461 VW Rijssen', 'latitude': 52.307, 'longitude': 6.519},
            {'address': SCHOOL, 'latitude': 999, 'longitude': 6.519},
        ]
        with patch.object(app, 'google_places_text_search', return_value=candidates):
            with self.assertRaisesRegex(ValueError, 'niet met geldige coördinaten'):
                app.beatrixschool_destination()

    def test_05_school_endpoint_returns_same_canonical_selection(self):
        result = {'address': SCHOOL, 'latitude': 52.307, 'longitude': 6.519,
                  'place_id': 'school-place', 'known_place_id': None, 'source': 'google_places'}
        with patch.object(app, 'beatrixschool_destination', return_value=result):
            h = Request('/api/places/beatrixschool'); h.do_GET()
        self.assertEqual(h.status, 200)
        self.assertEqual(h.json(), result)

    def test_06_start_stop_finish_store_selected_addresses_and_keep_financial_rules(self):
        start = {'odometer': 10000, 'created_at': '2026-10-07T08:00:00+02:00',
                 'latitude': 52.315, 'longitude': 6.528, 'manual_label': HOME,
                 'physical_confirmed': True}
        middle = {'odometer': 10010, 'created_at': '2026-10-07T09:00:00+02:00',
                  'latitude': 52.307, 'longitude': 6.519, 'manual_label': SCHOOL,
                  'physical_confirmed': True, 'segment_trip_type': 'business'}
        end = {'odometer': 10020, 'created_at': '2026-10-07T10:00:00+02:00',
               'latitude': 52.315, 'longitude': 6.528, 'manual_label': HOME,
               'physical_confirmed': True, 'segment_trip_type': 'business'}
        trip = app.start_business_trip(start)['trip']
        middle['trip_id'] = trip['id']; end['trip_id'] = trip['id']
        app.add_business_stop(middle)
        completed = app.add_business_stop(end, finish=True)['trip']

        self.assertEqual(completed['km'], 20)
        self.assertEqual(completed['business_km'], 20)
        self.assertEqual(completed['private_km'], 0)
        self.assertEqual(completed['trip_type'], 'business')
        with app.db() as con:
            stops = [dict(r) for r in con.execute(
                'SELECT sequence_no,odometer,manual_label,segment_trip_type FROM trip_stops ORDER BY sequence_no')]
        self.assertEqual([s['manual_label'] for s in stops], [HOME, SCHOOL, HOME])
        self.assertEqual([s['odometer'] for s in stops], [10000, 10010, 10020])
        self.assertEqual([s['segment_trip_type'] for s in stops], [None, 'business', 'business'])

        report = app.business_report('all')
        addresses = [s['report_address'] for s in report['trips'][0]['stops']]
        self.assertEqual(addresses, [HOME, SCHOOL, HOME])
        self.assertEqual([r['km'] for r in report['rows']], [10, 10])
        self.assertEqual(sum((r['reimbursement'] for r in report['rows']), Decimal('0')), Decimal('5.00'))
        self.assertEqual(app.business_report_validation(report)['status'], 'ok')

        recent = app.recent_business_trips('year', 5)
        self.assertTrue(recent)
        self.assertEqual([s['location_label'] for s in recent[0]['stops']], [HOME, SCHOOL, HOME])

        pdf, _ = app.business_pdf(report=report)
        self.assertIn(b'Van Broekhuizenstraat 4, 7461 VW', pdf)
        self.assertIn(b'Rijssen', pdf)

        h = Request('/api/business.csv?period=all'); h.do_GET()
        self.assertEqual(h.status, 200)
        rows = list(csv.DictReader(io.StringIO(h.wfile.getvalue().decode('utf-8-sig')), delimiter=';'))
        self.assertIn(SCHOOL, [r['locatie_label_live'] for r in rows])
        self.assertEqual(sum(float(r['segment_km']) for r in rows), 20)
        self.assertEqual({r['reimbursement_eur'] for r in rows}, {'5.00'})

    def test_07_report_address_still_prefers_confirmed_school_over_known_place(self):
        address = app.pdf_report.report_stop_address(
            {'manual_label': SCHOOL, 'location_address': HOME, 'known_place_id': 1},
            dependencies=app._report_dependencies())
        self.assertEqual(address, SCHOOL)


class Beatrixschool3200UiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = app.APP_HTML.decode('utf-8') if isinstance(app.APP_HTML, bytes) else app.APP_HTML

    def test_08_buttons_and_shared_modes_are_present(self):
        for text in ['id="tripHomeButton"', '🏠 Thuis', 'id="tripSchoolButton"', '🏫 Beatrixschool',
                     'id="tripCurrentLocationButton"', '📍 Gebruik huidige locatie',
                     "openTripPoint('start')", "openTripPoint(\\'stop\\')", "openTripPoint(\\'finish\\')"]:
            self.assertIn(text, self.html)

    def test_09_school_selection_only_selects_and_uses_shared_confirmation(self):
        start = self.html.index('async function selectTripFixedLocation(')
        end = self.html.index("$('tripManualAddress').addEventListener", start)
        source = self.html[start:end]
        self.assertIn("api/places/beatrixschool", source)
        self.assertIn(SCHOOL, source)
        self.assertIn('confirmTripAddress', source)
        for forbidden in ['saveTripPoint()', 'api/business/start', 'api/business/stop', 'api/business/finish']:
            self.assertNotIn(forbidden, source)

    def test_10_mobile_layout_is_two_fixed_buttons_plus_full_width_gps(self):
        self.assertIn('grid-template-columns:minmax(0,1fr) minmax(0,1fr)', self.html)
        self.assertIn('.trip-location-actions .location-current{grid-column:1/-1}', self.html)
        self.assertIn('overflow-wrap:normal', self.html)


if __name__ == '__main__':
    unittest.main()
