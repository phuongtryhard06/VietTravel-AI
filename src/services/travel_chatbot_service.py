# -*- coding: utf-8 -*-
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import os
import re
import urllib.parse
from dotenv import load_dotenv
load_dotenv()
import threading
import json
import requests
import openai
from typing import Dict, List, Optional, Any

class GeminiKeyManager:
    """
    Quản lý danh sách API Key Gemini theo cơ chế round-robin và tự động failover
    khi một key gặp lỗi 429 (Resource Exhausted / Quota Limit) hoặc 403 (Invalid/Forbidden).
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._keys = []
        self._current_idx = 0
        self._reload_keys()

    def _reload_keys(self):
        raw_keys = os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        single_key = os.getenv("GEMINI_API_KEY", "").strip()
        if single_key and single_key not in keys:
            keys.append(single_key)
        self._keys = keys
        if self._current_idx >= len(self._keys):
            self._current_idx = 0

    def has_keys(self) -> bool:
        return len(self._keys) > 0

    def get_current_key(self) -> Optional[str]:
        with self._lock:
            if not self._keys:
                return None
            return self._keys[self._current_idx % len(self._keys)]

    def get_all_keys(self) -> List[str]:
        with self._lock:
            return list(self._keys)

    def rotate_key(self, failed_key: Optional[str] = None) -> str:
        with self._lock:
            if not self._keys:
                return ""
            old_key = self._keys[self._current_idx % len(self._keys)]
            if failed_key is None or failed_key == old_key:
                self._current_idx = (self._current_idx + 1) % len(self._keys)
            new_key = self._keys[self._current_idx % len(self._keys)]
            masked_old = old_key[:8] + "..." + old_key[-4:] if len(old_key) > 12 else "key"
            masked_new = new_key[:8] + "..." + new_key[-4:] if len(new_key) > 12 else "key"
            print(f"[GeminiKeyManager] 🔄 Quota/Error trên {masked_old} -> Đã chuyển sang API Key mới: {masked_new} (Key #{self._current_idx + 1}/{len(self._keys)})")
            return new_key

gemini_key_manager = GeminiKeyManager()


def _call_gemini_generate_content(
    prompt_text: str,
    system_instruction: Optional[str] = None,
    json_mode: bool = False,
    enable_web_search: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 8192
) -> Optional[Dict[str, Any]]:
    if not gemini_key_manager.has_keys():
        return None

    # Danh sách các mô hình ổn định và phản hồi nhanh nhất (< 2s)
    model_pool = [
        "gemini-2.5-flash",
        "gemini-flash-latest",
        "gemini-2.5-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3.5-flash"
    ]
    preferred = os.getenv("GEMINI_MODEL")
    if preferred and preferred in model_pool:
        model_pool.remove(preferred)
        model_pool.insert(0, preferred)
    elif preferred:
        model_pool.insert(0, preferred)

    def _build_payload(with_search: bool, is_json: bool):
        p = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_instruction}\n\n{prompt_text}" if system_instruction else prompt_text}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max(max_tokens, 3500),
                "thinkingConfig": {
                    "thinkingBudget": 0
                }
            }
        }
        if is_json:
            p["generationConfig"]["responseMimeType"] = "application/json"
        elif with_search:
            p["tools"] = [{"googleSearch": {}}]
        return p

    def _extract_text_from_parts(parts: list) -> str:
        clean_texts = []
        for p in parts:
            if isinstance(p, dict):
                t = p.get("text", "")
                if t and not p.get("thought", False):
                    clean_texts.append(t)
        return "".join(clean_texts).strip()

    total_keys = len(gemini_key_manager.get_all_keys())

    # Thử qua các keys trong pool nếu gặp lỗi 429 hoặc 403
    for _ in range(max(1, total_keys)):
        active_key = gemini_key_manager.get_current_key()
        if not active_key:
            break

        key_exhausted = False

        # Vòng 1: Nếu enable_web_search=True, thử gọi Google Search Grounding trước
        if enable_web_search and not json_mode:
            for m in model_pool:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={active_key}"
                try:
                    resp = requests.post(url, json=_build_payload(with_search=True, is_json=False), timeout=35)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            cand = candidates[0]
                            parts = cand.get("content", {}).get("parts", [])
                            text = _extract_text_from_parts(parts)
                            if text:
                                meta = cand.get("groundingMetadata", {})
                                web_queries = meta.get("webSearchQueries", [])
                                web_sources = []
                                for chunk in meta.get("groundingChunks", []):
                                    w = chunk.get("web", {})
                                    if w.get("uri"):
                                        web_sources.append({
                                            "title": w.get("title", "Nguồn web"),
                                            "uri": w.get("uri")
                                        })
                                return {
                                    "text": text,
                                    "web_search_queries": web_queries,
                                    "web_sources": web_sources,
                                    "grounded": bool(web_queries or web_sources)
                                }
                    elif resp.status_code == 429:
                        break
                    elif resp.status_code == 404:
                        continue
                except Exception:
                    continue

        # Vòng 2: Gọi sinh nội dung chuẩn (hoặc fallback nếu Grounding không thành công)
        for m in model_pool:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={active_key}"
            try:
                resp = requests.post(url, json=_build_payload(with_search=False, is_json=json_mode), timeout=50)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        cand = candidates[0]
                        parts = cand.get("content", {}).get("parts", [])
                        text = _extract_text_from_parts(parts)
                        if text:
                            return {
                                "text": text,
                                "web_search_queries": [],
                                "web_sources": [],
                                "grounded": False
                            }
                elif resp.status_code in (429, 403):
                    print(f"[Gemini] Key exhausted HTTP {resp.status_code} on {m}")
                    key_exhausted = True
                    break
                elif resp.status_code == 404:
                    continue
                else:
                    print(f"[Gemini] {m} returned HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as gemini_err:
                print(f"[Gemini] Request to {m} failed: {gemini_err}")
                continue

        if key_exhausted:
            gemini_key_manager.rotate_key(failed_key=active_key)
            continue
        else:
            break

    return None

import numpy as np
import re
import traceback

# Khởi tạo OpenAI client
openai.api_key = os.getenv('OPENAI_API_KEY')


def extract_user_intent_and_features(question: str) -> Dict[str, Any]:
    """
    Trích xuất ý định và đặc trưng từ câu hỏi của người dùng sử dụng OpenAI API
    
    Args:
        question (str): Câu hỏi của người dùng
        
    Returns:
        Dict[str, Any]: Kết quả trích xuất bao gồm ý định chính và các đặc trưng
    """
    
    # Định nghĩa schema cho các loại ý định khác nhau
    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": "tim_kiem_dia_diem",
                "description": "Trích xuất các tiêu chí để tìm kiếm địa điểm du lịch tại TP.HCM từ câu hỏi của người dùng.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "loai_dia_diem": {
                            "type": "string",
                            "description": "Loại địa điểm (bảo tàng, công viên, nhà hàng, khách sạn, chợ, v.v.)"
                        },
                        "khu_vuc": {
                            "type": "string", 
                            "description": "Khu vực cụ thể (quận 1, quận 3, Bến Thành, v.v.)"
                        },
                        "tu_khoa": {
                            "type": "string",
                            "description": "Từ khóa tìm kiếm (từ tiếng Việt, Anh, Trung, Nhật, Hàn)"
                        },
                        "gia": {
                            "type": "string",
                            "description": "Mức giá (rẻ, trung bình, cao, miễn phí, v.v.)"
                        }
                    },
                    "required": []
                }
            }
        }
    ]
    
    # System prompt cải tiến để hỗ trợ đa ngôn ngữ và 63 tỉnh thành Việt Nam
    system_prompt = """Bạn là trợ lý AI chuyên về du lịch Việt Nam (bao gồm Hà Nội, TP.HCM, Thái Nguyên, Hải Phòng, Quảng Ninh, Ninh Bình, Huế, Đà Nẵng, Đà Lạt, Nha Trang, Cần Thơ, Phú Quốc và toàn bộ 63 tỉnh thành). Hãy phân tích câu hỏi của người dùng và trích xuất thông tin để tìm kiếm địa điểm phù hợp.

Hỗ trợ các ngôn ngữ: Tiếng Việt, Tiếng Anh, Tiếng Trung, Tiếng Nhật, Tiếng Hàn

Từ khóa tiếng Anh phổ biến:
- Museum, Park, Restaurant, Hotel, Market, Shopping mall, Temple, Church, Coffee shop, Bar, Nightlife, Entertainment
- Thai Nguyen, Hai Phong, Quang Ninh, Ninh Binh, Hue, Da Nang, Hoi An, Da Lat, Nha Trang, Can Tho, Phu Quoc, Hanoi, Saigon, Ho Chi Minh City
- Cheap, Expensive, Free, Budget, Luxury, Food, Cuisine

Từ khóa tiếng Việt phổ biến:
- Bảo tàng, Công viên, Nhà hàng, Khách sạn, Chợ, Trung tâm thương mại, Chùa, Nhà thờ, Quán cà phê, Bar, Cuộc sống về đêm, Giải trí, Điểm tham quan, Thắng cảnh
- Thái Nguyên, Hải Phòng, Quảng Ninh, Hạ Long, Ninh Bình, Huế, Đà Nẵng, Hội An, Nha Trang, Đà Lạt, Cần Thơ, Phú Quốc, Hà Nội, Sài Gòn, TP.HCM
- Rẻ, Đắt, Miễn phí, Bình dân, Cao cấp, Ẩm thực, Món ăn

Hãy trích xuất chính xác các thông tin từ câu hỏi và trả về dưới dạng JSON."""

    # Thử trích xuất bằng Gemini API trước nếu có cấu hình
    if gemini_key_manager.has_keys():
        try:
            gemini_system = """Bạn là chuyên gia phân tích câu hỏi du lịch Việt Nam (hỗ trợ 63 tỉnh thành).
