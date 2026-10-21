"""
VietTravel AI - Intent & Context Agent (Phase 2 & Phase 3)
Dedicated agent responsible for natural-language intent recognition, entity extraction,
and selective, non-destructive constraint updates within the TravelSessionState.

Principles:
1. Pure analysis: does NOT execute search tools, POI database queries, or routing.
2. Selective updates: updates ONLY fields explicitly modified or inferred without clobbering existing constraints.
3. Provenance tracking: distinguishes user_provided, model_inferred, and unknown.
4. Schema-guarded validation: rejects invalid constraints and poses clarification questions when input is ambiguous or conflicting.
5. Deterministic fallback: provides robust local regex and rule-based parsing when LLM is unavailable or outputs malformed schema.
"""

import json
import re
import time
import logging
from typing import Dict, List, Optional, Any, Set, Tuple
from pydantic import BaseModel, Field, ConfigDict

from src.services.ai.state import (
    TravelSessionState,
    TripConstraints,
    AgentTaskTrace
)

logger = logging.getLogger(__name__)

# Standard travel intent categories
INTENT_SEARCH_PLACES = "search_places"
INTENT_GENERAL_TRAVEL_INFO = "general_travel_info"
INTENT_CREATE_ITINERARY = "create_itinerary"
INTENT_MODIFY_ITINERARY = "modify_itinerary"
INTENT_CHANGE_BUDGET = "change_budget"
INTENT_CHANGE_GROUP_SIZE = "change_group_size"
INTENT_CHANGE_DATES = "change_dates"
INTENT_CHANGE_DESTINATION = "change_destination"
INTENT_REMOVE_STOP = "remove_stop"
INTENT_OUT_OF_SCOPE = "out_of_scope"
INTENT_CLARIFICATION_NEEDED = "clarification_needed"


class IntentAgentResult(BaseModel):
    """
    Structured outcome returned by the Intent & Context Agent.
    """
    model_config = ConfigDict(extra="ignore")

    intent: str = Field(..., description="Classified user objective")
    confidence: float = Field(default=0.85, ge=0.0, le=1.0, description="Confidence score")
    extracted_entities: Dict[str, Any] = Field(default_factory=dict, description="Extracted parameters")
    modified_constraint_fields: List[str] = Field(default_factory=list, description="Fields changed in state.constraints")
    missing_critical_fields: List[str] = Field(default_factory=list, description="Essential information still missing")
    clarification_questions: List[str] = Field(default_factory=list, description="Questions for user when ambiguous")
    target_stop_to_remove: Optional[Any] = Field(default=None, description="Identifier or index of stop to delete")
    is_out_of_scope: bool = Field(default=False, description="True if query is unrelated to travel")
    is_modification: bool = Field(default=False, description="True if user is modifying an existing plan")
    explanation: str = Field(default="", description="Reasoning summary")
    status: str = Field(default="success", description="'success' | 'clarification_needed' | 'out_of_scope' | 'error'")


KNOWN_DESTINATIONS = [
    "hà nội", "ha noi", "hanoi", "sài gòn", "sai gon", "tp.hcm", "tphcm", "hồ chí minh", "ho chi minh",
    "đà nẵng", "da nang", "hội an", "hoi an", "huế", "hue", "đà lạt", "da lat", "nha trang", "phú quốc",
    "phu quoc", "cát bà", "cat ba", "vũng tàu", "vung tau", "hạ long", "ha long", "sa pa", "sapa",
    "ninh bình", "ninh binh", "quy nhơn", "quy nhon", "côn đảo", "con dao", "mộc châu", "moc chau",
    "hà giang", "ha giang", "cao bằng", "cao bang", "ba bể", "ba be", "thái nguyên", "thai nguyen",
    "cần thơ", "can tho", "an giang", "bến tre", "mũi né", "mui ne", "phan thiết", "phan thiet"
]


