"""Release 17.00 regressions for assistant route review integration."""
import unittest
from pathlib import Path


ROOT = Path(__file__).parent
APP = (ROOT / 'app.py').read_text(encoding='utf-8')
ASSISTANT = (ROOT / 'assistant.py').read_text(encoding='utf-8')


class Release1700AssistantReviewTests(unittest.TestCase):
    def test_route_review_is_inside_the_arrival_modal(self):
        modal = APP.split('<div class="modal" id="assistantModal">', 1)[1].split(
            '<div class="modal" id="settingsModal">', 1
        )[0]
        self.assertIn('id="assistantRouteSection"', modal)
        self.assertIn('Vertrekadres aanpassen', modal)
        self.assertIn('Aankomstadres aanpassen', modal)
        self.assertNotIn('assistantAddressModal', APP)

    def test_existing_route_and_dictation_helpers_are_reused(self):
        for value in (
            "chooseAssistantRouteSide('origin')",
            "chooseAssistantRouteSide('destination')",
            'searchAssistantAddress()',
            'selectAssistantAddressResult',
            'useAssistantAddressResult',
            "startAddressDictation('addrQuery',searchAssistantAddress)",
            'window.SpeechRecognition||window.webkitSpeechRecognition',
            "recognition.lang='nl-NL'",
        ):
            self.assertIn(value, APP)
        self.assertIn('/api/places/search-address', APP)
        self.assertIn('/correct-route', APP)

    def test_legacy_effective_address_fallbacks_remain_backend_driven(self):
        self.assertIn('_assistant_arrival_effective_origin', ASSISTANT)
        self.assertIn('_assistant_arrival_effective_destination', ASSISTANT)
        self.assertIn("'Onbekende vertrekplek'", ASSISTANT)
        self.assertIn("'Onbekende bestemming'", ASSISTANT)

    def test_route_correction_refreshes_the_arrival_and_keeps_one_modal(self):
        self.assertIn("fresh=await api('api/assistant/arrivals')", APP)
        self.assertIn('updateAssistantRouteUI(updated)', APP)
        self.assertIn("await reloadData()", APP)
        self.assertIn("guideTo('assistantRouteSection')", APP)
        self.assertIn('function lockModalScroll()', APP)
        self.assertIn("document.querySelector('.modal.show')", APP)


if __name__ == '__main__':
    unittest.main()