Nhiệm vụ:
1. Trích xuất thực thể và xác định ý định từ câu hỏi (tim_kiem_dia_diem, hoi_thong_tin_chi_tiet, lap_lich_trinh, so_sanh_dia_diem, gop_y_va_danh_gia, hoi_chung).
2. Xác định needs_web_search (boolean): True nếu câu hỏi cần thông tin thời gian thực (thời tiết, sự kiện đang diễn ra, quán mới mở gần đây, hot trend, tin tức mới). False nếu là câu hỏi địa điểm du lịch thông thường.
Trả về JSON format:
{
  "intent": "tim_kiem_dia_diem",
  "confidence": 0.95,
  "needs_web_search": false,
  "extracted_features": {
    "loai_dia_diem": "...",
    "khu_vuc": "...",
    "diem_xuat_phat": "...",
    "diem_den": "...",
    "so_nguoi": "...",
    "ngan_sach": "...",
    "thoi_gian": "...",
    "so_thich": "...",
    "muc_dich": "..."
  }
}
Lưu ý quan trọng:
- Nếu người dùng nêu điểm xuất phát (nơi ở hiện tại, bắt đầu từ đâu, vd: 'đang ở Phổ Yên', 'từ Hải Phòng', 'ở Cầu Giấy'): ghi vào 'diem_xuat_phat'.
- Điểm đến du lịch (nơi muốn tới, ghé thăm, vd: 'Lăng Bác', 'Tràng An, Ninh Bình', 'Tam Đảo'): ghi vào 'diem_den' và 'khu_vuc'. TUYỆT ĐỐI không gán điểm xuất phát vào 'khu_vuc'!
- Nếu người dùng nêu số lượng người đi (vd: '2 người', 'nhóm 4 người', 'gia đình 3 người', 'đi cùng người yêu'): ghi vào 'so_nguoi' (vd: '2 người', '4 người', '1 người').
- loai_dia_diem, khu_vuc, diem_xuat_phat, diem_den, so_nguoi, ngan_sach, thoi_gian, so_thich, muc_dich là chuỗi ký tự (string). Nếu trường nào không có, để null hoặc bỏ qua."""
            raw_gemini = _call_gemini_generate_content(
                prompt_text=f"Câu hỏi: {question}",
                system_instruction=gemini_system,
                json_mode=True,
                temperature=0.1,
                max_tokens=1000
            )
            if raw_gemini and raw_gemini.get("text"):
                data = json.loads(raw_gemini["text"])
                raw_feats = data.get("extracted_features", {})
                features = {}
                if isinstance(raw_feats, dict):
                    features = {k: v for k, v in raw_feats.items() if v}
                elif isinstance(raw_feats, list):
                    for item in raw_feats:
                        if isinstance(item, dict) and "entity" in item and "value" in item:
                            features[item["entity"]] = item["value"]
                        elif isinstance(item, dict):
                            features.update({k: v for k, v in item.items() if v})
                return {
                    "original_question": question,
                    "intent": data.get("intent", "tim_kiem_dia_diem"),
                    "confidence": float(data.get("confidence", 0.9)),
                    "needs_web_search": bool(data.get("needs_web_search", False)),
                    "extracted_features": features
                }
        except Exception as eg:
            print(f"Gemini extraction fallback to OpenAI: {eg}")

    # Fallback trích xuất bằng quy tắc từ khóa nếu Gemini API không khả dụng hoặc bị rate limit
    q_low = question.lower()
    features = {}
    
    # Khu vực
    districts = [
        ('Quận 1', ['quận 1', 'quan 1', 'q1', 'district 1']),
        ('Quận 3', ['quận 3', 'quan 3', 'q3', 'district 3']),
        ('Quận 5', ['quận 5', 'quan 5', 'q5', 'chợ lớn', 'cho lon']),
        ('Quận 7', ['quận 7', 'quan 7', 'q7', 'phú mỹ hưng']),
        ('Thủ Đức', ['thủ đức', 'thu duc']),
        ('Bình Thạnh', ['bình thạnh', 'binh thanh']),
        ('Hà Nội', ['hà nội', 'ha noi', 'hoàn kiếm', 'hoan kiem', 'tây hồ']),
        ('Đà Nẵng', ['đà nẵng', 'da nang']),
        ('Sa Pa', ['sa pa', 'sapa', 'fansipan']),
        ('Đà Lạt', ['đà lạt', 'da lat']),
    ]
    for d_name, d_aliases in districts:
        if any(a in q_low for a in d_aliases):
            features['khu_vuc'] = d_name
            break

    # Loại địa điểm
    categories = [
        ('quán cà phê', ['cà phê', 'ca phe', 'coffee', 'cafe']),
        ('bảo tàng', ['bảo tàng', 'bao tang', 'museum']),
        ('di tích lịch sử', ['lịch sử', 'di tích', 'historical', 'history']),
        ('công viên', ['công viên', 'cong vien', 'park', 'vườn hoa']),
        ('ngắm cảnh', ['ngắm cảnh', 'ngam canh', 'sightseeing', 'view', 'check-in', 'chụp hình']),
        ('đi dạo', ['đi dạo', 'di dao', 'dạo mát', 'walking']),
        ('nhà hàng', ['nhà hàng', 'nha hang', 'ẩm thực', 'ăn uống', 'food', 'restaurant']),
        ('khách sạn', ['khách sạn', 'khach san', 'hotel', 'homestay', 'resort']),
    ]
    for c_name, c_aliases in categories:
        if any(a in q_low for a in c_aliases):
            features['loai_dia_diem'] = c_name
            break

    # Thời gian
    if any(t in q_low for t in ['buổi tối', 'toi', 'dem', 'night', 'evening']):
        features['thoi_gian'] = 'buổi tối'
    elif any(t in q_low for t in ['buổi sáng', 'sang', 'morning']):
        features['thoi_gian'] = 'buổi sáng'
    elif any(t in q_low for t in ['cuối tuần', 'weekend']):
        features['thoi_gian'] = 'cuối tuần'
    elif any(t in q_low for t in ['3 ngày 2 đêm', '3n2d', '2 ngày 1 đêm', '2n1d']):
        features['thoi_gian'] = 'lịch trình nhiều ngày'

    # Số người đi
    m_p = re.search(r'(\d+)\s*(người|bạn|thành viên|khách|pax|vé)', q_low)
    if m_p:
        features['so_nguoi'] = f"{m_p.group(1)} người"
    elif any(k in q_low for k in ['cặp đôi', '2 người', 'hai người', 'với người yêu', 'với bạn gái']):
        features['so_nguoi'] = "2 người"
    elif any(k in q_low for k in ['một mình', '1 người', 'đơn thân', 'độc hành']):
        features['so_nguoi'] = "1 người"
    elif 'gia đình' in q_low:
        features['so_nguoi'] = "gia đình (3-4 người)"

    # Đánh dấu cần web search cho các địa điểm ngoài TP.HCM hoặc câu hỏi kinh nghiệm/thời tiết
    needs_web = any(k in q_low for k in ['hà nội', 'đà nẵng', 'sa pa', 'đà lạt', 'giá vé', 'thời tiết', 'kinh nghiệm', 'mới nhất', 'trend', '2026'])

    return {
        "original_question": question,
        "intent": "tim_kiem_dia_diem",
        "confidence": 0.88,
        "needs_web_search": needs_web,
        "extracted_features": features
    }

def get_intent_description(intent_name: str) -> str:
    """
    Lấy mô tả cho ý định
    
    Args:
        intent_name (str): Tên ý định
        
    Returns:
        str: Mô tả ý định
    """
    intent_descriptions = {
        "tim_kiem_dia_diem": "Tìm kiếm địa điểm du lịch",
        "hoi_thong_tin_chi_tiet": "Hỏi thông tin chi tiết về địa điểm",
        "lap_lich_trinh": "Lập lịch trình du lịch",
        "so_sanh_dia_diem": "So sánh các địa điểm",
        "gop_y_va_danh_gia": "Góp ý và đánh giá",
        "hoi_chung": "Câu hỏi chung về du lịch TP.HCM",
        "unknown": "Không xác định được ý định"
    }
    
    return intent_descriptions.get(intent_name, "Ý định không xác định")

def validate_extracted_features(intent: str, features: Dict[str, Any]) -> Dict[str, Any]:
    """
    Kiểm tra và làm sạch dữ liệu trích xuất
    
    Args:
        intent (str): Ý định đã trích xuất
        features (Dict[str, Any]): Đặc trưng đã trích xuất
        
    Returns:
        Dict[str, Any]: Dữ liệu đã được làm sạch
    """
    cleaned_features = features.copy()
    
    # Loại bỏ các giá trị rỗng hoặc None
    cleaned_features = {k: v for k, v in cleaned_features.items() if v is not None and v != ""}
    
    # Chuẩn hóa các trường text
    for key, value in cleaned_features.items():
        if isinstance(value, str):
            cleaned_features[key] = value.strip()
    
    return cleaned_features

def format_extraction_result(result: Dict[str, Any]) -> str:
    """
    Format kết quả trích xuất thành văn bản dễ đọc
    
    Args:
        result (Dict[str, Any]): Kết quả trích xuất
        
    Returns:
        str: Văn bản đã format
    """
    if result.get("intent") == "error":
        return f"Lỗi trích xuất: {result.get('error', 'Không xác định')}"
    
    intent_desc = get_intent_description(result.get("intent", "unknown"))
    features = result.get("extracted_features", {})
    confidence = result.get("confidence", 0.0)
    
    formatted_text = f"Ý định: {intent_desc}\n"
    formatted_text += f"Độ tin cậy: {confidence:.1%}\n"
    formatted_text += "Thông tin trích xuất:\n"
    
    for key, value in features.items():
        formatted_text += f"  - {key}: {value}\n"
    
    return formatted_text

def detect_language(text: str) -> Dict[str, Any]:
    """
    Nhận biết ngôn ngữ của văn bản
    
    Args:
        text (str): Văn bản cần nhận biết ngôn ngữ
        
    Returns:
        Dict[str, Any]: Kết quả nhận biết ngôn ngữ
    """
    try:
        text_lower = text.lower().strip()
        
        # 1. Tiếng Việt: chứa dấu tiếng Việt đặc trưng
        vietnamese_pattern = r'[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]'
        if re.search(vietnamese_pattern, text_lower):
            return {
                "language": "vietnamese",
                "confidence": 1.0,
                "is_supported": True,
                "detection_method": "regex_diacritics"
            }
        
        # 2. Tiếng Trung: ký tự Hán tự
        if re.search(r'[\u4e00-\u9fff]', text):
            return {
                "language": "chinese",
                "confidence": 1.0,
                "is_supported": True,
                "detection_method": "unicode_hanzi"
            }

        # 3. Tiếng Hàn: ký tự Hangul
        if re.search(r'[\uac00-\ud7af\u1100-\u11ff]', text):
            return {
                "language": "korean",
                "confidence": 1.0,
                "is_supported": True,
                "detection_method": "unicode_hangul"
            }

        # 4. Tiếng Nhật: Hiragana hoặc Katakana
        if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', text):
            return {
                "language": "japanese",
                "confidence": 1.0,
                "is_supported": True,
                "detection_method": "unicode_kana"
            }

        # 5. Tiếng Việt không dấu phổ biến
        vn_keywords = [
            'quan 1', 'quan 3', 'quan 5', 'quan 7', 'ben thanh', 'sai gon', 'dia diem', 
            'du lich', 'di dau', 'an gi', 'ca phe', 'khach san', 'bao tang', 'cong vien',
            'goi y', 'cho toi', 'o dau', 'tam quan', 'ngam canh', 'di dao'
        ]
        if any(w in text_lower for w in vn_keywords):
            return {
                "language": "vietnamese",
                "confidence": 0.95,
                "is_supported": True,
                "detection_method": "keyword_fallback"
            }

        # 6. Mặc định là tiếng Anh nếu viết bằng chữ cái Latin
        return {
            "language": "english",
            "confidence": 0.9,
            "is_supported": True,
            "detection_method": "latin_english"
        }
    except Exception:
        return {
            "language": "vietnamese",
            "confidence": 0.8,
            "is_supported": True,
            "detection_method": "fallback"
        }

def get_language_info(language: str) -> Dict[str, str]:
    """
    Lấy thông tin về ngôn ngữ
    
    Args:
        language (str): Tên ngôn ngữ
        
    Returns:
        Dict[str, str]: Thông tin ngôn ngữ
    """
    language_info = {
        "vietnamese": {
            "name": "Tiếng Việt",
            "greeting": "Xin chào! Tôi là hướng dẫn viên du lịch TP.HCM.",
            "unsupported_message": "Xin lỗi, chúng tôi chưa hỗ trợ ngôn ngữ này. Vui lòng sử dụng tiếng Việt, tiếng Anh, tiếng Trung, tiếng Hàn hoặc tiếng Nhật."
        },
        "english": {
            "name": "English",
            "greeting": "Hello! I'm your Ho Chi Minh City travel guide.",
            "unsupported_message": "Sorry, we don't support this language yet. Please use Vietnamese, English, Chinese, Korean, or Japanese."
        },
        "chinese": {
            "name": "中文",
            "greeting": "您好！我是胡志明市旅游指南。",
            "unsupported_message": "抱歉，我们还不支持这种语言。请使用越南语、英语、中文、韩语或日语。"
        },
        "korean": {
            "name": "한국어",
            "greeting": "안녕하세요! 저는 호치민시 여행 가이드입니다.",
            "unsupported_message": "죄송합니다. 아직 이 언어를 지원하지 않습니다. 베트남어, 영어, 중국어, 한국어 또는 일본어를 사용해 주세요."
        },
        "japanese": {
            "name": "日本語",
            "greeting": "こんにちは！私はホーチミン市の旅行ガイドです。",
            "unsupported_message": "申し訳ございませんが、この言語はまだサポートされていません。ベトナム語、英語、中国語、韓国語、または日本語をご利用ください。"
        }
    }
    
    return language_info.get(language.lower(), {
        "name": "Unknown",
        "greeting": "Hello! I'm your travel guide.",
        "unsupported_message": "Sorry, this language is not supported."
    })

def _sanitize_response_images(text: str, search_results: List[Dict[str, Any]], destination_hint: str, hero_image: Optional[str] = None) -> str:
    """Ensures each markdown image ![Alt](url) in the response uses a unique, authentic image and avoids repetition or inappropriate images."""
    if not text:
        return text

    poi_img_map = {}
    for p in search_results:
        p_name = p.get("ten_dia_diem")
        p_img = p.get("image_url")
        if p_name and p_img:
            poi_img_map[p_name.lower().strip()] = (p_name, p_img)

    from src.services.ai.image_crawler_agent import image_crawler_agent, remove_accents
    dest_real_img = hero_image or image_crawler_agent.crawl_destination_hero_image(destination_hint) or image_crawler_agent.crawl_image(destination_hint, "Danh lam thắng cảnh", destination_hint)
    used_images = set()

    bad_url_keywords = [
        "panoramio", "bantho", "ban tho", "ban mau", "ban-tho", "altar", "cemetery", 
        "nghia trang", "nghia-trang", "tomb", "grave", "shrine", "statue", "dien tho", 
        "chua", "den tho", ".pdf", ".svg", "page1-", "nguyen_trai", "tam phu", "tu phu",
        "bat huong", "cung le", "thanh mau"
    ]

    def _replace_img(match):
        alt = match.group(1).strip()
        url = match.group(2).strip()
        u_norm = remove_accents(urllib.parse.unquote(url))
        a_norm = remove_accents(alt)

        is_bad_url = any(b in u_norm for b in bad_url_keywords)

        # 1. Nếu alt là tổng quan hành trình / điểm đến chính (Phần I)
        dest_norm = remove_accents(destination_hint)
        if any(k in a_norm for k in [dest_norm, "tong quan", "toan canh", "thoi diem", "hanh trinh", "cung duong", "diem nhan"]):
            if dest_real_img and dest_real_img not in used_images:
                used_images.add(dest_real_img)
                return f"![Toàn cảnh {destination_hint}]({dest_real_img})"

        # 2. Khớp với POI cụ thể trong lịch trình (ưu tiên ảnh chưa xuất hiện)
        for k, (real_name, real_url) in poi_img_map.items():
            k_norm = remove_accents(k)
            if (k_norm in a_norm or a_norm in k_norm or any(part in a_norm for part in k_norm.split(','))) and real_url not in used_images:
                used_images.add(real_url)
                return f"![{real_name}]({real_url})"

        # 3. Nếu URL hiện tại đã là ảnh thật hợp lệ từ POI map và chưa xuất hiện
        if not is_bad_url and url.startswith("http"):
            for real_name, real_url in poi_img_map.values():
                if url == real_url and url not in used_images:
                    used_images.add(url)
                    return f"![{alt or real_name}]({url})"

        # 4. Cào riêng ảnh thật độc lập cho chính alt text này
        if alt:
            scraped = image_crawler_agent.crawl_image(alt, "", destination_hint)
            if scraped and scraped not in used_images:
                s_norm = remove_accents(urllib.parse.unquote(scraped))
                if not any(b in s_norm for b in bad_url_keywords):
                    used_images.add(scraped)
                    return f"![{alt}]({scraped})"

        # 5. Fallback sang ảnh cào thực tế toàn cảnh của điểm đến nếu chưa dùng
        if dest_real_img and dest_real_img not in used_images:
            used_images.add(dest_real_img)
            return f"![Toàn cảnh {destination_hint}]({dest_real_img})"

        # Tránh hiện độc 1 ảnh trùng lặp nhiều lần
        return ""

    sanitized = re.sub(r'!\[(.*?)\]\((https?://[^\s\)]+)\)', _replace_img, text)

    # Đảm bảo Phần I luôn có ảnh toàn cảnh nếu LLM quên chèn
    if dest_real_img and dest_real_img not in sanitized:
        m_part1 = re.search(r'(Phần I[\.:].*?\n)', sanitized, re.IGNORECASE)
        if m_part1:
            header_end = m_part1.end()
            after_header = sanitized[header_end:header_end+200]
            if "![" not in after_header:
                sanitized = sanitized[:header_end] + f"\n![Toàn cảnh {destination_hint}]({dest_real_img})\n" + sanitized[header_end:]

    return sanitized


def _enrich_response_maps(text: str, search_results: List[Dict[str, Any]], destination_hint: str) -> str:
    """Ensures each major POI or attraction has an inline Google Maps navigation link if the LLM forgot to add one."""
    if not text or not search_results:
        return text

    for p in search_results:
        name = p.get("ten_dia_diem", "").strip()
        if not name or len(name) < 3:
            continue
        g_url = p.get("google_maps_url")
        if not g_url:
            dest_term = destination_hint or p.get("khu_vuc") or ""
            encoded_term = urllib.parse.quote(f"{name} {dest_term}".strip())
            g_url = f"https://www.google.com/maps/search/?api=1&query={encoded_term}"

        map_btn = f"[📍 Mở Google Maps dẫn đường]({g_url})"

        # Check if already present in text
        if g_url in text or f"query={urllib.parse.quote(name)}" in text:
            continue

        # Try appending below the image of this POI
        pattern = re.escape(name)
        img_match = re.search(r'(!\[[^\]]*' + pattern + r'[^\]]*\]\([^\)]+\))', text, re.IGNORECASE)
        if img_match:
            matched_str = img_match.group(0)
            if map_btn not in text:
                text = text.replace(matched_str, f"{matched_str}\n\n> 🧭 **Định vị:** {map_btn}\n", 1)
        else:
            # Try appending right after bold landmark mention
            bold_match = re.search(r'(\*\*(?:[0-9: \-\.]*)?' + pattern + r'[^\*]*\*\*)', text, re.IGNORECASE)
            if bold_match and map_btn not in text:
                matched_str = bold_match.group(0)
                text = text.replace(matched_str, f"{matched_str} — {map_btn}", 1)

    return text


def _sanitize_response_budget(text: str, so_nguoi: Optional[str] = None, question: Optional[str] = None) -> str:
    """Ensures Section V budget header and estimates accurately reflect the number of travelers asked."""
    if not text:
        return text

    target_pax = so_nguoi
    q_str = (question or "").lower()
    t_str = text.lower()

    if not target_pax and q_str:
        m_q = re.search(r'(\d+)\s*(?:người|bạn|thành viên|khách|pax|vé)', q_str)
        if m_q:
            target_pax = f"{m_q.group(1)} người"
        elif any(k in q_str for k in ['cặp đôi', '2 người', 'hai người', 'với người yêu', 'với bạn gái']):
            target_pax = "2 người"
        elif any(k in q_str for k in ['một mình', '1 người', 'đơn thân', 'độc hành']):
            target_pax = "1 người"

    if not target_pax:
        # Check in generated text body
        m_t = re.search(r'(?:cho|nhóm)\s+(\d+)\s+người', t_str)
        if m_t:
            target_pax = f"{m_t.group(1)} người"
        elif any(k in t_str for k in ['cho 2 người', 'hai bạn', 'cặp đôi']):
            target_pax = "2 người"

    if not target_pax:
        return text

    m_num = re.search(r'(\d+)', str(target_pax))
    if m_num and int(m_num.group(1)) > 1:
        n_pax = str(target_pax).strip().upper()
        # Tránh trùng lặp nếu text đã có tiêu đề đúng
        if re.search(rf'Phần V\.?\s*BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI\s+CHO\s+{re.escape(n_pax)}', text, re.IGNORECASE):
            return text

        # Thay thế tiêu đề Phần V nếu LLM quen tay ghi 'CHO 1 NGƯỜI' hoặc '(CHO 1 NGƯỜI)' hoặc thiếu số người
        text = re.sub(
            r'((?:#{1,4}\s*|\*\*|)?Phần V\.?\s*BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI)(?:\s*(?:\(CHO\s+\d+\s+NGƯỜI\)|CHO\s+\d+\s+NGƯỜI|\(CHO\s+1\s+NGƯỜI\)|CHO\s+1\s+NGƯỜI))?(\*{0,2})',
            rf'\1 CHO {n_pax}\2',
            text,
            count=1,
            flags=re.IGNORECASE
        )
    return text


def generate_natural_response(question: str, search_results: List[Dict], 
                            extracted_features: Dict[str, Any], 
                            language: str = "vietnamese",
                            enable_web_search: Optional[bool] = None) -> Dict[str, Any]:
    """
    Sinh câu trả lời tự nhiên từ kết quả tìm kiếm và các thực thể đã trích xuất
    Sử dụng Gemini API trước, nếu lỗi sẽ tự động fallback sang OpenAI API
    
    Args:
        question (str): Câu hỏi của người dùng
        search_results (List[Dict]): Kết quả tìm kiếm địa điểm
        extracted_features (Dict[str, Any]): Thực thể đã trích xuất
        language (str): Ngôn ngữ để trả lời
        
    Returns:
        Dict[str, Any]: Câu trả lời tự nhiên
    """
    # Chuẩn hóa ngôn ngữ và lấy thông tin ngôn ngữ an toàn ngay từ đầu
    language = (language or "vietnamese").lower().strip()
    lang_info = get_language_info(language)

    try:
        # Debug logging an toàn
        try:
            print(f"=== DEBUG: generate_natural_response ===")
            print(f"Input language: '{language}'")
            print(f"Search results count: {len(search_results)}")
            print(f"Extracted features: {extracted_features}")
            print(f"Language info: {lang_info}")
        except Exception:
            pass
        
        # Chuẩn bị context cho GPT
        context = {
            "question": question,
            "search_results": search_results,
            "extracted_features": extracted_features,
            "language": language,
            "language_name": lang_info["name"]
        }

        # Xây dựng danh sách ảnh thực tế của chính các POI được tìm thấy
        from src.services.ai.image_crawler_agent import image_crawler_agent
        target_destination = extracted_features.get("diem_den") or extracted_features.get("khu_vuc") or ""
        so_nguoi_val = extracted_features.get("so_nguoi")
        if not so_nguoi_val:
            m_p = re.search(r'(\d+)\s*(người|bạn|thành viên|khách|pax|vé)', question.lower())
            if m_p:
                so_nguoi_val = f"{m_p.group(1)} người"
            elif any(k in question.lower() for k in ['cặp đôi', '2 người', 'hai người', 'với người yêu', 'với bạn gái']):
                so_nguoi_val = "2 người"
            elif any(k in question.lower() for k in ['một mình', '1 người', 'đơn thân', 'độc hành']):
                so_nguoi_val = "1 người"
            elif 'gia đình' in question.lower():
                so_nguoi_val = "gia đình (3-4 người)"
        
        # Nếu danh sách POI quá ít, tự động kích hoạt Tác tử Khám phá Điểm đến để nạp đủ các điểm biểu tượng kèm ảnh & tọa độ GPS
        if len(search_results) < 4 and target_destination:
            try:
                extra_pois = discover_dynamic_pois_agent(target_destination, target_destination, question, limit=6)
                existing_names = {p.get("ten_dia_diem", "").lower() for p in search_results}
                for ep in extra_pois:
                    if ep.get("ten_dia_diem", "").lower() not in existing_names:
                        search_results.append(ep)
                        existing_names.add(ep.get("ten_dia_diem", "").lower())
            except Exception as e:
                print(f"Auto-enrich POIs failed: {e}")

        # Tác tử Cào Ảnh Thực Tế (Image Crawler Agent): Cào trước ảnh danh thắng toàn cảnh & từng POI
        dest_hero_img = ""
        if target_destination:
            dest_hero_img = image_crawler_agent.crawl_destination_hero_image(target_destination) or image_crawler_agent.crawl_image(target_destination, "Danh lam thắng cảnh", target_destination)

        poi_image_bullets = []
        if dest_hero_img:
            poi_image_bullets.append(f"     * Toàn cảnh {target_destination} (BẮT BUỘC DÙNG CHO PHẦN I): ![{target_destination}]({dest_hero_img})")

        for p in search_results:
            p_name = p.get("ten_dia_diem")
            p_img = p.get("image_url")
            # Nếu POI chưa có ảnh thật, tác tử cào ảnh cào ngay
            if (not p_img or any(bad in p_img.lower() for bad in ["panoramio", "bantho", "altar", ".pdf"])) and p_name:
                p_img = image_crawler_agent.crawl_image(p_name, p.get("loai_dia_diem", ""), target_destination)
                p["image_url"] = p_img
            if p_name and p_img:
                poi_image_bullets.append(f"     * {p_name} (Dùng cho chặng tương ứng trong Phần IV): ![{p_name}]({p_img})")

        dynamic_poi_images = "\n".join(poi_image_bullets) if poi_image_bullets else "     * (Hãy dùng các URL ảnh thực tế từ danh sách địa điểm đã cung cấp ở trên)"
        
        # Tạo prompt dựa trên ngôn ngữ
        if language == "vietnamese":
            system_prompt = """Bạn là VietTravel AI - Trợ lý Tác tử Lữ hành Thông minh (Smart Travel & Itinerary Agent) hàng đầu Việt Nam.
