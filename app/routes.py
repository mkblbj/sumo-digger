"""Flask routes definition"""

import io
import uuid
import time
import json
import zipfile
import logging
import threading
from typing import Generator, Dict, Any
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests as http_requests

from flask import (
    Blueprint, render_template, request, jsonify,
    Response, send_file, current_app, stream_with_context
)

from app.models import db, ScrapingTask, Property, Settings
from app.scraper.suumo import SuumoScraper, SuumoScraperError, PropertyData
from app.scraper.buy_scraper import BuyScraper, BuyScraperError
from app.scraper.search_parser import SuumoSearchParser, SearchParserError
from app.scraper.auth import SuumoAuthError, get_favorites_with_login
from app.exporters.exporter import DataExporter, ExportError

logger = logging.getLogger(__name__)

# Blueprints
main_bp = Blueprint('main', __name__)
api_bp = Blueprint('api', __name__)

# In-memory progress tracking (for SSE, volatile)
_progress: Dict[str, Dict[str, Any]] = {}
_progress_lock = threading.Lock()

# Validation constants
MIN_DELAY = 1
MAX_DELAY = 10


# ==================== Main Pages ====================

@main_bp.route('/')
def index():
    """Main page"""
    return render_template('index.html')


@main_bp.route('/results')
def results_page():
    """Results browsing page"""
    return render_template('results.html')


@main_bp.route('/settings')
def settings_page():
    """Settings page"""
    return render_template('settings.html')


# ==================== Scraping API ====================

@api_bp.route('/scrape', methods=['POST'])
def start_scrape():
    """Start scraping"""
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

    # Validate URLs (accept both rental and buy URLs)
    invalid_urls = [url for url in urls if not SuumoScraper.validate_url(url) and not BuyScraper.is_buy_url(url)]
    if invalid_urls:
        return jsonify({
            'error': 'Invalid URLs included',
            'invalid_urls': invalid_urls
        }), 400

    # Detect property type from first URL
    property_type = 'buy' if any(BuyScraper.is_buy_url(u) for u in urls) else 'rental'

    # Generate task ID and persist to DB
    task_id = str(uuid.uuid4())

    task = ScrapingTask(
        id=task_id,
        status='pending',
        property_type=property_type,
        total=len(urls),
    )
    db.session.add(task)
    db.session.commit()

    # Initialize in-memory progress
    with _progress_lock:
        _progress[task_id] = {
            'status': 'pending',
            'urls': urls,
            'delay': delay,
            'progress': 0,
            'total': len(urls),
            'current_url': '',
            'errors': [],
        }

    # Run scraping in background
    app = current_app._get_current_object()
    thread = threading.Thread(target=run_scraping_task, args=(app, task_id))
    thread.daemon = True
    thread.start()

    return jsonify({
        'task_id': task_id,
        'total': len(urls)
    })


