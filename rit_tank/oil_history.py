"""Motorolie-onderhoudshistorie, volledig gescheiden van ritten en tankbeurten.

De huidige Rit & Tank-administratie kent één geconfigureerd voertuig. De vaste
vehicle_key 'primary' blijft hetzelfde wanneer de weergavenaam verandert.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from typing import Any


class OilEntryNotFound(ValueError):
    """A requested maintenance entry does not exist in the current vehicle."""


def init_schema(con: sqlite3.Connection) -> None:
    """Additive, repeatable migration; never alters or rewrites existing tables."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS oil_maintenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_key TEXT NOT NULL DEFAULT 'primary' CHECK(vehicle_key = 'primary'),
            performed_on TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('top_up', 'change')),
            oil_type TEXT NOT NULL,
            amount_ml INTEGER NOT NULL CHECK(amount_ml BETWEEN 1 AND 20000),
            odometer INTEGER NOT NULL CHECK(odometer BETWEEN 0 AND 9999999),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_oil_maintenance_vehicle_date
            ON oil_maintenance(vehicle_key, performed_on DESC, id DESC);
    """)


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value).strip()):
        raise ValueError(f'{label}: gebruik een geheel getal.')
    number = int(str(value).strip())
    if not minimum <= number <= maximum:
        raise ValueError(f'{label}: vul een waarde van {minimum} t/m {maximum} in.')
    return number


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError('Ongeldige oliebeurt.')
    raw_date = payload.get('performed_on')
    if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
        raise ValueError('Vul een geldige datum in.')
    try:
        parsed = date.fromisoformat(raw_date)
    except ValueError:
        raise ValueError('Vul een geldige datum in.') from None
    if not (1900 <= parsed.year <= 2100):
        raise ValueError('Datum valt buiten het toegestane bereik.')
    kind = payload.get('kind')
    if kind not in ('top_up', 'change'):
        raise ValueError('Kies olie bijvullen of olie verversen.')
    oil_type = payload.get('oil_type')
    if not isinstance(oil_type, str):
        raise ValueError('Vul het olietype of de productspecificatie in.')
    oil_type = oil_type.strip()
    if not oil_type or len(oil_type) > 120 or any(ord(c) < 32 for c in oil_type):
        raise ValueError('Vul een olietype in (maximaal 120 tekens, één regel).')
    return {
        'performed_on': raw_date,
        'kind': kind,
        'oil_type': oil_type,
        'amount_ml': _integer(payload.get('amount_ml'), 'Hoeveelheid (ml)', 1, 20000),
        'odometer': _integer(payload.get('odometer'), 'Kilometerstand', 0, 9999999),
    }


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_entries(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_row(r) for r in con.execute(
        """SELECT id, performed_on, kind, oil_type, amount_ml, odometer, created_at, updated_at
           FROM oil_maintenance WHERE vehicle_key='primary'
           ORDER BY performed_on DESC, id DESC"""
    )]


def get_entry(con: sqlite3.Connection, entry_id: int) -> dict[str, Any]:
    row = con.execute(
        """SELECT id, performed_on, kind, oil_type, amount_ml, odometer, created_at, updated_at
           FROM oil_maintenance WHERE id=? AND vehicle_key='primary'""",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise OilEntryNotFound('Oliebeurt niet gevonden.')
    return _row(row)


def create_entry(con: sqlite3.Connection, payload: dict[str, Any], stamp: str) -> dict[str, Any]:
    clean = validate(payload)
    cur = con.execute(
        """INSERT INTO oil_maintenance
           (vehicle_key, performed_on, kind, oil_type, amount_ml, odometer, created_at, updated_at)
           VALUES ('primary', ?, ?, ?, ?, ?, ?, ?)""",
        (clean['performed_on'], clean['kind'], clean['oil_type'],
         clean['amount_ml'], clean['odometer'], stamp, stamp),
    )
    return get_entry(con, cur.lastrowid)


def update_entry(con: sqlite3.Connection, entry_id: int, payload: dict[str, Any],
                 stamp: str) -> dict[str, Any]:
    clean = validate(payload)
    cur = con.execute(
        """UPDATE oil_maintenance
           SET performed_on=?, kind=?, oil_type=?, amount_ml=?, odometer=?, updated_at=?
           WHERE id=? AND vehicle_key='primary'""",
        (clean['performed_on'], clean['kind'], clean['oil_type'], clean['amount_ml'],
         clean['odometer'], stamp, entry_id),
    )
    if cur.rowcount != 1:
        raise OilEntryNotFound('Oliebeurt niet gevonden.')
    return get_entry(con, entry_id)


def delete_entry(con: sqlite3.Connection, entry_id: int, *, confirmed: bool) -> None:
    if not confirmed:
        raise ValueError('Bevestig het verwijderen van deze oliebeurt.')
    cur = con.execute(
        "DELETE FROM oil_maintenance WHERE id=? AND vehicle_key='primary'",
        (entry_id,),
    )
    if cur.rowcount != 1:
        raise OilEntryNotFound('Oliebeurt niet gevonden.')
