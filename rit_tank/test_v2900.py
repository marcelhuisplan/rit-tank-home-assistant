"""Stored stop addresses win over nearby known places in every export."""
import base64
import csv
import io
import json
import unittest
from xml.etree import ElementTree
from decimal import Decimal
from unittest.mock import patch

import test_v2700
import test_v2800
from test_v2800 import app, google_result

A = 'Verenlandweg 4, 7461 AP Rijssen'
B = 'Verenlandweg 10, 7461 AP Rijssen'
C = 'Stationsstraat 1, 7461 AA Rijssen'


class Address2900Tests(unittest.TestCase):
    add_trip = test_v2700.Export2700Tests.add_trip
    get = test_v2700.Export2700Tests.get
    snapshot = test_v2800.Correction2800Tests.snapshot

    def setUp(self):
        test_v2700.Export2700Tests.setUp(self)
        with app.db() as con:
            con.execute("UPDATE business_trips SET started_at='2026-09-26T19:43:00+02:00', ended_at='2026-09-26T20:43:00+02:00', purpose='Afspraak', client='Project'")
            con.execute("INSERT INTO known_places(id,name,category,latitude,longitude,address,created_at,updated_at) VALUES(1,'Thuis','home',52.3,6.5,?,'2026-09-26','2026-09-26')", (A,))
            # A nearby known-place association can coexist with the selected address:
            # validate_trip_point uses match_known_place independently of manual_label.
            for sid, time, odo, address in [(1, '19:43', 64600, A), (2, '20:43', 64603, B)]:
                con.execute('UPDATE trip_stops SET created_at=?,odometer=?,manual_label=?,known_place_id=1,latitude=52.3,longitude=6.5 WHERE id=?',
                            ('2026-09-26T'+time+':00+02:00', odo, address, sid))
        for name in ['google_place_details', 'google_reverse_geocode']:
            p = patch.object(app, name, side_effect=AssertionError('unexpected network'))
            p.start(); self.addCleanup(p.stop)

    def assert_addresses(self, addresses):
        before = self.snapshot()
        editor = json.loads(self.get('/api/business/1/edit').wfile.getvalue())
        self.assertEqual([s['edit_address'] for s in editor['stops']], addresses)
        report = app.business_report('all')
        self.assertEqual([s['report_address'] for s in report['trips'][0]['stops']], addresses)
        self.assertEqual([(r['origin']['report_address'], r['destination']['report_address']) for r in report['rows']],
                         list(zip(addresses, addresses[1:])))
        self.assertEqual(app.business_report_validation(report)['status'], 'ok')
        for path in ['/api/business.pdf', '/api/export/pdf', '/api/business/pdf-preview']:
            response = self.get(path)
            self.assertEqual(response.status, 200)
            data = response.wfile.getvalue()
            if path.endswith('pdf-preview'):
                preview = json.loads(data)
                data = base64.b64decode(preview['pdf_base64'])
                for address in addresses:
                    self.assertIn(address, ''.join(preview['pages']))
            # Assert text actually emitted in the PDF stream, not only report input.
            for address in addresses:
                self.assertIn(address.encode(), data)
            self.assertIn(b'64.600 km', data)
            self.assertIn(b'64.603 km', data)
        response = self.get('/api/business.csv')
        self.assertEqual(response.status, 200)
        rows = list(csv.DictReader(io.StringIO(response.wfile.getvalue().decode('utf-8-sig')), delimiter=';'))
        self.assertEqual([r['locatie_label_live'] for r in rows], addresses)
        self.assertEqual(self.snapshot(), before, 'Exports must not rewrite historical data')
        return report

    def test_exact_example_known_home_must_not_replace_arrival(self):
        report = self.assert_addresses([A, B])
        self.assertEqual(report['rows'][0]['km'], 3)
        self.assertEqual(report['rows'][0]['reimbursement'], Decimal('1.05'))

    def test_compact_postcode_is_preserved(self):
        compact = B.replace('7461 AP', '7461AP')
        with app.db() as con:
            con.execute('UPDATE trip_stops SET manual_label=? WHERE id=2', (compact,))
        self.assert_addresses([A, compact])

    def test_three_stops_keep_intermediate_legs(self):
        with app.db() as con:
            con.execute("UPDATE trip_stops SET created_at='2026-09-26T20:13:00+02:00',odometer=64601 WHERE id=2")
            con.execute("INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label,known_place_id,segment_trip_type) VALUES(1,2,'2026-09-26T20:43:00+02:00',64603,?,1,'business')", (C,))
        report = self.assert_addresses([A, B, C])
        self.assertEqual(report['trips'][0]['report_origin']['report_address'], A)
        self.assertEqual(report['trips'][0]['report_destination']['report_address'], C)
        preview = json.loads(self.get('/api/business/pdf-preview').wfile.getvalue())
        text = ' '.join(' '.join(ElementTree.fromstring(page).itertext()) for page in preview['pages'])
        self.assertIn(f'Rit: {A} -> {C}', text)
        self.assertEqual(report['rows'][0]['origin']['report_address'], A)
        self.assertEqual(report['rows'][-1]['destination']['report_address'], C)
        self.assertEqual(sum(r['km'] for r in report['rows']), 3)

    def test_correct_only_departure_or_arrival_preserves_fiscal_data(self):
        for sid, addresses in [(1, [C, B]), (2, [A, C])]:
            with self.subTest(stop=sid):
                with app.db() as con:
                    con.execute('UPDATE trip_stops SET manual_label=? WHERE id=1', (A,))
                    con.execute('UPDATE trip_stops SET manual_label=? WHERE id=2', (B,))
                before = self.snapshot()
                with patch.object(app, 'google_place_details', return_value=google_result(C)):
                    app.edit_business_trip(1, {'stops': [{'id': sid, 'address': C, 'place_id': 'chosen'}]})
                after = self.snapshot()
                for old, new in zip(before['trip_stops'], after['trip_stops']):
                    for key in ['odometer', 'created_at', 'event_id', 'segment_trip_type']:
                        self.assertEqual(old[key], new[key])
                    if old['id'] != sid:
                        self.assertEqual(old, new)
                for key, value in before['business_trips'][0].items():
                    if key != 'modified_at':
                        self.assertEqual(after['business_trips'][0][key], value)
                self.assertEqual(before['events'], after['events'])
                audit = json.loads(after['audit_log'][-1]['details'])
                self.assertEqual(audit['stops_before'], before['trip_stops'])
                self.assertEqual(audit['stops_after'], after['trip_stops'])
                report = self.assert_addresses(addresses)
                self.assertEqual(report['rows'][0]['km'], 3)
                self.assertEqual(report['rows'][0]['reimbursement'], Decimal('1.05'))

    def test_google_failure_preserves_entire_trip(self):
        before = self.snapshot()
        with patch.object(app, 'google_place_details', return_value=None):
            with self.assertRaises(ValueError):
                app.edit_business_trip(1, {'stops': [{'id': 2, 'address': C, 'place_id': 'chosen'}]})
        self.assertEqual(self.snapshot(), before)
        self.assert_addresses([A, B])

    def test_stored_address_wins_over_stale_enriched_address(self):
        address = app.pdf_report.report_stop_address(
            {'manual_label': B, 'location_address': A, 'known_place_id': 1},
            dependencies=app._report_dependencies())
        self.assertEqual(address, B)


if __name__ == '__main__':
    unittest.main()
