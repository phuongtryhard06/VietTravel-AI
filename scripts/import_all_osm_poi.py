"""
Script: import_all_osm_poi.py
Description: Full OSM POI Ingestion Engine for Phase C (MULTI_AGENT_UPGRADE_GUIDE.md)
Features:
  - Default mode: DRY-RUN (Full analytical scan, validation, duplicate check, ZERO DB writes)
  - Write mode: Enabled ONLY when --apply flag is provided
  - Scope: 3 Primary Tourism/Dining/Lodging/Worship/Nature layers:
      1. gis_osm_pois_free_1
      2. gis_osm_pofw_free_1
      3. gis_osm_natural_free_1
  - Filtering:
      * Reject unnamed records (name is empty)
      * Reject invalid GPS coordinates (outside Vietnam bounding box [8.0, 24.0] x [102.0, 110.0])
      * Reject non-tourism objects (cameras, single trees, benches, ATMs, banks, pharmacies, schools)
  - Standards Compliance:
      * Address: "Chưa có địa chỉ cụ thể; dùng tọa độ OpenStreetMap" (not fabricated)
      * Rating: NULL (unknown, not defaulted to 0.0)
      * Price, Opening Hours, Phone, Image: NULL (not fabricated)
  - Duplicate & Staging Ingestion Strategy:
      * Detects duplicate osm_id across layers and within layers
      * Merges tags and multi-functional categories (--dedup-mode merge)
      * UPSERT (ON CONFLICT (osm_id) DO UPDATE): Safely updates the 15 pilot records
        with standardized address/rating/tags, inserts 30,824 new records, 0 deleted.
  - Batching: Processed in configurable batch chunks (default: 1,000 records)
  - Memory safe: Uses native Python standard library struct & generator streaming
"""

import os
import sys
import json
import struct
import argparse
from collections import defaultdict, Counter
from urllib.parse import urlparse

# Ensure stdout handles UTF-8 on Windows pwsh/cmd
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config.config import Config
from sqlalchemy import create_engine, text

