"""Privacy-redacted regression derived from the 2026-10-09 5-visit screenshot.

Do not commit the raw image: it contains customer names and addresses.
"""
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import day_planning as day

HEADER = 'Vrijdag 9 oktober 2026 > Vandaag Week'
ROWS = """Ochtend (08:00 – 12:00) 3 afspraken
# Tijd Adres Woningtype
1 ochtend Testweg 39 a, Stadsn... Hoekwoni... —
2 ochtend Bloemstraat 81, Teststad Tussenwo... 3,4 km
3 ochtend Rijksweg 155, Teststad Hoekwoni... 4,8 km
Middag (12:00 – 17:15) 2 afspraken
4 middag Waterheerd 80, Stadsn. Hoekwoni... 4,6 km
5 middag Hoofdstraat 102, 't Dorp Twee-ond... 22,7 km
"""


class FakeEvents:
    def __init__(self):
        self.items = []
        self.fail = None
        self.insert_count = 0

    def list(self, **kwargs):
        self.list_options = kwargs
        return self

    def insert(self, **kwargs):
        self.pending = kwargs['body']
        return self

    def execute(self):
        if hasattr(self, 'pending'):
            body = self.pending
            del self.pending
            if self.fail:
                raise self.fail
            self.insert_count += 1
            self.items.append(body)
            return body
        return {'items': list(self.items)}


class FakeCalendarList:
    def list(self, **kwargs):
        return self

    def execute(self):
        return {'items': [
            {'id': 'test@example.com', 'summary': 'Test', 'accessRole': 'owner'},
            {'id': 'readonly@example.com', 'summary': 'Read-only', 'accessRole': 'reader'}]}


class FakeService:
    def __init__(self):
        self.event_store = FakeEvents()

    def events(self):
        return self.event_store

    def calendarList(self):
        return FakeCalendarList()


class TestDayPlanning(unittest.TestCase):
    def setUp(self):
        day.DRAFTS.clear()

    def test_five_visits_date_order_daypart_and_uncertainty(self):
        result = day.parse_ocr(HEADER, ROWS)
        self.assertEqual(result['date'], '2026-10-09')
        self.assertFalse(result['date_uncertain'])
        self.assertEqual([r['source_id'] for r in result['visits']], [1, 2, 3, 4, 5])
        self.assertEqual([r['section'] for r in result['visits']], ['ochtend']*3 + ['middag']*2)
        self.assertEqual([r['uncertain'] for r in result['visits']], [True, False, False, True, False])
        self.assertEqual(result['visits'][4]['address'], "Hoofdstraat 102, 't Dorp")
        self.assertEqual(result['warnings'], [])

    def test_ambiguous_date_or_missing_rows_cannot_be_guessed(self):
        self.assertEqual(day.parse_ocr('Vrijdag 10 oktober 2026', ROWS)['date'], '')
        self.assertEqual(day.parse_ocr('9 oktober 2026, 10 oktober 2026', ROWS)['date'], '')
        self.assertTrue(day.parse_ocr(HEADER, ROWS.replace('2 ochtend', '8 ochtend'))['warnings'])

    def _draft(self):
        result = day.parse_ocr(HEADER, ROWS)
        day.DRAFTS['token'] = (time.monotonic() + 100, 'session', result)
        visits = [{'source_id': row['source_id'],
                   'address': ('Testweg 39 a, Teststad' if row['source_id'] == 1 else
                               'Waterheerd 80, Teststad' if row['source_id'] == 4 else row['address']),
                   'reviewed': True} for row in result['visits']]
        return {'draft_token': 'token', 'calendar_id': 'test@example.com',
                'date': '2026-10-09', 'confirmed': True, 'visits': visits}

    def test_no_import_without_confirmation_review_and_same_session(self):
        data = self._draft()
        data['confirmed'] = False
        with self.assertRaisesRegex(ValueError, 'Bevestig'):
            day.validate_submission(data, 'session')
        data['confirmed'] = True
        data['visits'][0]['address'] = 'Testweg 39 a, Stadsn'
        with self.assertRaisesRegex(ValueError, 'vul'):
            day.validate_submission(data, 'session')
        data['visits'][0]['address'] = 'Testweg 39 a, Teststad'
        data['visits'][0]['reviewed'] = False
        with self.assertRaisesRegex(ValueError, 'Controleer'):
            day.validate_submission(data, 'session')
        data['visits'][0]['reviewed'] = True
        with self.assertRaisesRegex(ValueError, 'verlopen'):
            day.validate_submission(data, 'other-session')

    def test_user_reorder_titles_locations_and_all_day_dates(self):
        payload = self._draft()
        payload['visits'][0], payload['visits'][4] = payload['visits'][4], payload['visits'][0]
        date, addresses, calendar_id = day.validate_submission(payload, 'session')
        service = FakeService()
        self.assertEqual(day.duplicate_check(service, calendar_id, date, addresses)['duplicates'], [])
        response = day.import_events(service, calendar_id, date, addresses)
        self.assertEqual(response['created'], 5)
        events = service.event_store.items
        self.assertEqual([e['summary'] for e in events],
                         [f'{i:02d} · Bezoek {i} van 5' for i in range(1, 6)])
        self.assertEqual([e['location'] for e in events], addresses)
        self.assertEqual([e['start'] for e in events], [{'date': '2026-10-09'}]*5)
        self.assertEqual([e['end'] for e in events], [{'date': '2026-10-10'}]*5)
        self.assertTrue(all('dateTime' not in e['start'] and 'dateTime' not in e['end'] for e in events))
        self.assertEqual(day.duplicate_check(service, calendar_id, date, addresses)['duplicates'], [1,2,3,4,5])
        self.assertEqual(day.import_events(service, calendar_id, date, addresses)['skipped'], 5)
        self.assertEqual(service.event_store.insert_count, 5)

    def test_google_authorization_read_only_calendar_and_failed_insert(self):
        with self.assertRaisesRegex(ValueError, 'gekoppeld'):
            day.calendar_service('')
        with self.assertRaisesRegex(ValueError, 'autorisatie'):
            day.calendar_service(json.dumps({'refresh_token': 'abc', 'scopes': ['drive']}))
        service = FakeService()
        self.assertEqual(len(day.available_calendars(service)), 1)
        with self.assertRaisesRegex(ValueError, 'schrijfrechten'):
            day.import_events(service, 'readonly@example.com', '2026-10-09', ['Testweg 1, Teststad'])
        service.event_store.fail = RuntimeError('unauthorized secret data')
        with self.assertRaisesRegex(ValueError, 'onderbroken') as caught:
            day.import_events(service, 'test@example.com', '2026-10-09', ['Testweg 1, Teststad'])
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(service.event_store.insert_count, 0)


if __name__ == '__main__':
    unittest.main()
