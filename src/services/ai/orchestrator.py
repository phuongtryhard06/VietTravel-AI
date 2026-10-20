"""
Module Draft Itinerary Orchestrator (Giai đoạn G)
Điều phối toàn diện luồng sinh lịch trình nháp Đa tác tử (Multi-Agent System).

Tuân thủ nghiêm ngặt các nguyên tắc:
1. Sử dụng đúng chữ ký hàm thật của:
   - search_staging_pois trong poi_search_tool.py
   - plan_draft_itinerary / ItineraryPlannerAgent trong planner_agent.py
   - validate_itinerary_constraints trong validator_tool.py
2. Không sử dụng extract_user_intent_and_features (luồng OpenAI cũ).
3. Validator đã tự động gọi routing_tool bên trong -> Orchestrator không gọi tính tuyến lần 2.
4. Luồng điều phối:
   - Bước 1: Tiếp nhận yêu cầu chuẩn hóa (selected_date, khu vực/tọa độ, sở thích, ngân sách).
   - Bước 2: Tìm kiếm POI từ staging (hoặc nhận candidate_pois truyền vào).
   - Bước 3: Gửi POI ứng viên cho Planner tạo lịch nháp.
   - Bước 4: Gửi lịch nháp và cùng danh sách ứng viên cho Validator.
   - Bước 5: Nếu có lỗi cứng, gửi lỗi phản hồi về Planner để lập lại tối đa 2 lần (max_retries=2).
             Cảnh báo đơn thuần (warnings) không tạo lại lịch.
   - Bước 6: Trả kết quả gồm trạng thái, lịch nháp, kết quả kiểm định, số lần thử và cảnh báo.
             Nếu thiếu dữ liệu hoặc không tìm thấy POI, trả lỗi rõ ràng; tuyệt đối không tự bịa địa điểm.
5. Mặc định không gọi API bên ngoài (execute_api=False), không đọc hoặc in API key.
6. Đây chỉ là bản lịch trình nháp: không ghi PostgreSQL/ChromaDB, không chuyển osm_id thành
   Attraction.id, không gọi create_itinerary_with_items, không sửa controller/API/schema.
"""

import logging
import math
import re
from typing import Dict, List, Optional, Any
from datetime import datetime, date

from src.services.ai.tools.poi_search_tool import search_staging_pois
from src.services.ai.agents.planner_agent import plan_draft_itinerary, ItineraryPlannerAgent
from src.services.ai.tools.validator_tool import validate_itinerary_constraints, ItineraryConstraintValidator

logger = logging.getLogger(__name__)

# Số lần thử lập lại lịch tối đa khi gặp lỗi cứng
DEFAULT_MAX_RETRIES = 2


