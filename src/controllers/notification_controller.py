from flask import request
from flask_restx import Namespace, Resource, fields
from src.services.notification_service import (
    get_user_notifications,
    mark_notification_as_read,
    delete_notification
)
from src.services.scheduler_service import get_scheduler_status

# Create namespace for notification API
notification_ns = Namespace('notification', description='Notification management operations')

# Define notification model
notification_model = notification_ns.model('Notification', {
    'id': fields.Integer(description='Notification ID'),
    'user_id': fields.Integer(description='User ID'),
    'itinerary_id': fields.Integer(description='Itinerary ID'),
    'title': fields.String(description='Notification title'),
    'message': fields.String(description='Notification message'),
    'notification_type': fields.String(description='Type of notification'),
    'is_read': fields.Boolean(description='Read status'),
    'scheduled_for': fields.String(description='Scheduled time in ISO format'),
    'sent_at': fields.String(description='Sent time in ISO format'),
    'created_at': fields.String(description='Creation time in ISO format'),
    'is_deleted': fields.Boolean(description='Deleted status')
})

# Define scheduler status model
scheduler_status_model = notification_ns.model('SchedulerStatus', {
    'running': fields.Boolean(description='Whether scheduler is running'),
    'processed_count': fields.Integer(description='Total reminder emails sent'),
    'error_count': fields.Integer(description='Total errors encountered'),
    'check_interval': fields.Integer(description='Check interval in seconds'),
    'last_check_time': fields.String(description='Last check time in ISO format'),
    'next_check_time': fields.String(description='Next check time in ISO format')
})

# Define response models
notification_response_model = notification_ns.model('NotificationResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.Nested(notification_model, description='Notification data')
})

notification_list_response_model = notification_ns.model('NotificationListResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.List(fields.Nested(notification_model), description='List of notifications')
})

scheduler_status_response_model = notification_ns.model('SchedulerStatusResponse', {
    'status': fields.String(description='Status of the response'),
    'message': fields.String(description='Response message'),
    'data': fields.Nested(scheduler_status_model, description='Scheduler status data')
})

@notification_ns.route('/list')
class UserNotificationsResource(Resource):
    @notification_ns.doc(params={
        'user_id': 'User ID (required)',
        'limit': 'Maximum number of notifications to return (default: 50)'
    })
    @notification_ns.response(200, 'Successfully retrieved notifications', notification_list_response_model)
    @notification_ns.response(400, 'Missing user_id parameter')
    @notification_ns.response(500, 'Internal server error')
    def get(self):
        """Get all notifications for a user"""
        user_id = request.args.get('user_id', type=int)
        limit = request.args.get('limit', type=int, default=50)
        
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        
        try:
            success, result = get_user_notifications(user_id, limit)
            
            if not success:
                return {'message': str(result)}, 500
                
            return {
                'status': 'success',
                'message': f'Successfully retrieved notifications for user {user_id}',
                'data': result
            }
            
        except Exception as e:
            return {'message': f'Error processing request: {str(e)}'}, 500

@notification_ns.route('/<int:notification_id>/read')
class MarkNotificationReadResource(Resource):
    @notification_ns.doc(params={
        'user_id': 'User ID (required for authorization)'
    })
    @notification_ns.response(200, 'Successfully marked notification as read')
    @notification_ns.response(400, 'Missing user_id parameter')
    @notification_ns.response(404, 'Notification not found')
    @notification_ns.response(403, 'Not authorized to modify this notification')
    @notification_ns.response(500, 'Internal server error')
    def put(self, notification_id):
        """Mark a notification as read"""
        user_id = request.args.get('user_id', type=int)
        
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        
        try:
            success, result = mark_notification_as_read(notification_id, user_id)
            
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

@notification_ns.route('/<int:notification_id>')
class DeleteNotificationResource(Resource):
    @notification_ns.doc(params={
        'user_id': 'User ID (required for authorization)'
    })
    @notification_ns.response(200, 'Successfully deleted notification')
    @notification_ns.response(400, 'Missing user_id parameter')
    @notification_ns.response(404, 'Notification not found')
    @notification_ns.response(403, 'Not authorized to delete this notification')
    @notification_ns.response(500, 'Internal server error')
    def delete(self, notification_id):
        """Delete a notification"""
        user_id = request.args.get('user_id', type=int)
        
        if not user_id:
            return {'message': 'user_id parameter is required'}, 400
        
        try:
            success, result = delete_notification(notification_id, user_id)
            
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

@notification_ns.route('/scheduler/status')
class SchedulerStatusResource(Resource):
    @notification_ns.response(200, 'Successfully retrieved scheduler status', scheduler_status_response_model)
    @notification_ns.response(500, 'Internal server error')
    def get(self):
        """Get itinerary reminder scheduler status"""
        try:
            status = get_scheduler_status()
            
            return {
                'status': 'success',
                'message': 'Successfully retrieved itinerary reminder scheduler status',
                'data': status
            }
            
        except Exception as e:
            return {'message': f'Error retrieving scheduler status: {str(e)}'}, 500 