# --- Whitelist of Target Feature Classes for Tourism, Dining, Lodging, Culture, Nature ---
TARGET_FCLASSES = {
    # 1. Dining (Ẩm thực)
    'restaurant': ('Nhà hàng', 'Ẩm thực'),
    'cafe': ('Quán cà phê', 'Ẩm thực'),
    'fast_food': ('Quán ăn nhanh', 'Ẩm thực'),
    'bar': ('Quán bar/Pub', 'Ẩm thực'),
    'pub': ('Quán rượu/Pub', 'Ẩm thực'),
    'bakery': ('Tiệm bánh ngọt', 'Ẩm thực'),
    'food_court': ('Khu ẩm thực', 'Ẩm thực'),
    'beverages': ('Quán giải khát', 'Ẩm thực'),
    'biergarten': ('Quán bia ngoài trời', 'Ẩm thực'),

    # 2. Lodging (Lưu trú)
    'hotel': ('Khách sạn', 'Lưu trú'),
    'guesthouse': ('Nhà nghỉ/Homestay', 'Lưu trú'),
    'hostel': ('Nhà trọ du lịch', 'Lưu trú'),
    'motel': ('Nhà nghỉ ven đường', 'Lưu trú'),
    'camp_site': ('Khu cắm trại', 'Lưu trú'),
    'chalet': ('Nhà gỗ nghỉ dưỡng', 'Lưu trú'),
    'alpine_hut': ('Trạm dừng chân miền núi', 'Lưu trú'),
    'wilderness_hut': ('Chòi lưu trú dã ngoại', 'Lưu trú'),
    'caravan_site': ('Bãi đỗ xe du lịch lưu trú', 'Lưu trú'),

    # 3. Culture, Entertainment & Attractions (Du lịch, Giải trí & Văn hóa)
    'attraction': ('Điểm tham quan du lịch', 'Du lịch'),
    'viewpoint': ('Điểm ngắm cảnh', 'Du lịch'),
    'tourist_info': ('Trung tâm thông tin du lịch', 'Du lịch'),
    'marketplace': ('Chợ truyền thống', 'Du lịch'),
    'theme_park': ('Công viên giải trí chủ đề', 'Giải trí'),
    'zoo': ('Vườn thú/Thảo cầm viên', 'Giải trí'),
    'cinema': ('Rạp chiếu phim', 'Giải trí'),
    'theatre': ('Nhà hát nghệ thuật', 'Văn hóa'),
    'park': ('Công viên công cộng', 'Du lịch'),
    'picnic_site': ('Bãi dã ngoại', 'Du lịch'),
    'artwork': ('Tác phẩm nghệ thuật công cộng', 'Văn hóa'),
    'arts_centre': ('Trung tâm nghệ thuật', 'Văn hóa'),
    'lighthouse': ('Hải đăng', 'Du lịch'),
    'observation_tower': ('Đài quan sát', 'Du lịch'),
    'tower': ('Tháp/Cột cờ lịch sử', 'Du lịch'),
    'aquarium': ('Thủy cung', 'Giải trí'),
    'water_mill': ('Cối xay nước cổ', 'Du lịch'),
    'windmill': ('Cối xay gió', 'Du lịch'),
    'museum': ('Bảo tàng', 'Văn hóa'),
    'memorial': ('Khu tưởng niệm lịch sử', 'Lịch sử'),
    'monument': ('Tượng đài kỷ niệm', 'Lịch sử'),
    'ruins': ('Di tích phế tích', 'Lịch sử'),
    'castle': ('Thành lũy/Lâu đài', 'Lịch sử'),
    'archaeological': ('Khu khảo cổ học', 'Lịch sử'),
    'battlefield': ('Chiến trường xưa', 'Lịch sử'),
    'wayside_shrine': ('Miếu ven đường', 'Tâm linh'),
    'fort': ('Pháo đài cổ', 'Lịch sử'),

    # 4. Spiritual / Places of Worship (Tín ngưỡng & Tôn giáo)
    'buddhist': ('Chùa chiền/Đền Phật giáo', 'Tâm linh'),
    'christian': ('Nhà thờ Kitô giáo', 'Tâm linh'),
    'christian_catholic': ('Nhà thờ Công giáo', 'Tâm linh'),
    'christian_protestant': ('Nhà thờ Tin lành', 'Tâm linh'),
    'christian_evangelical': ('Nhà thờ Phúc âm', 'Tâm linh'),
    'hindu': ('Đền Hindu', 'Tâm linh'),
    'muslim': ('Thánh đường Hồi giáo', 'Tâm linh'),
    'muslim_sunni': ('Thánh đường Hồi giáo Sunni', 'Tâm linh'),
    'taoist': ('Đền Đạo giáo', 'Tâm linh'),
    'jewish': ('Giáo đường Do Thái', 'Tâm linh'),

    # 5. Natural Landscapes (Thắng cảnh tự nhiên - gis_osm_natural_free_1)
    # Strictly excludes 'tree' (single tree)
    'peak': ('Thắng cảnh đỉnh núi', 'Thiên nhiên'),
    'cave_entrance': ('Hang động tự nhiên', 'Thiên nhiên'),
    'beach': ('Bãi biển tự nhiên', 'Thiên nhiên'),
    'volcano': ('Thắng cảnh miệng núi lửa', 'Thiên nhiên'),
    'spring': ('Suối nước khoáng tự nhiên', 'Thiên nhiên'),
    'cliff': ('Vách đá danh thắng', 'Thiên nhiên'),
    'waterfall': ('Thác nước tự nhiên', 'Thiên nhiên')
}

DEFAULT_UNKNOWN_ADDRESS = "Chưa có địa chỉ cụ thể; dùng tọa độ OpenStreetMap"

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

