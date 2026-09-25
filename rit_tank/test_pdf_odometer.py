"""Approved v3 table geometry, stored stop readings and fiscal addresses."""
import base64
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from test_v44 import app
from pdf_report import (
    ADDRESS_MAX_WIDTH, ADDRESS_X, ODOMETER_RIGHT, REIMBURSEMENT_RIGHT,
    _address_lines, _format_odometer, _full_address, _table_text_width,
    report_stop_address,
)

HOME = 'Verenlandweg 4, 7461 AP Rijssen'
DESTINATION = 'Nieuwlandsweg 1A, 7461 VP Rijssen'
LONG = 'Professor Kamerlingh Onnesstraat 120, 7553 JB Hengelo'


def fixture(count=10):
    return [
        (
            dict(status='completed', trip_type='business'),
            [
                dict(created_at=f'2026-09-{index+1:02d}T08:00:00+02:00',
                     odometer=63845 + index * 2, manual_label=HOME),
                dict(created_at=f'2026-09-{index+1:02d}T08:30:00+02:00',
                     odometer=63847 + index * 2, manual_label=DESTINATION,
                     segment_trip_type='business'),
            ],
        )
        for index in range(count)
    ]


def elements(markup, name):
    return ET.fromstring(markup).findall(f'{{http://www.w3.org/2000/svg}}{name}')


def find_text(markup, value):
    return [tag for tag in elements(markup, 'text') if tag.text == value]


class ReportAddressTests(unittest.TestCase):
    def setUp(self):
        self.place = Mock(return_value={'name': 'Thuis', 'address': HOME})
        self.cache = Mock(return_value=DESTINATION)
        self.deps = {'known_place_by_id': self.place, 'cached_report_address': self.cache}

    def address(self, **stop):
        return report_stop_address(stop, dependencies=self.deps)

    def test_stored_stop_address_has_first_priority(self):
        self.assertEqual(self.address(location_address=LONG, known_place_id=1), LONG)
        self.place.assert_not_called()
        self.cache.assert_not_called()

    def test_known_place_address_precedes_manual_and_cached_addresses(self):
        self.assertEqual(self.address(known_place_id=1, manual_label=LONG,
                                      latitude=52, longitude=6), HOME)
        self.cache.assert_not_called()

    def test_full_manual_address_precedes_cache(self):
        self.assertEqual(self.address(manual_label=LONG, latitude=52, longitude=6), LONG)
        self.cache.assert_not_called()

    def test_partial_manual_or_known_address_uses_complete_cache(self):
        self.place.return_value = {'name': 'Thuis', 'address': 'Verenlandweg 4'}
        self.assertEqual(self.address(known_place_id=1, manual_label='Thuis',
                                      latitude=52, longitude=6), DESTINATION)
        self.cache.assert_called_once_with(52.0, 6.0)

    def test_labels_partial_addresses_and_coordinates_never_pass(self):
        for label in ('Thuis', 'Ouders', 'Kantoor', 'School', 'Werk', 'Mijn magazijn',
                      'Verenlandweg 4', '7461 AP Rijssen', '52.123, 6.456'):
            with self.subTest(label=label):
                self.place.return_value = {'name': label, 'address': label}
                self.cache.return_value = label
                self.assertEqual(self.address(location_address=label, known_place_id=1,
                                              manual_label=label, location_label=label,
                                              latitude=52, longitude=6), 'Adres ontbreekt')

    def test_label_plus_address_is_never_printed_as_combination(self):
        for prefix in ('Thuis - ', 'Ouders \u2013 ', 'Mijn magazijn \u2014 '):
            self.assertEqual(self.address(location_address=prefix + HOME), HOME)
        self.assertEqual(self.address(location_label='Thuis - ' + HOME), 'Adres ontbreekt')

    def test_street_names_are_not_blacklisted(self):
        for value in ('Schoolstraat 4, 7461 AP Rijssen', 'Plein 1945 2, 1234 AB Zwolle',
                      'Rozengaarde 4-108, 7461DB Rijssen', 'Dorpsstraat 87a, 7468CG Enter'):
            self.assertEqual(_full_address(value), value)

    def test_old_destination_audit_address_is_not_a_current_address(self):
        self.assertEqual(self.address(original_destination_address=HOME), 'Adres ontbreekt')


class PdfOdometerTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = fixture()
        self.raw = patch.object(app, 'business_trips_raw', side_effect=lambda: deepcopy(self.fixtures))
        self.raw.start()
        self.addCleanup(self.raw.stop)
        for name, value in [('get_settings', {}), ('known_place_by_id', None),
                            ('cached_report_address', '')]:
            mock = patch.object(app, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        for name in ('google_reverse_geocode', 'google_place_details', 'rows_events'):
            mock = patch.object(app, name, side_effect=AssertionError('No network/event lookup during PDF'))
            mock.start()
            self.addCleanup(mock.stop)

    def export(self):
        return app.business_pdf('month', '2026', '9', preview=True)

    def test_original_pagination_and_table_positions_match_v3(self):
        pages = self.export()['pages']
        self.assertEqual(len(pages), 2)
        numbers = []
        for markup, count in zip(pages, (8, 2)):
            rows = [t for t in elements(markup, 'text') if t.get('x') == '38' and (t.text or '').isdigit()]
            self.assertEqual(len(rows), count)
            numbers.extend(t.text for t in rows)
            for label, x in [('#', 38), ('Datum', 56), ('Vertrek -> Aankomst (adres)', 157),
                             ('Tellerstand', 347)]:
                self.assertEqual(float(find_text(markup, label)[0].get('x')), x)
            reimbursement_x = float(find_text(markup, 'Vergoeding')[0].get('x'))
            self.assertAlmostEqual(
                reimbursement_x + _table_text_width('Vergoeding', bold=True),
                REIMBURSEMENT_RIGHT,
            )
            self.assertEqual(len(find_text(markup, 'Huisplan BV')), 1)
        self.assertEqual(numbers, [str(n) for n in range(1, 11)])
        self.assertAlmostEqual(float(find_text(pages[0], HOME)[0].get('y')), 443.87)
        self.assertAlmostEqual(float(find_text(pages[1], HOME)[0].get('y')), 169.87)

    def test_readings_use_same_baseline_and_actual_right_edge(self):
        self.fixtures = fixture(1)
        markup = self.export()['pages'][0]
        for address, reading in ((HOME, '63.845 km'), (DESTINATION, '63.847 km')):
            addr = find_text(markup, address)[0]
            odo = find_text(markup, reading)[0]
            self.assertEqual(addr.get('y'), odo.get('y'))
            self.assertEqual(odo.get('font-size'), '9.5')
            self.assertEqual(odo.get('font-weight'), '400')
            self.assertAlmostEqual(float(odo.get('x')) + _table_text_width(reading), 403)
        self.assertEqual(len(find_text(markup, '2,0 km')), 2)  # summary + leg

    def test_zero_and_missing_readings_reach_export_without_fabrication(self):
        self.fixtures = fixture(1)
        self.fixtures[0][1][0]['odometer'] = 0
        self.fixtures[0][1][1].pop('odometer')
        markup = self.export()['pages'][0]
        self.assertEqual(len(find_text(markup, '0 km')), 1)
        missing_y = find_text(markup, DESTINATION)[0].get('y')
        dash = next(t for t in find_text(markup, '\u2014') if t.get('y') == missing_y)
        self.assertEqual(float(dash.get('x')), ODOMETER_RIGHT - 9.5)
        # Unknown distance is also explicit, not silently converted into zero km.
        self.assertEqual(len(find_text(markup, '\u2014')), 2)

    def test_distance_is_rendered_from_segment_field_not_recomputed_in_pdf(self):
        self.fixtures = fixture(1)
        enriched = app.enrich_business_trip(*self.fixtures[0], resolve=False)
        enriched['stops'][1]['segment_km'] = 10
        with patch.object(app, 'enrich_business_trip', return_value=enriched):
            markup = self.export()['pages'][0]
        self.assertTrue(find_text(markup, '63.845 km'))
        self.assertTrue(find_text(markup, '63.847 km'))
        # Both the visible leg and its summary use the exported segment distance.
        self.assertEqual(len(find_text(markup, '10,0 km')), 2)

    def test_stop_address_survives_ui_enrichment_and_labels_become_missing(self):
        self.fixtures = fixture(1)
        start, end = self.fixtures[0][1]
        start.update(location_address=HOME, manual_label='Thuis')
        end.update(manual_label='Kantoor', location_label='Ouders')
        markup = self.export()['pages'][0]
        self.assertEqual(len(find_text(markup, HOME)), 1)
        self.assertEqual(len(find_text(markup, 'Adres ontbreekt')), 1)
        for label in ('Thuis', 'Ouders', 'Kantoor', 'School'):
            self.assertFalse(find_text(markup, label))

    def test_wrapped_addresses_keep_readings_aligned_and_rows_on_page(self):
        self.fixtures = fixture(24)
        for _, stops in self.fixtures:
            for stop in stops:
                stop['manual_label'] = LONG
        pages = self.export()['pages']
        self.assertGreater(len(pages), 2)
        row_count = 0
        for markup in pages:
            texts = elements(markup, 'text')
            address_texts = [t for t in texts if float(t.get('x')) == ADDRESS_X and t.get('font-weight') == '400']
            for tag in address_texts:
                self.assertLessEqual(_table_text_width(tag.text), ADDRESS_MAX_WIDTH)
                self.assertLess(float(tag.get('y')), 800)
            readings = [t for t in texts if (t.text or '').endswith(' km') and 347 <= float(t.get('x')) <= 403]
            self.assertTrue(readings)
            for reading in readings:
                self.assertTrue(any(a.get('y') == reading.get('y') for a in address_texts))
            row_count += len([t for t in texts if t.get('x') == '38' and (t.text or '').isdigit()])
            first_lines = [t for t in address_texts if t.text == _address_lines(LONG)[0]]
            last_lines = [t for t in address_texts if t.text == _address_lines(LONG)[-1]]
            self.assertEqual(len(first_lines), len(last_lines))
        self.assertEqual(row_count, 24)

    def test_private_and_mixed_labels_do_not_render_in_business_only_pdf(self):
        self.fixtures = fixture(3)
        self.fixtures[0][1][1]['segment_trip_type'] = 'private'
        self.fixtures[2][0].update(trip_type='mixed', private_detour_km=1)
        self.fixtures[2][1][1].pop('segment_trip_type')
        markup = self.export()['pages'][0]
        self.assertEqual(len(find_text(markup, 'Vergoeding')), 1)
        for label in ('Privé', 'Zakelijk', 'Privé/Zakelijk', 'Soort'):
            self.assertFalse(find_text(markup, label))
        rows = [t for t in elements(markup, 'text') if t.get('x') == '38' and (t.text or '').isdigit()]
        self.assertEqual([t.text for t in rows], ['1', '2'])

    def test_pdf_has_no_concept_or_stress_test_content(self):
        exported = self.export()
        binary = base64.b64decode(exported['pdf_base64'])
        visible_text = ' '.join(t.text or '' for page in exported['pages'] for t in elements(page, 'text'))
        for label in ('CONCEPT', 'fictief', 'T1', 'T2', 'T3', 'T4', 'stresstest'):
            self.assertNotIn(label, visible_text)
        self.assertTrue(binary.startswith(b'%PDF-1.4'))
        self.assertEqual(exported['filename'], 'rittenregistratie_2026-09.pdf')

    def test_oversized_address_or_reading_errors_instead_of_overlapping(self):
        self.fixtures = fixture(1)
        self.fixtures[0][1][0]['manual_label'] = 'Lange ' * 900 + 'Straat 1, 1234 AB Stad'
        with self.assertRaisesRegex(ValueError, 'ritadres'):
            self.export()
        self.fixtures = fixture(1)
        self.fixtures[0][1][1]['odometer'] = 123456789
        with self.assertRaisesRegex(ValueError, 'Tellerstand'):
            self.export()


class PdfTableMetricsTests(unittest.TestCase):
    def test_odometer_format_matches_whole_kilometer_ui(self):
        for value, expected in [(None, '\u2014'), ('', '\u2014'), (0, '0 km'), (63845, '63.845 km'),
                                (123456, '123.456 km'), (63844.5, '63.845 km')]:
            self.assertEqual(_format_odometer(value), expected)
        for invalid in (-1, float('nan'), float('inf'), 'invalid'):
            with self.assertRaises(ValueError):
                _format_odometer(invalid)

    def test_metrics_and_long_unbroken_street(self):
        self.assertAlmostEqual(_table_text_width('123.456 km'), 49.6375)
        self.assertEqual(_table_text_width('\u2014'), 9.5)
        value = 'W' * 150 + ' 1, 1234 AB Stad'
        lines = _address_lines(value)
        self.assertTrue(all(_table_text_width(line) <= ADDRESS_MAX_WIDTH for line in lines))
        self.assertEqual(''.join(lines).replace(' ', ''), value.replace(' ', ''))


if __name__ == '__main__':
    unittest.main()
