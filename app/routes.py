"""Flask routes definition"""

import uuid
import time
import json
import logging
import threading
from typing import Generator, Dict, Any
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from flask import (
    Blueprint, render_template, request, jsonify,
    Response, send_file, current_app
)

from app.scraper.suumo import SuumoScraper, SuumoScraperError, PropertyData
from app.scraper.auth import SuumoAuthError, get_favorites_with_login
from app.exporters.exporter import DataExporter, ExportError

logger = logging.getLogger(__name__)

# Blueprints
main_bp = Blueprint('main', __name__)
api_bp = Blueprint('api', __name__)

# Task storage
tasks: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()

# Thread pool
executor = ThreadPoolExecutor(max_workers=4)

# Validation constants
MIN_DELAY = 1
MAX_DELAY = 10
TASK_EXPIRY_HOURS = 1


def cleanup_old_tasks():
    """Remove tasks older than TASK_EXPIRY_HOURS"""
    expiry_time = datetime.now() - timedelta(hours=TASK_EXPIRY_HOURS)
    with tasks_lock:
        expired = [
            task_id for task_id, task in tasks.items()
            if task.get('completed_at') and
            datetime.fromisoformat(task['completed_at']) < expiry_time
        ]
        for task_id in expired:
            del tasks[task_id]
            logger.info(f"Cleaned up expired task: {task_id}")


# ==================== Main Page ====================

@main_bp.route('/')
def index():
    """Main page"""
    return render_template('index.html')


# ==================== API Endpoints ====================

@api_bp.route('/scrape', methods=['POST'])
def start_scrape():
    """Start scraping"""
    # Cleanup old tasks periodically
    cleanup_old_tasks()

    data = request.get_json()
    if not data or 'urls' not in data:
        return jsonify({'error': 'URL is not specified'}), 400

    urls = data.get('urls', [])
    delay = data.get('delay', 2)

    # Validate delay parameter
    try:
        delay = int(delay)
        if delay < MIN_DELAY or delay > MAX_DELAY:
            return jsonify({
                'error': f'Delay must be between {MIN_DELAY} and {MAX_DELAY} seconds'
            }), 400
    except (TypeError, ValueError):
        delay = 2

    # Filter empty URLs
    urls = [url.strip() for url in urls if url and url.strip()]

    if not urls:
        return jsonify({'error': 'No valid URLs'}), 400

    # Validate MAX_URLS
    max_urls = current_app.config.get('MAX_URLS', 100)
    if len(urls) > max_urls:
        return jsonify({
            'error': f'Maximum {max_urls} URLs allowed, got {len(urls)}'
        }), 400

    # Validate URLs
    scraper = SuumoScraper()
    invalid_urls = [url for url in urls if not scraper.validate_url(url)]
    if invalid_urls:
        return jsonify({
            'error': 'Invalid URLs included',
            'invalid_urls': invalid_urls
        }), 400

    # Generate task ID
    task_id = str(uuid.uuid4())

    # Initialize task info
    tasks[task_id] = {
        'status': 'pending',
        'urls': urls,
        'delay': delay,
        'progress': 0,
        'total': len(urls),
        'current_url': '',
        'results': [],
        'errors': [],
        'started_at': None,
        'completed_at': None
    }

    # Run scraping in background
    executor.submit(run_scraping_task, task_id)

    return jsonify({
        'task_id': task_id,
        'total': len(urls)
    })


@api_bp.route('/scrape/stream/<task_id>')
def scrape_stream(task_id: str):
    """Server-Sent Events for scraping progress"""
    if task_id not in tasks:
        return jsonify({'error': 'Task not found'}), 404

    def generate() -> Generator[str, None, None]:
        last_progress = -1

        while True:
            if task_id not in tasks:
                yield format_sse({'type': 'error', 'message': 'Task not found'})
                break

            task = tasks[task_id]

            # Send when progress updated
            if task['progress'] != last_progress or task['status'] in ['completed', 'error']:
                last_progress = task['progress']

                data = {
                    'type': 'progress',
                    'status': task['status'],
                    'progress': task['progress'],
                    'total': task['total'],
                    'current_url': task['current_url'],
                    'errors': task['errors']
                }

                yield format_sse(data)

                if task['status'] in ['completed', 'error']:
                    break

            time.sleep(0.5)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@api_bp.route('/scrape/status/<task_id>')
