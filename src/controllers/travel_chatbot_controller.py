import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from flask_restx import Namespace, Resource, fields, reqparse
from flask import request
from src.services.travel_chatbot_service import (
    search_pois_from_postgresql,
    extract_user_intent_and_features, 
    format_extraction_result, 
    detect_language,
    get_language_info,
    create_chatbot_response
)
import os
import json
import math
import openai
from datetime import datetime
import traceback

# Khởi tạo namespace
travel_chatbot_ns = Namespace('travel-chatbot', description='Travel chatbot operations')

# Định nghĩa parser cho pagination
pagination_parser = reqparse.RequestParser()
pagination_parser.add_argument('page', type=int, default=1, help='Page number (starts from 1)')
pagination_parser.add_argument('limit', type=int, default=10, help='Number of items per page')
pagination_parser.add_argument('sort_by', type=str, default='id', help='Field to sort by')
pagination_parser.add_argument('sort_order', type=str, default='asc', help='Sort order (asc/desc)')

# Định nghĩa model cho request/response
question_model = travel_chatbot_ns.model('Question', {
    'question': fields.String(required=True, description='User question about travel in Ho Chi Minh City'),
    'enable_web_search': fields.Boolean(required=False, description='Force enable live web search'),
    'session_id': fields.String(required=False, description='Optional session identifier for multi-turn state'),
    'conversation_id': fields.Integer(required=False, description='Optional conversation identifier for persistence')
})

answer_model = travel_chatbot_ns.model('Answer', {
    'id': fields.String(description='Location ID'),
    'ten_dia_diem': fields.String(description='Location name'),
    'mo_ta': fields.String(description='Location description'),
    'loai_dia_diem': fields.String(description='Location type'),
    'khu_vuc': fields.String(description='Area/Region'),
    'dia_chi': fields.String(description='Address'),
    'latitude': fields.Float(description='Latitude'),
    'longitude': fields.Float(description='Longitude'),
    'google_maps_url': fields.String(description='Direct Google Maps URL'),
    'distance_km': fields.Float(description='Distance from query center in km'),
    'similarity': fields.Float(description='Similarity score (0-1)'),
    'image_url': fields.String(description='Image URL for visual preview'),
    'thoi_gian_hoat_dong': fields.String(description='Opening hours'),
    'gia_ve': fields.String(description='Admission fee or price range'),
    'danh_gia': fields.Float(description='Rating out of 5.0'),
    'language': fields.String(description='Language of the result (vietnamese, english, chinese, korean, japanese)')
})

sync_response_model = travel_chatbot_ns.model('SyncResponse', {
    'status': fields.String(description='Status of the sync operation'),
    'message': fields.String(description='Detailed message about the sync operation'),
    'processed_count': fields.Integer(description='Number of locations processed')
})

# Model cho response của embeddings
embedding_model = travel_chatbot_ns.model('Embedding', {
    'id': fields.String(description='Location ID'),
    'embedding': fields.List(fields.Float, description='Vector embedding'),
    'document': fields.String(description='Original document text')
})

embeddings_response_model = travel_chatbot_ns.model('EmbeddingsResponse', {
    'status': fields.String(description='Status of the operation'),
    'message': fields.String(description='Detailed message'),
    'total': fields.Integer(description='Total number of embeddings'),
    'total_pages': fields.Integer(description='Total number of pages'),
    'current_page': fields.Integer(description='Current page number'),
    'items_per_page': fields.Integer(description='Number of items per page'),
    'embeddings': fields.List(fields.Nested(embedding_model), description='List of embeddings')
})

