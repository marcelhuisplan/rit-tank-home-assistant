"""Release 19.00 PDF address and header regressions."""
import re
import unittest
from unittest.mock import patch

from test_v44 import app


HOME_ADDRESS = 'Verenlandweg 4, 7461 AP Rijssen'
PARENTS_ADDRESS = 'Ouderstraat 12, 1234 AB Zwolle'
KNOWN_PLACES = {
    1: {'id': 1, 'name': 'Thuis', 'address': HOME_ADDRESS},
    2: {'id': 2, 'name': 'Ouders', 'address': PARENTS_ADDRESS},
}


def _fixture(count=12):
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


def _attrs(tag):
    return {key: float(value) for key, value in re.findall(
        r'(x|y|x1|y1|x2|y2|width|height)="([-\d.]+)"', tag)}


class PdfKnownPlaceAddressTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(app, 'get_settings', return_value={}),
            patch.object(app, 'business_trips_raw', return_value=_fixture()),
            patch.object(app, 'known_place_by_id',
                         side_effect=lambda place_id: KNOWN_PLACES.get(place_id)),
            patch.object(app, 'cached_report_address', return_value=''),
            patch.object(app, 'google_reverse_geocode',
                         side_effect=AssertionError('PDF must not use network')),
            patch.object(app, 'google_place_details',
                         side_effect=AssertionError('PDF must not use network')),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def test_known_places_render_full_addresses_instead_of_labels(self):
        pages = app.business_pdf('month', '2026', '3', preview=True)['pages']
        markup = ''.join(pages)
        self.assertIn(f'>{HOME_ADDRESS}</text>', markup)
        self.assertIn(f'>{PARENTS_ADDRESS}</text>', markup)
        self.assertNotIn('>Thuis</text>', markup)
        self.assertNotIn('>Ouders</text>', markup)

    def test_known_place_address_is_pdf_address_and_label_keeps_context(self):
        with patch.object(app, 'cached_report_address', return_value=''):
            details = app.trip_location_details({'known_place_id': 1})
        self.assertEqual(details['address'], HOME_ADDRESS)
        self.assertEqual(details['label'], f'Thuis - {HOME_ADDRESS}')
        self.assertIn('Verenlandweg%204%2C%207461%20AP%20Rijssen',
                      details['google_maps_uri'])

    def test_cached_address_is_not_prefixed_in_pdf_address_field(self):
        cached = 'Cachestaat 5, 5678 CD Deventer'
        with patch.object(app, 'cached_report_address', return_value=cached):
            details = app.trip_location_details({
                'known_place_id': 1, 'latitude': 52.1, 'longitude': 6.1,
            })
        self.assertEqual(details['address'], cached)
        self.assertEqual(details['label'], f'Thuis - {cached}')


class PdfHeaderConsistencyTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(app, 'get_settings', return_value={}),
            patch.object(app, 'business_trips_raw', return_value=_fixture()),
            patch.object(app, 'known_place_by_id',
                         side_effect=lambda place_id: KNOWN_PLACES.get(place_id)),
            patch.object(app, 'cached_report_address', return_value=''),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def test_page_one_and_two_share_header_coordinates_and_logo_ratio(self):
        pages = app.business_pdf('month', '2026', '3', preview=True)['pages']
        self.assertGreaterEqual(len(pages), 2)
        headers = []
        for markup in pages[:2]:
            title = re.search(r'<text [^>]*>Zakelijke kilometerdeclaratie</text>', markup).group(0)
            subtitle = re.search(
                r'<text [^>]*>Overzicht van zakelijke kilometers met privéauto</text>', markup).group(0)
            logo = re.search(r'<image [^>]*/>', markup).group(0)
            line = next(
                tag for tag in re.findall(r'<line [^>]*/>', markup)
                if _attrs(tag).get('x1') == 36 and _attrs(tag).get('x2') == 559
                and _attrs(tag).get('y1', 999) < 200
            )
            headers.append({
                'title': _attrs(title),
                'subtitle': _attrs(subtitle),
                'logo': _attrs(logo),
                'line': _attrs(line),
            })
        for element in ('title', 'subtitle', 'logo', 'line'):
            for coordinate in headers[0][element]:
                self.assertAlmostEqual(
                    headers[0][element][coordinate],
                    headers[1][element][coordinate],
                    places=6,
                )
        logo = headers[0]['logo']
        self.assertAlmostEqual(logo['width'] / logo['height'], 900 / 827, places=3)
        self.assertGreaterEqual(logo['y'], 0)
        self.assertLessEqual(logo['y'] + logo['height'], 842)


if __name__ == '__main__':
    unittest.main()
