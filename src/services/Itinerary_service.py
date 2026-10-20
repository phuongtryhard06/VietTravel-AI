import re
import logging
from src.models.itinerary import Itinerary
from src.models.itinerary_item import ItineraryItem
from src.models.attraction import Attraction
from src.models.user import User
from src.models.osm_attraction_staging import OsmAttractionStaging
from src.models.base import db
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy import text, null
from src.services.notification_service import create_itinerary_reminder_notification
from src.services.ai.tools.validator_tool import validate_itinerary_constraints

logger = logging.getLogger(__name__)

DRAFT_ID_REGEX = re.compile(r"^draft_[0-9a-fA-F]{12}$")

def create_itinerary_with_items(user_id: int, selected_date: str, 
                               itinerary_items: List[Dict[str, Any]], 
                               title: Optional[str] = None,
                               notes: Optional[str] = None) -> tuple[bool, Dict[str, Any] | str]:
    """
    Create a new itinerary with multiple items
    
    Args:
        user_id (int): ID of the user
        selected_date (str): Selected date in ISO format (YYYY-MM-DD)
        itinerary_items (List[Dict]): List of itinerary items
        title (str, optional): Title for the itinerary
        notes (str, optional): General notes for the itinerary
        
    Returns:
        tuple: (success: bool, result: Dict or str)
    """
    try:
        print(f"ℹ️ Creating itinerary: user_id={user_id}, selected_date={selected_date}, items_count={len(itinerary_items)}")
        
        # Validate user exists
        user = User.query.get(user_id)
        if not user:
            return False, f"User with ID {user_id} not found"
        
        # Parse selected date
        try:
            parsed_date = datetime.fromisoformat(selected_date.split('T')[0]).date()
        except ValueError:
            return False, "Invalid selected_date format. Use ISO format (YYYY-MM-DD)"
        
        # Check if date is in the future
        if parsed_date < date.today():
            return False, "Selected date cannot be in the past"
        
        # Create new itinerary
        itinerary = Itinerary(
            user_id=user_id,
            selected_date=parsed_date,
            title=title,
            notes=notes
        )
        
        db.session.add(itinerary)
        db.session.flush()  # Get the itinerary ID
        
        # Add itinerary items
        for index, item_data in enumerate(itinerary_items):
            # Validate attraction exists
            attraction = Attraction.query.get(item_data['attraction_id'])
            if not attraction:
                db.session.rollback()
                return False, f"Attraction with ID {item_data['attraction_id']} not found"
            
            # Parse visit time
            try:
                visit_time_str = item_data['visit_time'].replace('Z', '+00:00')
                parsed_visit_time = datetime.fromisoformat(visit_time_str)
            except ValueError:
                db.session.rollback()
                return False, f"Invalid visit_time format for item {index + 1}. Use ISO format (YYYY-MM-DD HH:MM:SS)"
            
            # Check if visit time is on the selected date
            if parsed_visit_time.date() != parsed_date:
                db.session.rollback()
                return False, f"Visit time for item {index + 1} must be on the selected date"
            
            # Create itinerary item
            itinerary_item = ItineraryItem(
                itinerary_id=itinerary.id,
                attraction_id=item_data['attraction_id'],
                visit_time=parsed_visit_time,
                estimated_duration=item_data.get('estimated_duration'),
                notes=item_data.get('notes'),
                order_index=index
            )
            
            db.session.add(itinerary_item)
        
        db.session.commit()
        
        # Create reminder notification (1 day before the itinerary)
        try:
            reminder_success, reminder_message = create_itinerary_reminder_notification(itinerary.id)
            if reminder_success:
                print(f"✅ Scheduled reminder notification for itinerary {itinerary.id}")
            else:
                print(f"⚠️ Warning: Could not schedule reminder for itinerary {itinerary.id}: {reminder_message}")
        except Exception as e:
            print(f"⚠️ Warning: Error creating reminder notification: {e}")
        
        # Return the created itinerary with items
        result = itinerary.to_dict()
        print(f"ℹ️ Successfully created itinerary with ID: {itinerary.id}")
        
        return True, result
        
    except Exception as e:
        db.session.rollback()
        print(f'ℹ️ Lỗi khi tạo lịch trình: {e}')
        return False, str(e)