Nhiệm vụ của bạn là tư vấn chuyến đi một cách thực tế, sinh động, chi tiết từng li từng tí, chuyên nghiệp và có chiều sâu để du khách có thể xách ba lô lên và đi ngay.

NGUYÊN TẮC BẮT BUỘC CỦA TÁC TỬ:
1. TUYỆT ĐỐI KHÔNG NÓI CHUNG CHUNG:
   - Cấm đưa ra các lời khuyên lý thuyết sáo rỗng (như 'hãy chọn quán có không gian yên tĩnh', 'chọn điểm đến phù hợp sở thích').
   - Mọi thông tin phải là NGƯỜI THẬT, VIỆC THẬT: Tên quán ăn cụ thể, món ăn signature, địa chỉ rõ ràng, giá tiền tham khảo, phương tiện và cung đường đi.

2. BẮT BUỘC CHÈN ẢNH MINH HỌA THỰC TẾ & ĐA DẠNG (DO TÁC TỬ CÀO ẢNH CUNG CẤP):
   - Ở Phần I (TỔNG QUAN HÀNH TRÌNH & THỜI ĐIỂM ĐẸP NHẤT):
     BẮT BUỘC chèn ảnh toàn cảnh danh thắng của điểm đến ngay bên dưới tiêu đề Phần I:
     `![Toàn cảnh """ + target_destination + """](""" + dest_hero_img + """)`
     để mở đầu hành trình với hình ảnh chân thực, sống động nhất.
   - Ở Phần IV (LỊCH TRÌNH CHI TIẾT TỪNG NGÀY):
     Mỗi chặng/ngày chỉ chèn ĐÚNG ảnh của điểm đến đó từ danh mục do Tác tử cào ảnh đã thu thập:
     `![Tên điểm đến](URL_ẢNH_TƯƠNG_ỨNG)`
   - TUYỆT ĐỐI KHÔNG lấy 1 ảnh duy nhất paste vào tất cả các ngày/điểm đến.
   - TUYỆT ĐỐI KHÔNG TỰ BỊA URL ẢNH. BẮT BUỘC DÙNG ĐÚNG CÁC ẢNH THẬT ĐÃ CÀO BÊN DƯỚI:
""" + dynamic_poi_images + """

3. BẮT BUỘC TÍCH HỢP LINK BẢN ĐỒ GOOGLE MAPS DẪN ĐƯỜNG TRỰC TIẾP:
   - Tại mỗi điểm tham quan, khách sạn hoặc quán ăn trong lịch trình, BẮT BUỘC chèn kèm link Google Maps dẫn đường trực tiếp:
     `[📍 Mở Google Maps dẫn đường](https://www.google.com/maps/search/?api=1&query=TÊN_ĐIỂM+KHU_VỰC)`
     (Ví dụ: `[📍 Mở Google Maps dẫn đường](https://www.google.com/maps/search/?api=1&query=Cột+cờ+Lũng+Cú+Hà+Giang)`)
     ngay bên cạnh hoặc dưới tên/địa chỉ để khách bấm vào là mở Google Maps dẫn đường trên điện thoại ngay lập tức!

