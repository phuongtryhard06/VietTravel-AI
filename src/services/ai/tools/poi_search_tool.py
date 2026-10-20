"""
Module POI Search Tool (Giai đoạn D)
Cung cấp công cụ tìm kiếm địa điểm du lịch, ẩm thực, lưu trú, văn hóa và thắng cảnh
từ bảng dữ liệu đệm PostgreSQL `osm_attractions_staging`.

Tuân thủ nghiêm ngặt các nguyên tắc:
1. PostgreSQL staging là nguồn dữ liệu chính.
2. Tìm kiếm theo bán kính: lọc chính xác theo công thức Haversine, sắp xếp gần nhất trước (distance_km ASC), sau đó mới áp dụng LIMIT. Tuyệt đối không bỏ sót kết quả gần hơn vì thứ tự id.
3. Không bịa đặt thông tin: giá vé, giờ mở cửa, rating, số điện thoại, hình ảnh giữ null khi chưa có.
4. Địa chỉ giữ nguyên trạng từ staging ("Chưa có địa chỉ cụ thể; dùng tọa độ OpenStreetMap" hoặc địa chỉ thật nếu có).
5. Quản lý kết nối: Trong Flask app context, dùng kết nối SQLAlchemy hiện có (db.session); khi chạy standalone thì tạo engine và đóng/dispose() đúng cách.
6. ChromaDB chỉ đọc (read-only): Xác định đúng đường dẫn tương đối từ gốc repo; nếu collection hoặc embedding model có sẵn không tìm thấy, trả cảnh báo và vẫn trả kết quả PostgreSQL; tuyệt đối không tải model mới.
"""

import os
import re
import math
import logging
from typing import Dict, List, Optional, Any, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, DatabaseError, SQLAlchemyError

# Nhận diện ngữ cảnh ứng dụng Flask
try:
    from flask import has_app_context
except ImportError:
    def has_app_context() -> bool:
        return False

from src.config.config import Config
from src.models.base import db

logger = logging.getLogger(__name__)

# Bán kính trái đất trung bình tính theo km (Chuẩn WGS 84)
EARTH_RADIUS_KM = 6371.0


def get_repo_root() -> str:
    """
    Xác định chính xác đường dẫn tuyệt đối đến thư mục gốc của repository
    thông qua vị trí các tệp định danh (main.py, src/).
    """
    current = os.path.abspath(__file__)
    for _ in range(10):
        parent = os.path.dirname(current)
        if os.path.exists(os.path.join(parent, 'main.py')) and os.path.exists(os.path.join(parent, 'src')):
            return parent
        if parent == current:
            break
        current = parent
    # Fallback chuẩn: 4 cấp thư mục từ src/services/ai/tools/
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))


def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Tính khoảng cách đường tròn lớn (Great-circle distance) giữa 2 tọa độ GPS bằng công thức Haversine (km).
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_KM * c


def _execute_sql(sql_statement: str, params: Dict[str, Any]) -> List[Any]:
    """
    Thực thi truy vấn SQL an toàn theo ngữ cảnh:
    - Trong Flask app context: sử dụng kết nối db.session hiện có của Flask-SQLAlchemy.
    - Chạy standalone: tạo engine tạm thời, kết nối và luôn gọi engine.dispose() trong finally.
    """
    if has_app_context():
        # Dùng kết nối SQLAlchemy hiện có trong Flask app
        cursor = db.session.execute(text(sql_statement), params)
        return cursor.fetchall()
    else:
        # Chạy standalone ngoài Flask context
        database_uri = Config.SQLALCHEMY_DATABASE_URI
        if not database_uri:
            raise OperationalError(
                statement=sql_statement,
                params=params,
                orig=Exception("Biến môi trường DATABASE_POSTGRESQL_URL chưa được cấu hình trong file .env")
            )
        engine = create_engine(database_uri, **Config.SQLALCHEMY_ENGINE_OPTIONS)
        try:
            with engine.connect() as conn:
                cursor = conn.execute(text(sql_statement), params)
                return cursor.fetchall()
        finally:
            engine.dispose()


