"""
Module Constraint Validator Tool (Giai đoạn F)
Cung cấp công cụ kiểm định các ràng buộc logic cứng, thời gian, không gian và tuyến đường
của bản lịch trình nháp trước khi chuyển giao cho các bước tiếp theo.

Tuân thủ nghiêm ngặt các nguyên tắc:
1. Kiểm tra mã osm_id phải nằm trong danh sách POI ứng viên đầu vào (chống ảo giác).
2. Kiểm tra không trùng lặp địa điểm.
3. Kiểm tra tọa độ GPS hợp lệ trong lãnh thổ Việt Nam.
4. Kiểm tra định dạng thời gian (HH:MM), thời lượng hợp lệ (> 0 phút) và lịch trình không chồng chéo nhau.
5. Kiểm tra tổng thời gian lịch trình trong ngày không vượt quá 12 giờ (720 phút).
6. Giờ mở cửa: chỉ kiểm tra nếu dữ liệu thực tế có và đọc được; nếu null thì ghi cảnh báo (warnings), không bịa đặt.
7. Thời gian di chuyển: chỉ dùng thời gian tuyến đường thật để kiểm tra khoảng nghỉ giữa hai điểm liên tiếp;
   nếu chưa có (travel_time_minutes is None) thì báo trạng thái chưa xác định trong warnings.
8. Trả về cấu trúc kết quả chuẩn hóa gồm: valid, errors, warnings, route_legs và draft.
9. Không tự động lập lại lịch, không điều phối tác tử, không ghi cơ sở dữ liệu.
"""

import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, time

from src.services.ai.tools.routing_tool import calculate_route_legs

logger = logging.getLogger(__name__)

# Giới hạn thời lượng hoạt động tối đa trong một ngày (12 tiếng = 720 phút)
MAX_DAILY_SPAN_MINUTES = 12 * 60

# Giới hạn tọa độ hợp lệ của lãnh thổ Việt Nam (WGS 84)
VIETNAM_LAT_MIN = 8.0
VIETNAM_LAT_MAX = 24.0
VIETNAM_LON_MIN = 102.0
VIETNAM_LON_MAX = 110.0