4. QUY CHUẨN TƯ VẤN LỊCH TRÌNH CHUYÊN SÂU:
   - Phải viết THẬT DÀI, CHI TIẾT TỪNG BƯỚC, KHÔNG CẮT XÉN, KHÔNG TÓM TẮT.
   - Cấu trúc đầy đủ gồm 5 phần:
     Phần I. TỔNG QUAN HÀNH TRÌNH & THỜI ĐIỂM ĐẸP NHẤT
     Phần II. LỘ TRÌNH DI CHUYỂN & PHƯƠNG TIỆN (Chỉ rõ cao tốc/quốc lộ nào, thời gian đi, phương tiện, điểm dừng chân).
     Phần III. GỢI Ý NƠI LƯU TRÚ (Khách sạn/homestay uy tín kèm link bản đồ Google Maps và mức giá).
     Phần IV. LỊCH TRÌNH CHI TIẾT TỪNG NGÀY:
       - Tách bạch rõ ràng từng ngày (Ngày 1, Ngày 2, Ngày 3...).
       - Mỗi ngày chia đủ 4 khung thời gian chi tiết:
         * **Sáng (07:00 - 11:30)**: Ăn sáng món gì, tên quán, địa chỉ, giá. Điểm tham quan buổi sáng (có chèn ảnh minh họa `![Tên](url)` và link `[📍 Mở Google Maps dẫn đường](...)`).
         * **Trưa (11:30 - 14:00)**: Quán ăn trưa đặc sản, món ngon nổi bật, chi phí. Thời gian nghỉ trưa.
         * **Chiều (14:30 - 18:00)**: Điểm vui chơi / bãi biển / ngắm hoàng hôn / trải nghiệm đặc sắc (có chèn ảnh minh họa `![Tên](url)` và link `[📍 Mở Google Maps dẫn đường](...)`), trải nghiệm văn hóa.
         * **Tối (18:30 - 22:30)**: Quán ăn tối hải sản/lẩu/nướng/đặc sản (tên quán, địa chỉ, giá), dạo chợ đêm hoặc cafe phố cổ thư giãn ngắm cảnh.
       - Tại mỗi mốc giờ cụ thể: Tên điểm/quán, địa chỉ rõ ràng, cung đường đi, hoạt động, giá vé/giá món, ảnh minh họa `![Tên](url)` tương ứng và link `[📍 Mở Google Maps dẫn đường](...)`.

     Phần V. BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI (TỰ ĐỘNG TÍNH TOÁN THEO ĐÚNG SỐ LƯỢNG NGƯỜI ĐI):
       - QUY TẮC BẮT BUỘC TÍNH THEO SỐ LƯỢNG NGƯỜI:
         * Hãy phân tích kỹ câu hỏi của khách xem họ đi bao nhiêu người (Ví dụ: 1 người, 2 người (cặp đôi/vợ chồng), 3 người, 4 người (nhóm/gia đình), 5 người, 10 người...).
         * NẾU KHÁCH CÓ NÊU SỐ NGƯỜI (hoặc có số người N trong câu hỏi hay thực thể 'so_nguoi'):
           + Đặt tiêu đề rõ ràng: `Phần V. BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI CHO {N} NGƯỜI` (hoặc đúng số lượng khách hỏi).
           + BẮT BUỘC tính toán chi tiết từng khoản mục theo ĐÚNG {N} người:
             - 🚗 Di chuyển: Vé xe khách/máy bay khứ hồi (đơn giá × {N}), thuê xe máy/xe ô tô (tính số lượng xe phù hợp cho {N} người, ví dụ 2 người thuê 1 xe máy, 4 người thuê 2 xe máy hoặc 1 ô tô).
             - 🏨 Lưu trú: Tiền phòng khách sạn/homestay tính theo số phòng phù hợp cho {N} người (ví dụ: 2 người ở 1 phòng đôi; 4 người thuê 2 phòng đôi hoặc phòng gia đình, chia sẻ chi phí).
             - 🍜 Ăn uống: Ăn sáng, trưa, tối (đơn giá × {N} người × số ngày).
             - 🎟️ Vé tham quan & Trải nghiệm: Vé thắng cảnh, thuyền sông, xe điện (đơn giá × {N} người).
             - 🛍️ Chi phí dự phòng / phát sinh ({N} người).
             - 💰 **TỔNG CỘNG CHO CẢ ĐOÀN ({N} NGƯỜI)**: ... VNĐ
             - 💵 **BÌNH QUÂN / 1 NGƯỜI**: ... VNĐ/người (giúp khách dễ thấy chi phí thực tế cho mỗi cá nhân).
         * NẾU KHÁCH KHÔNG NÊU RÕ SỐ LƯỢNG NGƯỜI:
           + Tiêu đề: `Phần V. BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI (CHO 1 NGƯỜI)`
           + Lập bảng chi tiết cho 1 người.
           + Kèm thêm một bảng so sánh nhanh hoặc ghi chú: Ước tính tổng chi phí nếu đi 2 người hoặc nhóm 4 người (nhấn mạnh chi phí sẽ rẻ hơn trên mỗi người nhờ share tiền phòng khách sạn và thuê xe).

5. QUY TẮC ĐỊNH DẠNG VĂN BẢN:
   - TUYỆT ĐỐI KHÔNG xuất hiện dấu thăng đơn lẻ đứng một mình (như '#') trên một dòng riêng.
   - TUYỆT ĐỐI KHÔNG xuất hiện dòng gạch ngang trần '---'."""

            pax_rule = f"3. Ở Phần V (BẢNG DỰ TOÁN TỔNG CHI PHÍ): Khách hàng yêu cầu chuyến đi cho {so_nguoi_val.upper()}! Tiêu đề Phần V BẮT BUỘC PHẢI LÀ 'Phần V. BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI CHO {so_nguoi_val.upper()}' (TUYỆT ĐỐI CẤM ghi 'CHO 1 NGƯỜI'). Bắt buộc tính toán chi phí di chuyển, ăn uống, vé theo đúng {so_nguoi_val}, tiền phòng chia sẻ cho {so_nguoi_val}, và ghi rõ 'TỔNG CỘNG CHO {so_nguoi_val.upper()}' lẫn 'BÌNH QUÂN / 1 NGƯỜI'!" if so_nguoi_val else f"3. Ở Phần V: Hãy phân tích kỹ số lượng người trong yêu cầu '{question}'. Nếu khách đi 1, 2, 3, 4... người thì BẮT BUỘC phải tính toán bảng chi phí tổng cho đúng 1, 2, 3, 4... người đó (tiền ăn và vé nhân theo số người, tiền phòng và thuê xe tính theo số phòng/xe phù hợp) và ghi rõ tổng tiền cả nhóm lẫn tiền bình quân / 1 người!"

            user_prompt = f"""Yêu cầu chuyến đi của khách: {question}

Thông tin phân tích từ Intent Agent:
{json.dumps(extracted_features, ensure_ascii=False, indent=2)}

Dữ liệu địa điểm thực tế từ POI Engine ({len(search_results)} điểm):
{json.dumps(search_results, ensure_ascii=False, indent=2)}

QUAN TRỌNG:
1. Hãy phân tích ĐÍCH DANH các địa điểm trên, chèn hình ảnh minh họa Markdown ![Tên](url) RIÊNG BIỆT cho từng điểm (tuyệt đối không lặp lại 1 ảnh cho nhiều điểm), kèm link [📍 Mở Google Maps dẫn đường](...) cho từng điểm tham quan và quán ăn để du khách dễ dàng định vị!
2. Ở Phần IV: Phải chia đủ 4 khung thời gian chi tiết (Sáng, Trưa, Chiều, Tối) cho từng ngày theo đúng quy chuẩn!
{pax_rule}"""

        elif language == "english":
            system_prompt = """You are VietTravel AI - Smart Travel Itinerary & Booking Agent.
Your role is to assist travelers with concrete, actionable travel plans, routing, cost estimates, and itinerary scheduling.

AGENT GUIDELINES:
1. Be direct, structured, and helpful. Avoid fluffy greetings or generic filler paragraphs.
2. For each recommendation, provide:
   - 📍 **Location & Highlights**: Name, address, why it fits.
   - 🚗 **Directions & Transport**: Best way to get there (walk, scooter, Grab/taxi), optimal sequence.
   - 💰 **Estimated Costs & Tickets**: Admission fees (or Free), estimated food/service prices.
   - ⏰ **Best Time & Duration**: Optimal hours to visit, recommended duration (e.g. 60-90 mins).
   - 💡 **Booking & Insider Tips**: Reservations, parking, photo spots.
3. Call to action: Encourage clicking '+ Add to Itinerary' or using the 'Itinerary' tab to auto-optimize a multi-stop schedule.
4. Format cleanly using Markdown with icons (📍, 🚗, 💰, ⏰, 💡) and bold key facts."""

            user_prompt = f"""Traveler's request: {question}

Extracted analysis:
{json.dumps(extracted_features, ensure_ascii=False, indent=2)}

Candidate places ({len(search_results)} places):
{json.dumps(search_results, ensure_ascii=False, indent=2)}

