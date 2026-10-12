"""Scoped BAG house-number lookup regressions. Synthetic PDOK fixtures only."""
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import google_places


class StreetHouseLookupTests(unittest.TestCase):
    def setUp(self):
        self.ctx = patch.dict(google_places._STREET_CONTEXTS, {}, clear=True)
        self.ctx.start()
        self.addCleanup(self.ctx.stop)
        self.calls = []
        self.reverse_doc = self.doc('1', 'Voorste Land 1, 9461 Gieten')
        self.nearby_doc = self.doc('1a', 'Voorste Land 1a, 9461 Gieten')
        self.hits = []

    @staticmethod
    def doc(number, label, street='Voorste Land', town='Gieten', street_id='12345678', coord='POINT(6.75 53.0)'):
        return {
            'openbareruimte_id': street_id,
            'straatnaam': street,
            'woonplaatsnaam': town,
            'huis_nlt': number,
            'huisnummer': ''.join(c for c in number if c.isdigit()),
            'weergavenaam': label,
            'centroide_ll': coord,
        }

    def http_json(self, url, **kwargs):
        self.calls.append(url)
        params = parse_qs(urlparse(url).query)
        if urlparse(url).path.endswith('/reverse'):
            return {'response': {'docs': [self.reverse_doc]}}
        if 'huisnummer:' in params.get('fq', [''])[0]:
            return {'response': {'docs': self.hits, 'numFound': len(self.hits)}}
        return {'response': {'docs': [self.reverse_doc, self.nearby_doc]}}

    def deps(self):
        return {'http_json': self.http_json, 'haversine_m': lambda *args: 10}

    def context(self):
        r = google_places.nearby_house_numbers(53, 6.75, dependencies=self.deps())
        self.assertEqual((r['street'], r['town']), ('Voorste Land', 'Gieten'))
        self.assertEqual(len(r['addresses']), 2)
        self.assertGreaterEqual(len(r['street_context']), 20)
        return r['street_context']

    def test_house_7_outside_nearby_ten_is_found_at_actual_coordinates(self):
        token = self.context()
        self.hits = [
            self.doc('7', 'Voorste Land 7, 9461 Gieten', coord='POINT(6.7505 53.0007)'),
            self.doc('7', 'Andereweg 7, Gieten', street='Andereweg'),
            self.doc('7', 'Voorste Land 7, Assen', town='Assen'),
            self.doc('7', 'Andere ID', street_id='98765432'),
            self.doc('7', 'Invented coordinates', coord='INVALID'),
        ]
        r = google_places.search_street_house_number(token, '7', dependencies=self.deps())
        self.assertEqual([v['address'] for v in r['addresses']], ['Voorste Land 7, 9461 Gieten'])
        self.assertEqual((r['addresses'][0]['latitude'], r['addresses'][0]['longitude']), (53.0007, 6.7505))
        self.assertIn('openbareruimte_id%3A12345678', self.calls[-1])
        self.assertIn('huisnummer%3A7', self.calls[-1])
        self.assertIn('rows=200', self.calls[-1])
        self.assertNotIn('google', self.calls[-1].lower())

    def test_house_letters_suffixes_and_bis_without_inventing_matches(self):
        token = self.context()
        for number in ('7a', '12-1', '12 bis'):
            self.hits = [self.doc(number, 'Voorste Land ' + number + ', Gieten')]
            found = google_places.search_street_house_number(token, number, dependencies=self.deps())
            self.assertEqual(len(found['addresses']), 1, number)
        self.hits = [self.doc('7a', 'Voorste Land 7a, Gieten')]
        self.assertEqual(google_places.search_street_house_number(token, '7', dependencies=self.deps())['addresses'], [])

    def test_unknown_house_and_other_town_never_leak(self):
        token = self.context()
        self.hits = [self.doc('7', 'Voorste Land 7, Assen', town='Assen')]
        self.assertEqual(google_places.search_street_house_number(token, '7', dependencies=self.deps())['addresses'], [])

    def test_client_cannot_submit_or_change_street_or_town(self):
        for invalid in ('', '12345678', 'Voorste Land,Gieten', 'a' * 33):
            with self.assertRaises(ValueError):
                google_places.search_street_house_number(invalid, '7', dependencies=self.deps())
        self.assertEqual(self.calls, [])
        token = self.context()
        for number in ('7 OR *:*', '7;straat:Assen', '../7', '0', '7 123456789', '7"'):
            with self.assertRaises(ValueError):
                google_places.search_street_house_number(token, number, dependencies=self.deps())

    def test_expired_context_is_rejected_before_network_access(self):
        token = self.context()
        previous = len(self.calls)
        with patch.object(google_places.time, 'monotonic', return_value=float('inf')):
            with self.assertRaisesRegex(ValueError, 'verlopen'):
                google_places.search_street_house_number(token, '7', dependencies=self.deps())
        self.assertEqual(len(self.calls), previous)

    def test_no_usable_street_identity_means_no_context(self):
        self.reverse_doc.pop('woonplaatsnaam')
        r = google_places.nearby_house_numbers(53, 6.75, dependencies=self.deps())
        self.assertEqual((r['addresses'], r['street_context']), ([], ''))


if __name__ == '__main__':
    unittest.main()
