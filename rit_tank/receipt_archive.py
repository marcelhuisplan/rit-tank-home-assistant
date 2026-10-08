"""PDF tankbon archive: deliberately independent from fuel and trip registrations."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import secrets
import sqlite3
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

MAX_FILE = 12 * 1024 * 1024
MAX_TOTAL = 18 * 1024 * 1024
MAX_PDF = 24 * 1024 * 1024
MAX_PAGES = 30


def init_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS receipt_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stored_name TEXT NOT NULL UNIQUE,
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_receipt_archive_hash ON receipt_archive(sha256)")


def decode_file(item: dict) -> tuple[bytes, str]:
    data_url = str(item.get('data_url') or '')
    match = re.fullmatch(r'data:(image/(?:jpeg|png|webp)|application/pdf);base64,([A-Za-z0-9+/=]+)', data_url, re.I)
    if not match:
        raise ValueError('Kies een JPEG-, PNG-, WebP-afbeelding of PDF. HEIC wordt op je iPhone eerst omgezet.')
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError('Bestand kon niet worden gelezen.') from None
    if not raw or len(raw) > MAX_FILE:
        raise ValueError('Bestand is leeg of groter dan 12 MB.')
    mime = match.group(1).lower()
    if mime == 'application/pdf':
        if not raw.lstrip().startswith(b'%PDF-'):
            raise ValueError('Dit is geen geldig PDF-bestand.')
    else:
        signatures = {'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
                      'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                      'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}
        if not signatures[mime]:
            raise ValueError('De afbeelding komt niet overeen met het bestandstype.')
    return raw, mime


def _run(command: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Het bestand kon niet veilig worden verwerkt.') from None


def pdf_details(raw: bytes) -> tuple[int, str]:
    if len(raw) > MAX_PDF or not raw.lstrip().startswith(b'%PDF-'):
        raise ValueError('Geen geldige PDF of bestand te groot.')
    with tempfile.TemporaryDirectory(prefix='rit_receipt_') as folder:
        path = Path(folder) / 'bon.pdf'
        path.write_bytes(raw)
        info = _run(['pdfinfo', str(path)])
        if info.returncode:
            raise ValueError('PDF is beschadigd, versleuteld of niet leesbaar.')
        pages = re.search(rb'^Pages:\s*(\d+)\s*$', info.stdout, re.M)
        if not pages or not 1 <= int(pages.group(1)) <= MAX_PAGES:
            raise ValueError('PDF moet 1 tot 30 leesbare pagina’s bevatten.')
        result = _run(['pdftotext', '-f', '1', '-l', '2', '-layout', str(path), '-'])
        text = result.stdout.decode('utf-8', errors='replace') if result.returncode == 0 else ''
        if not text.strip():
            prefix = Path(folder) / 'scan'
            render = _run(['pdftoppm', '-f', '1', '-l', '1', '-scale-to', '1800',
                           '-singlefile', '-jpeg', str(path), str(prefix)], timeout=25)
            if render.returncode == 0:
                text = _ocr(prefix.with_suffix('.jpg'))
        return int(pages.group(1)), text


def _ocr(image: Path) -> str:
    result = _run(['tesseract', str(image), 'stdout', '-l', 'nld+eng', '--psm', '6'], timeout=25)
    return result.stdout.decode('utf-8', errors='replace') if result.returncode == 0 else ''


def images_to_pdf(images: list[bytes]) -> tuple[bytes, str]:
    from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
    pages = []
    text = ''
    try:
        for index, raw in enumerate(images):
            try:
                with Image.open(io.BytesIO(raw)) as opened:
                    if opened.width * opened.height > 45_000_000:
                        raise ValueError('Afbeelding heeft te veel pixels.')
                    image = ImageOps.exif_transpose(opened).convert('RGB')
            except (UnidentifiedImageError, OSError):
                raise ValueError('De foto is beschadigd of niet leesbaar.') from None
            image = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
            image = image.filter(ImageFilter.UnsharpMask(radius=1.1, percent=105, threshold=3))
            image.thumbnail((1550, 2190), Image.Resampling.LANCZOS)
            page = Image.new('RGB', (1654, 2339), 'white')
            page.paste(image.convert('RGB'), ((1654 - image.width)//2, (2339-image.height)//2))
            pages.append(page)
            if index == 0:
                with tempfile.TemporaryDirectory(prefix='rit_receipt_') as folder:
                    temp_path = Path(folder) / 'ocr.png'
                    image.save(temp_path)
                    text = _ocr(temp_path)
        output = io.BytesIO()
        pages[0].save(output, format='PDF', save_all=True, append_images=pages[1:],
                      resolution=150.0, title='Tankbon')
        return output.getvalue(), text
    finally:
        for page in pages:
            page.close()


def _unique(values: list[str]) -> str:
    choices = {x.strip() for x in values if x.strip()}
    return next(iter(choices)) if len(choices) == 1 else ''


def recognize_filename_fields(text: str) -> dict[str, str]:
    """Conservative OCR: no inferred prices, liters or mileage."""
    lines = [re.sub(r'\s+', ' ', x).strip() for x in text.splitlines() if x.strip()]
    dates, totals, places, stations = [], [], [], []
    brands = ('totalenergies', 'shell', 'texaco', 'esso', 'tango', 'tinq',
              'avia', 'argos', 'gulf', 'q8', 'bp', 'ok tankstation')
    for line in lines:
        for match in re.finditer(r'\b(20\d\d)[-/](\d{1,2})[-/](\d{1,2})\b', line):
            try:
                dates.append(datetime(*map(int, match.groups())).date().isoformat())
            except ValueError:
                pass
        for match in re.finditer(r'\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d\d)\b', line):
            try:
                d, m, y = map(int, match.groups())
                dates.append(datetime(y, m, d).date().isoformat())
            except ValueError:
                pass
        if re.search(r'\b(?:eindtotaal|totaal|total|te betalen|betaald bedrag)\b', line, re.I):
            prices = re.findall(r'(?<!\d)(\d{1,4}[.,]\d{2})(?!\d)', line)
            if len(prices) == 1:
                totals.append(prices[0].replace('.', ','))
        explicit = re.search(r'\b(?:plaats|vestiging|locatie)\s*:\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ \-]{2,40})$', line, re.I)
        postal = re.search(r'\b\d{4}\s?[A-Z]{2}\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ \-]{2,35})$', line)
        if explicit or postal:
            places.append((explicit or postal).group(1).strip())
    for line in lines[:8]:
        match = next((brand for brand in brands if re.search(r'(?<![a-z])'+re.escape(brand)+r'(?![a-z])', line.casefold())), None)
        if match:
            stations.append(match.upper() if match in ('bp', 'q8', 'ok tankstation') else match.title())
    return {'date': _unique(dates), 'station': _unique(stations),
            'place': _unique(places), 'total': _unique(totals)}


def _part(value: str, fallback: str) -> str:
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^A-Za-z0-9,.-]+', '-', value).strip(' .-_')[:65]
    return value or fallback


def suggested_filename(fields: dict[str, str]) -> str:
    return '_'.join([
        fields.get('date') or 'Datum-onbekend',
        _part(fields.get('station', ''), 'Tankstation-onbekend'),
        _part(fields.get('place', ''), 'Plaats-onbekend'),
        _part(fields.get('total', ''), 'Bedrag-onbekend')
    ]) + '.pdf'


def clean_filename(value: str) -> str:
    name = str(value or '').strip()
    if not name.lower().endswith('.pdf') or len(name) > 200 or len(name) < 5:
        raise ValueError('Geef een bestandsnaam op die eindigt op .pdf (max. 200 tekens).')
    if name != Path(name).name or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or name.startswith('.'):
        raise ValueError('De bestandsnaam bevat onveilige tekens.')
    return name


def duplicates(con: sqlite3.Connection, digest: str, filename: str) -> list[dict]:
    return [dict(row) for row in con.execute(
        'SELECT id, filename FROM receipt_archive WHERE sha256=? OR filename=? ORDER BY id DESC LIMIT 10',
        (digest, filename))]


def prepare(items: list[dict], con: sqlite3.Connection) -> dict:
    if not isinstance(items, list) or not 1 <= len(items) <= 4:
        raise ValueError('Kies één PDF of maximaal vier foto’s.')
    decoded = [decode_file(item) for item in items]
    if sum(len(raw) for raw, _ in decoded) > MAX_TOTAL:
        raise ValueError('De selectie is samen groter dan 18 MB.')
    if len(decoded) > 1 and any(mime == 'application/pdf' for _, mime in decoded):
        raise ValueError('Een PDF kan niet met losse foto’s worden gecombineerd.')
    if decoded[0][1] == 'application/pdf':
        pdf = decoded[0][0]
        _, ocr_text = pdf_details(pdf)
    else:
        pdf, ocr_text = images_to_pdf([raw for raw, _ in decoded])
    if len(pdf) > MAX_PDF:
        raise ValueError('De gemaakte PDF is groter dan 24 MB.')
    fields = recognize_filename_fields(ocr_text)
    name = suggested_filename(fields)
    return {'pdf_data_url': 'data:application/pdf;base64,'+base64.b64encode(pdf).decode('ascii'),
            'suggested_filename': name, 'recognized': fields,
            'duplicates': duplicates(con, hashlib.sha256(pdf).hexdigest(), name)}


def decode_pdf(data_url: str) -> bytes:
    if not str(data_url).startswith('data:application/pdf;base64,'):
        raise ValueError('Er is geen voorbereide PDF ontvangen.')
    try:
        raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError('De PDF kon niet worden gelezen.') from None
    if not raw or len(raw) > MAX_PDF:
        raise ValueError('PDF is leeg of te groot.')
    return raw


def save(con: sqlite3.Connection, folder: Path, pdf_data_url: str, filename: str,
         confirm_duplicate: bool = False) -> dict:
    filename = clean_filename(filename)
    raw = decode_pdf(pdf_data_url)
    pdf_details(raw)  # Never archive corrupt PDFs.
    digest = hashlib.sha256(raw).hexdigest()
    found = duplicates(con, digest, filename)
    if found and not confirm_duplicate:
        return {'confirmation_required': True, 'duplicates': found}
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    stored_name = secrets.token_hex(16)+'.pdf'
    path = folder / stored_name
    try:
        with path.open('xb') as handle:
            os.chmod(path, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        cur = con.execute(
            'INSERT INTO receipt_archive(stored_name,filename,sha256,created_at) VALUES(?,?,?,?)',
            (stored_name, filename, digest, datetime.now().astimezone().isoformat()))
        con.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'ok': True, 'id': cur.lastrowid, 'filename': filename}


def archive_rows(con: sqlite3.Connection, query: str = '') -> list[dict]:
    rows = [dict(row) | {'legacy': False} for row in con.execute(
        'SELECT id, filename, created_at FROM receipt_archive ORDER BY id DESC')]
    # Old fuel receipt images are kept in place, readable and downloadable.
    for row in con.execute("""SELECT id, receipt_path, created_at FROM events
                              WHERE receipt_path IS NOT NULL AND receipt_path != ''
                              ORDER BY created_at DESC"""):
        rows.append({'id': 'legacy-'+str(row['id']), 'filename': Path(row['receipt_path']).name,
                     'created_at': row['created_at'], 'legacy': True})
    needle = query.strip().casefold()[:100]
    return [row for row in rows if not needle or needle in row['filename'].casefold()
            or needle in row['created_at'].casefold()]


def entry(con: sqlite3.Connection, folder: Path, old_folder: Path, entry_id: str) -> tuple[Path, str]:
    if re.fullmatch(r'legacy-\d+', entry_id):
        row = con.execute('SELECT receipt_path FROM events WHERE id=?',
                          (int(entry_id[7:]),)).fetchone()
        if not row or not row['receipt_path']:
            raise FileNotFoundError('Bestaande tankbon ontbreekt.')
        path = old_folder / Path(row['receipt_path']).name
        name = path.name
    elif re.fullmatch(r'\d+', entry_id):
        row = con.execute('SELECT stored_name, filename FROM receipt_archive WHERE id=?',
                          (int(entry_id),)).fetchone()
        if not row:
            raise FileNotFoundError('Tankbon niet gevonden.')
        path = folder / Path(row['stored_name']).name
        name = row['filename']
    else:
        raise FileNotFoundError('Tankbon niet gevonden.')
    if not path.is_file():
        raise FileNotFoundError('Tankbonbestand ontbreekt.')
    return path, name


def export_zip(con: sqlite3.Connection, folder: Path, old_folder: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for row in archive_rows(con):
            path, name = entry(con, folder, old_folder, str(row['id']))
            subdir = 'bestaande_bonnen/' if row['legacy'] else 'pdf/'
            # Prefix makes duplicate names unique; never drop a receipt.
            archive.write(path, subdir+str(row['id'])+'_'+Path(name).name)
    return output.getvalue()
