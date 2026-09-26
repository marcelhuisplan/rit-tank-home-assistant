"""Release 26: visible PDF legs and fresh persisted reimbursement rates."""
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
DISTANCES = [10, 10, 2, 1, 31, 3, 10, 122, 1, 2, 106, 12, 61, 0, 0]
GROUPS = [DISTANCES[i:i + 2] for i in range(0, 10, 2)] + [[km] for km in DISTANCES[10:]]


class CaptureHandler:
    def __init__(self):
        self.wfile = io.BytesIO()

    def send_response(self, status):
        pass

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass


def money(value):
    return '€ ' + format(Decimal(value), '.2f').replace('.', ',')


def text_nodes(report):
    return [node for page in report['pages'] for node in ET.fromstring(page).iter()
            if node.tag.endswith('}text')]


class Pdf2600Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(tmp.cleanup)
        for key, value in {'DATA_DIR': Path(tmp.name), 'DB_PATH': Path(tmp.name) / 'test.db',
                           'OPTIONS_PATH': Path(tmp.name) / 'options.json',
                           'publish_sensors_async': lambda: None}.items():
            p = patch.object(app, key, value)
            p.start()
            self.addCleanup(p.stop)
        app.init_db()
        with app.db() as con:
            for index, legs in enumerate(GROUPS):
                date = f'2026-09-{index + 1:02d}'
                tid = con.execute("INSERT INTO business_trips(started_at,ended_at,status,trip_type) VALUES(?,?,'completed','business')",
                                  (date + 'T08:00:00+02:00', date + 'T10:00:00+02:00')).lastrowid
                odo = 1000
                for seq, distance in enumerate([0] + legs):
                    odo += distance
                    con.execute('INSERT INTO trip_stops(trip_id,sequence_no,created_at,odometer,manual_label,segment_trip_type) VALUES(?,?,?,?,?,?)',
                                (tid, seq, date + f'T{8 + seq:02d}:00:00+02:00', odo,
                                 'Verenlandweg 4, 7461 AP Rijssen', 'business'))

    def report(self, rate):
        app.set_settings({'km_reimbursement_rate': rate})
        return app.business_pdf('all', preview=True)

    def assert_report(self, report, rate, total):
        nodes = text_nodes(report)
        texts = [node.text for node in nodes]
        self.assertIn('371,0 km', texts)
        self.assertIn(money(total), texts)
        self.assertTrue(any(money(rate) + ' per km' in (t or '') for t in texts))
        self.assertEqual(texts[texts.index('AANTAL RITTEN') + 1], '15')
        self.assertEqual([n.text for n in nodes if n.get('x') == '38' and n.get('font-size') == '9'],
                         [str(i) for i in range(1, 16)])
        amounts = [n.text for n in nodes if n.get('font-size') == '9.5' and (n.text or '').startswith('€ ')]
        self.assertEqual(amounts, [money(app.pdf_report.calculate_km_reimbursement(km, rate)) for km in DISTANCES])
        self.assertEqual(texts.count('0,0 km'), 2)
        self.assertGreater(len(report['pages']), 1)
        pdf = base64.b64decode(report['pdf_base64'])
        for expected in ['371,0 km', money(total), money(rate) + ' per km', '(15) Tj']:
            self.assertIn(expected.encode('cp1252'), pdf)

    def test_ten_parents_fifteen_visible_rows_at_025(self):
        self.assertEqual(len(app.business_trips_raw()), 10)
        self.assert_report(self.report('0.25'), '0.25', '92.75')

    def test_rate_changes_without_restart_and_reads_once_per_pdf(self):
        self.assert_report(self.report('0.25'), '0.25', '92.75')
        app.set_settings({'km_reimbursement_rate': '0,35'})
        with patch.object(app, 'get_settings', wraps=app.get_settings) as settings:
            report = app.business_pdf('all', preview=True)
        settings.assert_called_once_with()
        self.assert_report(report, '0.35', '129.85')

    def test_persisted_rate_in_new_process_generates_pdf(self):
        app.set_settings({'km_reimbursement_rate': '0.35'})
        app.init_db()
        self.assertEqual(app.get_settings()['km_reimbursement_rate'], '0.35')
        code = f'''import app
from pathlib import Path
app.DB_PATH = Path({str(app.DB_PATH)!r})
app.OPTIONS_PATH = Path({str(app.OPTIONS_PATH)!r})
assert app.get_settings()['km_reimbursement_rate'] == '0.35'
pdf, _ = app.business_pdf('all')
assert b'0,35 per km' in pdf and b'129,85' in pdf and b'(15) Tj' in pdf
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_pdf_csv_same_trip_rate_and_reimbursement(self):
        for rate in ['0.25', '0.35', '0']:
            self.report(rate)
            handler = CaptureHandler()
            app.Handler.export_business_csv(handler, 'all', allow_warnings=True)
            rows = list(csv.DictReader(io.StringIO(handler.wfile.getvalue().decode('utf-8-sig')), delimiter=';'))
            for row in rows:
                self.assertEqual(Decimal(row['km_reimbursement_rate']), Decimal(rate))
            unique = list({row['rit_id']: row for row in rows}.values())
            expected = [sum((app.pdf_report.calculate_km_reimbursement(km, rate) for km in legs), Decimal('0')) for legs in GROUPS]
            # CSV repeats a parent-trip amount on stop rows; PDF prints its legs.
            self.assertCountEqual([Decimal(row['reimbursement_eur']) for row in unique], expected)
            for row in unique:
                self.assertEqual(Decimal(row['reimbursement_eur']),
                                 app.pdf_report.calculate_km_reimbursement(row['totaal_km'], rate))

    def test_zero_rate_stays_zero(self):
        self.assert_report(self.report('0'), '0', '0')

    def test_missing_empty_comma_and_invalid_rate(self):
        helper = app.pdf_report.report_km_rate
        for settings in [{}, {'km_reimbursement_rate': None}, {'km_reimbursement_rate': ''}]:
            self.assertEqual(helper(settings), Decimal('0.25'))
        for raw in [0, '0', '0,35', '0.35']:
            self.assertEqual(helper({'km_reimbursement_rate': raw}), Decimal(str(raw).replace(',', '.')))
        for raw in ['abc', 'NaN', 'Infinity', '-0.1']:
            with self.assertRaises(ValueError):
                helper({'km_reimbursement_rate': raw})

    def test_summary_uses_rows_even_if_parent_total_differs(self):
        enrich = app.enrich_business_trip
        def different_total(*args, **kwargs):
            trip = enrich(*args, **kwargs)
            trip['km'] = 999
            return trip
        with patch.object(app, 'enrich_business_trip', side_effect=different_total):
            self.assert_report(self.report('0.35'), '0.35', '129.85')

    def test_round_half_up_total_is_sum_of_visible_amounts(self):
        with app.db() as con:
            con.execute('DELETE FROM trip_stops WHERE trip_id != 1')
            con.execute('DELETE FROM business_trips WHERE id != 1')
            con.execute('UPDATE trip_stops SET odometer = 1000 + sequence_no * 0.1')
        texts = [n.text for n in text_nodes(self.report('0.25'))]
        self.assertEqual(texts.count('€ 0,03'), 2)
        self.assertIn('€ 0,06', texts)
        self.assertIn('0,2 km', texts)

    def test_empty_and_single_stop_reports(self):
        with app.db() as con:
            con.execute('DELETE FROM trip_stops WHERE trip_id != 1 OR sequence_no != 0')
            con.execute('DELETE FROM business_trips WHERE id != 1')
        texts = [n.text for n in text_nodes(self.report('0.35'))]
        self.assertEqual(texts[texts.index('AANTAL RITTEN') + 1], '1')
        with app.db() as con:
            con.execute('DELETE FROM trip_stops')
        texts = [n.text for n in text_nodes(self.report('0.35'))]
        self.assertEqual(texts[texts.index('AANTAL RITTEN') + 1], '0')
        self.assertIn('Geen ritten in deze periode.', texts)
