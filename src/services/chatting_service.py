from datetime import datetime
import re
from src.models.conversation import Conversation
from src.models.message import Message
from src.services.ai.openai_service import OpenAIService
from src.services.travel_chatbot_service import (
    detect_language,
    get_language_info,
    extract_user_intent_and_features,
    search_pois_from_postgresql,
    create_chatbot_response
)
from src import db
from datetime import datetime, timezone
import os
import json
import openai
from typing import Dict, List, Optional, Any

def is_travel_related_question(question: str) -> bool:
    """
    Kiểm tra xem câu hỏi có liên quan đến du lịch hay không
    Hỗ trợ 5 ngôn ngữ: Tiếng Việt, Trung, Anh, Hàn, Nhật
    
    Args:
        question (str): Câu hỏi của người dùng
        
    Returns:
        bool: True nếu câu hỏi liên quan đến du lịch
    """
    # Chuyển về chữ thường để dễ so sánh
    question_lower = question.lower()
    
    # Từ khóa liên quan đến du lịch - 5 ngôn ngữ
    travel_keywords = [
        # === TIẾNG VIỆT ===
        # Địa điểm du lịch
        'địa điểm', 'địa danh', 'nơi', 'chỗ', 'khu vực', 'quận', 'huyện', 'phường', 'thành phố', 'tỉnh',
        'điểm đến', 'điểm tham quan', 'danh lam thắng cảnh', 'di tích', 'di tích lịch sử',
        
        # Hoạt động du lịch
        'du lịch', 'thăm quan', 'khám phá', 'đi chơi', 'nghỉ dưỡng', 'ăn uống', 'thưởng thức',
        'đi dạo', 'dạo chơi', 'thư giãn', 'giải trí', 'vui chơi', 'trải nghiệm',
        
        # Loại địa điểm
        'nhà hàng', 'khách sạn', 'cafe', 'quán ăn', 'chợ', 'trung tâm thương mại', 'siêu thị',
        'quán bar', 'pub', 'club', 'vũ trường', 'karaoke', 'massage', 'spa',
        'bảo tàng', 'thư viện', 'rạp chiếu phim', 'công viên', 'vườn hoa', 'hồ bơi',
        'sân golf', 'sân tennis', 'phòng gym', 'yoga', 'thiền',
        
        # Địa danh cụ thể ở HCM
        'bến thành', 'landmark', 'bùi viện', 'nguyễn huệ', 'đồng khởi', 'phố đi bộ',
        'ben thanh', 'landmark 81', 'bui vien', 'nguyen hue', 'dong khoi', 'walking street',
        'chợ bến thành', 'nhà thờ đức bà', 'dinh độc lập', 'bảo tàng chứng tích chiến tranh',
        'phố tây', 'phố nguyễn huệ', 'phố đồng khởi', 'phố bùi viện',
        
        # Từ khóa tìm kiếm
        'ở đâu', 'đi đâu', 'tìm', 'kiếm', 'tìm kiếm', 'hỏi', 'thắc mắc',
        'gợi ý', 'đề xuất', 'khuyên', 'tư vấn', 'hướng dẫn', 'chỉ đường',
        
        # === TIẾNG ANH ===
        # Places and locations
        'place', 'location', 'area', 'district', 'ward', 'attraction', 'destination', 'spot',
        'venue', 'site', 'landmark', 'monument', 'heritage', 'cultural site',
        
        # Travel activities
        'travel', 'visit', 'explore', 'tour', 'vacation', 'holiday', 'trip', 'journey',
        'sightseeing', 'adventure', 'experience', 'discover', 'wander', 'roam',
        
        # Types of places
        'restaurant', 'hotel', 'cafe', 'market', 'mall', 'shopping center', 'supermarket',
        'bar', 'pub', 'club', 'disco', 'karaoke', 'massage', 'spa',
        'museum', 'library', 'cinema', 'theater', 'park', 'garden', 'pool',
        'golf course', 'tennis court', 'gym', 'yoga', 'meditation',
        
        # Search keywords
        'where', 'find', 'search', 'look for', 'seek', 'ask', 'question',
        'suggest', 'recommend', 'advise', 'consult', 'guide', 'direct',
        
        # === TIẾNG TRUNG ===
        # 地点和位置
        '景点', '地方', '区域', '地区', '场所', '地点', '位置', '地址',
        '名胜古迹', '文化遗产', '历史遗迹', '地标', '标志性建筑',
        
        # 旅游活动
        '旅游', '参观', '探索', '游玩', '度假', '旅行', '观光', '游览',
        '体验', '发现', '漫步', '闲逛', '放松', '娱乐', '享受',
        
        # 场所类型
        '餐厅', '酒店', '咖啡', '市场', '购物中心', '超市',
        '酒吧', '夜总会', '卡拉ok', '按摩', '水疗',
        '博物馆', '图书馆', '电影院', '剧院', '公园', '花园', '游泳池',
        '高尔夫球场', '网球场', '健身房', '瑜伽', '冥想',
        
        # 搜索关键词
        '哪里', '找', '搜索', '寻找', '询问', '问题',
        '推荐', '建议', '咨询', '指导', '指引',
        
        # === TIẾNG NHẬT ===
        # 場所と位置
        '場所', 'エリア', '地域', '地区', 'スポット', '観光地', '名所',
        '名勝', '文化遺産', '史跡', 'ランドマーク', '記念碑',
        
        # 旅行活動
        '旅行', '観光', '探索', '遊び', '休暇', 'ツアー', '見学',
        '体験', '発見', '散歩', 'ぶらぶら', 'リラックス', '楽しみ',
        
        # 場所の種類
        'レストラン', 'ホテル', 'カフェ', '市場', 'ショッピングセンター', 'スーパー',
        'バー', 'クラブ', 'カラオケ', 'マッサージ', 'スパ',
        '博物館', '図書館', '映画館', '劇場', '公園', '庭園', 'プール',
        'ゴルフ場', 'テニスコート', 'ジム', 'ヨガ', '瞑想',
        
        # 検索キーワード
        'どこ', '探す', '検索', '見つける', '質問', '問題',
        'おすすめ', '提案', '相談', '案内', '指導',
        
        # === TIẾNG HÀN ===
        # 장소와 위치
        '장소', '지역', '구역', '지점', '관광지', '명소', '명승지',
        '문화유산', '사적', '랜드마크', '기념비', '유적지',
        
        # 여행 활동
        '여행', '관광', '탐험', '놀기', '휴가', '투어', '견학',
        '체험', '발견', '산책', '어슬렁거리기', '휴식', '즐기기',
        
        # 장소 유형
        '레스토랑', '호텔', '카페', '시장', '쇼핑센터', '슈퍼마켓',
        '바', '클럽', '노래방', '마사지', '스파',
        '박물관', '도서관', '영화관', '극장', '공원', '정원', '수영장',
        '골프장', '테니스장', '헬스장', '요가', '명상',
        
        # 검색 키워드
        '어디', '찾다', '검색', '찾기', '질문', '문제',
        '추천', '제안', '상담', '안내', '가이드',
        
        # === TỪ KHÓA CHUNG CHO TẤT CẢ NGÔN NGỮ ===
        # Thời gian và kế hoạch
        'time', 'schedule', 'plan', 'when', 'how long', 'duration',
        '时间', '日程', '计划', '什么时候', '多长时间',
        '時間', 'スケジュール', '計画', 'いつ', 'どのくらい',
        '시간', '일정', '계획', '언제', '얼마나',
        
        # Giao thông và di chuyển
        'transport', 'transportation', 'bus', 'subway', 'taxi', 'train', 'plane',
        '交通', '运输', '公交车', '地铁', '出租车', '火车', '飞机',
        '交通', '輸送', 'バス', '地下鉄', 'タクシー', '電車', '飛行機',
        '교통', '운송', '버스', '지하철', '택시', '기차', '비행기',
        
        # Đặc điểm và mô tả
        'beautiful', 'nice', 'good', 'bad', 'clean', 'dirty', 'big', 'small',
        '美丽', '好', '坏', '干净', '脏', '大', '小',
        '美しい', '良い', '悪い', 'きれい', '汚い', '大きい', '小さい',
        '아름다운', '좋은', '나쁜', '깨끗한', '더러운', '큰', '작은',
        
        # Cảm xúc và đánh giá
        'like', 'dislike', 'enjoy', 'hate', 'love', 'recommend',
        '喜欢', '不喜欢', '享受', '讨厌', '爱', '推荐',
        '好き', '嫌い', '楽しむ', '憎む', '愛する', 'おすすめ',
        '좋아하다', '싫어하다', '즐기다', '미워하다', '사랑하다', '추천하다'
    ]
    
    # Kiểm tra xem có từ khóa du lịch nào trong câu hỏi không
    for keyword in travel_keywords:
        if keyword in question_lower:
            return True
    
    # Kiểm tra các pattern đặc biệt cho từng ngôn ngữ
    travel_patterns = [
        # === TIẾNG VIỆT ===
        r'\b(địa điểm|nơi|chỗ)\s+(nào|đẹp|ngon|vui|thú vị|tốt|hay)',
        r'\b(đi|đến|thăm|khám phá|ghé|dừng)\s+',
        r'\b(ở đâu|đi đâu|tìm|kiếm|hỏi)\s+',
        r'\b(gợi ý|đề xuất|khuyên|tư vấn)\s+',
        r'\b(nhà hàng|khách sạn|cafe|quán|chợ|trung tâm)\s+(nào|đẹp|ngon|tốt)',
        r'\b(du lịch|thăm quan|nghỉ dưỡng)\s+(ở|tại|đến)',
        
        # === TIẾNG ANH ===
        r'\b(where|what|which|how|when|why)\s+(is|are|can|should|do|does)',
        r'\b(place|location|area|spot)\s+(to|for|near|around)',
        r'\b(restaurant|hotel|cafe|market|mall)\s+(near|around|in|at)',
        r'\b(travel|visit|explore|tour)\s+(to|around|in|at)',
        r'\b(suggest|recommend|advise)\s+(a|some|good|best)',
        r'\b(find|look for|search)\s+(a|some|good|best)',
        
        # === TIẾNG TRUNG ===
        r'\b(哪里|什么地方|哪个|怎么|什么时候|为什么)\s+(有|是|可以|应该)',
        r'\b(景点|地方|区域|地点)\s+(有|是|可以|应该)',
        r'\b(餐厅|酒店|咖啡|市场|购物中心)\s+(有|是|可以|应该)',
        r'\b(旅游|参观|探索|游玩)\s+(在|到|去)',
        r'\b(推荐|建议|咨询)\s+(什么|哪个|哪里)',
        r'\b(找|搜索|寻找)\s+(什么|哪个|哪里)',
        
        # === TIẾNG NHẬT ===
        r'\b(どこ|何|どの|どう|いつ|なぜ)\s+(に|で|が|を|は)',
        r'\b(場所|エリア|地域|スポット)\s+(に|で|が|を|は)',
        r'\b(レストラン|ホテル|カフェ|市場|ショッピングセンター)\s+(に|で|が|を|は)',
        r'\b(旅行|観光|探索|遊び)\s+(に|で|が|を|は)',
        r'\b(おすすめ|提案|相談)\s+(は|が|を|に)',
        r'\b(探す|検索|見つける)\s+(は|が|を|に)',
        
        # === TIẾNG HÀN ===
        r'\b(어디|무엇|어떤|어떻게|언제|왜)\s+(에|에서|가|을|를|는)',
        r'\b(장소|지역|구역|스팟)\s+(에|에서|가|을|를|는)',
        r'\b(레스토랑|호텔|카페|시장|쇼핑센터)\s+(에|에서|가|을|를|는)',
        r'\b(여행|관광|탐험|놀기)\s+(에|에서|가|을|를|는)',
        r'\b(추천|제안|상담)\s+(해주|해주세요|해주시)',
        r'\b(찾다|검색|찾기)\s+(해주|해주세요|해주시)',
        
        # === PATTERN CHUNG CHO TẤT CẢ NGÔN NGỮ ===
        # Câu hỏi với từ nghi vấn
        r'\b(where|what|which|how|when|why|where is|what is|how to)\b',
        r'\b(哪里|什么|哪个|怎么|什么时候|为什么|哪里是|什么是|怎么)\b',
        r'\b(どこ|何|どの|どう|いつ|なぜ|どこに|何が|どうやって)\b',
        r'\b(어디|무엇|어떤|어떻게|언제|왜|어디에|무엇이|어떻게)\b',
        
        # Pattern tìm kiếm và gợi ý
        r'\b(suggest|recommend|advise|find|look for|search)\b',
        r'\b(推荐|建议|咨询|找|搜索|寻找)\b',
        r'\b(おすすめ|提案|相談|探す|検索|見つける)\b',
        r'\b(추천|제안|상담|찾다|검색|찾기)\b',
        
        # Pattern địa điểm và hoạt động
        r'\b(restaurant|hotel|cafe|market|mall|park|museum|tourist|attraction)\b',
        r'\b(餐厅|酒店|咖啡|市场|购物中心|公园|博物馆|旅游|景点)\b',
        r'\b(レストラン|ホテル|カフェ|市場|ショッングセンター|公園|博物館|観光|名所)\b',
        r'\b(레스토랑|호텔|카페|시장|쇼핑센터|공원|박물관|관광|명소)\b'
    ]
    
    for pattern in travel_patterns:
        if re.search(pattern, question_lower):
            return True
    
    return False

