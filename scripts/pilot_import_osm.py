"""
Script: pilot_import_osm.py
Description: Pilot POI ingestion script for Phase B (MULTI_AGENT_UPGRADE_GUIDE.md)
Features:
  - Default: DRY-RUN mode (safe inspection & JSON export without DB write)
  - Write mode: Enabled ONLY when --apply flag is provided
  - Target: osm_attractions_staging table with UNIQUE osm_id
  - Environment: Verifies database is local/dev before writing
  - Data integrity: price, opening_hours, phone, image set strictly to None (no fabrication)
"""

import os
import sys
import json
import struct
import argparse
from urllib.parse import urlparse

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config.config import Config
from sqlalchemy import create_engine, text

# Mapping from OSM fclass to Vietnamese standardized categories
CATEGORY_MAPPING = {
    # Dining
    'restaurant': 'Nhà hàng',
    'cafe': 'Quán cà phê',
    'fast_food': 'Quán ăn nhanh',
    'food_court': 'Khu ẩm thực',
    'bar': 'Quán bar/Pub',
    'pub': 'Quán bar/Pub',
    'bakery': 'Tiệm bánh ngọt',
    
    # Accommodation
    'hotel': 'Khách sạn',
    'guesthouse': 'Nhà nghỉ/Homestay',
    'hostel': 'Nhà trọ du lịch',
    'motel': 'Nhà nghỉ ven đường',
    
    # Culture & Tourism
    'attraction': 'Điểm tham quan',
    'museum': 'Bảo tàng',
    'viewpoint': 'Điểm ngắm cảnh',
    'memorial': 'Khu tưởng niệm',
    'monument': 'Tượng đài kỷ niệm',
    'theme_park': 'Công viên giải trí',
    'park': 'Công viên công cộng',
    'marketplace': 'Chợ truyền thống',
    'tourist_info': 'Thông tin du lịch',
    'ruins': 'Di tích lịch sử',
    'castle': 'Thành lũy cổ',
    
    # Places of Worship (gis_osm_pofw_free_1)
    'buddhist': 'Chùa chiền/Đền Phật giáo',
    'christian': 'Nhà thờ Công giáo/Tin lành',
    'hindu': 'Đền Hindu',
    'muslim': 'Thánh đường Hồi giáo',
    'jewish': 'Giáo đường Do Thái',
    
    # Natural Landmarks (gis_osm_natural_free_1)
    'peak': 'Thắng cảnh đỉnh núi',
    'cave_entrance': 'Hang động tự nhiên',
    'spring': 'Suối nước khoáng',
    'beach': 'Bãi biển tự nhiên',
    'waterfall': 'Thác nước tự nhiên'
}

def verify_db_environment():
    """Verify that the configured database is a local/development environment."""
    db_url = Config.SQLALCHEMY_DATABASE_URI or ''
    if not db_url:
        return False, "DATABASE_POSTGRESQL_URL is not set"
    
    parsed = urlparse(db_url)
    host = parsed.hostname or Config.DB_HOST or ''
    port = parsed.port or Config.DB_PORT or 5432
    dbname = parsed.path.lstrip('/') if parsed.path else (Config.DB_NAME or '')
    user = parsed.username or Config.DB_USER or ''
    
    allowed_hosts = ['localhost', '127.0.0.1', '::1', '0.0.0.0', 'db', 'postgres']
    is_local = host.lower() in allowed_hosts
    
    safe_info = {
        'host': host,
        'port': port,
        'dbname': dbname,
        'user': user,
        'is_local': is_local
    }
    return is_local, safe_info

