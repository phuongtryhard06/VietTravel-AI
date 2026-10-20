from datetime import datetime, time
from src.models.base import db

class ItineraryItem(db.Model):
    __tablename__ = 'ItineraryItems'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    itinerary_id = db.Column(db.Integer, db.ForeignKey('Itineraries.id'), nullable=False)
    attraction_id = db.Column(db.String(50), db.ForeignKey('Attractions.id'), nullable=False)
    visit_time = db.Column(db.DateTime, nullable=False)
    estimated_duration = db.Column(db.Integer)  # Duration in minutes
    notes = db.Column(db.Text)
    order_index = db.Column(db.Integer, default=0)  # For ordering items in the itinerary
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    itinerary = db.relationship('Itinerary', back_populates='items', lazy=True)
    attraction = db.relationship('Attraction', back_populates='itinerary_items', lazy=True)
    
    def __repr__(self):
        return f'<ItineraryItem {self.attraction.name if self.attraction else "Unknown"} at {self.visit_time}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'itinerary_id': self.itinerary_id,
            'attraction': self.attraction.to_dict() if self.attraction else None,
            'attraction_id': self.attraction_id,
            'visit_time': self.visit_time.isoformat() if self.visit_time else None,
            'estimated_duration': self.estimated_duration,
            'notes': self.notes,
            'order_index': self.order_index,
            'created_at': self.created_at.isoformat() if self.created_at else None
        } 