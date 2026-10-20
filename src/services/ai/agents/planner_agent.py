"""
Module Itinerary Planner Agent (Giai đoạn E)
Tác tử lập lịch trình du lịch nháp dựa trên danh sách POI ứng viên từ PostgreSQL staging.

Tuân thủ nghiêm ngặt các nguyên tắc:
1. Nhận yêu cầu đã chuẩn hóa của người dùng cùng danh sách POI từ `search_staging_pois`; không tự tìm thêm địa điểm.
2. Dùng Gemini API thông qua một `GEMINI_API_KEY` đọc từ môi trường và model cấu hình qua `GEMINI_MODEL`.
3. Thêm dependency `google-genai` vào requirements.txt nhưng không gọi API trực tiếp và không in API key ra log/console.
4. Yêu cầu Gemini trả JSON có schema rõ ràng: ngày, các điểm theo thứ tự, osm_id, giờ bắt đầu, thời lượng và lý do chọn.
5. Kiểm tra bằng code (Deterministic Validator) rằng mọi osm_id đều có trong danh sách đầu vào, không trùng điểm,
   thời gian hợp lệ và không chồng lịch.
6. Nếu thiếu POI hoặc thiếu thông tin cần thiết, trả trạng thái cần bổ sung (needs_more_pois);
   tuyệt đối không bịa địa điểm, giờ mở cửa, giá vé hay thời gian di chuyển.
7. Chỉ trả lịch trình nháp (Draft). Không ghi database, không gọi create_itinerary_with_items vì osm_id chưa phải Attraction.id.
"""

import os
import re
import json
import uuid
import logging
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Any, Tuple, Union

logger = logging.getLogger(__name__)

# Cấu hình Gemini model & credential
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"

# Kiểm tra sự sẵn sàng của Google GenAI SDK chính thức (không bắt buộc cài đặt tại bước này)
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False


def get_gemini_config() -> Tuple[Optional[str], str]:
    """
    Đọc an toàn cấu hình Gemini từ biến môi trường (ưu tiên GEMINI_API_KEYS).
    Tuyệt đối không log hoặc in giá trị của API key.
    """
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    api_key = keys[0] if keys else os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
    return api_key, model_name


# =====================================================================
# 1. BỘ KIỂM ĐỊNH RÀNG BUỘC CODE THUẦN (DETERMINISTIC VALIDATOR)
# =====================================================================