# Model cho response của metadata
metadata_model = travel_chatbot_ns.model('Metadata', {
    'id': fields.String(description='Location ID'),
    'ten_dia_diem': fields.String(description='Location name'),
    'mo_ta': fields.String(description='Location description'),
    'loai_dia_diem': fields.String(description='Category/Type'),
    'khu_vuc': fields.String(description='Region/Area'),
    'dia_chi': fields.String(description='Address'),
    'latitude': fields.Float(description='Latitude'),
    'longitude': fields.Float(description='Longitude'),
    'google_maps_url': fields.String(description='Google Maps URL'),
    'image_url': fields.String(description='Thumbnail image URL'),
    'thoi_gian_hoat_dong': fields.String(description='Opening hours'),
    'gia_ve': fields.String(description='Ticket price'),
    'danh_gia': fields.Float(description='Rating out of 5'),
    'additional_info': fields.Raw(description='Additional metadata information')
})

metadata_response_model = travel_chatbot_ns.model('MetadataResponse', {
    'status': fields.String(description='Status of the operation'),
    'message': fields.String(description='Detailed message'),
    'total': fields.Integer(description='Total number of metadata entries'),
    'total_pages': fields.Integer(description='Total number of pages'),
    'current_page': fields.Integer(description='Current page number'),
    'items_per_page': fields.Integer(description='Number of items per page'),
    'metadata': fields.List(fields.Nested(metadata_model), description='List of metadata entries')
})

# Cập nhật model response để hỗ trợ chatbot
chatbot_response_model = travel_chatbot_ns.model('ChatbotResponse', {
    'status': fields.String(description='Status of the operation'),
    'message': fields.String(description='Detailed message'),
    'response': fields.String(description='Natural language response from chatbot'),
    'ai_status': fields.String(description='Whether the generated answer used the language model or a local fallback'),
    'ai_pipeline': fields.Raw(description='Details of AI agents involved in the response'),
    'web_search_queries': fields.List(fields.String, description='Web search queries executed'),
    'web_sources': fields.List(fields.Raw, description='Grounding web sources and links'),
    'language': fields.String(description='Detected language'),
    'language_name': fields.String(description='Language name in native script'),
    'search_results': fields.List(fields.Nested(answer_model), description='Search results'),
    'suggested_activities': fields.List(fields.String, description='Suggested activities'),
    'follow_up_questions': fields.List(fields.String, description='Follow-up questions'),
    'extracted_features': fields.Raw(description='Extracted entities and features')
})


def _chat_fallback_message(language, error_code, has_results):
    """Return a localized, provider-safe message when LLM generation fails."""
    quota = error_code == 'quota_exceeded'
    messages = {
        'vietnamese': {
            'quota': 'AI hội thoại đã hết hạn mức API. Kết quả địa điểm vẫn hiển thị bên dưới; lập lịch đa tác tử ở tab Lịch trình.',
            'error': 'AI hội thoại đang tạm thời không khả dụng. Kết quả địa điểm vẫn hiển thị bên dưới; lập lịch đa tác tử ở tab Lịch trình.'
        },
        'english': {
            'quota': 'The chat AI has reached its API limit. Place results are shown below; use the Itinerary tab for multi-agent planning.',
            'error': 'The chat AI is temporarily unavailable. Place results are shown below; use the Itinerary tab for multi-agent planning.'
        },
        'japanese': {
            'quota': 'チャットAIのAPI利用上限に達しました。場所の検索結果は下に表示されます。マルチエージェントの計画には「旅程」タブをご利用ください。',
            'error': 'チャットAIは一時的に利用できません。場所の検索結果は下に表示されます。マルチエージェントの計画には「旅程」タブをご利用ください。'
        },
        'korean': {
            'quota': '채팅 AI의 API 한도에 도달했습니다. 장소 검색 결과는 아래에 표시됩니다. 다중 에이전트 일정은 일정 탭을 이용하세요.',
            'error': '채팅 AI를 일시적으로 사용할 수 없습니다. 장소 검색 결과는 아래에 표시됩니다. 다중 에이전트 일정은 일정 탭을 이용하세요.'
        },
        'chinese': {
            'quota': '聊天 AI 已达到 API 配额。地点搜索结果会显示在下方；多智能体行程请使用“行程”标签。',
            'error': '聊天 AI 暂时不可用。地点搜索结果会显示在下方；多智能体行程请使用“行程”标签。'
        }
    }
    message = messages.get(language, messages['vietnamese'])
    if not has_results:
        if language == 'english':
            return 'The chat AI is temporarily unavailable and no place results were found. Use the Itinerary tab for multi-agent planning.'
        return 'AI hội thoại đang tạm thời không khả dụng và chưa tìm thấy địa điểm phù hợp. Lập lịch đa tác tử ở tab Lịch trình.'
    return message['quota' if quota else 'error']