def process_travel_question(question: str, enable_web_search: Optional[bool] = None) -> dict:
    """
    Xử lý câu hỏi du lịch sử dụng travel chatbot service
    
    Args:
        question (str): Câu hỏi của người dùng
        enable_web_search (bool, optional): Bật/tắt Google Search Grounding
        
    Returns:
        dict: Kết quả xử lý với response và metadata
    """
    try:
        # Bước 1: Nhận biết ngôn ngữ
        language_result = detect_language(question)
        
        # Kiểm tra ngôn ngữ có được hỗ trợ không
        if not language_result.get('is_supported', False):
            lang_info = get_language_info('unknown')
            return {
                'success': False,
                'response': lang_info['unsupported_message'],
                'error': 'Unsupported language'
            }
        
        detected_language = language_result.get('language', 'vietnamese')
        lang_info = get_language_info(detected_language)
        
        # Bước 2: Trích xuất thực thể và ý định
        extraction_result = extract_user_intent_and_features(question)
        
        # Bước 3: Thực hiện tìm kiếm kết hợp PostgreSQL POI Engine
        formatted_results = search_pois_from_postgresql(
            question=question,
            extracted_features=extraction_result.get('extracted_features', {}),
            limit=6
        )
        
        # Bước 4: Tạo câu trả lời tự nhiên cho chatbot
        chatbot_response = create_chatbot_response(
            question=question,
            search_results=formatted_results,
            extracted_features=extraction_result.get('extracted_features', {}),
            language=detected_language,
            enable_web_search=enable_web_search
        )

        web_sources = chatbot_response.get('web_sources', [])
        web_queries = chatbot_response.get('web_search_queries', [])
        used_web = bool(web_sources or web_queries or chatbot_response.get('grounded', False))

        ai_pipeline = {
            'intent_agent': 'Gemini Intent & Feature Extractor',
            'retrieval_agent': f'PostgreSQL POI Engine ({len(formatted_results)} địa điểm)',
            'web_search_agent': {
                'used': used_web,
                'mode': 'manual_on' if enable_web_search is True else ('auto_detected' if used_web else 'not_needed'),
                'queries': web_queries,
                'sources_count': len(web_sources)
            },
            'synthesis_agent': 'Gemini Tour Guide (Google Search Grounding)' if used_web else 'Gemini Tour Guide'
        }
        
        return {
            'success': True,
            'response': chatbot_response.get('response', ''),
            'language': detected_language,
            'language_name': lang_info['name'],
            'search_results': formatted_results,
            'suggested_activities': chatbot_response.get('suggested_activities', []),
            'follow_up_questions': chatbot_response.get('follow_up_questions', []),
            'extracted_features': extraction_result.get('extracted_features', {}),
            'web_sources': web_sources,
            'web_search_queries': web_queries,
            'grounded': used_web,
            'ai_pipeline': ai_pipeline,
            'search_method': 'postgresql_staging'
        }
        
    except Exception as e:
        return {
            'success': False,
            'response': f'Xin lỗi, có lỗi xảy ra khi xử lý câu hỏi du lịch: {str(e)}',
            'error': str(e)
        }

