"""Private, synthetic-only Rit & Tank oil preview server.

This is NOT the add-on production entrypoint. It must run in a dedicated
container with its own empty /data volume, never mounted to Home Assistant.
The service deliberately disables HA background threads and sensor publishing.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from http.server import ThreadingHTTPServer
from unittest.mock import patch

SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'rit_tank'
if not (SOURCE_ROOT / 'app.py').exists():
    SOURCE_ROOT = Path('/app')
sys.path.insert(0, str(SOURCE_ROOT))
import app
import oil_history

MARKER = '.synthetic-oil-preview-only'
NAME = 'Fictieve testauto — Olieproef'


def fake_pdf() -> bytes:
    """Create a valid one-page PDF with synthetic text; no external service."""
    text = b'BT /F1 14 Tf 35 190 Td (FICTIEVE TANKBON - GEEN ECHTE GEGEVENS) Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 500 250] '
        b'/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length ' + str(len(text)).encode() + b' >>\nstream\n' + text + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    data = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 ' + str(len(offsets)).encode() + b'\n0000000000 65535 f \n'
    for offset in offsets[1:]:
        data += f'{offset:010d} 00000 n \n'.encode()
    data += (b'trailer\n<< /Size ' + str(len(offsets)).encode()
             + b' /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n')
    return data


def prepare_data(folder: Path, password: str) -> dict[str, object]:
    """Fail closed on any existing unmarked data. Idempotent for this preview."""
    if not isinstance(password, str) or len(password) < 20 or len(password) > 200:
        raise ValueError('Een uniek testwachtwoord van minimaal 20 tekens is vereist.')
    folder = Path(folder)
    if folder.exists() and not folder.is_dir():
        raise ValueError('Ongeldige preview-datamap.')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker = folder / MARKER
    if any(folder.iterdir()) and not marker.is_file():
        raise RuntimeError('Testvolume is niet leeg of niet als preview gemarkeerd. NIETS wijzigen.')
    dbfile = folder / 'rit_tank.db'
    optionsfile = folder / 'options.json'
    if marker.exists():
        if not dbfile.is_file() or not optionsfile.is_file():
            raise RuntimeError('Onvolledige preview: herstel het aparte testvolume, niet overschrijven.')
        opts = json.loads(optionsfile.read_text(encoding='utf-8'))
        if opts.get('standalone_password') != password or opts.get('standalone_enabled') is not True:
            raise RuntimeError('Previewwachtwoord/configuratie komt niet overeen. Stop zonder wijziging.')
        with sqlite3.connect(dbfile) as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Previewdatabase heeft integriteitsfouten.')
        return {'reused': True, 'database': str(dbfile)}

    opts = dict(app.DEFAULT_OPTIONS)
    opts.update({
        'vehicle_name': NAME, 'fuel_type': 'Benzine',
        'timezone': 'Europe/Amsterdam', 'initial_odometer': 0,
        'standalone_enabled': True, 'standalone_password': password,
        'standalone_session_days': 1,
        'google_places_api_key': '',
        'google_drive_enabled': False, 'google_drive_folder_id': '',
        'google_drive_oauth_json': '', 'google_drive_service_account_json': '',
        'assistant_enabled': '0', 'assistant_sync_zones': '0',
        'assistant_location_entity': '', 'assistant_notify_service': '',
    })
    with patch.object(app, 'DATA_DIR', folder), \
         patch.object(app, 'DB_PATH', dbfile), \
         patch.object(app, 'OPTIONS_PATH', optionsfile), \
         patch.object(app, 'RECEIPT_DIR', folder / 'receipts'):
        optionsfile.write_text(json.dumps(opts, ensure_ascii=False), encoding='utf-8')
        optionsfile.chmod(0o600)
        app.init_db()
        with app.db() as con:
            con.execute('INSERT INTO events(created_at,type,odometer,note) VALUES (?,?,?,?)',
                        ('2026-04-15T08:00:00+02:00', 'odometer', 82000,
                         'Fictieve beginstand'))
            con.execute('INSERT INTO events(created_at,type,odometer,liters,price_per_liter,station) '
                        'VALUES (?,?,?,?,?,?)',
                        ('2026-09-20T14:00:00+02:00', 'fuel', 84750, 35.40, 1.899,
                         'Fictieve testpomp'))
            con.execute('INSERT INTO events(created_at,type,odometer,note) VALUES (?,?,?,?)',
                        ('2026-10-20T08:00:00+02:00', 'odometer', 85000,
                         'Fictieve voertuigstand'))
            con.execute("INSERT INTO business_trips(started_at,ended_at,status,purpose,client) "
                        "VALUES(?,?,'completed',?,?)",
                        ('2026-09-15T08:00:00+02:00', '2026-09-15T09:00:00+02:00',
                         'Fictief werkbezoek', 'Testklant'))
            trip_id = con.execute('SELECT last_insert_rowid()').fetchone()[0]
            con.execute('INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,'
                        'manual_label) VALUES (?,?,?,?,?)',
                        (trip_id, 1, '2026-09-15T08:00:00+02:00', 84300,
                         'Fictieve vertrekplek'))
            con.execute('INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,'
                        'manual_label) VALUES (?,?,?,?,?)',
                        (trip_id, 2, '2026-09-15T09:00:00+02:00', 84330,
                         'Fictieve afspraak'))
            oil_history.create_entry(con, {
                'performed_on': '2026-05-10', 'kind': 'change', 'oil_type': '0W-20',
                'amount_ml': 4200, 'odometer': 82000,
            }, '2026-05-10T12:00:00+02:00', registered_odometer=85000)
            pdf = fake_pdf()
            receipt_name = 'fictieve-bon.pdf'
            folder.joinpath('receipt_archive').mkdir(mode=0o700, exist_ok=True)
            folder.joinpath('receipt_archive', receipt_name).write_bytes(pdf)
            con.execute('INSERT INTO receipt_archive(stored_name,filename,sha256,created_at) '
                        'VALUES (?,?,?,?)',
                        (receipt_name, 'Fictieve tankbon.pdf', hashlib.sha256(pdf).hexdigest(),
                         '2026-09-20T14:00:00+02:00'))
        # Mark only after all entries and fixtures were committed successfully.
        marker.write_text('synthetic-oil-preview-v1\n', encoding='ascii')
        marker.chmod(0o600)
        return {'reused': False, 'database': str(dbfile)}


def serve() -> None:
    password = os.environ.pop('RIT_TANK_PREVIEW_PASSWORD', '')
    folder = Path('/data')
    prepare_data(folder, password)
    # Deliberately no app.main(): avoid HA sensors, GPS tracking, zones,
    # notification subscriptions and scheduled Drive backups.
    app.DATA_DIR = folder
    app.DB_PATH = folder / 'rit_tank.db'
    app.OPTIONS_PATH = folder / 'options.json'
    app.RECEIPT_DIR = folder / 'receipts'
    app.publish_sensors_async = lambda: None
    app.start_assistant_threads = lambda: None
    print('Fictieve oliepreview gereed; alleen via aparte HTTPS-proxy bereikbaar.', flush=True)
    ThreadingHTTPServer(('0.0.0.0', app.PORT), app.Handler).serve_forever()


if __name__ == '__main__':
    serve()
