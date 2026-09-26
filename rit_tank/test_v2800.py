"""Google trip corrections: offline provider, storage, audit and real export gates."""
import json
import subprocess
import unittest
from unittest.mock import patch
import test_v2700
from test_v2700 import app, ADDRESS, OTHER, ROOT


def google_result(address=ADDRESS, place_id='chosen'):
    return {'place_id': place_id, 'address': address, 'latitude': 52.3, 'longitude': 6.5,
            'address_components': [{'longText': kind, 'types': [kind]} for kind in
                                   ['route', 'street_number', 'postal_code', 'locality']]}


class Correction2800Tests(unittest.TestCase):
    setUp = test_v2700.Export2700Tests.setUp
    add_trip = test_v2700.Export2700Tests.add_trip
    get = test_v2700.Export2700Tests.get

    def snapshot(self):
        with app.db() as con:
            return {table: [dict(r) for r in con.execute('SELECT * FROM '+table)]
                    for table in ['business_trips', 'trip_stops', 'events', 'audit_log']}

    def correct(self, stop_id=1, address=OTHER, place_id='chosen'):
        return app.edit_business_trip(1, {'stops': [{'id': stop_id, 'address': address, 'place_id': place_id}]})

    def test_unchanged_full_address_exactly_preserved_without_google(self):
        before = self.snapshot()
        with patch.object(app, 'google_place_details', side_effect=AssertionError('unnecessary Google')):
            app.edit_business_trip(1, {'purpose': 'new purpose', 'stops': []})
        self.assertEqual(before['trip_stops'], self.snapshot()['trip_stops'])

    def test_free_text_including_full_address_rejected_atomically(self):
        for text in ['Wierdens', 'Rijssen', 'Dorpsstraat', 'Verenlandweg', OTHER, '']:
            with self.subTest(text=text):
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, 'Selecteer'):
                    app.edit_business_trip(1, {'note': 'must roll back', 'stops': [{'id': 1, 'address': text}]})
                self.assertEqual(self.snapshot(), before)

    def test_departure_arrival_and_intermediate_selection_and_audit(self):
        with app.db() as con:
            con.execute("INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label,segment_trip_type) VALUES(1,2,'2026-09-01T10:00:00+02:00',1020,'Wierdens','business')")
        for sid in [1, 2, 3]:
            with self.subTest(stop=sid):
                before = self.snapshot()
                report_before = app.business_report_validation(app.business_report('all'))['summary']
                with patch.object(app, 'google_place_details', return_value=google_result(OTHER)) as detail:
                    self.correct(sid)
                    detail.assert_called_once_with('chosen')
                after = self.snapshot()
                saved = next(s for s in after['trip_stops'] if s['id'] == sid)
                self.assertEqual((saved['manual_label'], saved['place_id'], saved['location_source']), (OTHER, 'chosen', 'google_selected'))
                for old, new in zip(before['trip_stops'], after['trip_stops']):
                    for key in ['odometer', 'created_at', 'event_id', 'segment_trip_type']:
                        self.assertEqual(old[key], new[key])
                self.assertEqual(before['events'], after['events'])
                summary = app.business_report_validation(app.business_report('all'))['summary']
                for key in ['business_km', 'reimbursement', 'km_reimbursement_rate']:
                    self.assertEqual(report_before[key], summary[key])
                audit = json.loads(after['audit_log'][-1]['details'])
                self.assertEqual(audit['stops_before'], before['trip_stops'])
                self.assertEqual(audit['stops_after'], after['trip_stops'])

    def test_address_only_keeps_existing_parent_dates_and_linked_events(self):
        with app.db() as con:
            event = con.execute("INSERT INTO events(created_at,type,odometer) VALUES('2026-09-01T07:59:00+02:00','odometer',999)").lastrowid
            con.execute('UPDATE trip_stops SET event_id=? WHERE id=1', (event,))
            con.execute("UPDATE business_trips SET started_at='2026-09-01T07:59:00+02:00'")
        before = self.snapshot()
        with patch.object(app, 'google_place_details', return_value=google_result(OTHER)):
            self.correct()
        after = self.snapshot()
        self.assertEqual(before['events'], after['events'])
        for key in ['started_at', 'ended_at', 'trip_type', 'private_detour_km']:
            self.assertEqual(before['business_trips'][0][key], after['business_trips'][0][key])

    def test_provider_http_failure_keeps_original(self):
        app.google_places._PLACE_CACHE.clear()
        before = self.snapshot()
        with patch.object(app, 'load_options', return_value={'google_places_api_key': 'existing-test-key'}), \
             patch.object(app, 'http_json', side_effect=TimeoutError('Google timeout')):
            with self.assertRaisesRegex(ValueError, 'Probeer later opnieuw'):
                self.correct()
        self.assertEqual(before, self.snapshot())

    def test_other_selected_result_wins_exactly(self):
        with patch.object(app, 'google_place_details', return_value=google_result(OTHER, 'other')):
            self.correct(place_id='other')
        self.assertEqual(self.snapshot()['trip_stops'][0]['manual_label'], OTHER)

    def test_forged_or_incomplete_google_result_rejected(self):
        results = [google_result(ADDRESS), google_result(OTHER, 'different-id'),
                   {**google_result(OTHER), 'address_components': []}]
        for result in results:
            before = self.snapshot()
            with patch.object(app, 'google_place_details', return_value=result), self.assertRaises(ValueError):
                self.correct()
            self.assertEqual(self.snapshot(), before)

    def test_google_failure_rolls_back_all_stops_and_metadata(self):
        before = self.snapshot()
        with patch.object(app, 'google_place_details', side_effect=[google_result(OTHER), None]):
            with self.assertRaisesRegex(ValueError, 'niet gewijzigd'):
                app.edit_business_trip(1, {'note': 'not saved', 'stops': [
                    {'id': 1, 'address': OTHER, 'place_id': 'chosen'},
                    {'id': 2, 'address': OTHER, 'place_id': 'failed'}]})
        self.assertEqual(self.snapshot(), before)

    def test_historical_incomplete_text_visible_no_geocoding(self):
        with app.db() as con: con.execute("UPDATE trip_stops SET manual_label='Wierdens' WHERE id=1")
        with patch.object(app, 'google_place_details', side_effect=AssertionError('network')):
            data = json.loads(self.get('/api/business/1/edit').wfile.getvalue())
        self.assertEqual(data['stops'][0]['edit_address'], 'Wierdens')
        with patch.object(app, 'google_place_details', return_value=google_result(OTHER)):
            self.correct()
        self.assertEqual(self.snapshot()['trip_stops'][0]['manual_label'], OTHER)

    def test_pdf_preview_pdf_csv_validation_use_selected_address(self):
        with patch.object(app, 'google_place_details', return_value=google_result(OTHER)):
            self.correct()
        with patch.object(app, 'google_place_details', side_effect=AssertionError('export network')):
            for path in ['/api/business.csv', '/api/business.pdf', '/api/business/pdf-preview', '/api/export/pdf']:
                response = self.get(path)
                self.assertEqual(response.status, 200)
                self.assertIn(OTHER.encode(), response.wfile.getvalue())
            report = app.business_report('all')
            self.assertEqual(report['rows'][0]['origin']['report_address'], OTHER)
            self.assertEqual(app.business_report_validation(report)['status'], 'ok')

    def test_existing_google_helpers_with_mocked_http(self):
        app.google_places._PLACE_CACHE.clear()
        data = {'id': 'chosen', 'formattedAddress': OTHER,
                'addressComponents': google_result()['address_components'],
                'location': {'latitude': 52.3, 'longitude': 6.5}}
        with patch.object(app, 'load_options', return_value={'google_places_api_key': 'existing-test-key'}), \
             patch.object(app, 'http_json', return_value=data) as http:
            self.correct()
            self.assertIn('/places/chosen?languageCode=nl&regionCode=NL', http.call_args.args[0])
            self.assertEqual(http.call_args.kwargs['headers']['X-Goog-Api-Key'], 'existing-test-key')
            self.assertIn('addressComponents', http.call_args.kwargs['headers']['X-Goog-FieldMask'])
        app.google_places._PLACE_CACHE.clear()
        with patch.object(app, 'load_options', return_value={'google_places_api_key': 'existing-test-key'}), \
             patch.object(app, 'http_json', return_value={'places': [data]}) as http:
            self.assertEqual(app.google_places_text_search('Stationsstraat')[0]['address'], OTHER)
            self.assertEqual(http.call_args.kwargs['payload']['regionCode'], 'NL')
            self.assertEqual(http.call_args.kwargs['payload']['languageCode'], 'nl')

    def test_ui_actual_frontend_logic(self):
        result = subprocess.run(['node', str(ROOT/'test_ui_v2800.cjs')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__': unittest.main()
