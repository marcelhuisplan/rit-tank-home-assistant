"""Release 30: atomic reset, HTTP/CSRF gates and physical odometers."""
import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from test_v2300 import app

OLD = 'Oude Teststraat 99, 1234 AB Teststad'

class Request(app.Handler):
    def __init__(self, path, payload=None, headers=None, ingress=True):
        self.path = path
        self.client_address = ('172.30.32.2' if ingress else '127.0.0.1', 1234)
        raw = json.dumps(payload or {}).encode()
        self.headers = {'X-Ingress-Path': '/api/hassio_ingress/test', 'Content-Type': 'application/json',
                        'Content-Length': str(len(raw)), **(headers or {})}
        self.rfile = io.BytesIO(raw); self.wfile = io.BytesIO(); self.status = None
        self.response_headers = {}
    def send_response(self, status): self.status = status
    def send_header(self, key, value): self.response_headers[key] = value
    def end_headers(self): pass
    def json(self): return json.loads(self.wfile.getvalue())

class Reset3000Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in {'DATA_DIR':root, 'DB_PATH':root/'test.db', 'OPTIONS_PATH':root/'options.json',
                            'RECEIPT_DIR':root/'receipts', 'publish_sensors_async':lambda:None,
                            'RESET_TOKENS':{}, 'google_reverse_geocode':lambda *a:{},
                            'google_place_details':lambda *a:{}}.items():
            p=patch.object(app,name,value);p.start();self.addCleanup(p.stop)
        self.opts={'initial_odometer':50000,'google_places_api_key':'test-only-places-secret',
                   'standalone_password':'test-only-password-1234','timezone':'Europe/Amsterdam',
                   'google_drive_oauth_json':'test-only-drive-secret'}
        app.OPTIONS_PATH.write_text(json.dumps(self.opts));app.init_db()
        app.set_settings({'vehicle_name':'Auto','license_plate':'XX-00-YY','km_reimbursement_rate':'0.35',
                          'driver_name':'Bestuurder','assistant_enabled':'1','assistant_location_entity':'person.test'})
        app.RECEIPT_DIR.mkdir();(app.RECEIPT_DIR/'fuel_2.jpg').write_bytes(b'test receipt')
        stamp=app.iso_local(app.now_local()-timedelta(days=1))
        with app.db() as con:
            con.execute('UPDATE events SET created_at=?',(stamp,))
            con.execute("INSERT INTO events(created_at,type,odometer,liters,price_per_liter,station,receipt_path) VALUES(?,'fuel',50010,30,2,?,'fuel_2.jpg')",(stamp,OLD))
            con.execute("INSERT INTO business_trips(id,started_at,ended_at,status,note) VALUES(1,?,?,'completed',?)",(stamp,stamp,OLD))
            for seq,odo in [(0,50000),(1,50010)]:
                con.execute('INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label,event_id) VALUES(1,?,?,?,?,2)',(seq,stamp,odo,OLD))
            con.execute("INSERT INTO known_places(id,name,category,latitude,longitude,address,created_at,updated_at) VALUES(1,'Thuis','home',52,6,'Thuisstraat 1, 1234 AB Stad',?,?)",(stamp,stamp))
            con.execute("INSERT INTO assistant_arrivals(detected_at,destination_latitude,destination_longitude,destination_label,trip_id) VALUES(?,52,6,?,1)",(stamp,OLD))
            con.execute("INSERT INTO route_memory(origin_known_place_id,destination_latitude,destination_longitude,business_count,last_seen_at) VALUES(1,52,6,3,?)",(stamp,))
            con.execute("INSERT INTO distance_calibration(vehicle,source,gps_km,actual_km,created_at) VALUES('Auto','trip:1',10,11,?)",(stamp,))
            con.execute("INSERT INTO report_addresses VALUES('52,6',?,123,'test')",(OLD,))
            app.audit('update','trip',1,{'old_address':OLD},con=con)
        for key in ['runtime','trip_distance','diagnostic_log','arrival_route:1','last_error']:
            app.assistant_state_set(key,{'old_address':OLD})
        app.assistant_state_set('backup_status',{'last_ok_at':stamp})
        self.before=self.snapshot();self.settings=app.get_settings()

    def snapshot(self):
        with app.db() as con:
            names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {n:[tuple(r) for r in con.execute(f'SELECT * FROM {n} ORDER BY rowid')] for n in names}
    def token(self,headers=None):
        h=Request('/api/administration/reset-token',headers=headers);h.do_GET()
        self.assertEqual(h.status,200);return h.json()['token']
    def reset(self,payload=None,headers=None,token=True):
        hdr={'X-Reset-Token':self.token()} if token else {}
        h=Request('/api/administration/reset',payload if payload is not None else {'confirmation':'RESET','odometer':'64.603'},{**hdr,**(headers or {})})
        h.do_POST();return h
    def assert_clean(self):
        with app.db() as con:
            for table in ['business_trips','trip_stops','assistant_arrivals','route_memory','distance_calibration','report_addresses']:
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0,table)
            rows=list(con.execute('SELECT * FROM events'));self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['odometer'],64603);self.assertEqual(rows[0]['source_kind'],'administration_baseline')
            self.assertEqual(list(con.execute('PRAGMA foreign_key_check')),[])
            self.assertEqual([r[0] for r in con.execute('SELECT key FROM assistant_state')],['backup_status'])
        self.assertNotIn(OLD,str(self.snapshot()))

    def test_reset_removes_administrative_tables_without_orphans(self):
        self.assertEqual(self.reset().status,200);self.assert_clean()
    def test_missing_confirmation_refused(self):
        self.assertEqual(self.reset({'odometer':64603}).status,400);self.assertEqual(self.snapshot(),self.before)
    def test_confirmation_is_exact(self):
        for value in ['reset','RESET ',' RESET',True,None]:
            self.assertEqual(self.reset({'confirmation':value,'odometer':64603}).status,400)
        self.assertEqual(self.snapshot(),self.before)
    def test_valid_odometer_formats(self):
        for value,expected in [(64603,64603),('64.603',64603),('64603,5',64603.5),('64.603,5',64603.5),('0',0),('999.999',999999)]:
            with self.subTest(value=value):self.assertEqual(app.reset_odometer(value),expected)
    def test_invalid_odometer_refused_without_backup(self):
        for value in [None,'','-1',-1,'NaN','Infinity',float('inf'),float('nan'),True,'64.60.3','1e5',1000000,'64603,55',[],{}]:
            with self.subTest(value=value):self.assertEqual(self.reset({'confirmation':'RESET','odometer':value}).status,400)
        self.assertEqual(self.snapshot(),self.before);self.assertFalse((app.DATA_DIR/'administration_backups').exists())
    def test_get_and_delete_cannot_reset(self):
        for method in ['do_GET','do_DELETE']:
            h=Request('/api/administration/reset');getattr(h,method)();self.assertIn(h.status,[404,405])
        self.assertEqual(self.snapshot(),self.before)
    def test_wrong_route_cannot_reset(self):
        h=Request('/api/administration/resett',{'confirmation':'RESET','odometer':64603});h.do_POST()
        self.assertEqual(h.status,404);self.assertEqual(self.snapshot(),self.before)
    def test_unauthorized_access_refused(self):
        for path,method in [('/api/administration/reset-token','do_GET'),('/api/administration/reset','do_POST')]:
            h=Request(path,ingress=False);getattr(h,method)();self.assertEqual(h.status,426)
        self.assertEqual(self.snapshot(),self.before)
    def test_csrf_missing_wrong_cross_site_and_wrong_binding_refused(self):
        for headers in [{},{'X-Reset-Token':'wrong'},{'X-Reset-Token':self.token(),'Sec-Fetch-Site':'cross-site'},
                        {'X-Reset-Token':self.token(),'Content-Type':'text/plain'},
                        {'X-Reset-Token':self.token(),'Cookie':'different-session'}]:
            self.assertEqual(self.reset(headers=headers,token=False).status,403)
        self.assertEqual(self.snapshot(),self.before)
    def test_expired_token_refused(self):
        token=self.token();binding,_=app.RESET_TOKENS[token];app.RESET_TOKENS[token]=(binding,0)
        self.assertEqual(self.reset(headers={'X-Reset-Token':token},token=False).status,403)
        self.assertEqual(self.snapshot(),self.before)
    def test_same_token_cannot_reset_twice(self):
        headers={'X-Reset-Token':self.token()};self.assertEqual(self.reset(headers=headers,token=False).status,200)
        before=self.snapshot();self.assertEqual(self.reset(headers=headers,token=False).status,403)
        self.assertEqual(self.snapshot(),before)
    def test_new_explicit_reset_safe_and_invalidates_other_dialogs(self):
        old=self.token();self.assertEqual(self.reset().status,200)
        self.assertEqual(self.reset(headers={'X-Reset-Token':old},token=False).status,403)
        self.assertEqual(self.reset().status,200);self.assert_clean()
    def test_settings_home_rate_options_session_secret_preserved(self):
        key=app._session_key();raw=app.OPTIONS_PATH.read_bytes();self.assertEqual(self.reset().status,200)
        settings=app.get_settings()
        for k,v in self.settings.items():self.assertEqual(settings[k],v,k)
        self.assertEqual(app.OPTIONS_PATH.read_bytes(),raw);self.assertEqual(app._session_key(),key)
        self.assertEqual(self.snapshot()['known_places'],self.before['known_places'])
        self.assertEqual(settings['km_reimbursement_rate'],'0.35')
    def test_private_backup_contains_database_and_receipts(self):
        self.assertEqual(self.reset().status,200)
        root=app.DATA_DIR/'administration_backups';directory=next(root.iterdir());database=directory/'rit_tank.db'
        self.assertEqual(root.stat().st_mode & 0o777,0o700);self.assertEqual(directory.stat().st_mode & 0o777,0o700)
        self.assertEqual(database.stat().st_mode & 0o777,0o600)
        with sqlite3.connect(database) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM trip_stops').fetchone()[0],2)
        self.assertEqual((directory/'receipts'/'fuel_2.jpg').read_bytes(),b'test receipt');self.assertFalse(app.RECEIPT_DIR.exists())
        for path in ['/administration_backups/'+directory.name+'/rit_tank.db','/api/receipt/2']:
            h=Request(path);h.do_GET();self.assertEqual(h.status,404)
    def test_backup_failure_aborts(self):
        with patch.object(app,'_reset_backup',side_effect=OSError('sensitive database path')):result=self.reset()
        self.assertEqual(result.status,400);self.assertEqual(self.snapshot(),self.before)
        self.assertTrue((app.RECEIPT_DIR/'fuel_2.jpg').exists());self.assertNotIn('sensitive',str(result.json()))
    def test_rollback_restores_every_table_and_receipt(self):
        with patch.object(app,'audit',side_effect=sqlite3.OperationalError('private detail')):self.assertEqual(self.reset().status,400)
        self.assertEqual(self.snapshot(),self.before);self.assertEqual((app.RECEIPT_DIR/'fuel_2.jpg').read_bytes(),b'test receipt')
        self.assertFalse((app.DATA_DIR/'administration-reset.pending').exists())
    def test_commit_failure_rolls_back(self):
        original=app.db
        @contextlib.contextmanager
        def failing_db():
            with original() as con:
                yield con
                if con.in_transaction and con.execute("SELECT 1 FROM settings WHERE key='administration_reset_id'").fetchone():
                    raise sqlite3.OperationalError('simulated commit failure')
        with patch.object(app,'db',failing_db):self.assertEqual(self.reset().status,400)
        self.assertEqual(self.snapshot(),self.before);self.assertTrue(app.RECEIPT_DIR.exists())
    def test_interrupted_uncommitted_move_recovers(self):
        with app.db() as con:con.execute('BEGIN IMMEDIATE');backup=app._reset_backup(con)
        (app.DATA_DIR/'administration-reset.pending').write_text(backup.name);app.RECEIPT_DIR.rename(backup/'receipts')
        app._recover_reset_receipts();self.assertTrue((app.RECEIPT_DIR/'fuel_2.jpg').exists());self.assertEqual(self.snapshot(),self.before)
    def test_interrupted_committed_reset_keeps_receipts_archived(self):
        self.assertEqual(self.reset().status,200);name=app.get_settings()['administration_reset_id']
        (app.DATA_DIR/'administration-reset.pending').write_text(name)
        app._recover_reset_receipts();self.assertFalse(app.RECEIPT_DIR.exists());self.assert_clean()
    def test_logs_and_audit_exclude_old_details_and_secrets(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(out):self.assertEqual(self.reset().status,200)
        self.assertEqual(out.getvalue(),'');audit=app.recent_audit();self.assertEqual(len(audit),1)
        self.assertEqual(audit[0]['details_obj'],{'odometer':64603})
        for value in [OLD,'test-only-places-secret','test-only-drive-secret']:
            self.assertNotIn(value,str(audit)+out.getvalue())
    def test_stats_history_odometer_after_reset(self):
        self.assertEqual(self.reset().status,200);result=app.summary()
        self.assertEqual(result['current_odometer'],64603);self.assertEqual(result['administration_km'],0)
        self.assertEqual(result['total_events'],0);self.assertEqual(result['recent'],[]);self.assertEqual(result['station_stats'],[])
        self.assertEqual(result['business']['recent_trips'],[]);self.assertIsNone(result['business']['active_trip'])
        for period in ['day','week','month','year']:
            for key in ['km','liters','cost','fuel_count']:self.assertEqual(app.stats_for_period(period)[key],0)
        self.assertEqual(result['business']['calibration']['samples'],0)
    def test_empty_reports_pdf_csv_no_deleted_trips(self):
        self.assertEqual(self.reset().status,200);self.assertEqual(app.business_report('all')['rows'],[])
        for path in ['/api/business.pdf?period=all','/api/export/pdf?period=all','/api/business/pdf-preview?period=all','/api/business.csv?period=all','/api/export.csv']:
            h=Request(path);h.do_GET();self.assertEqual(h.status,200,(path,h.wfile.getvalue()[:200]))
            self.assertNotIn(OLD.encode(),h.wfile.getvalue());self.assertNotIn(b'50010',h.wfile.getvalue())
    def point(self,odo,minutes):
        return {'odometer':odo,'created_at':app.iso_local(app.now_local()+timedelta(minutes=minutes)),
                'latitude':52,'longitude':6,'manual_label':'Nieuwstraat 1, 1234 AB Stad',
                'segment_trip_type':'business','trip_type':'business'}
    def test_first_and_next_trip_physical_baseline_and_exports(self):
        self.assertEqual(self.reset().status,200)
        app.start_business_trip(self.point(64603,1));app.add_business_stop(self.point(64613,31),finish=True)
        app.start_business_trip(self.point(64613,32));app.add_business_stop(self.point(64618,62),finish=True)
        report=app.business_report('all');self.assertEqual([r['km'] for r in report['rows']],[10,5])
        self.assertEqual(app.summary()['administration_km'],15);self.assertEqual(app.current_odometer(app.rows_events()),64618)
        for path in ['/api/business.pdf?period=all','/api/business.csv?period=all']:
            h=Request(path);h.do_GET();self.assertEqual(h.status,200)
            for n in [64603,64613,64618]:self.assertIn((f'{n:,}'.replace(',','.') if '.pdf' in path else str(n)).encode(),h.wfile.getvalue())
        self.assertTrue(all(r['delta_km']>=0 for r in app.rows_events()))
    def test_earlier_below_baseline_and_nan_refused(self):
        self.reset()
        for point in [self.point(64602,1),self.point(64603,-5),self.point(float('nan'),1)]:
            with self.assertRaises(ValueError):app.start_business_trip(point)
        self.assert_clean()
    def test_correction_cannot_move_trip_behind_baseline(self):
        self.reset();app.start_business_trip(self.point(64603,1))
        app.add_business_stop(self.point(64613,31),finish=True)
        trip=app.business_report('all')['trips'][0]
        before=self.snapshot()
        for change in [{'odometer':64602},{'created_at':self.point(64603,-10)['created_at']}]:
            with self.assertRaises(ValueError):
                app.edit_business_trip(trip['id'],{'stops':[{'id':trip['stops'][0]['id'],**change}]})
            self.assertEqual(self.snapshot(),before)

    def test_standalone_session_and_origin_required(self):
        opts={**self.opts,'standalone_enabled':True};app.OPTIONS_PATH.write_text(json.dumps(opts))
        cookie=app.SESSION_COOKIE+'='+app.make_session_token(opts['standalone_password'],1)
        headers={'X-Forwarded-Proto':'https','Host':'rit.example','Origin':'https://rit.example','Cookie':cookie}
        h=Request('/api/administration/reset-token',headers=headers,ingress=False);h.do_GET()
        self.assertEqual(h.status,200);headers['X-Reset-Token']=h.json()['token']
        for origin in ['','https://evil.example']:
            bad=Request('/api/administration/reset',{'confirmation':'RESET','odometer':64603},{**headers,'Origin':origin},ingress=False)
            bad.do_POST();self.assertEqual(bad.status,403)
        bad=Request('/api/administration/reset',headers={**headers,'Cookie':''},ingress=False)
        bad.do_POST();self.assertEqual(bad.status,401)
        valid=Request('/api/administration/reset',{'confirmation':'RESET','odometer':64603},headers,ingress=False)
        valid.do_POST();self.assertEqual(valid.status,200)

    def test_baseline_zero_is_explicit_and_survives_restart(self):
        self.assertEqual(self.reset({'confirmation':'RESET','odometer':0}).status,200)
        app.init_db();self.assertEqual(app.current_odometer(app.rows_events()),0)
        self.assertEqual(app.summary()['administration_km'],0)

    def test_old_receipt_or_notification_ids_are_not_reused(self):
        self.reset();app.start_business_trip(self.point(64603,1))
        self.assertGreater(app.active_business_trip()['id'],1)
        with self.assertRaises(ValueError):app.confirm_assistant_arrival(1,'business','notification')
        self.assertTrue(all(r['id']>2 for r in app.rows_events()))

    def test_reset_without_receipts_directory(self):
        (app.RECEIPT_DIR/'fuel_2.jpg').unlink();app.RECEIPT_DIR.rmdir()
        self.assertEqual(self.reset().status,200);self.assert_clean()

    def test_receipt_move_failure_rolls_back_without_deletion(self):
        original=Path.rename
        def fail_receipts(path,target):
            if path==app.RECEIPT_DIR:raise OSError('simulated move failure')
            return original(path,target)
        with patch.object(Path,'rename',fail_receipts):self.assertEqual(self.reset().status,400)
        self.assertEqual(self.snapshot(),self.before);self.assertTrue(app.RECEIPT_DIR.exists())

    def test_ui_flow(self):
        import subprocess
        result=subprocess.run(['node',str(Path(__file__).with_name('test_ui_v3000.cjs'))],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_baseline_cannot_be_deleted(self):
        self.reset()
        with self.assertRaises(ValueError):app.delete_event(app.rows_events()[0]['id'])
        self.assert_clean()
    def test_restart_does_not_reapply_old_initial_odometer(self):
        self.reset();app.init_db();self.assert_clean()
    def test_fuel_entry_works_after_reset(self):
        self.reset();result=app.add_fuel({**self.point(64610,1),'liters':10,'price_per_liter':2,'station':'Nieuw'})
        self.assertTrue(result['ok']);self.assertEqual(app.stats_for_period('day')['fuel_count'],1)
    def test_reset_waits_for_background_processing(self):
        entered=threading.Event();release=threading.Event();done=threading.Event()
        def processing(*args,**kwargs):entered.set();release.wait(3)
        with patch.object(app.assistant,'_process_assistant_location',processing):
            worker=threading.Thread(target=app._process_assistant_location,args=({},{}));worker.start();self.assertTrue(entered.wait(1))
            def reset():app.reset_administration({'confirmation':'RESET','odometer':64603});done.set()
            thread=threading.Thread(target=reset);thread.start();self.assertFalse(done.wait(.05))
            release.set();worker.join(3);thread.join(3)
        self.assertTrue(done.is_set());self.assert_clean()

if __name__=='__main__':unittest.main()
