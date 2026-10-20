import re
import logging
from datetime import datetime
from flask import request
from flask_restx import Namespace, Resource, fields
from src.services.Itinerary_service import (
    create_itinerary_with_items,
    get_user_itineraries,
    get_itinerary_by_id,
    delete_itinerary,
    update_itinerary_item,
    confirm_draft_itinerary
)
from src.services.ai.orchestrator import orchestrate_draft_itinerary

logger = logging.getLogger(__name__)

DRAFT_ID_REGEX = re.compile(r"^draft_[0-9a-fA-F]{12}$")

# Create namespace for itinerary API
itinerary_ns = Namespace('itinerary', description='Itinerary management operations')

# Define attraction model for nested response
attraction_model = itinerary_ns.model('Attraction', {
    'id': fields.String(description='Attraction ID'),
    'name': fields.String(description='Attraction name'),
    'address': fields.String(description='Attraction address'),
    'description': fields.String(description='Attraction description'),
    'latitude': fields.Float(description='Latitude coordinate'),
    'longitude': fields.Float(description='Longitude coordinate'),
    'category': fields.String(description='Attraction category'),
    'rating': fields.Float(description='Attraction rating'),
    'image_url': fields.String(description='Attraction image URL'),
    'language': fields.String(description='Language of the attraction'),
    'phone': fields.String(description='Attraction phone number'),
    'opening_hours': fields.String(description='Opening hours'),
    'price': fields.Float(description='Price range'),
    'tags': fields.List(fields.String, description='Attraction tags')
})

# Define itinerary item model
itinerary_item_model = itinerary_ns.model('ItineraryItem', {
    'id': fields.Integer(description='Itinerary item ID'),
    'itinerary_id': fields.Integer(description='Itinerary ID'),
    'attraction': fields.Nested(attraction_model, description='Attraction details'),
    'attraction_id': fields.String(description='Attraction ID'),
    'visit_time': fields.String(description='Visit time in ISO format'),
    'estimated_duration': fields.Integer(description='Estimated duration in minutes'),
    'notes': fields.String(description='Additional notes'),
    'order_index': fields.Integer(description='Order index in itinerary'),
    'created_at': fields.String(description='Creation time in ISO format')
})

# Define itinerary model
itinerary_model = itinerary_ns.model('Itinerary', {
    'id': fields.Integer(description='Itinerary ID'),
    'user_id': fields.Integer(description='User ID'),
    'selected_date': fields.String(description='Selected date in ISO format'),
    'title': fields.String(description='Itinerary title'),
    'notes': fields.String(description='General notes for itinerary'),
    'created_at': fields.String(description='Creation time in ISO format'),
    'updated_at': fields.String(description='Last update time in ISO format'),
    'items': fields.List(fields.Nested(itinerary_item_model), description='List of itinerary items')
})

# Define request model for creating itinerary
create_itinerary_request_model = itinerary_ns.model('CreateItineraryRequest', {
    'user_id': fields.Integer(required=True, description='User ID'),
    'selected_date': fields.String(required=True, description='Selected date in ISO format (YYYY-MM-DD)'),
    'itinerary_items': fields.List(fields.Raw, required=True, description='List of itinerary items'),
    'title': fields.String(description='Optional title for the itinerary'),
    'notes': fields.String(description='General notes for the itinerary')
})

# Define request model for updating itinerary item
update_itinerary_request_model = itinerary_ns.model('UpdateItineraryRequest', {
    'visit_time': fields.String(description='New visit time in ISO format'),
    'estimated_duration': fields.Integer(description='New estimated duration in minutes'),
    'notes': fields.String(description='New notes')
})

# Define response models
itinerary_response_model = itinerary_ns.model('ItineraryResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.Nested(itinerary_model, description='Itinerary data')
})

itinerary_list_response_model = itinerary_ns.model('ItineraryListResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.List(fields.Nested(itinerary_model), description='List of itineraries')
})

