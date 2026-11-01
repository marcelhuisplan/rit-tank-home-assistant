"""Read-only oil-check guidance and estimated monthly fuel net, without changing fiscal totals."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median
from typing import Any, Callable


def _date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def oil_guidance(entries: list[dict[str, Any]], checks: list[dict[str, Any]],
                 current_odometer: float | None, today: date) -> dict[str, Any]:
    """Use cautious defaults until at least two valid top-up intervals are available.

    Top-up volume is not a precise engine-consumption measurement because oil
    levels before and after topping up may differ. Oil changes reset the cycle.
    """
    items = sorted(
        [{**e, "_date": _date(e["performed_on"])} for e in entries]
        + [{**c, "_date": _date(c["checked_on"]), "kind": "check"} for c in checks],
        key=lambda x: (x["_date"], int(x.get("id") or 0)),
    )
    topups: list[dict[str, Any]] = []
    intervals: list[tuple[float, int, int]] = []
    for item in items:
        if item["kind"] == "change":
            topups = []
            intervals = []
        elif item["kind"] == "top_up":
            if topups:
                previous = topups[-1]
                km = float(item["odometer"]) - float(previous["odometer"])
                days = (item["_date"] - previous["_date"]).days
                if km > 0 and days > 0 and km <= 50000 and days <= 365:
                    intervals.append((km, days, int(item["amount_ml"])))
            topups.append(item)
    confident = len(intervals) >= 2
    km_interval, day_interval = 1500, 21
    estimated = None
    if confident:
        total_km = sum(x[0] for x in intervals)
        estimated = round(sum(x[2] for x in intervals) / total_km, 5) if total_km else None
        # A conservative lead time before the median historic top-up spacing.
        km_interval = max(500, min(5000, round(float(median(x[0] for x in intervals)) * .8)))
        day_interval = max(7, min(60, round(float(median(x[1] for x in intervals)) * .8)))
        # Heavier top-ups per km imply shorter check intervals; never lengthen.
        if estimated and estimated > 0:
            km_interval = min(km_interval, max(500, round(250 / estimated)))
    latest = max(items, key=lambda x: (x["_date"], int(x.get("id") or 0))) if items else None
    if latest is None:
        return dict(status="no_history", reminder=False, learned=False,
                    interval_km=km_interval, interval_days=day_interval,
                    next_km=None, next_date=None, remaining_km=None,
                    remaining_days=None, consumption_l_per_1000=None,
                    intervals=0)
    next_km = int(latest["odometer"]) + km_interval
    next_date = latest["_date"] + timedelta(days=day_interval)
    remaining_km = None if current_odometer is None else round(next_km - float(current_odometer), 0)
    days_left = (next_date - today).days
    # A concerning recorded level remains actionable even when the usual
    # mileage/time interval has not elapsed. A later normal check or top-up
    # clears it; never silently postpone a below-minimum finding.
    attention = (latest.get("kind") == "check" and
                 latest.get("level_status") in {
                     "low", "below_min", "high", "unreadable", "recheck"})
    due = attention or days_left <= 0 or (remaining_km is not None and remaining_km <= 0)
    return dict(status="due" if due else "scheduled", reminder=due,
                level_attention=bool(attention),
                learned=confident, interval_km=km_interval, interval_days=day_interval,
                next_km=next_km, next_date=next_date.isoformat(),
                remaining_km=remaining_km, remaining_days=days_left,
                consumption_l_per_1000=(round(estimated, 3) if estimated is not None else None),
                intervals=len(intervals))


def monthly_finances(trips: list[tuple[dict[str, Any], list[dict[str, Any]]]],
                     cycles: list[dict[str, Any]], rate: float,
                     start: datetime, end: datetime,
                     parse_dt: Callable[[str], datetime]) -> dict[str, Any]:
    """Estimate business kilometres on the calendar month, without changing trips.

    Crossing month boundaries is prorated by elapsed time between two actual
    odometer measurements and explicitly marked as an estimate.
    """
    business_km = 0.0
    prorated = False
    for trip, stops in trips:
        ordered = sorted(stops, key=lambda s: (int(s.get("sequence_no") or 0), int(s.get("id") or 0)))
        specified_types = any(s.get("segment_trip_type") in ("business", "private") for s in ordered)
        trip_type = str(trip.get("trip_type") or "business")
        for previous, current in zip(ordered, ordered[1:]):
            odo_start, odo_end = float(previous["odometer"]), float(current["odometer"])
            if odo_end <= odo_start:
                continue
            a, b = parse_dt(previous["created_at"]), parse_dt(current["created_at"])
            if b <= a or b <= start or a >= end:
                continue
            overlap = (min(b, end) - max(a, start)).total_seconds()
            fraction = max(0.0, min(1.0, overlap / (b - a).total_seconds()))
            if fraction < 1:
                prorated = True
            if specified_types:
                share = 1.0 if current.get("segment_trip_type") == "business" else 0.0
            elif trip_type == "private":
                share = 0.0
            elif trip_type == "mixed":
                total = max(1.0, float(ordered[-1]["odometer"]) - float(ordered[0]["odometer"]))
                share = 1.0 - min(1.0, max(0.0, float(trip.get("private_detour_km") or 0) / total))
            else:
                share = 1.0
            business_km += (odo_end - odo_start) * fraction * share
    # Complete full-tank cycles, unlike a single fuel purchase, measure both
    # fuel and distance. Weight cycles by distance and never use zero as missing.
    valid_cycles = [c for c in cycles
                    if float(c.get("km") or 0) > 0 and float(c.get("cost") or 0) > 0]
    total_km = sum(float(c["km"]) for c in valid_cycles)
    cost_per_km = (sum(float(c["cost"]) for c in valid_cycles) / total_km) if total_km > 0 else None
    gross = business_km * max(0.0, rate)
    fuel = business_km * cost_per_km if cost_per_km is not None else None
    return {
        "month": start.strftime("%Y-%m"),
        "business_km": round(business_km, 1),
        "rate_per_km": round(max(0.0, rate), 3),
        "gross_reimbursement": round(gross, 2),
        "fuel_cost_per_km": round(cost_per_km, 4) if cost_per_km is not None else None,
        "allocated_fuel_cost": round(fuel, 2) if fuel is not None else None,
        "balance_after_fuel": round(gross - fuel, 2) if fuel is not None else None,
        "estimated": True,
        "prorated_month_boundary": prorated,
        "fuel_cycles": len(valid_cycles),
    }


def finance_overview(trips: list[tuple[dict[str, Any], list[dict[str, Any]]]],
                     cycles: list[dict[str, Any]], rate: float, now: datetime,
                     requested_month: str | None,
                     parse_dt: Callable[[str], datetime]) -> dict[str, Any]:
    """Monthly navigator and current-year balance based on the same trip segments.

    Both figures remain indicative; fiscal reports and historical records are
    not changed. Only real trip stop dates establish the first available month.
    """
    import re

    tz = now.tzinfo
    current_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def first_of_next_month(start: datetime) -> datetime:
        return (start.replace(year=start.year + 1, month=1) if start.month == 12
                else start.replace(month=start.month + 1))

    first_dates = [parse_dt(stop["created_at"]) for _, stops in trips for stop in stops
                   if stop.get("created_at")]
    earliest = min(first_dates).replace(day=1, hour=0, minute=0, second=0, microsecond=0) if first_dates else current_start
    # A future-dated anomaly must not let the navigator jump to future months.
    earliest = min(earliest, current_start)
    selected = current_start
    if requested_month and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", requested_month):
        try:
            candidate = datetime.fromisoformat(requested_month + "-01").replace(tzinfo=tz)
            if earliest <= candidate <= current_start:
                selected = candidate
        except ValueError:
            pass

    selected_end = first_of_next_month(selected)
    if selected == current_start:
        # Ignore future-dated stops while retaining today's real registrations.
        selected_end = min(selected_end, (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
    monthly = monthly_finances(trips, cycles, rate, selected, selected_end, parse_dt)

    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    year_end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    annual = monthly_finances(trips, cycles, rate, year_start, year_end, parse_dt)
    annual["year"] = now.year
    annual["through_date"] = now.date().isoformat()
    annual["since_first_registration"] = min(first_dates).date().isoformat() if first_dates else None
    return {
        "monthly": monthly,
        "year": annual,
        "navigation": {
            "selected_month": selected.strftime("%Y-%m"),
            "earliest_month": earliest.strftime("%Y-%m"),
            "latest_month": current_start.strftime("%Y-%m"),
            "has_previous": selected > earliest,
            "has_next": selected < current_start,
            "has_registrations": bool(first_dates),
        },
    }