def orchestrate_draft_itinerary(
    user_request: Dict[str, Any],
    candidate_pois: Optional[List[Dict[str, Any]]] = None,
    execute_api: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES
) -> Dict[str, Any]:
    """
    Điều phối toàn trình quy trình lập lịch trình nháp và kiểm định ràng buộc.
    
    Args:
        user_request: Từ điển yêu cầu đã chuẩn hóa gồm:
            - selected_date (str): Ngày khởi hành (YYYY-MM-DD).
            - query / destination / area (str, optional): Khu vực hoặc từ khóa tìm kiếm.
            - category (str, optional): Danh mục POI mong muốn.
            - latitude, longitude (float, optional): Tọa độ trung tâm.
            - radius_km (float, optional): Bán kính tìm kiếm quanh tâm (km).
            - preferences (str, optional): Sở thích du lịch.
            - budget (str/int/float, optional): Ngân sách dự kiến nếu có.
            - max_stops (int, optional): Số điểm dừng mong muốn trong ngày (mặc định 3).
        candidate_pois: Danh sách POI ứng viên được truyền sẵn (nếu None sẽ tự tìm từ staging).
        execute_api: Cờ cho phép gọi API ngoài (mặc định False: không gọi API ngoài khi đang triển khai).
        max_retries: Số lần yêu cầu Planner lập lại tối đa nếu Validator phát hiện lỗi cứng (mặc định 2).
        
    Returns:
        Dict[str, Any]: Kết quả điều phối chuẩn gồm:
            - success (bool)
            - status (str): "success" | "validation_failed" | "no_pois_found" | "invalid_request"
            - message (str)
            - draft (Dict, optional)
            - validation (Dict, optional)
            - route_legs (List[Dict])
            - iterations (int)
            - max_retries (int)
            - warnings (List[str])
            - candidate_pois_count (int)
            - history (List[Dict])
    """
    history: List[Dict[str, Any]] = []
    accumulated_warnings: List[str] = []

    # =====================================================================
    # BƯỚC 1: TIẾP NHẬN & KIỂM TRA ĐẦU VÀO YÊU CẦU
    # =====================================================================
    if not isinstance(user_request, dict):
        return {
            "success": False,
            "status": "invalid_request",
            "message": "Yêu cầu người dùng (user_request) phải là đối tượng từ điển hợp lệ.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": ["Đầu vào không hợp lệ."],
            "candidate_pois_count": 0,
            "history": []
        }

    # Chuẩn hóa ngày khởi hành
    selected_date_raw = user_request.get("selected_date")
    if not selected_date_raw:
        selected_date_str = date.today().isoformat()
        accumulated_warnings.append(
            f"Thiếu ngày khởi hành (selected_date); hệ thống tự động gán ngày mặc định là hôm nay: {selected_date_str}."
        )
    else:
        try:
            # Kiểm tra ngày lịch thực, không chỉ kiểm tra hình thức chuỗi.
            selected_date_str = str(selected_date_raw).split("T")[0]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", selected_date_str):
                raise ValueError("date must use YYYY-MM-DD")
            parsed_date = date.fromisoformat(selected_date_str)
        except (ValueError, TypeError):
            return {
                "success": False,
                "status": "invalid_request",
                "message": f"Định dạng ngày '{selected_date_raw}' không hợp lệ. Yêu cầu chuẩn YYYY-MM-DD.",
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": ["Định dạng ngày không hợp lệ."],
                "candidate_pois_count": 0,
                "history": []
            }
        if parsed_date < date.today():
            return {
                "success": False,
                "status": "invalid_request",
                "message": "Ngày khởi hành không được ở trong quá khứ.",
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": [],
                "candidate_pois_count": 0,
                "history": []
            }

    normalized_request = dict(user_request)
    normalized_request["selected_date"] = selected_date_str

    # Xác thực số liệu truy vấn trước khi gọi lớp database để đầu vào sai
    # luôn được trả về là invalid_request thay vì database_error/HTTP 500.
    raw_lat = normalized_request.get("latitude")
    raw_lon = normalized_request.get("longitude")
    if (raw_lat is None) != (raw_lon is None):
        return {
            "success": False,
            "status": "invalid_request",
            "message": "Cần nhập đồng thời vĩ độ và kinh độ, hoặc để trống cả hai.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": [],
            "candidate_pois_count": 0,
            "history": []
        }

    lat_f = lon_f = None
    if raw_lat is not None:
        try:
            lat_f, lon_f = float(raw_lat), float(raw_lon)
        except (TypeError, ValueError):
            lat_f = lon_f = None
        if (
            lat_f is None or lon_f is None or
            not all(math.isfinite(value) for value in (lat_f, lon_f)) or
            not 8.0 <= lat_f <= 24.0 or not 102.0 <= lon_f <= 110.0
        ):
            return {
                "success": False,
                "status": "invalid_request",
                "message": "Tọa độ không hợp lệ hoặc nằm ngoài phạm vi Việt Nam.",
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": [],
                "candidate_pois_count": 0,
                "history": []
            }
        normalized_request["latitude"] = lat_f
        normalized_request["longitude"] = lon_f

    try:
        radius_f = float(normalized_request["radius_km"]) if normalized_request.get("radius_km") is not None else None
    except (TypeError, ValueError):
        radius_f = None
        if normalized_request.get("radius_km") is not None:
            return {
                "success": False,
                "status": "invalid_request",
                "message": "Bán kính tìm kiếm phải là một số lớn hơn 0 và không quá 100 km.",
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": [],
                "candidate_pois_count": 0,
                "history": []
            }
    if radius_f is not None and (radius_f <= 0 or radius_f > 100 or not math.isfinite(radius_f)):
        return {
            "success": False,
            "status": "invalid_request",
            "message": "Bán kính tìm kiếm phải lớn hơn 0 và không quá 100 km.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": [],
            "candidate_pois_count": 0,
            "history": []
        }
    if radius_f is not None and lat_f is None:
        return {
            "success": False,
            "status": "invalid_request",
            "message": "Chỉ có thể dùng bán kính tìm kiếm khi đã nhập cả vĩ độ và kinh độ.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": [],
            "candidate_pois_count": 0,
            "history": []
        }

    try:
        max_stops = int(normalized_request.get("max_stops", 3))
        limit = int(normalized_request.get("limit", 20))
    except (TypeError, ValueError):
        max_stops = limit = 0
    if not 1 <= max_stops <= 10:
        return {
            "success": False,
            "status": "invalid_request",
            "message": "Số điểm dừng phải nằm trong khoảng 1 đến 10.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": [],
            "candidate_pois_count": 0,
            "history": []
        }
    if not 1 <= limit <= 100:
        return {
            "success": False,
            "status": "invalid_request",
            "message": "Giới hạn kết quả tìm kiếm phải nằm trong khoảng 1 đến 100.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": [],
            "candidate_pois_count": 0,
            "history": []
        }
    normalized_request["max_stops"] = max_stops
    normalized_request["limit"] = limit

    # =====================================================================
    # BƯỚC 2: TÌM KIẾM POI TỪ STAGING (HOẶC DÙNG CANDIDATE ĐƯỢC TRUYỀN)
    # =====================================================================
    pois: List[Dict[str, Any]] = []
    if candidate_pois is not None and len(candidate_pois) > 0:
        pois = list(candidate_pois)
        logger.info(f"Sử dụng {len(pois)} POI ứng viên được truyền trực tiếp vào Orchestrator.")
    else:
        # Tự động tìm kiếm POI từ PostgreSQL staging thông qua search_staging_pois
        if lat_f is not None and lon_f is not None:
            # Khi đã có tọa độ không gian chính xác, query chỉ lọc theo từ khóa POI cụ thể (nếu có);
            # Không lấy nhãn địa danh/khu vực (area/destination) làm chuỗi tìm kiếm tên POI.
            query = normalized_request.get("query")
        else:
            query = (
                normalized_request.get("query") or
                normalized_request.get("destination") or
                normalized_request.get("area") or
                normalized_request.get("location")
            )
        category = normalized_request.get("category")
        radius_f = radius_f if radius_f is not None else (5.0 if (lat_f is not None and lon_f is not None) else None)

        try:
            search_result = search_staging_pois(
                query=str(query) if query else None,
                category=str(category) if category else None,
                latitude=lat_f,
                longitude=lon_f,
                radius_km=radius_f,
                limit=limit,
                include_chromadb=False  # Không dùng ChromaDB trong bước này theo quy ước
            )
        except Exception as e:
            logger.exception("Lỗi khi truy vấn POI từ PostgreSQL staging: %s", e)
            return {
                "success": False,
                "status": "database_error",
                "message": "Không thể đọc dữ liệu địa điểm. Hãy kiểm tra PostgreSQL và bảng osm_attractions_staging.",
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": ["Cơ sở dữ liệu địa điểm không khả dụng."],
                "candidate_pois_count": 0,
                "history": []
            }

        if search_result.get("error_code") == "database_error":
            message = search_result.get("message") or "Cơ sở dữ liệu địa điểm hiện không khả dụng."
            return {
                "success": False,
                "status": "database_error",
                "message": message,
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": [message],
                "candidate_pois_count": 0,
                "history": []
            }

        if not search_result.get("success") or not search_result.get("data"):
            msg = (
                search_result.get("message") or
                "Không tìm thấy địa điểm nào phù hợp trong khu vực yêu cầu từ OpenStreetMap staging."
            )
            return {
                "success": False,
                "status": "no_pois_found",
                "message": msg,
                "draft": None,
                "validation": None,
                "route_legs": [],
                "iterations": 0,
                "max_retries": max_retries,
                "warnings": [msg],
                "candidate_pois_count": 0,
                "history": []
            }

        pois = search_result.get("data", [])

    if not pois or len(pois) == 0:
        return {
            "success": False,
            "status": "no_pois_found",
            "message": "Không có điểm đến khả dụng từ OpenStreetMap staging. Tuyệt đối không tự bịa địa điểm.",
            "draft": None,
            "validation": None,
            "route_legs": [],
            "iterations": 0,
            "max_retries": max_retries,
            "warnings": ["Danh sách POI trống."],
            "candidate_pois_count": 0,
            "history": []
        }

    # =====================================================================
    # BƯỚC 3, 4, 5: VÒNG LẶP ĐIỀU PHỐI (PLANNER <-> VALIDATOR)
    # =====================================================================
    attempt = 0
    max_attempts = 1 + max(0, int(max_retries))
    current_request = dict(normalized_request)
    last_validation_result: Optional[Dict[str, Any]] = None
    last_draft: Optional[Dict[str, Any]] = None

    while attempt < max_attempts:
        attempt += 1
        iteration_label = f"Lượt thử #{attempt}/{max_attempts}"
        logger.info(f"Bắt đầu điều phối {iteration_label}...")

        # 3. Gửi POI ứng viên cho Planner tạo lịch nháp
        planner_response = plan_draft_itinerary(
            user_request=current_request,
            candidate_pois=pois,
            execute_api=execute_api
        )

        if not planner_response.get("success") and planner_response.get("status") == "needs_more_pois":
            # Thiếu POI nghiêm trọng, không thể tiếp tục lập lịch
            return {
                "success": False,
                "status": "needs_more_pois",
                "message": planner_response.get("message", "Thiếu POI để đáp ứng lịch trình."),
                "draft": None,
                "validation": planner_response.get("validation"),
                "route_legs": [],
                "iterations": attempt,
                "max_retries": max_retries,
                "warnings": accumulated_warnings,
                "candidate_pois_count": len(pois),
                "history": history
            }

        draft_plan = planner_response.get("data") or planner_response

        # 4. Gửi lịch nháp và danh sách POI cho Validator
        # Lưu ý: validator_tool đã tự động gọi routing_tool bên trong -> route_legs=None để không gọi 2 lần
        validation_result = validate_itinerary_constraints(
            draft_plan=draft_plan,
            candidate_pois=pois,
            route_legs=None
        )

        last_validation_result = validation_result
        last_draft = validation_result.get("draft")
        current_errors = validation_result.get("errors") or []
        current_warnings = validation_result.get("warnings") or []

        # Tích lũy cảnh báo
        for w in current_warnings:
            if w not in accumulated_warnings:
                accumulated_warnings.append(w)

        # Ghi nhận lịch sử lần thử
        history.append({
            "iteration": attempt,
            "valid": validation_result.get("valid", False),
            "errors": current_errors,
            "warning_count": len(current_warnings)
        })

        # 5. Phân tích kết quả kiểm định:
        if validation_result.get("valid") is True:
            # Thành công: không có lỗi cứng nào! Cảnh báo đơn thuần không tạo lại lịch.
            logger.info(f"{iteration_label}: Lịch trình hợp lệ, vượt qua toàn bộ 8 quy tắc kiểm định!")
            break
        else:
            # Có lỗi cứng: Nếu còn lượt retry, gửi thông tin lỗi phản hồi về cho Planner
            logger.warning(
                f"{iteration_label}: Phát hiện {len(current_errors)} lỗi cứng: {current_errors}. "
                f"Đang chuẩn bị lập lại lịch..."
            )

            if attempt < max_attempts:
                # Bổ sung thông tin lỗi vào yêu cầu để Planner tự điều chỉnh
                feedback_str = "; ".join(current_errors)
                current_request["validation_feedback"] = current_errors
                current_request["retry_count"] = attempt
                
                # Bổ sung rõ vào preferences để cả Gemini LLM hoặc thuật toán đều nhận biết
                orig_pref = normalized_request.get("preferences") or ""
                adjustment_notice = (
                    f" [ĐIỀU CHỈNH LƯỢT {attempt + 1}]: Lần lập trước vi phạm các ràng buộc: {feedback_str}. "
                    f"Vui lòng chọn POI khác trong danh sách hoặc điều chỉnh lại khung giờ không bị chồng chéo."
                )
                current_request["preferences"] = (orig_pref + adjustment_notice).strip()
            else:
                logger.error(f"Đã sử dụng hết {max_retries} lần lập lại nhưng vẫn còn lỗi cứng.")

    # =====================================================================
    # BƯỚC 6: TRẢ KẾT QUẢ ĐIỀU PHỐI CHUẨN HÓA
    # =====================================================================
    is_valid_final = (last_validation_result is not None and last_validation_result.get("valid") is True)
    route_legs = last_validation_result.get("route_legs", []) if last_validation_result else []

    status_code = "success" if is_valid_final else "validation_failed"
    message = (
        "Lập lịch trình nháp và kiểm định ràng buộc thành công."
        if is_valid_final else
        f"Lịch trình nháp chưa thỏa mãn các ràng buộc sau {attempt} lần thử. Cần điều chỉnh tiêu chí tìm kiếm hoặc khung giờ."
    )

    return {
        "success": is_valid_final,
        "status": status_code,
        "message": message,
        "draft": last_draft,
        "validation": last_validation_result,
        "route_legs": route_legs,
        "iterations": attempt,
        "max_retries": max_retries,
        "warnings": accumulated_warnings,
        "candidate_pois_count": len(pois),
        "history": history
    }


