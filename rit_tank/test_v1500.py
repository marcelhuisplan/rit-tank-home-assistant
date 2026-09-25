"""Source-level regressions for release 15.00."""
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
APP = (ROOT / 'app.py').read_text(encoding='utf-8')
ASSISTANT = (ROOT / 'assistant.py').read_text(encoding='utf-8')


class Release1500SourceTests(unittest.TestCase):
    def test_notification_open_targets_are_external_https(self):
        self.assertIn("https://rit.huisplanadvies.nl", ASSISTANT)
        self.assertNotIn("'url': '/675b3933_rit_tank'", ASSISTANT)
        self.assertNotIn("'uri': '/675b3933_rit_tank'", ASSISTANT)
        self.assertNotIn("'action': 'URI', 'title': 'Open Rit & Tank'", ASSISTANT)
        self.assertEqual(ASSISTANT.count("'action': 'OPEN', 'title': 'Open Rit & Tank', 'uri': 'https://rit.huisplanadvies.nl'"), 2)
        self.assertEqual(ASSISTANT.count("'url': 'https://rit.huisplanadvies.nl'"), 3)
        self.assertIn("'RITTANK_BUSINESS_", ASSISTANT)
        self.assertNotIn("'RITTANK_PRIVATE_", ASSISTANT)

    def test_assistant_actions_are_width_constrained(self):
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", APP)
        self.assertIn(".assistant-dismiss{position:absolute", APP)
        self.assertIn(".assistant-actions button{min-width:0", APP)
        self.assertNotIn('grid-template-columns:1fr 1fr auto auto auto', APP)

    def test_route_correction_has_effective_origin_and_additive_columns(self):
        for name in (
            'corrected_origin_latitude', 'corrected_origin_longitude',
            'corrected_origin_label', 'origin_manually_corrected',
            '_assistant_arrival_effective_origin',
            'preview_assistant_arrival_route',
            'correct_assistant_arrival_route',
        ):
            self.assertIn(name, APP + ASSISTANT)
        self.assertIn('/correct-destination', APP)
        self.assertIn('/correct-route', APP)

    def test_typed_address_search_remains_without_dictation(self):
        self.assertNotIn('SpeechRecognition', APP)
        self.assertNotIn('startAddressDictation', APP)
        self.assertIn('searchAssistantAddress()', APP)
        self.assertIn('searchKnownPlaceAddress()', APP)


if __name__ == '__main__':
    unittest.main()