def paginate_results(results, page, limit, sort_by='id', sort_order='asc'):
    """
    Phân trang và sắp xếp kết quả
    """
    if not results:
        return [], 0, 0
    
    # Sắp xếp kết quả
    if sort_order.lower() == 'desc':
        results = sorted(results, key=lambda x: x.get(sort_by, ''), reverse=True)
    else:
        results = sorted(results, key=lambda x: x.get(sort_by, ''))
    
    # Tính toán phân trang
    total = len(results)
    total_pages = math.ceil(total / limit)
    page = min(max(1, page), total_pages)  # Đảm bảo page nằm trong khoảng hợp lệ
    
    # Lấy kết quả cho trang hiện tại
    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    paginated_results = results[start_idx:end_idx]
    
    return paginated_results, total, total_pages

@travel_chatbot_ns.route('/search')
class SearchLocation(Resource):
    @travel_chatbot_ns.expect(question_model)
    @travel_chatbot_ns.marshal_with(chatbot_response_model)
    def post(self):
        """Search for travel locations with natural language chatbot response"""
        try:
            # Lấy câu hỏi từ request
            data = request.get_json() or {}
            question = data.get('question')
            enable_web_search = data.get('enable_web_search', None)
            
            if not question:
                return {
                    'status': 'error',
                    'message': 'Question is required',
                    'response': '',
                    'language': 'unknown',
                    'language_name': 'Unknown',
                    'search_results': [],
                    'suggested_activities': [],
                    'follow_up_questions': [],
                    'extracted_features': {}
                }, 400
            
            # Bước 1: Nhận biết ngôn ngữ
            print("=== NHẬN BIẾT NGÔN NGỮ ===")
            language_result = detect_language(question)
            print("Kết quả nhận biết ngôn ngữ:", language_result)
            
            # Kiểm tra ngôn ngữ có được hỗ trợ không
            if not language_result.get('is_supported', False):
                lang_info = get_language_info('unknown')
                return {
                    'status': 'error',
                    'message': lang_info['unsupported_message'],
                    'response': lang_info['unsupported_message'],
                    'language': 'unsupported',
                    'language_name': 'Unsupported',
                    'search_results': [],
                    'suggested_activities': [],
                    'follow_up_questions': [],
                    'extracted_features': {}
                }, 400
            
            detected_language = language_result.get('language', 'vietnamese')
            lang_info = get_language_info(detected_language)
            print(f"Ngôn ngữ phát hiện: {lang_info['name']} (confidence: {language_result.get('confidence', 0):.1%})")
            print("==========================")
            
            # Phase 2: Multi-Agent Coordinator Integration (with zero-downtime rollback)
            try:
                from src.services.ai.backend_adapter import is_coordinator_enabled, handle_coordinator_chat_request
                if is_coordinator_enabled():
                    coord_payload, status_code = handle_coordinator_chat_request(
                        request_data=data,
                        request_headers=request.headers,
                        detected_language=detected_language
                    )
                    if coord_payload is not None:
                        return coord_payload, status_code
            except Exception as coord_err:
                print(f"[CoordinatorAdapter] Error running multi-agent coordinator: {coord_err}. Falling back to legacy pipeline.")

            # Bước 2: Trích xuất thực thể và ý định từ câu hỏi
            print("=== TRÍCH XUẤT THỰC THỂ ===")
            extraction_result = extract_user_intent_and_features(question)
            
            # In ra kết quả trích xuất
            print("Câu hỏi gốc:", extraction_result.get('original_question', question))
            print("Ý định:", extraction_result.get('intent', 'unknown'))
            print("Độ tin cậy:", f"{extraction_result.get('confidence', 0):.1%}")
            print("Thực thể trích xuất:")
            for key, value in extraction_result.get('extracted_features', {}).items():
                print(f"  - {key}: {value}")
            print("==========================")
            
            # Format kết quả trích xuất để hiển thị
            try:
                formatted_extraction = format_extraction_result(extraction_result)
                print("Kết quả trích xuất đã format:")
                print(formatted_extraction)
                print("==========================")
            except Exception:
                pass
            
            # Bước 3: Thực hiện tìm kiếm kết hợp với bộ lọc
            print("=== THỰC HIỆN TÌM KIẾM TỪ POSTGRESQL STAGING (30.800+ POIs) ===")
            formatted_results = search_pois_from_postgresql(
                question=question,
                extracted_features=extraction_result.get('extracted_features', {}),
                limit=6
            )
            print(f"Số địa điểm PostgreSQL tìm thấy: {len(formatted_results)}")
            print("==========================")

            print("=== SINH CÂU TRẢ LỜI TỰ NHIÊN ===")
            try:
                print("About to call create_chatbot_response...")
                print(f"Question: {question}")
                print(f"Search results count: {len(formatted_results)}")
                print(f"Extracted features: {extraction_result.get('extracted_features', {})}")
                print(f"Language: {detected_language}")
                
                chatbot_response = create_chatbot_response(
                    question=question,
                    search_results=formatted_results,
                    extracted_features=extraction_result.get('extracted_features', {}),
                    language=detected_language,
                    enable_web_search=enable_web_search
                )
                
                print("create_chatbot_response completed successfully")
                print("Câu trả lời chatbot đã được tạo")
                print("Độ dài câu trả lời:", len(chatbot_response.get('response', '')))
                print("Số hoạt động gợi ý:", len(chatbot_response.get('suggested_activities', [])))
                print("Số câu hỏi tiếp theo:", len(chatbot_response.get('follow_up_questions', [])))
                
            except Exception as e:
                print(f"Chat answer generation failed ({type(e).__name__}): {e}")
                chatbot_response = {
                    'status': 'success',
                    'response': _chat_fallback_message(detected_language, 'provider_unavailable', bool(formatted_results)),
                    'language': detected_language,
                    'suggested_activities': [],
                    'follow_up_questions': []
                }

            ai_status = 'ready'
            if chatbot_response.get('status') != 'success':
                ai_status = 'unavailable'
                chatbot_response = {
                    **chatbot_response,
                    'response': _chat_fallback_message(
                        detected_language,
                        chatbot_response.get('error_code'),
                        bool(formatted_results)
                    ),
                    'status': 'success'
                }
                
            print("==========================")
            
            # Tạo thông tin đường ống xử lý của các tác tử AI
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

            # Trả về kết quả hoàn chỉnh
            return {
                'status': 'success',
                'message': 'Results and response are ready',
                'response': chatbot_response.get('response', ''),
                'ai_status': ai_status,
                'ai_pipeline': ai_pipeline,
                'web_search_queries': web_queries,
                'web_sources': web_sources,
                'language': detected_language,
                'language_name': lang_info['name'],
                'search_results': chatbot_response.get('search_results') or formatted_results,
                'suggested_activities': chatbot_response.get('suggested_activities', []),
                'follow_up_questions': chatbot_response.get('follow_up_questions', []),
                'extracted_features': extraction_result.get('extracted_features', {})
            }
            
        except Exception as e:
            print(f"Travel chatbot request failed ({type(e).__name__}): {e}")
            traceback.print_exc()
            return {
                'status': 'error',
                'message': 'Travel chatbot processing failed',
                'response': 'Không thể xử lý yêu cầu lúc này. Vui lòng thử lại sau.',
                'ai_status': 'unavailable',
                'language': 'unknown',
                'language_name': 'Unknown',
                'search_results': [],
                'suggested_activities': [],
                'follow_up_questions': [],
                'extracted_features': {}
            }, 500