def validate_itinerary_constraints(
    draft_plan: Dict[str, Any],
    candidate_pois: List[Dict[str, Any]],
    route_legs: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Kiểm định toàn bộ các ràng buộc logic, không gian, thời gian và tuyến đường cho lịch trình nháp.
    
    Args:
        draft_plan: Dữ liệu lịch trình nháp (hoặc đối tượng trả về từ planner_agent).
        candidate_pois: Danh sách POI ứng viên lấy từ search_staging_pois.
        route_legs: Danh sách chặng di chuyển (nếu chưa truyền vào, hàm sẽ tự tính qua calculate_route_legs).
        
    Returns:
        Dict[str, Any]: Kết quả kiểm định chuẩn gồm các trường:
            - valid (bool)
            - errors (List[str])
            - warnings (List[str])
            - route_legs (List[Dict])
            - draft (Dict)
            - summary (Dict)
    """
    errors: List[str] = []
    warnings: List[str] = []

    # Chuẩn hóa cấu trúc draft đầu vào (hỗ trợ cả dạng bọc trong plan/data hoặc trực tiếp)
    draft_data = draft_plan.get("data") if isinstance(draft_plan.get("data"), dict) else draft_plan
    items = draft_data.get("items") or []

    # 1. Kiểm tra danh sách điểm dừng không rỗng
    if not items or len(items) == 0:
        errors.append("Lịch trình không có điểm dừng nào để kiểm tra ràng buộc.")
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "route_legs": [],
            "draft": draft_data,
            "summary": {
                "total_stops": 0,
                "total_span_hours": 0.0,
                "total_visit_minutes": 0,
                "total_estimated_distance_km": 0.0
            }
        }

    # Bảng tra cứu POI ứng viên để đối chiếu tính xác thực
    candidate_lookup = {p.get("osm_id"): p for p in candidate_pois if p.get("osm_id")}

    seen_osm_ids = set()
    time_regex = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
    previous_end_minutes: Optional[int] = None

    first_start_minutes: Optional[int] = None
    last_end_minutes: Optional[int] = None
    total_visit_minutes: int = 0

    sanitized_items = []

    for idx, item in enumerate(items):
        item_label = f"Điểm dừng #{idx + 1}"
        osm_id = item.get("osm_id")
        name = item.get("name") or f"Điểm #{idx + 1}"

        # 2. Kiểm tra osm_id có trong danh sách POI ứng viên đầu vào (Chống ảo giác)
        if not osm_id:
            errors.append(f"{item_label}: Thiếu mã nhận diện 'osm_id'.")
            continue

        if osm_id not in candidate_lookup:
            errors.append(
                f"{item_label}: Phát hiện osm_id ảo giác '{osm_id}' ({name}) "
                f"không tồn tại trong danh sách POI ứng viên từ search_staging_pois."
            )
            continue

        # 3. Kiểm tra không trùng lặp địa điểm
        if osm_id in seen_osm_ids:
            errors.append(f"{item_label}: Trùng lặp địa điểm '{name}' (osm_id: {osm_id}) đã xuất hiện trước đó.")
            continue
        seen_osm_ids.add(osm_id)

        poi_source = candidate_lookup[osm_id]

        # 4. Kiểm tra tọa độ địa lý hợp lệ (thuộc phạm vi Việt Nam)
        coords = item.get("coordinates") or poi_source.get("coordinates") or {}
        lat = coords.get("latitude")
        lon = coords.get("longitude")

        if lat is None or lon is None:
            errors.append(f"{item_label} ('{name}'): Thiếu tọa độ vĩ độ hoặc kinh độ.")
        else:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                if not (VIETNAM_LAT_MIN <= lat_f <= VIETNAM_LAT_MAX and VIETNAM_LON_MIN <= lon_f <= VIETNAM_LON_MAX):
                    errors.append(
                        f"{item_label} ('{name}'): Tọa độ ({lat_f}, {lon_f}) nằm ngoài phạm vi lãnh thổ Việt Nam "
                        f"(Vĩ độ: [{VIETNAM_LAT_MIN}, {VIETNAM_LAT_MAX}], Kinh độ: [{VIETNAM_LON_MIN}, {VIETNAM_LON_MAX}])."
                    )
            except (ValueError, TypeError):
                errors.append(f"{item_label} ('{name}'): Giá trị tọa độ không phải kiểu số thực hợp lệ.")

        # 5. Kiểm tra định dạng thời gian và thời lượng hợp lệ
        start_str = item.get("start_time")
        end_str = item.get("end_time")

        if not start_str or not time_regex.match(str(start_str)):
            errors.append(f"{item_label} ('{name}'): Giờ bắt đầu '{start_str}' không đúng định dạng chuẩn HH:MM.")
            continue

        if not end_str or not time_regex.match(str(end_str)):
            errors.append(f"{item_label} ('{name}'): Giờ kết thúc '{end_str}' không đúng định dạng chuẩn HH:MM.")
            continue

        start_h, start_m = map(int, str(start_str).split(":"))
        end_h, end_m = map(int, str(end_str).split(":"))
        start_mins = start_h * 60 + start_m
        end_mins = end_h * 60 + end_m

        if end_mins <= start_mins:
            errors.append(
                f"{item_label} ('{name}'): Giờ kết thúc ({end_str}) phải lớn hơn giờ bắt đầu ({start_str})."
            )
            continue

        duration = end_mins - start_mins
        item_duration = item.get("estimated_duration")
        if item_duration is not None and int(item_duration) != duration:
            warnings.append(
                f"{item_label} ('{name}'): Thời lượng khai báo ({item_duration} phút) được đồng bộ "
                f"với khoảng cách giờ ({duration} phút)."
            )

        total_visit_minutes += duration

        if first_start_minutes is None:
            first_start_minutes = start_mins
        last_end_minutes = end_mins

        # 6. Kiểm tra lịch không chồng nhau (Thời gian bắt đầu >= Thời gian kết thúc điểm trước)
        if previous_end_minutes is not None:
            if start_mins < previous_end_minutes:
                prev_h, prev_m = divmod(previous_end_minutes, 60)
                errors.append(
                    f"{item_label} ('{name}'): Bị chồng chéo lịch trình! Giờ bắt đầu ({start_str}) "
                    f"sớm hơn giờ kết thúc của điểm dừng trước đó ({prev_h:02d}:{prev_m:02d})."
                )

        previous_end_minutes = end_mins

        # 7. Kiểm tra giờ mở cửa (Chỉ kiểm tra nếu dữ liệu thật có và đọc được; nếu null thì ghi cảnh báo)
        real_opening_hours = poi_source.get("opening_hours")
        if real_opening_hours is None or str(real_opening_hours).strip() == "" or str(real_opening_hours).lower() == "null":
            warnings.append(
                f"{item_label} ('{name}'): Chưa có dữ liệu giờ mở cửa thực tế trong OSM (opening_hours=null); "
                f"du khách cần kiểm tra giờ hoạt động trước khi ghé thăm."
            )
        else:
            # Nếu có chuỗi giờ mở cửa và có thể phân tích được
            parsed_oh = _parse_simple_opening_hours(str(real_opening_hours))
            if parsed_oh:
                oh_open, oh_close = parsed_oh
                if start_mins < oh_open or end_mins > oh_close:
                    errors.append(
                        f"{item_label} ('{name}'): Thời gian ghé thăm ({start_str} - {end_str}) "
                        f"nằm ngoài giờ mở cửa thực tế ({real_opening_hours})."
                    )
            else:
                warnings.append(
                    f"{item_label} ('{name}'): Giờ mở cửa '{real_opening_hours}' có định dạng phức tạp, "
                    f"cần xác nhận thủ công."
                )

        # Bảo toàn thông tin điểm dừng
        sanitized_item = dict(item)
        sanitized_item["order_index"] = idx + 1
        sanitized_item["estimated_duration"] = duration
        sanitized_items.append(sanitized_item)

    # 8. Kiểm tra tổng thời gian trong ngày không quá 12 giờ
    total_span_minutes = 0
    if first_start_minutes is not None and last_end_minutes is not None:
        total_span_minutes = last_end_minutes - first_start_minutes
        if total_span_minutes > MAX_DAILY_SPAN_MINUTES:
            first_h, first_m = divmod(first_start_minutes, 60)
            last_h, last_m = divmod(last_end_minutes, 60)
            errors.append(
                f"Tổng thời gian lịch trình trong ngày vượt quá giới hạn tối đa 12 giờ "
                f"(hiện tại: {total_span_minutes / 60.0:.1f} giờ, từ {first_h:02d}:{first_m:02d} đến {last_h:02d}:{last_m:02d})."
            )

    # 9. Tính toán hoặc sử dụng route_legs và kiểm tra khoảng nghỉ di chuyển giữa các điểm liên tiếp
    if route_legs is None:
        routing_res = calculate_route_legs(sanitized_items, execute_api=False)
        route_legs = routing_res.get("route_legs", [])
        warnings.extend(routing_res.get("warnings", []))

    # Kiểm tra khoảng nghỉ giữa các chặng liên tiếp:
    # "Chỉ dùng thời gian tuyến đường thật để kiểm tra khoảng nghỉ giữa hai điểm; nếu không có thì báo chưa xác định."
    for idx in range(len(sanitized_items) - 1):
        curr_i = sanitized_items[idx]
        next_i = sanitized_items[idx + 1]

        curr_end_h, curr_end_m = map(int, str(curr_i["end_time"]).split(":"))
        next_start_h, next_start_m = map(int, str(next_i["start_time"]).split(":"))
        buffer_minutes = (next_start_h * 60 + next_start_m) - (curr_end_h * 60 + curr_end_m)

        # Lấy thông tin chặng tương ứng
        leg = route_legs[idx] if idx < len(route_legs) else {}
        travel_time = leg.get("travel_time_minutes")

        curr_name = curr_i.get("name")
        next_name = next_i.get("name")

        if travel_time is None:
            # Chưa có thời gian tuyến đường thật -> Báo chưa xác định trong cảnh báo
            warnings.append(
                f"Chặng di chuyển #{idx + 1} ('{curr_name}' -> '{next_name}'): Khoảng nghỉ là {buffer_minutes} phút, "
                f"thời gian di chuyển thực tế chưa xác định (do chưa có kết nối định tuyến giao thông thời gian thực)."
            )
        else:
            # Đã có thời gian tuyến đường thật -> Kiểm tra khoảng nghỉ có đủ không
            if buffer_minutes < travel_time:
                errors.append(
                    f"Chặng di chuyển #{idx + 1} ('{curr_name}' -> '{next_name}'): Khoảng nghỉ giữa hai điểm ({buffer_minutes} phút) "
                    f"không đủ cho thời gian di chuyển thực tế ({travel_time} phút)."
                )

    is_valid = (len(errors) == 0)

    # Đóng gói đối tượng lịch trình nháp đã chuẩn hóa
    sanitized_draft = dict(draft_data)
    sanitized_draft["items"] = sanitized_items
    sanitized_draft["total_stops"] = len(sanitized_items)
    sanitized_draft["status"] = "validated_ready" if is_valid else "constraint_violated"

    valid_distances = [l.get("distance_km") for l in route_legs if l.get("distance_km") is not None]
    total_dist = round(sum(valid_distances), 2) if valid_distances else 0.0

    return {
        "valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "route_legs": route_legs,
        "draft": sanitized_draft,
        "summary": {
            "total_stops": len(sanitized_items),
            "total_span_hours": round(total_span_minutes / 60.0, 2) if total_span_minutes > 0 else 0.0,
            "total_visit_minutes": total_visit_minutes,
            "total_estimated_distance_km": total_dist,
            "distance_type": "ước tính"
        }
    }


def _parse_simple_opening_hours(hours_str: str) -> Optional[Tuple[int, int]]:
    """
    Phân tích đơn giản chuỗi giờ mở cửa dạng 'HH:MM-HH:MM' hoặc 'HH:MM - HH:MM'.
    Trả về (mốc_mở_cửa_phút, mốc_đóng_cửa_phút) hoặc None nếu không phân tích được.
    """
    match = re.search(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})", hours_str)
    if match:
        open_h, open_m, close_h, close_m = map(int, match.groups())
        return open_h * 60 + open_m, close_h * 60 + close_m
    return None


class ItineraryConstraintValidator:
    """
    Lớp đóng gói Validator Tool tương thích hệ thống Multi-Agent (LangChain / LangGraph).
    """
    name: str = "validate_itinerary_constraints"
    description: str = (
        "Kiểm định toàn bộ các ràng buộc logic cứng, thời gian, không gian, giờ mở cửa và chặng di chuyển "
        "của bản lịch trình nháp. Trả về kết quả gồm valid, errors, warnings, route_legs và draft."
    )

    def run(
        self,
        draft_plan: Dict[str, Any],
        candidate_pois: List[Dict[str, Any]],
        route_legs: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        return validate_itinerary_constraints(draft_plan, candidate_pois, route_legs=route_legs)

    def __call__(
        self,
        draft_plan: Dict[str, Any],
        candidate_pois: List[Dict[str, Any]],
        route_legs: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        return self.run(draft_plan, candidate_pois, route_legs=route_legs)


# Hỗ trợ chạy thử nghiệm nhanh qua dòng lệnh
if __name__ == "__main__":
    import json
    from src.services.ai.tools.poi_search_tool import search_staging_pois
    from src.services.ai.agents.planner_agent import plan_draft_itinerary

    # Lấy POI mẫu và tạo lịch nháp
    pois = search_staging_pois(latitude=21.04, longitude=105.84, radius_km=5.0, limit=5).get("data", [])
    req = {
        "selected_date": "2026-10-25",
        "title": "Chuyến đi mẫu kiểm định",
        "max_stops": 3
    }
    draft = plan_draft_itinerary(req, pois, execute_api=False).get("data", {})

    validation_result = validate_itinerary_constraints(draft, pois)
    print(json.dumps(validation_result, ensure_ascii=False, indent=2))
