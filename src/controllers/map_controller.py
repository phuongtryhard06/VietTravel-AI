from flask import request
from flask_restx import Namespace, Resource, fields
from src.services.map_service import get_attractions_from_places, search_attractions_by_name

# Create namespace for map API
map_ns = Namespace('map', description='Map and location related operations')

# Define request model
places_request_model = map_ns.model('PlacesRequest', {
    'places': fields.List(fields.String, required=True, description='List of place names to search for attractions'),
    'language': fields.String(description='Language filter (english, chinese, korean, japanese, vietnamese)', required=False)
})

# Define attraction model
attraction_model = map_ns.model('Attraction', {
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

# Define response model
attractions_response_model = map_ns.model('AttractionsResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.List(fields.Nested(attraction_model), description='List of found attractions')
})

@map_ns.route('/attractions/from-places')
class AttractionsFromPlacesResource(Resource):
    @map_ns.expect(places_request_model)
    @map_ns.response(200, 'Successfully retrieved attractions', attractions_response_model)
    @map_ns.response(400, 'Invalid request data')
    @map_ns.response(500, 'Internal server error')
    def post(self):
        """Get attractions from a list of place names with optional language filtering"""
        data = request.get_json()
        
        # Validate required fields
        if not data or 'places' not in data:
            return {'message': 'Missing required field: places'}, 400
            
        places = data['places']
        if not isinstance(places, list):
            return {'message': 'Places must be a list'}, 400
            
        if not places:
            return {'message': 'Places list cannot be empty'}, 400
        
        # Get optional language parameter
        language = data.get('language')
        
        try:
            success, result = get_attractions_from_places(places, language)
            
            if not success:
                return {'message': f'Failed to get attractions: {result}'}, 500
                
            return {
                'status': 'success',
                'message': 'Successfully retrieved attractions',
                'data': result
            }
            
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500

@map_ns.route('/attractions/search')
class AttractionsSearchResource(Resource):
    @map_ns.doc(params={
        'q': 'Search query (required)',
        'language': 'Language filter (english, chinese, korean, japanese, vietnamese)',
        'limit': 'Maximum number of results (default: 20)'
    })
    @map_ns.response(200, 'Successfully searched attractions', attractions_response_model)
    @map_ns.response(400, 'Missing search query')
    @map_ns.response(500, 'Internal server error')
    def get(self):
        """Search attractions by name, address, description, or tags"""
        query = request.args.get('q', '')
        language = request.args.get('language')
        limit = request.args.get('limit', 20, type=int)
        
        if not query:
            return {'message': 'Search query parameter "q" is required'}, 400
        
        try:
            success, result = search_attractions_by_name(query, language, limit)
            
            if not success:
                return {'message': f'Failed to search attractions: {result}'}, 500
                
            return {
                'status': 'success',
                'message': f'Successfully searched attractions for query: {query}',
                'data': result
            }
            
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500