@itinerary_ns.route('/create')
class CreateItineraryResource(Resource):
    @itinerary_ns.expect(create_itinerary_request_model)
    @itinerary_ns.response(201, 'Successfully created itinerary', itinerary_response_model)
    @itinerary_ns.response(400, 'Invalid request data')
    @itinerary_ns.response(404, 'User or attraction not found')
    @itinerary_ns.response(500, 'Internal server error')
    def post(self):
        """Create a new itinerary with multiple items"""
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['user_id', 'selected_date', 'itinerary_items']
        for field in required_fields:
            if field not in data:
                return {'message': f'Missing required field: {field}'}, 400
        
        # Validate itinerary_items is a list and not empty
        if not isinstance(data['itinerary_items'], list) or len(data['itinerary_items']) == 0:
            return {'message': 'itinerary_items must be a non-empty list'}, 400
        
        # Validate each itinerary item
        for index, item in enumerate(data['itinerary_items']):
            if not isinstance(item, dict):
                return {'message': f'itinerary_items[{index}] must be an object'}, 400
            
            item_required_fields = ['attraction_id', 'visit_time']
            for field in item_required_fields:
                if field not in item:
                    return {'message': f'Missing required field: {field} in itinerary_items[{index}]'}, 400
        
        try:
            success, result = create_itinerary_with_items(
                user_id=data['user_id'],
                selected_date=data['selected_date'],
                itinerary_items=data['itinerary_items'],
                title=data.get('title'),
                notes=data.get('notes')
            )
            
            if not success:
                if "not found" in str(result):
                    return {'message': str(result)}, 404
                return {'message': str(result)}, 400
                
            return {
                'status': 'success',
                'message': 'Successfully created itinerary',
                'data': result
            }, 201
            
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500

@itinerary_ns.route('/list')
class UserItinerariesResource(Resource):
    @itinerary_ns.doc(params={
        'user_id': 'User ID (required)'
    })
    @itinerary_ns.response(200, 'Successfully retrieved itineraries', itinerary_list_response_model)
    @itinerary_ns.response(404, 'User not found')
    @itinerary_ns.response(500, 'Internal server error')
    def get(self):
        """Get all itineraries for a user"""
        user_id = request.args.get('user_id', type=int)
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        try:
            success, result = get_user_itineraries(user_id)
            
            if not success:
                if "not found" in str(result):
                    return {'message': str(result)}, 404
                return {'message': str(result)}, 500
                
            return {
                'status': 'success',
                'message': f'Successfully retrieved itineraries for user {user_id}',
                'data': result
            }
            
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500

@itinerary_ns.route('/detail')
class ItineraryDetailResource(Resource):
    @itinerary_ns.doc(params={
        'itinerary_id': 'Itinerary ID (required)',
        'user_id': 'User ID (required for authorization)'
    })
    @itinerary_ns.response(200, 'Successfully retrieved itinerary', itinerary_response_model)
    @itinerary_ns.response(404, 'Itinerary not found')
    @itinerary_ns.response(403, 'Not authorized to view this itinerary')
    @itinerary_ns.response(500, 'Internal server error')
    def get(self):
        """Get a specific itinerary by ID"""
        itinerary_id = request.args.get('itinerary_id', type=int)
        user_id = request.args.get('user_id', type=int)
        if not itinerary_id:
            return {'message': 'itinerary_id parameter is required'}, 400
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        try:
            success, result = get_itinerary_by_id(itinerary_id, user_id)
            if not success:
                if "not found" in str(result):
                    return {'message': str(result)}, 404
                if "not authorized" in str(result):
                    return {'message': str(result)}, 403
                return {'message': str(result)}, 500
            return {
                'status': 'success',
                'message': f'Successfully retrieved itinerary {itinerary_id}',
                'data': result
            }
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500

@itinerary_ns.route('/delete')
class ItineraryDeleteResource(Resource):
    @itinerary_ns.doc(params={
        'itinerary_id': 'Itinerary ID (required)',
        'user_id': 'User ID (required for authorization)'
    })
    @itinerary_ns.response(200, 'Successfully deleted itinerary')
    @itinerary_ns.response(404, 'Itinerary not found')
    @itinerary_ns.response(403, 'Not authorized to delete this itinerary')
    @itinerary_ns.response(500, 'Internal server error')
    def delete(self):
        """Delete an entire itinerary"""
        itinerary_id = request.args.get('itinerary_id', type=int)
        user_id = request.args.get('user_id', type=int)
        if not itinerary_id:
            return {'message': 'itinerary_id parameter is required'}, 400
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        try:
            success, result = delete_itinerary(itinerary_id, user_id)
            if not success:
                if "not found" in str(result):
                    return {'message': str(result)}, 404
                if "not authorized" in str(result):
                    return {'message': str(result)}, 403
                return {'message': str(result)}, 500
            return {
                'status': 'success',
                'message': result
            }
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500