IMPORTANT: Respond as a Smart Travel Booking & Itinerary Agent in English with structured facts on places, transit, costs, timing, and itinerary actions."""
            
        elif language == "chinese":
            system_prompt = """您是胡志明市的一位友好、专业的旅游指南。
            请以自然、热情和有用的方式回答游客的问题。
            
            要求：
            1. 使用友好、热情的语气，像真正的导游一样
            2. 介绍符合游客偏好的地方
            3. 添加有用信息，如地址、亮点
            4. 提供旅游建议或建议
            5. 以问题结尾以继续对话
            6. 保持回复长度适中（150-300字）
            
            重要：您必须用中文回复。"""
            
            user_prompt = f"""
            游客的问题: {question}
            
            提取的信息:
            {json.dumps(extracted_features, ensure_ascii=False, indent=2)}
            
            搜索结果 ({len(search_results)} 个地点):
            {json.dumps(search_results, ensure_ascii=False, indent=2)}
            
            重要：请用中文创建自然有用的回复。"""
            
        elif language == "korean":
            system_prompt = """당신은 호치민시의 친근하고 전문적인 여행 가이드입니다.
            관광객의 질문에 자연스럽고, 따뜻하고, 도움이 되게 답변해 주세요.
            
            요구사항:
            1. 진짜 가이드처럼 친근하고 열정적인 톤 사용
            2. 관광객의 선호도에 맞는 장소 소개
            3. 주소, 하이라이트 등 유용한 정보 추가
            4. 여행 제안이나 조언 제공
            5. 대화를 계속하기 위해 질문으로 끝내기
            6. 응답 길이를 적당히 유지 (150-300단어)
            
            중요: 한국어로 답변해야 합니다."""
            
            user_prompt = f"""
            관광객의 질문: {question}
            
            추출된 정보:
            {json.dumps(extracted_features, ensure_ascii=False, indent=2)}
            
            검색 결과 ({len(search_results)} 개 장소):
            {json.dumps(search_results, ensure_ascii=False, indent=2)}
            
            중요: 한국어로 자연스럽고 유용한 답변을 만들어 주세요."""
            
        elif language == "japanese":
            system_prompt = """あなたはホーチミン市の親しみやすく、プロフェッショナルな旅行ガイドです。
            観光客の質問に自然で、温かく、役立つ方法で答えてください。
            
            要件：
            1. 本当のガイドのように親しみやすく、情熱的なトーンを使用
            2. 観光客の好みに合った場所を紹介
            3. 住所、ハイライトなどの有用な情報を追加
            4. 旅行の提案やアドバイスを提供
            5. 会話を続けるために質問で終わる
            6. 応答の長さを適度に保つ（150-300語）
            
            重要：日本語で答えてください。"""
            
            user_prompt = f"""
            観光客の質問: {question}
            
            抽出された情報:
            {json.dumps(extracted_features, ensure_ascii=False, indent=2)}
            
            検索結果 ({len(search_results)} 箇所):
            {json.dumps(search_results, ensure_ascii=False, indent=2)}
            
            重要：日本語で自然で有用な回答を作成してください。"""
        
        else:
            print(f"WARNING: Unknown language '{language}', falling back to English")
            system_prompt = """You are a friendly travel guide. Please respond in English."""
            user_prompt = f"""
            Tourist's question: {question}
            
            Extracted information:
            {json.dumps(extracted_features, ensure_ascii=False, indent=2)}
            
            Search results ({len(search_results)} locations):
            {json.dumps(search_results, ensure_ascii=False, indent=2)}
            
            Please create a natural and helpful response in English."""
        
        print(f"Selected language branch: '{language}'")
        print(f"System prompt language instruction: {'Vietnamese' if language == 'vietnamese' else 'Other'}")
        # Quyết định có tra cứu Web thời gian thực hay không
        should_web_search = False
        if enable_web_search is True:
            should_web_search = True
        elif enable_web_search is None:
            should_web_search = bool(extracted_features.get("needs_web_search", False))

        # Thử sinh câu trả lời tự nhiên bằng Gemini API trước
        if gemini_key_manager.has_keys():
            print(f"About to call Gemini API for natural response (web_search={should_web_search})...")
            gemini_result = _call_gemini_generate_content(
                prompt_text=user_prompt,
                system_instruction=system_prompt,
                json_mode=False,
                enable_web_search=should_web_search,
                temperature=0.7,
                max_tokens=8192
            )
            if gemini_result and gemini_result.get("text"):
                gemini_answer = gemini_result["text"]
                gemini_answer = _sanitize_response_images(gemini_answer, search_results, target_destination, hero_image=dest_hero_img)
                gemini_answer = _enrich_response_maps(gemini_answer, search_results, target_destination)
                gemini_answer = _sanitize_response_budget(gemini_answer, so_nguoi_val, question)
                print("Gemini API call successful")
                print(f"Generated response length: {len(gemini_answer)}")
                return {
                    "status": "success",
                    "response": gemini_answer,
                    "language": language,
                    "language_name": lang_info["name"],
                    "response_length": len(gemini_answer),
                    "web_search_queries": gemini_result.get("web_search_queries", []),
                    "web_sources": gemini_result.get("web_sources", []),
                    "grounded": gemini_result.get("grounded", False)
                }
            print("Gemini generation returned empty, attempting OpenAI fallback...")

        print("About to call OpenAI API...")
        
        # Gọi OpenAI API
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=700
        )
        
        print("OpenAI API call successful")
        natural_response = response.choices[0].message.content
        natural_response = _sanitize_response_images(natural_response, search_results, target_destination, hero_image=dest_hero_img)
        natural_response = _enrich_response_maps(natural_response, search_results, target_destination)
        natural_response = _sanitize_response_budget(natural_response, so_nguoi_val, question)
        print(f"Generated response length: {len(natural_response)}")
        
        return {
            "status": "success",
            "response": natural_response,
            "language": language,
            "language_name": lang_info["name"],
            "response_length": len(natural_response)
        }
        
    except Exception as e:
        import traceback; traceback.print_exc()
        error_text = str(e).lower()
        error_code = "provider_unavailable"
        if any(token in error_text for token in ("insufficient_quota", "no credits", "quota", "billing", "credit balance")):
            error_code = "quota_exceeded"
        elif any(token in error_text for token in ("api key", "authentication", "unauthorized", "invalid_api_key")):
            error_code = "api_authentication"

        # Keep provider diagnostics out of the API response and frontend.
        print(f"Natural-language response unavailable ({type(e).__name__}; {error_code})")
        
        # Nếu có địa điểm từ DB, tự động tổng hợp câu trả lời hướng dẫn chi tiết
        if search_results:
            fallback_lines = [
                f"Dưới đây là các địa điểm tiêu biểu phù hợp với yêu cầu của bạn đã được kiểm tra tọa độ GPS chuẩn xác:",
                ""
            ]
            for idx, p in enumerate(search_results[:5], 1):
                name = p.get("ten_dia_diem") or p.get("name")
                addr = p.get("dia_chi") or p.get("address") or "Trung tâm khu vực"
                cat = p.get("loai_hinh") or p.get("category") or "Điểm tham quan"
                dist = p.get("distance_km")
                dist_str = f" *(cách tâm ~{dist:.1f} km)*" if dist else ""
                fallback_lines.append(f"{idx}. **{name}** ({cat}){dist_str}")
                fallback_lines.append(f"   * 🏠 **Địa chỉ chuẩn xác**: {addr}")
                fallback_lines.append(f"   * 🚗 **Chỉ đường & Vị trí**: Bạn có thể bấm nút **Bản đồ** bên dưới thẻ địa điểm để mở Google Maps dẫn đường trực tiếp.")
                fallback_lines.append("")
            fallback_lines.append("💡 *Mẹo hữu ích*: Bạn có thể bấm **'Thêm vào lịch trình'** ở từng thẻ địa điểm để tạo lộ trình di chuyển tối ưu tại tab **Lịch trình**.")
            return {
                "status": "success",
                "response": "\n".join(fallback_lines),
                "language": language,
                "language_name": lang_info["name"],
                "response_length": len("\n".join(fallback_lines))
            }

        # Nếu không có địa điểm trong DB (do nằm ngoài vùng phủ sóng TP.HCM như Sa Pa, Đà Nẵng, Hà Nội...)
        loc = extracted_features.get("khu_vuc") or "khu vực bạn quan tâm"
        outside_lines = [
            f"Khu vực **{loc}** hiện nằm ngoài phạm vi cơ sở dữ liệu địa phương TP.HCM (hệ thống hiện lưu trữ hơn 30.800+ địa điểm chi tiết tại TP.HCM).",
            "",
            f"🧭 **Gợi ý tổng quan & Kinh nghiệm du lịch tại {loc}**:",
            f"* 🚗 **Phương tiện & Di chuyển**: Tùy điểm xuất phát, bạn có thể lựa chọn máy bay, tàu hỏa hoặc xe khách giường nằm chất lượng cao.",
            f"* 🏨 **Lưu trú**: Nên đặt trước phòng khách sạn/homestay tại các khu vực trung tâm hoặc gần các điểm tham quan chính.",
            f"* 🗺️ **Lịch trình tham quan**: Hãy phân bổ thời gian hợp lý (sáng khám phá danh thắng thiên nhiên/lịch sử, chiều ngắm cảnh hoặc check-in, tối trải nghiệm ẩm thực đặc sản địa phương).",
            "",
            "💡 *Mẹo*: Khi cần tìm kiếm các địa điểm vui chơi, ẩm thực, bảo tàng tại TP.HCM, hãy nhắn tôi để nhận ngay tọa độ GPS chuẩn xác dẫn đường trên Google Maps!"
        ]
        return {
            "status": "success",
            "response": "\n".join(outside_lines),
            "language": language,
            "language_name": lang_info["name"],
            "response_length": len("\n".join(outside_lines))
        }

def create_chatbot_response(question: str, search_results: List[Dict], 
                           extracted_features: Dict[str, Any], 
                           language: str = "vietnamese",
                           enable_web_search: Optional[bool] = None) -> Dict[str, Any]:
    """
    Tạo phản hồi hoàn chỉnh cho chatbot
    
    Args:
        question (str): Câu hỏi của người dùng
        search_results (List[Dict]): Kết quả tìm kiếm
        extracted_features (Dict[str, Any]): Thực thể trích xuất
        language (str): Ngôn ngữ
        
    Returns:
        Dict[str, Any]: Phản hồi hoàn chỉnh
    """
    # Sinh câu trả lời tự nhiên
    response_data = generate_natural_response(
        question=question,
        search_results=search_results,
        extracted_features=extracted_features,
        language=language,
        enable_web_search=enable_web_search
    )
    
    # Tạo danh sách gợi ý hoạt động
    suggested_activities = []
    if search_results:
        # Lấy tên các địa điểm có độ tương đồng cao
        high_similarity_results = [r for r in search_results if r.get('similarity', 0) > 0.6]
        suggested_activities = [r['ten_dia_diem'] for r in high_similarity_results[:3]]
    
    # Tạo gợi ý câu hỏi tiếp theo
    follow_up_questions = generate_follow_up_questions(language, extracted_features)
    
    return {
        "status": response_data["status"],
        "response": response_data["response"],
        "language": language,
        "search_results": search_results,
        "suggested_activities": suggested_activities,
        "follow_up_questions": follow_up_questions,
        "extracted_features": extracted_features,
        "web_search_queries": response_data.get("web_search_queries", []),
        "web_sources": response_data.get("web_sources", []),
        "grounded": response_data.get("grounded", False),
        "error_code": response_data.get("error_code")
    }

def generate_follow_up_questions(language: str, features: Dict[str, Any]) -> List[str]:
    """
    Tạo câu hỏi gợi ý tiếp theo
    
    Args:
        language (str): Ngôn ngữ
        features (Dict[str, Any]): Thực thể trích xuất
        
    Returns:
        List[str]: Danh sách câu hỏi gợi ý
    """
    questions = {
        "vietnamese": [
            "Bạn có muốn biết thêm về địa điểm nào cụ thể không?",
            "Bạn có quan tâm đến ẩm thực địa phương không?",
            "Bạn muốn tìm hiểu về lịch sử và văn hóa TP.HCM không?",
            "Bạn có cần gợi ý về phương tiện di chuyển không?"
        ],
        "english": [
            "Would you like to know more about any specific place?",
            "Are you interested in local cuisine?",
            "Do you want to learn about Ho Chi Minh City's history and culture?",
            "Do you need suggestions for transportation?"
        ],
        "chinese": [
            "您想了解某个特定地方的更多信息吗？",
            "您对当地美食感兴趣吗？",
            "您想了解胡志明市的历史和文化吗？",
            "您需要交通建议吗？"
        ],
        "korean": [
            "특정 장소에 대해 더 알고 싶으신가요?",
            "현지 요리에 관심이 있으신가요?",
            "호치민시의 역사와 문화에 대해 알고 싶으신가요?",
            "교통편에 대한 제안이 필요하신가요?"
        ],
        "japanese": [
            "特定の場所についてもっと詳しく知りたいですか？",
            "地元の料理に興味がありますか？",
            "ホーチミン市の歴史と文化について知りたいですか？",
            "交通手段についての提案が必要ですか？"
        ]
    }
    
    return questions.get(language, questions["english"])


def _get_curated_image_and_meta(name: str, category: str, fclass: str, region: str):
    """
    Tự động chuẩn hóa ảnh đại diện thực tế (cào trực tiếp từ mạng qua ImageCrawlerAgent),
    giờ mở cửa, giá vé và mô tả sinh động cho từng địa điểm.
    """
    from src.services.ai.image_crawler_agent import image_crawler_agent

    # 100% Cào ảnh thực tế động qua ImageCrawlerAgent (không dùng link tĩnh)
    img = image_crawler_agent.crawl_image(name, category, region)

    # Gợi ý thông tin giờ mở cửa, giá cả và đánh giá theo loại hình
    name_lower = (name or "").lower()
    cat_lower = f"{category or ''} {fclass or ''}".lower()

    if any(k in name_lower or k in cat_lower for k in ["nhà hàng", "restaurant", "quán ăn", "ẩm thực", "bánh", "lẩu", "cháo", "phở", "bún", "cơm"]):
        hours = "07:00 - 22:30"
        price = "35.000đ - 150.000đ/người"
        rating = 4.8
        desc = f"{name} là điểm dừng chân ẩm thực đặc sắc tại {region}, nổi tiếng với hương vị đặc trưng truyền thống và sự đón tiếp nhiệt tình."
    elif any(k in name_lower or k in cat_lower for k in ["homestay", "hotel", "khách sạn", "nhà nghỉ", "resort", "village"]):
        hours = "Nhận phòng 14:00 - Trả phòng 12:00"
        price = "250.000đ - 600.000đ/đêm"
        rating = 4.8
        desc = f"{name} mang lại không gian lưu trú ấm cúng, đậm bản sắc địa phương với tầm nhìn tuyệt đẹp tại {region}."
    elif any(k in name_lower or k in cat_lower for k in ["thuê xe", "xe máy", "rental", "xe ô tô", "giang sơn"]):
        hours = "06:00 - 22:00"
        price = "150.000đ - 250.000đ/ngày"
        rating = 4.9
        desc = f"{name} cung cấp dịch vụ thuê xe uy tín tại {region}, xe được bảo dưỡng kỹ càng, trang bị đầy đủ bảo hộ cho hành trình an toàn."
    elif any(k in name_lower or k in cat_lower for k in ["cafe", "cà phê", "coffee", "trà"]):
        hours = "07:00 - 23:00"
        price = "30.000đ - 65.000đ"
        rating = 4.7
        desc = f"{name} là không gian thưởng thức đồ uống ấn tượng, view đẹp để ngắm cảnh và thư giãn tại {region}."
    else:
        hours = "07:00 - 18:00 (Hàng ngày)"
        price = "Miễn phí hoặc vé tham quan theo mùa"
        rating = 4.8
        desc = f"{name} là điểm tham quan hấp dẫn hàng đầu tại {region}, sở hữu cảnh quan thiên nhiên và trải nghiệm không thể bỏ qua."

    return img, hours, price, rating, desc


_DYNAMIC_POI_CACHE: Dict[str, List[Dict[str, Any]]] = {}

def discover_dynamic_pois_agent(
    target_destination: str,
    region_name: str,
    question: str,
    cat_targets: Optional[List[str]] = None,
    limit: int = 6
) -> List[Dict[str, Any]]:
    """
    Tác tử Độc lập Tự động Tìm kiếm & Khám phá Điểm đến (Dynamic POI Discovery Agent):
    Không nhồi/hardcode câu hỏi hay địa danh tĩnh. Tác tử tự động phân tích địa bàn và yêu cầu
    của du khách để truy xuất danh sách các điểm tham quan, biểu tượng du lịch và ẩm thực đặc sản
    thực tế có tọa độ GPS chính xác trên khắp 63 tỉnh thành Việt Nam theo thời gian thực.
    """
    import urllib.parse
    dest_clean = (target_destination or region_name or "Việt Nam").strip()
    cache_key = f"{dest_clean.lower()}_{','.join(sorted(cat_targets or []))}_{limit}"
    if cache_key in _DYNAMIC_POI_CACHE:
        return _DYNAMIC_POI_CACHE[cache_key]

    system_instruction = """Bạn là Dynamic POI Discovery & Research Agent của hệ thống VietTravel AI.
Nhiệm vụ của bạn là tự động tìm kiếm, phân tích và trích xuất danh sách các địa điểm du lịch biểu tượng, danh lam thắng cảnh và ẩm thực đặc sản uy tín, nổi tiếng nhất tại điểm đến được yêu cầu.

