from datetime import datetime, timedelta
from src.models.notification import Notification
from src.models.itinerary import Itinerary
from src.models.user import User
from src.models.base import db
from src.services.email_service import send_notification_email
from typing import List, Dict, Any

def create_itinerary_reminder_notification(itinerary_id: int) -> tuple[bool, str]:
    """
    Create a reminder notification for an itinerary (1 day before)
    
    Args:
        itinerary_id (int): ID of the itinerary
        
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Get itinerary details
        itinerary = Itinerary.query.get(itinerary_id)
        if not itinerary:
            return False, f"Itinerary with ID {itinerary_id} not found"
        
        # Calculate when to send the notification (1 day before the itinerary date)
        notification_time = datetime.combine(itinerary.selected_date, datetime.min.time()) - timedelta(days=1)
        
        # Check if notification time is in the future
        if notification_time <= datetime.utcnow():
            return False, "Cannot create reminder for past or current date"
        
        # Check if notification already exists
        existing_notification = Notification.query.filter_by(
            itinerary_id=itinerary_id,
            notification_type='itinerary_reminder',
            is_deleted=False
        ).first()
        
        if existing_notification:
            return False, "Reminder notification already exists for this itinerary"
        
        # Create notification
        notification = Notification(
            user_id=itinerary.user_id,
            itinerary_id=itinerary_id,
            title=f"Reminder: Your itinerary for {itinerary.selected_date.strftime('%B %d, %Y')}",
            message=f"You have a planned itinerary for {itinerary.selected_date.strftime('%B %d, %Y')}. "
                   f"Don't forget to check your schedule!",
            notification_type='itinerary_reminder',
            scheduled_for=notification_time
        )
        
        db.session.add(notification)
        db.session.commit()
        
        return True, f"Reminder notification scheduled for {notification_time.strftime('%Y-%m-%d %H:%M:%S')}"
        
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def get_pending_notifications() -> List[Notification]:
    """
    Get all notifications that are due to be sent
    
    Returns:
        List[Notification]: List of pending notifications
    """
    now = datetime.utcnow()
    return Notification.query.filter(
        Notification.scheduled_for <= now,
        Notification.sent_at.is_(None),
        Notification.is_deleted == False
    ).all()

def send_notification(notification: Notification) -> bool:
    """
    Send a notification to the user
    
    Args:
        notification (Notification): The notification to send
        
    Returns:
        bool: True if sent successfully, False otherwise
    """
    try:
        # Get user details
        user = User.query.get(notification.user_id)
        if not user:
            return False
        
        # Send email notification
        success = send_notification_email(
            user.email,
            notification.title,
            notification.message,
            user.full_name
        )
        
        if success:
            # Mark as sent
            notification.sent_at = datetime.utcnow()
            db.session.commit()
            return True
        else:
            return False
            
    except Exception as e:
        print(f"Error sending notification {notification.id}: {e}")
        return False

def get_user_notifications(user_id: int, limit: int = 50) -> tuple[bool, List[Dict[str, Any]] | str]:
    """
    Get all notifications for a user
    
    Args:
        user_id (int): ID of the user
        limit (int): Maximum number of notifications to return
        
    Returns:
        tuple: (success: bool, result: List[Dict] or str)
    """
    try:
        notifications = Notification.query.filter_by(
            user_id=user_id,
            is_deleted=False
        ).order_by(Notification.created_at.desc()).limit(limit).all()
        
        result = [notification.to_dict() for notification in notifications]
        return True, result
        
    except Exception as e:
        return False, str(e)

def mark_notification_as_read(notification_id: int, user_id: int) -> tuple[bool, str]:
    """
    Mark a notification as read
    
    Args:
        notification_id (int): ID of the notification
        user_id (int): ID of the user (for authorization)
        
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        notification = Notification.query.get(notification_id)
        if not notification:
            return False, "Notification not found"
        
        if notification.user_id != user_id:
            return False, "Not authorized to modify this notification"
        
        notification.is_read = True
        db.session.commit()
        
        return True, "Notification marked as read"
        
    except Exception as e:
        db.session.rollback()
        return False, str(e)

def delete_notification(notification_id: int, user_id: int) -> tuple[bool, str]:
    """
    Soft delete a notification
    
    Args:
        notification_id (int): ID of the notification
        user_id (int): ID of the user (for authorization)
        
    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        notification = Notification.query.get(notification_id)
        if not notification:
            return False, "Notification not found"
        
        if notification.user_id != user_id:
            return False, "Not authorized to delete this notification"
        
        notification.is_deleted = True
        db.session.commit()
        
        return True, "Notification deleted successfully"
        
    except Exception as e:
        db.session.rollback()
        return False, str(e) 