def read_point_features_from_layer(osm_dir, layer_name, max_count, target_fclasses=None):
    """
    Directly parse Point ESRI Shapefile (.shp) and attribute table (.dbf)
    using Python standard library struct (zero external GIS dependencies).
    """
    shp_path = os.path.join(osm_dir, f"{layer_name}.shp")
    dbf_path = os.path.join(osm_dir, f"{layer_name}.dbf")
    
    if not os.path.exists(shp_path) or not os.path.exists(dbf_path):
        print(f"⚠️  Layer files not found: {shp_path} or {dbf_path}")
        return []
    
    features = []
    
    with open(dbf_path, 'rb') as f_dbf, open(shp_path, 'rb') as f_shp:
        # Read DBF Header
        dbf_hdr = f_dbf.read(32)
        num_records, = struct.unpack('<I', dbf_hdr[4:8])
        header_len, = struct.unpack('<H', dbf_hdr[8:10])
        record_len, = struct.unpack('<H', dbf_hdr[10:12])
        
        num_fields = (header_len - 33) // 32
        fields = []
        for _ in range(num_fields):
            fld = f_dbf.read(32)
            fname = fld[:11].replace(b'\x00', b'').decode('ascii', errors='ignore')
            ftyp = chr(fld[11])
            flen = fld[16]
            fields.append((fname, ftyp, flen))
            
        f_dbf.seek(header_len)
        f_shp.seek(100) # Skip 100-byte SHP header
        
        for _ in range(num_records):
            # Read SHP Record Header
            rec_hdr = f_shp.read(8)
            if len(rec_hdr) < 8:
                break
            rec_num, content_len_words = struct.unpack('>ii', rec_hdr)
            content_bytes = f_shp.read(content_len_words * 2)
            
            # Read DBF Record
            dbf_bytes = f_dbf.read(record_len)
            if not dbf_bytes or dbf_bytes[0:1] == b'*':
                continue
            
            # Geometry check
            if len(content_bytes) < 20:
                continue
            stype, = struct.unpack('<i', content_bytes[0:4])
            if stype != 1: # 1 = Point
                continue
            lon, lat = struct.unpack('<dd', content_bytes[4:20])
            
            # Validate Vietnam geographic boundary
            if lat < 8.0 or lat > 24.0 or lon < 102.0 or lon > 110.0:
                continue
            
            # Parse DBF Attributes
            row = {}
            offset = 1
            for fname, ftyp, flen in fields:
                val = dbf_bytes[offset:offset+flen].decode('utf-8', errors='replace').strip()
                row[fname] = val
                offset += flen
            
            name = row.get('name', '').strip()
            fclass = row.get('fclass', '').strip()
            
            if not name:
                continue
            if target_fclasses and fclass not in target_fclasses:
                continue
            
            category_vn = CATEGORY_MAPPING.get(fclass, fclass.title())
            osm_raw_id = row.get('osm_id', '')
            osm_code_raw = row.get('code', '0')
            try:
                osm_code = int(osm_code_raw)
            except (ValueError, TypeError):
                osm_code = None
            
            poi = {
                'osm_id': f"osm_{osm_raw_id}",
                'osm_source_layer': layer_name,
                'osm_code': osm_code,
                'fclass': fclass,
                'name': name,
                'category': category_vn,
                'latitude': round(lat, 6),
                'longitude': round(lon, 6),
                'address': 'Chưa có địa chỉ cụ thể; dùng tọa độ OpenStreetMap',
                'description': f"{name} là địa điểm {category_vn} tại Việt Nam theo dữ liệu OpenStreetMap (chưa có địa chỉ cụ thể, định vị bằng tọa độ GPS WGS 84).",
                'price': None,            # Factual: unknown in OSM, strictly not fabricated
                'opening_hours': None,    # Factual: unknown in OSM, strictly not fabricated
                'phone_number': None,     # Factual: unknown in OSM, strictly not fabricated
                'image_url': None,        # Factual: unknown in OSM, strictly not fabricated
                'rating': None,
                'language': 'vietnamese',
                'tags': [fclass, str(osm_code) if osm_code else fclass],
                'aliases': [],
                'raw_properties': row
            }
            features.append(poi)
            
            if len(features) >= max_count:
                break
                
    return features

def collect_pilot_dataset(osm_dir, total_limit=15):
    """
    Collect a balanced, diverse 15-POI sample across 3 primary OSM layers:
      1. gis_osm_pois_free_1 (Dining, Lodging, Culture/Attraction) -> ~8 POIs
      2. gis_osm_pofw_free_1 (Worship/Buddhist/Christian)         -> ~4 POIs
      3. gis_osm_natural_free_1 (Peaks, Springs, Caves)          -> ~3 POIs
    """
    poi_count = max(6, int(total_limit * 0.55))
    pofw_count = max(3, int(total_limit * 0.25))
    nat_count = max(2, total_limit - poi_count - pofw_count)
    
    print(f"\n📦 Trích xuất dữ liệu mẫu từ OpenStreetMap:")
    print(f"  - Lớp gis_osm_pois_free_1: mục tiêu {poi_count} bản ghi")
    print(f"  - Lớp gis_osm_pofw_free_1: mục tiêu {pofw_count} bản ghi")
    print(f"  - Lớp gis_osm_natural_free_1: mục tiêu {nat_count} bản ghi")
    
    pois = read_point_features_from_layer(
        osm_dir, 'gis_osm_pois_free_1', poi_count,
        target_fclasses=['restaurant', 'cafe', 'hotel', 'museum', 'attraction', 'viewpoint']
    )
    pofw = read_point_features_from_layer(
        osm_dir, 'gis_osm_pofw_free_1', pofw_count,
        target_fclasses=['buddhist', 'christian']
    )
    naturals = read_point_features_from_layer(
        osm_dir, 'gis_osm_natural_free_1', nat_count,
        target_fclasses=['peak', 'cave_entrance', 'spring', 'beach']
    )
    
    combined = pois + pofw + naturals
    return combined[:total_limit]

