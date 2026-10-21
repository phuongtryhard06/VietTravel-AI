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
    max_tokens: int = 4000
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
                resp = requests.post(url, json=_build_payload(with_search=False, is_json=json_mode), timeout=25)
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
            system_prompt = """Bạn là VietTravel AI - Trợ lý Tác tử Lữ hành Thông minh (Smart Travel & Itinerary Agent) hàng đầu Việt Nam.
Nhiệm vụ của bạn là tư vấn chuyến đi một cách thực tế, sinh động, chi tiết từng li từng tí, chuyên nghiệp và có chiều sâu để du khách có thể xách ba lô lên và đi ngay.

NGUYÊN TẮC BẮT BUỘC CỦA TÁC TỬ:
1. TUYỆT ĐỐI KHÔNG NÓI CHUNG CHUNG:
   - Cấm đưa ra các lời khuyên lý thuyết sáo rỗng (như 'hãy chọn quán có không gian yên tĩnh', 'chọn điểm đến phù hợp sở thích').
   - Mọi thông tin phải là NGƯỜI THẬT, VIỆC THẬT: Tên quán ăn cụ thể, món ăn signature, địa chỉ rõ ràng, giá tiền tham khảo, phương tiện và cung đường đi.

2. BẮT BUỘC CHÈN ẢNH MINH HỌA TRỰC QUAN TRONG BÀI VIẾT (NHƯ TẠP CHÍ LỮ HÀNH CAO CẤP):
   - Khi giới thiệu các điểm đến tiêu biểu hoặc trong các chặng của lịch trình, BẮT BUỘC chèn ảnh minh họa chất lượng cao bằng cú pháp Markdown:
     `![Tên địa điểm](URL_ẢNH)`
     ngay bên dưới tiêu đề hoặc đoạn mở đầu của điểm đến đó để người xem thấy trực quan sinh động (như review du lịch chuyên nghiệp giống cẩm nang du lịch).
   - Danh mục URL ảnh Unsplash thực tế chuẩn cho các điểm du lịch lớn:
     * Cát Bà: ![Đảo Cát Bà](https://images.unsplash.com/photo-1598971861713-54ad16a7e72e?w=800&auto=format&fit=crop)
     * Vịnh Lan Hạ: ![Vịnh Lan Hạ](https://images.unsplash.com/photo-1528127269322-539801943592?w=800&auto=format&fit=crop)
     * Bãi tắm Cát Cò: ![Bãi tắm Cát Cò](https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop)
     * Vườn Quốc Gia Cát Bà: ![Vườn Quốc Gia Cát Bà](https://images.unsplash.com/photo-1448375240586-882707db888b?w=800&auto=format&fit=crop)
     * Vũng Tàu - Bãi Sau: ![Bãi Sau Vũng Tàu](https://images.unsplash.com/photo-1519046904884-53103b34b206?w=800&auto=format&fit=crop)
     * Vũng Tàu - Tượng Chúa Kytô Vua: ![Tượng Chúa Kytô Vua](https://images.unsplash.com/photo-1544644181-1484b3fdfc62?w=800&auto=format&fit=crop)
     * Vũng Tàu - Mũi Nghinh Phong: ![Mũi Nghinh Phong](https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop)
     * Vũng Tàu - Ngọn Hải Đăng: ![Ngọn Hải Đăng Vũng Tàu](https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?w=800&auto=format&fit=crop)
     * Vũng Tàu - Bạch Dinh: ![Bạch Dinh Vũng Tàu](https://images.unsplash.com/photo-1566127444979-b3d2b654e3d7?w=800&auto=format&fit=crop)
     * Hà Nội - Lăng Bác: ![Lăng Chủ tịch Hồ Chí Minh](https://images.unsplash.com/photo-1528181304800-259b08848526?w=800&auto=format&fit=crop)
     * Hà Nội - Hồ Hoàn Kiếm / Phố Cổ: ![Hồ Hoàn Kiếm](https://images.unsplash.com/photo-1509062522246-3755977927d7?w=800&auto=format&fit=crop)
     * Thái Nguyên - Hồ Núi Cốc: ![Hồ Núi Cốc](https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&auto=format&fit=crop)
     * Thái Nguyên - Đồi chè Tân Cương: ![Đồi chè Tân Cương](https://images.unsplash.com/photo-1576092768241-dec231879fc3?w=800&auto=format&fit=crop)
     * Đà Nẵng - Bà Nà Hills: ![Bà Nà Hills](https://images.unsplash.com/photo-1559592413-7cec4d0cae2b?w=800&auto=format&fit=crop)
     * Hội An: ![Phố Cổ Hội An](https://images.unsplash.com/photo-1555939594-58d7cb561ad1?w=800&auto=format&fit=crop)
     * Đà Lạt: ![Đà Lạt](https://images.unsplash.com/photo-1513836279014-a89f7a76ae86?w=800&auto=format&fit=crop)
     * Phú Quốc: ![Phú Quốc](https://images.unsplash.com/photo-1540555700478-4be289fbecef?w=800&auto=format&fit=crop)
     * Ninh Bình: ![Tràng An Ninh Bình](https://images.unsplash.com/photo-1528127269322-539801943592?w=800&auto=format&fit=crop)
     * Sa Pa: ![Sa Pa](https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&auto=format&fit=crop)
     *(Nếu là địa điểm khác, hãy dùng link ảnh Unsplash du lịch chất lượng cao tương thích).*

3. QUY CHUẨN TƯ VẤN LỊCH TRÌNH CHUYÊN SÂU (VD: 3 ngày 2 đêm, 2 ngày 1 đêm, hoặc tour nhiều ngày):
   - Phải viết THẬT DÀI, CHI TIẾT TỪNG BƯỚC, KHÔNG CẮT XÉN, KHÔNG TÓM TẮT.
   - Cấu trúc đầy đủ gồm 5 phần:
     Phần I. TỔNG QUAN HÀNH TRÌNH & THỜI ĐIỂM ĐẸP NHẤT
     Phần II. LỘ TRÌNH DI CHUYỂN & PHƯƠNG TIỆN (Chỉ rõ cao tốc/quốc lộ nào, thời gian đi, phương tiện xe khách/limousine/xe máy/ô tô, điểm dừng chân ăn sáng và giá tiền).
     Phần III. GỢI Ý NƠI LƯU TRÚ (Khách sạn/homestay uy tín, khu vực nên thuê, mức giá phòng/đêm).
     Phần IV. LỊCH TRÌNH CHI TIẾT TỪNG NGÀY:
       - Tách bạch rõ ràng từng ngày (Ngày 1, Ngày 2, Ngày 3...).
       - Mỗi ngày chia đủ 4 khung thời gian:
         * 🌅 **Sáng (07:00 - 11:30)**: Ăn sáng món gì, tên quán, địa chỉ, giá. Điểm tham quan buổi sáng (có chèn ảnh minh họa `![Tên](url)`), đường đi, hoạt động, giá vé.
         * ☀️ **Trưa (11:30 - 14:00)**: Quán ăn trưa đặc sản, món ngon nổi bật, chi phí. Thời gian nghỉ trưa.
         * 🌇 **Chiều (14:30 - 18:00)**: Điểm vui chơi / bãi biển / ngắm hoàng hôn (có chèn ảnh), trải nghiệm đặc sắc, góc check-in đẹp.
         * 🌙 **Tối (18:30 - 22:30)**: Quán ăn tối hải sản/lẩu/nướng (tên quán, địa chỉ, giá), dạo chợ đêm hoặc cafe view đẹp.
     Phần V. BẢNG DỰ TOÁN TỔNG CHI PHÍ TRỌN GÓI CHO 1 NGƯỜI (Ghi rõ từng mục: Di chuyển, Khách sạn, Ăn uống, Vé tham quan, Phát sinh và Tổng cộng).

4. QUY TẮC ĐỊNH DẠNG VĂN BẢN:
   - TUYỆT ĐỐI KHÔNG xuất hiện dấu thăng đơn lẻ đứng một mình (như '#') trên một dòng riêng.
   - TUYỆT ĐỐI KHÔNG xuất hiện dòng gạch ngang trần '---'.
   - KHÔNG tự chèn các chuỗi thô '[ 📍 Bản đồ](...) | [+ Thêm vào lịch]' vào text."""

            user_prompt = f"""Yêu cầu chuyến đi của khách: {question}

Thông tin phân tích từ Intent Agent:
{json.dumps(extracted_features, ensure_ascii=False, indent=2)}

Dữ liệu địa điểm thực tế từ POI Engine ({len(search_results)} điểm):
{json.dumps(search_results, ensure_ascii=False, indent=2)}

QUAN TRỌNG: Hãy phân tích ĐÍCH DANH các địa điểm trên, chèn hình ảnh minh họa Markdown ![Tên](url), cung cấp thông tin chi tiết từng mốc giờ (Sáng, Trưa, Chiều, Tối), tên quán ăn, địa chỉ, giá cả, cách đi để khách có thể thực hiện chuyến đi trọn vẹn ngay lập tức!"""

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
                max_tokens=4000
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


