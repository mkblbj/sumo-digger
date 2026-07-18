"""Flask Application Factory"""

import os
import logging
from flask import Flask
from flask_cors import CORS

from app.models import db


def create_app(config_name: str = None) -> Flask:
    """
    Flask application factory

    Args:
        config_name: Configuration name (development, production, testing)

    Returns:
        Flask application instance
    """
    app = Flask(__name__)

    # Load configuration
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    if config_name == 'production':
        app.config.from_object('config.ProductionConfig')
    elif config_name == 'testing':
        app.config.from_object('config.TestingConfig')
    else:
        app.config.from_object('config.DevelopmentConfig')

    # Enable CORS
    CORS(app)

    # Initialize database
    os.makedirs(os.path.join(os.path.abspath(os.path.dirname(os.path.dirname(__file__))), 'instance'), exist_ok=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _fix_zombie_tasks(app)

    # Configure logging
    configure_logging(app)

    # Register blueprints
    from app.routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

    # Create download and upload directories
    os.makedirs(app.config.get('DOWNLOAD_FOLDER', 'downloads'), exist_ok=True)
    upload_folder = app.config.get('UPLOAD_FOLDER', 'uploads')
    os.makedirs(upload_folder, exist_ok=True)
    os.makedirs(os.path.join(upload_folder, 'floor_plans'), exist_ok=True)

    app.logger.info(f"Application started in {config_name} mode")

    return app


def _has_restart_interruption(errors) -> bool:
    for item in errors or []:
        msg = ''
        if isinstance(item, dict):
            msg = str(item.get('error') or item.get('message') or '')
        else:
            msg = str(item)
        if 'server restart' in msg.lower():
            return True
    return False


def _fix_zombie_tasks(app: Flask) -> None:
    """Finalize stale running/pending tasks on startup.

    A server restart can interrupt a task after many Property rows have already
    been committed. Those rows are still usable by the API, so keep that task
    completed instead of turning it into a hard failure. Also repair tasks that
    were already marked failed by older versions of this startup recovery.
    """
    from datetime import datetime, timezone
    from app.models import ScrapingTask
    zombies = ScrapingTask.query.filter(
        ScrapingTask.status.in_(['running', 'pending', 'collecting', 'failed'])
    ).all()
    if zombies:
        now = datetime.now(timezone.utc)
        fixed_count = 0
        for t in zombies:
            if t.status in ['running', 'pending', 'collecting']:
                t.status = 'completed' if t.result_count > 0 else 'failed'
                t.completed_at = now
                t.errors = [{'url': 'system', 'error': 'Task interrupted by server restart'}]
                fixed_count += 1
            elif t.result_count > 0 and _has_restart_interruption(t.errors):
                t.status = 'completed'
                if not t.completed_at:
                    t.completed_at = now
                fixed_count += 1
        if fixed_count:
            db.session.commit()
            app.logger.info(f"Fixed {fixed_count} zombie task(s) from previous run")


def configure_logging(app: Flask) -> None:
    """Configure logging"""
    log_level = app.config.get('LOG_LEVEL', logging.INFO)

    # Application logger
    app.logger.setLevel(log_level)

    # Root logger configuration
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Adjust external library log levels
    logging.getLogger('selenium').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