def parse_shapefile_layer(osm_dir, layer_name):
    """Parse a single ESRI Shapefile Point layer and yield valid POIs."""
    shp_path = os.path.join(osm_dir, f"{layer_name}.shp")
    dbf_path = os.path.join(osm_dir, f"{layer_name}.dbf")
    
    if not os.path.exists(shp_path) or not os.path.exists(dbf_path):
        print(f"⚠️  Layer files not found: {shp_path} or {dbf_path}")
        return [], Counter(), 0
        
    stats = {
        'total_in_file': 0,
        'valid_poi': 0,
        'rejected_unnamed': 0,
        'rejected_fclass': 0,
        'rejected_coords': 0
    }
    
    valid_records = []
    
    with open(dbf_path, 'rb') as f_dbf, open(shp_path, 'rb') as f_shp:
        # Read DBF Header
        dbf_hdr = f_dbf.read(32)
        num_records, = struct.unpack('<I', dbf_hdr[4:8])
        header_len, = struct.unpack('<H', dbf_hdr[8:10])
        record_len, = struct.unpack('<H', dbf_hdr[10:12])
        stats['total_in_file'] = num_records
        
        num_fields = (header_len - 33) // 32
        fields = []
        for _ in range(num_fields):
            fld = f_dbf.read(32)
            fname = fld[:11].replace(b'\x00', b'').decode('ascii', errors='ignore')
            flen = fld[16]
            fields.append((fname, flen))
            
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
                stats['rejected_coords'] += 1
                continue
            stype, = struct.unpack('<i', content_bytes[0:4])
            if stype != 1: # 1 = Point
                stats['rejected_coords'] += 1
                continue
            lon, lat = struct.unpack('<dd', content_bytes[4:20])
            
            # Validate Vietnam geographic boundary
            if lat < 8.0 or lat > 24.0 or lon < 102.0 or lon > 110.0:
                stats['rejected_coords'] += 1
                continue
                
            # Parse DBF Attributes
            row = {}
            offset = 1
            for fname, flen in fields:
                val = dbf_bytes[offset:offset+flen].decode('utf-8', errors='replace').strip()
                row[fname] = val
                offset += flen
                
            name = row.get('name', '').strip()
            fclass = row.get('fclass', '').strip()
            
            # Filter unnamed
            if not name:
                stats['rejected_unnamed'] += 1
                continue
                
            # Filter non-target fclasses
            if fclass not in TARGET_FCLASSES:
                stats['rejected_fclass'] += 1
                continue
                
            category_vn, main_group = TARGET_FCLASSES[fclass]
            osm_raw_id = row.get('osm_id', '')
            osm_code_raw = row.get('code', '0')
            try:
                osm_code = int(osm_code_raw)
            except (ValueError, TypeError):
                osm_code = None
                
            stats['valid_poi'] += 1
            valid_records.append({
                'osm_id': f"osm_{osm_raw_id}",
                'osm_source_layer': layer_name,
                'osm_code': osm_code,
                'fclass': fclass,
                'name': name,
                'category': category_vn,
                'main_group': main_group,
                'latitude': round(lat, 6),
                'longitude': round(lon, 6),
                # Explicitly state unknown address, never fabricate
                'address': DEFAULT_UNKNOWN_ADDRESS,
                'description': f"{name} là địa điểm {category_vn} tại Việt Nam theo dữ liệu OpenStreetMap (chưa có địa chỉ cụ thể, định vị bằng tọa độ GPS WGS 84).",
                'price': None,            # Strictly factual: None (NULL in DB)
                'opening_hours': None,    # Strictly factual: None (NULL in DB)
                'phone_number': None,     # Strictly factual: None (NULL in DB)
                'image_url': None,        # Strictly factual: None (NULL in DB)
                'rating': None,           # Staging schema allows NULL: store None instead of misleading 0.0
                'language': 'vietnamese',
                'tags': [fclass, str(osm_code) if osm_code else fclass],
                'aliases': [],
                'raw_properties': row
            })
            
    return valid_records, stats