@itinerary_ns.route('/item/<int:item_id>/update')
class UpdateItineraryItemResource(Resource):
    @itinerary_ns.expect(update_itinerary_request_model)
    @itinerary_ns.response(200, 'Successfully updated itinerary item', itinerary_response_model)
    @itinerary_ns.response(400, 'Invalid request data')
    @itinerary_ns.response(404, 'Itinerary item not found')
    @itinerary_ns.response(403, 'Not authorized to update this item')
    @itinerary_ns.response(500, 'Internal server error')
    def put(self, item_id):
        """Update a specific itinerary item"""
        data = request.get_json()
        user_id = request.args.get('user_id', type=int)
        
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        
        try:
            success, result = update_itinerary_item(
                item_id=item_id,
                user_id=user_id,
                visit_time=data.get('visit_time'),
                estimated_duration=data.get('estimated_duration'),
                notes=data.get('notes')
            )
            
            if not success:
                if "not found" in str(result):
                    return {'message': str(result)}, 404
                if "not authorized" in str(result):
                    return {'message': str(result)}, 403
                return {'message': str(result)}, 400
                
            return {
                'status': 'success',
                'message': 'Successfully updated itinerary item',
                'data': result
            }
            
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500


# =====================================================================
# GIAI ĐOẠN H1: API LẬP LỊCH TRÌNH NHÁP (DRAFT ITINERARY API)
# =====================================================================

plan_draft_request_model = itinerary_ns.model('PlanDraftItineraryRequest', {
    'selected_date': fields.String(required=True, description='Selected date in format YYYY-MM-DD', example='2026-10-25'),
    'query': fields.String(description='Search keyword for POIs (e.g. "Cà phê", "Bảo tàng")', example='Cà phê'),
    'destination': fields.String(description='Alternative destination or city name', example='Hà Nội'),
    'area': fields.String(description='District or area name', example='Hoàn Kiếm'),
    'category': fields.String(description='Category or OSM fclass filter', example='cafe'),
    'latitude': fields.Float(description='Center latitude for spatial radius search', example=21.04),
    'longitude': fields.Float(description='Center longitude for spatial radius search', example=105.84),
    'radius_km': fields.Float(description='Search radius around coordinates in km', default=5.0, example=5.0),
    'max_stops': fields.Integer(description='Maximum stops in itinerary (default: 3)', default=3, example=3),
    'preferences': fields.Raw(description='Travel preferences (string or object)', example='Thích không gian yên tĩnh và ẩm thực nhẹ nhàng'),
    'user_prompt': fields.String(description='Natural language prompt describing travel intent'),
    'budget': fields.String(description='Estimated budget if applicable', example='500.000 VNĐ'),
    'user_id': fields.Integer(description='Optional user ID for tracking')
})

plan_draft_response_model = itinerary_ns.model('PlanDraftItineraryResponse', {
    'status': fields.String(description='Status of draft generation: success | validation_failed | no_pois_found | invalid_request'),
    'message': fields.String(description='Status description message'),
    'is_saved': fields.Boolean(description='Whether itinerary is saved to database (always False for draft)', default=False),
    'draft_id': fields.String(description='Unique identifier for draft itinerary'),
    'draft': fields.Raw(description='Draft itinerary details'),
    'validation': fields.Raw(description='Constraint validation report'),
    'route_legs': fields.List(fields.Raw, description='Calculated route legs with estimated distance'),
    'warnings': fields.List(fields.String, description='List of system warnings and notices'),
    'summary': fields.Raw(description='Summary of itinerary metrics'),
    'iterations': fields.Integer(description='Number of planning attempts performed')
})


