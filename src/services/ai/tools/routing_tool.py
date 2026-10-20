"""
Module Map Routing Tool (Giai đoạn F)
Cung cấp công cụ tính toán cự ly và tuyến đường di chuyển giữa các điểm dừng trong lịch trình du lịch.

Tuân thủ nghiêm ngặt các nguyên tắc:
1. Tiếp nhận tọa độ của các điểm dừng liên tiếp trong lịch trình nháp.
2. Quản lý Mapbox token an toàn: đọc MAPBOX_ACCESS_TOKEN hoặc MAPBOX_TOKEN từ môi trường,
   tuyệt đối không in token ra màn hình/log và không gọi API bên ngoài khi đang triển khai.
3. Khi thiếu dữ liệu tuyến đường thực tế: tính khoảng cách theo đường chim bay Haversine x 1.3
   với nhãn "ước tính" (distance_type: "ước tính"), thời gian di chuyển đặt là null (None)
   kèm cảnh báo tường minh; tuyệt đối không giả mạo làm thời gian thật.
4. Không sửa đổi map_service.py để truy vấn bảng Attractions.
"""

import os
import math
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

# Bán kính Trái Đất trung bình theo km (WGS 84)
EARTH_RADIUS_KM = 6371.0

# Hệ số uốn lượn đường bộ ước tính so với đường chim bay
ROAD_DETOUR_FACTOR = 1.3


def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Tính khoảng cách đường tròn lớn giữa 2 tọa độ GPS bằng công thức Haversine (km).
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_KM * c


def get_mapbox_token() -> Optional[str]:
    """
    Đọc token Mapbox an toàn từ biến môi trường.
    Tuyệt đối không in hoặc log giá trị bí mật của token.
    """
    return os.getenv("MAPBOX_ACCESS_TOKEN") or os.getenv("MAPBOX_TOKEN")


