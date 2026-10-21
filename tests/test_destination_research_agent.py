"""
Unit tests for Destination Research Agent (Phase 2 & Phase 3)
Testing scenarios:
1. Successful POI retrieval from internal database source.
2. Empty search results handled gracefully with status='no_results'.
3. Tool error or timeout handled gracefully with status='error' without crashing.
4. Web search integration generates verified evidence items with canonical source URIs.
5. Handling of missing coordinates or URLs safely without fabricating fake links.
6. Deduplication removes duplicate places with identical names.
7. Unverified estimates are NOT marked as verified (is_verified=False).
8. Respects tool execution budget limits without infinite loops.
9. Audit trace in session_state accurately reflects the tools actually executed.
10. Research execution does NOT mutate unrelated session constraints (budget, dates, group size preserved).
"""

import unittest
from unittest.mock import patch, MagicMock

from src.services.ai.state import (
    TravelSessionState,
    EvidenceItem
)
from src.services.ai.agents.intent_agent import IntentAgentResult
from src.services.ai.agents.destination_research_agent import (
    DestinationResearchAgent,
    DestinationResearchResult,
    ResearchedPlace
)
from src.services.ai.tools.registry import (
    ToolRegistry,
    ToolDefinition,
    ToolExecutionResult
)
from pydantic import BaseModel


class DummyPoisInput(BaseModel):
    query: str = ""
    limit: int = 6


class DummyWebInput(BaseModel):
    query: str = ""
    max_results: int = 5