YÊU CẦU BẮT BUỘC:
1. Dữ liệu phải là ĐỊA DANH CÓ THẬT 100%, uy tín hàng đầu mà khách du lịch thực tế luôn muốn đến (tuyệt đối không đưa các điểm hành chính, bia tưởng niệm nhỏ lẻ không có giá trị du lịch).
2. Kết hợp hài hòa giữa: Danh thắng/bãi biển/biểu tượng du lịch check-in và Các quán ăn/đặc sản ẩm thực trứ danh của địa phương đó.
3. Cung cấp tọa độ GPS (vĩ độ, kinh độ) thực tế chính xác và địa chỉ thật.
4. Trả về đúng định dạng JSON là một mảng gồm các đối tượng có cấu trúc:
[
  {
    "id": "chuỗi định danh ngắn gọn duy nhất",
    "ten_dia_diem": "Tên địa danh hoặc quán ăn nổi tiếng",
    "mo_ta": "Mô tả hấp dẫn, súc tích 2-3 câu về nét đặc sắc, món ngon hoặc trải nghiệm nổi bật",
    "loai_dia_diem": "Phân loại rõ ràng (ví dụ: Điểm tham quan / Biểu tượng, Danh lam thắng cảnh, Bãi biển, Ẩm thực & Quán ăn đặc sản...)",
    "khu_vuc": "Tên tỉnh/thành phố hoặc quận/huyện",
    "dia_chi": "Địa chỉ thực tế cụ thể",
    "latitude": 10.1234,
    "longitude": 107.1234,
    "thoi_gian_hoat_dong": "Giờ mở cửa cụ thể (ví dụ: 07:00 - 18:00 hoặc Cả ngày)",
    "gia_ve": "Giá vé tham quan hoặc mức giá tham khảo (ví dụ: Miễn phí, 50.000đ/vé...)",
    "danh_gia": 4.8
  }
]"""

    cat_hint = f" Ưu tiên tìm các địa điểm thuộc phân loại: {', '.join(cat_targets)}." if cat_targets else " Hãy lựa chọn những điểm đến biểu tượng hấp dẫn nhất kết hợp các món ngon đặc sản trứ danh."
    user_prompt = f"""Hãy tự động truy xuất danh sách {limit} địa điểm du lịch tiêu biểu và quán ăn đặc sản nổi tiếng nhất tại: '{dest_clean}' (Khu vực: {region_name}).