def search_staging_pois(
    query: Optional[str] = None,
    category: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    radius_km: Optional[float] = None,
    limit: int = 20,
    include_chromadb: bool = False
) -> Dict[str, Any]:
    """
    Truy vấn POI từ bảng `osm_attractions_staging` trong PostgreSQL.
    
    Args:
        query (str, optional): Từ khóa tìm kiếm theo tên hoặc mô tả địa điểm.
        category (str, optional): Danh mục tiếng Việt hoặc fclass OSM (vd: 'Nhà hàng', 'Khách sạn', 'restaurant').
        latitude (float, optional): Vĩ độ tâm tìm kiếm.
        longitude (float, optional): Kinh độ tâm tìm kiếm.
        radius_km (float, optional): Bán kính tìm kiếm (km) quanh tâm tọa độ (mặc định 5.0 nếu có lat/lon).
        limit (int, optional): Số lượng kết quả tối đa cần lấy (mặc định 20, tối đa 100).
        include_chromadb (bool, optional): Có kích hoạt truy vấn ngữ nghĩa bổ trợ từ ChromaDB có sẵn hay không.
        
    Returns:
        Dict[str, Any]: Cấu trúc JSON chuẩn hóa chứa trạng thái, danh sách POI thực tế và metadata.
    """
    limit = max(1, min(limit or 20, 100))
    search_params = {
        "query": query.strip() if query else None,
        "category": category.strip() if category else None,
        "location": {
            "latitude": float(latitude) if latitude is not None else None,
            "longitude": float(longitude) if longitude is not None else None,
            "radius_km": float(radius_km) if radius_km is not None else (5.0 if latitude is not None and longitude is not None else None)
        },
        "limit": limit,
        "include_chromadb": include_chromadb
    }

    # BƯỚC 1: TRUY VẤN POSTGRESQL STAGING (NGUỒN DỮ LIỆU CHÍNH)
    try:
        results = _query_postgresql_staging(
            query=search_params["query"],
            category=search_params["category"],
            latitude=search_params["location"]["latitude"],
            longitude=search_params["location"]["longitude"],
            radius_km=search_params["location"]["radius_km"],
            limit=limit
        )
        
        response_data = {
            "success": True,
            "total": len(results),
            "source": "postgresql_staging",
            "search_params": search_params,
            "data": results
        }

    except (OperationalError, DatabaseError) as db_err:
        logger.error("Không truy vấn được PostgreSQL staging: %s", db_err)
        return {
            "success": False,
            "error_code": "database_error",
            "message": "Không thể kết nối cơ sở dữ liệu địa điểm. Hãy kiểm tra PostgreSQL và bảng osm_attractions_staging.",
            "source": "postgresql_staging",
            "search_params": search_params,
            "data": []
        }
    except Exception as general_err:
        logger.error("Lỗi khi truy vấn PostgreSQL staging: %s", general_err)
        return {
            "success": False,
            "error_code": "database_error",
            "message": "Không thể đọc dữ liệu địa điểm từ PostgreSQL staging.",
            "source": "postgresql_staging",
            "search_params": search_params,
            "data": []
        }

    # BƯỚC 2: TÙY CHỌN TRUY VẤN CHROMADB CHỈ ĐỌC (NẾU ĐƯỢC YÊU CẦU)
    if include_chromadb and search_params["query"]:
        chroma_items, chroma_warning = _query_chromadb_readonly(search_params["query"], limit=min(5, limit))
        if chroma_warning:
            response_data["chromadb_warning"] = chroma_warning
        if chroma_items:
            response_data["chromadb_supplement"] = {
                "collection": "diadiem_collection",
                "count": len(chroma_items),
                "items": chroma_items
            }
            response_data["source"] = "hybrid_postgresql_and_chromadb"

    return response_data