def get_user_itineraries(user_id: int) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Get all itineraries for a user (excluding soft deleted ones)
    
    Args:
        user_id (int): ID of the user
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        print(f"ℹ️ Getting itineraries for user_id: {user_id}")
        
        # Validate user exists
        user = User.query.get(user_id)
        if not user:
            return False, f"User with ID {user_id} not found"
        
        # Get itineraries ordered by selected date (excluding soft deleted ones)
        itineraries = Itinerary.query.filter_by(user_id=user_id, is_deleted=False)\
            .order_by(Itinerary.selected_date.asc()).all()
        
        result = [itinerary.to_dict() for itinerary in itineraries]
        print(f"ℹ️ Found {len(result)} itineraries for user {user_id}")
        
        return True, result
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi lấy lịch trình: {e}')
        return False, str(e)

def get_itinerary_by_id(itinerary_id: int, user_id: int) -> tuple[bool, Dict[str, Any] | str]:
    """
    Get a specific itinerary by ID
    
    Args:
        itinerary_id (int): ID of the itinerary
        user_id (int): ID of the user (for authorization)
        
    Returns:
        tuple: (success: bool, result: Dict or str)
    """
    try:
        print(f"ℹ️ Getting itinerary: itinerary_id={itinerary_id}, user_id={user_id}")
        
        # Find the itinerary
        itinerary = Itinerary.query.get(itinerary_id)
        if not itinerary:
            return False, f"Itinerary with ID {itinerary_id} not found"
        
        # Check if user owns this itinerary
        if itinerary.user_id != user_id:
            return False, "You are not authorized to view this itinerary"
        
        result = itinerary.to_dict()
        print(f"ℹ️ Successfully retrieved itinerary with ID: {itinerary_id}")
        
        return True, result
        
    except Exception as e:
        print(f'ℹ️ Lỗi khi lấy lịch trình: {e}')
        return False, str(e)

