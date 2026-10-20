from src.models.attraction import Attraction
from src.models.base import db
from typing import List, Dict, Any

def get_attractions_from_places(places: List[str], language: str = None) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Get attractions from a list of place names with optional language filtering
    
    Args:
        places (List[str]): List of place names to search for
        language (str, optional): Language filter (english, chinese, korean, japanese, vietnamese)
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        print(f"ℹ️ get_attractions_from_places with places: {places}, language: {language}")
        
        detected_attractions = []
        
        for place_name in places:
            if not place_name or not isinstance(place_name, str):
                continue
                
            lower_place_name = place_name.lower().strip()
            
            # Tìm kiếm trong database với filter ngôn ngữ
            query = Attraction.query
            
            # Thêm filter ngôn ngữ nếu có
            if language:
                query = query.filter(Attraction.language == language.lower())
            
            attractions = query.all()
            
            for attraction in attractions:
                attraction_name = attraction.name.lower() if attraction.name else ""
                
                # Kiểm tra tên địa điểm (tìm kiếm một phần)
                if (lower_place_name in attraction_name or 
                    attraction_name in lower_place_name):
                    # Kiểm tra xem đã có trong danh sách chưa
                    if not any(a['id'] == attraction.id for a in detected_attractions):
                        attraction_dict = {
                            'id': attraction.id,
                            'name': attraction.name,
                            'address': attraction.address,
                            'description': attraction.description,
                            'latitude': float(attraction.latitude) if attraction.latitude else None,
                            'longitude': float(attraction.longitude) if attraction.longitude else None,
                            'category': attraction.category,
                            'rating': float(attraction.rating) if attraction.rating else None,
                            'image_url': attraction.image_url,
                            'language': attraction.language,  # language field stores language
                            'phone': attraction.phone_number,
                            'opening_hours': attraction.opening_hours,
                            'price': attraction.price,
                            'tags': attraction.tags if attraction.tags else []
                        }
                        detected_attractions.append(attraction_dict)
                        print(f"ℹ️ Found attraction: {attraction.name} for place: {place_name}, language: {attraction.language}")
        
        print(f"ℹ️ Total attractions found: {len(detected_attractions)}")
        return True, detected_attractions
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi phát hiện địa điểm từ places: {e}')
        return False, str(e)

def get_attractions_by_language(language: str) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Get attractions by language
    
    Args:
        language (str): Language to filter by (english, chinese, korean, japanese, vietnamese)
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        if not language or not isinstance(language, str):
            return True, []
            
        # Validate language
        valid_languages = ['english', 'chinese', 'korean', 'japanese', 'vietnamese']
        if language.lower() not in valid_languages:
            return False, f"Invalid language. Must be one of: {', '.join(valid_languages)}"
        
        attractions = Attraction.query.filter(
            Attraction.language == language.lower()
        ).all()
        
        result = []
        for attraction in attractions:
            attraction_dict = {
                'id': attraction.id,
                'name': attraction.name,
                'address': attraction.address,
                'description': attraction.description,
                'latitude': float(attraction.latitude) if attraction.latitude else None,
                'longitude': float(attraction.longitude) if attraction.longitude else None,
                'category': attraction.category,
                'rating': float(attraction.rating) if attraction.rating else None,
                'image_url': attraction.image_url,
                'language': attraction.language,  # language field stores language
                'phone': attraction.phone_number,
                'opening_hours': attraction.opening_hours,
                'price': attraction.price,
                'tags': attraction.tags if attraction.tags else []
            }
            result.append(attraction_dict)
        
        return True, result
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi lấy địa điểm theo ngôn ngữ: {e}')
        return False, str(e)

