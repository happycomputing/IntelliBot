from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

class Conversation(db.Model):
    __tablename__ = 'conversations'
    
    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey('bots.id'), nullable=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    sources = db.Column(db.JSON)
    similarity_scores = db.Column(db.JSON)
    feedback = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    bot = db.relationship('Bot', backref='conversations', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'bot_id': self.bot_id,
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
    bot_id = db.Column(db.Integer, db.ForeignKey('bots.id'), nullable=True)
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
    bot = db.relationship('Bot', backref='intents', lazy=True)
    
    def to_dict(self):
        return {
            'id': self.id,
            'bot_id': self.bot_id,
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

class Bot(db.Model):
    __tablename__ = 'bots'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), unique=True, nullable=False)
    description = db.Column(db.Text)
    project_path = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), default='idle', nullable=False)
    last_error = db.Column(db.Text)
    last_trained_at = db.Column(db.DateTime)
    rasa_port = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description or '',
            'project_path': self.project_path,
            'status': self.status,
            'last_error': self.last_error or '',
            'last_trained_at': self.last_trained_at.isoformat() if self.last_trained_at else None,
            'rasa_port': self.rasa_port,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