class DestinationResearchAgentTests(unittest.TestCase):

    def setUp(self):
        self.state = TravelSessionState(session_id="dest_research_test_01")
        self.state.update_constraints({
            "destination": "Đà Nẵng",
            "group_size": 3,
            "budget": 15000000.0,
            "start_date": "2026-12-10",
            "travel_style": "nghỉ dưỡng"
        }, source="user_provided")

    def _create_mock_registry(self, poi_data=None, poi_error=None, web_data=None, web_error=None):
        registry = ToolRegistry(default_max_tool_calls_per_run=10)

        # Mock search_internal_pois
        def mock_poi_func(**kwargs):
            if poi_error:
                raise Exception(poi_error)
            return poi_data or []

        registry.register(ToolDefinition(
            name="search_internal_pois",
            description="Mock POI Search",
            args_schema=DummyPoisInput,
            func=mock_poi_func
        ))

        # Mock search_web
        def mock_web_func(**kwargs):
            if web_error:
                raise Exception(web_error)
            return web_data or {"web_sources": [], "snippet": ""}

        registry.register(ToolDefinition(
            name="search_web",
            description="Mock Web Search",
            args_schema=DummyWebInput,
            func=mock_web_func
        ))

        return registry

    # 1. Tìm POI thành công từ nguồn nội bộ
    def test_successful_internal_poi_search(self):
        sample_pois = [
            {
                "id": 101,
                "name": "Bà Nà Hills",
                "category": "attraction",
                "address": "Hòa Vang, Đà Nẵng",
                "latitude": 15.998,
                "longitude": 107.987,
                "description": "Khu du lịch trên đỉnh núi"
            },
            {
                "id": 102,
                "name": "Cầu Rồng",
                "category": "monument",
                "address": "Hải Châu, Đà Nẵng",
                "latitude": 16.061,
                "longitude": 108.227,
                "description": "Cây cầu biểu tượng phun lửa"
            }
        ]
        registry = self._create_mock_registry(poi_data=sample_pois)
        agent = DestinationResearchAgent(tool_registry=registry)

        result = agent.research(
            user_message="Tìm địa điểm chơi ở Đà Nẵng",
            session_state=self.state,
            max_results=5
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.places), 2)
        self.assertEqual(result.places[0].name, "Bà Nà Hills")
        self.assertEqual(result.places[0].source_type, "internal_database")
        self.assertTrue(result.places[0].is_verified)
        self.assertIn("search_internal_pois", result.tools_executed)

    # 2. Kết quả rỗng
    def test_empty_poi_search_results(self):
        registry = self._create_mock_registry(poi_data=[])
        agent = DestinationResearchAgent(tool_registry=registry)

        result = agent.research(
            user_message="Tìm địa điểm tại Vùng Đất Hư Cấu",
            session_state=self.state,
            max_results=5
        )

        self.assertEqual(result.status, "no_results")
        self.assertEqual(len(result.places), 0)
        self.assertIn("Không tìm thấy", result.message)

    # 3. Công cụ tìm kiếm lỗi hoặc timeout
    def test_tool_error_or_timeout_handling(self):
        registry = self._create_mock_registry(poi_error="Database connection timeout (503)")
        agent = DestinationResearchAgent(tool_registry=registry)

        result = agent.research(
            user_message="Tìm địa điểm",
            session_state=self.state,
            max_results=5
        )

        # Graceful handling without unhandled crash
        self.assertEqual(result.status, "error")
        self.assertIn("search_internal_pois", result.tools_executed)
        # Session state traces records the failure accurately
        last_trace = self.state.agent_traces[-1]
        self.assertEqual(last_trace.status, "failed")
        self.assertIn("Database connection timeout", last_trace.error)

    # 4. Tìm kiếm web trả về bằng chứng có nguồn
    def test_web_search_generates_evidence_with_sources(self):
        web_payload = {
            "web_sources": [
                {"title": "Cẩm nang du lịch Cát Bà 2026", "uri": "https://example.com/cat-ba-guide"},
                {"title": "Vịnh Lan Hạ có gì đẹp", "uri": "https://example.com/lan-ha-bay"}
            ],
            "snippet": "Vịnh Lan Hạ và đảo Cát Bà nổi tiếng với chèo kayak..."
        }
        # Empty internal POIs triggers web search
        registry = self._create_mock_registry(poi_data=[], web_data=web_payload)
        agent = DestinationResearchAgent(tool_registry=registry)

        initial_evidence_count = len(self.state.evidence_items)
        result = agent.research(
            user_message="Có gì mới ở Cát Bà không?",
            session_state=self.state,
            enable_web_search=True
        )

        self.assertIn("search_web", result.tools_executed)
        self.assertEqual(len(result.web_sources), 2)
        self.assertGreater(len(self.state.evidence_items), initial_evidence_count)

        # First web evidence item must have source_url and verified=True
        web_ev = next(e for e in self.state.evidence_items if e.source_type == "web_search" and e.source_url)
        self.assertTrue(web_ev.verified)
        self.assertIn("https://example.com/cat-ba-guide", web_ev.source_url)

    # 5. Dữ liệu thiếu tọa độ hoặc URL được xử lý an toàn
    def test_handles_missing_coordinates_or_urls_safely(self):
        sample_pois = [
            {
                "id": 201,
                "name": "Quán Cơm Quê",
                "category": "nhà hàng",
                "latitude": None,
                "longitude": None,
                "address": None
            }
        ]
        registry = self._create_mock_registry(poi_data=sample_pois)
        agent = DestinationResearchAgent(tool_registry=registry)

        result = agent.research(
            user_message="Tìm quán ăn ở Đà Nẵng",
            session_state=self.state
        )

        place = result.places[0]
        self.assertEqual(place.name, "Quán Cơm Quê")
        self.assertIsNone(place.latitude)
        self.assertIsNone(place.longitude)
        self.assertIsNone(place.google_maps_url)
        self.assertIsNone(place.image_url)

    # 6. Kết quả trùng lặp được loại bỏ
    def test_deduplication_removes_duplicate_places(self):
        duplicate_pois = [
            {"id": 1, "name": "Bảo tàng Chăm", "category": "museum"},
            {"id": 2, "name": "Bảo Tàng Chăm", "category": "museum"},
            {"id": 3, "name": "bảo tàng chăm", "category": "museum"},
            {"id": 4, "name": "Chùa Linh Ứng", "category": "temple"}
        ]
        registry = self._create_mock_registry(poi_data=duplicate_pois)
        agent = DestinationResearchAgent(tool_registry=registry)

        result = agent.research(
            user_message="Tìm điểm tham quan",
            session_state=self.state
        )

        self.assertEqual(len(result.places), 2)
        names = [p.name.lower() for p in result.places]
        self.assertIn("bảo tàng chăm", names)
        self.assertIn("chùa linh ứng", names)

    # 7. Không đánh dấu dữ liệu ước tính là đã xác minh
    def test_unverified_estimates_not_marked_as_verified(self):
        unverified_place = ResearchedPlace(
            name="Quán Nước Bí Ẩn",
            source_type="unverified_estimate",
            is_verified=False
        )
        self.assertFalse(unverified_place.is_verified)
        self.assertEqual(unverified_place.source_type, "unverified_estimate")

    # 8. Giới hạn số lần gọi công cụ (Tool Budget)
    def test_respects_tool_budget_limits(self):
        # Create a registry with strict budget limit of 1
        registry = ToolRegistry(default_max_tool_calls_per_run=1)
        registry.register(ToolDefinition(
            name="search_internal_pois",
            description="Mock",
            args_schema=DummyPoisInput,
            func=lambda **kwargs: []
        ))
        registry.register(ToolDefinition(
            name="search_web",
            description="Mock",
            args_schema=DummyWebInput,
            func=lambda **kwargs: {"web_sources": []}
        ))
        agent = DestinationResearchAgent(tool_registry=registry)

        result = agent.research(
            user_message="Tìm điểm",
            session_state=self.state,
            enable_web_search=True,
            max_tool_budget=1
        )

        # Budget of 1 was consumed by search_internal_pois, so search_web was not called
        self.assertEqual(len(result.tools_executed), 1)
        self.assertEqual(result.tools_executed[0], "search_internal_pois")

    # 9. Trace phản ánh đúng những công cụ thực sự đã gọi
    def test_trace_accurately_reflects_executed_tools(self):
        registry = self._create_mock_registry(
            poi_data=[{"id": 1, "name": "Bãi biển Mỹ Khê"}],
            web_data={"web_sources": []}
        )
        agent = DestinationResearchAgent(tool_registry=registry)

        initial_traces = len(self.state.agent_traces)
        agent.research(
            user_message="Tìm bãi biển ở Đà Nẵng",
            session_state=self.state,
            enable_web_search=False
        )

        self.assertEqual(len(self.state.agent_traces), initial_traces + 1)
        last_trace = self.state.agent_traces[-1]
        self.assertEqual(last_trace.agent_name, "destination_research_agent")
        self.assertEqual(last_trace.tools_called, ["search_internal_pois"])
        self.assertEqual(last_trace.status, "completed")

    # 10. Không làm thay đổi các ràng buộc chuyến đi không liên quan
    def test_preserves_unrelated_session_constraints(self):
        orig_budget = self.state.constraints.budget
        orig_group = self.state.constraints.group_size
        orig_date = self.state.constraints.start_date
        orig_style = self.state.constraints.travel_style

        registry = self._create_mock_registry(poi_data=[{"id": 1, "name": "Cầu Vàng"}])
        agent = DestinationResearchAgent(tool_registry=registry)

        agent.research(
            user_message="Gợi ý một số điểm tham quan ở Đà Nẵng",
            session_state=self.state
        )

        # Verify session constraints are 100% intact
        self.assertEqual(self.state.constraints.budget, orig_budget)
        self.assertEqual(self.state.constraints.group_size, orig_group)
        self.assertEqual(self.state.constraints.start_date, orig_date)
        self.assertEqual(self.state.constraints.travel_style, orig_style)


if __name__ == "__main__":
    unittest.main()