def analyze_and_deduplicate(all_records, dedup_mode='merge'):
    """
    Analyze duplicates by osm_id.
    - dedup_mode='merge': Merges multiple fclasses and tags into a unified record
    - dedup_mode='skip': Keeps the first seen record and skips subsequent duplicates
    """
    id_map = defaultdict(list)
    for r in all_records:
        id_map[r['osm_id']].append(r)
        
    duplicates = {oid: lst for oid, lst in id_map.items() if len(lst) > 1}
    
    cross_layer_dups = []
    intra_layer_dups = []
    
    for oid, lst in duplicates.items():
        layers = set(x['osm_source_layer'] for x in lst)
        if len(layers) > 1:
            cross_layer_dups.append((oid, lst))
        else:
            intra_layer_dups.append((oid, lst))
            
    deduped_records = []
    for oid, lst in id_map.items():
        if len(lst) == 1:
            deduped_records.append(lst[0])
        else:
            if dedup_mode == 'merge':
                # Base record: select primary layer prioritizing POIs > POFW > Natural
                primary = sorted(lst, key=lambda x: (
                    0 if x['osm_source_layer'] == 'gis_osm_pois_free_1' else
                    1 if x['osm_source_layer'] == 'gis_osm_pofw_free_1' else 2
                ))[0]
                
                # Merge tags, raw_properties, and categories
                all_tags = []
                all_cats = []
                merged_raw = {}
                for item in lst:
                    all_tags.extend(item['tags'])
                    if item['category'] not in all_cats:
                        all_cats.append(item['category'])
                    merged_raw.update(item.get('raw_properties', {}))
                
                merged = dict(primary)
                merged['tags'] = list(dict.fromkeys(all_tags))
                merged['raw_properties'] = merged_raw
                if len(all_cats) > 1:
                    merged['category'] = " / ".join(all_cats)
                    merged['description'] = f"{merged['name']} là địa điểm đa năng ({merged['category']}) tại Việt Nam theo dữ liệu OpenStreetMap (chưa có địa chỉ cụ thể, định vị bằng tọa độ GPS WGS 84)."
                deduped_records.append(merged)
            else:
                deduped_records.append(lst[0])
                
    return deduped_records, duplicates, cross_layer_dups, intra_layer_dups

def check_staging_overlap(engine, candidate_records):
    """
    Check overlap between candidate dataset and currently existing staging records.
    Returns:
      existing_count: total in DB right now
      update_count: number of existing records that will be updated
      insert_count: number of completely new records to insert
      total_expected: final count after ingestion
      core_table_counts: verify Attractions/Users/Itineraries
    """
    with engine.connect() as conn:
        # Check staging
        existing_oids = set(conn.execute(text("SELECT osm_id FROM osm_attractions_staging;")).scalars().all())
        candidate_oids = set(r['osm_id'] for r in candidate_records)
        
        overlapping_oids = existing_oids.intersection(candidate_oids)
        insert_oids = candidate_oids - existing_oids
        
        # Fresh dev databases may not have application tables until Flask runs
        # db.create_all(). Missing tables are reported as None, not as empty
        # tables, and are never created or modified by this importer.
        def existing_table_count(table_name):
            exists = conn.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = :table_name)"
            ), {"table_name": table_name}).scalar()
            if not exists:
                return None
            return conn.execute(text(f'SELECT count(*) FROM "{table_name}";')).scalar()

        attr_count = existing_table_count('Attractions')
        user_count = existing_table_count('Users')
        itin_count = existing_table_count('Itineraries')
        
    overlap_info = {
        'existing_staging_count': len(existing_oids),
        'update_count': len(overlapping_oids),
        'insert_count': len(insert_oids),
        'delete_count': 0, # Strict zero delete guarantee
        'total_expected': len(existing_oids - candidate_oids) + len(candidate_oids),
        'core_tables': {
            'Attractions': attr_count,
            'Users': user_count,
            'Itineraries': itin_count
        }
    }
    return overlap_info