def _query_postgresql_staging(
    query: Optional[str],
    category: Optional[str],
    latitude: Optional[float],
    longitude: Optional[float],
    radius_km: Optional[float],
    limit: int
) -> List[Dict[str, Any]]:
    """
    Truy vấn PostgreSQL staging đảm bảo tính đúng đắn không gian:
    - Khi có tọa độ & bán kính: Áp dụng bounding box lọc sơ bộ bằng index, sau đó tính
      khoảng cách Haversine chính xác trong SQL, lọc distance_km <= radius_km,
      sắp xếp distance_km ASC (gần nhất trước), và CHỈ áp dụng LIMIT ở cuối cùng.
      Đảm bảo 100% không bỏ sót điểm gần hơn vì thứ tự id.
    """
    conditions = ["1=1"]
    params: Dict[str, Any] = {"limit": limit}

    # 1. Lọc theo tên hoặc từ khóa tìm kiếm (Hỗ trợ từ ghép 'và', 'hoặc', dấu phẩy)
    if query:
        cleaned_query = query.lower()
        sub_terms = [t.strip() for t in re.split(r'[,;/]|\s+và\s+|\s+hoặc\s+', cleaned_query) if t.strip()]
        if len(sub_terms) > 1:
            term_clauses = []
            for idx, term in enumerate(sub_terms):
                p_name = f"query_term_{idx}"
                term_clauses.append(f"(LOWER(name) LIKE :{p_name} OR LOWER(category) LIKE :{p_name} OR LOWER(fclass) LIKE :{p_name})")
                params[p_name] = f"%{term}%"
            conditions.append(f"({' OR '.join(term_clauses)})")
        else:
            conditions.append("(LOWER(name) LIKE :query_like OR LOWER(category) LIKE :query_like OR LOWER(fclass) LIKE :query_like)")
            params["query_like"] = f"%{query.lower()}%"

    # 2. Lọc theo danh mục hoặc fclass
    if category:
        conditions.append("(LOWER(category) LIKE :cat_like OR LOWER(fclass) LIKE :cat_like)")
        params["cat_like"] = f"%{category.lower()}%"

    has_geo = (latitude is not None and longitude is not None and radius_km is not None and radius_km > 0)

    if has_geo:
        # Bounding box lọc sơ bộ bằng index (1 độ vĩ ~ 111.0 km; 1 độ kinh ~ 111.0 * cos(lat) km)
        delta_lat = radius_km / 111.0
        delta_lon = radius_km / (111.0 * max(0.1, math.cos(math.radians(latitude))))
        
        conditions.append("latitude BETWEEN :min_lat AND :max_lat")
        conditions.append("longitude BETWEEN :min_lon AND :max_lon")
        params["min_lat"] = latitude - delta_lat
        params["max_lat"] = latitude + delta_lat
        params["min_lon"] = longitude - delta_lon
        params["max_lon"] = longitude + delta_lon

        params["center_lat"] = latitude
        params["center_lon"] = longitude
        params["radius_km"] = radius_km

        # Công thức Haversine tính khoảng cách đường tròn lớn theo km trên PostgreSQL
        haversine_sql = """(6371.0 * 2.0 * ASIN(SQRT(
            POWER(SIN(RADIANS(latitude - :center_lat) / 2.0), 2) +
            COS(RADIANS(:center_lat)) * COS(RADIANS(latitude)) *
            POWER(SIN(RADIANS(longitude - :center_lon) / 2.0), 2)
        )))"""

        # Lọc chính xác theo bán kính thực tế (loại bỏ các điểm góc bounding box)
        conditions.append(f"{haversine_sql} <= :radius_km")

        where_clause = " AND ".join(conditions)

        # Sắp xếp theo khoảng cách tăng dần (gần nhất trước), sau đó mới giới hạn bằng LIMIT
        sql_statement = f"""
            SELECT 
                osm_id,
                osm_source_layer,
                osm_code,
                fclass,
                name,
                category,
                latitude,
                longitude,
                address,
                description,
                price,
                opening_hours,
                phone_number,
                image_url,
                rating,
                tags,
                {haversine_sql} AS distance_km
            FROM osm_attractions_staging
            WHERE {where_clause}
            ORDER BY distance_km ASC
            LIMIT :limit;
        """
    else:
        where_clause = " AND ".join(conditions)
        sql_statement = f"""
            SELECT 
                osm_id,
                osm_source_layer,
                osm_code,
                fclass,
                name,
                category,
                latitude,
                longitude,
                address,
                description,
                price,
                opening_hours,
                phone_number,
                image_url,
                rating,
                tags,
                NULL AS distance_km
            FROM osm_attractions_staging
            WHERE {where_clause}
            ORDER BY id ASC
            LIMIT :limit;
        """

    rows = _execute_sql(sql_statement, params)

    # Fallback: Nếu lọc theo từ khóa quá chặt mà không ra kết quả nhưng có tọa độ không gian,
    # nới lỏng để lấy các điểm tham quan/ẩm thực uy tín nhất trong bán kính
    if not rows and has_geo:
        fallback_sql = f"""
            SELECT 
                osm_id, osm_source_layer, osm_code, fclass, name, category,
                latitude, longitude, address, description, price, opening_hours,
                phone_number, image_url, rating, tags,
                {haversine_sql} AS distance_km
            FROM osm_attractions_staging
            WHERE latitude BETWEEN :min_lat AND :max_lat
              AND longitude BETWEEN :min_lon AND :max_lon
              AND {haversine_sql} <= :radius_km
              AND name IS NOT NULL AND TRIM(name) != ''
            ORDER BY distance_km ASC
            LIMIT :limit;
        """
        rows = _execute_sql(fallback_sql, params)

    poi_list = []
    for row in rows:
        mapping = row._mapping
        poi_lat = float(mapping["latitude"]) if mapping["latitude"] is not None else None
        poi_lon = float(mapping["longitude"]) if mapping["longitude"] is not None else None
        dist = mapping["distance_km"]
        distance_km = float(dist) if dist is not None else None

        # Chuẩn hóa tags
        tags = mapping.get("tags")
        if not isinstance(tags, list):
            tags = [tags] if tags else []

        poi_item = {
            "osm_id": mapping["osm_id"],
            "name": mapping["name"],
            "category": mapping["category"],
            "fclass": mapping["fclass"],
            "coordinates": {
                "latitude": poi_lat,
                "longitude": poi_lon
            },
            "description": mapping["description"],
            "address": mapping["address"],  # Giữ nguyên giá trị thật từ staging
            "tags": tags,
            # Các trường chưa có trong dữ liệu OSM giữ nguyên giá trị null
            "price": None,
            "opening_hours": None,
            "phone_number": None,
            "image_url": None,
            "rating": None,
            "distance_km": round(distance_km, 3) if distance_km is not None else None,
            "osm_source": {
                "layer": mapping["osm_source_layer"],
                "osm_id": mapping["osm_id"],
                "code": mapping["osm_code"]
            }
        }
        poi_list.append(poi_item)

    return poi_list


