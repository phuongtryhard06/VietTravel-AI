from datetime import datetime, time
from src.models.base import db

class Itinerary(db.Model):
    __tablename__ = 'Itineraries'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('Users.user_id'), nullable=False)
    selected_date = db.Column(db.Date, nullable=False)  # Date for the itinerary
    title = db.Column(db.String(255))  # Optional title for the itinerary
    notes = db.Column(db.Text)  # General notes for the entire itinerary
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_deleted = db.Column(db.Boolean, default=False)
    
    # Relationships
    user = db.relationship('User', backref='itineraries', lazy=True)
    items = db.relationship('ItineraryItem', back_populates='itinerary', cascade='all, delete-orphan', lazy=True)
    
    def __repr__(self):
        return f'<Itinerary {self.id} for user {self.user_id} on {self.selected_date}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'selected_date': self.selected_date.isoformat() if self.selected_date else None,
            'title': self.title,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'is_deleted': self.is_deleted,
            'items': [item.to_dict() for item in self.items] if self.items else []
        }