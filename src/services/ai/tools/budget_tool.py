"""Deterministic and explicitly labelled trip-budget calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List


DEFAULT_FOOD_PER_PERSON_PER_DAY_VND = 450_000
DEFAULT_LODGING_PER_ROOM_PER_NIGHT_VND = 700_000
DEFAULT_LOCAL_TRANSPORT_PER_KM_VND = 9_000
DEFAULT_CONTINGENCY_RATIO = Decimal("0.10")


def _money(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_trip_budget(
    travelers: int,
    duration_days: int,
    itinerary_days: Iterable[Dict[str, Any]],
    currency: str = "VND",
    budget_limit: float | None = None,
) -> Dict[str, Any]:
    """Calculate VND totals without using an LLM for arithmetic.

    Defaults are model assumptions, not live prices. POI ticket prices are only
    included when a numeric value was supplied by a registered data source.
    """
    if currency.upper() != "VND":
        return {
            "success": False,
            "error": {
                "code": "unsupported_currency",
                "message": "Only VND can be calculated without a verified exchange-rate tool.",
            },
            "currency": currency.upper(),
        }
    if not 1 <= int(travelers) <= 100 or not 1 <= int(duration_days) <= 60:
        return {
            "success": False,
            "error": {"code": "invalid_input", "message": "travelers and duration_days are out of range."},
        }

    travelers = int(travelers)
    duration_days = int(duration_days)
    total_distance_km = Decimal("0")
    ticket_total = Decimal("0")
    ticket_sources = 0
    for day in itinerary_days or []:
        for leg in day.get("route_legs", []) if isinstance(day, dict) else []:
            if leg.get("distance_km") is not None:
                total_distance_km += Decimal(str(leg["distance_km"]))
        for item in day.get("items", []) if isinstance(day, dict) else []:
            raw_price = item.get("price") if isinstance(item, dict) else None
            if raw_price is None:
                continue
            try:
                ticket_total += Decimal(str(raw_price)) * travelers
                ticket_sources += 1
            except Exception:
                continue

    food = Decimal(DEFAULT_FOOD_PER_PERSON_PER_DAY_VND * travelers * duration_days)
    rooms = (travelers + 1) // 2
    accommodation = Decimal(DEFAULT_LODGING_PER_ROOM_PER_NIGHT_VND * rooms * max(duration_days - 1, 0))
    local_transport = total_distance_km * Decimal(DEFAULT_LOCAL_TRANSPORT_PER_KM_VND)
    subtotal = food + accommodation + local_transport + ticket_total
    contingency = subtotal * DEFAULT_CONTINGENCY_RATIO
    total = subtotal + contingency
    limit = Decimal(str(budget_limit)) if budget_limit is not None else None

    categories = [
        {
            "category": "food",
            "group_total": _money(food),
            "per_person": _money(food / travelers),
            "estimate_status": "assumption",
            "basis": f"{DEFAULT_FOOD_PER_PERSON_PER_DAY_VND:,} VND per traveler per day",
        },
        {
            "category": "accommodation",
            "group_total": _money(accommodation),
            "per_person": _money(accommodation / travelers),
            "estimate_status": "assumption",
            "basis": f"{rooms} assumed room(s), {DEFAULT_LODGING_PER_ROOM_PER_NIGHT_VND:,} VND per room per night",
        },
        {
            "category": "local_transport",
            "group_total": _money(local_transport),
            "per_person": _money(local_transport / travelers),
            "estimate_status": "estimate" if total_distance_km else "not_included",
            "basis": f"{float(total_distance_km):.1f} km × {DEFAULT_LOCAL_TRANSPORT_PER_KM_VND:,} VND/km",
        },
        {
            "category": "attractions",
            "group_total": _money(ticket_total),
            "per_person": _money(ticket_total / travelers),
            "estimate_status": "retrieved" if ticket_sources else "not_included",
            "basis": f"{ticket_sources} numeric POI ticket price(s) supplied by a source",
        },
        {
            "category": "contingency",
            "group_total": _money(contingency),
            "per_person": _money(contingency / travelers),
            "estimate_status": "assumption",
            "basis": "10% of the above subtotal",
        },
    ]
    suggestions: List[str] = []
    within_budget = None
    if limit is not None:
        within_budget = total <= limit
        if not within_budget:
            suggestions.append("Reduce paid activities, accommodation assumptions, or trip duration; no live prices were used.")

    return {
        "success": True,
        "currency": "VND",
        "travelers": travelers,
        "duration_days": duration_days,
        "categories": categories,
        "group_total": _money(total),
        "per_person_total": _money(total / travelers),
        "budget_limit": _money(limit) if limit is not None else None,
        "within_budget": within_budget,
        "suggestions": suggestions,
        "assumptions": [
            "Default food and accommodation values are planning assumptions, not live quotations.",
            "Transport uses only available route distance and is not a booking price.",
        ],
    }