@itinerary_ns.route('/plan-draft')
@itinerary_ns.route('/multi-agent/plan-draft')
class PlanDraftItineraryResource(Resource):
    @itinerary_ns.expect(plan_draft_request_model)
    @itinerary_ns.response(200, 'Successfully generated draft itinerary', plan_draft_response_model)
    @itinerary_ns.response(400, 'Invalid request data')
    @itinerary_ns.response(404, 'No POIs found in the requested area')
    @itinerary_ns.response(422, 'Draft itinerary failed constraint validation after retries')
    @itinerary_ns.response(500, 'Internal server error')
    @itinerary_ns.response(503, 'POI database is unavailable')
    def post(self):
        """Generate a draft itinerary using Multi-Agent Orchestrator (In-memory draft only, no DB writes)"""
        data = request.get_json() or {}

        # 1. Validate required fields
        selected_date = data.get('selected_date')
        if not selected_date:
            return {'status': 'invalid_request', 'message': 'Missing required field: selected_date'}, 400

        try:
            datetime.fromisoformat(str(selected_date).split('T')[0])
        except (ValueError, TypeError):
            return {
                'status': 'invalid_request',
                'message': f"Invalid date format for 'selected_date': '{selected_date}'. Expected YYYY-MM-DD."
            }, 400

        # Text names are labels only; POI data is spatial, so an area search needs a center point.
        latitude_raw = data.get('latitude')
        longitude_raw = data.get('longitude')
        has_any_coord = latitude_raw is not None or longitude_raw is not None
        has_coords = latitude_raw is not None and longitude_raw is not None
        has_text = bool(
            data.get('query') or data.get('destination') or data.get('area') or
            data.get('location') or data.get('user_prompt')
        )

        if has_any_coord and not has_coords:
            return {
                'status': 'invalid_request',
                'message': 'Hãy nhập cả latitude và longitude, hoặc để trống cả hai.'
            }, 400

        if has_coords:
            try:
                latitude = float(latitude_raw)
                longitude = float(longitude_raw)
                radius_raw = data.get('radius_km')
                radius_km = float(5.0 if radius_raw in (None, '') else radius_raw)
            except (TypeError, ValueError):
                return {'status': 'invalid_request', 'message': 'Tọa độ và bán kính phải là số hợp lệ.'}, 400

            if not (8.0 <= latitude <= 24.0 and 102.0 <= longitude <= 110.0):
                return {'status': 'invalid_request', 'message': 'Tọa độ tìm kiếm phải nằm trong phạm vi Việt Nam.'}, 400
            if not (0.1 <= radius_km <= 100.0):
                return {'status': 'invalid_request', 'message': 'Bán kính tìm kiếm phải từ 0,1 đến 100 km.'}, 400
        else:
            latitude = longitude = radius_km = None

        if (data.get('area') or data.get('location')) and not has_coords:
            return {
                'status': 'invalid_request',
                'message': 'Khu vực dạng chữ chưa thể tự đổi thành tọa độ. Hãy nhập cả vĩ độ và kinh độ tâm tìm kiếm để hệ thống giới hạn POI đúng khu vực.'
            }, 400

        if not (has_coords or has_text):
            return {
                'status': 'invalid_request',
                'message': "Hãy nhập tọa độ tìm kiếm hoặc từ khóa địa điểm."
            }, 400

        # 2. Normalize preferences (handle string, dict or user_prompt)
        raw_pref = data.get('preferences')
        if isinstance(raw_pref, dict):
            pref_parts = []
            if raw_pref.get('categories'):
                cats = raw_pref['categories']
                pref_parts.append(f"Danh mục: {', '.join(cats) if isinstance(cats, list) else cats}")
            if raw_pref.get('district'):
                pref_parts.append(f"Khu vực: {raw_pref['district']}")
            if raw_pref.get('pace'):
                pref_parts.append(f"Nhịp độ: {raw_pref['pace']}")
            if raw_pref.get('budget_vnd'):
                pref_parts.append(f"Ngân sách: {raw_pref['budget_vnd']} VNĐ")
            pref_str = "; ".join(pref_parts)
        else:
            pref_str = str(raw_pref) if raw_pref else None

        if data.get('user_prompt'):
            pref_str = f"{data.get('user_prompt')}. {pref_str}" if pref_str else str(data.get('user_prompt'))

        user_req = {
            'selected_date': str(selected_date).split('T')[0],
            'query': data.get('query'),
            'destination': data.get('destination'),
            'area': data.get('area') or data.get('location'),
            'category': data.get('category'),
            'latitude': latitude,
            'longitude': longitude,
            'radius_km': radius_km,
            'max_stops': data.get('max_stops', 3),
            'preferences': pref_str,
            'budget': data.get('budget'),
            'user_id': data.get('user_id')
        }

        # 3. Call Orchestrator (strictly execute_api=False)
        try:
            orch_result = orchestrate_draft_itinerary(
                user_request=user_req,
                candidate_pois=None,
                execute_api=False,
                max_retries=2
            )

            status = orch_result.get('status')

            if status in ('no_pois_found', 'needs_more_pois'):
                return {
                    'status': status,
                    'message': orch_result.get('message'),
                    'warnings': orch_result.get('warnings', []),
                    'candidate_pois_count': orch_result.get('candidate_pois_count', 0),
                    'draft': None,
                    'validation': None
                }, 404

            if status == 'validation_failed':
                return {
                    'status': 'validation_failed',
                    'message': orch_result.get('message'),
                    'draft': orch_result.get('draft'),
                    'validation': orch_result.get('validation'),
                    'route_legs': orch_result.get('route_legs', []),
                    'warnings': orch_result.get('warnings', []),
                    'iterations': orch_result.get('iterations', 0),
                    'max_retries': orch_result.get('max_retries', 2),
                    'history': orch_result.get('history', [])
                }, 422

            if status == 'invalid_request':
                return {
                    'status': 'invalid_request',
                    'message': orch_result.get('message'),
                    'warnings': orch_result.get('warnings', [])
                }, 400

            if status == 'database_error':
                return {
                    'status': 'database_error',
                    'message': orch_result.get('message', 'Cơ sở dữ liệu địa điểm hiện không khả dụng.'),
                    'warnings': orch_result.get('warnings', [])
                }, 503

            # Success response (Draft only: is_saved is always False)
            return {
                'status': 'success',
                'message': orch_result.get('message', 'Successfully generated draft itinerary'),
                'is_saved': False,
                'draft_id': orch_result.get('draft', {}).get('draft_id') if orch_result.get('draft') else None,
                'draft': orch_result.get('draft'),
                'validation': orch_result.get('validation'),
                'route_legs': orch_result.get('route_legs', []),
                'warnings': orch_result.get('warnings', []),
                'summary': orch_result.get('validation', {}).get('summary') if orch_result.get('validation') else None,
                'iterations': orch_result.get('iterations', 1)
            }, 200

        except Exception as e:
            logger.exception('Unexpected error generating draft itinerary')
            return {'status': 'error', 'message': 'Có lỗi nội bộ khi tạo lịch trình nháp.'}, 500