@api_bp.route('/scrape/stream/<task_id>')
def scrape_stream(task_id: str):
    """Server-Sent Events for scraping progress"""
    if task_id not in _progress:
        # Check if task exists in DB (completed previously)
        task = db.session.get(ScrapingTask, task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        # Return completed status immediately
        def gen_done():
            yield format_sse({
                'type': 'progress',
                'status': task.status,
                'progress': task.total,
                'total': task.total,
                'current_url': '',
                'errors': task.errors,
            })
        return Response(gen_done(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive',
                                 'X-Accel-Buffering': 'no'})

    def generate() -> Generator[str, None, None]:
        last_progress = -1

        while True:
            if task_id not in _progress:
                yield format_sse({'type': 'error', 'message': 'Task not found'})
                break

            prog = _progress[task_id]

            if prog['progress'] != last_progress or prog['status'] in ['completed', 'error']:
                last_progress = prog['progress']

                data = {
                    'type': 'progress',
                    'status': prog['status'],
                    'progress': prog['progress'],
                    'total': prog['total'],
                    'current_url': prog['current_url'],
                    'errors': prog['errors']
                }

                yield format_sse(data)

                if prog['status'] in ['completed', 'error']:
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
    # Check in-memory first (active task)
    if task_id in _progress:
        prog = _progress[task_id]
        return jsonify({
            'status': prog['status'],
            'progress': prog['progress'],
            'total': prog['total'],
            'current_url': prog['current_url'],
            'errors': prog['errors'],
            'result_count': prog['progress'] - len(prog['errors'])
        })

    # Check DB
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    return jsonify({
        'status': task.status,
        'progress': task.total,
        'total': task.total,
        'current_url': '',
        'errors': task.errors,
        'result_count': task.result_count
    })


@api_bp.route('/download/<task_id>/<format>')
def download_results(task_id: str, format: str):
    """Download scraping results"""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    if task.status != 'completed':
        return jsonify({'error': 'Task not completed'}), 400

    properties = task.properties.all()
    if not properties:
        return jsonify({'error': 'No data to export'}), 400

    try:
        data = [p.to_dict(use_translated=True) for p in properties]
        # Strip internal fields for export
        clean_data = []
        for d in data:
            clean = {k: v for k, v in d.items() if not k.startswith('_')}
            clean_data.append(clean)

        file_stream, mime_type, extension = DataExporter.export(clean_data, format)
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


# ==================== Batch Search Scrape API ====================

@api_bp.route('/search/scrape', methods=['POST'])
def start_search_scrape():
    """Start batch scraping from a SUUMO search result URL"""
    data = request.get_json()
    if not data or 'search_url' not in data:
        return jsonify({'error': 'search_url is required'}), 400

    search_url = data['search_url'].strip()
    max_results = min(int(data.get('max_results', 100)), 1000)
    delay = int(data.get('delay', 2))
    delay = max(MIN_DELAY, min(delay, MAX_DELAY))

    if not SuumoSearchParser.is_search_url(search_url):
        return jsonify({'error': 'Not a valid SUUMO search URL'}), 400

    task_id = str(uuid.uuid4())

    task = ScrapingTask(
        id=task_id,
        status='pending',
        property_type='search',
        total=0,  # Will be updated after URL collection
    )
    db.session.add(task)
    db.session.commit()

    with _progress_lock:
        _progress[task_id] = {
            'status': 'collecting',
            'urls': [],
            'delay': delay,
            'progress': 0,
            'total': 0,
            'current_url': '',
            'errors': [],
            'phase': 'collecting',  # collecting -> scraping
        }

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=run_search_scrape_task,
        args=(app, task_id, search_url, max_results, delay)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id, 'max_results': max_results})


# ==================== Tasks API ====================

@api_bp.route('/tasks')
def list_tasks():
    """List all scraping tasks"""
    tasks = ScrapingTask.query.order_by(ScrapingTask.created_at.desc()).limit(50).all()
    return jsonify([t.to_dict() for t in tasks])


