"""Source-level regressions for release 16.00 modal scrolling."""
import unittest
from pathlib import Path


APP = (Path(__file__).parent / 'app.py').read_text(encoding='utf-8')


class Release1600ModalScrollTests(unittest.TestCase):
    def test_sheet_has_ios_safe_independent_scrolling(self):
        self.assertIn(
            '.sheet{width:min(100%,640px);max-height:calc(100dvh - '
            'env(safe-area-inset-top) - env(safe-area-inset-bottom) - 12px);'
            'overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;',
            APP,
        )
        self.assertIn('overscroll-behavior:contain;touch-action:pan-y', APP)

    def test_modal_overlay_prevents_scroll_chaining(self):
        self.assertIn(
            '.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);'
            'z-index:30;display:none;align-items:flex-end;justify-content:center;'
            'overflow:hidden;overscroll-behavior:contain}',
            APP,
        )

    def test_modal_helpers_lock_and_restore_page_scroll(self):
        self.assertIn(
            "body.modal-open{position:fixed;width:100%;overflow:hidden}",
            APP,
        )
        self.assertIn(
            "modalScrollY=window.scrollY;document.body.style.top=`-${modalScrollY}px`;"
            "document.body.classList.add('modal-open')",
            APP,
        )
        self.assertIn(
            "document.body.classList.remove('modal-open');document.body.style.top='';"
            "modalScrollY=0;window.scrollTo(0,scrollY)",
            APP,
        )
        self.assertIn(
            "function closeModal(id){$(id).classList.remove('show');unlockModalScroll()}",
            APP,
        )

    def test_pdf_modal_keeps_its_fullscreen_scroll_layout(self):
        self.assertIn(
            '#pdfModal .sheet{width:100%;max-width:100%;height:100%;'
            'max-height:100%;border:0;border-radius:0;padding:0;display:flex;'
            'flex-direction:column;overflow:hidden}',
            APP,
        )


if __name__ == '__main__':
    unittest.main()
