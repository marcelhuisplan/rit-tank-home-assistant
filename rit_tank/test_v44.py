"""Offline regression tests; no Home Assistant, OCR or Drive calls."""
import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import MagicMock, patch

try:
    import websocket
except ImportError:
    sys.modules['websocket'] = types.ModuleType('websocket')
spec = importlib.util.spec_from_file_location('rit_tank_test_app', Path(__file__).with_name('app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
spec.loader.exec_module(app)


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        app.DATA_DIR = Path(self.tmp.name)
        app.DB_PATH = app.DATA_DIR / 'test.db'
        app.OPTIONS_PATH = app.DATA_DIR / 'options.json'
        self.patches = [patch.object(app, 'publish_sensors_async', lambda: None),
                        patch.object(app, 'send_assistant_notification', lambda item: None),
                        patch.object(app, 'google_reverse_geocode', lambda *args: {})]
        for p in self.patches:
            p.start()
        app.init_db()
        with app.db() as con:
            con.execute("INSERT INTO known_places(name,latitude,longitude,created_at,updated_at) VALUES('Thuis',52,6,?,?)", (app.iso_local(), app.iso_local()))
            con.execute("INSERT INTO events(created_at,type,odometer) VALUES(?,'odometer',10000)", (app.iso_local(app.now_local()-timedelta(hours=2)),))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def learn(self, source, actual=10.2, checked=True, gps=10, samples=10):
        with app.db() as con:
            app.learn_distance(source, gps, actual, samples, checked, con=con)

    def test_database_context_commits_and_closes_connection(self):
        with app.db() as con:
            con.execute("INSERT INTO settings(key,value) VALUES('context_test','committed')")
        with self.assertRaisesRegex(Exception, 'closed'):
            con.execute("SELECT value FROM settings WHERE key='context_test'")
        with app.db() as con:
            self.assertEqual(con.execute("SELECT value FROM settings WHERE key='context_test'").fetchone()[0], 'committed')

    def test_database_context_rolls_back_and_closes_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'context failure'):
            with app.db() as con:
                con.execute("INSERT INTO settings(key,value) VALUES('context_test_rollback','discarded')")
                raise RuntimeError('context failure')
        with self.assertRaisesRegex(Exception, 'closed'):
            con.execute("SELECT 1")
        with app.db() as con:
            self.assertIsNone(con.execute("SELECT value FROM settings WHERE key='context_test_rollback'").fetchone())

    def test_home_assistant_rest_auth_uses_bearer_token_and_redacts_errors(self):
        token = 'secret-supervisor-token'
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"state": "ok"}'
        with patch.dict(app.os.environ, {'SUPERVISOR_TOKEN': token}), \
                patch.object(app.urllib.request, 'urlopen', return_value=response) as urlopen:
            self.assertEqual(app.ha_request('GET', 'states'), {'state': 'ok'})
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), f'Bearer {token}')

        with patch.dict(app.os.environ, {'SUPERVISOR_TOKEN': token}), \
                patch.object(app.urllib.request, 'urlopen', side_effect=RuntimeError(token)):
            with self.assertRaises(ValueError) as caught:
                app.ha_request('GET', 'states')
        self.assertNotIn(token, str(caught.exception))
        self.assertIn('[redacted]', str(caught.exception))

        with patch.dict(app.os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, '^Home Assistant API-token is niet beschikbaar\\.$') as caught:
                app.ha_request('GET', 'states')
        self.assertEqual(str(caught.exception), 'Home Assistant API-token is niet beschikbaar.')

        state_response = MagicMock()
        state_response.__enter__.return_value = state_response
        with patch.dict(app.os.environ, {'SUPERVISOR_TOKEN': token}), \
                patch.object(app.urllib.request, 'urlopen', return_value=state_response) as urlopen:
            app.ha_post_state('sensor.test', 'ok', {})
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), f'Bearer {token}')

    def test_delete_business_trip_removes_events_and_keeps_audit_snapshot(self):
        result = app.start_business_trip({
            'odometer': 10000,
            'created_at': app.iso_local(),
            'latitude': 52,
            'longitude': 6,
            'purpose': 'Testrit',
        })
        trip_id = int(result['trip']['id'])
        with app.db() as con:
            event_ids = [row['event_id'] for row in con.execute(
                'SELECT event_id FROM trip_stops WHERE trip_id=?', (trip_id,)
            )]
        app.delete_business_trip(trip_id)
        with app.db() as con:
            self.assertIsNone(con.execute('SELECT id FROM business_trips WHERE id=?', (trip_id,)).fetchone())
            for event_id in event_ids:
                self.assertIsNone(con.execute('SELECT id FROM events WHERE id=?', (event_id,)).fetchone())
            audit_row = con.execute(
                "SELECT details FROM audit_log WHERE action='delete' AND entity_type='trip' AND entity_id=?",
                (trip_id,)
            ).fetchone()
        self.assertIsNotNone(audit_row)
        snapshot = json.loads(audit_row['details'])
        self.assertEqual(snapshot['trip']['id'], trip_id)
        self.assertEqual(len(snapshot['stops']), len(event_ids))

    def test_current_version_is_consistent_across_runtime_and_docs(self):
        version = '11.00'
        root = Path(__file__).parent
        self.assertEqual(app.APP_VERSION, version)
        self.assertRegex((root / 'config.yaml').read_text(encoding='utf-8'), rf"(?m)^version: ['\"]{re.escape(version)}['\"]$")
        self.assertTrue((root / 'README.md').read_text(encoding='utf-8').startswith(f'# Rit & Tank {version}'))
        self.assertTrue((root / 'CHANGELOG.md').read_text(encoding='utf-8').startswith(f'# Changelog\n\n## {version}'))
        self.assertIn(f'rit-tank-shell-{version}'.encode('utf-8'), app.SERVICE_WORKER)
        self.assertEqual(app.summary()['app']['version'], version)

    def test_diagnostic_log_is_bounded_and_excludes_sensitive_fields(self):
        for i in range(505):
            app.diagnostic_event('achtergrondcontrole', tracked_m=i,
                                 latitude=52.1, address='Secret street', api_key='SECRET')
        report = app.diagnostic_report()
        self.assertEqual(len(report['events']), 500)
        self.assertEqual(report['events'][0]['details'], {'tracked_m': 5})
        self.assertNotIn('SECRET', str(report))
        self.assertNotIn('Secret street', str(report))
        app.diagnostic_event('achtergrondcontrole', tracked_m=504)
        self.assertEqual(len(app.diagnostic_report()['events']), 500)

    def test_diagnostic_report_excludes_expired_events(self):
        app.assistant_state_set('diagnostic_log', [
            {'at': app.iso_local(app.now_local()-timedelta(hours=49)),
             'event': 'oud', 'details': {}}])
        self.assertEqual(app.diagnostic_report()['events'], [])

    def arrival(self, incomplete=False):
        return app.create_assistant_arrival(1, None, 52.1, 6.1, 10,
            app.iso_local(app.now_local()-timedelta(hours=1)), 'Klant',
            route_snapshot={'route_m':10000, 'route_samples':10, 'route_incomplete':incomplete})

    def test_learning_needs_five_checked_stable_samples(self):
        self.learn('unchecked', checked=False)
        self.learn('short', gps=1)
        self.learn('outlier', actual=20)
        self.learn('sparse', samples=2)
        self.assertEqual(app.distance_calibration()['samples'], 0)
        for i in range(4):
            self.learn(str(i))
        self.assertEqual(app.distance_calibration()['factor'], 1)
        self.learn('4')
        self.learn('4')
        self.assertEqual(app.distance_calibration()['samples'], 5)
        self.assertEqual(app.distance_calibration()['factor'], 1.02)

    def test_calibration_can_disable_and_is_vehicle_specific(self):
        for i in range(5):
            self.learn(str(i))
        with app.db() as con:
            con.execute("INSERT INTO settings VALUES('distance_learning_enabled','0')")
        self.assertEqual(app.distance_calibration()['factor'], 1)
        with app.db() as con:
            con.execute("INSERT INTO settings VALUES('license_plate','OTHER')")
        self.assertEqual(app.distance_calibration()['samples'], 0)

    def test_draft_route_gaps_disable_suggestion(self):
        now = app.now_local()
        state = {'departure_at':app.iso_local(now), 'route_lat':52, 'route_lon':6, 'route_at':app.iso_local(now)}
        app.advance_draft_route(state,52.001,6,10,now+timedelta(seconds=30))
        self.assertGreater(state['route_m'], 100)
        app.advance_draft_route(state,52.1,6,10,now+timedelta(minutes=10))
        self.assertTrue(state['route_incomplete'])

    def test_arrival_is_only_a_draft_then_can_be_confirmed_once(self):
        item = self.arrival()
        self.assertEqual(item['proposal']['suggested_odometer'],10010)
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0],0)
        result = app.complete_assistant_arrival(item['id'],{'trip_type':'business','start_odometer':10000,'odometer':10010})
        self.assertTrue(result['ok'])
        self.assertEqual(app.distance_calibration()['samples'],0)
        with self.assertRaises(ValueError):
            app.complete_assistant_arrival(item['id'],{'trip_type':'business','odometer':10010})

    def test_checked_arrival_trains_and_gap_does_not(self):
        item = self.arrival()
        app.complete_assistant_arrival(item['id'],{'trip_type':'business','start_odometer':10000,'odometer':10010,'odometer_checked':True})
        self.assertEqual(app.distance_calibration()['samples'],1)

    def test_incomplete_arrival_has_no_suggested_odometer(self):
        item = self.arrival(incomplete=True)
        self.assertIsNone(item['proposal']['suggested_odometer'])
        app.complete_assistant_arrival(item['id'],{'trip_type':'business','start_odometer':10000,'odometer':10010,'odometer_checked':True})
        self.assertEqual(app.distance_calibration()['samples'],0)

    def test_overlap_rejected(self):
        item = self.arrival()
        with app.db() as con:
            con.execute("INSERT INTO business_trips(started_at,ended_at,status) VALUES(?,?,'completed')", (item['departure_at'],item['detected_at']))
        with self.assertRaisesRegex(ValueError,'overlapt'):
            app.complete_assistant_arrival(item['id'],{'trip_type':'business','start_odometer':10000,'odometer':10010})

    def test_migration_is_repeatable(self):
        app.init_db()
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    def test_known_place_departure_and_arrival_create_draft_automatically(self):
        with app.db() as con:
            con.execute("INSERT INTO known_places(name,latitude,longitude,radius_m,created_at,updated_at) VALUES('Kantoor',52.01,6,100,?,?)", (app.iso_local(),app.iso_local()))
        cfg = app.assistant_config()
        start = app.now_local()
        for i in range(12):
            lat = 52 + min(i, 10) * .001
            with patch.object(app,'now_local',return_value=start+timedelta(seconds=i*30)):
                app._process_assistant_location({'latitude':lat,'longitude':6,'accuracy':10},cfg)
        arrivals = app.assistant_arrivals()
        self.assertEqual(len(arrivals),1)
        self.assertEqual(arrivals[0]['origin_name'],'Thuis')
        self.assertEqual(arrivals[0]['destination_name'],'Kantoor')
        self.assertGreater(arrivals[0]['proposal']['gps_km'],.5)
        self.assertIsNotNone(arrivals[0]['proposal']['suggested_odometer'])
        with app.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM business_trips').fetchone()[0],0)

    def test_unstable_calibration_is_not_used(self):
        for i, ratio in enumerate([.86,.90,1.0,1.10,1.14]):
            self.learn(str(i), actual=10*ratio)
        self.assertFalse(app.distance_calibration()['ready'])
        self.assertEqual(app.distance_calibration()['factor'],1)

    def test_old_proposal_cannot_modify_newer_active_trip(self):
        item = self.arrival()
        app.start_business_trip({'odometer':10000, 'created_at':app.iso_local(app.now_local()+timedelta(seconds=5)), 'latitude':52,'longitude':6})
        with self.assertRaisesRegex(ValueError,'ouder'):
            app.complete_assistant_arrival(item['id'],{'trip_type':'business','odometer':10010})

    def test_active_trip_arrival_can_explicitly_finish(self):
        started = app.iso_local(app.now_local()-timedelta(minutes=90))
        app.start_business_trip({'odometer':10000,'created_at':started,'latitude':52,'longitude':6})
        item = self.arrival()
        app.complete_assistant_arrival(item['id'],{'trip_type':'business','odometer':10010,'finish':True})
        self.assertIsNone(app.active_business_trip())

    def test_manual_stop_learns_checked_distance_and_resets_route(self):
        started = app.iso_local(app.now_local()-timedelta(hours=1))
        app.start_business_trip({'odometer':10000,'created_at':started,'latitude':52,'longitude':6})
        state = app.assistant_state_get('trip_distance_tracking')
        state.update(segment_m=10000,sample_count=20,last_update=app.iso_local())
        app.assistant_state_set('trip_distance_tracking',state)
        self.assertEqual(app.trip_distance_tracking_public()['suggested_odometer'],10010)
        app.add_business_stop({'odometer':10010,'created_at':app.iso_local(),'latitude':52.1,'longitude':6.1,'segment_trip_type':'business','odometer_checked':True})
        self.assertEqual(app.distance_calibration()['samples'],1)
        self.assertEqual(app.assistant_state_get('trip_distance_tracking')['segment_m'],0)

    def test_personal_correction_is_capped(self):
        for i in range(5):
            self.learn(str(i),actual=11.4)
        self.assertEqual(app.distance_calibration()['factor'],1.10)

    def test_overijssel_stop_prompt_after_ten_seconds_only_once(self):
        now=app.now_local()
        app.start_business_trip({'odometer':10000,'created_at':app.iso_local(now-timedelta(hours=1)),'latitude':52,'longitude':6})
        state=app.assistant_state_get('trip_distance_tracking')
        state.update(segment_m=1000,sample_count=10,last_update=app.iso_local(now),stationary_since=app.iso_local(now))
        app.assistant_state_set('trip_distance_tracking',state)
        loc={'latitude':52,'longitude':6,'accuracy':10,'speed':0}
        with patch.object(app,'province_allowed_for_push',return_value=(True,'Overijssel',{'address':'Zwolle'})), patch.object(app,'send_active_trip_stop_notification',return_value=True) as notify:
            for seconds in (9,10,20):
                with patch.object(app,'now_local',return_value=now+timedelta(seconds=seconds)):
                    app.track_active_trip_distance(loc,app.assistant_config())
                self.assertEqual(bool(app.trip_distance_tracking_public().get('stop_prompt')),seconds>=10)
            self.assertEqual(notify.call_count,1)
        self.assertIsNotNone(app.active_business_trip())

    def test_drenthe_keeps_configured_thirty_second_delay(self):
        now=app.now_local()
        app.start_business_trip({'odometer':10000,'created_at':app.iso_local(now-timedelta(hours=1)),'latitude':52,'longitude':6})
        state=app.assistant_state_get('trip_distance_tracking')
        state.update(segment_m=1000,sample_count=10,last_update=app.iso_local(now),stationary_since=app.iso_local(now))
        app.assistant_state_set('trip_distance_tracking',state)
        with patch.object(app,'province_allowed_for_push',return_value=(True,'Drenthe',{})),patch.object(app,'send_active_trip_stop_notification',return_value=True):
            for seconds in (10,30):
                with patch.object(app,'now_local',return_value=now+timedelta(seconds=seconds)):
                    app.track_active_trip_distance({'latitude':52,'longitude':6,'speed':0},app.assistant_config())
                self.assertEqual(bool(app.trip_distance_tracking_public().get('stop_prompt')),seconds>=30)


if __name__ == '__main__':
    unittest.main(verbosity=2)