@api_bp.route('/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id: str):
    """Delete a scraping task"""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    db.session.delete(task)
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/properties/<task_id>')
def get_properties(task_id: str):
    """Get properties for a task"""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    properties = task.properties.all()
    return jsonify({
        'task': task.to_dict(),
        'properties': [p.to_dict(use_translated=True) for p in properties]
    })


# ==================== Favorites API ====================

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


# ==================== Validation API ====================

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


# ==================== Settings API ====================

@api_bp.route('/settings', methods=['GET'])
def get_settings():
    """Get application settings"""
    settings = Settings.get()
    return jsonify(settings.to_dict())


@api_bp.route('/settings', methods=['POST'])
def update_settings():
    """Update application settings"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    settings = Settings.get()

    if 'llm_base_url' in data:
        settings.llm_base_url = data['llm_base_url'].strip()
    if 'llm_api_key' in data and data['llm_api_key'] != '***':
        settings.llm_api_key = data['llm_api_key'].strip()
    if 'llm_model' in data:
        settings.llm_model = data['llm_model'].strip()

    db.session.commit()
    return jsonify(settings.to_dict())


@api_bp.route('/settings/test', methods=['POST'])
def test_llm_connection():
    """Test LLM API connection"""
    settings = Settings.get()
    if not settings.llm_base_url or not settings.llm_api_key:
        return jsonify({'error': 'LLM API not configured'}), 400

    try:
        from openai import OpenAI
        client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "テスト。「OK」とだけ返してください。"}],
            max_tokens=10,
        )
        reply = response.choices[0].message.content
        return jsonify({'ok': True, 'reply': reply})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Translation API ====================

@api_bp.route('/translate/<task_id>', methods=['POST'])
def translate_task(task_id: str):
    """Trigger translation for a task"""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    settings = Settings.get()
    if not settings.llm_base_url or not settings.llm_api_key:
        return jsonify({'error': 'LLM API not configured'}), 400

    # Run translation in background
    app = current_app._get_current_object()
    thread = threading.Thread(target=run_translation_task, args=(app, task_id))
    thread.daemon = True
    thread.start()

    return jsonify({'ok': True, 'message': 'Translation started'})


# ==================== Image API ====================

@api_bp.route('/image/proxy')
def image_proxy():
    """Proxy an image from SUUMO to avoid CORS / referrer issues"""
    url = request.args.get('url', '')
    if not url or 'suumo' not in url:
        return jsonify({'error': 'Invalid image URL'}), 400

    try:
        resp = http_requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://suumo.jp/',
        })
        if resp.status_code != 200:
            return jsonify({'error': f'Image fetch failed: {resp.status_code}'}), 502

        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        return Response(resp.content, mimetype=content_type, headers={
            'Cache-Control': 'public, max-age=86400',
        })
    except Exception as e:
        logger.error(f"Image proxy error: {e}")
        return jsonify({'error': 'Image proxy error'}), 500


@api_bp.route('/image/download/<int:property_id>/<int:image_idx>')
def download_single_image(property_id: int, image_idx: int):
    """Download a single property image"""
    prop = db.session.get(Property, property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    urls = prop.image_urls
    if image_idx < 0 or image_idx >= len(urls):
        return jsonify({'error': 'Image index out of range'}), 400

    url = urls[image_idx]
    try:
        resp = http_requests.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://suumo.jp/',
        })
        if resp.status_code != 200:
            return jsonify({'error': 'Image fetch failed'}), 502

        ext = 'jpg'
        ct = resp.headers.get('Content-Type', '')
        if 'png' in ct:
            ext = 'png'
        elif 'webp' in ct:
            ext = 'webp'

        return Response(resp.content, mimetype=ct, headers={
            'Content-Disposition': f'attachment; filename="property_{property_id}_{image_idx}.{ext}"',
        })
    except Exception as e:
        logger.error(f"Image download error: {e}")
        return jsonify({'error': 'Download error'}), 500


@api_bp.route('/image/zip/<int:property_id>')
def download_images_zip(property_id: int):
    """Download all images for a property as ZIP"""
    prop = db.session.get(Property, property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    urls = prop.image_urls
    if not urls:
        return jsonify({'error': 'No images available'}), 400

    # Build ZIP in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, url in enumerate(urls):
            try:
                resp = http_requests.get(url, timeout=15, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://suumo.jp/',
                })
                if resp.status_code == 200:
                    ext = 'jpg'
                    ct = resp.headers.get('Content-Type', '')
                    if 'png' in ct:
                        ext = 'png'
                    elif 'webp' in ct:
                        ext = 'webp'
                    zf.writestr(f'image_{i:03d}.{ext}', resp.content)
            except Exception as e:
                logger.warning(f"Failed to fetch image {i} for ZIP: {e}")
                continue

    buf.seek(0)
    name = prop.data.get('物件名', f'property_{property_id}')[:30]
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{name}_images.zip',
    )


# ==================== Helper Functions ====================

def format_sse(data: Dict[str, Any]) -> str:
    """Format data for SSE"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def run_scraping_task(app, task_id: str) -> None:
    """Run scraping in background thread"""
    with app.app_context():
        prog = _progress.get(task_id)
        if not prog:
            return

        task = db.session.get(ScrapingTask, task_id)
        if not task:
            return

        task.status = 'running'
        prog['status'] = 'running'
        db.session.commit()

        rental_scraper = SuumoScraper()
        buy_scraper = BuyScraper()

        try:
            for i, url in enumerate(prog['urls']):
                prog['current_url'] = url
                prog['progress'] = i

                try:
                    # Choose scraper based on URL
                    if BuyScraper.is_buy_url(url):
                        result = buy_scraper.scrape_property(url)
                    else:
                        result = rental_scraper.scrape_property(url)

                    if result:
                        prop = Property(
                            task_id=task_id,
                            url=url,
                        )
                        prop.data = result.to_dict()
                        prop.image_urls = result.image_urls
                        db.session.add(prop)
                        db.session.commit()
                    else:
                        prog['errors'].append({
                            'url': url,
                            'error': 'Could not get property info'
                        })
                except (SuumoScraperError, BuyScraperError) as e:
                    prog['errors'].append({
                        'url': url,
                        'error': str(e)
                    })
                except Exception as e:
                    logger.error(f"Scraping error for {url}: {e}")
                    prog['errors'].append({
                        'url': url,
                        'error': 'Unexpected error occurred'
                    })

                # Wait except for last URL
                if i < len(prog['urls']) - 1:
                    time.sleep(prog['delay'])

            task.status = 'completed'
            task.errors = prog['errors']
            task.completed_at = datetime.now(timezone.utc)
            prog['status'] = 'completed'
            prog['progress'] = len(prog['urls'])
            db.session.commit()

            # Auto-translate if LLM is configured
            settings = Settings.get()
            if settings.llm_base_url and settings.llm_api_key:
                run_translation_task(app, task_id)

        except Exception as e:
            logger.error(f"Task error: {e}")
            task.status = 'failed'
            task.errors = prog['errors'] + [{'url': '', 'error': f'Task error: {e}'}]
            prog['status'] = 'error'
            prog['errors'].append({'url': '', 'error': f'Task error: {e}'})
            db.session.commit()

        finally:
            task.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            prog['current_url'] = ''
            # Clean up progress after a delay (keep for SSE to pick up final state)
            def cleanup():
                time.sleep(30)
                with _progress_lock:
                    _progress.pop(task_id, None)
            threading.Thread(target=cleanup, daemon=True).start()


def run_translation_task(app, task_id: str) -> None:
    """Run translation in background thread"""
    with app.app_context():
        settings = Settings.get()
        if not settings.llm_base_url or not settings.llm_api_key:
            return

        try:
            from app.services.translator import TranslatorService
            translator = TranslatorService(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
            )

            properties = Property.query.filter_by(task_id=task_id).all()
            for prop in properties:
                if prop.translated_json:
                    continue  # Already translated

                try:
                    translated = translator.translate_property(prop.data)
                    prop.translated = translated
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Translation error for property {prop.id}: {e}")
                    continue

        except ImportError:
            logger.warning("TranslatorService not available yet")
        except Exception as e:
            logger.error(f"Translation task error: {e}")


def run_search_scrape_task(app, task_id: str, search_url: str, max_results: int, delay: int) -> None:
    """Collect URLs from search result page, then scrape each property."""
    with app.app_context():
        prog = _progress.get(task_id)
        if not prog:
            return

        task = db.session.get(ScrapingTask, task_id)
        if not task:
            return

        task.status = 'running'
        prog['status'] = 'collecting'
        prog['phase'] = 'collecting'
        db.session.commit()

        # Phase 1: Collect URLs from search pages
        try:
            parser = SuumoSearchParser(max_results=max_results, delay=delay)

            def on_progress(count, page):
                prog['progress'] = count
                prog['current_url'] = f'Page {page} ({count} URLs collected)'

            urls = parser.collect_urls(search_url, progress_callback=on_progress)

            if not urls:
                task.status = 'failed'
                task.errors = [{'url': search_url, 'error': 'No property URLs found'}]
                prog['status'] = 'error'
                prog['errors'].append({'url': search_url, 'error': 'No property URLs found'})
                db.session.commit()
                return

            prog['urls'] = urls
            prog['total'] = len(urls)
            task.total = len(urls)
            db.session.commit()

        except SearchParserError as e:
            task.status = 'failed'
            task.errors = [{'url': search_url, 'error': str(e)}]
            prog['status'] = 'error'
            prog['errors'].append({'url': search_url, 'error': str(e)})
            db.session.commit()
            return

        # Phase 2: Scrape each URL
        prog['phase'] = 'scraping'
        prog['status'] = 'running'
        prog['progress'] = 0

        rental_scraper = SuumoScraper()
        buy_scraper = BuyScraper()

        try:
            for i, url in enumerate(urls):
                prog['current_url'] = url
                prog['progress'] = i

                try:
                    if BuyScraper.is_buy_url(url):
                        result = buy_scraper.scrape_property(url)
                    else:
                        result = rental_scraper.scrape_property(url)

                    if result:
                        prop = Property(task_id=task_id, url=url)
                        prop.data = result.to_dict()
                        prop.image_urls = result.image_urls
                        db.session.add(prop)
                        db.session.commit()
                    else:
                        prog['errors'].append({'url': url, 'error': 'Could not get property info'})
                except (SuumoScraperError, BuyScraperError) as e:
                    prog['errors'].append({'url': url, 'error': str(e)})
                except Exception as e:
                    logger.error(f"Scraping error for {url}: {e}")
                    prog['errors'].append({'url': url, 'error': 'Unexpected error'})

                if i < len(urls) - 1:
                    time.sleep(delay)

            task.status = 'completed'
            task.errors = prog['errors']
            task.completed_at = datetime.now(timezone.utc)
            prog['status'] = 'completed'
            prog['progress'] = len(urls)
            db.session.commit()

            # Auto-translate
            settings = Settings.get()
            if settings.llm_base_url and settings.llm_api_key:
                run_translation_task(app, task_id)

        except Exception as e:
            logger.error(f"Search scrape task error: {e}")
            task.status = 'failed'
            task.errors = prog['errors'] + [{'url': '', 'error': f'Task error: {e}'}]
            prog['status'] = 'error'
            db.session.commit()
        finally:
            task.completed_at = datetime.now(timezone.utc)
            db.session.commit()
            prog['current_url'] = ''

            def cleanup():
                time.sleep(30)
                with _progress_lock:
                    _progress.pop(task_id, None)
            threading.Thread(target=cleanup, daemon=True).start()
