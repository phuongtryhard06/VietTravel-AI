"""
Unit tests for AI Core State Model and Tool Registry (Phase 2 & Phase 3)
Tests:
- TripConstraints validation, dates, group sizes, and budgets.
- Partial constraint updates preserving untouched attributes.
- Provenance tracking (user-provided vs model-inferred).
- Trace credential sanitization.
- ToolRegistry registration, schema validation, timeouts, budgets, and retry classification.
- Deterministic budget calculator logic.
- Spatial distance tool.
"""

import time
import unittest
from pydantic import ValidationError

from src.services.ai.state import (
    TripConstraints,
    EvidenceItem,
    AgentTaskTrace,
    TravelSessionState,
    ItineraryPlanDraft,
    ItineraryDayDraft,
    ItineraryStopDraft,
    BudgetBreakdown
)
from src.services.ai.tools.registry import (
    ToolRegistry,
    ToolDefinition,
    ToolExecutionResult,
    global_tool_registry
)
from src.services.ai.tools.budget_calculator_tool import calculate_trip_budget, CalculateBudgetInput
from src.services.ai.tools.definitions import (
    CalculateDistanceInput,
    _execute_calculate_distance
)
from pydantic import BaseModel, Field


class DummyToolInput(BaseModel):
    message: str = Field(..., min_length=2)
    count: int = Field(default=1, ge=1, le=10)


def dummy_success_func(message: str, count: int = 1) -> str:
    return message * count


def dummy_slow_func(message: str, count: int = 1) -> str:
    time.sleep(1.0)
    return message


