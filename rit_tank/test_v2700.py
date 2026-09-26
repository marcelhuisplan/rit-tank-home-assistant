"""Release 27: offline declaration validation and real HTTP export gates."""
import copy
import io
import json
import tempfile
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from test_v2300 import app
from report_validation import validate_business_report
from test_pdf_v2600 import DISTANCES, GROUPS

ROOT = Path(__file__).parent
ADDRESS = 'Verenlandweg 4, 7461 AP Rijssen'
OTHER = 'Stationsstraat 1, 7461 AA Rijssen'


def row(index=1, km=10, start=1000, end=None):
    date = datetime(2026, 9, index, 8)
    return {'trip_id': index, 'segment_number': 1, 'km': km,
            'reimbursement': app.pdf_report.calculate_km_reimbursement(km, Decimal('0.35')),
            'origin': {'id': index * 2, 'report_address': ADDRESS, 'odometer': start,
                       'created_at': date.isoformat()},
            'destination': {'id': index * 2 + 1, 'report_address': ADDRESS,
                            'odometer': start + km if end is None else end,
                            'created_at': (date + timedelta(hours=1)).isoformat()}}


class Validation2700Tests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js niet beschikbaar')
    def test_ui_flow_without_browser(self):
        result = subprocess.run(['node', str(ROOT / 'test_ui_v2700.cjs')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def codes(self, rows):
        return [i['code'] for i in validate_business_report(rows)['issues']]

    def test_good_month_and_no_required_purpose(self):
        result = validate_business_report([row(i) for i in range(1, 11)])
        self.assertEqual((result['status'], result['errors'], result['warnings'], result['clean_rows']), ('ok', 0, 0, 10))

    def test_fifteen_visible_rows_371_km(self):
        result = validate_business_report([row(i, km) for i, km in enumerate(DISTANCES, 1)])
        self.assertEqual((result['row_count'], result['business_km'], result['errors'], result['warnings']), (15, 371, 0, 2))
        self.assertEqual(result['clean_rows'], 13)

    def test_missing_addresses_and_internal_label(self):
        for side in ['origin', 'destination']:
            for value in [None, '', 'Adres ontbreekt', 'Thuis', 'Dorpsstraat']:
                with self.subTest(side=side, value=value):
                    r = row(); r[side]['report_address'] = value
                    self.assertEqual(validate_business_report([r])['errors'], 1)
        r = row(); r['origin']['known_place_name'] = 'Thuis'
        self.assertEqual(validate_business_report([r])['status'], 'ok')

    def test_missing_invalid_and_reversed_odometer(self):
        for side in ['origin', 'destination']:
            for value in [None, '', 'NaN', 'Infinity', -1]:
                r = row(); r[side]['odometer'] = value
                self.assertEqual(validate_business_report([r])['errors'], 1)
        self.assertIn('odometer_reversed', self.codes([row(start=64300, end=64290)]))

    def test_separate_business_trip_gaps_are_allowed(self):
        self.assertEqual(self.codes([row(start=64290, end=64300), row(2, start=64315, end=64325)]), [])

    def test_zero_distance_different_and_same_addresses(self):
        r = row(km=0)
        self.assertEqual(self.codes([r]), ['zero_distance_same_address'])
        r['destination']['report_address'] = OTHER
        self.assertEqual(self.codes([r]), ['zero_distance_different_addresses'])
        self.assertEqual(validate_business_report([r])['errors'], 0)

    def test_conservative_distance_thresholds(self):
        self.assertEqual(self.codes([row(km=10, end=1025)]), ['distance_mismatch'])
        self.assertEqual(self.codes([row(km=9, end=1010)]), [])
        self.assertEqual(self.codes([row(km=100, end=1103)]), [])
        self.assertEqual(self.codes([row(km=9, end=1012)]), ['distance_mismatch'])

    def test_times_reversed_zero_extreme_and_missing(self):
        r = row(); r['destination']['created_at'] = '2026-09-01T07:00:00'
        self.assertEqual(self.codes([r]), ['time_reversed'])
        r['destination']['created_at'] = r['origin']['created_at']
        self.assertEqual(self.codes([r]), ['zero_duration'])
        r['destination']['created_at'] = '2026-09-01T08:01:00'
        self.assertEqual(self.codes([r]), ['extreme_speed'])
        for value in [None, '', 'invalid', '2026-09-01T08:01:00+02:00']:
            r['destination']['created_at'] = value
            self.assertEqual(self.codes([r]), [])

    def test_duplicates_require_all_strong_fields(self):
        first = row(); second = copy.deepcopy(first); second['trip_id'] = 2
        self.assertEqual(self.codes([first, second]), ['possible_duplicate'])
        second['origin']['created_at'] = '2026-09-02T08:00:00'
        second['destination']['created_at'] = '2026-09-02T09:00:00'
        self.assertEqual(self.codes([first, second]), [])

    def test_issue_identification_and_read_only(self):
        r = row(); r['origin']['report_address'] = ''
        original = copy.deepcopy(r); issue = validate_business_report([r])['issues'][0]
        for field in ['severity', 'code', 'row_number', 'trip_id', 'segment_number', 'stop_id', 'title', 'explanation', 'fixable']:
            self.assertIn(field, issue)
        self.assertEqual(r, original)
        self.assertEqual((issue['trip_id'], issue['stop_id']), (1, 2))


class CaptureHandler:
    export_business_pdf = app.Handler.export_business_pdf
    export_business_csv = app.Handler.export_business_csv
    def __init__(self, path, query=None):
        self.path = path; self.query = query or {}; self.wfile = io.BytesIO(); self.status = None
    def _path(self): return self.path, self.query
    def _authorize_api(self): return True
    def send_response(self, status): self.status = status
    def send_header(self, key, value): pass
    def end_headers(self): pass


class Export2700Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=ROOT); self.addCleanup(tmp.cleanup)
        for key, value in {'DATA_DIR': Path(tmp.name), 'DB_PATH': Path(tmp.name)/'test.db',
                           'OPTIONS_PATH': Path(tmp.name)/'options.json', 'publish_sensors_async': lambda: None}.items():
            p = patch.object(app, key, value); p.start(); self.addCleanup(p.stop)
        app.init_db(); app.set_settings({'km_reimbursement_rate': '0.35'})
        self.add_trip()

    def add_trip(self, day=1, distances=(10,), start=1000, trip_type='business'):
        with app.db() as con:
            date = f'2026-09-{day:02d}'
            tid = con.execute("INSERT INTO business_trips(started_at,ended_at,status,trip_type) VALUES(?,?,'completed',?)",
                              (date+'T08:00:00+02:00', date+'T10:00:00+02:00', trip_type)).lastrowid
            odo = start
            for seq, distance in enumerate([0]+list(distances)):
                odo += distance
                con.execute('INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label,segment_trip_type) VALUES(?,?,?,?,?,?)',
                            (tid, seq, date+f'T{8+seq:02d}:00:00+02:00', odo, ADDRESS, 'business'))
        return tid

    def get(self, path, allow=False, **query):
        q = {'period': ['month'], 'year': ['2026'], 'month': ['9'], **{k: [str(v)] for k,v in query.items()}}
        if allow: q['allow_warnings'] = ['true']
        h = CaptureHandler(path, q)
        app.Handler.do_GET(h)
        return h

    def assert_exports(self, expected, allow=False):
        for path in ['/api/business.pdf', '/api/business.csv', '/api/business/pdf-preview', '/api/export/pdf']:
            with self.subTest(path=path, allow=allow):
                h = self.get(path, allow)
                self.assertEqual(h.status, expected, h.wfile.getvalue()[:250])

    def test_clean_exports_and_validation(self):
        self.assert_exports(200)
        data = json.loads(self.get('/api/business/validate').wfile.getvalue())
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['summary']['reimbursement'], '3.50')

    def test_errors_block_all_endpoints_even_with_override_then_correction(self):
        with app.db() as con:
            con.execute("UPDATE trip_stops SET manual_label='' WHERE sequence_no=0")
        self.assert_exports(422); self.assert_exports(422, True)
        edit = json.loads(self.get('/api/business/1/edit').wfile.getvalue())
        stop = edit['stops'][0]
        selected = {'place_id': 'chosen', 'address': ADDRESS, 'address_components': [{'longText': k, 'types': [k]} for k in ['route', 'street_number', 'postal_code', 'locality']]}
        with patch.object(app, 'google_place_details', return_value=selected):
            app.edit_business_trip(1, {'stops': [{'id': stop['id'], 'address': ADDRESS, 'place_id': 'chosen'}]})
        self.assert_exports(200)

    def test_warnings_require_exact_explicit_override(self):
        with app.db() as con:
            con.execute('UPDATE trip_stops SET odometer=1000')
        self.assert_exports(409); self.assert_exports(200, True)
        for override in ['false', '1', 'TRUE']:
            self.assertEqual(self.get('/api/business.pdf', allow_warnings=override).status, 409)

    def test_real_fifteen_rows_ten_parents_and_fresh_rate(self):
        with app.db() as con:
            con.execute('DELETE FROM trip_stops'); con.execute('DELETE FROM business_trips')
        for i, legs in enumerate(GROUPS, 1): self.add_trip(i, legs)
        data = json.loads(self.get('/api/business/validate').wfile.getvalue())
        self.assertEqual(data['summary'], {'row_count': 15, 'business_km': 371, 'errors': 0,
                         'warnings': 2, 'clean_rows': 13, 'reimbursement': '129.85', 'km_reimbursement_rate': '0.35'})
        self.assert_exports(409); self.assert_exports(200, True)
        app.set_settings({'km_reimbursement_rate': '0.25'})
        data = json.loads(self.get('/api/business/validate').wfile.getvalue())
        self.assertEqual(data['summary']['reimbursement'], '92.75')

    def test_export_rechecks_changes_after_validation(self):
        self.assertEqual(self.get('/api/business/validate').status, 200)
        with app.db() as con: con.execute("UPDATE trip_stops SET manual_label='' WHERE sequence_no=1")
        self.assert_exports(422, True)

    def test_missing_odometer_in_export_rows_blocks_backend(self):
        stored = app.business_trips_raw()
        stored[0][1][-1]['odometer'] = None
        with patch.object(app, 'business_trips_raw', return_value=stored):
            self.assert_exports(422, True)

    def test_departure_month_empty_month_and_single_stop(self):
        with app.db() as con:
            con.execute("UPDATE trip_stops SET created_at='2026-10-01T09:00:00+02:00' WHERE sequence_no=1")
        self.assertEqual(json.loads(self.get('/api/business/validate').wfile.getvalue())['summary']['row_count'], 1)
        self.assertEqual(json.loads(self.get('/api/business/validate', month=10).wfile.getvalue())['summary']['row_count'], 0)
        with app.db() as con: con.execute('DELETE FROM trip_stops WHERE sequence_no=1')
        self.assert_exports(409); self.assert_exports(200, True)
        with app.db() as con: con.execute('DELETE FROM trip_stops')
        self.assert_exports(200)

    def test_periods_private_filter_no_external_calls(self):
        self.add_trip(2, trip_type='private')
        with patch.object(app, 'google_reverse_geocode', side_effect=AssertionError('network')), \
             patch.object(app, 'google_place_details', side_effect=AssertionError('network')), \
             patch.object(app, 'get_route_distance', side_effect=AssertionError('network')):
            for period in ['month', 'year']:
                h = self.get('/api/business/validate', period=period)
                self.assertEqual(json.loads(h.wfile.getvalue())['summary']['row_count'], 1)
                self.assertEqual(self.get('/api/business.csv', period=period).status, 200)
            self.assertEqual(json.loads(self.get('/api/business/validate', month=8).wfile.getvalue())['summary']['row_count'], 0)
            self.assert_exports(200)
        self.assertEqual(self.get('/api/business/validate', year=0).status, 400)
        self.assertEqual(self.get('/api/business.csv', month=13).status, 400)

    def test_known_place_home_resolves_and_correction_wins(self):
        with app.db() as con:
            kp = con.execute('INSERT INTO known_places(name,address,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                             ('Thuis', ADDRESS, 52, 6, '2026-09-01', '2026-09-01')).lastrowid
            con.execute("UPDATE trip_stops SET known_place_id=?,manual_label='Thuis'", (kp,))
        self.assert_exports(200)
        selected = {'place_id': 'chosen', 'address': OTHER, 'address_components': [{'longText': k, 'types': [k]} for k in ['route', 'street_number', 'postal_code', 'locality']]}
        with patch.object(app, 'google_place_details', return_value=selected):
            app.edit_business_trip(1, {'stops': [{'id': 1, 'address': OTHER, 'place_id': 'chosen'}]})
        self.assertEqual(app.business_report('all')['rows'][0]['origin']['report_address'], OTHER)

    def test_correction_odometer_time_audit_and_linked_event(self):
        with app.db() as con:
            event = con.execute("INSERT INTO events(created_at,type,odometer) VALUES('2026-09-01T09:00:00+02:00','odometer',1010)").lastrowid
            con.execute('UPDATE trip_stops SET event_id=?,odometer=990 WHERE id=2', (event,))
        self.assert_exports(422)
        app.edit_business_trip(1, {'stops': [{'id': 2, 'odometer': 1020, 'created_at': '2026-09-01T10:00:00+02:00'}]})
        self.assert_exports(200)
        self.assertEqual(app.business_report_validation(app.business_report('all'))['summary']['business_km'], 20)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT odometer FROM events WHERE id=?', (event,)).fetchone()[0], 1020)
            self.assertIn('stops_before', con.execute("SELECT details FROM audit_log WHERE action='update' ORDER BY id DESC").fetchone()[0])

    def test_cross_trip_stop_correction_rejected_atomically(self):
        self.add_trip(2)
        with self.assertRaises(ValueError): app.edit_business_trip(1, {'note': 'must roll back', 'stops': [{'id': 3, 'odometer': 9}]})
        with app.db() as con: self.assertIsNone(con.execute('SELECT note FROM business_trips WHERE id=1').fetchone()[0])

    def test_all_selected_rows_older_than_dashboard_limit(self):
        for i in range(2, 21): self.add_trip(i)
        data = json.loads(self.get('/api/business/validate').wfile.getvalue())
        self.assertEqual(data['summary']['row_count'], 20)
        self.assertEqual(self.get('/api/business/1/edit').status, 200)


if __name__ == '__main__':
    unittest.main()
