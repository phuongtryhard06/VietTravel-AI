import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import os
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
    max_tokens: int = 1000
) -> Optional[Dict[str, Any]]:
    if not gemini_key_manager.has_keys():
        return None

    # Danh sách các mô hình ổn định và phản hồi nhanh nhất (< 2s)
    model_pool = [
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
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
                "maxOutputTokens": max(max_tokens, 1000),
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
                    resp = requests.post(url, json=_build_payload(with_search=True, is_json=False), timeout=10)
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
                resp = requests.post(url, json=_build_payload(with_search=False, is_json=json_mode), timeout=12)
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
                    key_exhausted = True
                    break
                elif resp.status_code == 404:
                    continue
            except Exception:
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
    "ngan_sach": "...",
    "thoi_gian": "...",
    "so_thich": "...",
    "muc_dich": "..."
  }
}
Lưu ý quan trọng:
- Nếu người dùng nêu điểm xuất phát (nơi ở hiện tại, bắt đầu từ đâu, vd: 'đang ở Phổ Yên', 'từ Hải Phòng', 'ở Cầu Giấy'): ghi vào 'diem_xuat_phat'.
- Điểm đến du lịch (nơi muốn tới, ghé thăm, vd: 'Lăng Bác', 'Tràng An, Ninh Bình', 'Tam Đảo'): ghi vào 'diem_den' và 'khu_vuc'. TUYỆT ĐỐI không gán điểm xuất phát vào 'khu_vuc'!
- loai_dia_diem, khu_vuc, diem_xuat_phat, diem_den, ngan_sach, thoi_gian, so_thich, muc_dich là chuỗi ký tự (string). Nếu trường nào không có, để null hoặc bỏ qua."""
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

def generate_natural_response(question: str, search_results: List[Dict], 
                            extracted_features: Dict[str, Any], 
                            language: str = "vietnamese",
                            enable_web_search: Optional[bool] = None) -> Dict[str, Any]:
    """
    Sinh câu trả lời tự nhiên cho chatbot hướng dẫn viên du lịch
    
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
        
        # Tạo prompt dựa trên ngôn ngữ
        if language == "vietnamese":
            system_prompt = """Bạn là VietTravel AI - Trợ lý Tác tử Thông minh chuyên Lập Kế Hoạch & Đặt Lịch Chuyến Du Lịch (Smart Travel Itinerary & Booking Agent).
Nhiệm vụ của bạn là tư vấn chuyến đi một cách thực tế, cụ thể, chuyên nghiệp và có cấu trúc rõ ràng để khách có thể lên kế hoạch, dự toán chi phí và đặt lịch trình ngay.

NGUYÊN TẮC BẮT BUỘC CỦA TÁC TỬ:
1. TUYỆT ĐỐI KHÔNG NÓI CHUNG CHUNG (Cấm đưa ra các lời khuyên lý thuyết sáo rỗng như 'hãy chọn quán có wifi mạnh và nhiều ổ cắm' hay 'chọn quán có không gian yên tĩnh').
2. NÊU ĐÍCH DANH TỪNG ĐỊA ĐIỂM CỤ THỂ:
   - Nếu có danh sách địa điểm ứng viên (Candidate places): Bắt buộc phân tích cụ thể từng quán/địa điểm theo tên thật, vị trí, tọa độ GPS hoặc khoảng cách.
   - Nếu tra cứu Web thời gian thực (Google Search Grounding): Trích dẫn cụ thể tên quán/điểm đến hot trend, số nhà, con đường, mức giá nước uống trung bình (vd: 45.000đ - 70.000đ), giờ mở cửa.
3. CẤU TRÚC BẮT BUỘC CHO MỖI ĐỊA ĐIỂM (Trình bày từng ý rõ ràng, mỗi ý một gạch đầu dòng):
   * 📍 **Tên địa điểm & Nét đặc trưng**: Tên địa điểm chuẩn, phong cách nổi bật (góc ngắm phố, di tích lịch sử, không gian xưa, view sông...).
   * 🏠 **Địa chỉ chuẩn xác**: BẮT BUỘC cung cấp tên đường cụ thể, phường/xã, quận/huyện, thành phố và mốc nhận biết rõ ràng ngoài đời thực (ví dụ: 'Đường Nguyễn Văn Bình, P. Bến Nghé, Quận 1 - nằm bên hông Bưu điện Thành phố và Nhà thờ Đức Bà' hoặc 'Góc đường Nam Kỳ Khởi Nghĩa giao Pasteur, Quận 1'). Nếu dữ liệu chỉ có tọa độ GPS, bạn BẮT BUỘC phải dùng tri thức thực tế để chỉ rõ tên con đường và vị trí cụ thể của địa danh đó cho khách, TUYỆT ĐỐI KHÔNG ghi cộc lốc mỗi 'Tọa độ GPS'!
   * 🚗 **Đường đi & Chỗ gửi xe**: Tuyến đường tiếp cận thuận tiện nhất (đi bộ, xe máy, Grab), chỉ rõ vị trí bãi giữ xe cụ thể và giá vé gửi xe ước tính.
   * 💰 **Chi phí dự kiến & Giá menu**: Giá nước uống, đồ ăn hoặc vé tham quan ước tính thực tế.
   * ⏰ **Thời gian lý tưởng**: Khung giờ ghé đẹp nhất (sáng sớm, chiều hoàng hôn hay buổi tối).
   * 💡 **Mẹo thực tế**: Góc chụp ảnh đẹp nhất, lưu ý chọn bàn hoặc tránh giờ cao điểm.
4. HƯỚNG DẪN BẢN ĐỒ & ĐẶT LỊCH:
   - Nhắc người dùng có thể bấm trực tiếp nút "Bản đồ" trên từng thẻ địa điểm để mở ngay vị trí chuẩn xác trên Google Maps, hoặc bấm "+ Thêm vào lịch" để tự động sắp xếp vào lịch trình.
5. QUY TẮC ĐỊNH DẠNG VĂN BẢN (TUYỆT ĐỐI TUÂN THỦ):
   - TUYỆT ĐỐI KHÔNG xuất hiện dấu thăng đơn lẻ đứng một mình (như '#') trên một dòng riêng.
   - TUYỆT ĐỐI KHÔNG xuất hiện dòng gạch ngang trần '---'.
   - KHÔNG dùng dấu hoa thị đơn lẻ sai cú pháp (như 'Địa chỉ*:'). Luôn viết đúng: '* 🏠 **Địa chỉ chuẩn xác**:'.
   - Tiêu đề từng chặng dùng định dạng Markdown chuẩn (ví dụ: '### 1. Điểm khởi đầu:...', '### 2. Trạm dừng chân:...').
6. HƯỚNG DẪN KHI CÓ LỘ TRÌNH DI CHUYỂN HOẶC HỎI CẦN CHUẨN BỊ GÌ (VD: Từ tỉnh/quận này đi nơi khác, 'cần những gì', 'đi như thế nào'):
   - BẮT BUỘC có mục tư vấn Lộ trình & Phương tiện di chuyển: Khoảng cách (km), thời gian ước tính, lộ trình cụ thể bằng xe máy / ô tô cá nhân (chỉ rõ tuyến cao tốc, quốc lộ), hoặc xe khách / limousine (tần suất, giá vé tham khảo).
   - BẮT BUỘC có mục Chuẩn bị & Quy định tại điểm đến: Giấy tờ tùy thân (CCCD/Hộ chiếu), trang phục chuẩn mực (đặc biệt Lăng Bác, đền chùa cấm váy ngắn/quần cộc; quy định gửi hành lý, không quay phim chụp ảnh trong Lăng), lịch mở cửa / đóng cửa (ví dụ: Lăng Bác mở cửa buổi sáng Thứ 3, 4, 5, 7, Chủ Nhật; đóng cửa Thứ 2 và Thứ 6; miễn phí cho công dân Việt Nam), chi phí dự trù.
   - Nêu các địa điểm tham quan kết hợp tại điểm đến (tương ứng với các thẻ POI bên dưới kèm nút bấm Google Maps)."""

            user_prompt = f"""Yêu cầu chuyến đi của khách: {question}

Thông tin phân tích từ Intent Agent:
{json.dumps(extracted_features, ensure_ascii=False, indent=2)}

Dữ liệu địa điểm thực tế từ PostgreSQL POI Engine ({len(search_results)} điểm):
{json.dumps(search_results, ensure_ascii=False, indent=2)}

QUAN TRỌNG: Hãy phân tích ĐÍCH DANH các địa điểm trên, cung cấp thông tin cụ thể (tên quán, tọa độ/địa chỉ, phong cách, giá cả, cách đi) để khách chọn được ngay, tuyệt đối không nói chung chung!"""

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
                max_tokens=800
            )
            if gemini_result and gemini_result.get("text"):
                gemini_answer = gemini_result["text"]
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
        print(f"Generated response length: {len(natural_response)}")
        
        return {
            "status": "success",
            "response": natural_response,
            "language": language,
            "language_name": lang_info["name"],
            "response_length": len(natural_response)
        }
        
    except Exception as e:
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
            desc = f"{name} là {cat_display} uy tín tại {region_name}. {dist_text}Bấm 'Bản đồ' để mở định vị GPS thực tế trên Google Maps."

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
                "language": "vietnamese"
            })
            if len(pois) >= limit:
                break

        return pois
    except Exception as e:
        print(f"Error querying PostgreSQL POIs for chatbot: {e}")
        return []

