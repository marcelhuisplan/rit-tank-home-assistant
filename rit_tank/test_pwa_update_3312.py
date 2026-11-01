"""PWA JavaScript must refresh after Home Assistant add-on updates."""
import unittest
import app


class PwaUpdate3312Tests(unittest.TestCase):
    def test_versioned_receipt_script_and_network_first_worker(self):
        self.assertEqual(app.APP_VERSION, '34.04')
        self.assertIn('<script src="receipt-scanner.js?v=34.04"></script>', app.APP_HTML)
        self.assertIn('<script src="receipt-archive.js?v=34.04"></script>', app.APP_HTML)
        self.assertIn('<script src="oil-history.js?v=34.04"></script>', app.APP_HTML)
        self.assertLess(
            app.APP_HTML.index('receipt-scanner.js?v=34.04'),
            app.APP_HTML.index('receipt-archive.js?v=34.04')
        )
        worker = app.SERVICE_WORKER.decode('utf-8')
        self.assertIn('const CACHE = "rit-tank-shell-34.04";', worker)
        self.assertIn('"receipt-scanner.js?v=34.04"', worker)
        self.assertIn('"receipt-archive.js?v=34.04"', worker)
        self.assertIn('"oil-history.js?v=34.04"', worker)
        self.assertIn('url.pathname.endsWith("/receipt-scanner.js")', worker)
        self.assertIn(
            'url.pathname.endsWith("/receipt-scanner.js") || '
            'url.pathname.endsWith("/receipt-archive.js")',
            worker
        )
        self.assertLess(
            worker.index(
                'if (url.pathname.endsWith("/receipt-scanner.js") || '
                'url.pathname.endsWith("/receipt-archive.js"))'
            ),
            worker.index('event.respondWith(caches.match(request)')
        )
        self.assertIn('fetch(request).then(response', worker)
        self.assertIn('catch(() => caches.match(request))', worker)
        self.assertIn('key.startsWith("rit-tank-shell-")', worker)
        self.assertNotIn('localStorage.clear(', worker)
        self.assertNotIn('indexedDB.deleteDatabase(', worker)


if __name__ == '__main__':
    unittest.main()