@travel_chatbot_ns.route('/embeddings')
class GetEmbeddings(Resource):
    @travel_chatbot_ns.expect(pagination_parser)
    @travel_chatbot_ns.marshal_with(embeddings_response_model)
    def get(self):
        """ChromaDB embeddings retired in favor of PostgreSQL POI Engine"""
        args = pagination_parser.parse_args()
        return {
            'status': 'success',
            'message': 'ChromaDB embeddings retired. All POI queries now use PostgreSQL osm_attractions_staging.',
            'total': 0,
            'total_pages': 0,
            'current_page': args['page'],
            'items_per_page': args['limit'],
            'embeddings': []
        }

@travel_chatbot_ns.route('/metadata')
class GetMetadata(Resource):
    @travel_chatbot_ns.expect(pagination_parser)
    @travel_chatbot_ns.marshal_with(metadata_response_model)
    def get(self):
        """Get POI metadata directly from PostgreSQL osm_attractions_staging with pagination"""
        try:
            from src import db
            from sqlalchemy import text
            args = pagination_parser.parse_args()
            page = max(1, args['page'])
            limit = min(100, max(1, args['limit']))
            offset = (page - 1) * limit
            
            count_res = db.session.execute(text("SELECT count(*) FROM osm_attractions_staging")).scalar() or 0
            rows = db.session.execute(
                text("SELECT osm_id, name, category, address, latitude, longitude FROM osm_attractions_staging ORDER BY osm_id LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset}
            ).fetchall()
            
            from src.services.travel_chatbot_service import _get_curated_image_and_meta
            metadata_list = []
            for r in rows:
                osm_id, name, category, address, lat, lon = r[0], r[1], r[2], r[3], r[4], r[5]
                lat_f = float(lat) if lat is not None else None
                lon_f = float(lon) if lon is not None else None
                gmaps_url = f"https://www.google.com/maps?q={lat_f:.6f},{lon_f:.6f}" if (lat_f and lon_f) else None
                addr_str = address if (address and "chưa có địa chỉ" not in address.lower()) else (f"{name}, Việt Nam" if name else "Việt Nam")
                img_url, hours, price, rating, desc = _get_curated_image_and_meta(name or '', category or '', '', 'Việt Nam')
                metadata_list.append({
                    'id': str(osm_id),
                    'ten_dia_diem': name or '',
                    'mo_ta': desc or f"Phân loại: {category or 'Địa điểm'} - Địa chỉ: {addr_str}",
                    'loai_dia_diem': category or 'Địa điểm',
                    'khu_vuc': 'Việt Nam',
                    'dia_chi': addr_str,
                    'latitude': lat_f,
                    'longitude': lon_f,
                    'google_maps_url': gmaps_url,
                    'image_url': img_url,
                    'thoi_gian_hoat_dong': hours,
                    'gia_ve': price,
                    'danh_gia': rating,
                    'additional_info': {'category': category, 'address': address, 'latitude': lat_f, 'longitude': lon_f}
                })
            
            total_pages = math.ceil(count_res / limit) if limit > 0 else 1
            return {
                'status': 'success',
                'message': f'Retrieved {len(metadata_list)} places from PostgreSQL',
                'total': count_res,
                'total_pages': total_pages,
                'current_page': page,
                'items_per_page': limit,
                'metadata': metadata_list
            }
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'total': 0,
                'total_pages': 0,
                'current_page': 1,
                'items_per_page': 10,
                'metadata': []
            }, 500

@travel_chatbot_ns.route('/sync-diadiem')
class SyncDiadiem(Resource):
    @travel_chatbot_ns.marshal_with(sync_response_model)
    def post(self):
        """POI data is managed directly in PostgreSQL"""
        return {
            'status': 'success',
            'message': 'Diadiem ChromaDB sync is deprecated. POI data is maintained in PostgreSQL osm_attractions_staging.',
            'processed_count': 0
        }



