"""
VietTravel AI - Budget Agent (Phase 2 & Phase 3)
Dedicated agent responsible for evaluating travel finances, validating itemized costs,
invoking the deterministic budget calculator via ToolRegistry, and suggesting savings.

Principles:
1. Tool-governed calculation: executes ONLY registered tools ('calculate_trip_budget').
2. Transparent cost breakdown: separates transportation, accommodation, food, activities, and contingency.
3. Distinction between group total and per-person cost.
4. Distinguishes verified prices, user-provided constraints, and model-estimated baselines.
5. Explicit assumptions: numbers of nights, rooms, travelers per room, meals, transit mode.
6. Non-destructive state: updates session_state.budget_breakdown but NEVER mutates session_state.constraints.budget.
7. Actionable savings suggestions: provides tailored cost-cutting alternatives when budget ceiling is exceeded.
"""

import time
import math
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict

from src.services.ai.state import (
    TravelSessionState,
    TripConstraints,
    BudgetBreakdown,
    AgentTaskTrace
)
from src.services.ai.tools.registry import (
    ToolRegistry,
    ToolExecutionResult,
    global_tool_registry
)

logger = logging.getLogger(__name__)


class CostItem(BaseModel):
    """
    Itemized cost component with explicit provenance and verification status.
    """
    model_config = ConfigDict(extra="ignore")

    category: str = Field(..., description="'accommodation' | 'food' | 'transportation' | 'activities' | 'contingency'")
    description: str = Field(..., description="Description of the cost item and basis")
    amount: float = Field(..., ge=0.0, description="Amount in specified currency")
    is_verified: bool = Field(default=False, description="True only if sourced from verified database or ticket receipt")
    source_type: str = Field(
        default="model_estimate",
        description="'verified_source' | 'user_provided' | 'model_estimate' | 'unknown'"
    )
    source_attribution: Optional[str] = Field(default=None, description="Source name, URL, or provider")


class BudgetAgentResult(BaseModel):
    """
    Structured response returned by BudgetAgent.
    """
    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., description="'sufficient_data' | 'partial_estimate' | 'missing_data' | 'tool_failed'")
    breakdown: Optional[BudgetBreakdown] = Field(default=None, description="Populated BudgetBreakdown object if calculated")
    itemized_costs: List[CostItem] = Field(default_factory=list, description="Itemized costs with provenance")
    savings_suggestions: List[str] = Field(default_factory=list, description="Suggested options to cut expenses")
    assumptions: List[str] = Field(default_factory=list, description="Explicit assumptions made in the calculation")
    warnings: List[str] = Field(default_factory=list, description="Financial or data coverage warnings")
    tools_executed: List[str] = Field(default_factory=list, description="Names of tools called")
    message: str = Field(default="", description="Summary message")
    execution_time_ms: float = Field(default=0.0, description="Execution duration in milliseconds")