def scrape_status(task_id: str):
    """Get scraping task status"""
    if task_id not in tasks:
        return jsonify({'error': 'Task not found'}), 404

    task = tasks[task_id]
    return jsonify({
        'status': task['status'],
        'progress': task['progress'],
        'total': task['total'],
        'current_url': task['current_url'],
        'errors': task['errors'],
        'result_count': len(task['results'])
    })


@api_bp.route('/download/<task_id>/<format>')
def download_results(task_id: str, format: str):
    """Download scraping results"""
    if task_id not in tasks:
        return jsonify({'error': 'Task not found'}), 404

    task = tasks[task_id]

    if task['status'] != 'completed':
        return jsonify({'error': 'Task not completed'}), 400

    if not task['results']:
        return jsonify({'error': 'No data to export'}), 400

    try:
        # Convert PropertyData to dict
        data = [r.to_dict() if isinstance(r, PropertyData) else r for r in task['results']]

        file_stream, mime_type, extension = DataExporter.export(data, format)
        filename = f"suumo_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}{extension}"

        return send_file(
            file_stream,
            mimetype=mime_type,
            as_attachment=True,
            download_name=filename
        )

    except ExportError as e:
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({'error': 'Download error'}), 500


@api_bp.route('/favorites', methods=['POST'])
def get_favorites():
    """Get favorite property URLs from SUUMO"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    try:
        urls = get_favorites_with_login(email, password, headless=True)
        return jsonify({
            'urls': urls,
            'count': len(urls)
        })
    except SuumoAuthError as e:
        return jsonify({'error': str(e)}), 401
    except Exception as e:
        logger.error(f"Favorites fetch error: {e}")
        return jsonify({'error': 'Failed to get favorites'}), 500


@api_bp.route('/validate', methods=['POST'])
def validate_urls():
    """Validate URLs"""
    data = request.get_json()
    if not data or 'urls' not in data:
        return jsonify({'error': 'URL is not specified'}), 400

    urls = data.get('urls', [])
    valid = []
    invalid = []

    for url in urls:
        url = url.strip() if url else ''
        if url:
            if SuumoScraper.validate_url(url):
                valid.append(url)
            else:
                invalid.append(url)

    return jsonify({
        'valid': valid,
        'invalid': invalid,
        'valid_count': len(valid),
        'invalid_count': len(invalid)
    })


@api_bp.route('/formats')
def get_formats():
    """Get supported export formats"""
    return jsonify({
        'formats': DataExporter.get_supported_formats()
    })


# ==================== Helper Functions ====================

def format_sse(data: Dict[str, Any]) -> str:
    """Format data for SSE"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def run_scraping_task(task_id: str) -> None:
    """Run scraping in background"""
    task = tasks.get(task_id)
    if not task:
        return

    task['status'] = 'running'
    task['started_at'] = datetime.now().isoformat()

    scraper = SuumoScraper()

    try:
        for i, url in enumerate(task['urls']):
            task['current_url'] = url
            task['progress'] = i

            try:
                result = scraper.scrape_property(url)
                if result:
                    task['results'].append(result)
                else:
                    task['errors'].append({
                        'url': url,
                        'error': 'Could not get property info'
                    })
            except SuumoScraperError as e:
                task['errors'].append({
                    'url': url,
                    'error': str(e)
                })
            except Exception as e:
                logger.error(f"Scraping error for {url}: {e}")
                task['errors'].append({
                    'url': url,
                    'error': 'Unexpected error occurred'
                })

            # Wait except for last URL
            if i < len(task['urls']) - 1:
                time.sleep(task['delay'])

        task['status'] = 'completed'
        task['progress'] = len(task['urls'])

    except Exception as e:
        logger.error(f"Task error: {e}")
        task['status'] = 'error'
        task['errors'].append({
            'url': '',
            'error': f'Task error: {e}'
        })

    finally:
        task['completed_at'] = datetime.now().isoformat()
        task['current_url'] = ''
