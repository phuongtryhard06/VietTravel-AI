"""
VietTravel AI - Deterministic Budget Calculator Tool (Phase 3 Core Foundation)
Calculates travel expenses using deterministic arithmetic rather than LLM guesswork.

Principles:
1. Transparent breakdown: transportation, accommodation, food, activities/tickets, and contingency.
2. Accurately distinguishes cost per person vs group total.
3. Compares total against user's budget ceiling and flags over-budget status.
4. Explains assumptions clearly.
"""

import math
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class CalculateBudgetInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration_days: int = Field(default=1, ge=1, le=30, description="Duration of trip in days")
    group_size: int = Field(default=1, ge=1, le=50, description="Number of travelers")
    travel_style: str = Field(default="tiêu chuẩn", description="'tiết kiệm' | 'tiêu chuẩn' | 'cao cấp'")
    transportation_mode: Optional[str] = Field(default="tự túc", description="'máy bay' | 'ô tô / xe khách' | 'xe máy' | 'tàu hỏa' | 'tự túc'")
    destination: Optional[str] = Field(default=None, description="Primary destination for contextual base rates")
    total_budget_limit: Optional[float] = Field(default=None, ge=0.0, description="User budget ceiling in specified currency")
    currency: str = Field(default="VND", description="Currency (default VND)")
    ticket_costs_known: Optional[float] = Field(default=None, ge=0.0, description="Actual verified ticket costs if available")


# Daily baseline unit costs in VND
TIERS = {
    "tiết kiệm": {
        "hotel_per_room_night": 300000.0,    # Hostel / Homestay bình dân
        "food_per_person_day": 160000.0,      # Ăn uống bình dân (50k sáng + 55k trưa + 55k tối)
        "local_transit_per_day": 70000.0,     # Thuê xe máy chia đôi hoặc xe bus
        "activities_per_day": 60000.0         # Vé tham quan di tích / công viên
    },
    "tiêu chuẩn": {
        "hotel_per_room_night": 750000.0,    # Khách sạn 3 sao tiện nghi
        "food_per_person_day": 320000.0,      # Ăn uống nhà hàng & đặc sản địa phương
        "local_transit_per_day": 150000.0,    # Taxi công nghệ / thuê xe riêng
        "activities_per_day": 120000.0        # Điểm tham quan, bảo tàng, cáp treo cơ bản
    },
    "cao cấp": {
        "hotel_per_room_night": 220000.0 * 10, # 2.2M / đêm khách sạn 4-5 sao hoặc resort
        "food_per_person_day": 850000.0,      # Nhà hàng cao cấp, hải sản, buffet
        "local_transit_per_day": 400000.0,    # Xe ô tô riêng cao cấp
        "activities_per_day": 350000.0        # Tour du thuyền, vé trọn gói, trải nghiệm đặc biệt
    }
}

INTERCITY_TRANSIT_ESTIMATE = {
    "máy bay": 1800000.0,         # Khứ hồi trung bình / người
    "ô tô / xe khách": 350000.0,   # Vé xe giường nằm khứ hồi / người
    "tàu hỏa": 550000.0,          # Vé tàu khứ hồi / người
    "xe máy": 150000.0,           # Xăng xe / người
    "tự túc": 100000.0
}


def calculate_trip_budget(
    duration_days: int = 1,
    group_size: int = 1,
    travel_style: str = "tiêu chuẩn",
    transportation_mode: Optional[str] = "tự túc",
    destination: Optional[str] = None,
    total_budget_limit: Optional[float] = None,
    currency: str = "VND",
    ticket_costs_known: Optional[float] = None
) -> Dict[str, Any]:
    """
    Computes deterministic itemized budget breakdown with transparent arithmetic.
    """
    style_key = travel_style.lower().strip() if travel_style else "tiêu chuẩn"
    if "tiết kiệm" in style_key or "rẻ" in style_key or "phượt" in style_key or "budget" in style_key:
        rates = TIERS["tiết kiệm"]
        selected_tier = "tiết kiệm"
    elif "cao cấp" in style_key or "sang trọng" in style_key or "luxury" in style_key or "resort" in style_key:
        rates = TIERS["cao cấp"]
        selected_tier = "cao cấp"
    else:
        rates = TIERS["tiêu chuẩn"]
        selected_tier = "tiêu chuẩn"

    nights = max(0, duration_days - 1)
    # 2 people per room assumed
    rooms_needed = max(1, math.ceil(group_size / 2.0))

    # 1. Accommodation
    accommodation_cost = float(nights * rooms_needed * rates["hotel_per_room_night"])

    # 2. Food & Dining
    food_cost = float(duration_days * group_size * rates["food_per_person_day"])

    # 3. Transportation (Intercity + Local daily)
    trans_mode_key = (transportation_mode or "tự túc").lower()
    intercity_rate = 100000.0
    for mode, cost in INTERCITY_TRANSIT_ESTIMATE.items():
        if mode in trans_mode_key:
            intercity_rate = cost
            break

    intercity_total = float(intercity_rate * group_size)
    local_transit_total = float(duration_days * group_size * rates["local_transit_per_day"])
    transportation_cost = intercity_total + local_transit_total

    # 4. Activities & Tickets
    if ticket_costs_known is not None and ticket_costs_known > 0:
        activities_cost = float(ticket_costs_known)
    else:
        activities_cost = float(duration_days * group_size * rates["activities_per_day"])

    # Subtotal before contingency
    subtotal = accommodation_cost + food_cost + transportation_cost + activities_cost

    # 5. Contingency buffer (10%)
    contingency_cost = round(subtotal * 0.10, -3)

    # Total
    total_cost = round(subtotal + contingency_cost, -3)
    cost_per_person = round(total_cost / max(1, group_size), -3)

    exceeds_budget = False
    if total_budget_limit is not None and total_budget_limit > 0:
        exceeds_budget = (total_cost > total_budget_limit)

    basis = [
        f"Phong cách: {selected_tier.capitalize()}",
        f"Số ngày: {duration_days} ngày ({nights} đêm), Đoàn: {group_size} người",
        f"Lưu trú: {rooms_needed} phòng x {nights} đêm (~{rates['hotel_per_room_night']:,.0f} đ/phòng/đêm)",
        f"Ẩm thực: ~{rates['food_per_person_day']:,.0f} đ/người/ngày",
        f"Di chuyển: ~{rates['local_transit_per_day']:,.0f} đ/ngày nội thành + di chuyển liên tỉnh",
        f"Dự phòng phát sinh: 10% tổng chi phí ({contingency_cost:,.0f} đ)"
    ]

    return {
        "currency": currency,
        "travel_style": selected_tier,
        "duration_days": duration_days,
        "group_size": group_size,
        "accommodation_cost": accommodation_cost,
        "food_cost": food_cost,
        "transportation_cost": transportation_cost,
        "activities_cost": activities_cost,
        "contingency_cost": contingency_cost,
        "total_cost": total_cost,
        "cost_per_person": cost_per_person,
        "is_estimated": True,
        "calculation_basis": basis,
        "exceeds_budget": exceeds_budget,
        "budget_limit": total_budget_limit
    }
