"""
VietTravel AI - Image Crawler Tool (Multi-Agent Tool Foundation)
Allows specialized agents (Destination Research Agent, Tour Guide Agent)
to dynamically crawl, verify, and retrieve authentic real-world photos
from Wikipedia, Wikimedia Commons, and web search scrapers.
"""

import logging
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict

from src.services.ai.image_crawler_agent import image_crawler_agent

logger = logging.getLogger(__name__)


class CrawlRealImageInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=2, max_length=200, description="Place name, POI, or destination to crawl photos for")
    category: Optional[str] = Field(default="", description="Category (e.g. Danh lam thắng cảnh, Điểm tham quan, Ẩm thực)")
    region: Optional[str] = Field(default="", description="Province, city, or regional area (e.g. Hà Giang, Đà Nẵng)")
    osm_id: Optional[str] = Field(default=None, description="Optional OpenStreetMap ID for database persistence")


def crawl_real_image_tool(
    name: str,
    category: Optional[str] = "",
    region: Optional[str] = "",
    osm_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes real-time dynamic image crawling for a given location or attraction.
    Returns the authentic photo URL and verification metadata.
    """
    clean_name = (name or "").strip()
    clean_region = (region or "").strip()
    clean_cat = (category or "").strip()

    if not clean_name:
        return {
            "success": False,
            "name": name,
            "image_url": None,
            "message": "Tên địa điểm không hợp lệ để cào ảnh."
        }

    try:
        # If it's a general destination/province overview
        if clean_cat.lower() in ("toàn cảnh", "hero", "tổng quan") or clean_name.lower() == clean_region.lower():
            crawled_url = image_crawler_agent.crawl_destination_hero_image(clean_name)
        else:
            crawled_url = image_crawler_agent.crawl_image(
                name=clean_name,
                category=clean_cat,
                region=clean_region,
                osm_id=osm_id
            )

        if crawled_url:
            return {
                "success": True,
                "name": clean_name,
                "region": clean_region,
                "image_url": crawled_url,
                "source": "crawled_real_photo",
                "verified": True,
                "message": f"Đã cào thành công ảnh thật cho '{clean_name}'."
            }
        else:
            return {
                "success": False,
                "name": clean_name,
                "region": clean_region,
                "image_url": None,
                "source": "not_found",
                "verified": False,
                "message": f"Không tìm thấy ảnh thật phù hợp cho '{clean_name}'."
            }
    except Exception as e:
        logger.error(f"Error executing crawl_real_image_tool: {e}", exc_info=True)
        return {
            "success": False,
            "name": clean_name,
            "image_url": None,
            "error": str(e),
            "message": f"Lỗi tác tử cào ảnh: {str(e)}"
        }