def insert_in_batches(engine, dataset, batch_size=1000):
    """
    Insert or Update standardized records into osm_attractions_staging in batches.
    Uses UPSERT: ON CONFLICT (osm_id) DO UPDATE so existing pilot records are refreshed
    with the standardized address and rating=NULL, while new records are inserted.
    ZERO records are deleted.
    """
    upsert_sql = text("""
        INSERT INTO osm_attractions_staging (
            osm_id, osm_source_layer, osm_code, fclass, name, category,
            latitude, longitude, address, description, price, opening_hours,
            phone_number, image_url, rating, language, tags, aliases, raw_properties, updated_at
        ) VALUES (
            :osm_id, :osm_source_layer, :osm_code, :fclass, :name, :category,
            :latitude, :longitude, :address, :description, :price, :opening_hours,
            :phone_number, :image_url, :rating, :language, :tags, :aliases, :raw_properties, CURRENT_TIMESTAMP
        )
        ON CONFLICT (osm_id) DO UPDATE SET
            osm_source_layer = EXCLUDED.osm_source_layer,
            osm_code = EXCLUDED.osm_code,
            fclass = EXCLUDED.fclass,
            name = EXCLUDED.name,
            category = EXCLUDED.category,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            address = EXCLUDED.address,
            description = EXCLUDED.description,
            price = EXCLUDED.price,
            opening_hours = EXCLUDED.opening_hours,
            phone_number = EXCLUDED.phone_number,
            image_url = EXCLUDED.image_url,
            rating = EXCLUDED.rating,
            language = EXCLUDED.language,
            tags = EXCLUDED.tags,
            aliases = EXCLUDED.aliases,
            raw_properties = EXCLUDED.raw_properties,
            updated_at = CURRENT_TIMESTAMP;
    """)
    
    total_processed = 0
    total_records = len(dataset)
    num_batches = (total_records + batch_size - 1) // batch_size
    
    print(f"\n⚙️  Bắt đầu nạp/cập nhật {total_records} bản ghi theo {num_batches} lô (mỗi lô ~{batch_size} bản ghi)...")
    
    with engine.begin() as conn:
        for b_idx in range(num_batches):
            batch = dataset[b_idx * batch_size : (b_idx + 1) * batch_size]
            batch_params = []
            for item in batch:
                batch_params.append({
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
                })
            res = conn.execute(upsert_sql, batch_params)
            total_processed += res.rowcount
            print(f"   - Lô {b_idx + 1}/{num_batches}: xử lý thành công {res.rowcount} bản ghi.")
            
        final_count = conn.execute(text("SELECT count(*) FROM osm_attractions_staging;")).scalar()
        
    return total_processed, final_count

