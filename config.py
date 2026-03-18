"""Flask Application Configuration"""

import os
import logging


class Config:
    """Base configuration"""

    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'sumo_digger.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Application settings
    DOWNLOAD_FOLDER = os.environ.get('DOWNLOAD_FOLDER', 'downloads')
    MAX_URLS = int(os.environ.get('MAX_URLS', 100))
    REQUEST_DELAY = int(os.environ.get('REQUEST_DELAY', 2))

    # Blueprint PDF upload
    UPLOAD_FOLDER = os.environ.get(
        'UPLOAD_FOLDER',
        os.path.join(os.path.abspath(os.path.dirname(__file__)), 'uploads')
    )
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 50 * 1024 * 1024))  # 50MB

    # Logging
    LOG_LEVEL = logging.INFO


class DevelopmentConfig(Config):
    """Development configuration"""

    DEBUG = True
    LOG_LEVEL = logging.DEBUG


class ProductionConfig(Config):
    """Production configuration"""

    DEBUG = False
    LOG_LEVEL = logging.WARNING

    # In production, SECRET_KEY MUST be set via environment variable
    SECRET_KEY = os.environ.get('SECRET_KEY')

    def __init__(self):
        if not self.SECRET_KEY:
            raise ValueError("SECRET_KEY environment variable must be set in production")


class TestingConfig(Config):
    """Testing configuration"""

    TESTING = True
    DEBUG = True
    LOG_LEVEL = logging.DEBUG
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