class BudgetAgent:
    """
    Agent specialized in evaluating trip budgets using the ToolRegistry and deterministic rules.
    """
    def __init__(self, tool_registry: Optional[ToolRegistry] = None):
        self.tool_registry = tool_registry or global_tool_registry

    def evaluate_budget(
        self,
        session_state: TravelSessionState,
        known_ticket_costs: Optional[float] = None,
        custom_items: Optional[List[CostItem]] = None,
        max_tool_budget: int = 3
    ) -> BudgetAgentResult:
        """
        Calculates trip budget from session constraints and itemized inputs without guessing missing critical facts.
        """
        start_time = time.perf_counter()
        tools_executed: List[str] = []
        warnings: List[str] = []
        assumptions: List[str] = []
        savings_suggestions: List[str] = []
        itemized_costs: List[CostItem] = []

        constraints = session_state.constraints

        # 1. Resolve Trip Duration
        duration_days = constraints.duration_days
        if not duration_days and constraints.start_date and constraints.end_date:
            try:
                d1 = datetime.strptime(constraints.start_date, "%Y-%m-%d")
                d2 = datetime.strptime(constraints.end_date, "%Y-%m-%d")
                diff = (d2 - d1).days + 1
                if diff > 0:
                    duration_days = diff
            except ValueError:
                pass

        # Check for missing critical duration
        if not duration_days:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            msg = "Thiếu thông tin về thời lượng chuyến đi (số ngày) hoặc ngày đi/về để tính toán ngân sách."
            trace = AgentTaskTrace(
                agent_name="budget_agent",
                status="failed",
                input_summary="Missing duration_days and start/end dates",
                output_summary="Failed: missing_data",
                execution_time_ms=round(duration_ms, 2)
            )
            session_state.add_trace(trace)
            return BudgetAgentResult(
                status="missing_data",
                breakdown=None,
                itemized_costs=[],
                savings_suggestions=[],
                assumptions=[],
                warnings=["Chưa có thông tin số ngày hoặc khoảng thời gian du lịch."],
                tools_executed=[],
                message=msg,
                execution_time_ms=round(duration_ms, 2)
            )

        # 2. Extract Travel Parameters
        group_size = max(1, constraints.group_size)
        destination = constraints.destination or "Điểm đến tiêu biểu"
        travel_style = constraints.travel_style or "tiêu chuẩn"
        transportation_mode = constraints.transportation or "tự túc"
        budget_limit = constraints.budget  # May be None, which means user has not specified a limit

        nights = max(0, duration_days - 1)
        rooms_needed = max(1, math.ceil(group_size / 2.0))
        estimated_meals = duration_days * 3

        assumptions.extend([
            f"Thời lượng: {duration_days} ngày ({nights} đêm).",
            f"Số người trong đoàn: {group_size} người.",
            f"Giả định lưu trú: {rooms_needed} phòng (sức chứa tiêu chuẩn 2 người/phòng, tự động điều chỉnh nếu yêu cầu phòng đơn).",
            f"Số bữa ăn: ước tính ~{estimated_meals} bữa chính cho cả đoàn ({duration_days} ngày x 3 bữa/ngày).",
            f"Phong cách chi tiêu: {travel_style}.",
            f"Phương tiện di chuyển: {transportation_mode}.",
            "Khoản dự phòng phát sinh: tính bằng 10% tổng chi phí trước dự phòng."
        ])

        # If custom_items contains activities costs and known_ticket_costs is not set, resolve it
        if known_ticket_costs is None and custom_items:
            custom_act = [item.amount for item in custom_items if item.category == "activities" and item.is_verified]
            if custom_act:
                known_ticket_costs = sum(custom_act)

        # 3. Execute Tool 'calculate_trip_budget' via ToolRegistry
        call_tracker: Dict[str, int] = {"total_calls": 0}
        tool_args = {
            "duration_days": duration_days,
            "group_size": group_size,
            "travel_style": travel_style,
            "transportation_mode": transportation_mode,
            "destination": destination,
            "total_budget_limit": budget_limit,
            "currency": constraints.currency or "VND",
            "ticket_costs_known": known_ticket_costs
        }

        tool_res: ToolExecutionResult = self.tool_registry.execute(
            tool_name="calculate_trip_budget",
            arguments=tool_args,
            call_count_tracker=call_tracker,
            max_budget=max_tool_budget
        )
        tools_executed.append("calculate_trip_budget")

        if not tool_res.success or not tool_res.data:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = tool_res.error or "Công cụ tính toán ngân sách gặp sự cố."
            trace = AgentTaskTrace(
                agent_name="budget_agent",
                status="failed",
                input_summary=f"Duration: {duration_days}, Group: {group_size}",
                output_summary=f"Failed: {err_msg}",
                tools_called=tools_executed,
                error=err_msg,
                execution_time_ms=round(duration_ms, 2)
            )
            session_state.add_trace(trace)
            return BudgetAgentResult(
                status="tool_failed",
                breakdown=None,
                itemized_costs=[],
                savings_suggestions=[],
                assumptions=assumptions,
                warnings=[err_msg],
                tools_executed=tools_executed,
                message=f"Lỗi tính toán ngân sách: {err_msg}",
                execution_time_ms=round(duration_ms, 2)
            )

        data = tool_res.data

        # 4. Construct Itemized Costs with Explicit Provenance
        accom_cost = float(data.get("accommodation_cost", 0.0))
        food_cost = float(data.get("food_cost", 0.0))
        trans_cost = float(data.get("transportation_cost", 0.0))
        act_cost = float(data.get("activities_cost", 0.0))
        cont_cost = float(data.get("contingency_cost", 0.0))

        # Check for custom item overrides
        custom_by_cat: Dict[str, List[CostItem]] = {}
        if custom_items:
            for c_item in custom_items:
                custom_by_cat.setdefault(c_item.category, []).append(c_item)

        # Accommodation item
        if "accommodation" in custom_by_cat:
            matching = custom_by_cat["accommodation"]
            itemized_costs.extend(matching)
            accom_cost = sum(i.amount for i in matching)
        else:
            itemized_costs.append(CostItem(
                category="accommodation",
                description=f"Lưu trú {nights} đêm cho {rooms_needed} phòng",
                amount=accom_cost,
                is_verified=False,
                source_type="model_estimate",
                source_attribution="Baseline rate standard 3-star / homestay"
            ))

        # Food item
        if "food" in custom_by_cat:
            matching = custom_by_cat["food"]
            itemized_costs.extend(matching)
            food_cost = sum(i.amount for i in matching)
        else:
            itemized_costs.append(CostItem(
                category="food",
                description=f"Ăn uống {duration_days} ngày (~{estimated_meals} bữa) cho {group_size} người",
                amount=food_cost,
                is_verified=False,
                source_type="model_estimate",
                source_attribution="Baseline per person daily dining standard"
            ))

        # Transportation item
        if "transportation" in custom_by_cat:
            matching = custom_by_cat["transportation"]
            itemized_costs.extend(matching)
            trans_cost = sum(i.amount for i in matching)
        else:
            itemized_costs.append(CostItem(
                category="transportation",
                description=f"Di chuyển liên tỉnh và nội thành ({transportation_mode})",
                amount=trans_cost,
                is_verified=False,
                source_type="model_estimate",
                source_attribution="Standard transit estimate"
            ))

        # Activities item
        if "activities" in custom_by_cat:
            matching = custom_by_cat["activities"]
            itemized_costs.extend(matching)
            act_cost = sum(i.amount for i in matching)
        else:
            act_verified = bool(known_ticket_costs is not None and known_ticket_costs > 0)
            itemized_costs.append(CostItem(
                category="activities",
                description="Vé tham quan và hoạt động trải nghiệm",
                amount=act_cost,
                is_verified=act_verified,
                source_type="verified_source" if act_verified else "model_estimate",
                source_attribution="Attraction pricing database" if act_verified else "Estimated attraction allowance"
            ))

        # Other custom categories (e.g. shopping, visa, etc.)
        other_costs = 0.0
        for cat, items in custom_by_cat.items():
            if cat not in ("accommodation", "food", "transportation", "activities", "contingency"):
                itemized_costs.extend(items)
                other_costs += sum(i.amount for i in items)

        # Contingency item
        if "contingency" in custom_by_cat:
            matching = custom_by_cat["contingency"]
            itemized_costs.extend(matching)
            cont_cost = sum(i.amount for i in matching)
        else:
            subtotal = accom_cost + food_cost + trans_cost + act_cost + other_costs
            cont_cost = round(subtotal * 0.10, -3)
            itemized_costs.append(CostItem(
                category="contingency",
                description="Quỹ dự phòng phát sinh (10%)",
                amount=cont_cost,
                is_verified=False,
                source_type="model_estimate",
                source_attribution="10% deterministic buffer"
            ))

        # Recalculate totals ensuring no double-counting
        total_cost = round(accom_cost + food_cost + trans_cost + act_cost + other_costs + cont_cost, -3)
        cost_per_person = round(total_cost / max(1, group_size), -3)
        exceeds = (total_cost > budget_limit) if (budget_limit is not None and budget_limit > 0) else False

        # Check verification status across items
        non_contingency = [item for item in itemized_costs if item.category != "contingency"]
        has_unknown = any(item.source_type == "unknown" or item.amount == 0.0 and not item.is_verified for item in non_contingency)
        all_verified = bool(non_contingency) and all(item.is_verified for item in non_contingency)

        if has_unknown:
            warnings.append("Một số hạng mục chi phí chưa có thông tin giá chính xác và đang cần bổ sung.")

        # 5. Populate BudgetBreakdown Object
        is_breakdown_estimated = not all_verified
        breakdown = BudgetBreakdown(
            currency=data.get("currency", "VND"),
            transportation_cost=trans_cost,
            accommodation_cost=accom_cost,
            food_cost=food_cost,
            activities_cost=act_cost,
            contingency_cost=cont_cost,
            total_cost=total_cost,
            cost_per_person=cost_per_person,
            group_size=group_size,
            is_estimated=is_breakdown_estimated,
            calculation_basis=data.get("calculation_basis", assumptions),
            exceeds_budget=exceeds,
            budget_limit=budget_limit
        )

        # 6. Savings Suggestions if Exceeds Budget
        if exceeds and budget_limit is not None and budget_limit > 0:
            diff = total_cost - budget_limit
            warnings.append(
                f"Tổng chi phí ước tính ({total_cost:,.0f} đ) vượt ngân sách mong muốn ({budget_limit:,.0f} đ) khoảng {diff:,.0f} đ."
            )
            savings_suggestions.extend([
                "Chuyển sang phong cách lưu trú 'tiết kiệm' (homestay hoặc hostel) để giảm khoảng 35% - 40% chi phí phòng nghỉ.",
                "Lựa chọn ẩm thực địa phương bình dân hoặc tự túc kết hợp để tiết kiệm chi phí ăn uống.",
                "Ưu tiên các điểm tham quan danh thắng miễn phí (bãi biển, phố đi bộ, công viên) thay vì các dịch vụ giải trí tính phí cao.",
                "Cân nhắc rút ngắn thời lượng chuyến đi bớt 1 ngày nếu muốn giữ nguyên mức tiện nghi cao cấp."
            ])

        # 7. Update State (Strict Rule: Do NOT overwrite constraints.budget!)
        session_state.budget_breakdown = breakdown

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        if all_verified:
            status_label = "sufficient_data"
        else:
            status_label = "partial_estimate"

        msg = f"Đã tính toán chi tiết ngân sách: {total_cost:,.0f} đ ({cost_per_person:,.0f} đ/người)."
        if exceeds:
            msg += f" (Cảnh báo: Vượt ngân sách {diff:,.0f} đ)."

        # 8. Record Execution Trace
        trace = AgentTaskTrace(
            agent_name="budget_agent",
            status="completed",
            input_summary=f"Duration: {duration_days}d, Group: {group_size}, BudgetLimit: {budget_limit}",
            output_summary=f"Total: {total_cost:,.0f} VND, Exceeds: {exceeds}",
            tools_called=tools_executed,
            execution_time_ms=round(duration_ms, 2)
        )
        session_state.add_trace(trace)

        return BudgetAgentResult(
            status=status_label,
            breakdown=breakdown,
            itemized_costs=itemized_costs,
            savings_suggestions=savings_suggestions,
            assumptions=assumptions,
            warnings=warnings,
            tools_executed=tools_executed,
            message=msg,
            execution_time_ms=round(duration_ms, 2)
        )


# Global default instance
default_budget_agent = BudgetAgent()


def evaluate_trip_budget(
    session_state: TravelSessionState,
    known_ticket_costs: Optional[float] = None,
    custom_items: Optional[List[CostItem]] = None,
    tool_registry: Optional[ToolRegistry] = None
) -> BudgetAgentResult:
    """Helper function interface for Budget Agent."""
    agent = BudgetAgent(tool_registry=tool_registry)
    return agent.evaluate_budget(
        session_state=session_state,
        known_ticket_costs=known_ticket_costs,
        custom_items=custom_items
    )