def _query_chromadb_readonly(query_text: str, limit: int = 5) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Truy vấn chỉ đọc (read-only) từ ChromaDB collection `diadiem_collection` có sẵn.
    
    Quy tắc an toàn:
    1. Lấy đúng đường dẫn thư mục gốc của repository.
    2. Nếu collection hoặc embedding model có sẵn không tìm thấy, trả cảnh báo và vẫn trả kết quả PostgreSQL.
    3. Tuyệt đối KHÔNG tải model mới từ internet (sử dụng offline mode).
    4. Tuyệt đối KHÔNG ghi/thay đổi dữ liệu ChromaDB.
    
    Returns:
        Tuple[List[Dict], Optional[str]]: (danh sách kết quả, cảnh báo nếu có)
    """
    # ChromaDB has been retired in favor of PostgreSQL POI Engine
    return [], "ChromaDB has been retired in favor of PostgreSQL staging search."

    try:
        client = chromadb.PersistentClient(path=chroma_path)
        existing_collections = [c.name for c in client.list_collections()]
    except Exception as e:
        warning_msg = f"Không thể kết nối ChromaDB client tại '{chroma_path}': {str(e)}"
        logger.warning(warning_msg)
        return [], warning_msg

    # 2. Kiểm tra collection diadiem_collection có sẵn
    if "diadiem_collection" not in existing_collections:
        warning_msg = "Collection 'diadiem_collection' không tồn tại trong ChromaDB; bỏ qua truy vấn vector."
        logger.warning(warning_msg)
        return [], warning_msg

    # 3. Kiểm tra embedding model trong cache cục bộ (ngăn chặn tải model mới)
    model_name = "paraphrase-multilingual-MiniLM-L12-v2"
    torch_cache = os.path.expanduser('~/.cache/torch/sentence_transformers')
    model_cache_path = os.path.join(torch_cache, f"sentence-transformers_{model_name}")
    
    hf_cache = os.path.expanduser('~/.cache/huggingface/hub')
    is_model_cached = os.path.isdir(model_cache_path) or any(
        model_name in item for item in (os.listdir(hf_cache) if os.path.isdir(hf_cache) else [])
    )

    if not is_model_cached:
        warning_msg = f"Embedding model '{model_name}' chưa có sẵn trong cache cục bộ; bỏ qua truy vấn ChromaDB (không tải model mới)."
        logger.warning(warning_msg)
        return [], warning_msg

    # Đặt biến môi trường offline để đảm bảo SentenceTransformer không gọi mạng tải model
    prev_trans_offline = os.environ.get('TRANSFORMERS_OFFLINE')
    prev_hf_offline = os.environ.get('HF_HUB_OFFLINE')
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_OFFLINE'] = '1'

    try:
        sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_name
        )
        collection = client.get_collection(
            name="diadiem_collection",
            embedding_function=sentence_transformer_ef
        )

        count = collection.count()
        if count == 0:
            return [], "Collection 'diadiem_collection' trống (0 bản ghi)."

        query_results = collection.query(
            query_texts=[query_text],
            n_results=min(limit, count),
            include=["metadatas", "documents", "distances"]
        )

        items = []
        if query_results and "ids" in query_results and len(query_results["ids"]) > 0:
            for i in range(len(query_results["ids"][0])):
                items.append({
                    "id": query_results["ids"][0][i],
                    "metadata": query_results["metadatas"][0][i] if "metadatas" in query_results else {},
                    "document": query_results["documents"][0][i] if "documents" in query_results else "",
                    "distance": query_results["distances"][0][i] if "distances" in query_results else None
                })
        return items, None

    except Exception as query_err:
        warning_msg = f"Lỗi khi thực hiện truy vấn đọc ChromaDB: {str(query_err)}; bỏ qua truy vấn vector."
        logger.warning(warning_msg)
        return [], warning_msg
    finally:
        # Khôi phục trạng thái biến môi trường ban đầu
        if prev_trans_offline is None:
            os.environ.pop('TRANSFORMERS_OFFLINE', None)
        else:
            os.environ['TRANSFORMERS_OFFLINE'] = prev_trans_offline
        if prev_hf_offline is None:
            os.environ.pop('HF_HUB_OFFLINE', None)
        else:
            os.environ['HF_HUB_OFFLINE'] = prev_hf_offline


class StagingPOISearchTool:
    """
    Lớp đóng gói Tool tìm kiếm POI tương thích với hệ thống Multi-Agent (LangChain / LangGraph).
    """
    name: str = "search_staging_pois"
    description: str = (
        "Truy vấn địa điểm tham quan, ẩm thực, khách sạn, tâm linh và thắng cảnh tự nhiên từ dữ liệu "
        "OpenStreetMap staging. Nhận tên/từ khóa, danh mục, hoặc tọa độ vĩ độ/kinh độ kèm bán kính (km). "
        "Trả về danh sách POI chuẩn JSON với tọa độ thực tế và thông tin chi tiết."
    )

    def __call__(self, **kwargs) -> Dict[str, Any]:
        return search_staging_pois(**kwargs)

    def run(self, **kwargs) -> Dict[str, Any]:
        return search_staging_pois(**kwargs)


# Hỗ trợ chạy thử nghiệm nhanh qua dòng lệnh
if __name__ == "__main__":
    import json
    import argparse

    parser = argparse.ArgumentParser(description="Kiểm tra công cụ tìm kiếm POI từ PostgreSQL Staging")
    parser.add_argument("--query", "-q", type=str, help="Từ khóa hoặc tên POI")
    parser.add_argument("--category", "-c", type=str, help="Danh mục hoặc fclass")
    parser.add_argument("--lat", type=float, help="Vĩ độ tâm")
    parser.add_argument("--lon", type=float, help="Kinh độ tâm")
    parser.add_argument("--radius", "-r", type=float, default=5.0, help="Bán kính (km)")
    parser.add_argument("--limit", "-l", type=int, default=5, help="Giới hạn số kết quả")
    parser.add_argument("--chroma", action="store_true", help="Bổ trợ tìm kiếm ChromaDB")
    args = parser.parse_args()

    result = search_staging_pois(
        query=args.query,
        category=args.category,
        latitude=args.lat,
        longitude=args.lon,
        radius_km=args.radius if (args.lat and args.lon) else None,
        limit=args.limit,
        include_chromadb=args.chroma
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
