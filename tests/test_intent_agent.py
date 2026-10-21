"""
Unit tests for Intent & Context Agent (Phase 2 & Phase 3)
Testing scenarios:
1. New trip request (intent recognition, entity extraction).
2. Change destination while preserving budget and other constraints.
3. Change group size while preserving dates and duration.
4. Remove stop request (identifies target stop to delete).
5. Ambiguous request triggers clarification questions without guessing.
6. Constraint validation and handling of invalid inputs (invalid date, negative budget).
7. LLM malformed schema output gracefully falls back to deterministic parsing.
8. LLM network/API error gracefully falls back to deterministic parsing.
9. Out of scope request detection (programming, math, sports news).
"""

import unittest
from unittest.mock import patch

from src.services.ai.state import TravelSessionState, TripConstraints
from src.services.ai.agents.intent_agent import (
    IntentAndContextAgent,
    process_user_intent,
    IntentAgentResult,
    INTENT_CREATE_ITINERARY,
    INTENT_CHANGE_DESTINATION,
    INTENT_CHANGE_GROUP_SIZE,
    INTENT_CHANGE_BUDGET,
    INTENT_REMOVE_STOP,
    INTENT_CLARIFICATION_NEEDED,
    INTENT_OUT_OF_SCOPE,
    INTENT_SEARCH_PLACES
)