def delete_itinerary(itinerary_id: int, user_id: int) -> tuple[bool, str]:
    """
    Delete (soft delete) an entire itinerary by setting isDelete=True
    Args:
        itinerary_id (int): ID of the itinerary
        user_id (int): ID of the user (for authorization)
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        print(f"ℹ️ Soft deleting itinerary: itinerary_id={itinerary_id}, user_id={user_id}")
        # Find the itinerary
        itinerary = Itinerary.query.get(itinerary_id)
        if not itinerary:
            return False, f"Itinerary with ID {itinerary_id} not found"
        # Check if user owns this itinerary
        if itinerary.user_id != user_id:
            return False, "You are not authorized to delete this itinerary"
        # Soft delete: set isDelete to True
        itinerary.is_deleted = True
        db.session.commit()
        print(f"ℹ️ Successfully soft deleted itinerary with ID: {itinerary_id}")
        return True, f"Successfully deleted itinerary with ID: {itinerary_id} (soft delete)"
    except Exception as e:
        db.session.rollback()
        print(f'ℹ️ Lỗi khi xóa mềm lịch trình: {e}')
        return False, str(e)

def update_itinerary_item(item_id: int, user_id: int, 
                         visit_time: Optional[str] = None,
                         estimated_duration: Optional[int] = None, 
                         notes: Optional[str] = None) -> tuple[bool, Dict[str, Any] | str]:
    """
    Update a specific itinerary item
    
    Args:
        item_id (int): ID of the itinerary item
        user_id (int): ID of the user (for authorization)
        visit_time (str, optional): New visit time in ISO format
        estimated_duration (int, optional): New estimated duration in minutes
        notes (str, optional): New notes
        
    Returns:
        tuple: (success: bool, result: Dict or str)
    """
    try:
        print(f"ℹ️ Updating itinerary item: item_id={item_id}, user_id={user_id}")
        
        # Find the itinerary item
        itinerary_item = ItineraryItem.query.get(item_id)
        if not itinerary_item:
            return False, f"Itinerary item with ID {item_id} not found"
        
        # Check if user owns this item's itinerary
        if itinerary_item.itinerary.user_id != user_id:
            return False, "You are not authorized to update this itinerary item"
        
        # Update fields if provided
        if visit_time is not None:
            try:
                visit_time_str = visit_time.replace('Z', '+00:00')
                parsed_visit_time = datetime.fromisoformat(visit_time_str)
                if parsed_visit_time.date() != itinerary_item.itinerary.selected_date:
                    return False, "Visit time must be on the itinerary's selected date"
                itinerary_item.visit_time = parsed_visit_time
            except ValueError:
                return False, "Invalid visit_time format. Use ISO format (YYYY-MM-DD HH:MM:SS)"
        
        if estimated_duration is not None:
            itinerary_item.estimated_duration = estimated_duration
            
        if notes is not None:
            itinerary_item.notes = notes
        
        # Save changes
        db.session.commit()
        
        result = itinerary_item.to_dict()
        print(f"ℹ️ Successfully updated itinerary item with ID: {item_id}")
        
        return True, result
        
    except Exception as e:
        db.session.rollback()
        print(f'ℹ️ Lỗi khi cập nhật lịch trình: {e}')
        return False, str(e)


# =====================================================================
# GIAI ĐOẠN H2-B: DỊCH VỤ XÁC NHẬN & LƯU LỊCH TRÌNH (TRANSACTION AN TOÀN)
# =====================================================================

def confirm_draft_itinerary(
    user_id: int,
    draft_data: Dict[str, Any]
) -> Tuple[bool, Dict[str, Any] | str, bool, int]:
    """
    Xác nhận và lưu bản lịch trình nháp vào cơ sở dữ liệu chính thức (Giai đoạn H2-B).
    
    Quy tắc nghiệp vụ & Kiến trúc:
    1. Nhận user_id từ danh tính đã xác thực qua JWT, tuyệt đối không tin client.
    2. Đọc lại từng POI từ bảng `osm_attractions_staging` theo `osm_id`; không tin name, coordinates, address từ client.
    3. Chạy lại validator trên dữ liệu đọc từ staging. Từ chối nếu POI không tồn tại, trùng lặp, hoặc không qua ràng buộc cứng.
    4. Chỉ thăng hạng POI thật sự được chọn sang bảng `Attractions` với ID ổn định (Attraction.id = osm_id, len <= 50). Không cắt cụt chuỗi.
    5. Đảm bảo toàn bộ thao tác thăng hạng POI, chèn Itinerary và chèn ItineraryItems nằm trọn trong 1 Database Transaction duy nhất.
    6. Chống gửi lặp cùng draft_id khi có request đồng thời bằng PostgreSQL Advisory Lock (pg_advisory_xact_lock) kết hợp thẻ meta draft_id.
    7. Trả lại itinerary đã lưu và cờ is_duplicate nếu request được gửi lại. Mọi nhánh trả về chưa commit đều rollback để giải phóng lock.
    8. Gửi notification/email hoàn toàn ngoài transaction (sau commit an toàn).
    
    Returns:
        tuple: (success: bool, result: Dict | str, is_duplicate: bool, status_code: int)
    """
    # 1. Kiểm tra user tồn tại và hợp lệ (trước khi khởi tạo giao dịch)
    user = User.query.get(user_id)
    if not user:
        return False, f"User with ID {user_id} not found", False, 404
    if not user.is_verified:
        return False, "User email is not verified", False, 403

    draft_id = draft_data.get("draft_id")
    if not draft_id or not isinstance(draft_id, str):
        return False, "Missing or invalid draft_id in request payload", False, 400

    if not DRAFT_ID_REGEX.match(draft_id):
        return False, "Invalid draft_id format. Expected 'draft_' followed by 12 hex characters.", False, 400

    selected_date_raw = draft_data.get("selected_date")
    if not selected_date_raw:
        return False, "Missing selected_date in request payload", False, 400

    try:
        parsed_date = datetime.fromisoformat(str(selected_date_raw).split("T")[0]).date()
    except (ValueError, TypeError):
        return False, f"Invalid selected_date format: '{selected_date_raw}'. Expected YYYY-MM-DD", False, 400

    if parsed_date < date.today():
        return False, "Selected date cannot be in the past", False, 400

    items_input = draft_data.get("items")
    if not isinstance(items_input, list) or len(items_input) == 0:
        return False, "items must be a non-empty list", False, 400

    # 2. Khóa giao dịch đồng thời (PostgreSQL Transaction Advisory Lock)
    # Tự động giải phóng khi transaction kết thúc (commit hoặc rollback)
    lock_key = f"confirm_{user_id}_{draft_id}"
    try:
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key}
        )
    except Exception as lock_err:
        db.session.rollback()
        logger.error(f"Failed to acquire advisory lock for key {lock_key}: {lock_err}", exc_info=True)
        return False, "Đã xảy ra lỗi hệ thống khi thiết lập khóa giao dịch.", False, 500

    committed = False
    try:
        # 3. Kiểm tra chống lưu trùng (Idempotency Check)
        # Escape các ký tự _ và % để không cho phép wildcard trong khóa idempotency
        escaped_draft_id = draft_id.replace("_", "\\_").replace("%", "\\%")
        existing_itinerary = Itinerary.query.filter(
            Itinerary.user_id == user_id,
            Itinerary.selected_date == parsed_date,
            Itinerary.notes.like(f"%[draft_id: {escaped_draft_id}]%", escape="\\"),
            Itinerary.is_deleted == False
        ).first()

        if existing_itinerary:
            db.session.rollback()  # Giải phóng ngay advisory lock khi request trùng lặp
            return True, existing_itinerary.to_dict(), True, 200

        # 4. Thu thập và kiểm tra danh sách osm_id
        osm_ids = []
        for idx, item in enumerate(items_input):
            if not isinstance(item, dict):
                db.session.rollback()
                return False, f"Item at index {idx} must be an object", False, 400
            osm_id = item.get("osm_id")
            if not osm_id:
                db.session.rollback()
                return False, f"Missing osm_id in item at index {idx}", False, 400
            osm_ids.append(osm_id)

        # Đọc lại toàn bộ POI từ bảng staging theo osm_id (KHÔNG TIN dữ liệu từ client)
        staging_records = OsmAttractionStaging.query.filter(OsmAttractionStaging.osm_id.in_(osm_ids)).all()
        staging_lookup = {p.osm_id: p for p in staging_records}

        missing_osm_ids = [oid for oid in osm_ids if oid not in staging_lookup]
        if missing_osm_ids:
            db.session.rollback()
            return False, f"Detected invalid or non-existent POI osm_id(s) from staging: {missing_osm_ids}", False, 404

        # 5. Tái cấu trúc lịch trình với dữ liệu trung thực 100% từ staging
        reconstructed_items = []
        for idx, item in enumerate(items_input):
            osm_id = item["osm_id"]
            st_poi = staging_lookup[osm_id]

            start_time = item.get("start_time")
            end_time = item.get("end_time")
            if not start_time or not end_time:
                db.session.rollback()
                return False, f"Item '{osm_id}' missing start_time or end_time", False, 400

            order_idx = item.get("order_index", idx + 1)
            duration = item.get("estimated_duration")

            reconstructed_items.append({
                "order_index": order_idx,
                "osm_id": osm_id,
                "name": st_poi.name,
                "category": st_poi.category,
                "fclass": st_poi.fclass,
                "start_time": start_time,
                "end_time": end_time,
                "estimated_duration": duration,
                "coordinates": {"latitude": float(st_poi.latitude), "longitude": float(st_poi.longitude)},
                "address": st_poi.address,
                "opening_hours": st_poi.opening_hours,
                "price": st_poi.price,
                "rating": float(st_poi.rating) if st_poi.rating is not None else None,
                "notes": item.get("notes")
            })

        # 6. Chạy lại validator trên dữ liệu đọc từ staging
        draft_to_validate = {
            "draft_id": draft_id,
            "selected_date": parsed_date.isoformat(),
            "items": reconstructed_items
        }
        candidate_pois = [p.to_dict() for p in staging_lookup.values()]

        validation_result = validate_itinerary_constraints(
            draft_plan=draft_to_validate,
            candidate_pois=candidate_pois,
            route_legs=None
        )

        if not validation_result.get("valid"):
            db.session.rollback()
            err_list = validation_result.get("errors", [])
            return False, f"Validation constraint violated: {'; '.join(err_list)}", False, 422

        # 7. Trích xuất và kiểm tra chặt chẽ các item đã chuẩn hóa từ validation_result
        validated_draft = validation_result.get("draft")
        if not isinstance(validated_draft, dict):
            db.session.rollback()
            return False, "Validation output draft structure is invalid", False, 422

        validated_items = validated_draft.get("items")
        if not isinstance(validated_items, list) or len(validated_items) == 0:
            db.session.rollback()
            return False, "Validated itinerary items are empty or invalid", False, 422

        if len(validated_items) != len(items_input):
            db.session.rollback()
            return False, f"Mismatch in item count after validation: expected {len(items_input)}, got {len(validated_items)}", False, 422

        # Kiểm tra tính toàn vẹn và đảm bảo thời lượng/thứ tự khớp chuẩn
        for idx, v_item in enumerate(validated_items):
            if not isinstance(v_item, dict):
                db.session.rollback()
                return False, f"Validated item at index {idx} must be an object", False, 422

            v_osm_id = v_item.get("osm_id")
            if not v_osm_id or v_osm_id not in staging_lookup:
                db.session.rollback()
                return False, f"Validated item at index {idx} missing valid osm_id from staging", False, 422

            v_start = v_item.get("start_time")
            v_end = v_item.get("end_time")
            if not v_start or not v_end:
                db.session.rollback()
                return False, f"Validated item '{v_osm_id}' missing start_time or end_time", False, 422

            try:
                sh, sm = map(int, str(v_start).split(":"))
                eh, em = map(int, str(v_end).split(":"))
                expected_dur = (eh * 60 + em) - (sh * 60 + sm)
            except Exception:
                db.session.rollback()
                return False, f"Validated item '{v_osm_id}' has invalid time format", False, 422

            if expected_dur <= 0:
                db.session.rollback()
                return False, f"Validated item '{v_osm_id}' end_time must be strictly after start_time", False, 422

            # Tuyệt đối không lưu estimated_duration chưa kiểm định từ client:
            # Thời lượng lưu phải khớp chính xác end_time - start_time
            v_item["estimated_duration"] = expected_dur

            # Thứ tự lưu phải khớp chính xác thứ tự đã kiểm định (1-based index)
            v_item["order_index"] = idx + 1

        # 8. Kiểm tra độ dài cột nghiêm ngặt (TUYỆT ĐỐI KHÔNG CẮT CỤT) và thăng hạng POI
        for osm_id, st_poi in staging_lookup.items():
            target_attr_id = str(osm_id)
            if len(target_attr_id) > 50:
                db.session.rollback()
                return False, f"POI osm_id '{target_attr_id}' exceeds maximum length for Attraction.id (50 characters)", False, 400

            if st_poi.name and len(st_poi.name) > 255:
                db.session.rollback()
                return False, f"POI '{osm_id}' name exceeds maximum length (255 characters)", False, 400

            if st_poi.address and len(st_poi.address) > 500:
                db.session.rollback()
                return False, f"POI '{osm_id}' address exceeds maximum length (500 characters)", False, 400

            if st_poi.image_url and len(st_poi.image_url) > 500:
                db.session.rollback()
                return False, f"POI '{osm_id}' image_url exceeds maximum length (500 characters)", False, 400

            if st_poi.category and len(st_poi.category) > 100:
                db.session.rollback()
                return False, f"POI '{osm_id}' category exceeds maximum length (100 characters)", False, 400

            if st_poi.opening_hours and len(st_poi.opening_hours) > 200:
                db.session.rollback()
                return False, f"POI '{osm_id}' opening_hours exceeds maximum length (200 characters)", False, 400

            if st_poi.phone_number and len(st_poi.phone_number) > 20:
                db.session.rollback()
                return False, f"POI '{osm_id}' phone_number exceeds maximum length (20 characters)", False, 400

            lang_val = st_poi.language or 'vietnamese'
            if len(lang_val) > 500:
                db.session.rollback()
                return False, f"POI '{osm_id}' language exceeds maximum length (500 characters)", False, 400

            existing_attr = Attraction.query.get(target_attr_id)
            if not existing_attr:
                # Giữ rating không có dữ liệu là SQL NULL (dùng null()),
                # không gán 0.0 và không để default=0.0 của model biến NULL thành 0.
                rating_val = float(st_poi.rating) if st_poi.rating is not None else null()

                new_attr = Attraction(
                    id=target_attr_id,
                    name=st_poi.name,
                    address=st_poi.address,
                    description=st_poi.description,
                    image_url=st_poi.image_url,
                    rating=rating_val,
                    latitude=float(st_poi.latitude) if st_poi.latitude is not None else None,
                    longitude=float(st_poi.longitude) if st_poi.longitude is not None else None,
                    category=st_poi.category,
                    tags=st_poi.tags or [],
                    price=float(st_poi.price) if st_poi.price is not None else None,
                    opening_hours=st_poi.opening_hours,
                    phone_number=st_poi.phone_number,
                    language=lang_val,
                    aliases=st_poi.aliases or []
                )
                db.session.add(new_attr)

            # Cập nhật trạng thái bảng staging
            st_poi.imported_to_attraction = True
            st_poi.attraction_id = target_attr_id

        db.session.flush()

        # 9. Tạo bản ghi Itinerary
        clean_notes = draft_data.get("notes") or ""
        meta_notes = f"[draft_id: {draft_id}] {clean_notes}".strip()
        itinerary = Itinerary(
            user_id=user_id,
            selected_date=parsed_date,
            title=draft_data.get("title") or f"Lịch trình ngày {parsed_date.isoformat()}",
            notes=meta_notes
        )
        db.session.add(itinerary)
        db.session.flush()

        # 10. Tạo các bản ghi ItineraryItem từ các item đã chuẩn hóa và kiểm định bởi validator
        for idx, item in enumerate(validated_items):
            start_str = item["start_time"]
            visit_dt = datetime.strptime(f"{parsed_date.isoformat()} {start_str}:00", "%Y-%m-%d %H:%M:%S")
            it_item = ItineraryItem(
                itinerary_id=itinerary.id,
                attraction_id=str(item["osm_id"]),
                visit_time=visit_dt,
                estimated_duration=item["estimated_duration"],
                notes=item.get("notes"),
                order_index=item["order_index"]
            )
            db.session.add(it_item)

        # 11. COMMIT TOÀN BỘ TRANSACTION DUY NHẤT
        db.session.commit()
        committed = True
        created_id = itinerary.id

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error during confirm_draft_itinerary database transaction: {e}", exc_info=True)
        return False, "Đã xảy ra lỗi nội bộ trong quá trình lưu trữ lịch trình.", False, 500

    finally:
        # Bảo đảm giải phóng lock/transaction nếu bất kỳ nhánh nào chưa commit
        if not committed:
            try:
                db.session.rollback()
            except Exception:
                pass

    # 11. Gửi thông báo/nhắc lịch ngoài transaction (sau commit an toàn)
    try:
        reminder_success, reminder_msg = create_itinerary_reminder_notification(created_id)
        if not reminder_success:
            logger.warning(f"Could not schedule reminder for itinerary {created_id}: {reminder_msg}")
    except Exception as notif_err:
        logger.warning(f"Error creating reminder notification: {notif_err}")

    # Lấy đối tượng đầy đủ trả về cho client
    saved_itinerary = Itinerary.query.get(created_id)
    return True, saved_itinerary.to_dict() if saved_itinerary else {"id": created_id}, False, 201

