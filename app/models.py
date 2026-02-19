"""Database Models"""

import json
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class ScrapingTask(db.Model):
    """Scraping task record"""
    __tablename__ = 'scraping_task'

    id = db.Column(db.String(36), primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending/running/completed/failed
    property_type = db.Column(db.String(20), default='rental')  # rental/buy
    total = db.Column(db.Integer, default=0)
    errors_json = db.Column(db.Text, default='[]')

    properties = db.relationship('Property', backref='task', lazy='dynamic',
                                 cascade='all, delete-orphan')

    @property
    def errors(self):
        return json.loads(self.errors_json) if self.errors_json else []

    @errors.setter
    def errors(self, value):
        self.errors_json = json.dumps(value, ensure_ascii=False)

    @property
    def result_count(self):
        return self.properties.count()

    def to_dict(self):
        return {
            'id': self.id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'status': self.status,
            'property_type': self.property_type,
            'total': self.total,
            'result_count': self.result_count,
            'errors': self.errors,
        }


class Property(db.Model):
    """Scraped property record"""
    __tablename__ = 'property'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.String(36), db.ForeignKey('scraping_task.id'), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    data_json = db.Column(db.Text, nullable=False)  # PropertyData.to_dict() serialized
    translated_json = db.Column(db.Text, nullable=True)  # LLM translation cache
    image_urls_json = db.Column(db.Text, default='[]')  # Image URLs
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def data(self):
        return json.loads(self.data_json) if self.data_json else {}

    @data.setter
    def data(self, value):
        self.data_json = json.dumps(value, ensure_ascii=False)

    @property
    def translated(self):
        return json.loads(self.translated_json) if self.translated_json else None

    @translated.setter
    def translated(self, value):
        self.translated_json = json.dumps(value, ensure_ascii=False) if value else None

    @property
    def image_urls(self):
        return json.loads(self.image_urls_json) if self.image_urls_json else []

    @image_urls.setter
    def image_urls(self, value):
        self.image_urls_json = json.dumps(value, ensure_ascii=False)

    def to_dict(self, use_translated=True, bilingual=False):
        """Return property data.

        bilingual=True: returns original data + _translated dict for frontend dual display.
        use_translated=True (non-bilingual): returns translated data replacing originals (for export).
        """
        if bilingual:
            base = dict(self.data)
            base['_id'] = self.id
            base['_url'] = self.url
            base['_has_translation'] = self.translated is not None
            base['_image_urls'] = self.image_urls
            if self.translated:
                base['_translated'] = dict(self.translated)
            return base

        base = dict(self.translated) if (use_translated and self.translated) else dict(self.data)
        base['_id'] = self.id
        base['_url'] = self.url
        base['_has_translation'] = self.translated is not None
        base['_image_urls'] = self.image_urls
        return base


class Settings(db.Model):
    """Application settings (single-row table)"""
    __tablename__ = 'settings'

    id = db.Column(db.Integer, primary_key=True, default=1)
    llm_base_url = db.Column(db.String(500), default='')
    llm_api_key = db.Column(db.String(500), default='')
    llm_model = db.Column(db.String(100), default='gemini-2.5-flash')

    @classmethod
    def get(cls):
        """Get or create the singleton settings row"""
        settings = cls.query.get(1)
        if not settings:
            settings = cls(id=1)
            db.session.add(settings)
            db.session.commit()
        return settings

    def to_dict(self):
        return {
            'llm_base_url': self.llm_base_url or '',
            'llm_api_key': '***' if self.llm_api_key else '',  # mask key
            'llm_model': self.llm_model or 'gemini-2.5-flash',
            'llm_configured': bool(self.llm_base_url and self.llm_api_key),
        }