# =====================================================================
# GIAI ĐOẠN H2-B: API XÁC NHẬN & LƯU LỊCH TRÌNH (CONFIRM ITINERARY API)
# =====================================================================

def get_authenticated_user_id():
    """
    Trích xuất và xác thực user_id từ JWT Bearer token trong Authorization header.
    Tuyệt đối không tin user_id do client truyền trong body hay query parameter.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None, "Missing Authorization header with Bearer token"

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None, "Invalid Authorization header format. Expected 'Bearer <token>'"

    token = parts[1]
    try:
        import jwt
        from src.config.config import Config
        from src.models.user import User

        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=['HS256'])
        user_id = payload.get('user_id')
        if not user_id:
            return None, "Token payload does not contain user_id"

        user = User.query.get(user_id)
        if not user:
            return None, f"Authenticated user with ID {user_id} not found in database"
        if not user.is_verified:
            return None, "User email has not been verified"

        return user_id, None
    except jwt.ExpiredSignatureError:
        return None, "Token has expired. Please log in again."
    except jwt.InvalidTokenError as e:
        return None, f"Invalid authentication token: {str(e)}"
    except Exception as e:
        return None, f"Authentication error: {str(e)}"


confirm_itinerary_request_model = itinerary_ns.model('ConfirmItineraryRequest', {
    'draft_id': fields.String(required=True, description='Unique identifier of the draft itinerary (format: draft_ + 12 hex chars)', example='draft_1a2b3c4d5e6f'),
    'selected_date': fields.String(required=True, description='Travel date in format YYYY-MM-DD', example='2026-10-25'),
    'title': fields.String(description='Optional customized title for itinerary', example='Chuyến khám phá Hà Nội'),
    'notes': fields.String(description='Optional notes for itinerary'),
    'items': fields.List(fields.Raw, required=True, description='List of stops with osm_id, start_time, end_time, order_index')
})

confirm_itinerary_response_model = itinerary_ns.model('ConfirmItineraryResponse', {
    'status': fields.String(description='Response status (success | error | validation_failed | server_error)'),
    'message': fields.String(description='Status description message'),
    'is_saved': fields.Boolean(description='Whether itinerary is saved in database', default=True),
    'is_duplicate': fields.Boolean(description='Whether this was a duplicate request for an already confirmed draft'),
    'itinerary_id': fields.Integer(description='ID of the confirmed Itinerary'),
    'draft_id': fields.String(description='Draft ID confirmed'),
    'data': fields.Raw(description='Full persisted itinerary object with items and attractions')
})


@itinerary_ns.route('/confirm')
@itinerary_ns.route('/multi-agent/confirm')
class ConfirmItineraryResource(Resource):
    @itinerary_ns.expect(confirm_itinerary_request_model)
    @itinerary_ns.response(200, 'Successfully retrieved duplicate confirmed itinerary', confirm_itinerary_response_model)
    @itinerary_ns.response(201, 'Successfully confirmed and saved new itinerary', confirm_itinerary_response_model)
    @itinerary_ns.response(400, 'Invalid request data or missing parameters')
    @itinerary_ns.response(401, 'Unauthorized - Missing or invalid Bearer token')
    @itinerary_ns.response(403, 'Forbidden - User email not verified')
    @itinerary_ns.response(404, 'Draft or POI not found in staging')
    @itinerary_ns.response(422, 'Draft itinerary failed constraint validation on staging data')
    @itinerary_ns.response(500, 'Internal server error during transaction')
    def post(self):
        """Confirm a draft itinerary and atomically persist it to PostgreSQL (Phase H2-B)"""
        try:
            # 1. Xác thực người dùng qua JWT - TUYỆT ĐỐI KHÔNG TIN user_id từ request
            auth_user_id, auth_err = get_authenticated_user_id()
            if auth_err:
                return {'status': 'unauthorized', 'message': auth_err}, 401

            data = request.get_json() or {}

            # 2. Kiểm tra các trường bắt buộc cơ bản
            draft_id = data.get('draft_id')
            if not draft_id:
                return {'status': 'invalid_request', 'message': 'Missing required field: draft_id'}, 400

            if not isinstance(draft_id, str) or not DRAFT_ID_REGEX.match(draft_id):
                return {
                    'status': 'invalid_request',
                    'message': "Invalid draft_id format. Expected 'draft_' followed by 12 hex characters."
                }, 400

            if not data.get('selected_date'):
                return {'status': 'invalid_request', 'message': 'Missing required field: selected_date'}, 400
            if not data.get('items') or not isinstance(data.get('items'), list) or len(data['items']) == 0:
                return {'status': 'invalid_request', 'message': 'items must be a non-empty list'}, 400

            # 3. Gọi service confirm_draft_itinerary thực hiện toàn bộ transaction nguyên tử
            try:
                success, result, is_duplicate, status_code = confirm_draft_itinerary(
                    user_id=auth_user_id,
                    draft_data=data
                )
            except Exception as service_err:
                logger.error(f"Error calling confirm_draft_itinerary: {service_err}", exc_info=True)
                return {
                    'status': 'server_error',
                    'message': 'Đã xảy ra lỗi hệ thống khi xử lý lưu lịch trình. Vui lòng thử lại sau.'
                }, 500

            if not success:
                err_msg = str(result)
                if status_code == 500:
                    return {
                        'status': 'server_error',
                        'message': 'Đã xảy ra lỗi hệ thống khi xử lý lưu lịch trình. Vui lòng thử lại sau.'
                    }, 500
                elif status_code == 422:
                    return {'status': 'validation_failed', 'message': err_msg}, 422
                elif status_code == 404:
                    return {'status': 'not_found', 'message': err_msg}, 404
                elif status_code == 403:
                    return {'status': 'forbidden', 'message': err_msg}, 403
                else:
                    return {'status': 'invalid_request', 'message': err_msg}, 400

            # 4. Trả kết quả thành công kèm cờ is_duplicate (200 nếu trùng lặp, 201 nếu tạo mới)
            itinerary_data = result if isinstance(result, dict) else {}
            itinerary_id = itinerary_data.get('id')
            msg = "Lịch trình đã được xác nhận trước đó (yêu cầu trùng lặp)." if is_duplicate else "Xác nhận và lưu lịch trình thành công."

            return {
                'status': 'success',
                'message': msg,
                'is_saved': True,
                'is_duplicate': is_duplicate,
                'itinerary_id': itinerary_id,
                'draft_id': data.get('draft_id'),
                'data': itinerary_data
            }, (200 if is_duplicate else 201)

        except Exception as e:
            logger.error(f"Unhandled error in ConfirmItineraryResource: {e}", exc_info=True)
            return {
                'status': 'server_error',
                'message': 'Đã xảy ra lỗi hệ thống khi xử lý yêu cầu.'
            }, 500

