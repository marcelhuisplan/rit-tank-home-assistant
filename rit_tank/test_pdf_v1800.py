"""Release 18.00 PDF regression tests: one row per leg and uniform type badges."""
import re
import unittest
from unittest.mock import patch

from test_v44 import app
from pdf_report import (
    ADDRESS_MAX_WIDTH,
    ADDRESS_X,
    ODOMETER_X,
    BADGE_HEIGHT,
    BADGE_LABELS,
    BADGE_RADIUS,
    BADGE_RIGHT_PADDING,
    BADGE_TEXT_OFFSET,
    BADGE_TEXT_SIZE,
    BADGE_WIDTH,
    BADGE_X,
    _SimplePdfPage,
    _table_text_width,
)

MULTI_STOP = (
    dict(purpose='Drie stops', status='completed'),
    [
        dict(created_at='2026-03-02T15:36:00+01:00', odometer=63925,
             manual_label='Verenlandweg 4, 7461 AP Rijssen'),
        dict(created_at='2026-03-02T15:41:00+01:00', odometer=63927,
             manual_label='Nieuwlandsweg 1A, 7461 VP Rijssen', segment_trip_type='business'),
        dict(created_at='2026-03-02T15:45:00+01:00', odometer=63929,
             manual_label='Verenlandweg 4, 7461 AP Rijssen', segment_trip_type='private'),
    ],
)

TWO_STOP = (
    dict(purpose='Twee stops', status='completed'),
    [
        dict(created_at='2026-03-03T09:00:00+01:00', odometer=70000,
             manual_label='Startstraat 1, 7461 AA Rijssen'),
        dict(created_at='2026-03-03T09:30:00+01:00', odometer=70010,
             manual_label='Eindstraat 2, 7461 BB Rijssen', segment_trip_type='business'),
    ],
)

UNCLASSIFIED_MIXED = (
    dict(purpose='Gemengd segment', status='completed', trip_type='mixed', private_detour_km=4),
    [
        dict(created_at='2026-03-04T08:00:00+01:00', odometer=80000,
             manual_label='Grijsstraat 1, 7461 CC Rijssen'),
        dict(created_at='2026-03-04T08:40:00+01:00', odometer=80010,
             manual_label='Grijsstraat 9, 7461 CC Rijssen'),
    ],
)


def _texts(markup):
    return re.findall(r'<text[^>]*>(.*?)</text>', markup)


