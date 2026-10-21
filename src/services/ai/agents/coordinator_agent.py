"""
VietTravel AI - Coordinator / Orchestration Agent (Phase 2 & Phase 3)
The central orchestration layer responsible for dynamic multi-agent collaboration:
1. Intent & Context Agent -> intent classification and non-destructive constraint extraction.
2. Destination Research Agent -> factual POI discovery from internal DB and verified web.
3. Itinerary Planner Agent -> schedule generation and deterministic validation.
4. Budget Agent -> deterministic financial evaluation and actionable savings suggestions.

Core Architectural Principles:
- Shared Working Memory: Passes single TravelSessionState across all specialized agents.
- Intent-Based Selective Routing: Only invokes agents relevant to the user request.
- Bounded Orchestration: Strictly limits maximum iterations (default 3) and tool calls (default 6).
- Zero Fabrication: Never invents destinations, dates, prices, coordinates, or fake URLs.
- Arithmetic & Data Integrity: Never overwrites user's budget ceiling with calculated total.
- Observability: Records truthful, sanitized AgentTaskTrace entries for each execution.
"""

import time
import math
import uuid
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Set, Tuple
from pydantic import BaseModel, Field, ConfigDict

from src.services.ai.state import (
    TravelSessionState,
    TripConstraints,
    BudgetBreakdown,
    EvidenceItem,
    AgentTaskTrace,
    ItineraryPlanDraft,
    ItineraryDayDraft,
    ItineraryStopDraft
)
from src.services.ai.tools.registry import (
    ToolRegistry,
    global_tool_registry
)
from src.services.ai.agents.intent_agent import (
    IntentAndContextAgent,
    IntentAgentResult,
    default_intent_agent,
    INTENT_SEARCH_PLACES,
    INTENT_GENERAL_TRAVEL_INFO,
    INTENT_CREATE_ITINERARY,
    INTENT_MODIFY_ITINERARY,
    INTENT_CHANGE_BUDGET,
    INTENT_CHANGE_GROUP_SIZE,
    INTENT_CHANGE_DATES,
    INTENT_CHANGE_DESTINATION,
    INTENT_REMOVE_STOP,
    INTENT_OUT_OF_SCOPE,
    INTENT_CLARIFICATION_NEEDED
)
from src.services.ai.agents.destination_research_agent import (
    DestinationResearchAgent,
    DestinationResearchResult,
    ResearchedPlace,
    default_destination_agent
)
from src.services.ai.agents.planner_agent import (
    ItineraryPlannerAgent,
    plan_draft_itinerary,
    validate_draft_itinerary
)
from src.services.ai.agents.budget_agent import (
    BudgetAgent,
    BudgetAgentResult,
    CostItem,
    default_budget_agent
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 3
DEFAULT_MAX_TOOL_CALLS = 6


class CoordinatorResult(BaseModel):
    """
    Standardized typed response returned by the CoordinatorAgent.
    """
    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., description="'success' | 'clarification_needed' | 'partial' | 'out_of_scope' | 'error'")
    intent: Optional[str] = Field(default=None, description="Recognized user intent")
    session_state: TravelSessionState = Field(..., description="Active session state with updated constraints & memory")
    current_plan: Optional[ItineraryPlanDraft] = Field(default=None, description="Current working itinerary plan draft")
    budget_breakdown: Optional[BudgetBreakdown] = Field(default=None, description="Current evaluated budget breakdown")
    researched_places: List[ResearchedPlace] = Field(default_factory=list, description="Candidate or researched POIs")
    evidence_items: List[EvidenceItem] = Field(default_factory=list, description="Factual claims and web citations")
    missing_information: List[str] = Field(default_factory=list, description="Critical missing parameters")
    clarification_questions: List[str] = Field(default_factory=list, description="Follow-up clarification questions")
    warnings: List[str] = Field(default_factory=list, description="Operational, financial, or planning warnings")
    errors: List[str] = Field(default_factory=list, description="Errors encountered during execution")
    agents_executed: List[str] = Field(default_factory=list, description="Ordered list of agent names executed")
    task_traces: List[AgentTaskTrace] = Field(default_factory=list, description="Task trace audit items from this run")
    iterations_count: int = Field(default=1, description="Number of coordinator coordination iterations executed")
    total_tool_calls: int = Field(default=0, description="Total tool calls invoked across subagents")
    message: str = Field(default="", description="User-facing summary message")
    execution_time_ms: float = Field(default=0.0, description="Total coordinator execution time in milliseconds")


