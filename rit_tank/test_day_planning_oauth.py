"""Focused OAuth/PKCE and iPhone/iPad receipt close button contracts."""
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import day_planning as day
import app

OPTIONS = {'google_calendar_client_id': 'test-client-id',
           'google_calendar_client_secret': 'test-secret'}
ROOT = 'https://example.test/api/hassio_ingress/stable/'
CALLBACK = ROOT + 'api/day-planning/oauth/callback'


class TokenResponse:
    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def read(self, limit=-1):
        return json.dumps({'access_token': 'secret-access', 'refresh_token': 'secret-refresh',
                           'scope': ' '.join(day.SCOPES), 'expires_in': 3600}).encode()


class OAuthAndMobileTests(unittest.TestCase):
    def setUp(self):
        day.OAUTH_PENDING.clear()

    def start(self):
        url = day.begin_calendar_oauth(OPTIONS, CALLBACK, ROOT, 'https://example.test')
        return url, parse_qs(urlparse(url).query)['state'][0]

    def test_pkce_only_calendar_scopes_and_redirect_restrictions(self):
        url, state = self.start()
        params = parse_qs(urlparse(url).query)
        self.assertEqual(urlparse(url).hostname, 'accounts.google.com')
        self.assertEqual(params['scope'][0].split(), list(day.SCOPES))
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertEqual(params['access_type'], ['offline'])
        self.assertEqual(params['redirect_uri'], [CALLBACK])
        self.assertIn(state, day.OAUTH_PENDING)
        self.assertNotIn('test-secret', url)
        for invalid, origin in [('http://example.test/api/day-planning/oauth/callback', 'https://example.test'),
                                ('https://evil.test/api/day-planning/oauth/callback', 'https://example.test'),
                                (CALLBACK, 'https://evil.test'),
                                (CALLBACK + '?secret=1', 'https://example.test')]:
            with self.assertRaises(ValueError):
                day.begin_calendar_oauth(OPTIONS, invalid, ROOT, origin)

    def test_one_use_server_token_storage_and_safe_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'oauth.json'
            _, state = self.start()
            with patch.object(day, 'urlopen', return_value=TokenResponse()) as request:
                day.finish_calendar_oauth(state, 'google-code', '', OPTIONS, path)
            saved = json.loads(path.read_text())
            self.assertEqual(saved['refresh_token'], 'secret-refresh')
            self.assertEqual(saved['scopes'], list(day.SCOPES))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertIn('code_verifier=', request.call_args.args[0].data.decode())
            with self.assertRaisesRegex(ValueError, 'verlopen'):
                day.finish_calendar_oauth(state, 'reused-code', '', OPTIONS, path)

    def test_denied_scope_does_not_persist_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'oauth.json'
            _, state = self.start()
            class Denied(TokenResponse):
                def read(self, limit=-1):
                    return json.dumps({'access_token': 'a', 'refresh_token': 'r',
                                       'scope': day.SCOPES[0]}).encode()
            with patch.object(day, 'urlopen', return_value=Denied()):
                with self.assertRaises(ValueError):
                    day.finish_calendar_oauth(state, 'code', '', OPTIONS, path)
            self.assertFalse(path.exists())

    def test_mobile_safe_area_sticky_and_archive_close_contract(self):
        source = Path(__file__).with_name('receipt_archive.js').read_text()
        self.assertIn('#receiptArchiveModal .sheethead { position:sticky; top:0; z-index:30', source)
        self.assertIn('safe-area-inset-top', source)
        self.assertIn('safe-area-inset-right', source)
        self.assertIn('width:56px; height:56px; min-width:56px; min-height:56px', source)
        self.assertIn('background:#50eec7; color:#05251d', source)
        self.assertIn('touch-action:manipulation', source)
        self.assertIn('onclick="closeModal(\'receiptArchiveModal\')"', source)
        self.assertIn('function closeModal(id)', app.APP_HTML)
        self.assertIn("$(id).classList.remove('show')", app.APP_HTML)
        self.assertIn('async function prepareArchiveReceipt(selection)', source)
        self.assertIn('async function saveArchiveReceipt()', source)


if __name__ == '__main__':
    unittest.main()