def calculate_route_legs(
    items: List[Dict[str, Any]],
    execute_api: bool = False
) -> Dict[str, Any]:
    """
    Tính toán cự ly và thông tin chặng di chuyển giữa các điểm liên tiếp trong lịch trình nháp.
    
    Args:
        items: Danh sách các điểm dừng trong lịch nháp (chứa coordinates, osm_id, name).
        execute_api: Cờ cho phép gọi API Mapbox bên ngoài (mặc định False: không gọi API ngoài khi đang triển khai).
        
    Returns:
        Dict[str, Any]: Cấu trúc chuẩn gồm total_legs, total_distance_km, route_legs và warnings.
    """
    if not items or len(items) < 2:
        return {
            "success": True,
            "total_legs": 0,
            "total_distance_km": 0.0,
            "distance_type": "ước tính",
            "route_legs": [],
            "warnings": ["Lịch trình có ít hơn 2 điểm dừng, không phát sinh chặng di chuyển."]
        }

    token = get_mapbox_token()
    route_legs = []
    warnings = []

    for i in range(len(items) - 1):
        curr_stop = items[i]
        next_stop = items[i + 1]

        curr_coords = curr_stop.get("coordinates") or {}
        next_coords = next_stop.get("coordinates") or {}

        lat1 = curr_coords.get("latitude")
        lon1 = curr_coords.get("longitude")
        lat2 = next_coords.get("latitude")
        lon2 = next_coords.get("longitude")

        curr_name = curr_stop.get("name") or f"Điểm #{i + 1}"
        next_name = next_stop.get("name") or f"Điểm #{i + 2}"

        # Kiểm tra tính hợp lệ của tọa độ
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            warning_msg = f"Chặng #{i + 1} ({curr_name} -> {next_name}): Thiếu tọa độ GPS, không thể tính cự ly."
            warnings.append(warning_msg)
            route_legs.append({
                "leg_index": i + 1,
                "from_stop": {
                    "order_index": curr_stop.get("order_index", i + 1),
                    "osm_id": curr_stop.get("osm_id"),
                    "name": curr_name,
                    "coordinates": curr_coords
                },
                "to_stop": {
                    "order_index": next_stop.get("order_index", i + 2),
                    "osm_id": next_stop.get("osm_id"),
                    "name": next_name,
                    "coordinates": next_coords
                },
                "distance_km": None,
                "distance_type": "chưa xác định",
                "haversine_direct_km": None,
                "travel_time_minutes": None,
                "routing_source": "missing_coordinates",
                "warning": warning_msg
            })
            continue

        # Tính khoảng cách đường chim bay
        haversine_dist = calculate_haversine_distance(float(lat1), float(lon1), float(lat2), float(lon2))
        estimated_dist = round(haversine_dist * ROAD_DETOUR_FACTOR, 2)

        # Mặc định theo quy định: KHÔNG GỌI API BÊN NGOÀI KHI ĐANG TRIỂN KHAI
        # Trả khoảng cách Haversine x 1.3 với nhãn "ước tính", travel_time_minutes = null
        leg_warning = (
            f"Chặng #{i + 1} ({curr_name} -> {next_name}): Khoảng cách được ước tính bằng đường chim bay x 1.3 "
            f"({estimated_dist} km); thời gian di chuyển là null (chưa có tuyến thực tế từ Mapbox/OSRM)."
        )
        warnings.append(leg_warning)

        route_legs.append({
            "leg_index": i + 1,
            "from_stop": {
                "order_index": curr_stop.get("order_index", i + 1),
                "osm_id": curr_stop.get("osm_id"),
                "name": curr_name,
                "coordinates": {"latitude": float(lat1), "longitude": float(lon1)}
            },
            "to_stop": {
                "order_index": next_stop.get("order_index", i + 2),
                "osm_id": next_stop.get("osm_id"),
                "name": next_name,
                "coordinates": {"latitude": float(lat2), "longitude": float(lon2)}
            },
            "distance_km": estimated_dist,
            "distance_type": "ước tính",
            "haversine_direct_km": round(haversine_dist, 2),
            "travel_time_minutes": None,  # Tuyệt đối không giả mạo làm thời gian thật
            "routing_source": "haversine_detour_1.3",
            "warning": leg_warning
        })

    valid_distances = [leg["distance_km"] for leg in route_legs if leg.get("distance_km") is not None]
    total_dist = round(sum(valid_distances), 2) if valid_distances else 0.0

    return {
        "success": True,
        "total_legs": len(route_legs),
        "total_distance_km": total_dist,
        "distance_type": "ước tính",
        "route_legs": route_legs,
        "warnings": warnings
    }


class MapRoutingTool:
    """
    Lớp đóng gói Routing Tool tương thích hệ thống Multi-Agent (LangChain / LangGraph).
    """
    name: str = "calculate_route_legs"
    description: str = (
        "Tính toán cự ly và tuyến đường di chuyển giữa các điểm dừng trong lịch trình nháp. "
        "Nếu chưa có kết nối Mapbox thực tế, trả khoảng cách ước tính Haversine x 1.3 và thời gian null."
    )

    def run(self, items: List[Dict[str, Any]], execute_api: bool = False) -> Dict[str, Any]:
        return calculate_route_legs(items, execute_api=execute_api)

    def __call__(self, items: List[Dict[str, Any]], execute_api: bool = False) -> Dict[str, Any]:
        return self.run(items, execute_api=execute_api)


# Hỗ trợ chạy thử nghiệm nhanh qua dòng lệnh
if __name__ == "__main__":
    import json

    sample_items = [
        {
            "order_index": 1,
            "osm_id": "osm_6685192250",
            "name": "Cà Phê Phố Xưa",
            "coordinates": {"latitude": 21.040913, "longitude": 105.840693}
        },
        {
            "order_index": 2,
            "osm_id": "osm_13392766014",
            "name": "Com Tam Suon",
            "coordinates": {"latitude": 21.040985, "longitude": 105.840996}
        },
        {
            "order_index": 3,
            "osm_id": "osm_4227974092",
            "name": "Tổ Chim Xanh",
            "coordinates": {"latitude": 21.041921, "longitude": 105.841541}
        }
    ]

    res = calculate_route_legs(sample_items, execute_api=False)
    print(json.dumps(res, ensure_ascii=False, indent=2))