class IntentAndContextAgent:
    """
    Intent and Context Analysis Agent for multi-agent travel coordination.
    """
    def __init__(self):
        pass

    def process(
        self,
        user_message: str,
        session_state: TravelSessionState,
        execute_llm: bool = True
    ) -> IntentAgentResult:
        """
        Analyzes user input against current session state, updates constraints selectively,
        and returns structured intent analysis.
        """
        start_time = time.perf_counter()
        clean_msg = (user_message or "").strip()

        if not clean_msg:
            return IntentAgentResult(
                intent=INTENT_CLARIFICATION_NEEDED,
                confidence=1.0,
                clarification_questions=["Bạn có thể chia sẻ kế hoạch hoặc câu hỏi du lịch của mình được không?"],
                status="clarification_needed",
                explanation="Tin nhắn người dùng bị trống."
            )

        # 1. Check for obvious out of scope queries early
        if self._is_obviously_out_of_scope(clean_msg):
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            trace = AgentTaskTrace(
                agent_name="intent_agent",
                status="completed",
                input_summary=clean_msg[:120],
                output_summary="Classified as out_of_scope",
                execution_time_ms=round(duration_ms, 2)
            )
            session_state.add_trace(trace)
            return IntentAgentResult(
                intent=INTENT_OUT_OF_SCOPE,
                confidence=0.98,
                is_out_of_scope=True,
                status="out_of_scope",
                explanation="Yêu cầu nằm ngoài phạm vi tư vấn và lập kế hoạch du lịch.",
                clarification_questions=["Em là trợ lý chuyên về du lịch Việt Nam. Anh/chị có câu hỏi nào về điểm đến, khách sạn hay lịch trình không ạ?"]
            )

        # 2. Try LLM extraction if enabled
        analysis: Optional[Dict[str, Any]] = None
        if execute_llm:
            analysis = self._classify_with_llm(clean_msg, session_state)

        # 3. Fallback to deterministic rules if LLM not used, unavailable, or failed
        if not analysis:
            analysis = self._deterministic_intent_analysis(clean_msg, session_state)

        # 4. Apply updates selectively to session_state with schema validation
        modified_fields, validation_errors = self._apply_updates_to_state(session_state, analysis)

        # 5. Check if clarification or conflicts exist
        clarification_questions = list(analysis.get("clarification_questions", []))
        if validation_errors:
            clarification_questions.extend(validation_errors)

        intent = analysis.get("intent", INTENT_SEARCH_PLACES)
        status = "success"
        if intent == INTENT_OUT_OF_SCOPE:
            status = "out_of_scope"
        elif intent == INTENT_CLARIFICATION_NEEDED or clarification_questions:
            status = "clarification_needed"

        # Check missing critical fields for itinerary creation
        missing_critical = []
        if intent in (INTENT_CREATE_ITINERARY, INTENT_MODIFY_ITINERARY):
            if not session_state.constraints.destination:
                missing_critical.append("destination")
            if not session_state.constraints.duration_days and not session_state.constraints.start_date:
                missing_critical.append("duration_or_dates")

        is_modification = intent in (
            INTENT_MODIFY_ITINERARY,
            INTENT_CHANGE_BUDGET,
            INTENT_CHANGE_GROUP_SIZE,
            INTENT_CHANGE_DATES,
            INTENT_CHANGE_DESTINATION,
            INTENT_REMOVE_STOP
        )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Record trace in session state
        trace = AgentTaskTrace(
            agent_name="intent_agent",
            status="completed",
            input_summary=clean_msg[:120],
            output_summary=f"Intent: {intent}, modified: {modified_fields}",
            tools_called=[],
            execution_time_ms=round(duration_ms, 2)
        )
        session_state.add_trace(trace)

        return IntentAgentResult(
            intent=intent,
            confidence=float(analysis.get("confidence", 0.85)),
            extracted_entities=analysis.get("extracted_entities", {}),
            modified_constraint_fields=modified_fields,
            missing_critical_fields=missing_critical,
            clarification_questions=clarification_questions,
            target_stop_to_remove=analysis.get("target_stop_to_remove"),
            is_out_of_scope=bool(analysis.get("is_out_of_scope", False)),
            is_modification=is_modification,
            explanation=analysis.get("explanation", ""),
            status=status
        )

    def _is_obviously_out_of_scope(self, text: str) -> bool:
        """Quick heuristic check for non-travel technical or general topics."""
        lower = text.lower()
        non_travel_keywords = [
            # Programming & Technical
            "viết code", "hàm python", "code python", "javascript", "code c++", "lập trình", "debug lỗi", "sql injection", "giải thuật",
            # Math & Academic
            "giải phương trình", "đạo hàm", "tích phân", "hóa học", "công thức toán", "hình học không gian",
            # Sports & Finance & General News
            "bóng đá", "ngoại hạng anh", "champions league", "world cup", "giá vàng", "chứng khoán", "cổ phiếu", "bitcoin", "tiền điện tử"
        ]
        has_travel_kw = any(k in lower for k in [
            "du lịch", "đi chơi", "khách sạn", "lịch trình", "vé", "tour", "máy bay",
            "địa điểm", "tham quan", "phượt", "homestay", "resort", "điểm đến", "nhà hàng", "quán ăn", "cà phê"
        ])
        if not has_travel_kw:
            for kw in non_travel_keywords:
                if kw in lower:
                    return True
        return False

    def _classify_with_llm(
        self,
        user_message: str,
        session_state: TravelSessionState
    ) -> Optional[Dict[str, Any]]:
        """Invokes Gemini LLM with structured JSON output instructions."""
        try:
            from src.services.travel_chatbot_service import _call_gemini_generate_content, gemini_key_manager

            if not gemini_key_manager.has_keys():
                return None

            existing_constraints = session_state.constraints.model_dump(exclude={"field_provenance"})
            system_prompt = f"""Bạn là Intent & Context Agent của hệ thống VietTravel-AI.
Nhiệm vụ: Phân loại mục tiêu của người dùng và trích xuất thực thể du lịch.

CÁC MỤC TIÊU (INTENT) HỢP LỆ:
- 'search_places': Tìm kiếm quán ăn, cà phê, điểm tham quan đơn lẻ.
- 'general_travel_info': Hỏi thời tiết, kinh nghiệm, thông tin văn hóa chung.
- 'create_itinerary': Yêu cầu lập lịch trình cho chuyến đi.
- 'modify_itinerary': Yêu cầu chỉnh sửa lịch trình nói chung.
- 'change_budget': Yêu cầu tăng/giảm ngân sách hoặc làm chuyến đi rẻ hơn/sang trọng hơn.
- 'change_group_size': Thay đổi số lượng người đi.
- 'change_dates': Đổi ngày đi hoặc thời lượng.
- 'change_destination': Đổi sang điểm đến/thành phố khác.
- 'remove_stop': Yêu cầu xóa/bỏ một điểm dừng trong lịch trình.
- 'out_of_scope': Yêu cầu hoàn toàn không liên quan đến du lịch.
- 'clarification_needed': Câu hỏi quá mơ hồ, thiếu thông tin cốt lõi để xác định ý định.

RÀNG BUỘC ĐÃ BIẾT HIỆN TẠI TỪ PHIÊN:
{json.dumps(existing_constraints, ensure_ascii=False)}

YÊU CẦU:
1. Chỉ trích xuất thông tin CÓ TRONG câu nói của người dùng. KHÔNG tự bịa ngày tháng, ngân sách hoặc sở thích nếu người dùng không nhắc.
2. Trả về đúng định dạng JSON với cấu trúc:
{{
  "intent": "tên_intent_hợp_lệ",
  "confidence": 0.95,
  "extracted_entities": {{
    "destination": "...",
    "origin": "...",
    "start_date": "YYYY-MM-DD",
    "duration_days": 3,
    "group_size": 2,
    "budget": 5000000.0,
    "travel_style": "...",
    "interests": ["..."]
  }},
  "target_stop_to_remove": null,
  "clarification_questions": [],
  "is_out_of_scope": false,
  "explanation": "Tóm tắt ngắn gọn lý do phân loại"
}}"""

            user_prompt = f"Phân tích câu nói sau: \"{user_message}\""
            res = _call_gemini_generate_content(
                prompt_text=user_prompt,
                system_instruction=system_prompt,
                json_mode=True,
                temperature=0.1,
                max_tokens=1000
            )

            if not res or not res.get("text"):
                return None

            raw_text = res["text"].strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and "intent" in parsed:
                return parsed

        except Exception as e:
            logger.warning(f"LLM intent classification failed: {e}. Falling back to deterministic rules.")
            return None

        return None

    def _deterministic_intent_analysis(
        self,
        text: str,
        session_state: TravelSessionState
    ) -> Dict[str, Any]:
        """
        Pure Python rule-based and regex intent analyzer.
        Reliably handles Vietnamese and English travel queries without hallucination.
        """
        lower = text.lower()
        extracted: Dict[str, Any] = {}
        clarification_questions: List[str] = []

        # 1. Remove stop intent
        remove_match = re.search(
            r"(?:bỏ|xóa|loại bỏ|hủy|remove|delete)\s+(?:điểm|địa điểm|chỗ|stop|attraction)?\s*(?:thứ\s*)?(\d+|[A-ZÀ-Ỹa-zà-ỹ0-9\s]+?)(?:\s+(?:trong|khỏi|ở)\s+lịch\s+trình)?(?:$|\s+nhé|\s+nha|\s+giúp|\.)",
            lower
        )
        if remove_match:
            raw_target = remove_match.group(1).strip()
            # If starts with digit, extract the number
            num_match = re.search(r"^\s*(\d+)", raw_target)
            if num_match:
                target = num_match.group(1)
            else:
                target = re.sub(r"\s+(?:trong|khỏi|ở)\s+lịch\s+trình.*$", "", raw_target, flags=re.IGNORECASE).strip()

            return {
                "intent": INTENT_REMOVE_STOP,
                "confidence": 0.95,
                "extracted_entities": {},
                "target_stop_to_remove": target,
                "clarification_questions": [],
                "explanation": f"Người dùng muốn xóa điểm dừng '{target}' khỏi lịch trình."
            }

        # 2. Change destination intent
        dest_change_match = re.search(
            r"(?:đổi|chuyển|thay đổi)(?:\s+điểm đến|\s+nơi đến|\s+địa điểm)?\s+(?:sang|thành|về|đến)\s+([A-ZÀ-Ỹa-zà-ỹ\s]+?)(?:$|\s+(?:nhé|nha|đi|giúp|với|\.))",
            lower
        )
        if dest_change_match:
            new_dest = dest_change_match.group(1).strip()
            new_dest = self._normalize_destination_name(new_dest)
            extracted["destination"] = new_dest
            return {
                "intent": INTENT_CHANGE_DESTINATION,
                "confidence": 0.95,
                "extracted_entities": extracted,
                "clarification_questions": [],
                "explanation": f"Người dùng đổi điểm đến sang {new_dest}."
            }

        # 3. Group size entity extraction & Change group size intent
        group_match = re.search(
            r"(?:đổi\s+(?:thành\s*)?|chúng tôi\s+(?:bây giờ|nay|hiện tại)\s+là|chúng tôi đi|bây giờ là|nhóm|đoàn)\s*(\d+)\s*(?:người|bạn|thành viên|khách|pax|adults?|people|persons?)",
            lower
        )
        if not group_match and re.search(r"\b(\d+)\s*(?:người|bạn|thành viên|khách|pax)\b", lower):
            group_match = re.search(r"\b(\d+)\s*(?:người|bạn|thành viên|khách|pax)\b", lower)

        is_itinerary_request = any(k in lower for k in [
            "lịch trình", "lập lịch", "lên lịch", "kế hoạch", "phượt", "tour", "chuyến đi", "tư vấn", "đi chơi"
        ])

        if group_match:
            try:
                g_val = int(group_match.group(1))
                if 1 <= g_val <= 50:
                    extracted["group_size"] = g_val
                    # Only treat as INTENT_CHANGE_GROUP_SIZE if explicitly requesting a modification, not planning a new trip
                    is_explicit_change = bool(re.search(r"\b(?:đổi\s+(?:thành|sang)?|chuyển\s+thành|tăng\s+lên|giảm\s+xuống|bây giờ là)\b", lower))
                    if is_explicit_change and not is_itinerary_request and session_state.current_plan:
                        return {
                            "intent": INTENT_CHANGE_GROUP_SIZE,
                            "confidence": 0.95,
                            "extracted_entities": extracted,
                            "clarification_questions": [],
                            "explanation": f"Người dùng thay đổi số lượng người đi thành {g_val}."
                        }
            except (ValueError, TypeError):
                pass

        # 4. Change budget intent
        budget_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:triệu|tr|củ|m)\b", lower)
        if budget_match:
            try:
                b_val = float(budget_match.group(1).replace(",", ".")) * 1000000.0
                extracted["budget"] = b_val
            except (ValueError, TypeError):
                pass
        else:
            raw_num_match = re.search(r"(?:ngân sách|kinh phí|chi phí|budget)(?:\s+là|\s*:\s*|\s+thành|\s+khoảng)?\s*(\d{1,3}(?:[.,]\d{3})+|\d{5,10})", lower)
            if raw_num_match:
                try:
                    cleaned_num = raw_num_match.group(1).replace(".", "").replace(",", "")
                    b_val = float(cleaned_num)
                    extracted["budget"] = b_val
                except (ValueError, TypeError):
                    pass

        if any(k in lower for k in ["rẻ hơn", "tiết kiệm hơn", "giảm chi phí", "làm cho rẻ", "cheaper"]):
            extracted["travel_style"] = "tiết kiệm"
            return {
                "intent": INTENT_CHANGE_BUDGET,
                "confidence": 0.92,
                "extracted_entities": extracted,
                "clarification_questions": [],
                "explanation": "Người dùng yêu cầu điều chỉnh ngân sách tiết kiệm hơn."
            }

        if "budget" in extracted and any(k in lower for k in ["ngân sách", "kinh phí", "chi phí", "budget"]):
            return {
                "intent": INTENT_CHANGE_BUDGET,
                "confidence": 0.92,
                "extracted_entities": extracted,
                "clarification_questions": [],
                "explanation": f"Người dùng điều chỉnh mức ngân sách thành {extracted['budget']:,.0f} VND."
            }

        # 5. Extract dates and duration
        duration_match = re.search(r"(\d+)\s*(?:ngày(?:\s*\d+\s*đêm)?|days?)", lower)
        if duration_match:
            try:
                d_val = int(duration_match.group(1))
                if 1 <= d_val <= 30:
                    extracted["duration_days"] = d_val
            except (ValueError, TypeError):
                pass

        date_iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", lower)
        if date_iso_match:
            extracted["start_date"] = date_iso_match.group(1)

        # Check for change dates intent
        is_change_dates = any(k in lower for k in ["đổi ngày", "chuyển ngày", "thay đổi ngày", "đổi thời gian", "dời ngày", "lùi ngày", "hoãn ngày"]) or \
                          (("start_date" in extracted or "duration_days" in extracted) and any(k in lower for k in ["đổi", "chuyển", "thay đổi", "sang ngày", "ngày bắt đầu sang", "ngày đi sang"]))
        if is_change_dates:
            return {
                "intent": INTENT_CHANGE_DATES,
                "confidence": 0.95,
                "extracted_entities": extracted,
                "clarification_questions": [],
                "explanation": "Người dùng thay đổi ngày khởi hành hoặc thời lượng chuyến đi."
            }

        # 6. Extract Destination
        dest_found = self._extract_destination_heuristic(lower)
        if dest_found:
            extracted["destination"] = dest_found

        # 7. Check for planning vs searching vs general info vs clarification
        is_itinerary_req = any(k in lower for k in [
            "lịch trình", "kế hoạch", "lên lịch", "itinerary", "plan",
            "ngày 1", "ngày 2", "sắp xếp chuyến đi", "gợi ý lịch trình"
        ])
        is_search_req = any(k in lower for k in [
            "tìm quán", "tìm địa điểm", "tìm khách sạn", "tìm cà phê", "tìm nhà hàng",
            "gợi ý quán", "ở đâu ngon", "quán ăn", "chỗ nào đẹp", "check-in"
        ])
        is_general_info = any(k in lower for k in [
            "thời tiết", "mùa này", "nhiệt độ", "nên đi tháng mấy", "kinh nghiệm", "lưu ý"
        ])

        # Ambiguous check: "tôi muốn đi du lịch" without destination or specifics
        is_vague_travel_prompt = any(lower == phrase or lower.startswith(phrase) for phrase in [
            "tôi muốn đi du lịch", "muốn đi chơi", "tư vấn du lịch", "du lịch việt nam", "tư vấn cho tôi"
        ])

        if is_vague_travel_prompt and not dest_found and not session_state.constraints.destination:
            return {
                "intent": INTENT_CLARIFICATION_NEEDED,
                "confidence": 0.90,
                "extracted_entities": extracted,
                "clarification_questions": [
                    "Bạn muốn khám phá địa danh hoặc tỉnh/thành phố nào ở Việt Nam?",
                    "Chuyến đi dự kiến của bạn sẽ kéo dài trong bao nhiêu ngày?"
                ],
                "explanation": "Yêu cầu du lịch chung chung, cần làm rõ điểm đến và thời gian mong muốn."
            }

        if is_itinerary_req or (extracted.get("duration_days") and dest_found):
            return {
                "intent": INTENT_CREATE_ITINERARY,
                "confidence": 0.92,
                "extracted_entities": extracted,
                "clarification_questions": [],
                "explanation": "Người dùng yêu cầu lập lịch trình du lịch."
            }

        if is_general_info:
            return {
                "intent": INTENT_GENERAL_TRAVEL_INFO,
                "confidence": 0.88,
                "extracted_entities": extracted,
                "clarification_questions": [],
                "explanation": "Người dùng hỏi thông tin tra cứu/kinh nghiệm du lịch chung."
            }

        # Default to place search or general inquiry
        return {
            "intent": INTENT_SEARCH_PLACES if is_search_req or dest_found else INTENT_GENERAL_TRAVEL_INFO,
            "confidence": 0.80,
            "extracted_entities": extracted,
            "clarification_questions": [],
            "explanation": "Yêu cầu tìm kiếm địa điểm hoặc tư vấn du lịch cơ bản."
        }

    def _normalize_destination_name(self, name: str) -> str:
        """Capitalizes and cleans destination text."""
        cleaned = name.strip().title()
        mapping = {
            "Ha Noi": "Hà Nội",
            "Hanoi": "Hà Nội",
            "Tp.Hcm": "TP.HCM",
            "Tphcm": "TP.HCM",
            "Sai Gon": "Sài Gòn",
            "Da Nang": "Đà Nẵng",
            "Hoi An": "Hội An",
            "Hue": "Huế",
            "Da Lat": "Đà Lạt",
            "Dalat": "Đà Lạt",
            "Nha Trang": "Nha Trang",
            "Phu Quoc": "Phú Quốc",
            "Cat Ba": "Cát Bà",
            "Vung Tau": "Vũng Tàu",
            "Ha Long": "Hạ Long",
            "Sapa": "Sa Pa",
            "Ninh Binh": "Ninh Bình",
            "Quy Nhon": "Quy Nhơn",
            "Con Dao": "Côn Đảo",
            "Moc Chau": "Mộc Châu",
            "Ha Giang": "Hà Giang",
            "Thai Nguyen": "Thái Nguyên"
        }
        return mapping.get(cleaned, cleaned)

    def _extract_destination_heuristic(self, lower_text: str) -> Optional[str]:
        """Scans for recognized Vietnamese destination entities in text."""
        # Sort by longest string match first
        sorted_destinations = sorted(KNOWN_DESTINATIONS, key=lambda d: len(d), reverse=True)
        for dest in sorted_destinations:
            pattern = rf"\b{re.escape(dest)}\b"
            if re.search(pattern, lower_text):
                return self._normalize_destination_name(dest)
        return None

    def _apply_updates_to_state(
        self,
        session_state: TravelSessionState,
        analysis: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """
        Validates extracted entities and partially updates session constraints.
        Returns (modified_fields_list, validation_errors_list).
        """
        entities = analysis.get("extracted_entities") or {}
        if not isinstance(entities, dict) or not entities:
            return [], []

        validation_errors: List[str] = []
        updates_to_apply: Dict[str, Any] = {}

        # 1. Validate dates
        start_date = entities.get("start_date")
        if start_date:
            try:
                # TripConstraints validator verifies YYYY-MM-DD
                TripConstraints.validate_date_format(start_date)
                updates_to_apply["start_date"] = start_date
            except Exception as e:
                validation_errors.append(f"Ngày khởi hành '{start_date}' không đúng chuẩn YYYY-MM-DD hoặc không tồn tại.")

        # 2. Validate group size
        group_size = entities.get("group_size")
        if group_size is not None:
            try:
                g = int(group_size)
                if 1 <= g <= 50:
                    updates_to_apply["group_size"] = g
                else:
                    validation_errors.append(f"Số lượng người ({g}) phải nằm trong khoảng từ 1 đến 50.")
            except (ValueError, TypeError):
                validation_errors.append("Số lượng người phải là một số nguyên hợp lệ.")

        # 3. Validate budget
        budget = entities.get("budget")
        if budget is not None:
            try:
                b = float(budget)
                if b >= 0:
                    updates_to_apply["budget"] = b
                else:
                    validation_errors.append("Ngân sách dự kiến không được là số âm.")
            except (ValueError, TypeError):
                validation_errors.append("Ngân sách phải là một số hợp lệ.")

        # 4. Duration
        duration_days = entities.get("duration_days")
        if duration_days is not None:
            try:
                d = int(duration_days)
                if 1 <= d <= 30:
                    updates_to_apply["duration_days"] = d
                else:
                    validation_errors.append("Thời lượng chuyến đi nên nằm trong khoảng 1 đến 30 ngày.")
            except (ValueError, TypeError):
                pass

        # 5. String fields
        for field_name in ("destination", "origin", "travel_style", "transportation"):
            val = entities.get(field_name)
            if val and isinstance(val, str) and val.strip():
                updates_to_apply[field_name] = val.strip()

        # 6. List fields
        for field_name in ("interests", "food_preferences", "accessibility_requirements"):
            val = entities.get(field_name)
            if val:
                if isinstance(val, str):
                    updates_to_apply[field_name] = [x.strip() for x in val.split(",") if x.strip()]
                elif isinstance(val, list):
                    updates_to_apply[field_name] = [str(x).strip() for x in val if str(x).strip()]

        if not updates_to_apply:
            return [], validation_errors

        try:
            modified = session_state.update_constraints(updates_to_apply, source="user_provided")
            return sorted(list(modified)), validation_errors
        except Exception as update_err:
            validation_errors.append(f"Lỗi cập nhật ràng buộc: {str(update_err)}")
            return [], validation_errors


# Global default instance
default_intent_agent = IntentAndContextAgent()


def process_user_intent(
    user_message: str,
    session_state: TravelSessionState,
    execute_llm: bool = True
) -> IntentAgentResult:
    """Helper functional interface for Intent & Context Agent."""
    return default_intent_agent.process(
        user_message=user_message,
        session_state=session_state,
        execute_llm=execute_llm
    )
