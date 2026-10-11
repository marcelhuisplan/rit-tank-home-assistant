"""Conservative, local extraction of tankbon facts from Tesseract / pdftotext output.

Only explicit document facts are returned. No odometer inference, geocoding or
external OCR requests. Ambiguity is reported rather than resolved by guessing.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BRANDS = ('totalenergies', 'shell', 'texaco', 'esso', 'tango', 'tinq',
          'avia', 'argos', 'gulf', 'q8', 'bp', 'ok tankstation')
FIELDS = ('station', 'shop', 'supplier', 'address', 'postcode', 'place',
          'date', 'time', 'fuel_type', 'pump', 'liters', 'price_per_liter',
          'total', 'vat_rate', 'vat_amount')
NUMBER = r'(\d{1,4}[.,]\d{1,3})'
MONEY = r'(\d{1,5}[.,]\d{2})'


def _clean(line: str) -> str:
    line = re.sub(r'\s+', ' ', line).strip()
    line = re.sub(r'(?<=\d)\s*([,.])\s*(?=\d)', r'\1', line)
    return re.sub(r'(?<=\d)\s*([/.-])\s*(?=\d)', r'\1', line)


def _num(value: str, decimals: int) -> str:
    """Require explicit decimal separator; accept dot or comma, never thousands."""
    raw = value.strip().replace(' ', '')
    if not re.fullmatch(r'\d{1,5}[,.]\d{1,3}', raw):
        return ''
    left, right = re.split('[,.]', raw)
    if len(right) > decimals:
        return ''
    return left + ',' + right.ljust(decimals, '0')


def _distinct(items: list[str]) -> str:
    found = {i.strip() for i in items if i and i.strip()}
    return next(iter(found)) if len(found) == 1 else ''


def recognize_receipt_fields(text: str) -> dict:
    """Recognize receipt location, monetary and tax fields with explicit uncertainty.

    Supplier details are *not* used for the station/location. Contradicting OCR
    lines suppress the field; multiplication only checks totals, never fills them.
    """
    lines = [_clean(line) for line in str(text or '').splitlines()]
    lines = [line for line in lines if line][:180]
    found: dict[str, list[str]] = {key: [] for key in FIELDS}
    issues: list[str] = []

    for i, line in enumerate(lines):
        lower = line.casefold()
        supplier_line = bool(re.search(r'\b(?:brandstofleverancier|leverancier|supplier|geleverd door)\b', lower))
        if supplier_line:
            match = re.search(r'\b(?:brandstofleverancier|leverancier|supplier|geleverd door)\s*[:\-]?\s*(.+)', line, re.I)
            if match and match.group(1).strip():
                found['supplier'].append(match.group(1).strip()[:100])
        elif i < 12:
            for brand in BRANDS:
                if re.search(r'(?<![a-z])' + r'\s*'.join(map(re.escape, brand)) + r'(?![a-z])', lower):
                    found['station'].append(brand.upper() if brand in ('bp', 'q8', 'ok tankstation') else brand.title())
                    break
            if re.search(r'\b(?:B\.?\s*V\.?|N\.?\s*V\.?)\b', line, re.I) and not re.search(r'\b(?:btw|kvk|iban)\b', lower):
                found['shop'].append(line[:100])
            explicit_shop = re.search(r'^(?:shop|exploitant)\s*:\s*(.+)', line, re.I)
            if explicit_shop:
                found['shop'].append(explicit_shop.group(1).strip()[:100])

        postal = re.search(r'(?<!\d)(\d{4})\s*([A-Z]{2})\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ \x27-]{1,38})\s*$', line, re.I)
        if postal and not supplier_line:
            postcode = postal.group(1) + ' ' + postal.group(2).upper()
            place = postal.group(3).strip()
            found['postcode'].append(postcode)
            found['place'].append(place)
            if i > 0 and re.search(r'\b\d{1,4}\s*[A-Za-z]?\s*$', lines[i-1]):
                # Street belongs to location directly above postcode, not to supplier.
                if not re.search(r'\b(?:leverancier|supplier|postbus)\b', lines[i-1], re.I):
                    found['address'].append(lines[i-1] + ', ' + postcode + ' ' + place)
            else:
                found['address'].append(postcode + ' ' + place)
        explicit_place = re.search(r'^(?:vestiging|plaats|locatie)\s*:\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ -]{2,40})$', line, re.I)
        if explicit_place and not supplier_line:
            found['place'].append(explicit_place.group(1).strip())

        # Date text may be year-first or Dutch day-first.
        candidates = re.findall(r'(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)', line)
        candidates += [(y, m, d) for d, m, y in
                       re.findall(r'(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})(?!\d)', line)]
        for y, m, d in candidates:
            try:
                found['date'].append(datetime(int(y), int(m), int(d)).date().isoformat())
            except ValueError:
                issues.append('Ongeldige datum in de OCR-tekst')
        for hour, minute, second in re.findall(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)', line):
            found['time'].append(f'{int(hour):02d}:{minute}:{second or "00"}')

        fuel = re.search(r'\b(95\s*(?:ongelood|ongel\.?|e10)?|98\s*(?:ongelood|e5)?|euro\s*95|e10|e5|diesel|hvo\s*100|super\s*plus)\b', line, re.I)
        if fuel and (not supplier_line):
            value = re.sub(r'\s+', ' ', fuel.group(1)).strip()
            if value not in ('95', '98') or re.search(r'(?:brandstof|product|benzine)', lower):
                found['fuel_type'].append(value.lower().replace('ongel.', 'ongelood'))
        pump = re.search(r'\bpomp(?:nummer|\s*nr\.?)?\s*[:#\-]?\s*(\d{1,2})(?!\d)', line, re.I)
        if pump:
            found['pump'].append(str(int(pump.group(1))))

        # Explicit labels and units prevent debit/payment references being mistaken
        # for litres or litre price.
        liters = re.search(r'\b(?:liters?|hoeveelheid|afgegeven)\s*[:=]?\s*(\d{1,3}[.,]\d{1,2})(?!\d)', line, re.I)
        if not liters:
            liters = re.search(r'(?<!\d)(\d{1,3}[.,]\d{1,2})\s*(?:liters?|ltr|l)\b', line, re.I)
        if liters:
            val = _num(liters.group(1), 2)
            if val and Decimal(val.replace(',', '.')) > 0:
                found['liters'].append(val)

        price = re.search(r'\b(?:prijs\s*per\s*liter|literprijs|prijs\s*/\s*l|eur\s*/\s*l|euro\s*/\s*l|per\s*liter)\s*[:=]?\s*(?:€|eur)?\s*(\d{1,2}[.,]\d{3})(?!\d)', line, re.I)
        if not price:
            price = re.search(r'(?<!\d)(\d{1,2}[.,]\d{3})\s*(?:€|eur)?\s*(?:/\s*l|per\s*liter)\b', line, re.I)
        if not price:
            price = re.search(r'\b\d{1,3}[.,]\d{2}\s*[x×*]\s*(\d{1,2}[.,]\d{3})(?!\d)', line, re.I)
        if price:
            val = _num(price.group(1), 3)
            if val and Decimal(val.replace(',', '.')) > 0:
                found['price_per_liter'].append(val)

        if re.search(r'\b(?:eindtotaal|totaal|total|te betalen|betaald bedrag)\b', lower):
            candidates_total = re.findall(r'(?<![\d,.])\d{1,5}[.,]\d{2}(?![\d,.])', line)
            if len(candidates_total) == 1:
                found['total'].append(_num(candidates_total[0], 2))

        if re.search(r'\b(?:btw|vat)\b', lower):
            rate = re.search(r'(?<!\d)(\d{1,2})(?:[,.]0{1,2})?\s*%', line)
            if rate:
                found['vat_rate'].append(rate.group(1) + '%')
                after = line[rate.end():]
                tax_amount = re.search(MONEY, after)
                if tax_amount:
                    found['vat_amount'].append(_num(tax_amount.group(1), 2))
            else:
                tax_amount = re.search(r'(?:btw\s*bedrag|btw\s*€)\s*[:=]?\s*(?:€)?\s*' + MONEY, line, re.I)
                if tax_amount:
                    found['vat_amount'].append(_num(tax_amount.group(1), 2))

    fields = {key: _distinct(values) for key, values in found.items()}
    status = {key: ('tegenstrijdig' if len(set(v for v in found[key] if v)) > 1
                    else 'herkend' if fields[key] else 'ontbreekt') for key in FIELDS}
    for key in FIELDS:
        if status[key] == 'tegenstrijdig':
            issues.append(f'Tegenstrijdige OCR-waarden voor {key}; controle vereist')
    if fields['liters'] and fields['price_per_liter'] and fields['total']:
        try:
            liters = Decimal(fields['liters'].replace(',', '.'))
            price = Decimal(fields['price_per_liter'].replace(',', '.'))
            actual = Decimal(fields['total'].replace(',', '.'))
            calculated = (liters * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if actual != calculated:
                status['total'] = 'rekenverschil'
                issues.append(f'Liters × literprijs = € {str(calculated).replace(".", ",")}, bon vermeldt € {fields["total"]}')
        except InvalidOperation:
            status['total'] = 'tegenstrijdig'
    if fields['total'] and fields['vat_rate'] and fields['vat_amount']:
        rate = Decimal(fields['vat_rate'].strip('%'))
        if rate > 0:
            gross = Decimal(fields['total'].replace(',', '.'))
            expected = (gross * rate / (Decimal(100) + rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if expected != Decimal(fields['vat_amount'].replace(',', '.')):
                status['vat_amount'] = 'rekenverschil'
                issues.append('Btw-bedrag komt niet overeen met het opgegeven percentage en totaal')
    return {**fields, 'status': status, 'warnings': list(dict.fromkeys(issues))}
