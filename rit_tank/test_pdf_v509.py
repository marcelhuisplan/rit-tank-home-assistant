"""Offline PDF period regression tests; no live accounts or network."""
import unittest
from unittest.mock import patch
from test_v44 import app


def fixture():
    result = []
    for start, end, purpose in [
        ('2026-09-19', '2026-09-19', 'Septemberbezoek'),
        ('2026-01-31', '2026-02-01', 'Grensrit'),
        ('2027-01-01', '2027-01-01', 'Nieuwjaar'),
    ]:
        stops = [dict(created_at=f'{start}T10:00:00+02:00', odometer=1000,
                      latitude=52.0, longitude=6.0),
                 dict(created_at=f'{end}T11:00:00+02:00', odometer=1025,
                      manual_label='Voorbeeldstraat 10, Voorbeeldstad')]
        result.append((dict(purpose=purpose, status='completed', trip_type='business'), stops))
    return result


class PdfTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('get_settings', {}), ('business_trips_raw', fixture()), ('known_place_by_id', None)]:
            mock = patch.object(app, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        for name in ['google_reverse_geocode', 'google_place_details']:
            mock = patch.object(app, name, side_effect=AssertionError('PDF must not use network'))
            mock.start()
            self.addCleanup(mock.stop)

    def test_month_and_boundary(self):
        data, name = app.business_pdf('month', '2026', '1')
        self.assertEqual(name, 'rittenregistratie_2026-01.pdf')
        self.assertIn(b'Grensrit', data)
        self.assertNotIn(b'Septemberbezoek', data)
        data, _ = app.business_pdf('month', '2026', '2')
        self.assertIn(b'Geen ritten', data)

    def test_year_sorted_unique(self):
        data, name = app.business_pdf('year', '2026')
        self.assertEqual(name, 'rittenregistratie_2026.pdf')
        self.assertLess(data.index(b'Grensrit'), data.index(b'Septemberbezoek'))
        self.assertEqual(data.count(b'Grensrit'), 1)
        self.assertNotIn(b'Nieuwjaar', data)
        self.assertIn(b'Januari 2026', data)
        self.assertIn(b'September 2026', data)
        self.assertNotIn(b'Februari 2026', data)

    def test_other_year_and_empty(self):
        self.assertIn(b'Nieuwjaar', app.business_pdf('year', '2027')[0])
        self.assertIn(b'Geen ritten', app.business_pdf('year', '2028')[0])

    def test_invalid_period_parameters(self):
        for year, month in [('oops', '1'), ('2026', '13'), ('9999', '1'), ('2026', '0')]:
            with self.assertRaises(ValueError):
                app.business_pdf('month', year, month)


if __name__ == '__main__':
    unittest.main()
