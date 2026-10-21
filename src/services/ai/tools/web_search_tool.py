"""
VietTravel AI - Web Search & Grounding Tool (Phase 3 Core Foundation)
Retrieves live web facts and citations with explicit provenance metadata.

Principles:
1. Uses Google Search Grounding when Gemini keys are configured and available.
2. If web search is disabled, provider quota exhausted, or offline, reports clear status.
3. Extracts source titles, URIs, and search queries with zero fabrication.
4. Distinguishes verified external sources from unverified local assumptions.
"""

import os
import logging
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)


class SearchWebInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=2, max_length=250, description="Search keywords or travel question")
    destination: Optional[str] = Field(default=None, description="Optional destination for geo-focus")
    max_results: int = Field(default=5, ge=1, le=10, description="Max web source references to retrieve")


def search_web_travel_info(
    query: str,
    destination: Optional[str] = None,
    max_results: int = 5
) -> Dict[str, Any]:
    """
    Executes web grounding search using the project's GeminiKeyManager and Google Search Grounding.
    Returns structured web queries, sources, and snippets.
    """
    clean_query = query.strip()
    if destination:
        clean_query = f"{clean_query} tại {destination}"

    try:
        from src.services.travel_chatbot_service import _call_gemini_generate_content, gemini_key_manager

        if not gemini_key_manager.has_keys():
            return {
                "success": False,
                "provider_available": False,
                "message": "Không có Gemini API Key để thực thi Google Search Grounding.",
                "query": clean_query,
                "web_sources": [],
                "web_search_queries": [],
                "grounded": False
            }

        prompt = f"Tìm kiếm thông tin du lịch cập nhật nhất cho câu hỏi: '{clean_query}'. Trích xuất thông tin thực tế, giá vé và giờ mở cửa nếu có."
        res = _call_gemini_generate_content(
            prompt_text=prompt,
            system_instruction="Bạn là trợ lý tra cứu dữ liệu web thời gian thực. Cung cấp thông tin xác thực, có dẫn chứng nguồn gốc.",
            enable_web_search=True,
            temperature=0.2,
            max_tokens=1500
        )

        if not res:
            return {
                "success": False,
                "provider_available": True,
                "message": "Dịch vụ tìm kiếm web tạm thời không trả về dữ liệu (hạn mức hoặc lỗi mạng).",
                "query": clean_query,
                "web_sources": [],
                "web_search_queries": [],
                "grounded": False
            }

        sources = res.get("web_sources", [])[:max_results]
        queries = res.get("web_search_queries", [])
        snippet_text = res.get("text", "")

        return {
            "success": True,
            "provider_available": True,
            "query": clean_query,
            "snippet": snippet_text,
            "web_sources": sources,
            "web_search_queries": queries,
            "grounded": bool(sources or queries)
        }

    except Exception as e:
        logger.error(f"Error executing web search: {e}", exc_info=True)
        return {
            "success": False,
            "provider_available": False,
            "message": f"Lỗi tra cứu web: {str(e)}",
            "query": clean_query,
            "web_sources": [],
            "web_search_queries": [],
            "grounded": False
        }
