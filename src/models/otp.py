from datetime import datetime
from src.models.base import db

class OTP(db.Model):
    __tablename__ = 'OTP'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(100))
    otp_code = db.Column(db.String(6))
    purpose = db.Column(db.String(20))  # 'register' or 'reset_password'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    is_used = db.Column(db.Boolean, default=False) 