def ensure_staging_table_exists(engine):
    """Ensure the osm_attractions_staging table exists using the migration script."""
    migration_path = os.path.join(PROJECT_ROOT, 'migrations', 'create_osm_staging_table.sql')
    if os.path.exists(migration_path):
        with open(migration_path, 'r', encoding='utf-8') as f:
            sql_script = f.read()
        with engine.begin() as conn:
            conn.execute(text(sql_script))
    else:
        # Fallback DDL if migration file is missing
        ddl = """
        CREATE TABLE IF NOT EXISTS osm_attractions_staging (
            id SERIAL PRIMARY KEY,
            osm_id VARCHAR(50) NOT NULL UNIQUE,
            osm_source_layer VARCHAR(100) NOT NULL,
            osm_code INTEGER,
            fclass VARCHAR(100) NOT NULL,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(100) NOT NULL,
            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            address VARCHAR(500) NOT NULL,
            description TEXT,
            price DOUBLE PRECISION DEFAULT NULL,
            opening_hours VARCHAR(200) DEFAULT NULL,
            phone_number VARCHAR(50) DEFAULT NULL,
            image_url VARCHAR(500) DEFAULT NULL,
            rating DOUBLE PRECISION DEFAULT NULL,
            language VARCHAR(50) DEFAULT 'vietnamese',
            tags JSONB DEFAULT '[]'::jsonb,
            aliases JSONB DEFAULT '[]'::jsonb,
            raw_properties JSONB DEFAULT '{}'::jsonb,
            imported_to_attraction BOOLEAN DEFAULT FALSE,
            attraction_id VARCHAR(50) DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        with engine.begin() as conn:
            conn.execute(text(ddl))

def apply_to_database(engine, dataset):
    """Insert dataset into osm_attractions_staging table idempotently."""
    ensure_staging_table_exists(engine)
    
    insert_sql = text("""
        INSERT INTO osm_attractions_staging (
            osm_id, osm_source_layer, osm_code, fclass, name, category,
            latitude, longitude, address, description, price, opening_hours,
            phone_number, image_url, rating, language, tags, aliases, raw_properties
        ) VALUES (
            :osm_id, :osm_source_layer, :osm_code, :fclass, :name, :category,
            :latitude, :longitude, :address, :description, :price, :opening_hours,
            :phone_number, :image_url, :rating, :language, :tags, :aliases, :raw_properties
        )
        ON CONFLICT (osm_id) DO NOTHING;
    """)
    
    inserted_count = 0
    with engine.begin() as conn:
        for item in dataset:
            params = {
                'osm_id': item['osm_id'],
                'osm_source_layer': item['osm_source_layer'],
                'osm_code': item['osm_code'],
                'fclass': item['fclass'],
                'name': item['name'],
                'category': item['category'],
                'latitude': item['latitude'],
                'longitude': item['longitude'],
                'address': item['address'],
                'description': item['description'],
                'price': item['price'],
                'opening_hours': item['opening_hours'],
                'phone_number': item['phone_number'],
                'image_url': item['image_url'],
                'rating': item['rating'],
                'language': item['language'],
                'tags': json.dumps(item['tags']),
                'aliases': json.dumps(item['aliases']),
                'raw_properties': json.dumps(item['raw_properties'], ensure_ascii=False)
            }
            result = conn.execute(insert_sql, params)
            inserted_count += result.rowcount
            
        total_in_staging = conn.execute(text("SELECT count(*) FROM osm_attractions_staging;")).scalar()
        
    return inserted_count, total_in_staging

def main():
    parser = argparse.ArgumentParser(description="Pilot POI Ingestion from OSM Vietnam Shapefiles (Phase B)")
    parser.add_argument('--apply', action='store_true', default=False,
                        help="Actually apply changes to the staging table. Default: DRY-RUN mode.")
    parser.add_argument('--limit', type=int, default=15,
                        help="Number of pilot POIs to extract (10-20, default: 15).")
    parser.add_argument('--osm-dir', type=str, default=os.path.join(PROJECT_ROOT, 'data', 'osm_vietnam'),
                        help="Directory containing OSM Shapefiles.")
    parser.add_argument('--output', type=str, default=os.path.join(PROJECT_ROOT, 'data', 'osm_vietnam_pilot_sample.json'),
                        help="Path to save preview JSON file.")
    args = parser.parse_args()
    
    limit = max(10, min(20, args.limit))
    
    print("=" * 70)
    print("🚀 GIAI ĐOẠN B: NHẬP THỬ NGHIỆM POI TỪ OPENSTREETMAP (PILOT INGESTION)")
    print("=" * 70)
    
    # Step 1: Verify environment
    is_local, db_info = verify_db_environment()
    print(f"\n🔍 Kiểm tra môi trường Database:")
    if not is_local:
        print(f"❌ CẢNH BÁO: Database không thuộc môi trường local/dev ({db_info.get('host')})!")
        print(f"   Để bảo vệ an toàn dữ liệu, script từ chối ghi và chỉ chạy chế độ xem.")
    else:
        print(f"✅ Môi trường xác nhận: LOCAL / DEV")
        print(f"   Host: {db_info['host']} | Port: {db_info['port']} | DB: {db_info['dbname']} | User: {db_info['user']}")
        
    # Step 2: Extract pilot dataset
    dataset = collect_pilot_dataset(args.osm_dir, total_limit=limit)
    if not dataset:
        print("❌ Không trích xuất được dữ liệu mẫu nào từ shapefiles.")
        return
    
    print(f"\n📋 Đã trích xuất thành công {len(dataset)} bản ghi POI hợp lệ (WGS 84):")
    print("-" * 70)
    print(f"{'STT':<4} | {'OSM ID':<16} | {'Phân loại':<24} | {'Tên địa điểm':<26} | {'Vĩ độ / Kinh độ'}")
    print("-" * 70)
    for idx, item in enumerate(dataset, 1):
        coords = f"{item['latitude']:.4f}, {item['longitude']:.4f}"
        name_trunc = item['name'][:24] + ".." if len(item['name']) > 24 else item['name']
        cat_trunc = item['category'][:22] + ".." if len(item['category']) > 22 else item['category']
        print(f"{idx:<4} | {item['osm_id']:<16} | {cat_trunc:<24} | {name_trunc:<26} | {coords}")
    print("-" * 70)
    
    # Save preview JSON
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Đã lưu dữ liệu mẫu chi tiết tại: {args.output}")
    
    # Step 3: Check execution mode
    if not args.apply:
        print("\n" + "=" * 70)
        print("🛡️  CHẾ ĐỘ MẶC ĐỊNH: DRY-RUN (AN TOÀN)")
        print("   - CHƯA CÓ DỮ LIỆU NÀO ĐƯỢC GHI VÀO POSTGRESQL.")
        print("   - Toàn bộ thông tin giá vé (price), giờ mở cửa (opening_hours) đều được giữ là NULL.")
        print("   - Bảng Attractions chính hoàn toàn không bị thay đổi.")
        print("   👉 Để nạp 15 bản ghi này vào bảng staging, hãy chạy lệnh:")
        print("      .\\venv\\Scripts\\python.exe scripts/pilot_import_osm.py --apply")
        print("=" * 70)
    else:
        if not is_local:
            print("\n❌ LỖI: Không thể thực hiện --apply vì database không được xác nhận là local/dev!")
            return
            
        print("\n⚙️  Đang thực thi ghi dữ liệu vào bảng osm_attractions_staging...")
        try:
            engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
            inserted, total = apply_to_database(engine, dataset)
            print(f"✅ Ghi thành công!")
            print(f"   - Số bản ghi mới được nạp: {inserted}")
            print(f"   - Tổng số bản ghi trong osm_attractions_staging: {total}")
            print(f"   - Bảng Attractions hiện tại không bị ảnh hưởng (Zero modifications to core table).")
        except Exception as e:
            print(f"❌ Lỗi khi ghi vào database: {e}")

if __name__ == '__main__':
    main()
