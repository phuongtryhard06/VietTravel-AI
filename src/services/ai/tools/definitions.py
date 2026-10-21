"""
VietTravel AI - Standard Tool Definitions and Global Registry Setup (Phase 3)
Connects all implemented and existing tools into the ToolRegistry.
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict

from src.services.ai.tools.registry import ToolDefinition, ToolRegistry, global_tool_registry
from src.services.ai.tools.poi_search_tool import search_staging_pois, calculate_haversine_distance
from src.services.ai.tools.routing_tool import calculate_route_legs
from src.services.ai.tools.budget_calculator_tool import calculate_trip_budget, CalculateBudgetInput
from src.services.ai.tools.web_search_tool import search_web_travel_info, SearchWebInput
from src.services.ai.tools.image_crawl_tool import crawl_real_image_tool, CrawlRealImageInput


# 1. POI Search Input Schema
class SearchInternalPoisInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: Optional[str] = Field(default=None, description="Keywords or attraction name")
    category: Optional[str] = Field(default=None, description="Category filter (e.g. cà phê, nhà hàng, bảo tàng)")
    latitude: Optional[float] = Field(default=None, description="WGS84 latitude")
    longitude: Optional[float] = Field(default=None, description="WGS84 longitude")
    radius_km: Optional[float] = Field(default=10.0, ge=0.5, le=100.0, description="Search radius in kilometers")
    limit: int = Field(default=6, ge=1, le=50, description="Max results")


def _execute_search_internal_pois(
    query: Optional[str] = None,
    category: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius_km: Optional[float] = 10.0,
    limit: int = 6
) -> Dict[str, Any]:
    return search_staging_pois(
        query=query,
        category=category,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        limit=limit,
        include_chromadb=False
    )


# 2. Distance Calculation Input Schema
class CalculateDistanceInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat1: float = Field(..., ge=-90.0, le=90.0, description="Start latitude")
    lon1: float = Field(..., ge=-180.0, le=180.0, description="Start longitude")
    lat2: float = Field(..., ge=-90.0, le=90.0, description="End latitude")
    lon2: float = Field(..., ge=-180.0, le=180.0, description="End longitude")


def _execute_calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> Dict[str, Any]:
    straight_line_km = calculate_haversine_distance(lat1, lon1, lat2, lon2)
    # Estimated road distance is approx 1.3x straight line
    road_estimate_km = round(straight_line_km * 1.3, 2)
    return {
        "straight_line_km": round(straight_line_km, 2),
        "estimated_road_km": road_estimate_km,
        "calculation_method": "Haversine formula x 1.3 road factor"
    }


# 3. Route Calculation Input Schema
class GetRouteInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: List[Dict[str, Any]] = Field(..., min_length=2, description="Ordered list of stops with coordinates")
    execute_api: bool = Field(default=False, description="Whether to call external Mapbox API")


def _execute_get_route(items: List[Dict[str, Any]], execute_api: bool = False) -> Dict[str, Any]:
    return calculate_route_legs(items=items, execute_api=execute_api)


def build_default_tool_registry() -> ToolRegistry:
    """Builds and returns a populated ToolRegistry instance."""
    registry = ToolRegistry(default_max_tool_calls_per_run=10)

    # Register search_internal_pois
    registry.register(ToolDefinition(
        name="search_internal_pois",
        description="Tìm kiếm địa điểm du lịch, ẩm thực, văn hóa từ cơ sở dữ liệu đệm PostgreSQL OSM Staging.",
        args_schema=SearchInternalPoisInput,
        func=_execute_search_internal_pois,
        timeout_seconds=8.0,
        max_retries=1,
        is_external_api=False
    ))

    # Register calculate_distance
    registry.register(ToolDefinition(
        name="calculate_distance",
        description="Tính khoảng cách đường chim bay và ước tính cự ly đường bộ giữa 2 tọa độ GPS.",
        args_schema=CalculateDistanceInput,
        func=_execute_calculate_distance,
        timeout_seconds=2.0,
        max_retries=0,
        is_external_api=False
    ))

    # Register get_route
    registry.register(ToolDefinition(
        name="get_route",
        description="Tính toán chặng di chuyển và khoảng cách liên tiếp cho chuỗi điểm dừng trong lịch trình.",
        args_schema=GetRouteInput,
        func=_execute_get_route,
        timeout_seconds=6.0,
        max_retries=1,
        is_external_api=False
    ))

    # Register calculate_trip_budget
    registry.register(ToolDefinition(
        name="calculate_trip_budget",
        description="Tính toán chi phí chuyến đi chi tiết (lưu trú, ăn uống, di chuyển, vé tham quan, dự phòng) bằng công thức tất định.",
        args_schema=CalculateBudgetInput,
        func=calculate_trip_budget,
        timeout_seconds=3.0,
        max_retries=0,
        is_external_api=False
    ))

    # Register search_web
    registry.register(ToolDefinition(
        name="search_web",
        description="Tra cứu thông tin du lịch cập nhật và xác thực nguồn tin qua Google Search Grounding.",
        args_schema=SearchWebInput,
        func=search_web_travel_info,
        timeout_seconds=15.0,
        max_retries=1,
        is_external_api=True
    ))

    # Register crawl_real_image
    registry.register(ToolDefinition(
        name="crawl_real_image",
        description="Tác tử cào ảnh thực tế tự động từ Wikipedia, Wikimedia Commons và web search cho các địa danh hoặc toàn cảnh điểm đến.",
        args_schema=CrawlRealImageInput,
        func=crawl_real_image_tool,
        timeout_seconds=8.0,
        max_retries=1,
        is_external_api=True
    ))

    return registry


# Populate the global tool registry
_default_reg = build_default_tool_registry()
for _tool in _default_reg.list_tools():
    global_tool_registry.register(_tool)