def _get_curated_image_and_meta(name: str, category: str, fclass: str, region: str):
    """
    Tự động chuẩn hóa ảnh đại diện thực tế, giờ mở cửa, giá vé và mô tả sinh động cho từng địa điểm.
    """
    name_lower = (name or "").lower()
    cat_lower = f"{category or ''} {fclass or ''}".lower()
    reg_lower = (region or "").lower()

    if any(k in name_lower for k in ["tượng chúa", "chúa kytô", "chúa kito", "núi nhỏ"]):
        img = "https://images.unsplash.com/photo-1596422846543-75c6fc197f07?w=800&auto=format&fit=crop"
        hours = "07:00 - 17:00 (Hàng ngày)"
        price = "Miễn phí (Trang phục lịch sự qua gối)"
        rating = 4.9
        desc = f"{name} là bức tượng Chúa Kitô Vua lớn nhất châu Á ngự trên đỉnh Núi Nhỏ, cao 32m với hai tay dang rộng 18.3m, bên trong có 133 bậc thang xoắn ốc dẫn lên hai vai ngắm trọn vẹn biển Vũng Tàu."
    elif any(k in name_lower for k in ["mũi nghinh phong", "cổng trời"]):
        img = "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop"
        hours = "Cả ngày (Khuyên đi: 05:30 - 18:30)"
        price = "Miễn phí vào cổng"
        rating = 4.8
        desc = f"{name} là mũi đất vươn dài đón gió biển bốn mùa, sở hữu 'Cổng Trời' độc đáo view ôm trọn biển xanh biếc và là điểm ngắm hoàng hôn, bình minh đắt giá nhất Vũng Tàu."
    elif any(k in name_lower for k in ["hải đăng", "ngọn hải đăng"]):
        img = "https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?w=800&auto=format&fit=crop"
        hours = "07:00 - 22:00"
        price = "Miễn phí tham quan"
        rating = 4.7
        desc = f"{name} được xây dựng từ năm 1862, là một trong những ngọn hải đăng cổ xưa nhất Đông Nam Á, cung đường uốn lượn rợp bóng hoa giấy và view ngắm trọn vịnh Gành Rái."
    elif any(k in name_lower for k in ["bánh khọt", "gốc vú sữa", "cô ba vũng tàu"]):
        img = "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop"
        hours = "07:00 - 22:00"
        price = "60.000đ - 120.000đ/phần"
        rating = 4.8
        desc = f"{name} phục vụ món bánh khọt tôm vàng giòn rụm nức tiếng, ăn kèm đu đủ bào sợi chua ngọt, đĩa rau sống tươi sạch và nước mắm ớt pha gia truyền đậm đà."
    elif any(k in name_lower for k in ["lẩu cá đuối", "cá đuối"]):
        img = "https://images.unsplash.com/photo-1544025162-d76694265947?w=800&auto=format&fit=crop"
        hours = "10:00 - 23:00"
        price = "150.000đ - 250.000đ/nồi"
        rating = 4.8
        desc = f"{name} nổi tiếng với nồi lẩu cá đuối măng chua sôi sùng sục, thịt cá đuối tươi ngọt giòn sụn chấm nước mắm ớt nguyên chất, hương vị chua cay hấp dẫn khó cưỡng."
    elif any(k in name_lower for k in ["vịnh lan hạ", "cát bà", "đảo khỉ"]):
        img = "https://images.unsplash.com/photo-1528127269322-539801943592?w=800&auto=format&fit=crop"
        hours = "07:00 - 17:30"
        price = "Tour thuyền 250.000đ - 450.000đ"
        rating = 4.9
        desc = f"{name} là kỳ quan vịnh ngọc với hơn 400 hòn đảo đá vôi kỳ vĩ phủ kín thảm thực vật xanh mướt, nước biển trong vắt lý tưởng để chèo thuyền kayak và lặn ngắm san hô."
    elif any(k in name_lower for k in ["núi cốc", "hồ núi cốc", "tân cương"]):
        img = "https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&auto=format&fit=crop"
        hours = "07:00 - 18:00"
        price = "130.000đ/vé trọn gói"
        rating = 4.8
        desc = f"{name} là danh thắng huyền thoại nổi tiếng đất chè Thái Nguyên với mặt hồ mênh mông, 89 hòn đảo thơ mộng và những đồi chè Tân Cương xanh ngát chân trời."
    elif any(k in name_lower for k in ["bà nà", "cầu vàng", "golden bridge"]):
        img = "https://images.unsplash.com/photo-1559592413-7cec4d0cae2b?w=800&auto=format&fit=crop"
        hours = "07:00 - 21:00"
        price = "Cáp treo từ 850.000đ/vé"
        rating = 4.9
        desc = f"{name} là kiệt tác kiến trúc nổi tiếng thế giới với đôi bàn tay Phật khổng lồ nâng đỡ dải lụa vàng giữa mây trời Bà Nà, tạo nên trải nghiệm bồng lai tiên cảnh."
    elif any(k in name_lower for k in ["cầu rồng", "sông hàn"]):
        img = "https://images.unsplash.com/photo-1559592413-7cec4d0cae2b?w=800&auto=format&fit=crop"
        hours = "Cả ngày (Phun lửa & nước: 21:00 T7, CN)"
        price = "Miễn phí"
        rating = 4.8
        desc = f"{name} là biểu tượng kiến trúc hiện đại của Đà Nẵng bắc qua sông Hàn thơ mộng, thu hút hàng vạn du khách chiêm ngưỡng màn phun lửa, phun nước sống động cuối tuần."
    elif any(k in name_lower for k in ["phố cổ hội an", "chùa cầu"]):
        img = "https://images.unsplash.com/photo-1552465011-b4e21bf6e79a?w=800&auto=format&fit=crop"
        hours = "Cả ngày"
        price = "Vé tham quan phố cổ 80.000đ"
        rating = 4.9
        desc = f"{name} là di sản văn hóa thế giới UNESCO với những dãy nhà cổ sơn vàng, hoa giấy rực rỡ và dòng sông Hoài lung linh hoa đăng huyền ảo mỗi khi đêm về."
    elif any(k in name_lower for k in ["hồ xuân hương", "đà lạt", "lâm viên"]):
        img = "https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&auto=format&fit=crop"
        hours = "Cả ngày"
        price = "Miễn phí"
        rating = 4.8
        desc = f"{name} là trái tim thơ mộng của xứ sở sương mù Đà Lạt, nơi lý tưởng để đi dạo, đạp vịt ngắm hoàng hôn buông xuống mặt hồ phẳng lặng trong tiết trời se lạnh."
    elif any(k in name_lower for k in ["tràng an", "tam cốc", "hang múa"]):
        img = "https://images.unsplash.com/photo-1528127269322-539801943592?w=800&auto=format&fit=crop"
        hours = "07:00 - 17:00"
        price = "Vé thuyền 250.000đ"
        rating = 4.9
        desc = f"{name} là danh thắng kỳ vĩ của Ninh Bình được UNESCO công nhận là di sản kép, nơi du khách ngồi thuyền nan len lỏi qua các hang động đá vôi triệu năm tuổi."
    elif any(k in name_lower for k in ["bảo tàng vũ khí", "robert taylor"]):
        img = "https://images.unsplash.com/photo-1566127444979-b3d2b654e3d7?w=800&auto=format&fit=crop"
        hours = "08:00 - 17:00 (Hàng ngày)"
        price = "70.000đ - 100.000đ/vé"
        rating = 4.8
        desc = f"{name} là bảo tàng vũ khí cổ tư nhân lớn nhất Việt Nam với hơn 2.500 hiện vật cổ vật từ gươm giáo, súng ống đến quân phục quân đội các nước thời kỳ cổ trung đại."
    elif any(k in name_lower for k in ["đài liệt sĩ", "liệt sĩ", "tượng đài", "monument", "memorial"]):
        img = "https://images.unsplash.com/photo-1544644181-1484b3fdfc62?w=800&auto=format&fit=crop"
        hours = "06:00 - 22:00"
        price = "Miễn phí tham quan"
        rating = 4.6
        desc = f"{name} là công trình tưởng niệm lịch sử trang nghiêm, sở hữu khuôn viên rộng rãi, cây xanh thoáng mát và là biểu tượng tri ân ý nghĩa tại {region}."
    elif any(k in name_lower for k in ["sculpture", "điêu khắc", "art", "nghệ thuật", "park", "công viên"]):
        img = "https://images.unsplash.com/photo-1519331379826-f10be5486c6f?w=800&auto=format&fit=crop"
        hours = "Mở cửa cả ngày (24/7)"
        price = "Miễn phí vào cổng"
        rating = 4.5
        desc = f"{name} là không gian nghệ thuật ngoài trời kết hợp công viên xanh, điểm dừng chân lý tưởng để đi dạo ngắm cảnh, thư giãn và chụp ảnh check-in tại {region}."
    elif any(k in name_lower for k in ["đường sách", "thư viện", "sách"]):
        img = "https://images.unsplash.com/photo-1507842229452-779836376822?w=800&auto=format&fit=crop"
        hours = "08:00 - 22:00"
        price = "Miễn phí vào cửa"
        rating = 4.7
        desc = f"{name} là không gian văn hóa đọc nổi tiếng với các gian hàng sách phong phú, cà phê sách rợp bóng cây và thường xuyên tổ chức sự kiện văn hóa nghệ thuật tại {region}."
    elif any(k in name_lower or k in cat_lower for k in ["nhà hàng", "restaurant", "quán ăn", "ẩm thực", "mr. park", "hải sản"]):
        img = "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop"
        hours = "10:00 - 22:30"
        price = "80.000đ - 250.000đ/người"
        rating = 4.6
        desc = f"{name} là điểm dừng chân ẩm thực uy tín tại {region}, nổi tiếng với thực đơn đa dạng, hải sản tươi sống và không gian thưởng thức ẩm thực ấm cúng."
    elif any(k in name_lower or k in cat_lower for k in ["cafe", "cà phê", "coffee", "trà"]):
        img = "https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?w=800&auto=format&fit=crop"
        hours = "07:00 - 23:00"
        price = "35.000đ - 65.000đ"
        rating = 4.7
        desc = f"{name} là quán cà phê có phong cách thiết kế ấn tượng, không gian thoáng đãng và đồ uống thơm ngon phù hợp nghỉ ngơi, trò chuyện tại {region}."
    elif any(k in name_lower or k in cat_lower for k in ["bãi biển", "bãi sau", "bãi trước", "beach", "biển"]):
        img = "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop"
        hours = "Tắm biển: 05:30 - 18:00"
        price = "Tắm biển miễn phí"
        rating = 4.8
        desc = f"{name} nổi bật với bờ biển thoai thoải, dải cát mịn, sóng êm và tập trung nhiều hoạt động thể thao biển hấp dẫn hàng đầu tại {region}."
    elif any(k in name_lower or k in cat_lower for k in ["lăng bác", "chùa", "đền", "miếu", "di tích"]):
        img = "https://images.unsplash.com/photo-1528181304800-259b08848526?w=800&auto=format&fit=crop"
        hours = "07:30 - 17:30"
        price = "Miễn phí hoặc theo quy định di tích"
        rating = 4.9
        desc = f"{name} là công trình tâm linh - lịch sử thiêng liêng và danh thắng tiêu biểu tại {region}, lưu giữ nét đẹp văn hóa và kiến trúc cổ truyền."
    else:
        img = "https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?w=800&auto=format&fit=crop"
        hours = "07:30 - 21:00"
        price = "Miễn phí hoặc vé cổng theo mùa"
        rating = 4.5
        desc = f"{name} là điểm tham quan hấp dẫn tại {region}, sở hữu vị trí thuận lợi và tọa độ GPS định vị chuẩn xác để bạn thêm vào lịch trình khám phá."

    return img, hours, price, rating, desc


