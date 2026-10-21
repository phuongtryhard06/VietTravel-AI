"""
Integration tests for Backend Multi-Agent Coordinator Integration (Phase 2 & Phase 3)
Testing scenarios:
1. Authenticated travel chat request with valid JWT and user_id extraction.
2. Successful itinerary generation via chat (CoordinatorResult mapped to chatbot response).
3. Attraction search without unnecessary planning or budgeting.
4. Clarification response for missing constraints (follow_up_questions populated, 200 OK).
5. Budget modification preserving user's budget ceiling in extracted_features.
6. Group-size and date changes triggering correct recalculation.
7. Removing a stop from an existing itinerary.
8. Destination research failure handled gracefully without 500 crash.
9. Planner validation failure returns honest partial response without 500.
10. Budget calculation failure preserves valid itinerary in response.
11. Conversation persistence and ownership enforcement (403 for unauthorized access).
12. API response schema compatibility (strict adherence to chatbot_response_model).
13. Existing endpoint regression behavior (itinerary draft endpoint intact).
14. Feature-flag rollback (ENABLE_MULTI_AGENT_COORDINATOR=False routes to legacy pipeline).
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import jwt
from flask import Flask
from flask_restx import Api

from src.models.base import db
from src.models.conversation import Conversation
from src.models.user import User
from src.config.config import Config
from src.controllers.travel_chatbot_controller import travel_chatbot_ns
from src.controllers.Itinerary_controller import itinerary_ns
from src.services.ai.state import (
    TravelSessionState,
    TripConstraints,
    BudgetBreakdown
)
from src.services.ai.agents.destination_research_agent import (
    DestinationResearchAgent,
    DestinationResearchResult,
    ResearchedPlace
)
from src.services.ai.agents.budget_agent import (
    BudgetAgent,
    BudgetAgentResult
)
from src.services.ai.session_manager import default_session_manager
from src.services.ai.backend_adapter import (
    is_coordinator_enabled,
    handle_coordinator_chat_request,
    adapt_coordinator_result_to_chat_response,
    extract_user_id_from_header
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "osm_vietnam_pilot_sample.json"


class BackendIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        Config.SECRET_KEY = Config.SECRET_KEY or "test_secret_key_viet_travel"
        cls.app = Flask(__name__)
        cls.app.config['TESTING'] = True
        cls.app.config['SECRET_KEY'] = Config.SECRET_KEY

        cls.api = Api(cls.app, doc=False)
        cls.api.add_namespace(travel_chatbot_ns, path="/api/travel-chatbot")
        cls.api.add_namespace(itinerary_ns, path="/api/itinerary")
        cls.client = cls.app.test_client()

        # Load pilot fixture for deterministic tests
        if FIXTURE_PATH.exists():
            cls.pilot_pois = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        else:
            cls.pilot_pois = [
                {
                    "osm_id": 101,
                    "name": "Bà Nà Hills",
                    "category": "attraction",
                    "fclass": "attraction",
                    "latitude": 15.998,
                    "longitude": 107.987,
                    "address": "Đà Nẵng"
                },
                {
                    "osm_id": 102,
                    "name": "Cầu Rồng",
                    "category": "monument",
                    "fclass": "monument",
                    "latitude": 16.061,
                    "longitude": 108.227,
                    "address": "Đà Nẵng"
                },
                {
                    "osm_id": 103,
                    "name": "Bãi biển Mỹ Khê",
                    "category": "beach",
                    "fclass": "beach",
                    "latitude": 16.059,
                    "longitude": 108.243,
                    "address": "Đà Nẵng"
                }
            ]

    def setUp(self):
        default_session_manager.clear()
        # Patch search_staging_pois globally for test isolation
        self.patcher_staging = patch(
            "src.services.ai.tools.definitions.search_staging_pois",
            return_value={"success": True, "data": self.pilot_pois}
        )
        self.patcher_orch = patch(
            "src.services.ai.orchestrator.search_staging_pois",
            return_value={"success": True, "data": self.pilot_pois}
        )
        self.patcher_poi_tool = patch(
            "src.services.ai.tools.poi_search_tool.search_staging_pois",
            return_value={"success": True, "data": self.pilot_pois}
        )
        self.mock_staging = self.patcher_staging.start()
        self.mock_orch = self.patcher_orch.start()
        self.mock_poi_tool = self.patcher_poi_tool.start()

    def tearDown(self):
        self.patcher_staging.stop()
        self.patcher_orch.stop()
        self.patcher_poi_tool.stop()

    def _create_jwt_token(self, user_id: int = 1) -> str:
        payload = {
            'user_id': user_id,
            'exp': datetime.now(timezone.utc) + timedelta(days=1)
        }
        return jwt.encode(payload, Config.SECRET_KEY, algorithm='HS256')

    # 1. Authenticated travel chat request
    def test_authenticated_travel_chat_request(self):
        token = self._create_jwt_token(user_id=42)
        headers = {'Authorization': f'Bearer {token}'}

        response = self.client.post(
            "/api/travel-chatbot/search",
            headers=headers,
            json={"question": "Tìm địa điểm tham quan ở Đà Nẵng"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("ai_pipeline", data)
        self.assertIn("coordinator", data["ai_pipeline"])

    # 2. Successful itinerary generation via chat
    def test_successful_itinerary_generation_via_chat(self):
        response = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Lên lịch trình 3 ngày ở Đà Nẵng"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("lịch trình", data["response"].lower())
        features = data.get("extracted_features", {})
        self.assertIn("itinerary_draft", features)
        self.assertIn("budget_breakdown", features)
        self.assertGreater(len(data.get("search_results", [])), 0)

    # 3. Attraction search without unnecessary planning or budgeting
    def test_attraction_search_without_unnecessary_planning(self):
        response = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Tìm quán cà phê và điểm check-in ở Đà Nẵng"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        # Places should be returned
        self.assertGreater(len(data.get("search_results", [])), 0)
        # Should not have planned an itinerary
        features = data.get("extracted_features", {})
        self.assertNotIn("itinerary_draft", features)

    # 4. Clarification response for missing constraints
    def test_clarification_response_for_missing_constraints(self):
        response = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Tôi muốn đi du lịch"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        # follow_up_questions should contain clarification prompts
        self.assertGreater(len(data.get("follow_up_questions", [])), 0)

    # 5. Budget modification preserving user's budget ceiling
    def test_budget_modification_preserving_user_budget(self):
        # Initial turn to start session
        res1 = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Lên lịch trình 3 ngày ở Đà Nẵng"}
        )
        session_id = res1.get_json()["extracted_features"]["session_id"]

        # Follow-up turn modifying budget
        res2 = self.client.post(
            "/api/travel-chatbot/search",
            json={
                "question": "Đổi ngân sách thành 12 triệu",
                "session_id": session_id
            }
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        features2 = data2.get("extracted_features", {})
        self.assertEqual(features2.get("budget"), 12000000.0)

    # 6. Group-size and date changes triggering correct recalculation
    def test_group_size_and_date_changes_trigger_recalculation(self):
        res1 = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Lên lịch trình 3 ngày ở Đà Nẵng"}
        )
        session_id = res1.get_json()["extracted_features"]["session_id"]

        # Change group size
        res2 = self.client.post(
            "/api/travel-chatbot/search",
            json={
                "question": "Đoàn chúng tôi đi 5 người",
                "session_id": session_id
            }
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertEqual(data2["extracted_features"]["group_size"], 5)

        # Change start date
        res3 = self.client.post(
            "/api/travel-chatbot/search",
            json={
                "question": "Đổi ngày bắt đầu sang 2026-12-20",
                "session_id": session_id
            }
        )
        self.assertEqual(res3.status_code, 200)
        data3 = res3.get_json()
        self.assertEqual(data3["extracted_features"]["start_date"], "2026-12-20")

    # 7. Removing a stop from an existing itinerary
    def test_removing_stop_from_existing_itinerary(self):
        res1 = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Lên lịch trình 3 ngày ở Đà Nẵng"}
        )
        data1 = res1.get_json()
        session_id = data1["extracted_features"]["session_id"]
        initial_stops = data1["extracted_features"]["itinerary_draft"]["total_stops"]

        # Remove stop 2
        res2 = self.client.post(
            "/api/travel-chatbot/search",
            json={
                "question": "Bỏ điểm thứ 2 khỏi lịch trình",
                "session_id": session_id
            }
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        new_stops = data2["extracted_features"]["itinerary_draft"]["total_stops"]
        self.assertEqual(new_stops, initial_stops - 1)

    # 8. Destination research failure handled gracefully without 500 crash
    def test_destination_research_failure_graceful_handling(self):
        with patch.object(
            DestinationResearchAgent,
            "research",
            return_value=DestinationResearchResult(
                destination="Đà Nẵng",
                places=[],
                status="error",
                message="External database connection timeout"
            )
        ):
            response = self.client.post(
                "/api/travel-chatbot/search",
                json={"question": "Tìm điểm chơi ở Đà Nẵng"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("trục trặc khi tra cứu", data["response"])

    # 9. Planner validation failure returns honest partial response without 500
    def test_planner_validation_failure_graceful_handling(self):
        with patch(
            "src.services.ai.agents.coordinator_agent.ItineraryPlannerAgent.plan",
            return_value={
                "success": False,
                "status": "invalid_schedule",
                "validation": {"is_valid": False, "errors": ["Chồng chéo thời gian điểm dừng"]},
                "data": None
            }
        ):
            response = self.client.post(
                "/api/travel-chatbot/search",
                json={"question": "Lên lịch trình 3 ngày Đà Nẵng"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("thất bại", data["response"].lower())

    # 10. Budget calculation failure preserves valid itinerary in response
    def test_budget_calculation_failure_preserves_itinerary(self):
        with patch.object(
            BudgetAgent,
            "evaluate_budget",
            return_value=BudgetAgentResult(
                status="tool_failed",
                breakdown=None,
                message="Budget service unavailable"
            )
        ):
            response = self.client.post(
                "/api/travel-chatbot/search",
                json={"question": "Lên lịch trình 3 ngày Đà Nẵng"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            features = data.get("extracted_features", {})
            self.assertIn("itinerary_draft", features)

    # 11. Conversation persistence and ownership enforcement
    def test_conversation_persistence_and_ownership_enforcement(self):
        mock_conv = MagicMock()
        mock_conv.conversation_id = 999
        mock_conv.user_id = 99  # Owned by user 99

        with patch("src.services.ai.backend_adapter.get_conversation_by_id", return_value=mock_conv):
            # User 1 tries to access user 99's conversation -> 403 Forbidden
            token_user1 = self._create_jwt_token(user_id=1)
            headers = {'Authorization': f'Bearer {token_user1}'}
            res_forbidden = self.client.post(
                "/api/travel-chatbot/search",
                headers=headers,
                json={"question": "Chào bạn", "conversation_id": 999}
            )
            self.assertEqual(res_forbidden.status_code, 403)

            # Unauthenticated user tries to access user 99's conversation -> 401 Unauthorized
            res_unauth = self.client.post(
                "/api/travel-chatbot/search",
                json={"question": "Chào bạn", "conversation_id": 999}
            )
            self.assertEqual(res_unauth.status_code, 401)

            # Owner (user 99) accesses conversation -> 200 OK
            token_user99 = self._create_jwt_token(user_id=99)
            headers_owner = {'Authorization': f'Bearer {token_user99}'}
            res_ok = self.client.post(
                "/api/travel-chatbot/search",
                headers=headers_owner,
                json={"question": "Chào bạn", "conversation_id": 999}
            )
            self.assertEqual(res_ok.status_code, 200)

    # 12. API response schema compatibility
    def test_api_response_schema_compatibility(self):
        response = self.client.post(
            "/api/travel-chatbot/search",
            json={"question": "Tìm địa điểm ở Đà Nẵng"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        # Check all required keys in chatbot_response_model
        expected_keys = [
            'status', 'message', 'response', 'ai_status', 'ai_pipeline',
            'web_search_queries', 'web_sources', 'language', 'language_name',
            'search_results', 'suggested_activities', 'follow_up_questions',
            'extracted_features'
        ]
        for key in expected_keys:
            self.assertIn(key, data, f"Missing key in response: {key}")

        self.assertIsInstance(data['search_results'], list)
        self.assertIsInstance(data['suggested_activities'], list)
        self.assertIsInstance(data['follow_up_questions'], list)
        self.assertIsInstance(data['extracted_features'], dict)

    # 13. Existing endpoint regression behavior
    def test_existing_endpoint_regression_behavior(self):
        response = self.client.post(
            "/api/itinerary/multi-agent/plan-draft",
            json={"selected_date": "2026-11-20", "query": "Hà Nội", "max_stops": 3}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")

    # 14. Feature-flag rollback
    def test_feature_flag_rollback(self):
        with patch("src.services.ai.backend_adapter.is_coordinator_enabled", return_value=False):
            # When feature flag is False, handle_coordinator_chat_request returns (None, 200)
            payload, code = handle_coordinator_chat_request(
                request_data={"question": "Tìm điểm du lịch"},
                request_headers={}
            )
            self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
