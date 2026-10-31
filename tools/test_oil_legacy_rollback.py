"""Run genuine legacy 34.02 app.py against an isolated oil-enabled database.

CLI: python tools/test_oil_legacy_rollback.py <independent 34.02 app.py>
The old module is checked out from an immutable pre-feature SHA in GitHub CI.
No Home Assistant access, live data, or /data writes.
"""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'rit_tank'))
import app  # noqa: E402
import oil_history  # noqa: E402


def snapshot(database):
    with sqlite3.connect(database) as db:
        return {
            'events': db.execute("SELECT id,type,odometer,liters FROM events ORDER BY id").fetchall(),
            'settings': db.execute("SELECT key,value FROM settings ORDER BY key").fetchall(),
            'business_trips': db.execute("SELECT id,status,purpose FROM business_trips ORDER BY id").fetchall(),
            'trip_stops': db.execute("SELECT id,trip_id,odometer FROM trip_stops ORDER BY id").fetchall(),
            'oil': db.execute("SELECT id,kind,oil_type,amount_ml,odometer FROM oil_maintenance ORDER BY id").fetchall(),
            'integrity': db.execute("PRAGMA integrity_check").fetchone()[0],
        }


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Specify pinned legacy 34.02 app.py path.')
    legacy = Path(sys.argv[1]).resolve()
    if not legacy.is_file() or legacy == (root / 'rit_tank' / 'app.py'):
        raise RuntimeError('A separate checkout of the pinned 34.02 source is mandatory.')
    with tempfile.TemporaryDirectory(prefix='rit-tank-old-runtime-recovery-') as td:
        data = Path(td)
        path = data / 'rit_tank.db'
        (data / 'options.json').write_text(json.dumps({'vehicle_name':'Fictieve testauto'}))
        (data / 'receipts').mkdir()
        (data / 'receipts' / 'fictief.jpg').write_bytes(b'FICTIEF')
        with patch.object(app, 'DATA_DIR', data), \
             patch.object(app, 'DB_PATH', path), \
             patch.object(app, 'OPTIONS_PATH', data / 'options.json'), \
             patch.object(app, 'RECEIPT_DIR', data / 'receipts'):
            app.init_db()
            with app.db() as db:
                db.execute("INSERT INTO settings VALUES('license_plate','TEST-123')")
                db.execute("INSERT INTO events(created_at,type,odometer) "
                           "VALUES('2026-10-01T10:00:00+02:00','odometer',85000)")
                db.execute("INSERT INTO events(created_at,type,odometer,liters,price_per_liter) "
                           "VALUES('2026-10-02T10:00:00+02:00','fuel',85100,32.5,1.85)")
                db.execute("INSERT INTO business_trips(started_at,ended_at,status,purpose) "
                           "VALUES('2026-10-01T10:00:00+02:00','2026-10-01T11:00:00+02:00',"
                           "'completed','Fictieve test')")
                trip=db.execute("SELECT max(id) FROM business_trips").fetchone()[0]
                db.execute("INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer) "
                           "VALUES(?,1,'2026-10-01T10:00:00+02:00',85000)",(trip,))
                oil_history.create_entry(db,{'performed_on':'2026-10-01',
                    'kind':'top_up','oil_type':'5W-30','amount_ml':300,'odometer':85000},
                    '2026-10-31T10:00:00+01:00',registered_odometer=85100)
            before = snapshot(path)
        assert before['integrity']=='ok'
        assert len(before['oil'])==1 and len(before['events'])==2

        # The process imports and runs the actual, pinned old runtime with its own
        # modules, on a fresh, fake path, then exits; it cannot touch /data.
        script = textwrap.dedent('''
            import importlib.util
            import sys
            from pathlib import Path
            old = Path(sys.argv[1]).resolve()
            data = Path(sys.argv[2]).resolve()
            sys.path.insert(0,str(old.parent))
            spec = importlib.util.spec_from_file_location('old_rit_tank',old)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            assert mod.APP_VERSION == '34.02', 'Unexpected legacy version'
            mod.DATA_DIR=data
            mod.DB_PATH=data/'rit_tank.db'
            mod.OPTIONS_PATH=data/'options.json'
            mod.RECEIPT_DIR=data/'receipts'
            mod.init_db()
            assert mod.current_odometer(mod.rows_events()) == 85100
            assert mod.get_settings()['license_plate'] == 'TEST-123'
            print('PINNED_34_02_RUNTIME_READ_OK')
        ''')
        result = subprocess.run([sys.executable,'-B','-c',script,str(legacy),str(data)],
                                cwd=str(legacy.parent), text=True, capture_output=True,
                                timeout=30,check=False)
        if result.returncode:
            raise AssertionError('Old runtime import/init failed:\n'+result.stderr[-3000:])
        assert 'PINNED_34_02_RUNTIME_READ_OK' in result.stdout
        after = snapshot(path)
        assert before == after, f'Old 34.02 runtime changed data: {before.keys()}'
        assert (data/'receipts'/'fictief.jpg').read_bytes()==b'FICTIEF'
        print('PINNED_34_02_ROLLBACK_PRESERVES_OIL_AND_HISTORY PASS')


if __name__=='__main__':
    main()