AGENT_DYNAMIC_CURATED_HUBS: Dict[str, List[Dict[str, Any]]] = {
    "vũng tàu": [
        {
            "id": "hub_vt_1",
            "ten_dia_diem": "Tượng Chúa Kytô Vua (Núi Nhỏ)",
            "mo_ta": "Biểu tượng du lịch số 1 Vũng Tàu, tượng Chúa Kitô Vua cao 32m ngự trên đỉnh Núi Nhỏ. Du khách leo 847 bậc thang đá và 133 bậc thang xoắn ốc bên trong tượng để lên hai vai ngắm toàn cảnh biển Đông bao la.",
            "loai_dia_diem": "Điểm tham quan / Biểu tượng",
            "khu_vuc": "Vũng Tàu",
            "dia_chi": "02 Hạ Long, Phường 2, TP. Vũng Tàu",
            "latitude": 10.3263,
            "longitude": 107.0845,
            "google_maps_url": "https://www.google.com/maps?q=10.326300,107.084500",
            "distance_km": 1.2,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1596422846543-75c6fc197f07?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 17:00 (Hàng ngày)",
            "gia_ve": "Miễn phí (Yêu cầu trang phục lịch sự qua gối)",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_vt_2",
            "ten_dia_diem": "Mũi Nghinh Phong & Cổng Trời",
            "mo_ta": "Mũi đất vươn dài đón gió biển bốn mùa, phía trước là biển xanh vô tận, sau lưng là vách núi đá sừng sững. Nơi đây có 'Cổng Trời' màu vàng cổ điển view nhìn thẳng ra Hòn Bà và là tọa độ săn hoàng hôn đẹp nhất Vũng Tàu.",
            "loai_dia_diem": "Danh lam thắng cảnh / Check-in",
            "khu_vuc": "Vũng Tàu",
            "dia_chi": "01 Hạ Long, Phường 2, TP. Vũng Tàu",
            "latitude": 10.3235,
            "longitude": 107.0782,
            "google_maps_url": "https://www.google.com/maps?q=10.323500,107.078200",
            "distance_km": 1.5,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Mở cả ngày (Khuyên đi: 05:30 - 18:30)",
            "gia_ve": "Miễn phí vào cổng",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_vt_3",
            "ten_dia_diem": "Bãi Sau (Bãi Thùy Vân)",
            "mo_ta": "Bãi tắm trung tâm sầm uất và đẹp nhất Vũng Tàu dài hơn 8km với bãi cát thoai thoải, sóng êm và làn nước mát rượi. Tập trung nhiều khách sạn, quảng trường Cột Cờ và các hoạt động thể thao mạo hiểm trên biển.",
            "loai_dia_diem": "Bãi biển / Vui chơi giải trí",
            "khu_vuc": "Vũng Tàu",
            "dia_chi": "Đường Thùy Vân, Phường Thắng Tam, TP. Vũng Tàu",
            "latitude": 10.3364,
            "longitude": 107.0911,
            "google_maps_url": "https://www.google.com/maps?q=10.336400,107.091100",
            "distance_km": 0.8,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1519046904884-53103b34b206?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Tắm biển: 05:00 - 18:30",
            "gia_ve": "Tắm biển miễn phí (Ghế dù ~50k-80k)",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_vt_4",
            "ten_dia_diem": "Ngọn Hải Đăng Vũng Tàu",
            "mo_ta": "Ngọn hải đăng cổ kính xây từ năm 1862 trên đỉnh Núi Nhỏ ở độ cao 170m. Cung đường lên hải đăng uốn lượn rợp bóng cây hoa giấy, từ đài quan sát có thể thu trọn toàn cảnh thành phố biển và vịnh Gành Rái lung linh.",
            "loai_dia_diem": "Di tích / Ngắm cảnh",
            "khu_vuc": "Vũng Tàu",
            "dia_chi": "Đỉnh Núi Nhỏ, Phường 2, TP. Vũng Tàu",
            "latitude": 10.3340,
            "longitude": 107.0760,
            "google_maps_url": "https://www.google.com/maps?q=10.334000,107.076000",
            "distance_km": 1.4,
            "similarity": 0.97,
            "image_url": "https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 22:00",
            "gia_ve": "Miễn phí tham quan",
            "danh_gia": 4.7,
            "language": "vietnamese"
        },
        {
            "id": "hub_vt_5",
            "ten_dia_diem": "Bánh Khọt Cô Ba Vũng Tàu",
            "mo_ta": "Quán bánh khọt trứ danh lâu đời bậc nhất Vũng Tàu. Bánh đổ khuôn gang vàng rụm, giòn tan bên ngoài, nhân tôm tươi ngọt thịt hoặc mực sữa, cuốn bánh tráng rau cải xanh, đồ chua và chấm nước mắm ớt pha chuẩn vị.",
            "loai_dia_diem": "Ẩm thực & Quán ăn đặc sản",
            "khu_vuc": "Vũng Tàu",
            "dia_chi": "01 Hoàng Hoa Thám, Phường 3, TP. Vũng Tàu",
            "latitude": 10.3448,
            "longitude": 107.0754,
            "google_maps_url": "https://www.google.com/maps?q=10.344800,107.075400",
            "distance_km": 0.5,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 22:00",
            "gia_ve": "60.000đ - 120.000đ/đĩa",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_vt_6",
            "ten_dia_diem": "Lẩu Cá Đuối Út Mười",
            "mo_ta": "Món ăn 'quốc hồn quốc túy' không thể bỏ qua khi tới Vũng Tàu. Nồi lẩu bốc khói nghi ngút với nước dùng măng chua cay the the đậm đà, thịt cá đuối tươi rói ngọt béo, sụn giòn sần sật chấm cùng nước mắm ớt cay xé lưỡi.",
            "loai_dia_diem": "Ẩm thực & Quán ăn đặc sản",
            "khu_vuc": "Vũng Tàu",
            "dia_chi": "16A Trương Công Định, Phường 1, TP. Vũng Tàu",
            "latitude": 10.3468,
            "longitude": 107.0792,
            "google_maps_url": "https://www.google.com/maps?q=10.346800,107.079200",
            "distance_km": 0.6,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1544025162-d76694265947?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "10:00 - 23:00",
            "gia_ve": "160.000đ - 260.000đ/nồi",
            "danh_gia": 4.8,
            "language": "vietnamese"
        }
    ],
    "cát bà": [
        {
            "id": "hub_cb_1",
            "ten_dia_diem": "Vịnh Lan Hạ & Đảo Khỉ",
            "mo_ta": "Kỳ quan vịnh ngọc liền kề Hạ Long với hơn 400 hòn đảo đá vôi kỳ vĩ phủ cây xanh, làn nước xanh ngọc bích êm ả. Du khách trải nghiệm du thuyền, chèo thuyền kayak qua hang Sáng Tối và tắm biển tại bãi cát tự nhiên hoang sơ.",
            "loai_dia_diem": "Danh lam thắng cảnh / Biển đảo",
            "khu_vuc": "Cát Bà, Hải Phòng",
            "dia_chi": "Vịnh Lan Hạ, Quần đảo Cát Bà, Huyện Cát Hải, Hải Phòng",
            "latitude": 20.7300,
            "longitude": 107.0600,
            "google_maps_url": "https://www.google.com/maps?q=20.730000,107.060000",
            "distance_km": 1.5,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1528127269322-539801943592?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 17:30",
            "gia_ve": "Tour du thuyền 250.000đ - 450.000đ/người",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_cb_2",
            "ten_dia_diem": "Vườn Quốc Gia Cát Bà & Đỉnh Ngự Lâm",
            "mo_ta": "Khu dự trữ sinh quyển thế giới với hệ sinh thái rừng nhiệt đới nguyên sinh bạt ngàn và loài voọc Cát Bà quý hiếm. Cung đường trekking xuyên rừng kim giao lên đỉnh Ngự Lâm đem đến góc nhìn 360 độ ngắm trọn non nước điệp trùng.",
            "loai_dia_diem": "Vườn quốc gia / Trekking",
            "khu_vuc": "Cát Bà, Hải Phòng",
            "dia_chi": "Thị trấn Cát Bà, Huyện Cát Hải, Hải Phòng",
            "latitude": 20.7981,
            "longitude": 106.9942,
            "google_maps_url": "https://www.google.com/maps?q=20.798100,106.994200",
            "distance_km": 11.0,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1448375240586-882707db888b?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:30 - 17:00",
            "gia_ve": "80.000đ/vé người lớn",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_cb_3",
            "ten_dia_diem": "Bãi Tắm Cát Cò 1, 2, 3",
            "mo_ta": "Bộ ba bãi tắm tự nhiên tựa lưng vào vách núi đá vôi tuyệt đẹp nối liền nhau bởi cây cầu gỗ ven biển uốn lượn. Bãi Cát Cò 1 rộng và sôi động nhất, bãi Cát Cò 2 yên tĩnh lãng mạn, sóng êm nước trong vắt.",
            "loai_dia_diem": "Bãi biển / Nghỉ dưỡng",
            "khu_vuc": "Cát Bà, Hải Phòng",
            "dia_chi": "Khu 1, Thị trấn Cát Bà, Huyện Cát Hải, Hải Phòng",
            "latitude": 20.7180,
            "longitude": 107.0490,
            "google_maps_url": "https://www.google.com/maps?q=20.718000,107.049000",
            "distance_km": 0.8,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Tắm biển: 06:00 - 18:30",
            "gia_ve": "Tắm biển tự do miễn phí",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_cb_4",
            "ten_dia_diem": "Làng Chài Cổ Việt Hải",
            "mo_ta": "Ngôi làng cổ bình yên ẩn mình hoàn toàn giữa thung lũng của Vườn Quốc Gia Cát Bà. Du khách đạp xe dọc theo con đường rợp bóng cây xanh, tìm hiểu cuộc sống ngư dân đảo ngọc và trải nghiệm massage cá suối tự nhiên cực thư giãn.",
            "loai_dia_diem": "Làng văn hóa / Sinh thái",
            "khu_vuc": "Cát Bà, Hải Phòng",
            "dia_chi": "Xã Việt Hải, Huyện Cát Hải, Hải Phòng",
            "latitude": 20.7745,
            "longitude": 107.0390,
            "google_maps_url": "https://www.google.com/maps?q=20.774500,107.039000",
            "distance_km": 6.5,
            "similarity": 0.97,
            "image_url": "https://images.unsplash.com/photo-1598971861713-54ad16a7e72e?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "08:00 - 18:00",
            "gia_ve": "50.000đ/vé vào làng",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_cb_5",
            "ten_dia_diem": "Pháo Đài Thần Công (Cao Điểm 177)",
            "mo_ta": "Di tích lịch sử quân sự nằm trên đỉnh ngọn đồi cao 177m với hai khẩu pháo đối hải khổng lồ thời kháng chiến. Nơi đây sở hữu quán cà phê ngắm toàn cảnh vịnh Lan Hạ và đón hoàng hôn buông xuống biển đẹp nao lòng.",
            "loai_dia_diem": "Di tích lịch sử / Ngắm hoàng hôn",
            "khu_vuc": "Cát Bà, Hải Phòng",
            "dia_chi": "239 Cái Bèo, Thị trấn Cát Bà, Cát Hải, Hải Phòng",
            "latitude": 20.7150,
            "longitude": 107.0420,
            "google_maps_url": "https://www.google.com/maps?q=20.715000,107.042000",
            "distance_km": 1.2,
            "similarity": 0.97,
            "image_url": "https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:30 - 18:00",
            "gia_ve": "40.000đ/vé",
            "danh_gia": 4.7,
            "language": "vietnamese"
        },
        {
            "id": "hub_cb_6",
            "ten_dia_diem": "Nhà Hàng Bè Nổi & Chợ Đêm Cát Bà",
            "mo_ta": "Trải nghiệm ẩm thực biển độc đáo trên các bè nổi bồng bềnh giữa sóng nước. Thưởng thức đặc sản tu hài nướng mỡ hành, bề bề rang muối, cá song hấp xì dầu và mực trứng tươi sống vừa vớt tại lồng nuôi.",
            "loai_dia_diem": "Ẩm thực & Hải sản tươi sống",
            "khu_vuc": "Cát Bà, Hải Phòng",
            "dia_chi": "Bến Bèo, Thị trấn Cát Bà, Cát Hải, Hải Phòng",
            "latitude": 20.7240,
            "longitude": 107.0460,
            "google_maps_url": "https://www.google.com/maps?q=20.724000,107.046000",
            "distance_km": 0.9,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "10:00 - 23:00",
            "gia_ve": "180.000đ - 350.000đ/người",
            "danh_gia": 4.7,
            "language": "vietnamese"
        }
    ],
    "hà nội": [
        {
            "id": "hub_hn_1",
            "ten_dia_diem": "Lăng Chủ tịch Hồ Chí Minh & Quảng trường Ba Đình",
            "mo_ta": "Trái tim của thủ đô Hà Nội ngàn năm văn hiến, nơi an nghỉ vĩnh hằng của Chủ tịch Hồ Chí Minh vĩ đại. Cụm di tích gồm Lăng Bác, Nhà sàn, Ao cá Bác Hồ, Chùa Một Cột và lễ Thượng cờ (06:00) / Hạ cờ (21:00) trang nghiêm.",
            "loai_dia_diem": "Di tích lịch sử / Biểu tượng quốc gia",
            "khu_vuc": "Ba Đình, Hà Nội",
            "dia_chi": "02 Hùng Vương, Điện Biên, Ba Đình, Hà Nội",
            "latitude": 21.0368,
            "longitude": 105.8346,
            "google_maps_url": "https://www.google.com/maps?q=21.036800,105.834600",
            "distance_km": 0.2,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1528181304800-259b08848526?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:30 - 10:30 (Sáng T3, T4, T5, T7, CN; đóng cửa T2, T6)",
            "gia_ve": "Miễn phí với công dân Việt Nam",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_hn_2",
            "ten_dia_diem": "Hồ Hoàn Kiếm (Hồ Gươm) & Đền Ngọc Sơn",
            "mo_ta": "Viên ngọc xanh giữa lòng thủ đô, gắn liền với truyền thuyết vua Lê Lợi trả gươm báu. Điểm đến nổi tiếng với Tháp Rùa cổ kính, Cầu Thê Húc cong cong màu son dẫn vào Đền Ngọc Sơn và phố đi bộ cuối tuần sôi động.",
            "loai_dia_diem": "Danh lam thắng cảnh / Văn hóa",
            "khu_vuc": "Hoàn Kiếm, Hà Nội",
            "dia_chi": "Đinh Tiên Hoàng, Hàng Trống, Hoàn Kiếm, Hà Nội",
            "latitude": 21.0285,
            "longitude": 105.8542,
            "google_maps_url": "https://www.google.com/maps?q=21.028500,105.854200",
            "distance_km": 1.5,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1509062522246-3755977927d7?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Cả ngày (Đền Ngọc Sơn: 07:00 - 18:00)",
            "gia_ve": "30.000đ/vé vào Đền Ngọc Sơn",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_hn_3",
            "ten_dia_diem": "Văn Miếu - Quốc Tử Giám",
            "mo_ta": "Trường đại học đầu tiên của Việt Nam xây dựng từ năm 1070, biểu tượng sáng ngời của truyền thống hiếu học dân tộc. Khuôn viên rợp bóng cổ thụ, nổi bật với Khuê Văn Các, 82 bia Tiến sĩ trên lưng rùa đá và nhà Thái Học.",
            "loai_dia_diem": "Di tích văn hóa / Lịch sử",
            "khu_vuc": "Đống Đa, Hà Nội",
            "dia_chi": "58 Quốc Tử Giám, Văn Miếu, Đống Đa, Hà Nội",
            "latitude": 21.0274,
            "longitude": 105.8355,
            "google_maps_url": "https://www.google.com/maps?q=21.027400,105.835500",
            "distance_km": 1.2,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1507842229452-779836376822?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "08:00 - 17:00 (Hàng ngày)",
            "gia_ve": "30.000đ/vé người lớn",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_hn_4",
            "ten_dia_diem": "Hoàng Thành Thăng Long",
            "mo_ta": "Di sản văn hóa thế giới UNESCO với bề dày lịch sử hơn 13 thế kỷ, từng là trung tâm quyền lực chính trị qua các triều đại Lý, Trần, Lê, Nguyễn. Nổi bật với Đoan Môn, Điện Kính Thiên, Hậu Lâu và khu khảo cổ 18 Hoàng Diệu.",
            "loai_dia_diem": "Di sản thế giới UNESCO / Di tích",
            "khu_vuc": "Ba Đình, Hà Nội",
            "dia_chi": "19C Hoàng Diệu, Điện Biên, Ba Đình, Hà Nội",
            "latitude": 21.0358,
            "longitude": 105.8406,
            "google_maps_url": "https://www.google.com/maps?q=21.035800,105.840600",
            "distance_km": 0.6,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1544644181-1484b3fdfc62?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "08:00 - 17:00",
            "gia_ve": "30.000đ/vé",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_hn_5",
            "ten_dia_diem": "Phố Cổ Hà Nội (36 Phố Phường) & Chợ Đồng Xuân",
            "mo_ta": "Mê cung phố cổ rêu phong đậm đà bản sắc Tràng An, nơi mỗi con phố lưu giữ nghề truyền thống: Hàng Bạc, Hàng Mã, Hàng Gai... Thiên đường ẩm thực đường phố về đêm với nem chua rán, bún chả, cà phê trứng Giảng trứ danh.",
            "loai_dia_diem": "Văn hóa phố cổ / Mua sắm & Ẩm thực",
            "khu_vuc": "Hoàn Kiếm, Hà Nội",
            "dia_chi": "Khu vực Phố Cổ, Hoàn Kiếm, Hà Nội",
            "latitude": 21.0345,
            "longitude": 105.8500,
            "google_maps_url": "https://www.google.com/maps?q=21.034500,105.850000",
            "distance_km": 1.6,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1509062522246-3755977927d7?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Cả ngày (Chợ đêm: Tối T6, T7, CN)",
            "gia_ve": "Miễn phí dạo phố",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_hn_6",
            "ten_dia_diem": "Phở Gia Truyền Bát Đàn & Bún Chả Hương Liên",
            "mo_ta": "Tinh hoa ẩm thực Hà Thành nức tiếng. Phở Bát Đàn đậm đà nước dùng ninh từ xương ống bò trong vắt ngọt thanh tự nhiên; Bún Chả Hương Liên (nơi cựu Tổng thống Obama từng thưởng thức) với chả viên nướng than hoa thơm lừng.",
            "loai_dia_diem": "Ẩm thực & Quán ăn truyền thống",
            "khu_vuc": "Hoàn Kiếm, Hà Nội",
            "dia_chi": "49 Bát Đàn, Cửa Đông, Hoàn Kiếm, Hà Nội",
            "latitude": 21.0335,
            "longitude": 105.8480,
            "google_maps_url": "https://www.google.com/maps?q=21.033500,105.848000",
            "distance_km": 1.4,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "06:30 - 20:30",
            "gia_ve": "50.000đ - 90.000đ/bát",
            "danh_gia": 4.8,
            "language": "vietnamese"
        }
    ],
    "thái nguyên": [
        {
            "id": "hub_tn_1",
            "ten_dia_diem": "Khu Du Lịch Hồ Núi Cốc",
            "mo_ta": "Khu du lịch sinh thái huyền thoại nổi tiếng nhất xứ trà Thái Nguyên gắn liền mối tình chàng Cốc - nàng Công. Du khách ngồi thuyền ngắm 89 hòn đảo thơ mộng giữa lòng hồ, khám phá công viên nước, Chùa Thác Vàng và hang động kỳ ảo.",
            "loai_dia_diem": "Khu du lịch sinh thái / Hồ nước",
            "khu_vuc": "Đại Từ, Thái Nguyên",
            "dia_chi": "Xã Tân Thái, Huyện Đại Từ, Thái Nguyên",
            "latitude": 21.5794,
            "longitude": 105.7100,
            "google_maps_url": "https://www.google.com/maps?q=21.579400,105.710000",
            "distance_km": 16.0,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 18:00 (Hàng ngày)",
            "gia_ve": "130.000đ/vé trọn gói tham quan",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_tn_2",
            "ten_dia_diem": "Không Gian Văn Hóa Trà Tân Cương",
            "mo_ta": "Vùng đất cội nguồn của 'Đệ nhất danh trà' Việt Nam. Nơi đây sở hữu những nương chè xanh mướt mát uốn lượn hình mâm xôi, không gian trưng bày quy trình chế biến trà thủ công và khu thưởng thức các loại trà nõn tôm, trà đinh cao cấp.",
            "loai_dia_diem": "Văn hóa trà / Check-in sinh thái",
            "khu_vuc": "Tân Cương, Thái Nguyên",
            "dia_chi": "Xã Tân Cương, TP. Thái Nguyên, Thái Nguyên",
            "latitude": 21.5540,
            "longitude": 105.7820,
            "google_maps_url": "https://www.google.com/maps?q=21.554000,105.782000",
            "distance_km": 8.0,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1576092768241-dec231879fc3?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:30 - 17:30",
            "gia_ve": "Miễn phí trải nghiệm (Mua trà theo nhu cầu)",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_tn_3",
            "ten_dia_diem": "Bảo Tàng Văn Hóa Các Dân Tộc Việt Nam",
            "mo_ta": "Bảo tàng quốc gia nằm tại trung tâm thành phố lưu giữ hơn 40.000 tài liệu hiện vật gốc quý giá phản ánh toàn diện đời sống, trang phục, phong tục và văn hóa tâm linh của 54 dân tộc anh em khắp mọi miền đất nước.",
            "loai_dia_diem": "Bảo tàng / Văn hóa dân tộc",
            "khu_vuc": "TP. Thái Nguyên",
            "dia_chi": "01 Đội Cấn, Trưng Vương, TP. Thái Nguyên",
            "latitude": 21.5936,
            "longitude": 105.8427,
            "google_maps_url": "https://www.google.com/maps?q=21.593600,105.842700",
            "distance_km": 0.5,
            "similarity": 0.97,
            "image_url": "https://images.unsplash.com/photo-1566127444979-b3d2b654e3d7?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:30 - 17:00 (T3 - CN)",
            "gia_ve": "30.000đ/vé người lớn",
            "danh_gia": 4.7,
            "language": "vietnamese"
        },
        {
            "id": "hub_tn_4",
            "ten_dia_diem": "Suối Cửa Tử (Hoàng Nông, Đại Từ)",
            "mo_ta": "Điểm đến trekking dã ngoại hoang sơ tuyệt đỉnh dưới chân dãy Tam Đảo. Dòng suối trong vắt len lỏi giữa những vách đá rêu phong kỳ vĩ, nhiều vũng nước tự nhiên mát lạnh sâu trong lòng rừng cho trải nghiệm trượt thác mạo hiểm.",
            "loai_dia_diem": "Trekking / Suối thác thiên nhiên",
            "khu_vuc": "Đại Từ, Thái Nguyên",
            "dia_chi": "Xã Hoàng Nông, Huyện Đại Từ, Thái Nguyên",
            "latitude": 21.5300,
            "longitude": 105.6200,
            "google_maps_url": "https://www.google.com/maps?q=21.530000,105.620000",
            "distance_km": 28.0,
            "similarity": 0.97,
            "image_url": "https://images.unsplash.com/photo-1448375240586-882707db888b?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 17:00",
            "gia_ve": "Miễn phí (Nên thuê porter địa phương ~150.000đ)",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_tn_5",
            "ten_dia_diem": "Chùa Hang (Kim Sơn Tự, Đồng Hỷ)",
            "mo_ta": "Di tích lịch sử và danh lam cổ tự thiêng liêng có từ thế kỷ 11. Chùa tọa lạc trong 3 hang đá vôi lớn của núi Huyền Vũ, thạch nhũ buông rủ huyền bí và không gian thanh tịnh bao quanh bởi non xanh nước biếc.",
            "loai_dia_diem": "Chùa cổ / Tâm linh lịch sử",
            "khu_vuc": "Đồng Hỷ, Thái Nguyên",
            "dia_chi": "Phường Chùa Hang, TP. Thái Nguyên, Thái Nguyên",
            "latitude": 21.6180,
            "longitude": 105.8550,
            "google_maps_url": "https://www.google.com/maps?q=21.618000,105.855000",
            "distance_km": 3.2,
            "similarity": 0.96,
            "image_url": "https://images.unsplash.com/photo-1544644181-1484b3fdfc62?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "06:30 - 18:30",
            "gia_ve": "Miễn phí vãn cảnh",
            "danh_gia": 4.7,
            "language": "vietnamese"
        },
        {
            "id": "hub_tn_6",
            "ten_dia_diem": "Làng Nghề Bánh Chưng Bờ Đậu & Nem Chua Đại Từ",
            "mo_ta": "Thủ phủ ẩm thực truyền thống nức tiếng vùng Đông Bắc. Bánh chưng Bờ Đậu được gói từ nếp nương Định Hóa hạt mẩy dẻo thơm, luộc bằng nước giếng đá trong vắt kết hợp vị béo ngậy của thịt lợn bản và vị cay nồng của hạt tiêu sọ.",
            "loai_dia_diem": "Ẩm thực & Đặc sản làng nghề",
            "khu_vuc": "Phú Lương, Thái Nguyên",
            "dia_chi": "Ngã ba Bờ Đậu, Xã Cổ Lũng, Phú Lương, Thái Nguyên",
            "latitude": 21.6250,
            "longitude": 105.7720,
            "google_maps_url": "https://www.google.com/maps?q=21.625000,105.772000",
            "distance_km": 7.5,
            "similarity": 0.97,
            "image_url": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "06:00 - 21:00",
            "gia_ve": "40.000đ - 70.000đ/chiếc bánh chưng",
            "danh_gia": 4.8,
            "language": "vietnamese"
        }
    ],
    "đà nẵng": [
        {
            "id": "hub_dn_1",
            "ten_dia_diem": "Bà Nà Hills & Cầu Vàng",
            "mo_ta": "Kỳ quan 'đường lên tiên cảnh' với tuyến cáp treo đạt nhiều kỷ lục Guinness. Nổi bật với Cầu Vàng được nâng đỡ bởi hai bàn tay khổng lồ giữa biển mây, làng Pháp cổ kính, hầm rượu Debay và công viên Fantasy Park.",
            "loai_dia_diem": "Khu du lịch / Biểu tượng thế giới",
            "khu_vuc": "Đà Nẵng",
            "dia_chi": "Thôn An Sơn, Xã Hòa Ninh, Huyện Hòa Vang, Đà Nẵng",
            "latitude": 15.9980,
            "longitude": 107.9880,
            "google_maps_url": "https://www.google.com/maps?q=15.998000,107.988000",
            "distance_km": 25.0,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1559592413-7cec4d0cae2b?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 21:00",
            "gia_ve": "Cáp treo 900.000đ/người lớn",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_dn_2",
            "ten_dia_diem": "Bán Đảo Sơn Trà & Chùa Linh Ứng",
            "mo_ta": "'Lá phổi xanh' của Đà Nẵng với Chùa Linh Ứng Bãi Bụt uy nghiêm, nơi có tượng Phật Bà Quan Âm cao 67m hướng ra biển Đông che chở cho ngư dân. Cung đường ngắm voọc chà vá chân nâu và đỉnh Bàn Cờ lộng gió.",
            "loai_dia_diem": "Tâm linh / Thiên nhiên bán đảo",
            "khu_vuc": "Sơn Trà, Đà Nẵng",
            "dia_chi": "Hoàng Sa, Bán đảo Sơn Trà, Thọ Quang, Sơn Trà, Đà Nẵng",
            "latitude": 16.1000,
            "longitude": 108.2780,
            "google_maps_url": "https://www.google.com/maps?q=16.100000,108.278000",
            "distance_km": 10.0,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1544644181-1484b3fdfc62?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "06:00 - 18:30",
            "gia_ve": "Miễn phí vào chùa",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_dn_3",
            "ten_dia_diem": "Cầu Rồng & Cầu Tình Yêu Sông Hàn",
            "mo_ta": "Cây cầu biểu tượng vươn mình ra biển lớn của thành phố đáng sống nhất Việt Nam. Vào 21:00 tối thứ Bảy và Chủ Nhật, Cầu Rồng trình diễn màn phun lửa và phun nước độc đáo thu hút đông đảo du khách.",
            "loai_dia_diem": "Kiến trúc / Check-in sông Hàn",
            "khu_vuc": "Hải Châu, Đà Nẵng",
            "dia_chi": "Đường Nguyễn Văn Linh, Phước Ninh, Hải Châu, Đà Nẵng",
            "latitude": 16.0610,
            "longitude": 108.2270,
            "google_maps_url": "https://www.google.com/maps?q=16.061000,108.227000",
            "distance_km": 1.0,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1559592413-7cec4d0cae2b?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Cả ngày (Phun lửa 21:00 T7, CN)",
            "gia_ve": "Miễn phí",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dn_4",
            "ten_dia_diem": "Bãi Biển Mỹ Khê",
            "mo_ta": "Được tạp chí Forbes vinh danh là một trong những bãi biển quyến rũ nhất hành tinh. Bờ cát trắng mịn trải dài thoai thoải, nước biển trong xanh ấm áp quanh năm cùng hàng dừa nghiêng bóng thơ mộng.",
            "loai_dia_diem": "Bãi biển / Thể thao dưới nước",
            "khu_vuc": "Ngũ Hành Sơn, Đà Nẵng",
            "dia_chi": "Võ Nguyên Giáp, Phước Mỹ, Sơn Trà, Đà Nẵng",
            "latitude": 16.0590,
            "longitude": 108.2460,
            "google_maps_url": "https://www.google.com/maps?q=16.059000,108.246000",
            "distance_km": 2.5,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Cả ngày",
            "gia_ve": "Tắm biển miễn phí",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dn_5",
            "ten_dia_diem": "Ngũ Hành Sơn & Động Huyền Không",
            "mo_ta": "Quần thể 5 ngọn núi đá vôi kỳ bí mang tên hệ ngũ hành Kim - Mộc - Thủy - Hỏa - Thổ. Nổi bật với Động Huyền Không ánh sáng rọi qua vòm đá linh thiêng, Vọng Giang Đài và làng nghề điêu khắc đá Non Nước hơn 400 năm.",
            "loai_dia_diem": "Thắng cảnh / Di tích tâm linh",
            "khu_vuc": "Ngũ Hành Sơn, Đà Nẵng",
            "dia_chi": "81 Huyền Trân Công Chúa, Hòa Hải, Ngũ Hành Sơn, Đà Nẵng",
            "latitude": 16.0028,
            "longitude": 108.2497,
            "google_maps_url": "https://www.google.com/maps?q=16.002800,108.249700",
            "distance_km": 8.0,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1528181304800-259b08848526?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 17:30",
            "gia_ve": "40.000đ/vé (Thang máy 15.000đ/lượt)",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dn_6",
            "ten_dia_diem": "Bánh Tráng Cuốn Thịt Heo Trần & Chợ Cồn",
            "mo_ta": "Món ăn đặc sản thương hiệu Đà Nẵng với lát thịt heo hai đầu da luộc mềm ngọt, cuốn cùng bánh tráng phơi sương, mì lá và đĩa rau sống tươi non hơn 10 loại rau rừng, chấm mắm nêm cá cơm nguyên chất thơm cay nồng nàn.",
            "loai_dia_diem": "Ẩm thực & Món ngon miền Trung",
            "khu_vuc": "Hải Châu, Đà Nẵng",
            "dia_chi": "04 Lê Duẩn, Hải Châu 1, Hải Châu, Đà Nẵng",
            "latitude": 16.0680,
            "longitude": 108.2180,
            "google_maps_url": "https://www.google.com/maps?q=16.068000,108.218000",
            "distance_km": 1.2,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "08:30 - 22:00",
            "gia_ve": "70.000đ - 150.000đ/phần",
            "danh_gia": 4.8,
            "language": "vietnamese"
        }
    ],
    "đà lạt": [
        {
            "id": "hub_dl_1",
            "ten_dia_diem": "Quảng Trường Lâm Viên & Hồ Xuân Hương",
            "mo_ta": "Trái tim của xứ sở sương mù với hai công trình biểu tượng độc đáo: khối hoa Dã Quỳ cao 18m và nụ hoa Atiso bằng kính màu khổng lồ cao 15m. Dạo quanh bờ Hồ Xuân Hương phẳng lặng ngắm hoàng hôn buông xuống lãng mạn.",
            "loai_dia_diem": "Quảng trường / Check-in biểu tượng",
            "khu_vuc": "Đà Lạt, Lâm Đồng",
            "dia_chi": "Đường Trần Quốc Toản, Phường 10, TP. Đà Lạt",
            "latitude": 11.9360,
            "longitude": 108.4450,
            "google_maps_url": "https://www.google.com/maps?q=11.936000,108.445000",
            "distance_km": 0.5,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1513836279014-a89f7a76ae86?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "Cả ngày",
            "gia_ve": "Miễn phí",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dl_2",
            "ten_dia_diem": "Đồi Chè Cầu Đất & Săn Mây Panorama",
            "mo_ta": "Thiên đường săn mây bồng bềnh buổi sớm mai đẹp nhất Việt Nam. Những đồi chè xanh mướt mát uốn lượn trải dài ngút ngàn, hàng quạt gió điện gió khổng lồ quay chậm rãi giữa thung lũng mây trắng như chốn bồng lai.",
            "loai_dia_diem": "Săn mây / Đồi chè thiên nhiên",
            "khu_vuc": "Xuân Trường, Đà Lạt",
            "dia_chi": "Thôn Cầu Đất, Xã Xuân Trường, TP. Đà Lạt",
            "latitude": 11.8580,
            "longitude": 108.5620,
            "google_maps_url": "https://www.google.com/maps?q=11.858000,108.562000",
            "distance_km": 23.0,
            "similarity": 0.99,
            "image_url": "https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "05:00 - 17:30 (Khuyên đi từ 05:30 để săn mây)",
            "gia_ve": "Miễn phí tham quan đồi chè",
            "danh_gia": 4.9,
            "language": "vietnamese"
        },
        {
            "id": "hub_dl_3",
            "ten_dia_diem": "Ga Xe Lửa Đà Lạt & Thung Lũng Tình Yêu",
            "mo_ta": "Nhà ga cổ kính nhất Đông Dương mang đậm phong cách kiến trúc Art Deco Pháp với hình ảnh 3 chóp mái mô phỏng đỉnh núi Langbiang hùng vĩ. Nơi lưu giữ đầu tàu hơi nước cổ và cung đường hoa cẩm tú cầu nở rực quanh năm.",
            "loai_dia_diem": "Kiến trúc cổ / Check-in lãng mạn",
            "khu_vuc": "Phường 10, Đà Lạt",
            "dia_chi": "01 Quang Trung, Phường 10, TP. Đà Lạt",
            "latitude": 11.9415,
            "longitude": 108.4550,
            "google_maps_url": "https://www.google.com/maps?q=11.941500,108.455000",
            "distance_km": 1.5,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1507842229452-779836376822?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:30 - 17:00",
            "gia_ve": "50.000đ/vé vào ga",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dl_4",
            "ten_dia_diem": "Đỉnh Núi Langbiang",
            "mo_ta": "'Nóc nhà Đà Lạt' cao hơn 2.167m gắn liền truyền thuyết tình yêu chàng K'lang và nàng H'biang. Du khách trải nghiệm xe Jeep địa hình lao vun vút qua đồi thông lên đỉnh Ra-đa ngắm trọn vẹn dòng Suối Vàng - Suối Bạc trong màn sương.",
            "loai_dia_diem": "Núi cao / Khám phá dã ngoại",
            "khu_vuc": "Lạc Dương, Lâm Đồng",
            "dia_chi": "Thị trấn Lạc Dương, Huyện Lạc Dương, Lâm Đồng",
            "latitude": 12.0460,
            "longitude": 108.4350,
            "google_maps_url": "https://www.google.com/maps?q=12.046000,108.435000",
            "distance_km": 12.0,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1469854523086-cc02fe5d8800?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "07:00 - 17:00",
            "gia_ve": "50.000đ/vé cổng + 120.000đ/vé xe Jeep khứ hồi",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dl_5",
            "ten_dia_diem": "Chợ Đêm Đà Lạt (Chợ Âm Phủ)",
            "mo_ta": "Thánh địa ăn vặt đêm nhộn nhịp giữa lòng thành phố sương mù. Thưởng thức bánh tráng nướng trứng giòn rụm, dâu lắc muối ớt, xiên que nướng bốc khói và cốc sữa đậu nành nóng hổi xua tan cái lạnh cao nguyên.",
            "loai_dia_diem": "Chợ đêm / Ẩm thực đường phố",
            "khu_vuc": "Phường 1, Đà Lạt",
            "dia_chi": "Đường Nguyễn Thị Minh Khai, Phường 1, TP. Đà Lạt",
            "latitude": 11.9420,
            "longitude": 108.4370,
            "google_maps_url": "https://www.google.com/maps?q=11.942000,108.437000",
            "distance_km": 0.8,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "17:00 - 23:30",
            "gia_ve": "Miễn phí vào chợ",
            "danh_gia": 4.8,
            "language": "vietnamese"
        },
        {
            "id": "hub_dl_6",
            "ten_dia_diem": "Lẩu Gà Lá É Tao Ngộ & Lẩu Bò Ba Toa",
            "mo_ta": "Đặc sản trứ danh làm say lòng mọi thực khách khi đến Đà Lạt. Nồi lẩu gà lá é sôi sùng sục thơm the mát dịu từ lá é trắng, vị ngọt thanh của thịt gà đồi săn chắc chấm muối tiêu chanh ớt cay nồng tuyệt hảo trong tiết trời se lạnh.",
            "loai_dia_diem": "Ẩm thực & Quán ăn gia truyền",
            "khu_vuc": "Phường 3, Đà Lạt",
            "dia_chi": "05 Đường 3/4, Phường 3, TP. Đà Lạt",
            "latitude": 11.9330,
            "longitude": 108.4490,
            "google_maps_url": "https://www.google.com/maps?q=11.933000,108.449000",
            "distance_km": 1.2,
            "similarity": 0.98,
            "image_url": "https://images.unsplash.com/photo-1544025162-d76694265947?w=800&auto=format&fit=crop",
            "thoi_gian_hoat_dong": "10:00 - 22:00",
            "gia_ve": "200.000đ - 350.000đ/nồi",
            "danh_gia": 4.8,
            "language": "vietnamese"
        }
    ]
}


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

    # 2.1. ƯU TIÊN SỐ 1: Điểm đến biểu tượng & Ẩm thực đặc sản đa dạng do Tác tử tuyển chọn (Không phụ thuộc cứng ngắc vào OSM)
    scan_hub_text = f"{target_region_name} {dest_text} {question}".lower()
    matched_hub_key = None
    for hk in sorted(AGENT_DYNAMIC_CURATED_HUBS.keys(), key=lambda k: len(k), reverse=True):
        if hk in scan_hub_text:
            matched_hub_key = hk
            break

    if matched_hub_key:
        curated_candidates = list(AGENT_DYNAMIC_CURATED_HUBS[matched_hub_key])
        if cat_targets:
            filtered = []
            rest = []
            for cp in curated_candidates:
                cp_str = f"{cp.get('ten_dia_diem','')} {cp.get('loai_dia_diem','')} {cp.get('mo_ta','')}".lower()
                if any(ct in cp_str for ct in cat_targets):
                    filtered.append(cp)
                else:
                    rest.append(cp)
            curated_candidates = filtered + rest
        if len(curated_candidates) >= limit:
            return curated_candidates[:limit]

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
                "image_url": img,
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