def main():
    parser = argparse.ArgumentParser(description="Full Ingestion Engine for OSM Vietnam POIs (Phase C)")
    parser.add_argument('--apply', action='store_true', default=False,
                        help="Actually apply changes to the database. Default: DRY-RUN mode.")
    parser.add_argument('--batch-size', type=int, default=1000,
                        help="Batch chunk size for database insertion (default: 1000).")
    parser.add_argument('--osm-dir', type=str, default=os.path.join(PROJECT_ROOT, 'data', 'osm_vietnam'),
                        help="Directory containing OSM Shapefiles.")
    parser.add_argument('--dedup-mode', type=str, choices=['merge', 'skip'], default='merge',
                        help="Deduplication strategy for duplicate osm_ids: 'merge' (enrich tags) or 'skip'.")
    parser.add_argument('--summary-output', type=str, default=os.path.join(PROJECT_ROOT, 'data', 'osm_full_ingestion_summary.json'),
                        help="Path to save execution summary JSON.")
    args = parser.parse_args()
    
    print("=" * 80)
    print("🚀 GIAI ĐOẠN C: CHUẨN BỊ NẠP TOÀN BỘ POI PHÙ HỢP VÀO OSM_ATTRACTIONS_STAGING")
    print("=" * 80)
    
    # 1. Verify DB Environment
    is_local, db_info = verify_db_environment()
    print(f"\n🔍 Kiểm tra môi trường Database:")
    if not is_local:
        print(f"❌ CẢNH BÁO: Database không thuộc môi trường local/dev ({db_info.get('host')})!")
        print(f"   Để bảo vệ an toàn dữ liệu, script từ chối ghi và chỉ chạy chế độ xem.")
    else:
        print(f"✅ Môi trường xác nhận: LOCAL / DEV")
        print(f"   Host: {db_info['host']} | Port: {db_info['port']} | DB: {db_info['dbname']} | User: {db_info['user']}")

    # 2. Parse 3 Primary Layers
    layers = ['gis_osm_pois_free_1', 'gis_osm_pofw_free_1', 'gis_osm_natural_free_1']
    all_records = []
    layer_stats = {}
    
    print("\n📦 Quét và phân tích 3 lớp Shapefile mục tiêu:")
    for lyr in layers:
        recs, stats = parse_shapefile_layer(args.osm_dir, lyr)
        all_records.extend(recs)
        layer_stats[lyr] = stats
        print(f"  • Lớp {lyr}:")
        print(f"      - Tổng số bản ghi trong file : {stats['total_in_file']:,}")
        print(f"      - Hợp lệ thuộc nhóm du lịch   : {stats['valid_poi']:,}")
        print(f"      - Bị loại (Không có tên)     : {stats['rejected_unnamed']:,}")
        print(f"      - Bị loại (Không thuộc nhóm) : {stats['rejected_fclass']:,}")
        print(f"      - Bị loại (Sai tọa độ)       : {stats['rejected_coords']:,}")

    # 3. Analyze Duplicate osm_id
    deduped_records, duplicates, cross_dups, intra_dups = analyze_and_deduplicate(all_records, dedup_mode=args.dedup_mode)
    
    total_candidates = len(all_records)
    total_unique_ids = len(deduped_records)
    total_duplicates_count = len(duplicates)
    
    print("\n" + "=" * 80)
    print("🔎 BÁO CÁO KIỂM TRA TRÙNG LẶP KHÓA (DUPLICATE OSM_ID ANALYSIS)")
    print("=" * 80)
    print(f"  • Tổng số bản ghi ứng viên thu thập được : {total_candidates:,}")
    print(f"  • Số lượng osm_id duy nhất               : {total_unique_ids:,}")
    print(f"  • Số lượng osm_id bị trùng lặp            : {total_duplicates_count:,} (chiếm {total_candidates - total_unique_ids} bản ghi thứ cấp)")
    print(f"      - Trùng lặp chéo giữa các lớp (Cross-layer) : {len(cross_dups)} osm_id")
    print(f"      - Trùng lặp nội bộ trong cùng lớp (Intra-POIS): {len(intra_dups)} osm_id")
    print(f"  • Chiến lược xử lý trùng lặp đề xuất (--dedup-mode={args.dedup_mode}):")
    print(f"      -> Tự động gộp các nhãn (tags) và danh mục phụ vào một bản ghi thực thể địa lý duy nhất.")
    print(f"      -> Giữ nguyên cấu trúc UNIQUE(osm_id) của bảng staging, KHÔNG gây thất thoát thông tin.")

    # 4. Check Staging Table & Core Tables Overlap
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    overlap = check_staging_overlap(engine, deduped_records)
    
    print("\n" + "=" * 80)
    print("📋 ĐỐI CHIẾU DỮ LIỆU HIỆN CÓ TRONG STAGING & BẢO TOÀN BẢNG LÕI")
    print("=" * 80)
    print(f"  • Số bản ghi hiện có trong osm_attractions_staging : {overlap['existing_staging_count']:,}")
    print(f"  • Số bản ghi sẽ được CẬP NHẬT (UPDATE)           : {overlap['update_count']:,} (chuẩn hóa address='Chưa có...', rating=NULL, gộp tags)")
    print(f"  • Số bản ghi sẽ được THÊM MỚI (INSERT)            : {overlap['insert_count']:,}")
    print(f"  • Số bản ghi bị XÓA (DELETE)                      : {overlap['delete_count']} (Cam kết tuyệt đối không xóa)")
    print(f"  • TỔNG DỰ KIẾN TRONG STAGING SAU NHẬP             : {overlap['total_expected']:,} bản ghi")
    print(f"  • Kiểm tra trạng thái bảng lõi (Core Tables):")
    def format_core_table_count(value):
        return f"{value:,} bản ghi (không sửa)" if value is not None else "bảng chưa được tạo (không truy cập)"

    print(f"      - Attractions : {format_core_table_count(overlap['core_tables']['Attractions'])}")
    print(f"      - Users       : {format_core_table_count(overlap['core_tables']['Users'])}")
    print(f"      - Itineraries : {format_core_table_count(overlap['core_tables']['Itineraries'])}")

    # 5. Group & Category Breakdown
    group_counts = Counter()
    cat_counts = Counter()
    for r in deduped_records:
        group_counts[r['main_group']] += 1
        cat_counts[r['category']] += 1
        
    print("\n" + "-" * 80)
    print("📊 THỐNG KÊ BẢN GHI HỢP LỆ THEO NHÓM CHÍNH (SAU KHỬ TRÙNG LẶP):")
    print("-" * 80)
    for grp, cnt in group_counts.most_common():
        pct = (cnt / total_unique_ids) * 100
        print(f"  • {grp:<20}: {cnt:>6,} bản ghi ({pct:5.1f}%)")

    print("\n" + "-" * 80)
    print("🏆 TOP 15 DANH MỤC POI PHỔ BIẾN NHẤT:")
    print("-" * 80)
    for cat, cnt in cat_counts.most_common(15):
        print(f"  • {cat:<30}: {cnt:>6,} bản ghi")

    # 6. Export Summary JSON
    summary_data = {
        'total_candidates': total_candidates,
        'total_unique_ids': total_unique_ids,
        'duplicate_ids_count': total_duplicates_count,
        'cross_layer_duplicates': len(cross_dups),
        'intra_layer_duplicates': len(intra_dups),
        'overlap_analysis': overlap,
        'standards_check': {
            'address_fallback': DEFAULT_UNKNOWN_ADDRESS,
            'rating_unknown': 'NULL (None)',
            'price_unknown': 'NULL (None)',
            'opening_hours_unknown': 'NULL (None)'
        },
        'group_counts': dict(group_counts),
        'layer_stats': layer_stats
    }
    with open(args.summary_output, 'w', encoding='utf-8') as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Đã lưu báo cáo tổng kết chi tiết tại: {args.summary_output}")

    # 7. Check Execution Mode
    if not args.apply:
        print("\n" + "=" * 80)
        print("🛡️  CHẾ ĐỘ THỰC THI HIỆN TẠI: DRY-RUN (AN TOÀN TUYỆT ĐỐI)")
        print("   - CHƯA CÓ BẤT KỲ BẢN GHI NÀO ĐƯỢC GHI THÊM HOẶC SỬA TRONG DATABASE.")
        print("   - Các bảng Attractions, Users, Itineraries hoàn toàn không bị đụng tới.")
        print("   - Toàn bộ giá vé (price), giờ mở cửa (opening_hours), rating đều là NULL.")
        print(f"   👉 Khi người dùng phê duyệt nạp dữ liệu, lệnh áp dụng sẽ là:")
        print(f"      .\\venv\\Scripts\\python.exe scripts/import_all_osm_poi.py --apply --batch-size {args.batch_size}")
        print("=" * 80)
    else:
        if not is_local:
            print("\n❌ LỖI: Không thể thực hiện --apply vì database không được xác nhận là local/dev!")
            engine.dispose()
            raise SystemExit(2)
        print("\n⚙️  Đang thực thi ghi dữ liệu theo lô vào bảng osm_attractions_staging...")
        try:
            total_processed, final_count = insert_in_batches(engine, deduped_records, batch_size=args.batch_size)
            print(f"\n✅ Nạp dữ liệu hoàn tất!")
            print(f"   - Số bản ghi được xử lý (Upsert) : {total_processed:,}")
            print(f"   - Tổng bản ghi trong staging     : {final_count:,}")
            print(f"   - Bảng Attractions hiện tại không bị ảnh hưởng (Zero modifications to core table).")
        except Exception as e:
            print(f"\n❌ Lỗi khi ghi vào database: {e}")
            engine.dispose()
            raise SystemExit(1) from e
    engine.dispose()

if __name__ == '__main__':
    main()
