"""Release 25 persistent rate and direct location selection regressions."""
import base64
import csv
import io
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from test_v2300 import app

ROOT = Path(__file__).parent


class Release2500Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(tmp.cleanup)
        for key, value in {'DATA_DIR': Path(tmp.name), 'DB_PATH': Path(tmp.name)/'test.db', 'OPTIONS_PATH': Path(tmp.name)/'options.json', 'publish_sensors_async': lambda: None}.items():
            p = patch.object(app, key, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()

    def test_default_normalization_persistence_and_restart(self):
        self.assertEqual(app.get_settings()['km_reimbursement_rate'], '0.25')
        for raw, expected in [('0,35','0.35'), ('0.23','0.23'), ('0,25','0.25'), ('0','0')]:
            app.set_settings({'km_reimbursement_rate': raw})
            app.init_db()
            self.assertEqual(app.get_settings()['km_reimbursement_rate'], expected)
            code = f'import app; from pathlib import Path; app.DB_PATH=Path({str(app.DB_PATH)!r}); app.OPTIONS_PATH=Path({str(app.OPTIONS_PATH)!r}); print(app.get_settings()["km_reimbursement_rate"])'
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), expected)

    def test_invalid_rate_is_rejected_and_old_setting_preserved(self):
        for invalid in ['-0.10','abc','NaN','Infinity','-Infinity','']:
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                app.set_settings({'km_reimbursement_rate': invalid})
        self.assertEqual(app.get_settings()['km_reimbursement_rate'], '0.25')

    def test_zero_option_is_preserved(self):
        app.OPTIONS_PATH.write_text('{"km_reimbursement_rate":0}')
        self.assertEqual(Decimal(app.get_settings()['km_reimbursement_rate']), Decimal('0'))

    def test_decimal_round_half_up(self):
        for rate, km, amount in [('0.25','10','2.50'),('0.35','10','3.50'),('0.35','31','10.85'),('0.23','7','1.61'),('0.25','10.5','2.63')]:
            with self.subTest(rate=rate, km=km):
                self.assertEqual(app.pdf_report.calculate_km_reimbursement(km,rate), Decimal(amount))

    def test_pdf_regenerates_rate_rows_total_and_matches_csv(self):
        with app.db() as con:
            for index, km in enumerate([10,31]):
                date=f'2026-09-{index+1:02d}'
                tid=con.execute("INSERT INTO business_trips(started_at,ended_at,status,trip_type) VALUES(?,?,'completed','business')",(date+'T08:00:00+02:00',date+'T09:00:00+02:00')).lastrowid
                for seq,odo,address in [(0,100,app.HOME_ADDRESS),(1,100+km,'Nieuwlandsweg 1A, 7461 VP Rijssen')]:
                    con.execute('INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,latitude,longitude,manual_label,segment_trip_type) VALUES(?,?,?,?,?,?,?,?)',(tid,seq,date+f'T0{8+seq}:00:00+02:00',odo,52+seq/10,6,address,'business'))
        class Handler:
            def __init__(self): self.wfile=io.BytesIO()
            def send_response(self, status): pass
            def send_header(self, key, value): pass
            def end_headers(self): pass
        currency=lambda value: '€ ' + format(Decimal(value), '.2f').replace('.', ',')
        for rate,amounts,total in [('0.25',['2.50','7.75'],'10.25'),('0.35',['3.50','10.85'],'14.35'),('0',['0.00','0.00'],'0.00')]:
            app.set_settings({'km_reimbursement_rate':rate})
            report=app.business_pdf('all',preview=True)
            texts=[e.text or '' for page in report['pages'] for e in ET.fromstring(page).iter()]
            self.assertTrue(any(currency(rate)+' per km' in t for t in texts))
            pdf=base64.b64decode(report['pdf_base64'])
            self.assertTrue(pdf.startswith(b'%PDF'))
            for amount in amounts+[total]:
                self.assertIn(currency(amount),texts)
                self.assertIn(amount.replace('.',',').encode(),pdf)
            handler=Handler();app.Handler.export_business_csv(handler,'all')
            rows=list(csv.DictReader(io.StringIO(handler.wfile.getvalue().decode('utf-8-sig')),delimiter=';'))
            self.assertEqual([r['reimbursement_eur'] for r in {r['rit_id']:r for r in rows}.values()],amounts)
            for row in rows:
                self.assertEqual(Decimal(row['km_reimbursement_rate']),Decimal(rate))
                self.assertIn(currency(row['reimbursement_eur']),texts)

    def test_home_reuses_known_place_without_writes(self):
        self.assertEqual(app.HOME_ADDRESS,'Verenlandweg 4, 7461 AP Rijssen')
        with app.db() as con:
            con.execute('INSERT INTO known_places(name,address,latitude,longitude,created_at,updated_at) VALUES(?,?,?,?,?,?)',('Thuis',app.HOME_ADDRESS,52.3,6.2,app.iso_local(),app.iso_local()))
        with patch.object(app,'google_places_text_search') as search:
            for _ in range(2): self.assertEqual(app.home_destination()['address'],app.HOME_ADDRESS)
        search.assert_not_called()
        with app.db() as con:
            for table,count in [('known_places',1),('business_trips',0),('trip_stops',0),('events',0)]:
                self.assertEqual(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],count)

    def test_ui_controls_and_settings_binding(self):
        html=app.APP_HTML
        if isinstance(html,bytes): html=html.decode()
        for removed in ['Dicteer adres','Dit adres gebruiken','SpeechRecognition','startAddressDictation']:
            self.assertNotIn(removed,html)
        for required in ["$('tripHomeButton').hidden=false",'Zakelijke kilometervergoeding','inputmode="decimal"',"km_reimbursement_rate:$('setKmRate').value","DATA.settings.km_reimbursement_rate??'0.25'"]:
            self.assertIn(required,html)

    def test_location_javascript_without_browser(self):
        result=subprocess.run(['node',str(ROOT/'test_ui_v2500.cjs')],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
