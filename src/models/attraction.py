from src.models.base import db
from sqlalchemy import JSON

class Attraction(db.Model):
    __tablename__ = 'Attractions'
    
    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    address = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    rating = db.Column(db.Float, default=0.0)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    category = db.Column(db.String(100))
    tags = db.Column(JSON)  # Store as JSON array
    price = db.Column(db.Float)
    opening_hours = db.Column(db.String(200))
    phone_number = db.Column(db.String(20))
    language = db.Column(db.String(500))
    aliases = db.Column(JSON)  # Store as JSON array
    
    # Relationship with ItineraryItem - sử dụng back_populates thay vì backref
    itinerary_items = db.relationship('ItineraryItem', back_populates='attraction', lazy=True)
    
    def __repr__(self):
        return f'<Attraction {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'description': self.description,
            'image_url': self.image_url,
            'rating': self.rating,
            'location': {
                'latitude': self.latitude,
                'longitude': self.longitude
            },
            'category': self.category,
            'tags': self.tags or [],
            'price': self.price,
            'opening_hours': self.opening_hours,
            'phone_number': self.phone_number,
            'language': self.language,
            'aliases': self.aliases or []
        } 