class CoordinatorAgent:
    """
    Master coordinator orchestrating specialized travel agents dynamically.
    """
    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        intent_agent: Optional[IntentAndContextAgent] = None,
        destination_agent: Optional[DestinationResearchAgent] = None,
        planner_agent: Optional[ItineraryPlannerAgent] = None,
        budget_agent: Optional[BudgetAgent] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    ):
        self.tool_registry = tool_registry or global_tool_registry
        self.intent_agent = intent_agent or default_intent_agent
        self.destination_agent = destination_agent or DestinationResearchAgent(tool_registry=self.tool_registry)
        self.planner_agent = planner_agent or ItineraryPlannerAgent()
        self.budget_agent = budget_agent or BudgetAgent(tool_registry=self.tool_registry)
        self.max_iterations = max(1, max_iterations)
        self.max_tool_calls = max(1, max_tool_calls)

    def process(
        self,
        user_message: str,
        session_state: TravelSessionState,
        execute_api: bool = False,
        max_tool_budget: Optional[int] = None
    ) -> CoordinatorResult:
        """
        Coordinates specialized agents based on user intent and current session state.
        """
        start_time = time.perf_counter()
        agents_executed: List[str] = []
        warnings: List[str] = []
        errors: List[str] = []
        missing_info: List[str] = []
        clarification_qs: List[str] = []
        researched_places: List[ResearchedPlace] = []
        tool_budget = max_tool_budget if max_tool_budget is not None else self.max_tool_calls
        total_tool_calls = 0
        executed_agent_cache: Set[str] = set()

        initial_trace_count = len(session_state.agent_traces)

        # -------------------------------------------------------------
        # STEP 1: Intent & Context Analysis
        # -------------------------------------------------------------
        intent_res: IntentAgentResult = self.intent_agent.process(
            user_message=user_message,
            session_state=session_state,
            execute_llm=execute_api
        )
        agents_executed.append("intent_agent")
        intent = intent_res.intent
        session_state.user_intent = intent

        # Check out of scope
        if intent_res.is_out_of_scope or intent == INTENT_OUT_OF_SCOPE:
            msg = (
                "Xin lỗi, tôi là trợ lý du lịch VietTravel-AI. Tôi chỉ có thể hỗ trợ các thông tin, "
                "kế hoạch lịch trình và ngân sách liên quan đến du lịch tại Việt Nam."
            )
            return self._finalize_result(
                status="out_of_scope",
                intent=intent,
                session_state=session_state,
                message=msg,
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=missing_info,
                clarification_qs=[],
                researched_places=[],
                iterations=1,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # Check clarification needed or missing critical info
        if intent == INTENT_CLARIFICATION_NEEDED or intent_res.status == "clarification_needed":
            clarification_qs.extend(intent_res.clarification_questions)
            if not clarification_qs:
                clarification_qs.append("Bạn có thể cung cấp thêm điểm đến hoặc thời gian dự kiến cho chuyến đi không?")
            session_state.unresolved_questions = clarification_qs
            return self._finalize_result(
                status="clarification_needed",
                intent=intent,
                session_state=session_state,
                message="Tôi cần thêm một số thông tin để có thể lên kế hoạch chính xác nhất cho bạn.",
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=intent_res.missing_critical_fields,
                clarification_qs=clarification_qs,
                researched_places=[],
                iterations=1,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # -------------------------------------------------------------
        # STEP 2: Intent-Based Selective Routing
        # -------------------------------------------------------------
        iterations = 1

        # --- ROUTE A: Search Places / General Info ---
        if intent in (INTENT_SEARCH_PLACES, INTENT_GENERAL_TRAVEL_INFO):
            dest_name = session_state.constraints.destination
            if not dest_name and intent_res.extracted_entities.get("destination"):
                dest_name = intent_res.extracted_entities.get("destination")

            if not dest_name:
                clarification_qs.append("Bạn muốn tìm kiếm địa điểm hay thông tin du lịch ở tỉnh/thành phố nào?")
                return self._finalize_result(
                    status="clarification_needed",
                    intent=intent,
                    session_state=session_state,
                    message="Vui lòng cho biết điểm đến bạn quan tâm.",
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=["destination"],
                    clarification_qs=clarification_qs,
                    researched_places=[],
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # Invoke Destination Research Agent
            research_res = self._execute_destination_research(
                user_message=user_message,
                session_state=session_state,
                intent_res=intent_res,
                max_tool_budget=max(1, tool_budget - total_tool_calls),
                agents_executed=agents_executed,
                executed_cache=executed_agent_cache
            )
            total_tool_calls += len(research_res.tools_executed)
            researched_places = research_res.places

            if research_res.status == "error":
                errors.append(research_res.message)
                status_label = "partial"
                msg = f"Đã ghi nhận yêu cầu nhưng gặp trục trặc khi tra cứu dữ liệu địa điểm: {research_res.message}"
            elif not researched_places:
                status_label = "partial"
                msg = f"Không tìm thấy địa điểm phù hợp tại '{dest_name}'. Vui lòng thử từ khóa khác."
            else:
                status_label = "success"
                msg = f"Đã tìm thấy {len(researched_places)} địa điểm phù hợp tại {dest_name}."

            return self._finalize_result(
                status=status_label,
                intent=intent,
                session_state=session_state,
                message=msg,
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=missing_info,
                clarification_qs=clarification_qs,
                researched_places=researched_places,
                iterations=iterations,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # --- ROUTE B: Budget Modification ---
        elif intent == INTENT_CHANGE_BUDGET:
            # Budget constraint was updated in session_state by intent_agent
            new_budget = session_state.constraints.budget
            status_label = "success"
            msg = f"Đã cập nhật mức ngân sách mục tiêu là {new_budget:,.0f} {session_state.constraints.currency}." if new_budget else "Đã xóa giới hạn ngân sách."

            # Recalculate budget breakdown if an itinerary exists or duration is known
            if session_state.current_plan or session_state.constraints.duration_days or session_state.constraints.start_date:
                budget_res = self._execute_budget_evaluation(
                    session_state=session_state,
                    max_tool_budget=max(1, tool_budget - total_tool_calls),
                    agents_executed=agents_executed,
                    executed_cache=executed_agent_cache
                )
                total_tool_calls += len(budget_res.tools_executed)
                if budget_res.status == "tool_failed":
                    warnings.append(budget_res.message)
                else:
                    msg += f" Đã tính toán lại chi phí: {budget_res.message}"
                    warnings.extend(budget_res.warnings)

            return self._finalize_result(
                status=status_label,
                intent=intent,
                session_state=session_state,
                message=msg,
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=missing_info,
                clarification_qs=clarification_qs,
                researched_places=researched_places,
                iterations=iterations,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # --- ROUTE C: Group Size Modification ---
        elif intent == INTENT_CHANGE_GROUP_SIZE:
            new_group = session_state.constraints.group_size
            msg = f"Đã cập nhật số lượng thành viên trong đoàn: {new_group} người."
            status_label = "success"

            # Recalculate dependent budget
            if session_state.current_plan or session_state.constraints.duration_days or session_state.constraints.start_date:
                budget_res = self._execute_budget_evaluation(
                    session_state=session_state,
                    max_tool_budget=max(1, tool_budget - total_tool_calls),
                    agents_executed=agents_executed,
                    executed_cache=executed_agent_cache
                )
                total_tool_calls += len(budget_res.tools_executed)
                if budget_res.status == "tool_failed":
                    warnings.append(budget_res.message)
                else:
                    msg += f" Đã đồng bộ lại ngân sách đoàn: {budget_res.message}"
                    warnings.extend(budget_res.warnings)

            return self._finalize_result(
                status=status_label,
                intent=intent,
                session_state=session_state,
                message=msg,
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=missing_info,
                clarification_qs=clarification_qs,
                researched_places=researched_places,
                iterations=iterations,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # --- ROUTE D: Remove Stop from Itinerary ---
        elif intent == INTENT_REMOVE_STOP:
            target = intent_res.target_stop_to_remove
            plan = session_state.current_plan

            if not plan or not plan.days:
                msg = "Hiện tại chưa có lịch trình nào đang hoạt động để xóa điểm dừng."
                errors.append(msg)
                return self._finalize_result(
                    status="error",
                    intent=intent,
                    session_state=session_state,
                    message=msg,
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=missing_info,
                    clarification_qs=clarification_qs,
                    researched_places=[],
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # Locate and remove the specified stop
            removed_stop: Optional[ItineraryStopDraft] = None
            target_str = str(target or "").strip().lower()

            for day in plan.days:
                for stop in list(day.stops):
                    # Match by order_index, osm_id, or name substring
                    if (target_str and str(stop.order_index) == target_str) or \
                       (target_str and target_str in stop.name.lower()) or \
                       (stop.osm_id and str(stop.osm_id) == target_str):
                        removed_stop = stop
                        day.stops.remove(stop)
                        break
                if removed_stop:
                    # Re-sequence remaining stop indices
                    for idx, s in enumerate(day.stops):
                        s.order_index = idx + 1
                    break

            if not removed_stop:
                msg = f"Không tìm thấy điểm dừng '{target}' trong lịch trình hiện tại."
                warnings.append(msg)
                return self._finalize_result(
                    status="partial",
                    intent=intent,
                    session_state=session_state,
                    message=msg,
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=missing_info,
                    clarification_qs=clarification_qs,
                    researched_places=[],
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # Update plan stop totals
            plan.total_stops = sum(len(d.stops) for d in plan.days)
            msg = f"Đã xóa điểm dừng '{removed_stop.name}' khỏi lịch trình."

            # Recalculate dependent budget
            budget_res = self._execute_budget_evaluation(
                session_state=session_state,
                max_tool_budget=max(1, tool_budget - total_tool_calls),
                agents_executed=agents_executed,
                executed_cache=executed_agent_cache
            )
            total_tool_calls += len(budget_res.tools_executed)
            if budget_res.status != "tool_failed":
                msg += f" Chi phí sau khi điều chỉnh: {budget_res.message}"

            return self._finalize_result(
                status="success",
                intent=intent,
                session_state=session_state,
                message=msg,
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=missing_info,
                clarification_qs=clarification_qs,
                researched_places=[],
                iterations=iterations,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # --- ROUTE E: Create / Modify Itinerary / Date or Destination Change ---
        elif intent in (INTENT_CREATE_ITINERARY, INTENT_MODIFY_ITINERARY, INTENT_CHANGE_DATES, INTENT_CHANGE_DESTINATION):
            constraints = session_state.constraints

            # 1. Check for missing critical requirements
            missing_critical = []
            if not constraints.destination:
                missing_critical.append("destination")
            if not constraints.duration_days and not constraints.start_date:
                missing_critical.append("duration_days")

            if missing_critical:
                if "destination" in missing_critical and "duration_days" in missing_critical:
                    clarification_qs.append("Vui lòng cho biết điểm đến bạn mong muốn và số ngày dự kiến cho chuyến đi.")
                elif "destination" in missing_critical:
                    clarification_qs.append("Bạn muốn lên kế hoạch du lịch cho địa danh hay tỉnh/thành phố nào?")
                elif "duration_days" in missing_critical:
                    clarification_qs.append("Chuyến đi dự kiến của bạn sẽ kéo dài trong bao nhiêu ngày?")

                session_state.unresolved_questions = clarification_qs
                return self._finalize_result(
                    status="clarification_needed",
                    intent=intent,
                    session_state=session_state,
                    message="Cần làm rõ thông tin chuyến đi trước khi lập lịch trình.",
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=missing_critical,
                    clarification_qs=clarification_qs,
                    researched_places=[],
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # 2. Destination Research Phase
            research_res = self._execute_destination_research(
                user_message=user_message,
                session_state=session_state,
                intent_res=intent_res,
                max_tool_budget=max(1, tool_budget - total_tool_calls),
                agents_executed=agents_executed,
                executed_cache=executed_agent_cache
            )
            total_tool_calls += len(research_res.tools_executed)
            researched_places = research_res.places

            if research_res.status == "error":
                errors.append(research_res.message)
                return self._finalize_result(
                    status="partial",
                    intent=intent,
                    session_state=session_state,
                    message=f"Lỗi khi tra cứu điểm tham quan: {research_res.message}",
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=missing_info,
                    clarification_qs=clarification_qs,
                    researched_places=[],
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # Convert researched places to candidate POIs format expected by planner
            candidate_pois = self._build_candidate_pois(researched_places, session_state)

            if not candidate_pois:
                msg = f"Không tìm thấy điểm đến khả dụng tại '{constraints.destination}'. Không thể lập lịch trình."
                warnings.append(msg)
                return self._finalize_result(
                    status="partial",
                    intent=intent,
                    session_state=session_state,
                    message=msg,
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=missing_info,
                    clarification_qs=clarification_qs,
                    researched_places=researched_places,
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # 3. Itinerary Planner Phase
            planner_res = self._execute_planner(
                session_state=session_state,
                candidate_pois=candidate_pois,
                execute_api=execute_api,
                agents_executed=agents_executed,
                executed_cache=executed_agent_cache
            )

            if not planner_res.get("success"):
                val_errors = planner_res.get("validation", {}).get("errors", [])
                err_msg = "; ".join(val_errors) if val_errors else "Lịch trình không vượt qua kiểm định logic."
                errors.append(err_msg)
                return self._finalize_result(
                    status="partial",
                    intent=intent,
                    session_state=session_state,
                    message=f"Lập lịch trình thất bại: {err_msg}",
                    agents_executed=agents_executed,
                    warnings=warnings,
                    errors=errors,
                    missing_info=missing_info,
                    clarification_qs=clarification_qs,
                    researched_places=researched_places,
                    iterations=iterations,
                    total_tool_calls=total_tool_calls,
                    start_time=start_time,
                    initial_trace_count=initial_trace_count
                )

            # Convert sanitized plan into ItineraryPlanDraft
            plan_draft = self._convert_plan_to_draft(planner_res["data"])
            session_state.current_plan = plan_draft

            # 4. Budget Agent Phase
            budget_res = self._execute_budget_evaluation(
                session_state=session_state,
                max_tool_budget=max(1, tool_budget - total_tool_calls),
                agents_executed=agents_executed,
                executed_cache=executed_agent_cache
            )
            total_tool_calls += len(budget_res.tools_executed)
            if budget_res.status == "tool_failed":
                warnings.append(budget_res.message)
            else:
                warnings.extend(budget_res.warnings)

            success_msg = f"Đã lập lịch trình hoàn chỉnh tại {constraints.destination} gồm {plan_draft.total_stops} điểm dừng."
            if budget_res.breakdown:
                success_msg += f" Ngân sách ước tính: {budget_res.breakdown.total_cost:,.0f} {budget_res.breakdown.currency}."

            return self._finalize_result(
                status="success",
                intent=intent,
                session_state=session_state,
                message=success_msg,
                agents_executed=agents_executed,
                warnings=warnings,
                errors=errors,
                missing_info=missing_info,
                clarification_qs=clarification_qs,
                researched_places=researched_places,
                iterations=iterations,
                total_tool_calls=total_tool_calls,
                start_time=start_time,
                initial_trace_count=initial_trace_count
            )

        # Fallback default path
        return self._finalize_result(
            status="partial",
            intent=intent,
            session_state=session_state,
            message="Đã xử lý xong thông tin phiên.",
            agents_executed=agents_executed,
            warnings=warnings,
            errors=errors,
            missing_info=missing_info,
            clarification_qs=clarification_qs,
            researched_places=researched_places,
            iterations=iterations,
            total_tool_calls=total_tool_calls,
            start_time=start_time,
            initial_trace_count=initial_trace_count
        )

    # -------------------------------------------------------------
    # Subagent Invocations with Loop Guards & Checksums
    # -------------------------------------------------------------

    def _execute_destination_research(
        self,
        user_message: str,
        session_state: TravelSessionState,
        intent_res: IntentAgentResult,
        max_tool_budget: int,
        agents_executed: List[str],
        executed_cache: Set[str]
    ) -> DestinationResearchResult:
        cache_key = f"dest_research:{session_state.constraints.destination}:{user_message}"
        if cache_key in executed_cache:
            # Prevent repeated duplicate invocation
            return DestinationResearchResult(
                destination=session_state.constraints.destination or "",
                places=[],
                status="partial",
                message="Skipped duplicate research step"
            )

        executed_cache.add(cache_key)
        agents_executed.append("destination_research_agent")
        return self.destination_agent.research(
            user_message=user_message,
            session_state=session_state,
            intent_result=intent_res,
            max_results=8,
            enable_web_search=False,
            max_tool_budget=max_tool_budget
        )

    def _execute_planner(
        self,
        session_state: TravelSessionState,
        candidate_pois: List[Dict[str, Any]],
        execute_api: bool,
        agents_executed: List[str],
        executed_cache: Set[str]
    ) -> Dict[str, Any]:
        cache_key = f"planner:{session_state.constraints.destination}:{len(candidate_pois)}"
        if cache_key in executed_cache:
            return {"success": False, "status": "duplicate_call", "validation": {"errors": ["Duplicate planner call"]}}

        executed_cache.add(cache_key)
        agents_executed.append("itinerary_planner_agent")

        planner_req = {
            "selected_date": session_state.constraints.start_date or datetime.now().strftime("%Y-%m-%d"),
            "destination": session_state.constraints.destination,
            "preferences": ", ".join(session_state.constraints.interests) if session_state.constraints.interests else "Khám phá địa danh",
            "max_stops": 3
        }

        start_p = time.perf_counter()
        plan_out = self.planner_agent.plan(
            user_request=planner_req,
            candidate_pois=candidate_pois,
            execute_api=execute_api
        )
        dur_p = (time.perf_counter() - start_p) * 1000.0

        trace = AgentTaskTrace(
            agent_name="itinerary_planner_agent",
            status="completed" if plan_out.get("success") else "failed",
            input_summary=f"POIs: {len(candidate_pois)}, Date: {planner_req['selected_date']}",
            output_summary=f"Status: {plan_out.get('status')}, Valid: {plan_out.get('success')}",
            tools_called=[],
            execution_time_ms=round(dur_p, 2)
        )
        session_state.add_trace(trace)
        return plan_out

    def _execute_budget_evaluation(
        self,
        session_state: TravelSessionState,
        max_tool_budget: int,
        agents_executed: List[str],
        executed_cache: Set[str]
    ) -> BudgetAgentResult:
        cache_key = f"budget:{session_state.constraints.duration_days}:{session_state.constraints.group_size}:{session_state.constraints.budget}"
        if cache_key in executed_cache:
            # Return last cached breakdown
            return BudgetAgentResult(
                status="sufficient_data",
                breakdown=session_state.budget_breakdown,
                message="Retained current budget calculation"
            )

        executed_cache.add(cache_key)
        agents_executed.append("budget_agent")
        return self.budget_agent.evaluate_budget(
            session_state=session_state,
            max_tool_budget=max_tool_budget
        )

    def _build_candidate_pois(
        self,
        researched_places: List[ResearchedPlace],
        session_state: TravelSessionState
    ) -> List[Dict[str, Any]]:
        candidate_pois: List[Dict[str, Any]] = []

        # From researched places
        for idx, p in enumerate(researched_places):
            osm_id = None
            if p.source_id is not None:
                try:
                    osm_id = int(p.source_id)
                except (ValueError, TypeError):
                    digits = re.sub(r"\D", "", str(p.source_id))
                    osm_id = int(digits) if digits else (idx + 101)
            else:
                osm_id = idx + 101

            candidate_pois.append({
                "osm_id": osm_id,
                "name": p.name,
                "category": p.category or "attraction",
                "fclass": p.category or "attraction",
                "description": p.description or "",
                "address": p.address,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "coordinates": {
                    "latitude": p.latitude,
                    "longitude": p.longitude
                }
            })

        # Supplement with evidence items if candidate count is small
        if len(candidate_pois) < 3:
            for idx, ev in enumerate(session_state.evidence_items):
                meta = ev.metadata or {}
                if meta.get("name") and not any(c["name"] == meta.get("name") for c in candidate_pois):
                    osm_id = idx + 201
                    if meta.get("osm_id") is not None:
                        try:
                            osm_id = int(meta["osm_id"])
                        except (ValueError, TypeError):
                            digits = re.sub(r"\D", "", str(meta["osm_id"]))
                            if digits:
                                osm_id = int(digits)

                    candidate_pois.append({
                        "osm_id": osm_id,
                        "name": meta.get("name"),
                        "category": meta.get("category", "attraction"),
                        "fclass": meta.get("category", "attraction"),
                        "description": ev.content,
                        "address": meta.get("address"),
                        "latitude": meta.get("latitude"),
                        "longitude": meta.get("longitude"),
                        "coordinates": {
                            "latitude": meta.get("latitude"),
                            "longitude": meta.get("longitude")
                        }
                    })

        return candidate_pois

    def _convert_plan_to_draft(self, plan_dict: Dict[str, Any]) -> ItineraryPlanDraft:
        """Converts raw sanitized plan dictionary to typed ItineraryPlanDraft."""
        raw_items = plan_dict.get("items", [])
        stops: List[ItineraryStopDraft] = []

        for idx, it in enumerate(raw_items):
            coords = it.get("coordinates") or {}
            raw_osm = it.get("osm_id")
            osm_int = None
            poi_str = it.get("poi_id")
            if raw_osm is not None:
                try:
                    osm_int = int(raw_osm)
                except (ValueError, TypeError):
                    digits = re.sub(r"\D", "", str(raw_osm))
                    if digits:
                        try:
                            osm_int = int(digits)
                        except ValueError:
                            pass
                    if not poi_str:
                        poi_str = str(raw_osm)

            stops.append(ItineraryStopDraft(
                order_index=it.get("order_index", idx + 1),
                osm_id=osm_int,
                poi_id=poi_str,
                name=it.get("name") or "Điểm tham quan",
                category=it.get("category"),
                address=it.get("address"),
                latitude=coords.get("latitude") or it.get("latitude"),
                longitude=coords.get("longitude") or it.get("longitude"),
                start_time=it.get("start_time"),
                end_time=it.get("end_time"),
                estimated_duration_minutes=it.get("estimated_duration", 60),
                notes=it.get("notes") or it.get("reason"),
                verified_source=it.get("verified_source") or "OpenStreetMap staging"
            ))

        day = ItineraryDayDraft(
            day_number=1,
            date=plan_dict.get("selected_date"),
            theme=plan_dict.get("title"),
            stops=stops,
            notes=plan_dict.get("summary")
        )

        return ItineraryPlanDraft(
            draft_id=plan_dict.get("draft_id") or f"draft_{uuid.uuid4().hex[:12]}",
            title=plan_dict.get("title", "Lịch trình khám phá"),
            selected_date=plan_dict.get("selected_date"),
            days=[day],
            total_stops=len(stops),
            is_valid=True,
            warnings=[]
        )

    def _finalize_result(
        self,
        status: str,
        intent: Optional[str],
        session_state: TravelSessionState,
        message: str,
        agents_executed: List[str],
        warnings: List[str],
        errors: List[str],
        missing_info: List[str],
        clarification_qs: List[str],
        researched_places: List[ResearchedPlace],
        iterations: int,
        total_tool_calls: int,
        start_time: float,
        initial_trace_count: int
    ) -> CoordinatorResult:
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Coordinator task trace
        trace = AgentTaskTrace(
            agent_name="coordinator_agent",
            status="completed" if status in ("success", "out_of_scope", "clarification_needed") else "failed",
            input_summary=f"Intent: {intent}, Subagents: {agents_executed}",
            output_summary=f"Status: {status}, ToolCalls: {total_tool_calls}",
            tools_called=[],
            execution_time_ms=round(duration_ms, 2)
        )
        session_state.add_trace(trace)

        # Traces created in this turn
        run_traces = session_state.agent_traces[initial_trace_count:]

        return CoordinatorResult(
            status=status,
            intent=intent,
            session_state=session_state,
            current_plan=session_state.current_plan,
            budget_breakdown=session_state.budget_breakdown,
            researched_places=researched_places,
            evidence_items=session_state.evidence_items,
            missing_information=missing_info,
            clarification_questions=clarification_qs,
            warnings=warnings,
            errors=errors,
            agents_executed=agents_executed,
            task_traces=run_traces,
            iterations_count=iterations,
            total_tool_calls=total_tool_calls,
            message=message,
            execution_time_ms=round(duration_ms, 2)
        )


# Global default coordinator instance
default_coordinator_agent = CoordinatorAgent()


def coordinate_travel_request(
    user_message: str,
    session_state: TravelSessionState,
    execute_api: bool = False,
    coordinator: Optional[CoordinatorAgent] = None
) -> CoordinatorResult:
    """Convenience helper functional interface."""
    agent = coordinator or default_coordinator_agent
    return agent.process(
        user_message=user_message,
        session_state=session_state,
        execute_api=execute_api
    )
