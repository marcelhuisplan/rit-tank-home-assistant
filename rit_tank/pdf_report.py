from __future__ import annotations

import base64
import html
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping


def _provider(dependencies: Mapping[str, Callable[..., Any]], name: str) -> Callable[..., Any]:
    provider = dependencies.get(name)
    if provider is None:
        raise RuntimeError(f'PDF dependency ontbreekt: {name}')
    return provider


def _pdf_text(value: Any) -> str:
    """Text safe for the built-in PDF WinAnsi fonts."""
    s = str(value if value is not None else '')
    return s.replace('\u2192', '->').replace('\u00a0', ' ')


def _pdf_escape(value: Any) -> bytes:
    raw = _pdf_text(value).encode('cp1252', 'replace')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


class _SimplePdfPage:
    def __init__(self, title: str = ''):
        self.commands: list[bytes] = []
        self.preview: list[str] = []
        self.images: set[str] = set()
        self.y = 806.0
        self.title = title
        if title:
            self.text(title, 36, self.y, 15, bold=True)
            self.y -= 12
            self.line(36, self.y, 559, self.y, 0.75)
            self.y -= 18

    @staticmethod
    def _normalize_rgb(rgb: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
        if rgb is None:
            return None
        return tuple(max(0, min(255, int(v))) for v in rgb)

    @classmethod
    def _svg_color(cls, gray: float | None = None, rgb: tuple[int, int, int] | None = None) -> str:
        norm = cls._normalize_rgb(rgb)
        if norm is not None:
            return f'rgb({norm[0]},{norm[1]},{norm[2]})'
        level = round((0.08 if gray is None else gray) * 255)
        return f'rgb({level},{level},{level})'

    @classmethod
    def _fill_command(cls, gray: float | None = None, rgb: tuple[int, int, int] | None = None) -> str:
        norm = cls._normalize_rgb(rgb)
        if norm is not None:
            r, g, b = [v / 255 for v in norm]
            return f'{r:.3f} {g:.3f} {b:.3f} rg'
        return f'{(0.08 if gray is None else gray):.3f} g'

    @classmethod
    def _stroke_command(cls, gray: float | None = None, rgb: tuple[int, int, int] | None = None) -> str:
        norm = cls._normalize_rgb(rgb)
        if norm is not None:
            r, g, b = [v / 255 for v in norm]
            return f'{r:.3f} {g:.3f} {b:.3f} RG'
        return f'{(0.75 if gray is None else gray):.3f} G'

    def text(self, value: Any, x: float, y: float, size: float = 9, bold: bool = False,
             gray: float | None = 0.08, rgb: tuple[int, int, int] | None = None):
        color = self._svg_color(gray, rgb)
        self.preview.append(f'<text x="{x}" y="{842-y}" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{html.escape(_pdf_text(value))}</text>')
        font = 'F2' if bold else 'F1'
        esc = _pdf_escape(value)
        self.commands.append(
            (self._fill_command(gray, rgb) + f' BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ').encode('ascii')
            + b'(' + esc + b') Tj ET\n'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5,
             gray: float | None = 0.75, rgb: tuple[int, int, int] | None = None):
        color = self._svg_color(gray, rgb)
        self.preview.append(f'<line x1="{x1}" y1="{842-y1}" x2="{x2}" y2="{842-y2}" stroke="{color}" stroke-width="{width}"/>')
        self.commands.append(
            f'{self._stroke_command(gray, rgb)} {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S\n'.encode('ascii')
        )

    def rect(self, x: float, y: float, width: float, height: float,
             gray: float | None = 0.94, rgb: tuple[int, int, int] | None = None):
        color = self._svg_color(gray, rgb)
        self.preview.append(f'<rect x="{x}" y="{842-y-height}" width="{width}" height="{height}" fill="{color}"/>')
        self.commands.append(
            f'{self._fill_command(gray, rgb)} {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f\n'.encode('ascii')
        )

    def rounded_rect(self, x: float, y: float, width: float, height: float, radius: float = 4.0,
                      gray: float | None = 0.94, rgb: tuple[int, int, int] | None = None):
        """Filled rectangle with subtly rounded corners (radius clamped to half the smallest side)."""
        r = max(0.0, min(radius, width / 2, height / 2))
        color = self._svg_color(gray, rgb)
        self.preview.append(
            f'<rect x="{x}" y="{842-y-height}" width="{width}" height="{height}" rx="{r}" ry="{r}" fill="{color}"/>'
        )
        if r <= 0:
            self.commands.append(
                f'{self._fill_command(gray, rgb)} {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f\n'.encode('ascii')
            )
            return
        k = r * 0.5522847498
        x0, x1 = x, x + width
        y0, y1 = y, y + height
        path = (
            f'{x0 + r:.2f} {y0:.2f} m '
            f'{x1 - r:.2f} {y0:.2f} l '
            f'{x1 - r + k:.2f} {y0:.2f} {x1:.2f} {y0 + r - k:.2f} {x1:.2f} {y0 + r:.2f} c '
            f'{x1:.2f} {y1 - r:.2f} l '
            f'{x1:.2f} {y1 - r + k:.2f} {x1 - r + k:.2f} {y1:.2f} {x1 - r:.2f} {y1:.2f} c '
            f'{x0 + r:.2f} {y1:.2f} l '
            f'{x0 + r - k:.2f} {y1:.2f} {x0:.2f} {y1 - r + k:.2f} {x0:.2f} {y1 - r:.2f} c '
            f'{x0:.2f} {y0 + r:.2f} l '
            f'{x0:.2f} {y0 + r - k:.2f} {x0 + r - k:.2f} {y0:.2f} {x0 + r:.2f} {y0:.2f} c h f\n'
        )
        self.commands.append((f'{self._fill_command(gray, rgb)} ' + path).encode('ascii'))

    def circle(self, x: float, y: float, radius: float,
               gray: float | None = 0.75, rgb: tuple[int, int, int] | None = None):
        color = self._svg_color(gray, rgb)
        self.preview.append(f'<circle cx="{x}" cy="{842-y}" r="{radius}" fill="{color}"/>')
        c = radius * 0.5522847498
        self.commands.append(
            (
                f'{self._fill_command(gray, rgb)} '
                f'{x + radius:.2f} {y:.2f} m '
                f'{x + radius:.2f} {y + c:.2f} {x + c:.2f} {y + radius:.2f} {x:.2f} {y + radius:.2f} c '
                f'{x - c:.2f} {y + radius:.2f} {x - radius:.2f} {y + c:.2f} {x - radius:.2f} {y:.2f} c '
                f'{x - radius:.2f} {y - c:.2f} {x - c:.2f} {y - radius:.2f} {x:.2f} {y - radius:.2f} c '
                f'{x + c:.2f} {y - radius:.2f} {x + radius:.2f} {y - c:.2f} {x + radius:.2f} {y:.2f} c h f\n'
            ).encode('ascii')
        )

    def image(self, name: str, x: float, y: float, width: float, height: float):
        self.images.add(name)
        self.preview.append(f'<image x="{x}" y="{842-y-height}" width="{width}" height="{height}" preserveAspectRatio="none" href="IMAGE_{name}"/>')
        self.commands.append(
            f'q {width:.2f} 0 0 {height:.2f} {x:.2f} {y:.2f} cm /{name} Do Q\n'.encode('ascii')
        )

    @staticmethod
    def text_width(value: str, size: float = 8.5, bold: bool = False) -> float:
        """Rough estimate of rendered text width for Helvetica(-Bold) at a given size."""
        factor = 0.60 if bold else 0.52
        return len(str(value)) * size * factor

    @staticmethod
    def wrap_lines(value: Any, width: float, size: float = 8.5) -> list[str]:
        chars = max(18, int(width / max(3.7, size * 0.52)))
        return textwrap.wrap(_pdf_text(value), width=chars, break_long_words=False, break_on_hyphens=False) or ['']

    def wrapped(self, value: Any, x: float, width: float, size: float = 8.5, bold: bool = False,
                leading: float | None = None, indent: float = 0,
                gray: float | None = 0.08, rgb: tuple[int, int, int] | None = None):
        leading = leading or (size + 3)
        lines = self.wrap_lines(value, width, size)
        for line in lines:
            self.text(line, x + indent, self.y, size, bold=bold, gray=gray, rgb=rgb)
            self.y -= leading
        return len(lines)

    def need(self, height: float) -> bool:
        return self.y - height < 42

    def stream(self) -> bytes:
        return b''.join(self.commands)

    def svg(self, images: dict[str, tuple[int, int, bytes]]) -> str:
        markup = ''.join(self.preview)
        for name, (_, _, data) in images.items():
            markup = markup.replace(f'IMAGE_{name}', 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii'))
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 595 842" role="img" aria-label="Pagina rittenregistratie" style="font-family:Arial,Helvetica,sans-serif;background:white">' + markup + '</svg>'


BADGE_LABELS = ('Privé', 'Zakelijk', 'Privé/Zakelijk')
BADGE_TEXT_SIZE = 8.5
BADGE_TEXT_OFFSET = 26.0
BADGE_RIGHT_PADDING = 8.0
BADGE_HEIGHT = 16.0
BADGE_RADIUS = 8.0
BADGE_X = 402.0
BADGE_WIDTH = round(
    max(_SimplePdfPage.text_width(label, BADGE_TEXT_SIZE) for label in BADGE_LABELS)
    + BADGE_TEXT_OFFSET + BADGE_RIGHT_PADDING,
    1,
)
ADDRESS_X = 202.0
ADDRESS_MAX_WIDTH = BADGE_X - 8 - ADDRESS_X
ROW_HEIGHT = 43.0


def _fit_text(value: Any, width: float, size: float = 9.5) -> str:
    """Clip text so it stays inside the available column width."""
    text = _pdf_text(value)
    if _SimplePdfPage.text_width(text, size) <= width:
        return text
    return text[:max(1, int(width / (size * 0.52)))]


def _build_pdf(pages: list[_SimplePdfPage], images: dict[str, tuple[int, int, bytes]] | None = None) -> bytes:
    """Minimal dependency-free PDF writer using core Helvetica fonts and JPEGs."""
    objects: dict[int, bytes] = {}
    objects[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[3] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>'
    objects[4] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>'
    image_ids: dict[str, int] = {}
    next_id = 5
    for name, (width, height, data) in (images or {}).items():
        image_ids[name] = next_id
        objects[next_id] = (
            f'<< /Type /XObject /Subtype /Image /Width {int(width)} /Height {int(height)} '
            f'/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\n'
        ).encode('ascii') + b'stream\n' + data + b'\nendstream'
        next_id += 1
    kids = []
    for page in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        kids.append(f'{page_id} 0 R')
        stream = page.stream()
        objects[content_id] = b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'endstream'
        xobjects = ' '.join(
            f'/{name} {image_ids[name]} 0 R'
            for name in sorted(page.images)
            if name in image_ids
        )
        xobject_resource = f' /XObject << {xobjects} >>' if xobjects else ''
        objects[page_id] = (
            f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
            f'/Resources << /Font << /F1 3 0 R /F2 4 0 R >>{xobject_resource} >> /Contents {content_id} 0 R >>'
        ).encode('ascii')
    objects[2] = f'<< /Type /Pages /Count {len(pages)} /Kids [{" ".join(kids)}] >>'.encode('ascii')

    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = {0: 0}
    max_id = max(objects)
    for obj_id in range(1, max_id + 1):
        offsets[obj_id] = len(out)
        out.extend(f'{obj_id} 0 obj\n'.encode('ascii'))
        out.extend(objects[obj_id])
        out.extend(b'\nendobj\n')
    xref = len(out)
    out.extend(f'xref\n0 {max_id + 1}\n'.encode('ascii'))
    out.extend(b'0000000000 65535 f \n')
    for obj_id in range(1, max_id + 1):
        out.extend(f'{offsets[obj_id]:010d} 00000 n \n'.encode('ascii'))
    out.extend(f'trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode('ascii'))
    return bytes(out)


def business_pdf(period: str = 'month', year: str | None = None, month: str | None = None, preview: bool = False, *, dependencies: Mapping[str, Callable[..., Any]] | None = None) -> Any:
    deps = dependencies or {}
    get_settings = _provider(deps, 'get_settings')
    now_local = _provider(deps, 'now_local')
    period_bounds = _provider(deps, 'period_bounds')
    business_trips_raw = _provider(deps, 'business_trips_raw')
    enrich_business_trip = _provider(deps, 'enrich_business_trip')
    parse_dt = _provider(deps, 'parse_dt')
    period_label = _provider(deps, 'period_label')
    settings = get_settings()
    ref = now_local()
    try:
        if year is not None:
            if period not in {'month', 'year'}:
                raise ValueError()
            ref = ref.replace(year=int(year), month=int(month or 1), day=1)
        elif month is not None:
            raise ValueError()
        if not 1900 <= ref.year <= 9998:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Kies een geldig jaar (1900-9998) en een maand (1-12).') from None

    safe_period = period if period in {'day', 'week', 'month', 'year'} else 'month'
    selection_start, selection_end = period_bounds(safe_period, ref)
    trips = []
    for trip, stops in business_trips_raw():
        if stops and (period == 'all' or selection_start <= parse_dt(stops[0]['created_at']) < selection_end):
            trips.append(enrich_business_trip(trip, stops, resolve=False))
    trips.sort(key=lambda trip: parse_dt(trip['stops'][0]['created_at']))

    if period == 'all':
        label = 'Alle geregistreerde ritten'
        filename_label = 'alles'
        period_stops = [
            parse_dt(stop.get('created_at'))
            for trip in trips
            for stop in (trip.get('stops') or [])
            if stop.get('created_at')
        ]
        period_start = min(period_stops) if period_stops else None
        period_end = max(period_stops) if period_stops else None
    else:
        period_start, period_end_exclusive = selection_start, selection_end
        label = period_label(safe_period, period_start)
        if safe_period == 'year':
            filename_label = period_start.strftime('%Y')
        elif safe_period == 'month':
            filename_label = period_start.strftime('%Y-%m')
        else:
            filename_label = safe_period
        period_end = period_end_exclusive - timedelta(days=1)

    if period_start and period_end:
        year_label = str(period_start.year) if period_start.year == period_end.year else f'{period_start.year}-{period_end.year}'
        date_range = f'{period_start:%d-%m-%Y} - {period_end:%d-%m-%Y}'
    else:
        year_label = '-'
        date_range = 'Geen geregistreerde datums'

    total_km = round(sum(float(t.get('km') or 0) for t in trips), 1)
    business_km = round(sum(float(t.get('business_km') or 0) for t in trips), 1)
    private_km = round(sum(float(t.get('private_km') or 0) for t in trips), 1)

    company_name = str(settings.get('company_name') or 'Huisplan BV').strip() or 'Huisplan BV'
    footer_company = 'Huisplan BV'
    vehicle_bits = [x for x in [settings.get('vehicle_make'), settings.get('vehicle_model')] if x]
    vehicle_name = ' '.join(str(x).strip() for x in vehicle_bits if str(x).strip()) or str(settings.get('vehicle_name') or '-').strip() or '-'
    driver_name = str(settings.get('driver_name') or '-').strip() or '-'
    license_plate = str(settings.get('license_plate') or '-').strip() or '-'
    generated_label = now_local().strftime('%d-%m-%Y %H:%M')
    footer_period_label = label if label else '-'
    report_period_main = label if label else '-'
    report_period_range = f'{period_start:%d-%m-%Y} t/m {period_end:%d-%m-%Y}' if period_start and period_end else ''

    MM_TO_PT = 72 / 25.4
    TOP_MARGIN_MM = 25
    TOP_MARGIN_PT = TOP_MARGIN_MM * MM_TO_PT
    PAGE_HEIGHT_PT = 842
    HEADER_TOP_Y = PAGE_HEIGHT_PT - TOP_MARGIN_PT

    palette = {
        'text': (12, 15, 18),
        'muted': (151, 167, 180),
        'line': (43, 53, 64),
        'blue': (82, 186, 255),
        'teal': (88, 223, 177),
        'card': (244, 247, 250),
        'card2': (236, 243, 247),
    }

    def tint(rgb: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
        return tuple(max(0, min(255, int(round(v + (255 - v) * ratio)))) for v in rgb)

    def fmt_km(value: Any) -> str:
        number = round(float(value or 0), 1)
        if abs(number - round(number)) < 0.05:
            return f'{int(round(number))}'
        return f'{number:.1f}'

    pdf_images: dict[str, tuple[int, int, bytes]] = {}
    logo_path = Path(__file__).with_name('huisplan-logo.jpg')
    try:
        if logo_path.exists():
            pdf_images['ImLogo'] = (900, 827, logo_path.read_bytes())
    except OSError:
        pass

    pages: list[_SimplePdfPage] = []

    def draw_header(page: _SimplePdfPage, compact: bool = False) -> None:
        if not compact:
            title_y = HEADER_TOP_Y
            subtitle_y = HEADER_TOP_Y - 18
            logo_y = HEADER_TOP_Y - 43
            header_line_y = HEADER_TOP_Y - 52
            page_y_after = HEADER_TOP_Y - 72
        else:
            title_y = 691
            subtitle_y = 672
            logo_y = 653
            header_line_y = 642
            page_y_after = 624
        logo_w = 46 if not compact else 40
        logo_h = 42 if not compact else 36
        logo_x = 489 if not compact else 515
        page.text('Rittenregistratie', 36, title_y, 26 if not compact else 15, bold=True, rgb=palette['text'])
        page.text('Fiscale kilometeradministratie', 36, subtitle_y, 9.3 if not compact else 8.4, rgb=palette['muted'])
        if 'ImLogo' in pdf_images:
            page.image('ImLogo', logo_x, logo_y, logo_w, logo_h)
        page.line(36, header_line_y, 559, header_line_y, 0.8, rgb=tint(palette['line'], 0.35))
        page.y = page_y_after

    def draw_field_icon(page: _SimplePdfPage, kind: str, x: float, y: float) -> None:
        """Draw a small (~9x9pt) vector glyph to the left of a report-info label."""
        c = tint(palette['blue'], 0.15)
        if kind == 'calendar':
            page.rect(x, y - 8, 9, 8, rgb=tint(palette['blue'], 0.82))
            page.rect(x, y, 9, 1.6, rgb=c)
            page.line(x + 2, y + 1.6, x + 2, y - 0.6, 0.8, rgb=c)
            page.line(x + 7, y + 1.6, x + 7, y - 0.6, 0.8, rgb=c)
        elif kind == 'person':
            page.circle(x + 4.5, y - 1.5, 2.1, rgb=c)
            page.rect(x + 1, y - 7.5, 7, 4.5, rgb=c)
        elif kind == 'car':
            page.rect(x, y - 5, 9, 3, rgb=c)
            page.rect(x + 1.5, y - 2.2, 6, 2.2, rgb=c)
            page.circle(x + 2, y - 6, 1.3, rgb=tint(palette['muted'], 0.1))
            page.circle(x + 7, y - 6, 1.3, rgb=tint(palette['muted'], 0.1))
        elif kind == 'plate':
            page.rect(x, y - 6, 9, 6, rgb=c)
            page.rect(x + 1, y - 5, 7, 1, rgb=(255, 255, 255))
        elif kind == 'clock':
            page.circle(x + 4.5, y - 4, 4.2, rgb=c)
            page.line(x + 4.5, y - 4, x + 4.5, y - 1.3, 0.8, rgb=(255, 255, 255))
            page.line(x + 4.5, y - 4, x + 6.6, y - 4, 0.8, rgb=(255, 255, 255))

    def draw_report_table(page: _SimplePdfPage) -> None:
        table_top = page.y
        row_h = 44
        left_x, right_x = 48, 304
        page.rounded_rect(36, table_top - row_h * 3, 523, row_h * 3, radius=5, rgb=palette['card'])
        for i in range(4):
            y = table_top - i * row_h
            page.line(36, y, 559, y, 0.5, rgb=tint(palette['line'], 0.72))
        page.line(292, table_top, 292, table_top - row_h * 3, 0.5, rgb=tint(palette['line'], 0.72))
        cells = [
            (('Kalenderjaar', year_label, 'calendar'), ('Rapportperiode', None, 'calendar')),
            (('Bestuurder', driver_name, 'person'), ('Auto', vehicle_name, 'car')),
            (('Kenteken', license_plate, 'plate'), ('Gegenereerd op', generated_label, 'clock')),
        ]
        for row_idx, (left_cell, right_cell) in enumerate(cells):
            baseline = table_top - row_idx * row_h - 13
            for x, width, (key, value, icon) in ((left_x, 218, left_cell), (right_x, 215, right_cell)):
                icon_x = x
                text_x = x + 14
                draw_field_icon(page, icon, icon_x, baseline + 11)
                page.text(key.upper(), text_x, baseline, 7.2, bold=True, rgb=palette['muted'])
                if key == 'Rapportperiode':
                    page.text(report_period_main, text_x, baseline - 12, 9.2, bold=(row_idx == 0), rgb=palette['text'])
                    page.text(report_period_range, text_x, baseline - 26, 7, rgb=palette['muted'])
                    continue
                value_lines = _SimplePdfPage.wrap_lines(value, width - 14, 9.2)[:2]
                for line_idx, line in enumerate(value_lines):
                    page.text(line, text_x, baseline - 13 - line_idx * 11, 9.2, bold=(row_idx == 0 and x == left_x), rgb=palette['text'])
        page.y = table_top - row_h * 3 - 18

    def draw_summary_cards(page: _SimplePdfPage) -> None:
        card_y = page.y - 58
        width = 165
        gap = 14
        cards = [
            ('TOTAAL', format_dutch_km(total_km), palette['card2'], None),
            ('ZAKELIJK', format_dutch_km(business_km), tint(palette['teal'], 0.85), palette['teal']),
            ('PRIVÉ', format_dutch_km(private_km), tint(palette['blue'], 0.88), palette['blue']),
        ]
        for idx, (title, value, bg, bullet) in enumerate(cards):
            x = 36 + idx * (width + gap)
            page.rounded_rect(x, card_y, width, 58, radius=6, rgb=bg)
            label_x = x + 14
            if title == 'TOTAAL':
                page.rect(x + 12, card_y + 36, 9, 11, rgb=tint(palette['muted'], 0.2))
                page.line(x + 16.5, card_y + 38, x + 16.5, card_y + 45, 0.8, rgb=(255, 255, 255))
                label_x = x + 26
            elif bullet is not None:
                page.circle(x + 16, card_y + 42.9, 3.6, rgb=bullet)
                label_x = x + 26
            page.text(title, label_x, card_y + 40, 8.2, bold=True, rgb=palette['muted'])
            page.text(f'{value} km', x + 14, card_y + 18, 18, bold=True, rgb=palette['text'])
        page.y = card_y - 22

    def new_page(*, compact: bool = False, section_label: str | None = None) -> _SimplePdfPage:
        page = _SimplePdfPage()
        draw_header(page, compact=compact)
        if section_label:
            page.text(section_label, 36, page.y, 9.5, bold=True, rgb=palette['muted'])
            page.y -= 18
        pages.append(page)
        return page

    def dutch_day_abbr(dt: datetime) -> str:
        days = ('ma', 'di', 'wo', 'do', 'vr', 'za', 'zo')
        return days[dt.weekday()]

    def format_dutch_date(dt: datetime) -> str:
        return f'{dutch_day_abbr(dt)} {dt:%d-%m-%Y}'

    def format_dutch_time(dt: datetime) -> str:
        return dt.strftime('%H:%M')

    def format_dutch_km(value: Any) -> str:
        number = round(float(value or 0), 1)
        if abs(number - round(number)) < 0.05:
            formatted = f'{int(round(number))},0'
        else:
            formatted = f'{number:.1f}'.replace('.', ',')
        return formatted

    def trip_badge_text(trip: dict[str, Any]) -> tuple[str, tuple[int, int, int]]:
        has_business = float(trip.get('business_km') or 0) > 0
        has_private = float(trip.get('private_km') or 0) > 0
        if has_business and has_private:
            return 'Privé/Zakelijk', palette['line']
        if has_private:
            return 'Privé', palette['blue']
        return 'Zakelijk', palette['teal']

    def segment_badge(trip: dict[str, Any], destination: dict[str, Any]) -> tuple[str, tuple[int, int, int]]:
        """Badge for one leg; only an unclassified leg falls back to the trip classification."""
        segment_type = str(destination.get('segment_trip_type') or '')
        if segment_type == 'business':
            return 'Zakelijk', palette['teal']
        if segment_type == 'private':
            return 'Privé', palette['blue']
        return trip_badge_text(trip)

    def trip_segment_rows(trip: dict[str, Any], stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Every destination stop after the first becomes its own exported leg."""
        if len(stops) < 2:
            label, color = trip_badge_text(trip)
            return [{'origin': stops[0], 'destination': stops[0], 'km': trip.get('km'),
                     'label': label, 'color': color}]
        rows = []
        for idx in range(1, len(stops)):
            destination = stops[idx]
            label, color = segment_badge(trip, destination)
            rows.append({'origin': stops[idx - 1], 'destination': destination,
                         'km': destination.get('segment_km'), 'label': label, 'color': color})
        return rows

    def draw_table_header(page: _SimplePdfPage, x1: float, y_top: float) -> float:
        """Draw table header and return the y position after header."""
        header_h = 22
        header_bg = (241, 244, 248)
        page.rect(x1, y_top - header_h, 523, header_h, rgb=header_bg)
        page.line(x1, y_top - header_h, x1 + 523, y_top - header_h, 0.4, rgb=tint(palette['line'], 0.5))
        header_text_y = y_top - 15
        page.text('#', 60, header_text_y, 9.5, bold=True, rgb=palette['text'])
        page.text('Datum', 90, header_text_y, 9.5, bold=True, rgb=palette['text'])
        page.text('Vertrek → Aankomst (adres)', ADDRESS_X, header_text_y, 9.5, bold=True, rgb=palette['text'])
        page.text('Soort', BADGE_X, header_text_y, 9.5, bold=True, rgb=palette['text'])
        page.text('Afstand', 557 - _SimplePdfPage.text_width('Afstand', 9.5, bold=True), header_text_y, 9.5, bold=True, rgb=palette['text'])
        return y_top - header_h - 1

    def draw_table_row(page: _SimplePdfPage, y_top: float, row_num: int, row: dict[str, Any]) -> float:
        """Draw a single leg row. Returns the next available y position."""
        row_h = ROW_HEIGHT
        x1, x2 = 36, 559
        origin, destination = row['origin'], row['destination']

        start_dt = parse_dt(origin['created_at'])
        end_dt = parse_dt(destination['created_at'])

        start_addr = (origin.get('location_address') or origin.get('location_label') or
                      origin.get('manual_label') or 'Locatie onbekend')
        end_addr = (destination.get('location_address') or destination.get('location_label') or
                    destination.get('manual_label') or 'Locatie onbekend')

        trip_label, trip_color = row['label'], row['color']
        trip_km = format_dutch_km(row['km'])

        page.line(x1, y_top, x2, y_top, 0.2, rgb=tint(palette['line'], 0.85))

        mid_y = y_top - row_h / 2

        page.text(str(row_num), 60, mid_y + 2.5, 9, rgb=palette['text'])

        date_text = format_dutch_date(start_dt)
        time_text = f'{format_dutch_time(start_dt)} \u2013 {format_dutch_time(end_dt)}'
        page.text(date_text, 90, y_top - 16, 9.5, bold=True, rgb=palette['text'])
        page.text(time_text, 90, y_top - 30, 8, rgb=palette['muted'])

        page.circle(182, y_top - 10, 4.3, rgb=(76, 175, 80))
        page.text(_fit_text(start_addr, ADDRESS_MAX_WIDTH), ADDRESS_X, y_top - 13, 9.5, rgb=palette['text'])

        page.circle(182, y_top - 26, 4.3, rgb=(244, 67, 54))
        page.text(_fit_text(end_addr, ADDRESS_MAX_WIDTH), ADDRESS_X, y_top - 29, 9.5, rgb=palette['text'])

        page.rounded_rect(BADGE_X, mid_y - BADGE_HEIGHT / 2, BADGE_WIDTH, BADGE_HEIGHT,
                          radius=BADGE_RADIUS, rgb=tint(trip_color, 0.87))
        page.circle(BADGE_X + 9, mid_y, 3.4, rgb=trip_color)
        page.text(trip_label, BADGE_X + BADGE_TEXT_OFFSET, mid_y - 2.9, BADGE_TEXT_SIZE, rgb=trip_color)

        km_text = trip_km + ' km'
        km_w = _SimplePdfPage.text_width(km_text, 9.5, bold=True)
        page.text(km_text, 557 - km_w, mid_y - 2.9, 9.5, bold=True, rgb=palette['text'])

        return y_top - row_h

    page = new_page(compact=False)
    draw_report_table(page)
    draw_summary_cards(page)
    page.text('Rittenoverzicht', 36, page.y, 14, bold=True, rgb=palette['text'])
    page.y -= 20

    if not trips:
        page.text('Geen ritten in deze periode.', 36, page.y, 10.5, rgb=palette['text'])
    else:
        table_y = page.y
        page.y = draw_table_header(page, 36, table_y)

        row_num = 0
        for trip in trips:
            stops = trip.get('stops') or []
            if not stops:
                continue

            for row in trip_segment_rows(trip, stops):
                row_num += 1

                if page.need(ROW_HEIGHT + 3):
                    page = new_page(compact=True)
                    page.y = draw_table_header(page, 36, page.y)

                page.y = draw_table_row(page, page.y, row_num, row)

    total_pages = len(pages)
    for n, pg in enumerate(pages, start=1):
        pg.line(36, 34, 559, 34, 0.45, rgb=tint(palette['line'], 0.65))
        pg.text(footer_company, 36, 20, 7.4, bold=True, rgb=palette['muted'])
        pg.text(footer_period_label, 262, 20, 7.4, rgb=palette['muted'])
        pg.text(f'Pagina {n} van {total_pages}', 475, 20, 7.4, rgb=palette['muted'])

    data = _build_pdf(pages, pdf_images)
    filename = f'rittenregistratie_{filename_label}.pdf'
    if preview:
        return {'filename': filename, 'pdf_base64': base64.b64encode(data).decode('ascii'), 'pages': [p.svg(pdf_images) for p in pages]}
    return data, filename
