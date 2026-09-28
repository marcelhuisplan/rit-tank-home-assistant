"""Release 31: real POST gates, isolated SQLite, no external services."""
import csv
import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from test_v2300 import app
from test_v3000 import Request
import odometer_control as control


class Physical3100Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.now = datetime.fromisoformat('2026-09-28T12:00:00+02:00')
        for name, value in {'DATA_DIR': root, 'DB_PATH': root/'test.db',
                            'OPTIONS_PATH': root/'options.json', 'RECEIPT_DIR': root/'receipts',
                            'now_local': lambda: self.now, 'publish_sensors_async': lambda: None,
                            'google_reverse_geocode': lambda *a: {}, 'google_place_details': lambda *a: {},
                            'http_json': lambda *a, **k: self.fail('Unexpected external request')}.items():
            p = patch.object(app, name, value); p.start(); self.addCleanup(p.stop)
        control.PENDING.clear(); self.addCleanup(control.PENDING.clear)
        app.init_db()
        app.set_settings({'km_reimbursement_rate': '0.35'})
        self.start = dict(odometer='64.334', created_at=app.iso_local(self.now-timedelta(hours=2)),
                          latitude=52., longitude=6., manual_label='Startstraat 1, 1234 AB Teststad',
                          physical_confirmed=True)
        self.end = dict(self.start, odometer='64.375', created_at=app.iso_local(self.now),
                        latitude=52.2, manual_label='Eindstraat 2, 1234 AC Teststad')

    def post(self, path, payload, headers=None, ingress=True):
        request = Request(path, payload, {'X-Trip-Session': 'test-session-3100-abcdefgh', **(headers or {})}, ingress)
        request.do_POST(); return request

    def begin(self):
        r = self.post('/api/business/start', self.start)
        self.assertEqual(r.status, 201, r.json())
        trip = r.json()['trip']; self.end['trip_id'] = trip['id']; return trip

    def gps(self, km=36.4, incomplete=False, samples=50):
        trip = app.active_business_trip()
        app.assistant_state_set('trip_distance_tracking', {'trip_id': trip['id'],
            'stop_id': trip['stops'][-1]['id'], 'segment_m': km*1000, 'incomplete': incomplete,
            'sample_count': samples})

    def snapshot(self):
        with app.db() as con:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {t: [tuple(r) for r in con.execute('SELECT * FROM '+t+' ORDER BY rowid')] for t in tables}

    def challenge(self, payload=None, path='/api/business/finish'):
        before = self.snapshot(); r = self.post(path, payload or self.end)
        self.assertEqual(r.status, 409, r.json()); self.assertEqual(self.snapshot(), before)
        return r.json()

    def finish(self, challenge, payload=None, path='/api/business/finish', headers=None):
        r = self.post(path, {**(payload or self.end), 'confirmation_token': challenge['confirmation_token']}, headers)
        self.assertEqual(r.status, 201, r.json()); return r.json()

    def test_01_example_41_everywhere_and_durable_audit(self):
        self.begin(); self.gps(); check = self.challenge()
        self.assertAlmostEqual(check['check']['difference_km'], 4.6)
        self.assertAlmostEqual(check['check']['relative_difference'], 4.6/41)
        result = self.finish(check)['trip']; self.assertEqual(result['km'], 41)
        summary = app.summary('month')
        self.assertEqual(summary['current_odometer'], 64375)
        self.assertEqual(summary['period']['km'], 41)
        self.assertEqual(summary['business']['recent_trips'][0]['km'], 41)
        for period in ['day', 'week', 'month', 'year']:
            self.assertEqual(app.business_stats_for_period(period)['business_km'], 41)
        report = app.checked_business_report('month', '2026', '9')
        self.assertEqual(report['rows'][0]['km'], 41)
        self.assertEqual(str(report['rows'][0]['reimbursement']), '14.35')
        self.assertEqual(app.business_report_validation(report)['summary']['business_km'], 41)
        pdf, _ = app.business_pdf(report=report)
        self.assertTrue(pdf.startswith(b'%PDF'))
        for text in ['64.334 km', '64.375 km', '41,0', '14,35']:
            self.assertIn(app.pdf_report._pdf_escape(text), pdf)
        h = Request('/api/business.csv'); h.do_GET(); self.assertEqual(h.status, 200)
        rows = list(csv.DictReader(io.StringIO(h.wfile.getvalue().decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(sum(float(r['segment_km']) for r in rows), 41)
        self.assertEqual({r['totaal_km'] for r in rows}, {'41.0'})
        self.assertEqual({r['reimbursement_eur'] for r in rows}, {'14.35'})
        self.assertEqual(app.assistant_state_get('trip_distance_tracking'), {})
        with app.db() as con:
            audit = json.loads(con.execute("SELECT details FROM audit_log WHERE action='finish'").fetchone()[0])['distance_control']
        for key, value in {'start_odometer':64334, 'end_odometer':64375, 'odometer_km':41,
                           'gps_km':36.4, 'distance_warning_km':2, 'distance_warning_fraction':.05,
                           'confirmation_required':True, 'confirmation_given':True}.items():
            self.assertEqual(audit[key], value)
        self.assertIn('confirmed_at', audit)

    def test_02_proposal_becomes_36_only_after_physical_confirmation(self):
        self.begin(); self.gps(); self.end['odometer']=64370
        r=self.post('/api/business/finish',self.end)
        self.assertEqual(r.status,201); self.assertEqual(r.json()['trip']['km'],36)

    def test_03_small_absolute_difference(self): self.assertFalse(control.compare(10,10.6,control.thresholds({}))['significant'])
    def test_04_large_absolute_small_relative(self): self.assertFalse(control.compare(200,205,control.thresholds({}))['significant'])
    def test_05_large_relative_small_absolute(self): self.assertFalse(control.compare(1,2,control.thresholds({}))['significant'])
    def test_06_both_thresholds(self): self.assertTrue(control.compare(36.4,41,control.thresholds({}))['significant'])
    def test_07_symmetric(self):
        a=control.compare(36.4,41,control.thresholds({})); b=control.compare(41,36.4,control.thresholds({}))
        self.assertEqual(a['relative_difference'],b['relative_difference']); self.assertTrue(b['significant'])
        self.assertEqual(b['difference_km'],-4.6)
    def test_08_missing_is_not_zero(self):
        self.begin(); c=self.challenge(); self.assertIsNone(c['check']['gps_km'])
        self.assertIsNone(c['check']['relative_difference']); self.assertTrue(c['check']['gps_missing'])
        self.assertEqual(self.finish(c)['trip']['km'],41)
    def test_09_interrupted_even_when_numerically_close(self):
        self.begin(); self.gps(40.9,True); c=self.challenge()
        self.assertFalse(c['check']['significant']); self.assertTrue(c['check']['gps_incomplete'])
        self.assertIn('GPS-route mogelijk onderbroken',app.trip_distance_tracking_public()['distance_warning'])
        self.assertEqual(self.finish(c)['trip']['km'],41)
    def test_10_end_below_start_no_write(self):
        self.begin(); self.end['odometer']=64333; before=self.snapshot()
        self.assertEqual(self.post('/api/business/finish',self.end).status,400);self.assertEqual(self.snapshot(),before)
    def test_11_end_below_stop_no_write(self):
        self.begin(); self.gps(16); stop={**self.end,'odometer':64350,'created_at':app.iso_local(self.now-timedelta(hours=1))}
        self.assertEqual(self.post('/api/business/stop',stop).status,201)
        self.end['odometer']=64349;before=self.snapshot()
        self.assertEqual(self.post('/api/business/finish',self.end).status,400);self.assertEqual(self.snapshot(),before)
    def test_12_dutch(self): self.assertEqual(control.physical_odometer('64.375'),64375)
    def test_13_plain(self): self.assertEqual(control.physical_odometer('64375'),64375)
    def test_14_invalid_no_write(self):
        self.begin(); before=self.snapshot()
        for value in ['',None,'text','NaN','Infinity',-1,'64.37.5','64,375','64375.5',True,[],{},1e20,'1e5']:
            with self.subTest(value=value):
                self.assertEqual(self.post('/api/business/finish',{**self.end,'odometer':value}).status,400)
                self.assertEqual(self.snapshot(),before)
    def test_16_changed_value_invalidates_token(self):
        self.begin();self.gps();c=self.challenge();before=self.snapshot()
        r=self.post('/api/business/finish',{**self.end,'odometer':64376,'confirmation_token':c['confirmation_token']})
        self.assertEqual(r.status,400);self.assertEqual(self.snapshot(),before)
    def test_17_duplicate_finish(self):
        self.begin();self.gps();c=self.challenge();self.finish(c);before=self.snapshot()
        r=self.post('/api/business/finish',{**self.end,'confirmation_token':c['confirmation_token']})
        self.assertEqual(r.status,400);self.assertEqual(self.snapshot(),before)
    def test_18_cancel_no_write(self):
        self.begin();self.gps();before=self.snapshot();self.challenge();self.assertEqual(self.snapshot(),before)
    def test_19_stops_total_and_gps_are_not_last_segment(self):
        self.begin();self.gps(16)
        stop={**self.end,'odometer':64350,'created_at':app.iso_local(self.now-timedelta(hours=1))}
        self.assertEqual(self.post('/api/business/stop',stop).status,201)
        self.gps(20.4);c=self.challenge();self.assertEqual(c['check']['gps_km'],36.4)
        result=self.finish(c)['trip'];self.assertEqual([s['segment_km'] for s in result['stops']],[0,16,25])
        self.assertEqual(result['km'],41)
        report=app.business_report('all');self.assertEqual(sum(r['km'] for r in report['rows']),41)
    def test_20_missing_earlier_gps_keeps_total_unknown(self):
        self.begin();stop={**self.end,'odometer':64350,'created_at':app.iso_local(self.now-timedelta(hours=1))}
        c=self.challenge(stop,'/api/business/stop');self.finish(c,stop,'/api/business/stop')
        self.gps(25);c=self.challenge();self.assertIsNone(c['check']['gps_km']);self.assertEqual(c['check']['gps_partial_km'],25)
    def test_21_session_binding(self):
        self.begin();self.gps();c=self.challenge();before=self.snapshot()
        r=self.post('/api/business/finish',{**self.end,'confirmation_token':c['confirmation_token']}, {'X-Trip-Session':'different-session-abcdefgh'})
        self.assertEqual(r.status,400);self.assertEqual(self.snapshot(),before)
    def test_22_expired(self):
        self.begin();self.gps();c=self.challenge();control.PENDING[c['confirmation_token']]['expires']=0
        self.assertEqual(self.post('/api/business/finish',{**self.end,'confirmation_token':c['confirmation_token']}).status,400)
    def test_23_no_physical_confirmation(self):
        before=self.snapshot();self.assertEqual(self.post('/api/business/start',{**self.start,'physical_confirmed':False}).status,400)
        self.assertEqual(self.snapshot(),before)
    def test_24_alias_cannot_bypass(self):
        self.begin();self.gps();self.challenge(path='/api/trips/finish')
    def test_25_direct_post_cannot_forge_check(self):
        self.begin();self.gps();self.end.update(_distance_control={'confirmation_given':True},distance_km=999)
        c=self.challenge();self.assertEqual(self.finish(c)['trip']['km'],41)
    def test_26_cross_site_and_content_type(self):
        self.begin();before=self.snapshot()
        for headers in [{'Sec-Fetch-Site':'cross-site'},{'Content-Type':'text/plain'},{'X-Trip-Session':''}]:
            self.assertEqual(self.post('/api/business/finish',self.end,headers).status,403)
        self.assertEqual(self.snapshot(),before)
    def test_27_unauthorized(self):
        before=self.snapshot();self.assertNotEqual(self.post('/api/business/start',self.start,ingress=False).status,201)
        self.assertEqual(self.snapshot(),before)
    def test_28_threshold_boundary_exact(self):
        for gps,actual in [(38,40),(95,100)]: self.assertFalse(control.compare(gps,actual,control.thresholds({}))['significant'])
        self.assertTrue(control.compare(94.999,100,control.thresholds({}))['significant'])
    def test_29_zero_denominator(self):
        c=control.compare(0,0,control.thresholds({}));self.assertEqual(c['relative_difference'],0);self.assertFalse(c['significant'])
    def test_30_settings_central(self):
        app.set_settings({'distance_warning_km':5,'distance_warning_fraction':'.1'})
        self.begin();self.gps();self.assertEqual(self.post('/api/business/finish',self.end).status,201)
    def test_31_threshold_change_invalidates_confirmation(self):
        self.begin();self.gps();c=self.challenge();app.set_settings({'distance_warning_km':3})
        before=self.snapshot();self.assertEqual(self.post('/api/business/finish',{**self.end,'confirmation_token':c['confirmation_token']}).status,400)
        self.assertEqual(self.snapshot(),before)
    def test_32_gps_snapshot_frozen_while_reading(self):
        self.begin();self.gps();c=self.challenge();self.gps(36.5)
        self.finish(c)
        with app.db() as con:
            detail=json.loads(con.execute("SELECT details FROM audit_log WHERE action='finish'").fetchone()[0])
        self.assertEqual(detail['distance_control']['gps_km'],36.4)
    def test_33_history_unchanged(self):
        self.begin();self.gps(41);self.post('/api/business/finish',self.end)
        with app.db() as con:
            previous={t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in ['trip_stops','business_trips','events']}
        self.start.update(odometer=64375,created_at=app.iso_local(self.now+timedelta(minutes=1)))
        self.begin()
        with app.db() as con:
            for t,rows in previous.items(): self.assertEqual([tuple(r) for r in con.execute('SELECT * FROM '+t+' ORDER BY id')][:len(rows)],rows)
    def test_34_no_schema_extension(self):
        with app.db() as con: columns=[r['name'] for r in con.execute('PRAGMA table_info(business_trips)')]
        self.assertNotIn('gps_km',columns);self.assertNotIn('distance_km',columns)
    def test_35_no_learning_from_unconfirmed(self):
        trip=app.start_business_trip({**self.start,'odometer':64334})['trip'];self.gps(10)
        app.add_business_stop({**self.end,'odometer':64344,'odometer_checked':True,'physical_confirmed':False},finish=True)
        self.assertEqual(app.distance_calibration()['samples'],0)
    def test_36_incomplete_no_learning(self):
        self.begin();self.gps(40,True);self.end['odometer_checked']=True
        self.finish(self.challenge());self.assertEqual(app.distance_calibration()['samples'],0)
    def test_37_no_calibration_in_fiscal_distance(self):
        self.begin();self.gps()
        with patch.object(app,'distance_calibration',return_value={'factor':1.1,'ready':True}):
            c=self.challenge();self.assertEqual(c['check']['gps_km'],36.4)
            self.assertEqual(self.finish(c)['trip']['km'],41)
    def test_38_low_samples_independent_warning(self):
        self.begin();self.gps(41,samples=1);c=self.challenge();self.assertTrue(c['check']['gps_insufficient'])
    def test_39_end_equals_start(self):
        self.begin();self.end['odometer']=64334;c=self.challenge()
        self.assertEqual(self.finish(c)['trip']['km'],0)
    def test_40_duplicate_concurrent_finish(self):
        self.begin();self.gps();c=self.challenge();payload={**self.end,'confirmation_token':c['confirmation_token']}
        results=[]
        threads=[threading.Thread(target=lambda:results.append(self.post('/api/business/finish',payload).status)) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results),[201,400])
        self.assertEqual(len(app.business_trips_raw()[0][1]),2)
    def test_41_wrong_trip(self):
        self.begin();before=self.snapshot();self.assertEqual(self.post('/api/business/finish',{**self.end,'trip_id':999}).status,400)
        self.assertEqual(self.snapshot(),before)
    def test_42_reverse_time(self):
        self.begin();before=self.snapshot()
        r=self.post('/api/business/finish',{**self.end,'created_at':app.iso_local(self.now-timedelta(days=1))})
        self.assertEqual(r.status,400);self.assertEqual(self.snapshot(),before)

    def arrival(self):
        with app.db() as con:
            for name,lat in [('Start',52),('Eind',52.2)]:
                con.execute('INSERT INTO known_places(name,latitude,longitude,address,created_at,updated_at) VALUES(?,?,6,?,?,?)',
                            (name,lat,name+'straat 1, 1234 AB Teststad',app.iso_local(),app.iso_local()))
            ids=[r[0] for r in con.execute('SELECT id FROM known_places ORDER BY id')]
        with patch.object(app,'send_assistant_notification',lambda *a:None):
            arrival=app.create_assistant_arrival(ids[0],ids[1],52.2,6,10,self.start['created_at'],
                   route_snapshot={'route_m':36400,'route_samples':50,'route_incomplete':False})
        return arrival['id']

    def test_43_assistant_cannot_bypass_physical_confirmation(self):
        ident=self.arrival();before=self.snapshot()
        r=self.post(f'/api/assistant/{ident}/complete',{'start_odometer':64334,'odometer':64375})
        self.assertEqual(r.status,400);self.assertEqual(self.snapshot(),before)

    def test_44_assistant_two_step_and_audit(self):
        ident=self.arrival();path=f'/api/assistant/{ident}/complete'
        payload={'start_odometer':'64.334','odometer':'64.375','physical_confirmed':True}
        c=self.challenge(payload,path);self.assertEqual(c['check']['gps_km'],36.4)
        self.finish(c,payload,path)
        self.assertEqual(app.business_stats_for_period('day')['km'],41)
        with app.db() as con:
            audit=json.loads(con.execute("SELECT details FROM audit_log WHERE action='assistant_complete'").fetchone()[0])
        self.assertEqual(audit['distance_control']['odometer_km'],41)
        self.assertTrue(audit['distance_control']['confirmation_given'])

    def test_45_changed_address_invalidates_token(self):
        self.begin();self.gps();c=self.challenge();before=self.snapshot()
        self.assertEqual(self.post('/api/business/finish',{**self.end,'manual_label':'Andersstraat 1, 1234 AB Stad',
                         'confirmation_token':c['confirmation_token']}).status,400)
        self.assertEqual(self.snapshot(),before)

    def test_46_modified_stops_invalidates_token(self):
        self.begin();self.gps();c=self.challenge()
        with app.db() as con:con.execute('UPDATE trip_stops SET odometer=64335')
        before=self.snapshot();self.assertEqual(self.post('/api/business/finish',{**self.end,'confirmation_token':c['confirmation_token']}).status,400)
        self.assertEqual(self.snapshot(),before)

    def test_47_reset_invalidates_tokens_only_in_test_database(self):
        self.begin();self.gps();c=self.challenge()
        app.reset_administration({'confirmation':'RESET','odometer':'64.375'})
        self.assertFalse(control.PENDING)
        before=self.snapshot();self.assertEqual(self.post('/api/business/finish',{**self.end,'confirmation_token':c['confirmation_token']}).status,400)
        self.assertEqual(self.snapshot(),before)

    def test_48_sql_failure_rolls_back_stop_and_audit(self):
        self.begin();self.gps(41);before=self.snapshot();real=app.audit
        def fail(action,*args,**kwargs):
            if action=='finish':raise ValueError('test rollback')
            return real(action,*args,**kwargs)
        with patch.object(app,'audit',fail):self.assertEqual(self.post('/api/business/finish',self.end).status,400)
        self.assertEqual(self.snapshot(),before)


if __name__ == '__main__': unittest.main()