class PdfLegExportTests(unittest.TestCase):
    def _install(self, fixtures):
        for name, value in [('get_settings', {}), ('business_trips_raw', list(fixtures)),
                            ('known_place_by_id', None)]:
            mock = patch.object(app, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        for name in ['google_reverse_geocode', 'google_place_details']:
            mock = patch.object(app, name, side_effect=AssertionError('PDF must not use network'))
            mock.start()
            self.addCleanup(mock.stop)

    def _pages(self, fixtures):
        self._install(fixtures)
        return app.business_pdf('month', '2026', '3', preview=True)['pages']

    def _row_texts(self, fixtures):
        texts = _texts(self._pages(fixtures)[0])
        return texts[texts.index('Afstand') + 1:]

    def test_multi_stop_trip_exports_every_leg(self):
        rows = self._row_texts([MULTI_STOP])
        self.assertIn('Nieuwlandsweg 1A, 7461 VP Rijssen', rows)
        # Two legs: A -> B and B -> C, numbered 1 and 2.
        self.assertEqual(rows.count('15:36 \u2013 15:41'), 1)
        self.assertEqual(rows.count('15:41 \u2013 15:45'), 1)
        # No collapsed A -> C row.
        self.assertNotIn('15:36 \u2013 15:45', rows)
        self.assertEqual(rows.count('2,0 km'), 2)
        self.assertNotIn('4,0 km', rows)
        self.assertEqual(rows.count('Zakelijk'), 1)
        self.assertEqual(rows.count('Privé'), 1)
        self.assertNotIn('Privé/Zakelijk', rows)

    def test_two_stop_trip_stays_one_row(self):
        rows = self._row_texts([TWO_STOP])
        self.assertEqual(rows.count('09:00 \u2013 09:30'), 1)
        self.assertEqual(rows.count('10,0 km'), 1)

    def test_leg_split_does_not_double_count_totals(self):
        texts = _texts(self._pages([MULTI_STOP])[0])
        summary = texts[texts.index('TOTAAL'):]
        self.assertEqual(summary[1], '4,0 km')
        self.assertEqual(summary[summary.index('ZAKELIJK') + 1], '2,0 km')
        self.assertEqual(summary[summary.index('PRIVÉ') + 1], '2,0 km')

    def test_unclassified_leg_uses_mixed_badge(self):
        texts = _texts(self._pages([UNCLASSIFIED_MIXED])[0])
        self.assertIn('Privé/Zakelijk', texts)

    def test_leg_numbering_is_continuous(self):
        page = self._pages([MULTI_STOP, TWO_STOP])[0]
        texts = _texts(page)
        start = texts.index('Afstand')
        numbers = [t for t in texts[start:] if t in {'1', '2', '3', '4'}]
        self.assertEqual(numbers[:3], ['1', '2', '3'])

    def test_pagination_keeps_rows_inside_the_page(self):
        trip, stops = MULTI_STOP
        fixtures = [(dict(trip), [dict(s) for s in stops]) for _ in range(12)]
        self._install(fixtures)
        pages = app.business_pdf('month', '2026', '3', preview=True)['pages']
        self.assertGreater(len(pages), 1)
        numbers = []
        for markup in pages:
            texts = _texts(markup)
            self.assertIn('Afstand', texts)
            self.assertEqual(texts.count('Huisplan BV'), 1)
            numbers += [t for t in texts[texts.index('Afstand'):] if t.isdigit() and len(t) <= 2]
            for tag in re.findall(r'<line [^>]*/>', markup):
                y1 = float(re.search(r'y1="([-\d.]+)"', tag).group(1))
                self.assertLessEqual(y1, 842 - 34)
        self.assertEqual(numbers, [str(n) for n in range(1, 25)])
        self.assertEqual(_texts(pages[-1]).count('Pagina %d van %d' % (len(pages), len(pages))), 1)


class PdfBadgeGeometryTests(unittest.TestCase):
    def test_badge_width_fits_longest_label(self):
        longest = max(_SimplePdfPage.text_width(label, BADGE_TEXT_SIZE) for label in BADGE_LABELS)
        self.assertGreaterEqual(BADGE_WIDTH, BADGE_TEXT_OFFSET + longest + BADGE_RIGHT_PADDING)

    def test_mixed_label_stays_inside_badge_with_padding(self):
        text_end = BADGE_X + BADGE_TEXT_OFFSET + _SimplePdfPage.text_width('Privé/Zakelijk', BADGE_TEXT_SIZE)
        self.assertLessEqual(text_end, BADGE_X + BADGE_WIDTH - BADGE_RIGHT_PADDING + 0.05)

    def test_badge_does_not_overlap_distance_column(self):
        widest_km = _table_text_width('1234,5 km', 9.5, bold=True)
        self.assertLess(BADGE_X + BADGE_WIDTH, 557 - widest_km)

    def test_address_column_stops_before_badge(self):
        self.assertLess(ADDRESS_X + ADDRESS_MAX_WIDTH, ODOMETER_X)

    def test_all_badges_share_identical_geometry(self):
        fixtures = [MULTI_STOP, UNCLASSIFIED_MIXED]
        for name, value in [('get_settings', {}), ('business_trips_raw', fixtures),
                            ('known_place_by_id', None)]:
            mock = patch.object(app, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        pages = app.business_pdf('month', '2026', '3', preview=True)['pages']
        badges = []
        for markup in pages:
            for tag in re.findall(r'<rect [^>]*rx="[^"]*"[^>]*/>', markup):
                attrs = {k: float(v) for k, v in re.findall(r'(x|width|height|rx)="([-\d.]+)"', tag)}
                if abs(attrs['width'] - BADGE_WIDTH) < 0.05:
                    badges.append(attrs)
        self.assertGreaterEqual(len(badges), 3)
        self.assertEqual({b['width'] for b in badges}, {BADGE_WIDTH})
        self.assertEqual({b['height'] for b in badges}, {BADGE_HEIGHT})
        self.assertEqual({b['rx'] for b in badges}, {BADGE_RADIUS})
        self.assertEqual({b['x'] for b in badges}, {BADGE_X})


if __name__ == '__main__':
    unittest.main()
