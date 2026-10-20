from datetime import datetime
from src.models.base import db
import json

class Message(db.Model):
    __tablename__ = 'Messages'
    
    message_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('Conversations.conversation_id'))
    sender = db.Column(db.Enum('user', 'bot', name='sender_enum'))
    message_text = db.Column(db.Text)
    translated_text = db.Column(db.Text)
    message_type = db.Column(db.Enum('text', 'voice', name='message_type_enum'))
    voice_url = db.Column(db.Text)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    places = db.Column(db.JSON)  # Lưu trữ danh sách các tên địa điểm dưới dạng mảng JSON
    
    def __init__(self, **kwargs):
        super(Message, self).__init__(**kwargs)
        if self.places is None:
            self.places = []
    
    def _ensure_places_list(self):
        """Đảm bảo places là list và xử lý encoding"""
        if self.places is None:
            self.places = []
        elif isinstance(self.places, str):
            # Nếu places là string, thử parse JSON
            try:
                self.places = json.loads(self.places)
            except (json.JSONDecodeError, ValueError):
                self.places = []
        elif not isinstance(self.places, list):
            self.places = []
    
    def _decode_unicode_places(self, places_list):
        """Decode Unicode escape sequences trong places - hỗ trợ đa ngôn ngữ"""
        if not places_list:
            return []
        
        decoded_places = []
        for place in places_list:
            if isinstance(place, str):
                # Thử nhiều cách decode khác nhau
                decoded_place = self._decode_string(place)
                decoded_places.append(decoded_place)
            else:
                decoded_places.append(str(place))
        
        return decoded_places
    
    def _decode_string(self, text):
        """Decode string với nhiều phương pháp khác nhau"""
        if not text:
            return text
        
        # Phương pháp 1: Xử lý double encoding UTF-8
        try:
            # Kiểm tra xem có phải double encoding không
            if 'Ã' in text or 'á»' in text or 'Ä' in text:
                # Thử decode double encoding
                decoded = text.encode('latin-1').decode('utf-8')
                if decoded != text:
                    return decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        # Phương pháp 2: Thử decode Unicode escape sequences
        try:
            # Xử lý các Unicode escape sequences phổ biến
            decoded = text.encode('utf-8').decode('unicode_escape')
            if decoded != text:  # Nếu có thay đổi
                return decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        # Phương pháp 3: Thử decode với latin-1 rồi encode lại
        try:
            decoded = text.encode('latin-1').decode('utf-8')
            if decoded != text:  # Nếu có thay đổi
                return decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        # Phương pháp 4: Thử decode với cp1252 rồi encode lại
        try:
            decoded = text.encode('cp1252').decode('utf-8')
            if decoded != text:  # Nếu có thay đổi
                return decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        # Phương pháp 5: Xử lý các escape sequences đặc biệt cho tiếng Việt
        try:
            # Thay thế các escape sequences phổ biến cho tiếng Việt
            replacements = {
                # Dấu thanh
                '\\u00e0': 'à', '\\u00e1': 'á', '\\u00e2': 'â', '\\u00e3': 'ã',
                '\\u00e8': 'è', '\\u00e9': 'é', '\\u00ea': 'ê', '\\u00eb': 'ë',
                '\\u00ec': 'ì', '\\u00ed': 'í', '\\u00ee': 'î', '\\u00ef': 'ï',
                '\\u00f2': 'ò', '\\u00f3': 'ó', '\\u00f4': 'ô', '\\u00f5': 'õ',
                '\\u00f9': 'ù', '\\u00fa': 'ú', '\\u00fb': 'û', '\\u00fc': 'ü',
                '\\u00fd': 'ý', '\\u00ff': 'ÿ',
                
                # Chữ cái đặc biệt
                '\\u0103': 'ă', '\\u0102': 'Ă',
                '\\u0111': 'đ', '\\u0110': 'Đ',
                
                # Dấu thanh kết hợp
                '\\u1ea1': 'ạ', '\\u1ea3': 'ả', '\\u1ea5': 'ấ', '\\u1ea7': 'ầ',
                '\\u1ea9': 'ẩ', '\\u1eab': 'ẫ', '\\u1ead': 'ậ', '\\u1eaf': 'ắ',
                '\\u1eb1': 'ằ', '\\u1eb3': 'ẳ', '\\u1eb5': 'ẵ', '\\u1eb7': 'ặ',
                '\\u1eb9': 'ẹ', '\\u1ebb': 'ẻ', '\\u1ebd': 'ẽ', '\\u1ebf': 'ế',
                '\\u1ec1': 'ề', '\\u1ec3': 'ể', '\\u1ec5': 'ễ', '\\u1ec7': 'ệ',
                '\\u1ec9': 'ỉ', '\\u1ecb': 'ị', '\\u1ecd': 'ọ', '\\u1ecf': 'ỏ',
                '\\u1ed1': 'ố', '\\u1ed3': 'ồ', '\\u1ed5': 'ổ', '\\u1ed7': 'ỗ',
                '\\u1ed9': 'ộ', '\\u1edb': 'ớ', '\\u1edd': 'ờ', '\\u1edf': 'ở',
                '\\u1ee1': 'ỡ', '\\u1ee3': 'ợ', '\\u1ee5': 'ụ', '\\u1ee7': 'ủ',
                '\\u1ee9': 'ứ', '\\u1eeb': 'ừ', '\\u1eed': 'ử', '\\u1eef': 'ữ',
                '\\u1ef1': 'ự', '\\u1ef3': 'ỳ', '\\u1ef5': 'ỵ', '\\u1ef7': 'ỷ',
                '\\u1ef9': 'ỹ',
                
                # Các ký tự phổ biến khác
                '\\u00e3': 'ã', '\\u1ec3': 'ể', '\\u1ea1': 'ạ', '\\u1ecb': 'ị',
                '\\u1ec7': 'ệ', '\\u1ea1': 'ạ', '\\u1ea3': 'ả', '\\u1ea5': 'ấ',
                '\\u1ea7': 'ầ', '\\u1ea9': 'ẩ', '\\u1eab': 'ẫ', '\\u1ead': 'ậ',
                '\\u1eaf': 'ắ', '\\u1eb1': 'ằ', '\\u1eb3': 'ẳ', '\\u1eb5': 'ẵ',
                '\\u1eb7': 'ặ', '\\u1eb9': 'ẹ', '\\u1ebb': 'ẻ', '\\u1ebd': 'ẽ',
                '\\u1ebf': 'ế', '\\u1ec1': 'ề', '\\u1ec3': 'ể', '\\u1ec5': 'ễ',
                '\\u1ec7': 'ệ', '\\u1ec9': 'ỉ', '\\u1ecb': 'ị', '\\u1ecd': 'ọ',
                '\\u1ecf': 'ỏ', '\\u1ed1': 'ố', '\\u1ed3': 'ồ', '\\u1ed5': 'ổ',
                '\\u1ed7': 'ỗ', '\\u1ed9': 'ộ', '\\u1edb': 'ớ', '\\u1edd': 'ờ',
                '\\u1edf': 'ở', '\\u1ee1': 'ỡ', '\\u1ee3': 'ợ', '\\u1ee5': 'ụ',
                '\\u1ee7': 'ủ', '\\u1ee9': 'ứ', '\\u1eeb': 'ừ', '\\u1eed': 'ử',
                '\\u1eef': 'ữ', '\\u1ef1': 'ự', '\\u1ef3': 'ỳ', '\\u1ef5': 'ỵ',
                '\\u1ef7': 'ỷ', '\\u1ef9': 'ỹ',
                
                # Chinese characters
                '\\u4e2d': '中', '\\u6587': '文', '\\u8bed': '语', '\\u8a00': '言',
                '\\u65e5': '日', '\\u672c': '本', '\\u8a9e': '語',
                '\\ud55c': '한', '\\uad6d': '국', '\\uc5b4': '어',
                # Japanese characters
                '\\u65e5': '日', '\\u672c': '本', '\\u8a9e': '語',
                # Korean characters
                '\\ud55c': '한', '\\uad6d': '국', '\\uc5b4': '어'
            }
            
            decoded = text
            for escaped, unicode_char in replacements.items():
                decoded = decoded.replace(escaped, unicode_char)
            
            if decoded != text:  # Nếu có thay đổi
                return decoded
        except Exception:
            pass
        
        # Nếu không decode được, trả về nguyên bản
        return text
    
    def add_place(self, place_name):
        """Thêm một địa điểm vào danh sách"""
        self._ensure_places_list()
        if place_name not in self.places:
            self.places.append(place_name)
    
    def remove_place(self, place_name):
        """Xóa một địa điểm khỏi danh sách"""
        self._ensure_places_list()
        if place_name in self.places:
            self.places.remove(place_name)
    
    def get_places(self):
        """Lấy danh sách địa điểm với xử lý Unicode đa ngôn ngữ"""
        self._ensure_places_list()
        return self._decode_unicode_places(self.places)
    
    def set_places(self, places_list):
        """Thiết lập danh sách địa điểm"""
        if isinstance(places_list, list):
            # Đảm bảo tất cả items là string và encode đúng
            clean_places = []
            for place in places_list:
                if isinstance(place, str):
                    # Đảm bảo string được encode đúng UTF-8
                    try:
                        # Normalize Unicode
                        import unicodedata
                        normalized = unicodedata.normalize('NFC', place)
                        clean_places.append(normalized)
                    except Exception:
                        clean_places.append(place)
                else:
                    clean_places.append(str(place))
            
            self.places = clean_places
        else:
            self.places = []
    
    def clear_places(self):
        """Xóa tất cả địa điểm"""
        self.places = []
    
    def has_place(self, place_name):
        """Kiểm tra xem có địa điểm trong danh sách không"""
        self._ensure_places_list()
        decoded_places = self._decode_unicode_places(self.places)
        return place_name in decoded_places
    
    def to_dict(self):
        """Chuyển đổi object thành dictionary"""
        return {
            'message_id': self.message_id,
            'conversation_id': self.conversation_id,
            'sender': self.sender,
            'message_text': self.message_text,
            'translated_text': self.translated_text,
            'message_type': self.message_type,
            'voice_url': self.voice_url,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'places': self.get_places()
        } 