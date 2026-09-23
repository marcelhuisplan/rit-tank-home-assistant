"""Release 20.00 PDF metadata table spacing regressions."""
import re
import unittest
from unittest.mock import patch

from test_v44 import app


KNOWN_PLACES = {
    1: {'id': 1, 'name': 'Thuis', 'address': 'Verenlandweg 4, 7461 AP Rijssen'},
    2: {'id': 2, 'name': 'Ouders', 'address': 'Ouderstraat 12, 1234 AB Zwolle'},
}


def _fixture(count=2):
    trips = []
    for index in range(count):
        base = 90000 + index * 10
        stops = [
            dict(created_at=f'2026-03-{index + 1:02d}T08:00:00+01:00', odometer=base,
                 known_place_id=1, latitude=52.31554, longitude=6.52766),
            dict(created_at=f'2026-03-{index + 1:02d}T08:30:00+01:00', odometer=base + 10,
                 known_place_id=2, latitude=52.0, longitude=6.0,
                 segment_trip_type='business'),
        ]
        trips.append((dict(purpose='Familiebezoek', status='completed', trip_type='business'), stops))
    return trips


def _attrs(tag, keys=('x', 'y', 'x1', 'y1', 'x2', 'y2', 'width', 'height')):
    pattern = r'(' + '|'.join(keys) + r')="([-\d.]+)"'
    return {key: float(value) for key, value in re.findall(pattern, tag)}


class PdfMetadataTablePaddingTests(unittest.TestCase):
    """Correction round: only the internal vertical padding of the 6 metadata
    cells changed. Header, cards, table and footer must stay exactly as before."""

    def setUp(self):
        settings = {
            'driver_name': 'Marcel van der Boomgaarden-Huisplan',
            'vehicle_make': 'Volkswagen',
            'vehicle_model': 'Transporter T6.1 Lang Hoog Dubbelcabine',
            'license_plate': 'AB-123-Z',
        }
        patches = [
            patch.object(app, 'get_settings', return_value=settings),
            patch.object(app, 'business_trips_raw', return_value=_fixture()),
            patch.object(app, 'known_place_by_id',
                         side_effect=lambda place_id: KNOWN_PLACES.get(place_id)),
            patch.object(app, 'cached_report_address', return_value=''),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def _table_dividers(self, markup):
        lines = sorted((
            _attrs(tag) for tag in re.findall(r'<line [^>]*/>', markup)
            if _attrs(tag).get('x1') == 36 and _attrs(tag).get('x2') == 559
            and _attrs(tag).get('y1', 0) < 300
        ), key=lambda a: a['y1'])
        # lines[0] is the header rule; the next four are the 3-row table dividers.
        return lines[1:5]

    def test_icon_has_at_least_eight_points_of_top_padding(self):
        markup = app.business_pdf('month', '2026', '3', preview=True)['pages'][0]
        dividers = self._table_dividers(markup)
        row_top = dividers[0]['y1']
        icon_bars = [
            _attrs(tag) for tag in re.findall(r'<rect [^>]*/>', markup)
            if _attrs(tag).get('height') == 1.6
        ]
        self.assertTrue(icon_bars, 'expected the calendar icon glyph to be present')
        topmost_icon_y = min(bar['y'] for bar in icon_bars)
        self.assertGreaterEqual(topmost_icon_y - row_top, 8.0)

    def test_label_has_clear_gap_below_the_row_top(self):
        markup = app.business_pdf('month', '2026', '3', preview=True)['pages'][0]
        dividers = self._table_dividers(markup)
        row_top = dividers[0]['y1']
        label = _attrs(re.search(r'<text [^>]*>KILOMETERVERGOEDING</text>', markup).group(0))
        self.assertGreaterEqual(label['y'] - row_top, 8.0)

    def test_report_period_third_line_stays_above_row_divider(self):
        markup = app.business_pdf('month', '2026', '3', preview=True)['pages'][0]
        dividers = self._table_dividers(markup)
        row0_bottom = dividers[1]['y1']
        range_line = _attrs(re.search(r'<text [^>]*>[^<]*t/m[^<]*</text>', markup).group(0))
        self.assertGreaterEqual(row0_bottom - range_line['y'], 8.0)

    def test_wrapped_two_line_value_stays_above_its_row_divider(self):
        markup = app.business_pdf('month', '2026', '3', preview=True)['pages'][0]
        dividers = self._table_dividers(markup)
        row1_bottom = dividers[2]['y1']
        wrapped_lines = [
            _attrs(tag) for tag in re.findall(r'<text [^>]*>[^<]*</text>', markup)
            if 'Dubbelcabine' in tag
        ]
        self.assertTrue(wrapped_lines, 'expected the wrapped vehicle name to appear')
        for line in wrapped_lines:
            self.assertGreaterEqual(row1_bottom - line['y'], 8.0)

    def test_all_six_fields_share_identical_icon_and_label_offsets(self):
        """All 6 cells must use the same top offset for icon and label (no per-field tuning)."""
        markup = app.business_pdf('month', '2026', '3', preview=True)['pages'][0]
        dividers = self._table_dividers(markup)
        row_tops = [dividers[0]['y1'], dividers[1]['y1'], dividers[2]['y1']]
        labels = ['KILOMETERVERGOEDING', 'RAPPORTPERIODE', 'BESTUURDER', 'PRIVÉAUTO', 'KENTEKEN', 'GEGENEREERD OP']
        offsets = []
        for idx, label_text in enumerate(labels):
            row_top = row_tops[idx // 2]
            tag = re.search(rf'<text [^>]*>{re.escape(label_text)}</text>', markup).group(0)
            offsets.append(_attrs(tag)['y'] - row_top)
        for offset in offsets[1:]:
            self.assertAlmostEqual(offsets[0], offset, places=6)

    def test_header_and_logo_position_are_unchanged(self):
        """Regression guard: the metadata-table padding fix must not move the header."""
        markup = app.business_pdf('month', '2026', '3', preview=True)['pages'][0]
        title = _attrs(re.search(r'<text [^>]*>Zakelijke kilometerdeclaratie</text>', markup).group(0))
        subtitle = _attrs(re.search(
            r'<text [^>]*>Overzicht van zakelijke kilometers met privéauto</text>', markup).group(0))
        logo = _attrs(re.search(r'<image [^>]*/>', markup).group(0))
        # These are release 19.00's approved header coordinates (unchanged in this release).
        self.assertAlmostEqual(title['y'], 70.8661417322835, places=3)
        self.assertAlmostEqual(subtitle['y'], 88.8661417322835, places=3)
        self.assertAlmostEqual(logo['y'], 68.59725284339461, places=3)
        self.assertAlmostEqual(logo['width'] / logo['height'], 900 / 827, places=3)


if __name__ == '__main__':
    unittest.main()
