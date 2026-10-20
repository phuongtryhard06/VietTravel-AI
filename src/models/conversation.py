from datetime import datetime
from src.models.base import db

class Conversation(db.Model):
    __tablename__ = 'Conversations'
    
    conversation_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('Users.user_id'))
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    source_language = db.Column(db.String(10))
    title = db.Column(db.String(100), nullable=True)
    messages = db.relationship('Message', backref='conversation', lazy=True)