# =====================================================================
# LỚP ĐÓNG GÓI DRAFT ITINERARY ORCHESTRATOR
# =====================================================================

class DraftItineraryOrchestrator:
    """
    Lớp đóng gói Bộ điều phối Lịch trình nháp tương thích hệ thống Đa tác tử (LangChain / LangGraph).
    """
    name: str = "draft_itinerary_orchestrator"
    description: str = (
        "Bộ điều phối trung tâm: tiếp nhận yêu cầu người dùng, tìm kiếm POI từ OpenStreetMap staging, "
        "gửi cho Planner Agent lập lịch nháp, thực thi Validator kiểm tra ràng buộc logic/không gian/thời gian, "
        "và tự động yêu cầu Planner sửa đổi tối đa 2 lần nếu phát sinh lỗi cứng."
    )

    def __init__(self, execute_api: bool = False, max_retries: int = DEFAULT_MAX_RETRIES):
        self.execute_api = execute_api
        self.max_retries = max_retries

    def orchestrate(
        self,
        user_request: Dict[str, Any],
        candidate_pois: Optional[List[Dict[str, Any]]] = None,
        execute_api: Optional[bool] = None
    ) -> Dict[str, Any]:
        api_flag = self.execute_api if execute_api is None else execute_api
        return orchestrate_draft_itinerary(
            user_request=user_request,
            candidate_pois=candidate_pois,
            execute_api=api_flag,
            max_retries=self.max_retries
        )

    def run(self, **kwargs) -> Dict[str, Any]:
        user_request = kwargs.get("user_request", {})
        candidate_pois = kwargs.get("candidate_pois")
        execute_api = kwargs.get("execute_api", self.execute_api)
        return self.orchestrate(user_request=user_request, candidate_pois=candidate_pois, execute_api=execute_api)

    def __call__(self, **kwargs) -> Dict[str, Any]:
        return self.run(**kwargs)


# Hỗ trợ chạy thử nghiệm nhanh qua dòng lệnh
if __name__ == "__main__":
    import json

    test_request = {
        "selected_date": "2026-10-25",
        "query": "Cà phê",
        "latitude": 21.04,
        "longitude": 105.84,
        "radius_km": 5.0,
        "preferences": "Thích quán cà phê không gian yên tĩnh và trải nghiệm ẩm thực",
        "max_stops": 3
    }

    result = orchestrate_draft_itinerary(test_request, execute_api=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
