from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class Conversation(db.Model):
    __tablename__ = 'conversations'
    
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    sources = db.Column(db.JSON)
    similarity_scores = db.Column(db.JSON)
    feedback = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    def to_dict(self):
        return {
            'id': self.id,
            'question': self.question,
            'answer': self.answer,
            'sources': self.sources,
            'similarity_scores': self.similarity_scores,
            'feedback': self.feedback,
            'timestamp': self.timestamp.isoformat()
        }

class Intent(db.Model):
    __tablename__ = 'intents'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text)
    patterns = db.Column(db.JSON)
    examples = db.Column(db.JSON)
    auto_detected = db.Column(db.Boolean, default=False)
    enabled = db.Column(db.Boolean, default=True)
    action_type = db.Column(db.String(50), default='retrieval')
    responses = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'patterns': self.patterns or [],
            'examples': self.examples or [],
            'auto_detected': self.auto_detected,
            'enabled': self.enabled,
            'action_type': self.action_type or 'retrieval',
            'responses': self.responses or [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
