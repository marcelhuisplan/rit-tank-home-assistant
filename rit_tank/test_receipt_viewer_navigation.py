"""Regression: receipt previews stay inside Rit & Tank on iOS/PWA."""
import unittest
import app


class ReceiptViewerNavigationTests(unittest.TestCase):
    def test_receipt_preview_uses_in_app_viewer(self):
        html = app.APP_HTML
        self.assertIn('id="fuelReceiptViewer"', html)
        self.assertIn('id="fuelReceiptFrame"', html)
        self.assertIn('← Terug naar Rit & Tank', html)
        self.assertIn('function showFuelReceipt(kind,id)', html)
        self.assertIn('function closeFuelReceiptViewer()', html)
        self.assertIn("closeModal('fuelReceiptViewer')", html)
        self.assertIn("removeAttribute('src')", html)
        self.assertIn("showFuelReceipt('archive'", html)
        self.assertIn("showFuelReceipt('legacy'", html)
        self.assertNotIn('target="_blank" rel="noopener" href="api/receipt-archive/', html)
        self.assertNotIn('target="_blank" rel="noopener" href="api/receipt/', html)
        self.assertIn('download>Downloaden</a>', html)


if __name__ == '__main__':
    unittest.main()
