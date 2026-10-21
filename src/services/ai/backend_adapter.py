"""
VietTravel AI - Backend Adapter (Phase 2 & Phase 3)
Acts as the bridge between production Flask/RESTX controllers and the Multi-Agent Coordinator.
Responsibilities:
1. Feature Flag management (ENABLE_MULTI_AGENT_COORDINATOR) allowing instant rollback.
2. Token decoding & conversation ownership validation.
3. Converting CoordinatorResult into production API contracts (chatbot_response_model).
4. Redacting internal traces, secrets, and sensitive tokens from API payloads.
5. In-memory and database session synchronization.
"""

import os
import re
import jwt
import logging
import urllib.parse
from typing import Dict, List, Optional, Any, Tuple

from src.config.config import Config
from src.services.ai.state import (
    TravelSessionState,
    ItineraryPlanDraft,
    BudgetBreakdown
)
from src.services.ai.agents.coordinator_agent import (
    CoordinatorAgent,
    CoordinatorResult,
    default_coordinator_agent
)
from src.services.ai.session_manager import (
    SessionManager,
    default_session_manager
)

logger = logging.getLogger(__name__)


def is_coordinator_enabled() -> bool:
    """Returns True if the Multi-Agent Coordinator feature flag is active."""
    env_val = os.getenv("ENABLE_MULTI_AGENT_COORDINATOR")
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes")
    return getattr(Config, "ENABLE_MULTI_AGENT_COORDINATOR", True)