def search_attractions_by_name_and_language(name: str, language: str = None) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Search attractions by name and optional language (fuzzy search)
    
    Args:
        name (str): Name to search for
        language (str, optional): Language filter (english, chinese, korean, japanese, vietnamese)
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        if not name or not isinstance(name, str):
            return True, []
            
        search_term = f"%{name.lower()}%"
        
        # Tìm kiếm với LIKE (case insensitive)
        query = Attraction.query.filter(
            db.func.lower(Attraction.name).like(search_term)
        )
        
        # Thêm filter ngôn ngữ nếu có
        if language:
            query = query.filter(Attraction.language == language.lower())
        
        attractions = query.all()
        
        result = []
        for attraction in attractions:
            attraction_dict = {
                'id': attraction.id,
                'name': attraction.name,
                'address': attraction.address,
                'description': attraction.description,
                'latitude': float(attraction.latitude) if attraction.latitude else None,
                'longitude': float(attraction.longitude) if attraction.longitude else None,
                'category': attraction.category,
                'rating': float(attraction.rating) if attraction.rating else None,
                'image_url': attraction.image_url,
                'language': attraction.language,  # language field stores language
                'phone': attraction.phone_number,
                'opening_hours': attraction.opening_hours,
                'price': attraction.price,
                'tags': attraction.tags if attraction.tags else []
            }
            result.append(attraction_dict)
        
        return True, result
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi tìm kiếm địa điểm: {e}')
        return False, str(e)

def search_attractions_by_name(query: str, language: str = None, limit: int = 20) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Search attractions by name with optional language filtering
    
    Args:
        query (str): Search query
        language (str, optional): Language filter (english, chinese, korean, japanese, vietnamese)
        limit (int): Maximum number of results to return
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        print(f"ℹ️ search_attractions_by_name with query: {query}, language: {language}, limit: {limit}")
        
        if not query or not isinstance(query, str):
            return True, []
            
        search_term = f"%{query.lower()}%"
        
        # Tìm kiếm với LIKE (case insensitive) trong name, address, description
        query_filter = db.or_(
            db.func.lower(Attraction.name).like(search_term),
            db.func.lower(Attraction.address).like(search_term),
            db.func.lower(Attraction.description).like(search_term)
        )
        
        # Tìm kiếm trong tags (JSON array)
        # Note: MySQL JSON_SEARCH function for searching in JSON arrays
        if hasattr(db.func, 'json_search'):
            tags_filter = db.func.json_search(Attraction.tags, 'one', search_term)
            query_filter = db.or_(query_filter, tags_filter.isnot(None))
        
        db_query = Attraction.query.filter(query_filter)
        
        # Thêm filter ngôn ngữ nếu có
        if language:
            db_query = db_query.filter(Attraction.language == language.lower())
        
        # Giới hạn số lượng kết quả
        attractions = db_query.limit(limit).all()
        
        result = []
        for attraction in attractions:
            attraction_dict = {
                'id': attraction.id,
                'name': attraction.name,
                'address': attraction.address,
                'description': attraction.description,
                'latitude': float(attraction.latitude) if attraction.latitude else None,
                'longitude': float(attraction.longitude) if attraction.longitude else None,
                'category': attraction.category,
                'rating': float(attraction.rating) if attraction.rating else None,
                'image_url': attraction.image_url,
                'language': attraction.language,  # language field stores language
                'phone': attraction.phone_number,
                'opening_hours': attraction.opening_hours,
                'price': attraction.price,
                'tags': attraction.tags if attraction.tags else []
            }
            result.append(attraction_dict)
        
        print(f"ℹ️ Total attractions found: {len(result)}")
        return True, result
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi tìm kiếm địa điểm: {e}')
        return False, str(e)

def get_attractions_by_category(category: str, language: str = None) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Get attractions by category with optional language filtering
    
    Args:
        category (str): Category to filter by
        language (str, optional): Language filter (english, chinese, korean, japanese, vietnamese)
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        if not category or not isinstance(category, str):
            return True, []
            
        query = Attraction.query.filter(
            db.func.lower(Attraction.category) == category.lower()
        )
        
        # Thêm filter ngôn ngữ nếu có
        if language:
            query = query.filter(Attraction.language == language.lower())
        
        attractions = query.all()
        
        result = []
        for attraction in attractions:
            attraction_dict = {
                'id': attraction.id,
                'name': attraction.name,
                'address': attraction.address,
                'description': attraction.description,
                'latitude': float(attraction.latitude) if attraction.latitude else None,
                'longitude': float(attraction.longitude) if attraction.longitude else None,
                'category': attraction.category,
                'rating': float(attraction.rating) if attraction.rating else None,
                'image_url': attraction.image_url,
                'language': attraction.language,  # language field stores language
                'phone': attraction.phone_number,
                'opening_hours': attraction.opening_hours,
                'price': attraction.price,
                'tags': attraction.tags if attraction.tags else []
            }
            result.append(attraction_dict)
        
        return True, result
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi lấy địa điểm theo category: {e}')
        return False, str(e)