def create_conversation(user_id: int, source_language: str = 'en', started_at: datetime = None, title: str = None):
    """
    Create a new conversation for a user
    
    Args:
        user_id (int): ID of the user
        source_language (str): Source language for the conversation
        started_at (datetime): Start time of the conversation
        title (str): Title of the conversation
    Returns:
        tuple: (success: bool, result: dict or str)
    """
    try:
        if started_at is None:
            started_at = datetime.utcnow()
            
        new_conversation = Conversation(
            title=title,
            user_id=user_id,
            source_language=source_language,
            started_at=started_at,
            ended_at=None
        )
        
        db.session.add(new_conversation)
        db.session.commit()
        db.session.refresh(new_conversation)
        
        return True, {
            "conversation_id": new_conversation.conversation_id,
            "user_id": new_conversation.user_id,
            "source_language": new_conversation.source_language,
            "started_at": new_conversation.started_at.isoformat() if new_conversation.started_at else None,
            "ended_at": new_conversation.ended_at.isoformat() if new_conversation.ended_at else None,
            "title": new_conversation.title
        }
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def get_user_conversations(user_id: int):
    """
    Get all conversations for a specific user
    
    Args:
        user_id (int): ID of the user
        
    Returns:
        tuple: (success: bool, result: list or str)
    """
    try:
        conversations = Conversation.query.filter_by(user_id=user_id).order_by(Conversation.started_at.desc()).all()
        
        return True, [{
            "conversation_id": conv.conversation_id,
            "user_id": conv.user_id,
            "source_language": conv.source_language,
            "started_at": conv.started_at.isoformat() if conv.started_at else None,
            "ended_at": conv.ended_at.isoformat() if conv.ended_at else None,
            "title": conv.title
        } for conv in conversations]
    except Exception as e:
        return False, str(e)