def extract_user_id_from_header(auth_header: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    """
    Extracts and validates user_id from Authorization Bearer JWT token.
    Returns (user_id, None) on success or if no header is present; (None, error_msg) if token is invalid/expired.
    """
    if not auth_header:
        return None, None

    parts = auth_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, "Invalid Authorization header format. Expected 'Bearer <token>'"

    token = parts[1]
    try:
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("user_id")
        if not user_id:
            return None, "Token does not contain user_id"
        return int(user_id), None
    except jwt.ExpiredSignatureError:
        return None, "Token has expired. Please log in again."
    except jwt.InvalidTokenError as e:
        return None, f"Invalid authentication token: {str(e)}"
    except Exception as e:
        return None, f"Authentication error: {str(e)}"


def get_conversation_by_id(conversation_id: int) -> Any:
    """
    Safely retrieves conversation record by ID from database if available.
    """
    try:
        from src.models.conversation import Conversation
        return Conversation.query.get(conversation_id)
    except Exception as e:
        logger.debug(f"Failed to query conversation {conversation_id}: {e}")
        return None


def verify_conversation_ownership(conversation_id: int, user_id: Optional[int]) -> Tuple[bool, Optional[str]]:
    """
    Checks that the requesting user owns the specified conversation.
    Returns (True, None) if authorized; (False, error_reason) if unauthorized.
    """
    try:
        conv = get_conversation_by_id(conversation_id)
        if not conv:
            return True, None  # If conversation doesn't exist in DB, let session handle it in-memory

        if getattr(conv, 'user_id', None) is not None:
            if user_id is None:
                return False, "Authentication required to access this private conversation."
            if int(conv.user_id) != int(user_id):
                return False, "You do not have permission to access this conversation."
        return True, None
    except Exception as e:
        logger.debug(f"Conversation ownership check bypassed (DB offline or unconfigured): {e}")
        return True, None


def adapt_coordinator_result_to_chat_response(
    coord_result: CoordinatorResult,
    detected_language: str = "vietnamese",
    user_message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Maps CoordinatorResult to the exact schema defined by chatbot_response_model in travel_chatbot_controller.
    Guarantees no secret keys or internal traces are exposed.
    """
    state = coord_result.session_state
    constraints = state.constraints

    # 1. Map places to search_results (matching answer_model)
    search_results: List[Dict[str, Any]] = []

    # Include researched places
    for idx, p in enumerate(coord_result.researched_places):
        place_id = str(p.source_id or f"place_{idx + 1}")
        lat = p.latitude
        lng = p.longitude
        dest_fallback = constraints.destination or 'Việt Nam'
        maps_url = p.google_maps_url or (
            f"https://www.google.com/maps/search/?api=1&query={lat},{lng}" if lat and lng else (
                f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote((p.name or '') + ' ' + (getattr(p, 'address', '') or dest_fallback))}"
            )
        )
        search_results.append({
            'id': place_id,
            'ten_dia_diem': p.name,
            'mo_ta': p.description or '',
            'loai_dia_diem': p.category or 'attraction',
            'khu_vuc': getattr(p, 'address', '') or '',
            'dia_chi': p.address or '',
            'latitude': lat,
            'longitude': lng,
            'google_maps_url': maps_url,
            'distance_km': None,
            'similarity': 1.0,
            'image_url': p.image_url,
            'thoi_gian_hoat_dong': p.opening_hours,
            'gia_ve': p.estimated_price,
            'danh_gia': None,
            'language': detected_language
        })

    # If search_results is empty but current_plan exists, populate from itinerary stops
    if not search_results and coord_result.current_plan:
        for day in coord_result.current_plan.days:
            for s in day.stops:
                lat = s.latitude
                lng = s.longitude
                dest_fallback = constraints.destination or 'Việt Nam'
                maps_url = s.google_maps_url or (
                    f"https://www.google.com/maps/search/?api=1&query={lat},{lng}" if lat and lng else (
                        f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote((s.name or '') + ' ' + (s.address or dest_fallback))}"
                    )
                )
                search_results.append({
                    'id': str(s.osm_id or s.poi_id or f"stop_{s.order_index}"),
                    'ten_dia_diem': s.name,
                    'mo_ta': s.notes or '',
                    'loai_dia_diem': s.category or 'attraction',
                    'khu_vuc': s.address or '',
                    'dia_chi': s.address or '',
                    'latitude': lat,
                    'longitude': lng,
                    'google_maps_url': maps_url,
                    'distance_km': None,
                    'similarity': 1.0,
                    'image_url': s.image_url,
                    'thoi_gian_hoat_dong': s.opening_hours,
                    'gia_ve': f"{s.estimated_ticket_price:,.0f} đ" if s.estimated_ticket_price else None,
                    'danh_gia': None,
                    'language': detected_language
                })

    # If search_results is still empty, fallback to querying PostgreSQL POI Engine
    if not search_results:
        try:
            from src.services.travel_chatbot_service import search_pois_from_postgresql
            dest = constraints.destination or "Hà Giang"
            pg_pois = search_pois_from_postgresql(dest, {"khu_vuc": dest, "diem_den": dest}, limit=6)
            if pg_pois:
                search_results.extend(pg_pois)
        except Exception as e:
            logger.warning(f"Failed to populate search_results from PostgreSQL in coordinator adapter: {e}")

    # 2. Extract suggested activities
    suggested_activities: List[str] = []
    if coord_result.current_plan:
        for day in coord_result.current_plan.days:
            if day.theme and day.theme not in suggested_activities:
                suggested_activities.append(day.theme)
            for stop in day.stops:
                if stop.name and stop.name not in suggested_activities:
                    suggested_activities.append(f"Tham quan {stop.name}")
    suggested_activities = suggested_activities[:5]

    # 3. Web sources & queries
    web_sources = []
    web_queries = []
    for ev in state.evidence_items:
        if ev.source_type == "web_search" and ev.source_url:
            web_sources.append({
                "title": ev.source_title or "Web source",
                "uri": ev.source_url
            })
            if ev.metadata.get("query") and ev.metadata.get("query") not in web_queries:
                web_queries.append(ev.metadata.get("query"))

    # 4. Safe AI Pipeline Summary
    ai_pipeline = {
        'coordinator': 'CoordinatorAgent (Bounded Multi-Agent Orchestration)',
        'agents_executed': coord_result.agents_executed,
        'iterations': coord_result.iterations_count,
        'intent': coord_result.intent,
        'total_tool_calls': coord_result.total_tool_calls,
        'status': coord_result.status
    }

    # 5. Build Extracted Features
    features: Dict[str, Any] = constraints.model_dump(exclude={"field_provenance"})
    if getattr(constraints, "group_size", None):
        features["so_nguoi"] = f"{constraints.group_size} người"
    features["session_id"] = state.session_id
    if state.conversation_id:
        features["conversation_id"] = state.conversation_id
    if coord_result.budget_breakdown:
        features["budget_breakdown"] = coord_result.budget_breakdown.model_dump()
    if coord_result.current_plan:
        features["itinerary_draft"] = coord_result.current_plan.model_dump()

    adapted_response = coord_result.message or 'Đã xử lý thông tin chuyến đi.'
    try:
        from src.services.travel_chatbot_service import _sanitize_response_images, _enrich_response_maps, _sanitize_response_budget
        from src.services.ai.image_crawler_agent import image_crawler_agent
        dest_hint = constraints.destination or "Hà Giang"
        hero_img = image_crawler_agent.crawl_destination_hero_image(dest_hint)
        adapted_response = _sanitize_response_images(adapted_response, search_results, dest_hint, hero_image=hero_img)
        adapted_response = _enrich_response_maps(adapted_response, search_results, dest_hint)
        so_nguoi_hint = getattr(constraints, "travelers", None) or getattr(constraints, "group_size", None)
        adapted_response = _sanitize_response_budget(adapted_response, f"{so_nguoi_hint} người" if so_nguoi_hint else None, user_message)
    except Exception as san_err:
        logger.warning(f"Failed to sanitize coordinator response: {san_err}")

    return {
        'status': 'success',
        'message': adapted_response,
        'response': adapted_response,
        'ai_status': 'ready',
        'ai_pipeline': ai_pipeline,
        'web_search_queries': web_queries,
        'web_sources': web_sources,
        'language': detected_language,
        'language_name': 'Tiếng Việt' if detected_language == 'vietnamese' else detected_language.capitalize(),
        'search_results': search_results,
        'suggested_activities': suggested_activities,
        'follow_up_questions': coord_result.clarification_questions,
        'extracted_features': features
    }


def handle_coordinator_chat_request(
    request_data: Dict[str, Any],
    request_headers: Any,
    detected_language: str = "vietnamese",
    coordinator: Optional[CoordinatorAgent] = None,
    session_mgr: Optional[SessionManager] = None
) -> Tuple[Optional[Dict[str, Any]], int]:
    """
    Main entry point for handling chat requests through the Multi-Agent Coordinator.
    Returns (response_payload, status_code). If disabled, returns (None, 200).
    """
    if not is_coordinator_enabled():
        return None, 200

    coord = coordinator or default_coordinator_agent
    mgr = session_mgr or default_session_manager

    # 1. Authorization check
    auth_header = request_headers.get("Authorization") if hasattr(request_headers, "get") else None
    user_id, auth_err = extract_user_id_from_header(auth_header)
    if auth_err and auth_header:
        return {
            'status': 'unauthorized',
            'message': auth_err,
            'response': auth_err,
            'language': detected_language,
            'language_name': 'Unknown',
            'search_results': [],
            'suggested_activities': [],
            'follow_up_questions': [],
            'extracted_features': {}
        }, 401

    # 2. Conversation Ownership check
    conversation_id = request_data.get("conversation_id")
    if conversation_id is not None:
        try:
            cid_int = int(conversation_id)
            is_owner, owner_err = verify_conversation_ownership(cid_int, user_id)
            if not is_owner:
                status_code = 401 if user_id is None else 403
                return {
                    'status': 'forbidden' if status_code == 403 else 'unauthorized',
                    'message': owner_err,
                    'response': owner_err,
                    'language': detected_language,
                    'language_name': 'Unknown',
                    'search_results': [],
                    'suggested_activities': [],
                    'follow_up_questions': [],
                    'extracted_features': {}
                }, status_code
        except (ValueError, TypeError):
            pass

    # 3. Retrieve or create session
    session_id = request_data.get("session_id")
    session_state = mgr.get_or_create_session(
        session_id=session_id,
        user_id=user_id,
        conversation_id=conversation_id,
        language=detected_language
    )

    # 4. Invoke Multi-Agent Coordinator
    question = request_data.get("question", "")
    coord_result = coord.process(
        user_message=question,
        session_state=session_state,
        execute_api=False
    )

    # 5. Persist and save updated session
    mgr.save_session(session_state)
    mgr.persist_chat_turn(session_state, question, coord_result.message)

    # 6. Adapt response schema
    adapted_payload = adapt_coordinator_result_to_chat_response(coord_result, detected_language, user_message=question)
    return adapted_payload, 200
