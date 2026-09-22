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
        self.assertIn("'RITTANK_PRIVATE_", ASSISTANT)
        self.assertIn("'RITTANK_BUSINESS_", ASSISTANT)

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

    def test_dictation_uses_browser_speech_and_preserves_typed_fallback(self):
        self.assertIn('window.SpeechRecognition||window.webkitSpeechRecognition', APP)
        self.assertIn("recognition.lang='nl-NL'", APP)
        self.assertIn('recognition.interimResults=false', APP)
        self.assertIn('recognition.maxAlternatives=1', APP)
        self.assertIn("startAddressDictation('addrQuery',searchAssistantAddress)", APP)
        self.assertIn("startAddressDictation('knownAddressQuery',searchKnownPlaceAddress)", APP)
        self.assertIn('Typ het adres of gebruik de microfoon van het toetsenbord.', APP)


if __name__ == '__main__':
    unittest.main()