def get_conversation_messages(conversation_id: int):
    """
    Get all messages for a specific conversation
    
    Args:
        conversation_id (int): ID of the conversation
        
    Returns:
        tuple: (success: bool, result: list or str)
    """
    try:
        # Check if conversation exists
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return False, "Conversation not found"
            
        # Get messages ordered by created_at
        messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.sent_at.asc()).all()
        
        return True, [{
            "message_id": msg.message_id,
            "conversation_id": msg.conversation_id,
            "message_text": msg.message_text,
            "translated_text": msg.translated_text,
            "sender": msg.sender,
            "message_type": msg.message_type,
            "voice_url": msg.voice_url,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            "places": msg.get_places()
        } for msg in messages]
    except Exception as e:
        return False, str(e)

def save_message(conversation_id: int, sender: str, message_text: str, translated_text: str = None, 
                message_type: str = 'text', voice_url: str = None, places: list = None):
    """
    Save a new message to the database and get AI response if message is from user
    
    Args:
        conversation_id (int): ID of the conversation
        sender (str): Sender of the message (bot or user)
        message_text (str): Content of the message
        translated_text (str, optional): Translated text of the message
        message_type (str, optional): Type of the message (default: text)
        voice_url (str, optional): URL of the voice message if any
        places (list, optional): List of place names mentioned in the message
        
    Returns:
        tuple: (success: bool, result: dict or str)
    """
    try:
        # Check if conversation exists
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return False, "Conversation not found"
            
        # print(f"Current conversation title: {conversation.title}")
            
        # Create new message
        new_message = Message(
            conversation_id=conversation_id,
            sender=sender,
            message_text=message_text,
            translated_text=translated_text,
            message_type=message_type,
            voice_url=voice_url,
            sent_at=datetime.now(timezone.utc)
        )
        
        # Set places if provided
        if places:
            new_message.set_places(places)
        
        db.session.add(new_message)
        
        # If message is from user, get AI response
        if sender == "user":
            try:
                # Initialize OpenAI service
                openai_service = OpenAIService()
                
                # Get AI response
                ai_response = openai_service.generate_response(message_text)
                # print(f"AI generated title: {ai_response['title']}")
                
                # Check if conversation needs a title
                if conversation.title is None or conversation.title.strip() == "":
                    conversation.title = ai_response['title']
                    # print(f"Setting new conversation title: {conversation.title}")
                    # Commit title update separately
                    db.session.commit()
                    db.session.refresh(conversation)
                # else:
                #     # print(f"Conversation already has title: {conversation.title}")
                
                # Save AI response as a new message
                bot_message = Message(
                    conversation_id=conversation_id,
                    sender="bot",
                    message_text=ai_response['text'],
                    message_type='text',
                    sent_at=datetime.now(timezone.utc)
                )
                
                # Set places if provided
                if places:
                    bot_message.set_places(places)
                
                db.session.add(bot_message)
                db.session.commit()
                db.session.refresh(new_message)
                db.session.refresh(bot_message)
                
                # Return both messages
                return True, {
                    "user_message": {
                        "message_id": new_message.message_id,
                        "conversation_id": new_message.conversation_id,
                        "sender": new_message.sender,
                        "message_text": new_message.message_text,
                        "translated_text": new_message.translated_text,
                        "message_type": new_message.message_type,
                        "voice_url": new_message.voice_url,
                        "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
                        "places": new_message.get_places()
                    },
                    "bot_message": {
                        "message_id": bot_message.message_id,
                        "conversation_id": bot_message.conversation_id,
                        "sender": bot_message.sender,
                        "message_text": bot_message.message_text,
                        "translated_text": bot_message.translated_text,
                        "message_type": bot_message.message_type,
                        "sent_at": bot_message.sent_at.isoformat() if bot_message.sent_at else None,
                        "places": bot_message.get_places()
                    },
                }
            except Exception as e:
                # If AI response fails, still return the user message
                db.session.commit()
                db.session.refresh(new_message)
                return True, {
                    "user_message": {
                        "message_id": new_message.message_id,
                        "conversation_id": new_message.conversation_id,
                        "sender": new_message.sender,
                        "message_text": new_message.message_text,
                        "translated_text": new_message.translated_text,
                        "message_type": new_message.message_type,
                        "voice_url": new_message.voice_url,
                        "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
                        "places": new_message.get_places()
                    },
                    "error": f"Failed to get AI response: {str(e)}"
                }
        
        # If message is from bot, just return the message
        db.session.commit()
        db.session.refresh(new_message)
        return True, {
            "message_id": new_message.message_id,
            "conversation_id": new_message.conversation_id,
            "sender": new_message.sender,
            "message_text": new_message.message_text,
            "translated_text": new_message.translated_text,
            "message_type": new_message.message_type,
            "voice_url": new_message.voice_url,
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
            "places": new_message.get_places()
        }
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def save_message_update(conversation_id: int, sender: str, message_text: str, translated_text: str = None, 
                message_type: str = 'text', voice_url: str = None, places: list = None):
    """
    Save a new message update to the database and get AI response if message is from user
    
    Args:
        conversation_id (int): ID of the conversation
        sender (str): Sender of the message (bot or user)
        message_text (str): Content of the message
        translated_text (str, optional): Translated text of the message
        message_type (str, optional): Type of the message (default: text)
        voice_url (str, optional): URL of the voice message if any
        places (list, optional): List of place names mentioned in the message
        
    Returns:
        tuple: (success: bool, result: dict or str)
    """
    try:
        # Check if conversation exists
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return False, "Conversation not found"
            
        # print(f"Current conversation title: {conversation.title}")
            
        # Create new message (không lưu places cho user message)
        new_message = Message(
            conversation_id=conversation_id,
            sender=sender,
            message_text=message_text,
            translated_text=translated_text,
            message_type=message_type,
            voice_url=voice_url,
            sent_at=datetime.now(timezone.utc)
        )
        
        db.session.add(new_message)
        
        # If message is from user, get AI response
        if sender == "user":
            try:
                # Kiểm tra xem câu hỏi có liên quan đến du lịch không
                if is_travel_related_question(message_text):
                    print("✅ Câu hỏi liên quan đến du lịch")
                    # Thử xử lý câu hỏi du lịch
                    travel_result = process_travel_question(message_text)
                    
                    if travel_result['success']:
                        # Nếu xử lý du lịch thành công, sử dụng kết quả đó
                        print(f"✅ xử lý du lịch thành công")
                        ai_response_text = travel_result['response']
                        
                        # Trích xuất địa điểm từ kết quả tìm kiếm
                        extracted_places = []
                        if travel_result.get('search_results'):
                            for result in travel_result['search_results']:
                                place_name = result.get('ten_dia_diem', '')
                                if place_name and place_name not in extracted_places:
                                    extracted_places.append(place_name)
                        
                        # Tạo title từ kết quả du lịch nếu có
                        if travel_result.get('search_results'):
                            first_result = travel_result['search_results'][0]
                            ai_response_title = f"Tư vấn du lịch: {first_result.get('ten_dia_diem', 'Địa điểm')}"
                        else:
                            ai_response_title = "Tư vấn du lịch"
                    else:
                        # Nếu xử lý du lịch thất bại, fallback về OpenAI
                        print("❌ xử lý du lịch thất bại")
                        openai_service = OpenAIService()
                        ai_response = openai_service.generate_response(message_text)
                        ai_response_text = ai_response['text']
                        ai_response_title = ai_response['title']
                        travel_result = {'success': False}
                else:
                    print("✅ Câu hỏi không liên quan đến du lịch")
                    # Nếu không phải câu hỏi du lịch, sử dụng OpenAI service
                    openai_service = OpenAIService()
                    ai_response = openai_service.generate_response(message_text)
                    ai_response_text = ai_response['text']
                    ai_response_title = ai_response['title']
                    travel_result = {'success': False}
                
                # Check if conversation needs a title
                if conversation.title is None or conversation.title.strip() == "":
                    conversation.title = ai_response_title if 'ai_response_title' in locals() else "Cuộc trò chuyện mới"
                    # Commit title update separately
                    db.session.commit()
                    db.session.refresh(conversation)
                
                # Save AI response as a new message
                bot_message = Message(
                    conversation_id=conversation_id,
                    sender="bot",
                    message_text=ai_response_text,
                    message_type='text',
                    sent_at=datetime.now(timezone.utc)
                )
                
                # Chỉ lưu places cho bot message, không lưu cho user message
                if travel_result.get('success') and travel_result.get('search_results'):
                    bot_message.translated_text = travel_result.get('language')
                    bot_places = []
                    for result in travel_result['search_results']:
                        place_name = result.get('ten_dia_diem', '')
                        if place_name and place_name not in bot_places:
                            bot_places.append(place_name)
                    if bot_places:
                        cleaned_bot_places = _clean_places_list(bot_places)
                        bot_message.set_places(cleaned_bot_places)
                
                db.session.add(bot_message)
                db.session.commit()
                db.session.refresh(new_message)
                db.session.refresh(bot_message)
                
                # Return both messages
                return True, {
                    "user_message": {
                        "message_id": new_message.message_id,
                        "conversation_id": new_message.conversation_id,
                        "sender": new_message.sender,
                        "message_text": new_message.message_text,
                        "translated_text": new_message.translated_text,
                        "message_type": new_message.message_type,
                        "voice_url": new_message.voice_url,
                        "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
                        "places": []  # User message không có places
                    },
                    "bot_message": {
                        "message_id": bot_message.message_id,
                        "conversation_id": bot_message.conversation_id,
                        "sender": bot_message.sender,
                        "message_text": bot_message.message_text,
                        "translated_text": bot_message.translated_text,
                        "message_type": bot_message.message_type,
                        "voice_url": bot_message.voice_url,
                        "sent_at": bot_message.sent_at.isoformat() if bot_message.sent_at else None,
                        "places": bot_message.get_places()
                    },
                    "travel_data": travel_result if travel_result.get('success') else None
                }
            except Exception as e:
                # If AI response fails, still return the user message
                db.session.commit()
                db.session.refresh(new_message)
                return True, {
                    "user_message": {
                        "message_id": new_message.message_id,
                        "conversation_id": new_message.conversation_id,
                        "sender": new_message.sender,
                        "message_text": new_message.message_text,
                        "translated_text": new_message.translated_text,
                        "message_type": new_message.message_type,
                        "voice_url": new_message.voice_url,
                        "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
                        "places": []  # User message không có places
                    },
                    "error": f"Failed to get AI response: {str(e)}"
                }
        
        # If message is from bot, just return the message
        db.session.commit()
        db.session.refresh(new_message)
        return True, {
            "message_id": new_message.message_id,
            "conversation_id": new_message.conversation_id,
            "sender": new_message.sender,
            "message_text": new_message.message_text,
            "translated_text": new_message.translated_text,
            "message_type": new_message.message_type,
            "voice_url": new_message.voice_url,
            "sent_at": new_message.sent_at.isoformat() if new_message.sent_at else None,
            "places": new_message.get_places()  # Bot message có thể có places
        }
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def end_conversation(conversation_id: int):
    """
    End a conversation by setting its ended_at timestamp
    
    Args:
        conversation_id (int): ID of the conversation to end
        
    Returns:
        tuple: (success: bool, result: dict or str)
    """
    try:
        # Check if conversation exists
        conversation = Conversation.query.get(conversation_id)
        if not conversation:
            return False, "Conversation not found"
            
        # Check if conversation is already ended
        if conversation.ended_at is not None:
            return False, "Conversation is already ended"
            
        # Set ended_at to current time
        conversation.ended_at = datetime.utcnow()
        db.session.commit()
        
        return True, {
            "conversation_id": conversation.conversation_id,
            "user_id": conversation.user_id,
            "source_language": conversation.source_language,
            "started_at": conversation.started_at.isoformat() if conversation.started_at else None,
            "ended_at": conversation.ended_at.isoformat() if conversation.ended_at else None,
            "title": conversation.title
        }
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def _decode_place_name(place_name: str) -> str:
    """
    Decode place name với nhiều phương pháp khác nhau
    """
    if not place_name or not isinstance(place_name, str):
        return place_name
    
    # Phương pháp 1: Xử lý double encoding UTF-8
    try:
        if 'Ã' in place_name or 'á»' in place_name or 'Ä' in place_name:
            # Thử decode double encoding
            decoded = place_name.encode('latin-1').decode('utf-8')
            if decoded != place_name:
                return decoded
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    
    # Phương pháp 2: Thử decode Unicode escape sequences
    try:
        decoded = place_name.encode('utf-8').decode('unicode_escape')
        if decoded != place_name:
            return decoded
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    
    # Phương pháp 3: Thử decode với latin-1 rồi encode lại
    try:
        decoded = place_name.encode('latin-1').decode('utf-8')
        if decoded != place_name:
            return decoded
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    
    # Phương pháp 4: Thử decode với cp1252 rồi encode lại
    try:
        decoded = place_name.encode('cp1252').decode('utf-8')
        if decoded != place_name:
            return decoded
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    
    # Phương pháp 5: Xử lý các escape sequences đặc biệt cho tiếng Việt
    try:
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
            
            # Chinese characters
            '\\u4e2d': '中', '\\u6587': '文', '\\u8bed': '语', '\\u8a00': '言',
            '\\u65e5': '日', '\\u672c': '本', '\\u8a9e': '語',
            '\\ud55c': '한', '\\uad6d': '국', '\\uc5b4': '어',
        }
        
        decoded = place_name
        for escaped, unicode_char in replacements.items():
            decoded = decoded.replace(escaped, unicode_char)
        
        if decoded != place_name:
            return decoded
    except Exception:
        pass
    
    # Phương pháp 6: Xử lý double encoding phức tạp hơn
    try:
        # Kiểm tra xem có phải double encoding không
        if 'Ã' in place_name or 'á»' in place_name or 'Ä' in place_name:
            # Thử decode nhiều lần
            current = place_name
            for _ in range(3):  # Tối đa 3 lần decode
                try:
                    decoded = current.encode('latin-1').decode('utf-8')
                    if decoded == current:  # Không thay đổi nữa
                        break
                    current = decoded
                except (UnicodeDecodeError, UnicodeEncodeError):
                    break
            return current
    except Exception:
        pass
    
    # Nếu không decode được, trả về nguyên bản
    return place_name

def _clean_places_list(places_list: List[str]) -> List[str]:
    """
    Clean và decode danh sách places
    """
    if not places_list:
        return []
    
    cleaned_places = []
    for place in places_list:
        if isinstance(place, str):
            decoded_place = _decode_place_name(place)
            # Normalize Unicode
            try:
                import unicodedata
                normalized = unicodedata.normalize('NFC', decoded_place)
                cleaned_places.append(normalized)
            except Exception:
                cleaned_places.append(decoded_place)
        else:
            cleaned_places.append(str(place))
    
    return cleaned_places 