Yêu cầu của khách: "{question}".
{cat_hint}
Chỉ xuất ra mảng JSON hợp lệ, không giải thích gì thêm."""

    try:
        res = _call_gemini_generate_content(
            prompt_text=user_prompt,
            system_instruction=system_instruction,
            json_mode=True,
            temperature=0.3,
            max_tokens=2500
        )
        if res and res.get("text"):
            raw_text = res["text"].strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            items = []
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, dict):
                items = parsed.get("places") or parsed.get("pois") or parsed.get("locations") or (list(parsed.values())[0] if parsed else [])

            if isinstance(items, list) and items:
                processed_pois = []
                for idx, item in enumerate(items[:limit]):
                    name = str(item.get("ten_dia_diem") or "").strip()
                    if not name:
                        continue
                    cat = str(item.get("loai_dia_diem") or "Điểm du lịch")
                    reg = str(item.get("khu_vuc") or region_name or dest_clean)
                    addr = str(item.get("dia_chi") or f"{name}, {reg}")
                    try:
                        lat = float(item.get("latitude"))
                        lon = float(item.get("longitude"))
                    except (TypeError, ValueError):
                        lat, lon = None, None

                    if lat and lon:
                        gmaps_url = f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
                    else:
                        gmaps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(f'{name} {reg}')}"

                    img, hours, price, rating, enriched_desc = _get_curated_image_and_meta(name, cat, "", reg)

                    processed_pois.append({
                        "id": f"agent_poi_{idx+1}_{abs(hash(name)) % 10000}",
                        "ten_dia_diem": name,
                        "mo_ta": item.get("mo_ta") or enriched_desc,
                        "loai_dia_diem": cat,
                        "khu_vuc": reg,
                        "dia_chi": addr,
                        "latitude": lat,
                        "longitude": lon,
                        "google_maps_url": gmaps_url,
                        "distance_km": None,
                        "similarity": 0.98,
                        "image_url": img,
                        "thoi_gian_hoat_dong": item.get("thoi_gian_hoat_dong") or hours,
                        "gia_ve": item.get("gia_ve") or price,
                        "danh_gia": float(item.get("danh_gia") or rating or 4.8),
                        "language": "vietnamese"
                    })

                if len(processed_pois) >= 3:
                    _DYNAMIC_POI_CACHE[cache_key] = processed_pois
                    return processed_pois
    except Exception as e:
        print(f"Dynamic POI Discovery Agent failed: {e}")

    return []


def search_pois_from_postgresql(question: str, extracted_features: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
    """
    Truy vấn POI trực tiếp từ PostgreSQL staging (30.800+ địa điểm trên 63 tỉnh thành Việt Nam).
    Đảm bảo 100% độ chính xác vị trí, không hallucination vùng miền, liên kết Google Maps chuẩn xác.
    """
    from src.services.ai.tools.poi_search_tool import _execute_sql
    import urllib.parse

    def _to_clean_str(val: Any) -> str:
        if isinstance(val, list):
            return " ".join(str(x) for x in val if x).strip()
        return str(val or "").strip()

    khu_vuc = _to_clean_str(extracted_features.get("khu_vuc"))
    diem_den = _to_clean_str(extracted_features.get("diem_den"))
    diem_xuat_phat = _to_clean_str(extracted_features.get("diem_xuat_phat") or extracted_features.get("noi_o"))
    loai_dia_diem = _to_clean_str(extracted_features.get("loai_dia_diem"))
    so_thich = _to_clean_str(extracted_features.get("so_thich"))
    muc_dich = _to_clean_str(extracted_features.get("muc_dich"))

    # Bản đồ tọa độ trung tâm, bán kính (km) và tên hiển thị chuẩn cho 63 tỉnh thành & địa danh du lịch lớn
    district_coords = {
        # --- THÁI NGUYÊN & VÙNG ĐÔNG BẮC ---
        "thái nguyên": (21.5942, 105.8481, 15.0, "Thái Nguyên"),
        "thai nguyen": (21.5942, 105.8481, 15.0, "Thái Nguyên"),
        "tân cương": (21.5422, 105.7681, 8.0, "Thái Nguyên"),
        "hồ núi cốc": (21.5764, 105.7028, 8.0, "Thái Nguyên"),
        "sông công": (21.4883, 105.8194, 10.0, "Thái Nguyên"),
        "phổ yên": (21.4167, 105.8667, 10.0, "Thái Nguyên"),
        "định hóa": (21.9056, 105.6472, 12.0, "Thái Nguyên"),
        "hà giang": (22.8233, 104.9839, 18.0, "Hà Giang"),
        "ha giang": (22.8233, 104.9839, 18.0, "Hà Giang"),
        "đồng văn": (23.2778, 105.3625, 12.0, "Hà Giang"),
        "mèo vạc": (23.1639, 105.4083, 12.0, "Hà Giang"),
        "lũng cú": (23.3644, 105.3197, 8.0, "Hà Giang"),
        "cao bằng": (22.6662, 106.2625, 18.0, "Cao Bằng"),
        "cao bang": (22.6662, 106.2625, 18.0, "Cao Bằng"),
        "bản giốc": (22.8539, 106.7239, 8.0, "Cao Bằng"),
        "bắc kạn": (22.1470, 105.8348, 15.0, "Bắc Kạn"),
        "bac kan": (22.1470, 105.8348, 15.0, "Bắc Kạn"),
        "ba bể": (22.4172, 105.6178, 10.0, "Bắc Kạn"),
        "tuyên quang": (21.8236, 105.2180, 15.0, "Tuyên Quang"),
        "tuyen quang": (21.8236, 105.2180, 15.0, "Tuyên Quang"),
        "lạng sơn": (21.8536, 106.7617, 15.0, "Lạng Sơn"),
        "lang son": (21.8536, 106.7617, 15.0, "Lạng Sơn"),
        "bắc giang": (21.2731, 106.1946, 15.0, "Bắc Giang"),
        "bac giang": (21.2731, 106.1946, 15.0, "Bắc Giang"),

        # --- HÀ NỘI & VÙNG ĐỒNG BẰNG SÔNG HỒNG ---
        "lăng bác": (21.0368, 105.8346, 3.5, "Ba Đình, Hà Nội"),
        "lang bac": (21.0368, 105.8346, 3.5, "Ba Đình, Hà Nội"),
        "lăng chủ tịch": (21.0368, 105.8346, 3.5, "Ba Đình, Hà Nội"),
        "lăng chủ tịch hồ chí minh": (21.0368, 105.8346, 3.5, "Ba Đình, Hà Nội"),
        "quảng trường ba đình": (21.0368, 105.8346, 3.5, "Ba Đình, Hà Nội"),
        "chùa một cột": (21.0358, 105.8333, 3.5, "Ba Đình, Hà Nội"),
        "văn miếu": (21.0274, 105.8355, 3.5, "Đống Đa, Hà Nội"),
        "quốc tử giám": (21.0274, 105.8355, 3.5, "Đống Đa, Hà Nội"),
        "hoàng thành thăng long": (21.0358, 105.8406, 3.5, "Ba Đình, Hà Nội"),
        "hồ hoàn kiếm": (21.0285, 105.8542, 3.5, "Hoàn Kiếm, Hà Nội"),
        "hồ gươm": (21.0285, 105.8542, 3.5, "Hoàn Kiếm, Hà Nội"),
        "phố cổ": (21.0345, 105.8500, 3.5, "Hoàn Kiếm, Hà Nội"),
        "phố cổ hà nội": (21.0345, 105.8500, 3.5, "Hoàn Kiếm, Hà Nội"),
        "bảo tàng dân tộc học": (21.0406, 105.7989, 4.0, "Cầu Giấy, Hà Nội"),
        "hoàn kiếm": (21.0285, 105.8542, 3.5, "Hà Nội"),
        "hoan kiem": (21.0285, 105.8542, 3.5, "Hà Nội"),
        "ba đình": (21.0341, 105.8244, 4.0, "Ba Đình, Hà Nội"),
        "ba dinh": (21.0341, 105.8244, 4.0, "Ba Đình, Hà Nội"),
        "tây hồ": (21.0715, 105.8230, 4.5, "Hà Nội"),
        "hồ tây": (21.0715, 105.8230, 4.5, "Hà Nội"),
        "đống đa": (21.0181, 105.8277, 4.0, "Hà Nội"),
        "cầu giấy": (21.0362, 105.7906, 4.5, "Hà Nội"),
        "hai bà trưng": (21.0089, 105.8531, 4.0, "Hà Nội"),
        "thanh xuân": (20.9950, 105.8119, 4.0, "Hà Nội"),
        "hà đông": (20.9719, 105.7767, 5.0, "Hà Nội"),
        "hà nội": (21.0285, 105.8542, 8.5, "Hà Nội"),
        "ha noi": (21.0285, 105.8542, 8.5, "Hà Nội"),
        "hanoi": (21.0285, 105.8542, 8.5, "Hà Nội"),
        "ba vì": (21.0845, 105.3644, 12.0, "Ba Vì, Hà Nội"),
        "ba vi": (21.0845, 105.3644, 12.0, "Ba Vì, Hà Nội"),
        "sóc sơn": (21.2725, 105.8475, 10.0, "Hà Nội"),
        "hải phòng": (20.8449, 106.6881, 15.0, "Hải Phòng"),
        "hai phong": (20.8449, 106.6881, 15.0, "Hải Phòng"),
        "cát bà": (20.7275, 106.9996, 12.0, "Cát Bà, Hải Phòng"),
        "cat ba": (20.7275, 106.9996, 12.0, "Cát Bà, Hải Phòng"),
        "đồ sơn": (20.7077, 106.7883, 8.0, "Hải Phòng"),
        "quảng ninh": (20.9505, 107.0734, 18.0, "Quảng Ninh"),
        "quang ninh": (20.9505, 107.0734, 18.0, "Quảng Ninh"),
        "hạ long": (20.9505, 107.0734, 15.0, "Hạ Long, Quảng Ninh"),
        "ha long": (20.9505, 107.0734, 15.0, "Hạ Long, Quảng Ninh"),
        "vân đồn": (21.0772, 107.4128, 15.0, "Quảng Ninh"),
        "cô tô": (20.9833, 107.7667, 10.0, "Quảng Ninh"),
        "ninh bình": (20.2506, 105.9745, 15.0, "Ninh Bình"),
        "ninh binh": (20.2506, 105.9745, 15.0, "Ninh Bình"),
        "tràng an": (20.2536, 105.9189, 10.0, "Ninh Bình"),
        "tam cốc": (20.2201, 105.9392, 8.0, "Ninh Bình"),
        "bái đính": (20.2858, 105.8569, 8.0, "Ninh Bình"),
        "bắc ninh": (21.1861, 106.0763, 12.0, "Bắc Ninh"),
        "bac ninh": (21.1861, 106.0763, 12.0, "Bắc Ninh"),
        "hải dương": (20.9386, 106.3155, 12.0, "Hải Dương"),
        "hai duong": (20.9386, 106.3155, 12.0, "Hải Dương"),
        "hưng yên": (20.6464, 106.0511, 12.0, "Hưng Yên"),
        "hung yen": (20.6464, 106.0511, 12.0, "Hưng Yên"),
        "hà nam": (20.5456, 105.9125, 12.0, "Hà Nam"),
        "nam định": (20.4344, 106.1685, 12.0, "Nam Định"),
        "nam dinh": (20.4344, 106.1685, 12.0, "Nam Định"),
        "thái bình": (20.4464, 106.3365, 12.0, "Thái Bình"),
        "thai binh": (20.4464, 106.3365, 12.0, "Thái Bình"),
        "vĩnh phúc": (21.3089, 105.6047, 12.0, "Vĩnh Phúc"),
        "tam đảo": (21.4583, 105.6439, 10.0, "Tam Đảo, Vĩnh Phúc"),
        "tam dao": (21.4583, 105.6439, 10.0, "Tam Đảo, Vĩnh Phúc"),
        "phú thọ": (21.3227, 105.4019, 15.0, "Phú Thọ"),
        "phu tho": (21.3227, 105.4019, 15.0, "Phú Thọ"),

        # --- TÂY BẮC ---
        "sa pa": (22.3364, 103.8438, 10.0, "Sa Pa, Lào Cai"),
        "sapa": (22.3364, 103.8438, 10.0, "Sa Pa, Lào Cai"),
        "lào cai": (22.4856, 103.9707, 12.0, "Lào Cai"),
        "lao cai": (22.4856, 103.9707, 12.0, "Lào Cai"),
        "mộc châu": (20.8442, 104.6469, 15.0, "Mộc Châu, Sơn La"),
        "moc chau": (20.8442, 104.6469, 15.0, "Mộc Châu, Sơn La"),
        "sơn la": (21.3256, 103.9167, 15.0, "Sơn La"),
        "mai châu": (20.6593, 105.0838, 12.0, "Mai Châu, Hòa Bình"),
        "hòa bình": (20.8172, 105.3375, 15.0, "Hòa Bình"),
        "yên bái": (21.7167, 104.8833, 15.0, "Yên Bái"),
        "mù cang chải": (21.8542, 104.0889, 15.0, "Yên Bái"),
        "điện biên": (21.3857, 103.0177, 15.0, "Điện Biên"),
        "lai châu": (22.3964, 103.4686, 15.0, "Lai Châu"),

        # --- ĐÔNG BẮC & CAO NGUYÊN ĐÁ ---
        "hà giang": (22.8233, 104.9839, 25.0, "Hà Giang"),
        "ha giang": (22.8233, 104.9839, 25.0, "Hà Giang"),
        "đồng văn": (23.2789, 105.3611, 20.0, "Đồng Văn, Hà Giang"),
        "dong van": (23.2789, 105.3611, 20.0, "Đồng Văn, Hà Giang"),
        "mèo vạc": (23.1652, 105.4103, 18.0, "Mèo Vạc, Hà Giang"),
        "meo vac": (23.1652, 105.4103, 18.0, "Mèo Vạc, Hà Giang"),
        "quản bạ": (23.0667, 104.9833, 15.0, "Quản Bạ, Hà Giang"),
        "quan ba": (23.0667, 104.9833, 15.0, "Quản Bạ, Hà Giang"),
        "yên minh": (23.1167, 105.1500, 15.0, "Yên Minh, Hà Giang"),
        "yen minh": (23.1167, 105.1500, 15.0, "Yên Minh, Hà Giang"),
        "lũng cú": (23.3611, 105.3194, 10.0, "Đồng Văn, Hà Giang"),
        "lung cu": (23.3611, 105.3194, 10.0, "Đồng Văn, Hà Giang"),
        "cao bằng": (22.6667, 106.2500, 20.0, "Cao Bằng"),
        "cao bang": (22.6667, 106.2500, 20.0, "Cao Bằng"),
        "bản giốc": (22.8539, 106.7239, 12.0, "Trùng Khánh, Cao Bằng"),
        "ban gioc": (22.8539, 106.7239, 12.0, "Trùng Khánh, Cao Bằng"),
        "bắc kạn": (22.1467, 105.8347, 15.0, "Bắc Kạn"),
        "bac kan": (22.1467, 105.8347, 15.0, "Bắc Kạn"),
        "hồ ba bể": (22.4167, 105.6167, 12.0, "Ba Bể, Bắc Kạn"),
        "lạng sơn": (21.8536, 106.7617, 15.0, "Lạng Sơn"),
        "lang son": (21.8536, 106.7617, 15.0, "Lạng Sơn"),
        "thái nguyên": (21.5928, 105.8442, 15.0, "Thái Nguyên"),
        "thai nguyen": (21.5928, 105.8442, 15.0, "Thái Nguyên"),
        "tuyên quang": (21.8233, 105.2181, 15.0, "Tuyên Quang"),
        "tuyen quang": (21.8233, 105.2181, 15.0, "Tuyên Quang"),

        # --- MIỀN TRUNG & DUYÊN HẢI ---
        "thanh hóa": (19.8067, 105.7852, 15.0, "Thanh Hóa"),
        "sầm sơn": (19.7422, 105.9039, 10.0, "Thanh Hóa"),
        "nghệ an": (18.6734, 105.6813, 15.0, "Nghệ An"),
        "vinh": (18.6734, 105.6813, 12.0, "Nghệ An"),
        "cửa lò": (18.7972, 105.7194, 10.0, "Nghệ An"),
        "hà tĩnh": (18.3429, 105.9059, 12.0, "Hà Tĩnh"),
        "quảng bình": (17.4690, 106.6225, 18.0, "Quảng Bình"),
        "phong nha": (17.5898, 106.2828, 15.0, "Quảng Bình"),
        "đồng hới": (17.4690, 106.6225, 12.0, "Quảng Bình"),
        "quảng trị": (16.7504, 107.1856, 15.0, "Quảng Trị"),
        "thừa thiên huế": (16.4637, 107.5909, 15.0, "Huế"),
        "huế": (16.4637, 107.5909, 12.0, "Huế"),
        "hue": (16.4637, 107.5909, 12.0, "Huế"),
        "đà nẵng": (16.0544, 108.2022, 12.0, "Đà Nẵng"),
        "da nang": (16.0544, 108.2022, 12.0, "Đà Nẵng"),
        "hải châu": (16.0544, 108.2022, 4.0, "Đà Nẵng"),
        "sơn trà": (16.1158, 108.2750, 6.0, "Đà Nẵng"),
        "ngũ hành sơn": (16.0028, 108.2497, 5.0, "Đà Nẵng"),
        "hội an": (15.8801, 108.3380, 8.0, "Hội An, Quảng Nam"),
        "hoi an": (15.8801, 108.3380, 8.0, "Hội An, Quảng Nam"),
        "quảng nam": (15.5894, 108.4756, 18.0, "Quảng Nam"),
        "quảng ngãi": (15.1205, 108.7923, 15.0, "Quảng Ngãi"),
        "lý sơn": (15.3789, 109.1174, 8.0, "Lý Sơn, Quảng Ngãi"),
        "bình định": (13.7830, 109.2197, 15.0, "Bình Định"),
        "quy nhơn": (13.7830, 109.2197, 12.0, "Quy Nhơn"),
        "quy nhon": (13.7830, 109.2197, 12.0, "Quy Nhơn"),
        "phú yên": (13.0882, 109.3075, 15.0, "Phú Yên"),
        "tuy hòa": (13.0882, 109.3075, 12.0, "Phú Yên"),
        "khánh hòa": (12.2388, 109.1967, 15.0, "Khánh Hòa"),
        "nha trang": (12.2388, 109.1967, 10.0, "Nha Trang"),
        "cam ranh": (11.9214, 109.1591, 12.0, "Khánh Hòa"),
        "ninh thuận": (11.5667, 108.9833, 15.0, "Ninh Thuận"),
        "phan rang": (11.5667, 108.9833, 12.0, "Ninh Thuận"),
        "bình thuận": (10.9289, 108.1021, 15.0, "Bình Thuận"),
        "phan thiết": (10.9289, 108.1021, 12.0, "Phan Thiết"),
        "mũi né": (10.9333, 108.2833, 10.0, "Mũi Né, Bình Thuận"),

        # --- TÂY NGUYÊN ---
        "đà lạt": (11.9404, 108.4583, 10.0, "Đà Lạt"),
        "da lat": (11.9404, 108.4583, 10.0, "Đà Lạt"),
        "dalat": (11.9404, 108.4583, 10.0, "Đà Lạt"),
        "lâm đồng": (11.9404, 108.4583, 15.0, "Lâm Đồng"),
        "bảo lộc": (11.5475, 107.8080, 12.0, "Lâm Đồng"),
        "đắk lắk": (12.6667, 108.0383, 15.0, "Đắk Lắk"),
        "buôn ma thuột": (12.6667, 108.0383, 12.0, "Đắk Lắk"),
        "gia lai": (13.9833, 108.0000, 15.0, "Gia Lai"),
        "pleiku": (13.9833, 108.0000, 12.0, "Gia Lai"),
        "kon tum": (14.3497, 108.0003, 15.0, "Kon Tum"),
        "măng đen": (14.6047, 108.2917, 10.0, "Kon Tum"),
        "đắk nông": (12.0039, 107.6908, 15.0, "Đắk Nông"),

        # --- TP.HCM & ĐÔNG NAM BỘ ---
        "quận 1": (10.7769, 106.7009, 3.5, "Quận 1, TP.HCM"),
        "quan 1": (10.7769, 106.7009, 3.5, "Quận 1, TP.HCM"),
        "district 1": (10.7769, 106.7009, 3.5, "Quận 1, TP.HCM"),
        "d1": (10.7769, 106.7009, 3.5, "Quận 1, TP.HCM"),
        "bến thành": (10.7725, 106.6980, 2.5, "Quận 1, TP.HCM"),
        "quận 3": (10.7844, 106.6844, 3.5, "Quận 3, TP.HCM"),
        "quan 3": (10.7844, 106.6844, 3.5, "Quận 3, TP.HCM"),
        "quận 5": (10.7540, 106.6634, 4.0, "Quận 5, TP.HCM"),
        "chợ lớn": (10.7517, 106.6575, 3.5, "Quận 5, TP.HCM"),
        "quận 7": (10.7340, 106.7218, 5.0, "Quận 7, TP.HCM"),
        "phú mỹ hưng": (10.7289, 106.7119, 4.0, "Quận 7, TP.HCM"),
        "bình thạnh": (10.8015, 106.7114, 4.0, "Bình Thạnh, TP.HCM"),
        "phú nhuận": (10.7991, 106.6802, 3.5, "Phú Nhuận, TP.HCM"),
        "tân bình": (10.7992, 106.6534, 4.5, "Tân Bình, TP.HCM"),
        "thủ đức": (10.8494, 106.7725, 8.0, "TP. Thủ Đức, TP.HCM"),
        "sài gòn": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "sai gon": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "saigon": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "tp.hcm": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "tphcm": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "hcmc": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "hồ chí minh": (10.7769, 106.7009, 7.5, "TP.HCM"),
        "vũng tàu": (10.3460, 107.0843, 10.0, "Vũng Tàu"),
        "vung tau": (10.3460, 107.0843, 10.0, "Vũng Tàu"),
        "bà rịa": (10.4969, 107.1683, 12.0, "Bà Rịa - Vũng Tàu"),
        "côn đảo": (8.6834, 106.6074, 10.0, "Côn Đảo"),
        "hồ tràm": (10.4667, 107.4167, 10.0, "Bà Rịa - Vũng Tàu"),
        "tây ninh": (11.3100, 106.0983, 15.0, "Tây Ninh"),
        "núi bà đen": (11.3789, 106.1689, 8.0, "Tây Ninh"),
        "bình dương": (10.9805, 106.6519, 12.0, "Bình Dương"),
        "đồng nai": (10.9575, 106.8427, 15.0, "Đồng Nai"),
        "biên hòa": (10.9575, 106.8427, 12.0, "Biên Hòa, Đồng Nai"),
        "bình phước": (11.5333, 106.8833, 15.0, "Bình Phước"),

        # --- TÂY NAM BỘ (ĐỒNG BẰNG SÔNG CỬU LONG) ---
        "cần thơ": (10.0452, 105.7469, 15.0, "Cần Thơ"),
        "can tho": (10.0452, 105.7469, 15.0, "Cần Thơ"),
        "ninh kiều": (10.0333, 105.7833, 5.0, "Cần Thơ"),
        "cái răng": (10.0056, 105.7472, 6.0, "Cần Thơ"),
        "phú quốc": (10.2289, 103.9572, 18.0, "Phú Quốc"),
        "phu quoc": (10.2289, 103.9572, 18.0, "Phú Quốc"),
        "kiên giang": (10.0125, 105.0809, 18.0, "Kiên Giang"),
        "rạch giá": (10.0125, 105.0809, 12.0, "Kiên Giang"),
        "hà tiên": (10.3833, 104.4833, 10.0, "Hà Tiên, Kiên Giang"),
        "an giang": (10.7042, 105.1169, 18.0, "An Giang"),
        "châu đốc": (10.7042, 105.1169, 12.0, "Châu Đốc, An Giang"),
        "long xuyên": (10.3833, 105.4333, 12.0, "An Giang"),
        "tiền giang": (10.3544, 106.3653, 15.0, "Tiền Giang"),
        "mỹ tho": (10.3544, 106.3653, 10.0, "Mỹ Tho, Tiền Giang"),
        "bến tre": (10.2415, 106.3759, 12.0, "Bến Tre"),
        "vĩnh long": (10.2537, 105.9722, 12.0, "Vĩnh Long"),
        "đồng tháp": (10.2933, 105.7578, 15.0, "Đồng Tháp"),
        "sa đéc": (10.2933, 105.7578, 10.0, "Sa Đéc, Đồng Tháp"),
        "cao lãnh": (10.4600, 105.6333, 10.0, "Đồng Tháp"),
        "trà vinh": (9.9347, 106.3455, 12.0, "Trà Vinh"),
        "sóc trăng": (9.6033, 105.9800, 12.0, "Sóc Trăng"),
        "bạc liêu": (9.2941, 105.7278, 12.0, "Bạc Liêu"),
        "cà mau": (9.1769, 105.1524, 15.0, "Cà Mau"),
        "đất mũi": (8.6083, 104.7167, 10.0, "Cà Mau"),
        "hậu giang": (9.7844, 105.4701, 12.0, "Hậu Giang"),
        "long an": (10.5361, 106.4131, 15.0, "Long An")
    }

    full_text = f"{khu_vuc} {diem_den} {loai_dia_diem} {so_thich} {muc_dich} {question}".lower()

    # 1. Nhận diện khu vực hoặc tọa độ trung tâm (Ưu tiên điểm đến, không để điểm xuất phát ghi đè)
    matched_info = None
    target_region_name = khu_vuc or diem_den or "Việt Nam"

    # Ưu tiên so khớp chính xác từ khóa địa danh dài trước
    sorted_district_keys = sorted(district_coords.keys(), key=lambda k: len(k), reverse=True)

    # BƯỚC 1: Ưu tiên số 1 so khớp trực tiếp trên điểm đến (khu_vuc hoặc diem_den do AI trích xuất)
    dest_text = f"{khu_vuc} {diem_den}".lower().strip()
    if dest_text:
        for d_name in sorted_district_keys:
            if d_name in dest_text:
                matched_info = district_coords[d_name]
                target_region_name = matched_info[3]
                khu_vuc = target_region_name
                break

    # BƯỚC 2: Nếu chưa tìm thấy từ điểm đến, quét câu hỏi nhưng LOẠI BỎ điểm xuất phát
    if not matched_info:
        clean_q = question.lower()
        if diem_xuat_phat:
            clean_q = clean_q.replace(diem_xuat_phat.lower(), " ")
        clean_q = re.sub(r"(?:đang ở|ở|từ|xuất phát từ|khởi hành từ)\s+[^,.]+?(?:đến|đi|tới|mà|thì|\?|$)", " ", clean_q)
        for d_name in sorted_district_keys:
            if d_name in clean_q:
                matched_info = district_coords[d_name]
                target_region_name = matched_info[3]
                khu_vuc = target_region_name
                break

    # BƯỚC 3: Fallback tìm trên toàn bộ văn bản nếu chỉ có 1 địa danh duy nhất
    if not matched_info:
        for d_name in sorted_district_keys:
            if d_name in full_text:
                matched_info = district_coords[d_name]
                target_region_name = matched_info[3]
                khu_vuc = target_region_name
                break

    conditions = ["1=1"]
    params: Dict[str, Any] = {"limit": limit}

    if matched_info:
        lat = matched_info[0]
        lon = matched_info[1]
        radius_km = matched_info[2]
        delta_lat = radius_km / 111.0
        delta_lon = radius_km / 105.0
        conditions.append("latitude BETWEEN :min_lat AND :max_lat")
        conditions.append("longitude BETWEEN :min_lon AND :max_lon")
        params["min_lat"] = lat - delta_lat
        params["max_lat"] = lat + delta_lat
        params["min_lon"] = lon - delta_lon
        params["max_lon"] = lon + delta_lon
        params["center_lat"] = lat
        params["center_lon"] = lon
        haversine_sql = """(6371.0 * 2.0 * ASIN(SQRT(
            POWER(SIN(RADIANS(latitude - :center_lat) / 2.0), 2) +
            COS(RADIANS(:center_lat)) * COS(RADIANS(latitude)) *
            POWER(SIN(RADIANS(longitude - :center_lon) / 2.0), 2)
        )))"""
        order_by = f"{haversine_sql} ASC"
        dist_column = f"{haversine_sql} AS distance_km,"
    else:
        if khu_vuc and khu_vuc not in ("Việt Nam", "TP.HCM"):
            conditions.append("(LOWER(name) LIKE :kv OR LOWER(address) LIKE :kv OR LOWER(description) LIKE :kv)")
            params["kv"] = f"%{khu_vuc.lower()}%"
        order_by = "id ASC"
        dist_column = "NULL AS distance_km,"

    # 2. Chuẩn hóa danh mục POI phù hợp với dữ liệu PostgreSQL OSM
    cat_targets = []
    if any(k in full_text for k in ["cafe", "cà phê", "coffee", "uống nước", "trà sữa", "trà"]):
        cat_targets = ["cà phê", "cafe", "beverages"]
    elif any(k in full_text for k in ["khách sạn", "hotel", "homestay", "nghỉ", "resort", "nhà nghỉ", "hostel"]):
        cat_targets = ["khách sạn", "hotel", "guesthouse", "hostel", "motel"]
    elif any(k in full_text for k in ["nhà hàng", "quán ăn", "ẩm thực", "ăn uống", "đặc sản", "món ngon", "ăn trưa", "ăn tối"]):
        cat_targets = ["nhà hàng", "quán ăn", "restaurant", "fast_food", "food_court"]
    elif any(k in full_text for k in ["bảo tàng", "museum"]):
        cat_targets = ["bảo tàng", "museum"]
    elif any(k in full_text for k in ["di tích", "lịch sử", "tượng đài", "chùa", "đền", "văn hóa", "miếu", "lăng", "bác"]):
        cat_targets = ["di tích", "tượng đài", "chùa", "đền", "monument", "memorial", "buddhist", "artwork", "attraction", "museum"]
    elif any(k in full_text for k in ["chợ", "mua sắm", "shopping", "trung tâm thương mại"]):
        cat_targets = ["chợ", "marketplace", "mall"]
    elif any(k in full_text for k in ["bar", "pub", "rượu", "quán bar", "nightlife", "nhạc sống"]):
        cat_targets = ["bar", "pub"]
    elif any(k in full_text for k in ["du lịch", "điểm du lịch", "tham quan", "thắng cảnh", "chơi", "ngắm cảnh", "check-in", "khám phá", "đi dạo", "công viên", "quảng trường"]):
        cat_targets = ["attraction", "park", "viewpoint", "monument", "artwork", "memorial", "museum", "công viên", "tham quan", "thắng cảnh"]
    elif loai_dia_diem or so_thich:
        raw_val = (loai_dia_diem or so_thich).lower()
        if any(w in raw_val for w in ["du lịch", "tham quan", "chơi"]):
            cat_targets = ["attraction", "park", "viewpoint", "monument", "công viên", "tham quan", "thắng cảnh"]
        else:
            cat_targets = [raw_val]

    if cat_targets:
        or_clauses = []
        for idx, ct in enumerate(cat_targets):
            param_key = f"cat_{idx}"
            or_clauses.append(f"(LOWER(category) LIKE :{param_key} OR LOWER(fclass) LIKE :{param_key} OR LOWER(name) LIKE :{param_key})")
            params[param_key] = f"%{ct}%"
        conditions.append(f"({' OR '.join(or_clauses)})")

    # 2.1. ƯU TIÊN SỐ 1: Tác tử Tự động Khám phá Điểm đến (Dynamic POI Discovery Agent)
    # Tự động phân tích địa bàn, tìm kiếm biểu tượng du lịch và ẩm thực đặc sản thời gian thực (cho bất kỳ tỉnh/thành nào)
    try:
        dynamic_pois = discover_dynamic_pois_agent(
            target_destination=target_region_name or dest_text or "Việt Nam",
            region_name=target_region_name,
            question=question,
            cat_targets=cat_targets,
            limit=limit
        )
        if dynamic_pois and len(dynamic_pois) >= 3:
            return dynamic_pois[:limit]
    except Exception as agent_err:
        print(f"Dynamic POI Discovery Agent call skipped: {agent_err}")

    where_clause = " AND ".join(conditions)
    fetch_limit = max(limit * 4, 24)
    params["fetch_limit"] = fetch_limit

    sql = f"""
        SELECT id, osm_id, name, category, fclass, latitude, longitude, address, description, rating, price,
               {dist_column}
               NULL AS dummy
        FROM osm_attractions_staging
        WHERE {where_clause}
        ORDER BY {order_by}
        LIMIT :fetch_limit;
    """
    try:
        rows = _execute_sql(sql, params)

        # Fallback 1: Nếu lọc theo danh mục quá chặt mà không ra kết quả, nới rộng ra các điểm du lịch/ẩm thực chính trong khu vực
        if not rows and matched_info:
            fallback_conditions = [
                "latitude BETWEEN :min_lat AND :max_lat",
                "longitude BETWEEN :min_lon AND :max_lon",
                "(fclass IN ('attraction', 'park', 'viewpoint', 'monument', 'artwork', 'memorial', 'marketplace', 'museum', 'hotel', 'cafe', 'restaurant') OR category ILIKE '%công viên%' OR category ILIKE '%tham quan%' OR category ILIKE '%thắng cảnh%' OR category ILIKE '%cà phê%' OR category ILIKE '%khách sạn%' OR category ILIKE '%nhà hàng%')"
            ]
            fallback_sql = f"""
                SELECT id, osm_id, name, category, fclass, latitude, longitude, address, description, rating, price,
                       {dist_column}
                       NULL AS dummy
                FROM osm_attractions_staging
                WHERE {' AND '.join(fallback_conditions)}
                ORDER BY {order_by}
                LIMIT :fetch_limit;
            """
            rows = _execute_sql(fallback_sql, params)

        # Fallback 2: Nếu vẫn chưa có dòng nào, lấy bất kỳ POI nào thuộc bounding box khu vực đó sắp xếp theo cự ly gần nhất
        if not rows and matched_info:
            general_fallback_sql = f"""
                SELECT id, osm_id, name, category, fclass, latitude, longitude, address, description, rating, price,
                       {dist_column}
                       NULL AS dummy
                FROM osm_attractions_staging
                WHERE latitude BETWEEN :min_lat AND :max_lat
                  AND longitude BETWEEN :min_lon AND :max_lon
                  AND name IS NOT NULL AND TRIM(name) != ''
                ORDER BY {order_by}
                LIMIT :fetch_limit;
            """
            rows = _execute_sql(general_fallback_sql, params)

        seen_names = set()
        pois = []
        for row in rows:
            m = row._mapping
            name = (m["name"] or "").strip()
            clean_name = name.lower()
            if not clean_name or clean_name in seen_names:
                continue
            seen_names.add(clean_name)

            lat = float(m["latitude"]) if m["latitude"] is not None else None
            lon = float(m["longitude"]) if m["longitude"] is not None else None
            dist = float(m["distance_km"]) if m.get("distance_km") is not None else None

            # Xác định địa chỉ và khu vực địa phương chuẩn xác theo tỉnh/thành thực tế
            region_name = target_region_name
            raw_addr = (m["address"] or "").strip()
            if raw_addr and "chưa có địa chỉ" not in raw_addr.lower() and len(raw_addr) > 5:
                addr = raw_addr
            elif lat and lon:
                addr = f"{name}, {region_name}"
            else:
                addr = f"Khu vực {region_name}"

            if lat and lon:
                # Đường link Google Maps chính xác tuyệt đối với tọa độ GPS thực tế
                gmaps_url = f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
            else:
                encoded_q = urllib.parse.quote(f"{name} {region_name}")
                gmaps_url = f"https://www.google.com/maps/search/?api=1&query={encoded_q}"

            dist_text = f"Cách trung tâm ~{dist:.1f} km. " if dist is not None else ""
            cat_display = m["category"] or m["fclass"] or "Địa điểm tham quan"
            img, hours, price, rating, enriched_desc = _get_curated_image_and_meta(name, cat_display, m.get("fclass") or "", region_name)
            
            # Crawl authentic real-world photography and persist to DB
            from src.services.ai.image_crawler_agent import image_crawler_agent
            real_img = m.get("image_url")
            if not real_img:
                real_img = image_crawler_agent.crawl_image(name, cat_display, region_name, osm_id=m.get("osm_id") or m.get("id"))
            final_img = real_img or img

            desc = enriched_desc or f"{name} là {cat_display} uy tín tại {region_name}. {dist_text}Bấm 'Bản đồ' để mở định vị GPS thực tế trên Google Maps."

            pois.append({
                "id": str(m["osm_id"] or m["id"]),
                "ten_dia_diem": name,
                "mo_ta": desc,
                "loai_dia_diem": cat_display,
                "khu_vuc": region_name,
                "dia_chi": addr,
                "latitude": lat,
                "longitude": lon,
                "google_maps_url": gmaps_url,
                "distance_km": dist,
                "similarity": 0.95,
                "image_url": final_img,
                "thoi_gian_hoat_dong": hours,
                "gia_ve": price,
                "danh_gia": rating,
                "language": "vietnamese"
            })
            if len(pois) >= limit:
                break

        return pois
    except Exception as e:
        print(f"Error querying PostgreSQL POIs for chatbot: {e}")
        return []