class AIStateAndToolRegistryTests(unittest.TestCase):

    def test_trip_constraints_initialization_defaults(self):
        constraints = TripConstraints()
        self.assertEqual(constraints.group_size, 1)
        self.assertEqual(constraints.currency, "VND")
        self.assertIsNone(constraints.destination)
        self.assertIsNone(constraints.budget)
        self.assertEqual(constraints.field_provenance, {})

    def test_trip_constraints_validates_dates(self):
        # Valid date
        c = TripConstraints(start_date="2026-11-15", end_date="2026-11-20")
        self.assertEqual(c.start_date, "2026-11-15")
        self.assertEqual(c.end_date, "2026-11-20")

        # Invalid date format
        with self.assertRaises(ValidationError):
            TripConstraints(start_date="invalid-date-string")

        # Non-existent calendar date
        with self.assertRaises(ValidationError):
            TripConstraints(start_date="2026-02-31")

    def test_trip_constraints_rejects_negative_budget_or_invalid_group(self):
        with self.assertRaises(ValidationError):
            TripConstraints(budget=-1000.0)

        with self.assertRaises(ValidationError):
            TripConstraints(group_size=0)

        with self.assertRaises(ValidationError):
            TripConstraints(group_size=100)

    def test_session_state_partial_updates_preserves_untouched_constraints(self):
        state = TravelSessionState(session_id="session_test_001")
        initial_updates = {
            "origin": "Hà Nội",
            "destination": "Đà Nẵng",
            "group_size": 4,
            "budget": 15000000.0,
            "start_date": "2026-11-01",
            "travel_style": "nghỉ dưỡng"
        }
        modified_1 = state.update_constraints(initial_updates, source="user_provided")
        self.assertEqual(len(modified_1), 6)
        self.assertEqual(state.constraints.destination, "Đà Nẵng")
        self.assertEqual(state.constraints.group_size, 4)

        # Now simulate user saying: "Đổi điểm đến sang Cát Bà"
        modified_2 = state.update_constraints({"destination": "Cát Bà"}, source="user_provided")
        self.assertEqual(modified_2, {"destination"})

        # Verify untouched constraints are 100% preserved
        self.assertEqual(state.constraints.destination, "Cát Bà")
        self.assertEqual(state.constraints.origin, "Hà Nội")
        self.assertEqual(state.constraints.group_size, 4)
        self.assertEqual(state.constraints.budget, 15000000.0)
        self.assertEqual(state.constraints.start_date, "2026-11-01")
        self.assertEqual(state.constraints.travel_style, "nghỉ dưỡng")

        # Check provenance
        self.assertEqual(state.constraints.field_provenance["destination"], "user_provided")
        self.assertEqual(state.constraints.field_provenance["origin"], "user_provided")

    def test_session_state_provenance_tracking_inferred(self):
        state = TravelSessionState(session_id="session_test_002")
        state.update_constraints({"interests": ["ẩm thực", "cà phê"]}, source="model_inferred")

        self.assertEqual(state.constraints.interests, ["ẩm thực", "cà phê"])
        self.assertEqual(state.constraints.field_provenance["interests"], "model_inferred")

    def test_session_state_trace_credential_sanitization(self):
        state = TravelSessionState(session_id="session_test_003")
        dirty_trace = AgentTaskTrace(
            agent_name="test_agent",
            status="completed",
            input_summary="User passed Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz and sk-12345678901234567890123456789012",
            output_summary='{"password": "secret_password_123"}'
        )
        state.add_trace(dirty_trace)

        saved = state.agent_traces[0]
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz", saved.input_summary)
        self.assertIn("Bearer [REDACTED]", saved.input_summary)
        self.assertNotIn("sk-12345678901234567890123456789012", saved.input_summary)
        self.assertIn("[REDACTED_API_KEY]", saved.input_summary)
        self.assertNotIn("secret_password_123", saved.output_summary)
        self.assertIn('"password": "[REDACTED]"', saved.output_summary)

    def test_session_state_add_evidence(self):
        state = TravelSessionState(session_id="session_test_004")
        evidence = EvidenceItem(
            content="Bảo tàng Chứng tích Chiến tranh mở cửa 07:30 - 17:30",
            source_type="internal_database",
            source_title="osm_attractions_staging",
            verified=True
        )
        state.add_evidence(evidence)
        self.assertEqual(len(state.evidence_items), 1)
        self.assertTrue(state.evidence_items[0].verified)
        self.assertEqual(state.evidence_items[0].source_type, "internal_database")

    def test_tool_registry_registration_and_execution_success(self):
        registry = ToolRegistry(default_max_tool_calls_per_run=5)
        registry.register(ToolDefinition(
            name="dummy_tool",
            description="Repeats a message",
            args_schema=DummyToolInput,
            func=dummy_success_func,
            timeout_seconds=2.0
        ))

        self.assertIsNotNone(registry.get_tool("dummy_tool"))
        result = registry.execute("dummy_tool", {"message": "VietTravel", "count": 2})

        self.assertTrue(result.success)
        self.assertEqual(result.data, "VietTravelVietTravel")
        self.assertFalse(result.is_retryable)
        self.assertGreaterEqual(result.execution_time_ms, 0.0)

    def test_tool_registry_execution_invalid_arguments_non_retryable(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="dummy_tool",
            description="Repeats a message",
            args_schema=DummyToolInput,
            func=dummy_success_func
        ))

        # count must be between 1 and 10; here 999 violates schema
        result = registry.execute("dummy_tool", {"message": "A", "count": 999})
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "invalid_arguments")
        self.assertFalse(result.is_retryable)

    def test_tool_registry_execution_timeout_is_retryable(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="slow_tool",
            description="Simulates slow network or hangs",
            args_schema=DummyToolInput,
            func=dummy_slow_func,
            timeout_seconds=0.3,
            max_retries=0
        ))

        result = registry.execute("slow_tool", {"message": "Hello"})
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "timeout")
        self.assertTrue(result.is_retryable)

    def test_tool_registry_budget_exhaustion(self):
        registry = ToolRegistry(default_max_tool_calls_per_run=2)
        registry.register(ToolDefinition(
            name="dummy_tool",
            description="Test tool",
            args_schema=DummyToolInput,
            func=dummy_success_func
        ))

        tracker = {"total_calls": 0}
        # Call 1: ok
        r1 = registry.execute("dummy_tool", {"message": "Test"}, call_count_tracker=tracker, max_budget=2)
        self.assertTrue(r1.success)
        # Call 2: ok
        r2 = registry.execute("dummy_tool", {"message": "Test"}, call_count_tracker=tracker, max_budget=2)
        self.assertTrue(r2.success)
        # Call 3: exceeds budget of 2
        r3 = registry.execute("dummy_tool", {"message": "Test"}, call_count_tracker=tracker, max_budget=2)
        self.assertFalse(r3.success)
        self.assertEqual(r3.error_code, "tool_budget_exhausted")
        self.assertFalse(r3.is_retryable)

    def test_budget_calculator_deterministic_arithmetic(self):
        # 3 days, 2 people, tier "tiết kiệm", no intercity
        budget = calculate_trip_budget(
            duration_days=3,
            group_size=2,
            travel_style="tiết kiệm",
            transportation_mode="xe máy",
            total_budget_limit=4000000.0
        )

        self.assertEqual(budget["travel_style"], "tiết kiệm")
        self.assertEqual(budget["group_size"], 2)
        self.assertEqual(budget["duration_days"], 3)

        # 3 days = 2 nights. 2 people = 1 room. 2 nights * 1 room * 300,000 = 600,000
        self.assertEqual(budget["accommodation_cost"], 600000.0)

        # 3 days * 2 people * 160,000 = 960,000
        self.assertEqual(budget["food_cost"], 960000.0)

        # Total is deterministic
        self.assertGreater(budget["total_cost"], 0.0)
        self.assertEqual(budget["cost_per_person"], round(budget["total_cost"] / 2.0, -3))

        # Check budget limit comparison
        self.assertFalse(budget["exceeds_budget"])

        # Test exceeding budget
        budget_exceeded = calculate_trip_budget(
            duration_days=5,
            group_size=4,
            travel_style="cao cấp",
            transportation_mode="máy bay",
            total_budget_limit=10000000.0  # 10M is way below 4 people luxury flight trip
        )
        self.assertTrue(budget_exceeded["exceeds_budget"])

    def test_distance_calculation_haversine(self):
        # Hanoi (21.0285, 105.8542) to Da Nang (16.0544, 108.2022)
        res = _execute_calculate_distance(21.0285, 105.8542, 16.0544, 108.2022)
        self.assertIn("straight_line_km", res)
        self.assertIn("estimated_road_km", res)
        # Haversine distance between Hanoi and Da Nang is ~620-640km
        self.assertGreater(res["straight_line_km"], 600.0)
        self.assertLess(res["straight_line_km"], 660.0)
        self.assertGreater(res["estimated_road_km"], res["straight_line_km"])

    def test_global_tool_registry_has_expected_tools(self):
        registered_names = [t.name for t in global_tool_registry.list_tools()]
        expected = ["search_internal_pois", "calculate_distance", "get_route", "calculate_trip_budget", "search_web"]
        for exp in expected:
            self.assertIn(exp, registered_names, f"Expected '{exp}' to be in global_tool_registry.")


if __name__ == "__main__":
    unittest.main()