def validate_draft_itinerary(
    plan_data: Dict[str, Any],
    candidate_pois: List[Dict[str, Any]],
    user_request: Dict[str, Any]
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Kiểm tra tính hợp lệ logic của lịch trình nháp bằng mã nguồn Python thuần:
    1. Kiểm tra đủ POI ứng viên đầu vào (không rỗng).
    2. Chống ảo giác (Anti-hallucination): Mọi osm_id trong lịch trình BẮT BUỘC phải nằm trong candidate_pois.
    3. Không trùng lặp địa điểm (Mỗi osm_id xuất hiện tối đa 1 lần).
    4. Định dạng thời gian hợp lệ (HH:MM), thời lượng dương (estimated_duration > 0).
    5. Không chồng chéo lịch trình (Thời gian bắt đầu điểm sau >= Thời gian kết thúc điểm trước).
    6. Tính trung thực dữ liệu: Không tự bịa giá vé, giờ mở cửa hay điểm đánh giá (giữ null).
    
    Returns:
        Tuple[bool, List[str], Dict[str, Any]]: (is_valid, error_messages, sanitized_plan)
    """
    errors: List[str] = []

    # 1. Kiểm tra danh sách POI ứng viên đầu vào
    if not candidate_pois or len(candidate_pois) == 0:
        return False, ["Danh sách POI ứng viên từ search_staging_pois bị trống. Cần bổ sung POI trước khi lập lịch."], {
            "status": "needs_more_pois",
            "message": "Không có điểm đến khả dụng trong khu vực tìm kiếm. Vui lòng mở rộng bán kính hoặc chọn danh mục khác."
        }

    # Tạo bảng tra cứu POI hợp lệ theo osm_id
    candidate_lookup = {p.get("osm_id"): p for p in candidate_pois if p.get("osm_id")}

    # 2. Kiểm tra ngày lịch trình
    selected_date_str = plan_data.get("selected_date") or user_request.get("selected_date")
    if not selected_date_str:
        errors.append("Thiếu thông tin ngày khởi hành (selected_date).")
    else:
        try:
            datetime.fromisoformat(str(selected_date_str).split("T")[0])
        except ValueError:
            errors.append(f"Định dạng ngày không hợp lệ: '{selected_date_str}'. Yêu cầu chuẩn YYYY-MM-DD.")

    items = plan_data.get("items", [])
    if not items or len(items) == 0:
        return False, ["Lịch trình không có điểm dừng nào được tạo."], {
            "status": "needs_more_pois",
            "message": "Không thể phân bổ điểm dừng nào vào lịch trình."
        }

    seen_osm_ids = set()
    time_regex = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
    previous_end_minutes: Optional[int] = None
    sanitized_items = []

    for idx, item in enumerate(items):
        item_prefix = f"Điểm dừng #{idx + 1}"
        osm_id = item.get("osm_id")

        # 3. Kiểm tra tính hợp lệ của osm_id (Chống ảo giác địa điểm)
        if not osm_id:
            errors.append(f"{item_prefix}: Thiếu mã nhận diện 'osm_id'.")
            continue

        if osm_id not in candidate_lookup:
            errors.append(
                f"{item_prefix}: Phát hiện osm_id ảo giác '{osm_id}' ({item.get('name')}) "
                f"không tồn tại trong danh sách POI ứng viên đầu vào."
            )
            continue

        # 4. Kiểm tra trùng lặp địa điểm
        if osm_id in seen_osm_ids:
            errors.append(f"{item_prefix}: Địa điểm '{item.get('name')}' (osm_id: {osm_id}) bị xếp trùng lặp nhiều lần.")
            continue
        seen_osm_ids.add(osm_id)

        # Lấy thông tin gốc từ candidate POI để bảo toàn tính xác thực
        original_poi = candidate_lookup[osm_id]

        # 5. Kiểm tra thời gian bắt đầu và kết thúc
        start_time_str = item.get("start_time")
        end_time_str = item.get("end_time")

        if not start_time_str or not time_regex.match(str(start_time_str)):
            errors.append(f"{item_prefix}: Giờ bắt đầu '{start_time_str}' không đúng định dạng HH:MM.")
            continue

        if not end_time_str or not time_regex.match(str(end_time_str)):
            errors.append(f"{item_prefix}: Giờ kết thúc '{end_time_str}' không đúng định dạng HH:MM.")
            continue

        # Đổi ra phút tính từ 00:00 để so sánh số học
        start_h, start_m = map(int, start_time_str.split(":"))
        end_h, end_m = map(int, end_time_str.split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m

        if end_minutes <= start_minutes:
            errors.append(
                f"{item_prefix}: Giờ kết thúc ({end_time_str}) phải lớn hơn giờ bắt đầu ({start_time_str})."
            )
            continue

        calc_duration = end_minutes - start_minutes
        duration = item.get("estimated_duration")
        if duration is None or int(duration) <= 0:
            duration = calc_duration
        elif int(duration) != calc_duration:
            # Tự động đồng bộ thời lượng với khoảng thời gian bắt đầu - kết thúc
            duration = calc_duration

        # 6. Kiểm tra chồng chéo lịch trình (Không chồng lịch)
        if previous_end_minutes is not None:
            if start_minutes < previous_end_minutes:
                prev_end_h, prev_end_m = divmod(previous_end_minutes, 60)
                errors.append(
                    f"{item_prefix}: Chồng chéo thời gian! Giờ bắt đầu ({start_time_str}) sớm hơn giờ kết thúc của điểm trước ({prev_end_h:02d}:{prev_end_m:02d})."
                )
                continue

        previous_end_minutes = end_minutes

        # Dữ liệu staging chuẩn hóa dùng coordinates lồng nhau; file pilot OSM
        # lại lưu latitude/longitude ở cấp ngoài. Hỗ trợ cả hai schema để không
        # làm mất tọa độ khi POI được truyền trực tiếp từ file pilot.
        poi_coordinates = original_poi.get("coordinates")
        if not isinstance(poi_coordinates, dict):
            poi_coordinates = {}
        poi_latitude = poi_coordinates.get("latitude")
        poi_longitude = poi_coordinates.get("longitude")
        if poi_latitude is None:
            poi_latitude = original_poi.get("latitude")
        if poi_longitude is None:
            poi_longitude = original_poi.get("longitude")

        # 7. Chuẩn hóa bản ghi điểm dừng (Giữ nguyên trung thực, không bịa giá/giờ mở cửa/rating)
        sanitized_item = {
            "order_index": idx + 1,
            "osm_id": osm_id,
            "name": original_poi.get("name") or item.get("name"),
            "category": original_poi.get("category") or item.get("category"),
            "fclass": original_poi.get("fclass"),
            "start_time": start_time_str,
            "end_time": end_time_str,
            "estimated_duration": duration,
            "time_slot": item.get("time_slot") or _infer_time_slot(start_minutes),
            "reason": item.get("reason") or f"Địa điểm {original_poi.get('category')} phù hợp lịch trình.",
            "notes": item.get("notes") or "",
            "coordinates": {
                "latitude": poi_latitude,
                "longitude": poi_longitude
            },
            "address": original_poi.get("address"),  # Giữ nguyên thông báo từ staging
            "price": None,                           # Giữ nguyên null (không bịa giá)
            "opening_hours": None,                   # Giữ nguyên null (không bịa giờ mở cửa)
            "rating": None                           # Giữ nguyên null (không bịa rating)
        }
        sanitized_items.append(sanitized_item)

    is_valid = len(errors) == 0
    sanitized_plan = {
        "draft_id": plan_data.get("draft_id") or f"draft_{uuid.uuid4().hex[:12]}",
        "status": "ready_for_review" if is_valid else "invalid_schedule",
        "selected_date": selected_date_str,
        "title": plan_data.get("title") or user_request.get("title") or f"Lịch trình tham quan ngày {selected_date_str}",
        "summary": plan_data.get("summary") or f"Lịch trình nháp gồm {len(sanitized_items)} điểm dừng theo sở thích du lịch.",
        "total_stops": len(sanitized_items),
        "items": sanitized_items,
        "created_at": plan_data.get("created_at") or datetime.utcnow().isoformat() + "Z"
    }

    return is_valid, errors, sanitized_plan


def _infer_time_slot(minutes_from_midnight: int) -> str:
    """Suy luận khung giờ (Sáng / Trưa / Chiều / Tối) theo mốc phút trong ngày."""
    if minutes_from_midnight < 11 * 60:
        return "morning"
    elif minutes_from_midnight < 14 * 60:
        return "lunch"
    elif minutes_from_midnight < 18 * 60:
        return "afternoon"
    else:
        return "evening"


# =====================================================================
# 2. XÂY DỰNG PROMPT & SCHEMA CHO GEMINI
# =====================================================================

def build_gemini_planner_prompt(
    user_request: Dict[str, Any],
    candidate_pois: List[Dict[str, Any]]
) -> str:
    """
    Tạo nội dung System & User Prompt cho Gemini với chỉ dẫn định dạng JSON nghiêm ngặt.
    Chỉ cho phép sử dụng các osm_id có trong candidate_pois.
    """
    # Lược trích danh sách POI để gửi cho Gemini (chỉ gửi thông tin cần thiết để tiết kiệm token)
    compact_pois = []
    for p in candidate_pois[:30]:  # Giới hạn tối đa 30 POI ứng viên tốt nhất
        compact_pois.append({
            "osm_id": p.get("osm_id"),
            "name": p.get("name"),
            "category": p.get("category"),
            "fclass": p.get("fclass"),
            "description": p.get("description"),
            "distance_km": p.get("distance_km")
        })

    prompt = f"""Bạn là một Chuyên gia Lập Lịch trình Du lịch Thông minh (Itinerary Planner Agent).
Nhiệm vụ của bạn là lập một bản LỊCH TRÌNH NHÁP (Draft Itinerary) hợp lý, khoa học và tối ưu cho khách du lịch.

=== YÊU CẦU CỦA NGƯỜI DÙNG ===
- Ngày đi: {user_request.get('selected_date', 'Chưa xác định')}
- Mục tiêu chuyến đi / Sở thích: {user_request.get('preferences') or user_request.get('query') or 'Khám phá văn hóa và ẩm thực'}
- Số điểm dừng mong muốn: {user_request.get('max_stops', 3)} điểm dừng trong ngày
- Khung giờ hoạt động: Từ 08:00 đến 21:00

=== DANH SÁCH ĐỊA ĐIỂM ỨNG VIÊN (CHỈ ĐƯỢC CHỌN TỪ DANH SÁCH NÀY) ===
{json.dumps(compact_pois, ensure_ascii=False, indent=2)}

=== CÁC QUY TẮC BẮT BUỘC (VI PHẠM LÀ LỖI NGHIÊM TRỌNG) ===
1. CHỈ CHỌN từ danh sách POI ở trên. TUYỆT ĐỐI KHÔNG tự bịa ra bất kỳ osm_id hay địa điểm mới nào ngoài danh sách.
2. Không xếp trùng lặp địa điểm (mỗi osm_id chỉ xuất hiện tối đa 1 lần).
3. Thời gian logic: Điểm dừng sáng bắt đầu khoảng 08:30 - 09:00, thời lượng ghé thăm từ 60 đến 120 phút.
4. KHÔNG CHỒNG LỊCH: Giờ bắt đầu của điểm dừng tiếp theo phải LỚN HƠN HOẶC BẰNG giờ kết thúc của điểm dừng trước đó (cộng thêm thời gian di chuyển dự kiến giữa các điểm).
5. TUYỆT ĐỐI KHÔNG bịa giá vé, giờ mở cửa cụ thể hay điểm đánh giá (các trường này hệ thống sẽ tự để null).
6. Trả về DUY NHẤT một đối tượng JSON hợp lệ (không kèm văn bản dẫn dắt ngoài JSON) theo đúng cấu trúc sau:

{{
  "selected_date": "{user_request.get('selected_date', '2026-10-25')}",
  "title": "Tên chuyến đi hấp dẫn ngắn gọn",
  "summary": "Tóm tắt ngắn gọn trải nghiệm chuyến đi 1-2 câu",
  "items": [
    {{
      "osm_id": "Mã osm_id chính xác trích từ danh sách trên",
      "name": "Tên địa điểm chính xác",
      "category": "Danh mục địa điểm",
      "start_time": "08:30",
      "end_time": "10:00",
      "estimated_duration": 90,
      "time_slot": "morning",
      "reason": "Lý do lựa chọn điểm đến này phù hợp với sở thích của người dùng",
      "notes": "Lưu ý trải nghiệm thực tế ngắn gọn"
    }}
  ]
}}
"""
    return prompt


# =====================================================================
# 3. ENGINE LẬP LỊCH THUẦN TOÁN HỌC (DETERMINISTIC FALLBACK ENGINE)
# =====================================================================

def _deterministic_planner_engine(
    user_request: Dict[str, Any],
    candidate_pois: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Bộ máy lập lịch thuật toán thuần Python (chạy khi chưa kích hoạt gọi Gemini API).
    Phân bổ khoa học các POI vào các khung giờ Sáng - Trưa - Chiều - Tối, đảm bảo 100% không chồng lịch,
    không ảo giác và không bịa đặt dữ liệu.
    """
    selected_date_str = str(user_request.get("selected_date") or date.today().isoformat())
    max_stops = max(1, min(int(user_request.get("max_stops", 3)), len(candidate_pois)))

    # Phân loại POI theo tính chất (ẩm thực vs tham quan/tín ngưỡng/thắng cảnh)
    dining_pois = []
    sightseeing_pois = []
    for p in candidate_pois:
        fclass = str(p.get("fclass") or "").lower()
        category = str(p.get("category") or "").lower()
        if any(k in fclass or k in category for k in ["restaurant", "cafe", "food", "ẩm thực", "nhà hàng", "cà phê"]):
            dining_pois.append(p)
        else:
            sightseeing_pois.append(p)

    # Chọn các điểm kết hợp hài hòa
    selected_stops: List[Dict[str, Any]] = []
    
    # Khung giờ mặc định cho các điểm dừng (Sáng, Trưa, Chiều, Tối)
    slot_schedules = [
        {"slot": "morning",   "start": "08:30", "end": "10:00", "duration": 90,  "pref": "dining_or_cafe"},
        {"slot": "sightseeing", "start": "10:30", "end": "12:00", "duration": 90,  "pref": "sightseeing"},
        {"slot": "lunch",     "start": "12:30", "end": "14:00", "duration": 90,  "pref": "dining"},
        {"slot": "afternoon", "start": "14:45", "end": "16:45", "duration": 120, "pref": "sightseeing"},
        {"slot": "evening",   "start": "18:00", "end": "19:30", "duration": 90,  "pref": "dining"}
    ]

    used_osm_ids = set()

    def pick_poi(pool, fallback_pool):
        for candidate in pool:
            if candidate.get("osm_id") not in used_osm_ids:
                used_osm_ids.add(candidate.get("osm_id"))
                return candidate
        for candidate in fallback_pool:
            if candidate.get("osm_id") not in used_osm_ids:
                used_osm_ids.add(candidate.get("osm_id"))
                return candidate
        return None

    for i in range(max_stops):
        schedule = slot_schedules[i % len(slot_schedules)]
        if schedule["pref"] == "dining" or schedule["pref"] == "dining_or_cafe":
            poi = pick_poi(dining_pois, candidate_pois)
        else:
            poi = pick_poi(sightseeing_pois, candidate_pois)

        if not poi:
            break

        selected_stops.append({
            "osm_id": poi.get("osm_id"),
            "name": poi.get("name"),
            "category": poi.get("category"),
            "start_time": schedule["start"],
            "end_time": schedule["end"],
            "estimated_duration": schedule["duration"],
            "time_slot": schedule["slot"],
            "reason": f"Điểm dừng {schedule['slot']} phù hợp nhóm ngành {poi.get('category')} theo sở thích người dùng.",
            "notes": "Địa điểm thực tế từ OpenStreetMap staging."
        })

    title = user_request.get("title") or f"Chuyến đi khám phá {len(selected_stops)} điểm dừng ngày {selected_date_str}"
    summary = (
        f"Lịch trình nháp 1 ngày được tối ưu hóa nhịp sinh học, bao gồm {len(selected_stops)} địa điểm "
        f"từ dữ liệu OpenStreetMap staging, không bị trùng lặp và không chồng chéo thời gian."
    )

    return {
        "draft_id": f"draft_{uuid.uuid4().hex[:12]}",
        "selected_date": selected_date_str,
        "title": title,
        "summary": summary,
        "items": selected_stops,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }


# =====================================================================
# 4. HÀM ĐIỀU PHỐI CHÍNH CỦA PLANNER AGENT
# =====================================================================

def plan_draft_itinerary(
    user_request: Dict[str, Any],
    candidate_pois: List[Dict[str, Any]],
    execute_api: bool = False
) -> Dict[str, Any]:
    """
    Sinh lịch trình du lịch nháp từ yêu cầu người dùng và danh sách POI staging.
    
    Args:
        user_request: Thông tin yêu cầu đã chuẩn hóa (selected_date, preferences, max_stops...).
        candidate_pois: Danh sách địa điểm lấy từ `search_staging_pois` (không tự tìm thêm).
        execute_api: Cờ kích hoạt gọi API Gemini (mặc định False: không gọi API để tuân thủ quy tắc).
        
    Returns:
        Dict[str, Any]: Kết quả lịch trình nháp kèm trạng thái kiểm định logic.
    """
    # BƯỚC 1: KIỂM TRA ĐẦU VÀO ĐẦY ĐỦ
    if not candidate_pois or len(candidate_pois) == 0:
        return {
            "success": False,
            "status": "needs_more_pois",
            "message": "Không có POI ứng viên nào được cung cấp. Vui lòng mở rộng phạm vi tìm kiếm hoặc chọn thêm danh mục.",
            "data": None
        }

    # BƯỚC 2: TẠO LỊCH TRÌNH NHÁP (QUA GEMINI HOẶC FALLBACK ENGINE)
    api_key, model_name = get_gemini_config()
    raw_plan: Optional[Dict[str, Any]] = None

    if execute_api and api_key and HAS_GENAI_SDK:
        # Nhánh gọi Gemini API khi được cấp phép rõ ràng và SDK đã sẵn sàng
        try:
            prompt = build_gemini_planner_prompt(user_request, candidate_pois)
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            raw_plan = json.loads(response.text)
        except Exception as e:
            logger.warning(f"Lỗi khi gọi Gemini API ({model_name}): {str(e)}; tự động chuyển sang Fallback Planner.")
            raw_plan = _deterministic_planner_engine(user_request, candidate_pois)
    else:
        # Mặc định theo yêu cầu: KHÔNG GỌI API, sử dụng deterministic scheduling engine
        raw_plan = _deterministic_planner_engine(user_request, candidate_pois)

    # BƯỚC 3: KIỂM ĐỊNH RÀNG BUỘC CHẶT CHẼ BẰNG CODE
    is_valid, errors, sanitized_plan = validate_draft_itinerary(raw_plan, candidate_pois, user_request)

    # BƯỚC 4: TRẢ KẾT QUẢ BẢN NHÁP (TUYỆT ĐỐI KHÔNG LƯU VÀO DATABASE)
    return {
        "success": is_valid,
        "status": sanitized_plan["status"],
        "draft_id": sanitized_plan["draft_id"],
        "model_used": model_name if execute_api else "deterministic_planner_v1",
        "validation": {
            "is_valid": is_valid,
            "error_count": len(errors),
            "errors": errors
        },
        "data": sanitized_plan
    }


# =====================================================================
# 5. LỚP ĐÓNG GÓI PLANNER AGENT (MULTI-AGENT INTERFACE)
# =====================================================================

class ItineraryPlannerAgent:
    """
    Lớp đóng gói Tác tử Lập Lịch Trình (Itinerary Planner Agent) tương thích Multi-Agent Orchestrator.
    """
    name: str = "itinerary_planner_agent"
    description: str = (
        "Tác tử chuyên trách quy hoạch và phân bổ các địa điểm du lịch vào từng khung giờ trong ngày, "
        "đảm bảo không bị ảo giác địa điểm, không trùng lặp và không chồng chéo thời gian."
    )

    def __init__(self, model_name: Optional[str] = None):
        self.api_key, default_model = get_gemini_config()
        self.model_name = model_name or default_model

    def plan(
        self,
        user_request: Dict[str, Any],
        candidate_pois: List[Dict[str, Any]],
        execute_api: bool = False
    ) -> Dict[str, Any]:
        return plan_draft_itinerary(user_request, candidate_pois, execute_api=execute_api)

    def validate(
        self,
        plan_data: Dict[str, Any],
        candidate_pois: List[Dict[str, Any]],
        user_request: Dict[str, Any]
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        return validate_draft_itinerary(plan_data, candidate_pois, user_request)

    def run(self, **kwargs) -> Dict[str, Any]:
        user_request = kwargs.get("user_request", {})
        candidate_pois = kwargs.get("candidate_pois", [])
        execute_api = kwargs.get("execute_api", False)
        return self.plan(user_request, candidate_pois, execute_api=execute_api)

    def __call__(self, **kwargs) -> Dict[str, Any]:
        return self.run(**kwargs)


# Hỗ trợ chạy thử nghiệm nhanh qua dòng lệnh
if __name__ == "__main__":
    from src.services.ai.tools.poi_search_tool import search_staging_pois

    # Lấy mẫu POI từ staging
    search_res = search_staging_pois(latitude=21.04, longitude=105.84, radius_km=5.0, limit=6)
    pois = search_res.get("data", [])

    sample_request = {
        "selected_date": "2026-10-25",
        "title": "Chuyến dạo quanh Hồ Tây & Phố Cổ",
        "preferences": "Thích ăn uống nhẹ nhàng và ghé thăm chùa cổ",
        "max_stops": 3
    }

    draft_result = plan_draft_itinerary(sample_request, pois, execute_api=False)
    print(json.dumps(draft_result, ensure_ascii=False, indent=2))