class IntentAndContextAgentTests(unittest.TestCase):

    def setUp(self):
        self.agent = IntentAndContextAgent()
        self.state = TravelSessionState(session_id="session_intent_test_01")

    # 1. New Trip Request
    def test_new_trip_request_extraction(self):
        query = "Lên lịch trình 3 ngày đi Đà Lạt cho 2 người vào ngày 2026-11-20"
        result = self.agent.process(query, self.state, execute_llm=False)

        self.assertEqual(result.intent, INTENT_CREATE_ITINERARY)
        self.assertEqual(result.status, "success")
        self.assertEqual(self.state.constraints.destination, "Đà Lạt")
        self.assertEqual(self.state.constraints.duration_days, 3)
        self.assertEqual(self.state.constraints.group_size, 2)
        self.assertEqual(self.state.constraints.start_date, "2026-11-20")
        self.assertEqual(self.state.constraints.field_provenance["destination"], "user_provided")

    # 2. Change Destination while Preserving Budget & Untouched Constraints
    def test_change_destination_preserves_budget_and_other_constraints(self):
        # Prepopulate session state with existing planned trip
        self.state.update_constraints({
            "origin": "Hà Nội",
            "destination": "Đà Nẵng",
            "group_size": 4,
            "budget": 20000000.0,
            "duration_days": 4,
            "start_date": "2026-12-01",
            "travel_style": "nghỉ dưỡng"
        }, source="user_provided")

        # User says: "Đổi điểm đến sang Cát Bà nhé"
        query = "Đổi điểm đến sang Cát Bà nhé"
        result = self.agent.process(query, self.state, execute_llm=False)

        self.assertEqual(result.intent, INTENT_CHANGE_DESTINATION)
        self.assertTrue(result.is_modification)
        self.assertIn("destination", result.modified_constraint_fields)

        # Destination changed
        self.assertEqual(self.state.constraints.destination, "Cát Bà")
        # All other constraints preserved exactly
        self.assertEqual(self.state.constraints.origin, "Hà Nội")
        self.assertEqual(self.state.constraints.group_size, 4)
        self.assertEqual(self.state.constraints.budget, 20000000.0)
        self.assertEqual(self.state.constraints.duration_days, 4)
        self.assertEqual(self.state.constraints.start_date, "2026-12-01")
        self.assertEqual(self.state.constraints.travel_style, "nghỉ dưỡng")

    # 3. Change Group Size while Preserving Dates
    def test_change_group_size_preserves_dates_and_destination(self):
        self.state.update_constraints({
            "destination": "Phú Quốc",
            "start_date": "2026-11-10",
            "duration_days": 3,
            "group_size": 2,
            "budget": 12000000.0
        }, source="user_provided")

        query = "Chúng tôi bây giờ là 6 người"
        result = self.agent.process(query, self.state, execute_llm=False)

        self.assertEqual(result.intent, INTENT_CHANGE_GROUP_SIZE)
        self.assertTrue(result.is_modification)
        self.assertIn("group_size", result.modified_constraint_fields)

        # Group size updated
        self.assertEqual(self.state.constraints.group_size, 6)
        # Dates and destination remain untouched
        self.assertEqual(self.state.constraints.destination, "Phú Quốc")
        self.assertEqual(self.state.constraints.start_date, "2026-11-10")
        self.assertEqual(self.state.constraints.duration_days, 3)
        self.assertEqual(self.state.constraints.budget, 12000000.0)

    # 4. Remove Stop Request
    def test_remove_stop_identified(self):
        query = "Bỏ điểm thứ 2 trong lịch trình giúp tôi"
        result = self.agent.process(query, self.state, execute_llm=False)

        self.assertEqual(result.intent, INTENT_REMOVE_STOP)
        self.assertTrue(result.is_modification)
        self.assertEqual(result.target_stop_to_remove, "2")

        # Also test naming a specific location
        query2 = "Xóa điểm Chợ Bến Thành ra nhé"
        result2 = self.agent.process(query2, self.state, execute_llm=False)
        self.assertEqual(result2.intent, INTENT_REMOVE_STOP)
        self.assertIn("chợ bến thành", result2.target_stop_to_remove.lower())

    # 5. Ambiguous Request Asks for Clarification without Guessing
    def test_ambiguous_request_triggers_clarification(self):
        query = "Tôi muốn đi du lịch"
        result = self.agent.process(query, self.state, execute_llm=False)

        self.assertEqual(result.intent, INTENT_CLARIFICATION_NEEDED)
        self.assertEqual(result.status, "clarification_needed")
        self.assertGreater(len(result.clarification_questions), 0)
        # Should NOT guess a random city
        self.assertIsNone(self.state.constraints.destination)

    # 6. Handling Constraint Conflicts and Invalid Inputs
    def test_invalid_constraint_inputs_returns_clarification(self):
        # Invalid date format
        analysis_with_bad_date = {
            "intent": INTENT_CREATE_ITINERARY,
            "extracted_entities": {
                "destination": "Huế",
                "start_date": "2026-02-31"  # non-existent calendar date
            }
        }
        modified, errors = self.agent._apply_updates_to_state(self.state, analysis_with_bad_date)
        self.assertIn("destination", modified)
        self.assertEqual(self.state.constraints.destination, "Huế")
        # Start date should NOT be applied due to calendar invalidity
        self.assertIsNone(self.state.constraints.start_date)
        self.assertGreater(len(errors), 0)

        # Negative budget
        analysis_with_neg_budget = {
            "intent": INTENT_CHANGE_BUDGET,
            "extracted_entities": {
                "budget": -500000.0
            }
        }
        modified2, errors2 = self.agent._apply_updates_to_state(self.state, analysis_with_neg_budget)
        self.assertNotIn("budget", modified2)
        self.assertGreater(len(errors2), 0)

    # 7. LLM Malformed Output Schema Triggers Safe Fallback
    def test_llm_malformed_output_falls_back_to_deterministic_parsing(self):
        # Mock Gemini returning invalid JSON syntax
        with patch(
            "src.services.travel_chatbot_service._call_gemini_generate_content",
            return_value={"text": "Tôi nghĩ bạn muốn đi Đà Nẵng nhưng tôi không trả JSON đâu!"}
        ):
            with patch("src.services.travel_chatbot_service.gemini_key_manager.has_keys", return_value=True):
                query = "Tôi muốn đi Đà Nẵng 3 ngày"
                result = self.agent.process(query, self.state, execute_llm=True)

                # Fallback successfully recognized Đà Nẵng and 3 days
                self.assertEqual(result.intent, INTENT_CREATE_ITINERARY)
                self.assertEqual(self.state.constraints.destination, "Đà Nẵng")
                self.assertEqual(self.state.constraints.duration_days, 3)

    # 8. LLM Error / Network Failure Triggers Safe Fallback
    def test_llm_api_error_falls_back_to_deterministic_parsing(self):
        # Mock Gemini raising an exception
        with patch(
            "src.services.travel_chatbot_service._call_gemini_generate_content",
            side_effect=Exception("API Key Quota Exhausted 429")
        ):
            with patch("src.services.travel_chatbot_service.gemini_key_manager.has_keys", return_value=True):
                query = "Tìm quán cà phê đẹp ở Hội An"
                result = self.agent.process(query, self.state, execute_llm=True)

                self.assertEqual(result.intent, INTENT_SEARCH_PLACES)
                self.assertEqual(self.state.constraints.destination, "Hội An")

    # 9. Out of Scope Query Recognition
    def test_out_of_scope_query(self):
        queries = [
            "Viết cho tôi một hàm Python giải phương trình bậc hai",
            "Cho tôi kết quả bóng đá Ngoại Hạng Anh tối qua",
            "Hướng dẫn giải tích phân bội 2"
        ]
        for q in queries:
            state = TravelSessionState(session_id="test_oos")
            result = self.agent.process(q, state, execute_llm=False)
            self.assertEqual(result.intent, INTENT_OUT_OF_SCOPE)
            self.assertTrue(result.is_out_of_scope)
            self.assertEqual(result.status, "out_of_scope")

    # 10. Trace is Logged in Session State
    def test_trace_is_recorded_in_state(self):
        query = "Lên lịch đi Sa Pa 2 ngày"
        initial_traces = len(self.state.agent_traces)
        self.agent.process(query, self.state, execute_llm=False)
        self.assertEqual(len(self.state.agent_traces), initial_traces + 1)
        self.assertEqual(self.state.agent_traces[-1].agent_name, "intent_agent")


if __name__ == "__main__":
    unittest.main()
