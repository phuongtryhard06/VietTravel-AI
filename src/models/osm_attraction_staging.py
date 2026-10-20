from src.models.base import db
from sqlalchemy.dialects.postgresql import JSONB

class OsmAttractionStaging(db.Model):
    """
    SQLAlchemy model for the osm_attractions_staging table.
    Stores cleaned and pre-processed OpenStreetMap POIs before promotion to Attractions.
    """
    __tablename__ = 'osm_attractions_staging'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    osm_id = db.Column(db.String(50), unique=True, nullable=False)
    osm_source_layer = db.Column(db.String(100), nullable=False)
    osm_code = db.Column(db.Integer)
    fclass = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    address = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=True, default=None)
    opening_hours = db.Column(db.String(200), nullable=True, default=None)
    phone_number = db.Column(db.String(50), nullable=True, default=None)
    image_url = db.Column(db.String(500), nullable=True, default=None)
    rating = db.Column(db.Float, nullable=True, default=None)
    language = db.Column(db.String(50), default='vietnamese')
    tags = db.Column(JSONB)
    aliases = db.Column(JSONB)
    raw_properties = db.Column(JSONB)
    imported_to_attraction = db.Column(db.Boolean, default=False)
    attraction_id = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    
    def __repr__(self):
        return f"<OsmAttractionStaging {self.osm_id} - {self.name}>"

    def to_dict(self):
        return {
            'osm_id': self.osm_id,
            'name': self.name,
            'category': self.category,
            'fclass': self.fclass,
            'location': {
                'latitude': float(self.latitude) if self.latitude is not None else None,
                'longitude': float(self.longitude) if self.longitude is not None else None
            },
            'description': self.description,
            'address': self.address,
            'tags': self.tags if self.tags is not None else [],
            'price': self.price,
            'opening_hours': self.opening_hours,
            'phone_number': self.phone_number,
            'image_url': self.image_url,
            'rating': self.rating,
            'osm_source': {
                'layer': self.osm_source_layer,
                'osm_id': self.osm_id,
                'code': self.osm_code
            }
        }
