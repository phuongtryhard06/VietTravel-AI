"""
Unit tests for Budget Agent (Phase 2 & Phase 3)
Testing scenarios:
1. Successful budget calculation with valid constraints.
2. Distinct calculations for group total vs. per-person cost.
3. Budget limit equal to zero or unspecified (None).
4. Missing trip duration (both duration_days and start/end dates absent).
5. Missing prices on some items marked as partial estimate.
6. Distinction between verified source prices and model estimates.
7. Warning and flag when total estimated cost exceeds budget limit.
8. Actionable savings suggestions generated without mutating original budget constraint.
9. Tool registry error or timeout handled gracefully with status='tool_failed'.
10. Arithmetic integrity ensuring no double-counting between items and contingency.
11. Preservation of all unrelated constraints in session state.
12. Execution trace accurately records tools called and performance.
"""

import unittest
from unittest.mock import MagicMock
from datetime import datetime

from src.services.ai.state import (
    TravelSessionState,
    TripConstraints,
    BudgetBreakdown,
    AgentTaskTrace
)
from src.services.ai.agents.budget_agent import (
    BudgetAgent,
    evaluate_trip_budget,
    BudgetAgentResult,
    CostItem
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


class BudgetAgentTests(unittest.TestCase):

    def setUp(self):
        self.state = TravelSessionState(session_id="budget_agent_test_session")
        self.state.update_constraints({
            "destination": "Đà Nẵng",
            "group_size": 2,
            "duration_days": 3,
            "budget": 10000000.0,
            "currency": "VND",
            "travel_style": "tiêu chuẩn",
            "transportation": "ô tô / xe khách",
            "interests": ["biển", "ẩm thực"],
            "accessibility_requirements": ["không có"]
        }, source="user_provided")

        # Set up a real tool registry with calculate_trip_budget registered
        self.registry = ToolRegistry(default_max_tool_calls_per_run=5)
        self.registry.register(ToolDefinition(
            name="calculate_trip_budget",
            description="Calculate trip budget",
            args_schema=CalculateBudgetInput,
            func=calculate_trip_budget
        ))

    # 1. Tính ngân sách thành công
    def test_successful_budget_calculation(self):
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertIn(result.status, ("partial_estimate", "sufficient_data"))
        self.assertIsNotNone(result.breakdown)
        self.assertGreater(result.breakdown.total_cost, 0)
        self.assertEqual(result.breakdown.group_size, 2)
        self.assertIn("calculate_trip_budget", result.tools_executed)
        # Check that session_state.budget_breakdown is updated
        self.assertIsNotNone(self.state.budget_breakdown)
        self.assertEqual(self.state.budget_breakdown.total_cost, result.breakdown.total_cost)

    # 2. Phân biệt tổng đoàn và từng người
    def test_distinguishes_group_total_and_per_person(self):
        self.state.constraints.group_size = 4
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        breakdown = result.breakdown
        self.assertIsNotNone(breakdown)
        self.assertGreater(breakdown.total_cost, breakdown.cost_per_person)
        # Ratio total / cost_per_person should approximate 4 (accounting for rounding to thousands)
        ratio = breakdown.total_cost / breakdown.cost_per_person
        self.assertAlmostEqual(ratio, 4.0, delta=0.1)

    # 3. Ngân sách bằng 0 hoặc không được cung cấp
    def test_zero_or_unspecified_budget(self):
        self.state.constraints.budget = None
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertIsNotNone(result.breakdown)
        self.assertFalse(result.breakdown.exceeds_budget)
        self.assertEqual(len(result.warnings), 0)
        self.assertEqual(len(result.savings_suggestions), 0)

        # Also test with budget = 0.0
        self.state.constraints.budget = 0.0
        result_zero = agent.evaluate_budget(session_state=self.state)
        self.assertIsNotNone(result_zero.breakdown)
        self.assertFalse(result_zero.breakdown.exceeds_budget)

    # 4. Thiếu ngày đi/về hoặc thời lượng
    def test_missing_duration_and_dates(self):
        self.state.constraints.duration_days = None
        self.state.constraints.start_date = None
        self.state.constraints.end_date = None

        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertEqual(result.status, "missing_data")
        self.assertIsNone(result.breakdown)
        self.assertGreater(len(result.warnings), 0)
        self.assertIn("số ngày", result.warnings[0].lower())
        # Audit trace should reflect the failure
        last_trace = self.state.agent_traces[-1]
        self.assertEqual(last_trace.status, "failed")
        self.assertEqual(last_trace.agent_name, "budget_agent")

    # 5. Thiếu giá một số hạng mục
    def test_missing_prices_some_items(self):
        custom_items = [
            CostItem(
                category="activities",
                description="Vé trải nghiệm đặc biệt (chưa có giá)",
                amount=0.0,
                is_verified=False,
                source_type="unknown",
                source_attribution=None
            )
        ]
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(
            session_state=self.state,
            custom_items=custom_items
        )

        self.assertEqual(result.status, "partial_estimate")
        self.assertGreater(len(result.warnings), 0)
        self.assertTrue(any("chưa có thông tin giá" in w for w in result.warnings))

    # 6. Phân biệt giá có nguồn với giá ước tính
    def test_distinguishes_verified_vs_estimated_sources(self):
        custom_items = [
            CostItem(
                category="accommodation",
                description="Khách sạn Novotel Đà Nẵng",
                amount=3500000.0,
                is_verified=True,
                source_type="verified_source",
                source_attribution="Agoda confirmed rate"
            ),
            CostItem(
                category="activities",
                description="Vé cáp treo Bà Nà Hills",
                amount=1800000.0,
                is_verified=True,
                source_type="verified_source",
                source_attribution="Sun World official site"
            )
        ]
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(
            session_state=self.state,
            custom_items=custom_items
        )

        # Accommodation and activities should be verified
        verified_items = [it for it in result.itemized_costs if it.is_verified]
        unverified_items = [it for it in result.itemized_costs if not it.is_verified]

        self.assertEqual(len(verified_items), 2)
        self.assertGreater(len(unverified_items), 0)  # food, trans, contingency are estimates
        self.assertTrue(result.breakdown.is_estimated)  # Not all items are verified

    # 7. Cảnh báo khi vượt ngân sách
    def test_warning_when_exceeding_budget(self):
        # Set an unrealistically low budget ceiling
        self.state.constraints.budget = 1000000.0  # 1M VND for 3 days 2 people
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertTrue(result.breakdown.exceeds_budget)
        self.assertGreater(len(result.warnings), 0)
        self.assertTrue(any("vượt ngân sách" in w.lower() for w in result.warnings))

    # 8. Đề xuất tiết kiệm nhưng không sửa ngân sách gốc
    def test_savings_suggestions_preserves_original_budget(self):
        original_budget = 1500000.0
        self.state.constraints.budget = original_budget

        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertGreater(len(result.savings_suggestions), 0)
        # Original user budget constraint MUST remain untouched
        self.assertEqual(self.state.constraints.budget, original_budget)
        self.assertNotEqual(self.state.constraints.budget, result.breakdown.total_cost)

    # 9. Lỗi hoặc timeout của công cụ
    def test_tool_error_or_timeout_handling(self):
        failing_registry = ToolRegistry(default_max_tool_calls_per_run=5)

        def mock_failing_tool(**kwargs):
            raise TimeoutError("Budget calculator service timed out after 5000ms")

        failing_registry.register(ToolDefinition(
            name="calculate_trip_budget",
            description="Mock failing tool",
            args_schema=CalculateBudgetInput,
            func=mock_failing_tool
        ))

        agent = BudgetAgent(tool_registry=failing_registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertEqual(result.status, "tool_failed")
        self.assertIsNone(result.breakdown)
        self.assertIn("calculate_trip_budget", result.tools_executed)
        last_trace = self.state.agent_traces[-1]
        self.assertEqual(last_trace.status, "failed")
        self.assertIn("timed out", last_trace.error)

    # 10. Không tính trùng chi phí
    def test_no_double_counting(self):
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        b = result.breakdown
        subtotal = b.accommodation_cost + b.food_cost + b.transportation_cost + b.activities_cost
        expected_contingency = round(subtotal * 0.10, -3)
        expected_total = round(subtotal + expected_contingency, -3)

        self.assertEqual(b.contingency_cost, expected_contingency)
        self.assertEqual(b.total_cost, expected_total)

    # 11. Bảo toàn các ràng buộc không liên quan
    def test_preserves_unrelated_constraints(self):
        orig_dest = self.state.constraints.destination
        orig_group = self.state.constraints.group_size
        orig_duration = self.state.constraints.duration_days
        orig_style = self.state.constraints.travel_style
        orig_trans = self.state.constraints.transportation
        orig_interests = list(self.state.constraints.interests)
        orig_access = list(self.state.constraints.accessibility_requirements)

        agent = BudgetAgent(tool_registry=self.registry)
        agent.evaluate_budget(session_state=self.state)

        self.assertEqual(self.state.constraints.destination, orig_dest)
        self.assertEqual(self.state.constraints.group_size, orig_group)
        self.assertEqual(self.state.constraints.duration_days, orig_duration)
        self.assertEqual(self.state.constraints.travel_style, orig_style)
        self.assertEqual(self.state.constraints.transportation, orig_trans)
        self.assertEqual(self.state.constraints.interests, orig_interests)
        self.assertEqual(self.state.constraints.accessibility_requirements, orig_access)

    # 12. Trace và nguồn dữ liệu chính xác
    def test_trace_and_provenance_logged_accurately(self):
        initial_traces = len(self.state.agent_traces)
        agent = BudgetAgent(tool_registry=self.registry)
        result = agent.evaluate_budget(session_state=self.state)

        self.assertEqual(len(self.state.agent_traces), initial_traces + 1)
        last_trace = self.state.agent_traces[-1]
        self.assertEqual(last_trace.agent_name, "budget_agent")
        self.assertEqual(last_trace.status, "completed")
        self.assertIn("calculate_trip_budget", last_trace.tools_called)
        self.assertGreater(last_trace.execution_time_ms, 0.0)
        self.assertIn("Total:", last_trace.output_summary)

        # Check provenance in itemized_costs
        categories = {item.category for item in result.itemized_costs}
        self.assertTrue({"accommodation", "food", "transportation", "activities", "contingency"}.issubset(categories))


if __name__ == "__main__":
    unittest.main()
