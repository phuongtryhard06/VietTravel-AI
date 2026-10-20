from datetime import datetime
from src.models.base import db

class Notification(db.Model):
    __tablename__ = 'Notifications'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('Users.user_id'), nullable=False)
    itinerary_id = db.Column(db.Integer, db.ForeignKey('Itineraries.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    notification_type = db.Column(db.String(50), default='itinerary_reminder')  # itinerary_reminder, etc.
    is_read = db.Column(db.Boolean, default=False)
    scheduled_for = db.Column(db.DateTime, nullable=False)  # When to send the notification
    sent_at = db.Column(db.DateTime)  # When the notification was actually sent
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_deleted = db.Column(db.Boolean, default=False)
    
    # Relationships
    user = db.relationship('User', backref='notifications', lazy=True)
    itinerary = db.relationship('Itinerary', backref='notifications', lazy=True)
    
    def __repr__(self):
        return f'<Notification {self.id} for user {self.user_id} - {self.title}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'itinerary_id': self.itinerary_id,
            'title': self.title,
            'message': self.message,
            'notification_type': self.notification_type,
            'is_read': self.is_read,
            'scheduled_for': self.scheduled_for.isoformat() if self.scheduled_for else None,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_deleted': self.is_deleted
        } 