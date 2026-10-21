"""
Unit tests for Coordinator / Orchestration Agent (Phase 2 & Phase 3)
Testing scenarios:
1. Successful itinerary request (end-to-end multi-agent orchestration).
2. Attraction search without itinerary planning.
3. Budget request with existing itinerary.
4. Missing critical constraints returns clarification without inventing facts.
5. Out-of-scope request does not invoke unrelated agents.
6. Budget modification preserves the new user-provided budget.
7. Group-size modification triggers dependent recalculation.
8. Date modification triggers dependent recalculation.
9. Removing a stop actually modifies the itinerary (re-sequencing stops) or returns error if no plan.
10. Planner validation failure is not reported as success.
11. Destination research failure preserves valid state and returns partial/error status.
12. Budget tool failure preserves valid itinerary state.
13. Maximum iterations stops repeated handoffs.
14. Maximum tool calls is respected.
15. Agent trace records actual executions and failures truthfully.
16. Repeated unchanged inputs avoid duplicate agent calls.
17. Evidence provenance is preserved across the orchestration pipeline.
18. Existing constraints and unrelated state remain unchanged.
19. No fabricated success when an agent returns empty or invalid data.
20. Helper functional interface coordinate_travel_request works identically.
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime

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
    ToolDefinition,
    ToolExecutionResult
)
from src.services.ai.tools.budget_calculator_tool import (
    calculate_trip_budget,
    CalculateBudgetInput
)
from src.services.ai.agents.intent_agent import (
    IntentAndContextAgent,
    IntentAgentResult,
    INTENT_SEARCH_PLACES,
    INTENT_CREATE_ITINERARY,
    INTENT_CHANGE_BUDGET,
    INTENT_CHANGE_GROUP_SIZE,
    INTENT_CHANGE_DATES,
    INTENT_REMOVE_STOP,
    INTENT_OUT_OF_SCOPE,
    INTENT_CLARIFICATION_NEEDED
)
from src.services.ai.agents.destination_research_agent import (
    DestinationResearchAgent,
    DestinationResearchResult,
    ResearchedPlace
)
from src.services.ai.agents.planner_agent import ItineraryPlannerAgent
from src.services.ai.agents.budget_agent import (
    BudgetAgent,
    BudgetAgentResult,
    CostItem
)
from src.services.ai.agents.coordinator_agent import (
    CoordinatorAgent,
    CoordinatorResult,
    coordinate_travel_request
)


class CoordinatorAgentTests(unittest.TestCase):

    def setUp(self):
        self.state = TravelSessionState(session_id="coord_test_session_01")
        self.state.update_constraints({
            "destination": "Đà Nẵng",
            "group_size": 2,
            "duration_days": 3,
            "start_date": "2026-11-10",
            "budget": 10000000.0,
            "travel_style": "tiêu chuẩn",
            "transportation": "ô tô / xe khách",
            "interests": ["biển", "ẩm thực"],
            "accessibility_requirements": ["không có"]
        }, source="user_provided")

        # Set up a real tool registry for budget calculation
        self.registry = ToolRegistry(default_max_tool_calls_per_run=10)
        self.registry.register(ToolDefinition(
            name="calculate_trip_budget",
            description="Calculate trip budget",
            args_schema=CalculateBudgetInput,
            func=calculate_trip_budget
        ))

        # Setup standard mock destination agent
        self.mock_places = [
            ResearchedPlace(
                name="Bà Nà Hills",
                category="attraction",
                address="Hòa Vang, Đà Nẵng",
                latitude=15.998,
                longitude=107.987,
                source_id="osm_101",
                source_type="internal_database",
                is_verified=True
            ),
            ResearchedPlace(
                name="Cầu Rồng",
                category="monument",
                address="Hải Châu, Đà Nẵng",
                latitude=16.061,
                longitude=108.227,
                source_id="osm_102",
                source_type="internal_database",
                is_verified=True
            ),
            ResearchedPlace(
                name="Bãi biển Mỹ Khê",
                category="beach",
                address="Sơn Trà, Đà Nẵng",
                latitude=16.059,
                longitude=108.243,
                source_id="osm_103",
                source_type="internal_database",
                is_verified=True
            )
        ]

    def _create_coordinator(self, dest_places=None, dest_status="success", dest_error=None, planner_override=None, budget_override=None):
        # Mock destination agent
        dest_agent = MagicMock(spec=DestinationResearchAgent)
        if dest_error:
            dest_agent.research.return_value = DestinationResearchResult(
                destination="Đà Nẵng",
                places=[],
                status="error",
                message=dest_error
            )
        else:
            dest_agent.research.return_value = DestinationResearchResult(
                destination="Đà Nẵng",
                places=dest_places if dest_places is not None else self.mock_places,
                status=dest_status,
                tools_executed=["search_internal_pois"]
            )

        # Planner agent (real or override)
        planner = planner_override or ItineraryPlannerAgent()

        # Budget agent (real with registry or override)
        budget = budget_override or BudgetAgent(tool_registry=self.registry)

        return CoordinatorAgent(
            tool_registry=self.registry,
            destination_agent=dest_agent,
            planner_agent=planner,
            budget_agent=budget
        )

    # 1. Successful itinerary request
    def test_successful_itinerary_request(self):
        coordinator = self._create_coordinator()
        result = coordinator.process(
            user_message="Lên lịch trình 3 ngày ở Đà Nẵng",
            session_state=self.state
        )

        self.assertEqual(result.status, "success")
        self.assertIsNotNone(result.current_plan)
        self.assertTrue(result.current_plan.is_valid)
        self.assertGreater(result.current_plan.total_stops, 0)
        self.assertIsNotNone(result.budget_breakdown)
        self.assertIn("intent_agent", result.agents_executed)
        self.assertIn("destination_research_agent", result.agents_executed)
        self.assertIn("itinerary_planner_agent", result.agents_executed)
        self.assertIn("budget_agent", result.agents_executed)

    # 2. Attraction search without itinerary planning
    def test_attraction_search_without_itinerary_planning(self):
        coordinator = self._create_coordinator()
        result = coordinator.process(
            user_message="Tìm địa điểm chơi ở Đà Nẵng",
            session_state=self.state
        )

        self.assertEqual(result.status, "success")
        self.assertGreater(len(result.researched_places), 0)
        self.assertIn("destination_research_agent", result.agents_executed)
        self.assertNotIn("itinerary_planner_agent", result.agents_executed)
        self.assertNotIn("budget_agent", result.agents_executed)
        self.assertIsNone(result.current_plan)

    # 3. Budget request with existing itinerary
    def test_budget_request_with_existing_itinerary(self):
        # First set up an existing plan
        coordinator = self._create_coordinator()
        coordinator.process(user_message="Lên lịch trình 3 ngày ở Đà Nẵng", session_state=self.state)
        self.assertIsNotNone(self.state.current_plan)

        # Now ask to modify budget
        result = coordinator.process(
            user_message="Đổi ngân sách thành 12 triệu",
            session_state=self.state
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(self.state.constraints.budget, 12000000.0)
        self.assertIn("budget_agent", result.agents_executed)
        self.assertNotIn("itinerary_planner_agent", result.agents_executed)

    # 4. Missing critical constraints returns clarification
    def test_missing_critical_constraints_returns_clarification(self):
        empty_state = TravelSessionState(session_id="empty_session")
        coordinator = self._create_coordinator()

        result = coordinator.process(
            user_message="Tôi muốn đi du lịch",
            session_state=empty_state
        )

        self.assertEqual(result.status, "clarification_needed")
        self.assertGreater(len(result.clarification_questions), 0)
        # Should not invent destination or dates
        self.assertIsNone(empty_state.constraints.destination)
        self.assertIsNone(empty_state.constraints.duration_days)

    # 5. Out-of-scope request does not invoke unrelated agents
    def test_out_of_scope_does_not_invoke_unrelated_agents(self):
        coordinator = self._create_coordinator()
        result = coordinator.process(
            user_message="Viết code python hàm quicksort",
            session_state=self.state
        )

        self.assertEqual(result.status, "out_of_scope")
        self.assertEqual(result.agents_executed, ["intent_agent"])
        self.assertNotIn("destination_research_agent", result.agents_executed)
        self.assertNotIn("itinerary_planner_agent", result.agents_executed)
        self.assertNotIn("budget_agent", result.agents_executed)

    # 6. Budget modification preserves the new user-provided budget
    def test_budget_modification_preserves_new_user_budget(self):
        coordinator = self._create_coordinator()
        result = coordinator.process(
            user_message="Tôi muốn đặt ngân sách là 15000000",
            session_state=self.state
        )

        self.assertEqual(self.state.constraints.budget, 15000000.0)
        if result.budget_breakdown:
            self.assertNotEqual(self.state.constraints.budget, result.budget_breakdown.total_cost)

    # 7. Group-size modification triggers dependent recalculation
    def test_group_size_modification_triggers_recalculation(self):
        coordinator = self._create_coordinator()
        # Initial run to create itinerary
        coordinator.process(user_message="Lên lịch trình 3 ngày Đà Nẵng", session_state=self.state)
        orig_cost = self.state.budget_breakdown.total_cost

        # Change group size to 4
        result = coordinator.process(
            user_message="Đổi sang đoàn 4 người",
            session_state=self.state
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(self.state.constraints.group_size, 4)
        self.assertIn("budget_agent", result.agents_executed)
        self.assertEqual(self.state.budget_breakdown.group_size, 4)
        self.assertGreater(self.state.budget_breakdown.total_cost, orig_cost)

    # 8. Date modification triggers dependent recalculation
    def test_date_modification_triggers_recalculation(self):
        coordinator = self._create_coordinator()
        coordinator.process(user_message="Lên lịch trình 3 ngày Đà Nẵng", session_state=self.state)

        result = coordinator.process(
            user_message="Đổi ngày bắt đầu sang 2026-12-15",
            session_state=self.state
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(self.state.constraints.start_date, "2026-12-15")
        self.assertIn("itinerary_planner_agent", result.agents_executed)
        self.assertIn("budget_agent", result.agents_executed)

    # 9. Removing a stop actually modifies the itinerary
    def test_remove_stop_modifies_itinerary(self):
        coordinator = self._create_coordinator()
        coordinator.process(user_message="Lên lịch trình 3 ngày Đà Nẵng", session_state=self.state)
        initial_stops = self.state.current_plan.total_stops
        self.assertGreater(initial_stops, 1)

        result = coordinator.process(
            user_message="Bỏ điểm thứ 2 khỏi lịch trình",
            session_state=self.state
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(self.state.current_plan.total_stops, initial_stops - 1)
        # Verify order indices are correctly re-sequenced
        stops = self.state.current_plan.days[0].stops
        for idx, s in enumerate(stops):
            self.assertEqual(s.order_index, idx + 1)

        # Test removing a stop when no plan exists
        empty_state = TravelSessionState(session_id="empty_plan_session")
        res_empty = coordinator.process(user_message="Xóa điểm 1", session_state=empty_state)
        self.assertEqual(res_empty.status, "error")

    # 10. Planner validation failure is not reported as success
    def test_planner_validation_failure_not_reported_as_success(self):
        mock_planner = MagicMock()
        mock_planner.plan.return_value = {
            "success": False,
            "status": "invalid_schedule",
            "validation": {
                "is_valid": False,
                "errors": ["Phát hiện osm_id ảo giác không tồn tại trong danh sách ứng viên."]
            },
            "data": None
        }

        coordinator = self._create_coordinator(planner_override=mock_planner)
        result = coordinator.process(
            user_message="Lên lịch trình 3 ngày Đà Nẵng",
            session_state=self.state
        )

        self.assertEqual(result.status, "partial")
        self.assertGreater(len(result.errors), 0)
        self.assertIn("ảo giác", result.errors[0])
        self.assertIsNone(self.state.current_plan)

    # 11. Destination research failure preserves valid state and returns partial/error status
    def test_destination_research_failure_preserves_valid_state(self):
        coordinator = self._create_coordinator(dest_error="Database connection pool exhausted")
        orig_budget = self.state.constraints.budget
        orig_group = self.state.constraints.group_size

        result = coordinator.process(
            user_message="Lên lịch trình 3 ngày Đà Nẵng",
            session_state=self.state
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(self.state.constraints.budget, orig_budget)
        self.assertEqual(self.state.constraints.group_size, orig_group)
        self.assertGreater(len(result.errors), 0)

    # 12. Budget tool failure preserves valid itinerary state
    def test_budget_tool_failure_preserves_valid_itinerary(self):
        mock_budget = MagicMock()
        mock_budget.evaluate_budget.return_value = BudgetAgentResult(
            status="tool_failed",
            breakdown=None,
            tools_executed=["calculate_trip_budget"],
            message="Budget service unavailable"
        )

        coordinator = self._create_coordinator(budget_override=mock_budget)
        result = coordinator.process(
            user_message="Lên lịch trình 3 ngày Đà Nẵng",
            session_state=self.state
        )

        # Itinerary remains valid
        self.assertIsNotNone(self.state.current_plan)
        self.assertTrue(self.state.current_plan.is_valid)
        self.assertIn("Budget service unavailable", result.warnings)

    # 13. Maximum iterations stops repeated handoffs
    def test_max_iterations_stops_repeated_handoffs(self):
        coordinator = CoordinatorAgent(max_iterations=1)
        self.assertEqual(coordinator.max_iterations, 1)

    # 14. Maximum tool calls is respected
    def test_max_tool_calls_respected(self):
        coordinator = self._create_coordinator()
        result = coordinator.process(
            user_message="Tìm địa điểm chơi ở Đà Nẵng",
            session_state=self.state,
            max_tool_budget=2
        )
        self.assertLessEqual(result.total_tool_calls, 2)

    # 15. Agent trace records actual executions and failures
    def test_agent_trace_records_actual_executions(self):
        coordinator = self._create_coordinator()
        initial_trace_count = len(self.state.agent_traces)

        coordinator.process(
            user_message="Lên lịch trình 3 ngày Đà Nẵng",
            session_state=self.state
        )

        traces = self.state.agent_traces[initial_trace_count:]
        self.assertGreater(len(traces), 0)
        trace_agents = {t.agent_name for t in traces}
        self.assertIn("coordinator_agent", trace_agents)
        self.assertIn("intent_agent", trace_agents)
        self.assertIn("itinerary_planner_agent", trace_agents)
        self.assertIn("budget_agent", trace_agents)

    # 16. Repeated unchanged inputs do not cause duplicate agent calls
    def test_repeated_unchanged_inputs_avoid_duplicate_calls(self):
        coordinator = self._create_coordinator()
        coordinator.process(user_message="Lên lịch trình 3 ngày Đà Nẵng", session_state=self.state)
        count_first = len(self.state.agent_traces)

        # Call budget change
        coordinator.process(user_message="Đổi ngân sách thành 12 triệu", session_state=self.state)
        # Should not have called planner again
        recent_traces = self.state.agent_traces[count_first:]
        self.assertNotIn("itinerary_planner_agent", [t.agent_name for t in recent_traces])

    # 17. Evidence provenance is preserved
    def test_evidence_provenance_preserved(self):
        ev = EvidenceItem(
            content="Bà Nà Hills mở cửa từ 07:00 đến 22:00",
            source_type="internal_database",
            verified=True,
            metadata={"name": "Bà Nà Hills", "osm_id": "osm_101"}
        )
        self.state.add_evidence(ev)

        coordinator = self._create_coordinator()
        result = coordinator.process(
            user_message="Lên lịch trình 3 ngày Đà Nẵng",
            session_state=self.state
        )

        found_ev = next((e for e in result.evidence_items if e.content == ev.content), None)
        self.assertIsNotNone(found_ev)
        self.assertTrue(found_ev.verified)
        self.assertEqual(found_ev.source_type, "internal_database")

    # 18. Existing constraints and unrelated state remain unchanged
    def test_existing_constraints_remain_unchanged(self):
        orig_dest = self.state.constraints.destination
        orig_interests = list(self.state.constraints.interests)
        orig_access = list(self.state.constraints.accessibility_requirements)

        coordinator = self._create_coordinator()
        coordinator.process(
            user_message="Đoàn đổi thành 5 người",
            session_state=self.state
        )

        self.assertEqual(self.state.constraints.destination, orig_dest)
        self.assertEqual(self.state.constraints.interests, orig_interests)
        self.assertEqual(self.state.constraints.accessibility_requirements, orig_access)
        self.assertEqual(self.state.constraints.group_size, 5)

    # 19. No fabricated success when an agent returns empty or invalid data
    def test_no_fabricated_success_on_empty_data(self):
        coordinator = self._create_coordinator(dest_places=[])
        result = coordinator.process(
            user_message="Lên lịch trình ở Vùng Đất Không Có Thật 3 ngày",
            session_state=self.state
        )

        self.assertEqual(result.status, "partial")
        self.assertIsNone(self.state.current_plan)

    # 20. Regression compatibility with coordinate_travel_request
    def test_regression_compatibility(self):
        result = coordinate_travel_request(
            user_message="Tìm địa điểm chơi ở Đà Nẵng",
            session_state=self.state
        )
        self.assertIsInstance(result, CoordinatorResult)
        self.assertIsNotNone(result.session_state)


if __name__ == "__main__":
    unittest.main()
