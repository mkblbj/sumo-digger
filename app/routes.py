"""Flask routes definition"""

import io
import os
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
from app.schema.property_types import PropertyType
from app.schema.mapper import FieldMapper, detect_property_type

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


@main_bp.route('/blueprint')
def blueprint_page():
    """Blueprint PDF analysis page"""
    return render_template('blueprint.html')


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
    """List all scraping tasks, with live progress for running ones. Supports pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    per_page = min(per_page, 50)  # cap

    query = ScrapingTask.query.order_by(ScrapingTask.created_at.desc())
    total = query.count()
    tasks = query.offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for t in tasks:
        d = t.to_dict()
        # Attach live progress from in-memory tracker if running
        if t.id in _progress:
            prog = _progress[t.id]
            d['progress'] = prog['progress']
            d['current_url'] = prog.get('current_url', '')
            d['phase'] = prog.get('phase', '')
            d['status'] = prog['status']
        result.append(d)
    return jsonify({'tasks': result, 'total': total, 'page': page, 'per_page': per_page, 'total_pages': -(-total // per_page)})


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
    """Get properties for a task. Supports pagination via page/per_page params.
    per_page=0 returns all properties (used by inline results on index page).
    """
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = task.properties
    total = query.count()

    if per_page <= 0:
        # Return all (no pagination)
        properties = query.all()
        page = 1
        total_pages = 1
    else:
        per_page = min(per_page, 200)
        total_pages = max(1, -(-total // per_page))
        page = min(page, total_pages)
        properties = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'task': task.to_dict(),
        'properties': [p.to_dict(bilingual=True) for p in properties],
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
    })


@api_bp.route('/properties/<task_id>/sectioned')
def get_properties_sectioned(task_id: str):
    """Get properties organized by customer field sections."""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)

    query = task.properties
    total = query.count()

    if per_page <= 0:
        properties = query.all()
        page = 1
        total_pages = 1
    else:
        per_page = min(per_page, 200)
        total_pages = max(1, -(-total // per_page))
        page = min(page, total_pages)
        properties = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify({
        'task': task.to_dict(),
        'properties': [p.to_sectioned_dict() for p in properties],
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
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
    if 'llm_provider' in data:
        settings.llm_provider = data['llm_provider'].strip()

    db.session.commit()
    return jsonify(settings.to_dict())


@api_bp.route('/settings/test', methods=['POST'])
def test_llm_connection():
    """Test LLM API connection"""
    settings = Settings.get()
    if not settings.llm_api_key:
        return jsonify({'error': 'LLM API not configured'}), 400

    try:
        from app.services.llm_client import get_llm_client
        client = get_llm_client(settings)
        ok, reply = client.test_connection()
        if ok:
            return jsonify({'ok': True, 'reply': reply, 'provider': client.provider})
        else:
            return jsonify({'error': reply}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Translation API ====================

@api_bp.route('/translate/<task_id>', methods=['POST'])
def translate_task(task_id: str):
    """Trigger translation for a task. Send force=true to clear and re-translate."""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    settings = Settings.get()
    if not settings.llm_api_key:
        return jsonify({'error': 'LLM API not configured. Please set up in Settings page.'}), 400

    # Force re-translate: clear existing translations first
    force = request.json.get('force', False) if request.is_json else False
    if force:
        properties = Property.query.filter_by(task_id=task_id).all()
        for prop in properties:
            prop.translated_json = None
        db.session.commit()
        logger.info(f"Cleared translations for task {task_id} ({len(properties)} properties)")

    # Run translation in background
    app = current_app._get_current_object()
    thread = threading.Thread(target=run_translation_task, args=(app, task_id))
    thread.daemon = True
    thread.start()

    return jsonify({'ok': True, 'message': 'Translation started', 'force': force})


@api_bp.route('/translate/property/<int:property_id>', methods=['POST'])
def translate_single_property(property_id: int):
    """Translate a single property synchronously and return the result."""
    prop = db.session.get(Property, property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    settings = Settings.get()
    if not settings.llm_api_key:
        return jsonify({'error': 'LLM API not configured. Please set up in Settings page.'}), 400

    force = request.json.get('force', False) if request.is_json else False

    # Return cached translation if available
    if prop.translated_json and not force:
        return jsonify({'ok': True, 'property': prop.to_dict(bilingual=True), 'cached': True})

    # Clear existing translation if forcing
    if force:
        prop.translated_json = None
        db.session.commit()

    try:
        from app.services.translator import TranslatorService
        from app.services.llm_client import get_llm_client
        translator = TranslatorService(llm_client=get_llm_client(settings))
        translated = translator.translate_property(prop.data)
        if translated is not None:
            prop.translated = translated
            db.session.commit()
        return jsonify({'ok': True, 'property': prop.to_dict(bilingual=True), 'cached': False})
    except Exception as e:
        logger.error(f"Single property translation error: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== AI Summary API ====================

def _generate_property_summary_items(prop: Property, summary_type: str):
    settings = Settings.get()
    if not settings.llm_api_key:
        raise ValueError('LLM API not configured')

    data = prop.data
    skip = {'_id', '_url', '_has_translation', '_image_urls', '_translated', 'URL', '物件画像'}
    prop_text = '\n'.join(f'{k}: {v}' for k, v in data.items() if k not in skip and v)

    if summary_type == 'title':
        system = (
            "你是日本房产信息编辑。根据物件信息生成3个适合中国客户阅读的中文标题候选。\n"
            "规则：\n"
            "- 返回 JSON 数组，例如 [\"标题1\", \"标题2\", \"标题3\"]\n"
            "- 每个标题 20-40 字左右，不要标点符号\n"
            "- 包含：地区/站名 + 户型 + 核心卖点\n"
            "- 地名/站名保留日文\n"
            "- 不要返回解释文字"
        )
    else:
        system = (
            "你是日本房产信息编辑。根据物件信息写3个版本的简体中文介绍短文。\n"
            "规则：\n"
            "- 返回 JSON 数组，例如 [\"版本1\", \"版本2\", \"版本3\"]\n"
            "- 每个版本 120-220 字\n"
            "- 涵盖：位置交通、房屋基本情况、费用、设备亮点\n"
            "- 地名/站名保留日文原文\n"
            "- 语气专业简洁，适合房产发布\n"
            "- 不要返回解释文字"
        )

    from app.services.llm_client import get_llm_client, extract_json
    client = get_llm_client(settings)
    text = client.chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prop_text},
        ],
        temperature=0.5,
        max_tokens=2048,
    ).strip()
    parsed = extract_json(text)
    if isinstance(parsed, list) and parsed:
        items = [str(item).strip() for item in parsed if str(item).strip()][:3]
        if items:
            return items
    return [text]


@api_bp.route('/summarize/property/<int:property_id>', methods=['POST'])
def summarize_property(property_id: int):
    """Generate AI title or short summary for a property."""
    prop = db.session.get(Property, property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    summary_type = 'title'
    if request.is_json:
        summary_type = request.json.get('type', 'title')

    try:
        items = _generate_property_summary_items(prop, summary_type)
        return jsonify({'ok': True, 'type': summary_type, 'items': items[:3], 'text': items[0]})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"AI summary error: {e}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/property/<int:property_id>/detail-assets', methods=['POST'])
def property_detail_assets(property_id: int):
    """Ensure translation and pre-generate title/summary candidates for detail page."""
    prop = db.session.get(Property, property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    force_translate = request.json.get('force_translate', False) if request.is_json else False

    translated = prop.translated
    if force_translate or not translated:
        settings = Settings.get()
        if settings.llm_api_key:
            try:
                from app.services.translator import TranslatorService
                from app.services.llm_client import get_llm_client
                translator = TranslatorService(llm_client=get_llm_client(settings))
                translated = translator.translate_property(prop.data)
                if translated is not None:
                    prop.translated = translated
                    db.session.commit()
            except Exception as e:
                logger.error(f"Detail asset translation error: {e}")

    try:
        title_items = _generate_property_summary_items(prop, 'title')
        summary_items = _generate_property_summary_items(prop, 'summary')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Detail asset summary error: {e}")
        return jsonify({'error': str(e)}), 500

    refreshed = db.session.get(Property, property_id)
    return jsonify({
        'ok': True,
        'property': refreshed.to_sectioned_dict(),
        'title_items': title_items[:3],
        'summary_items': summary_items[:3],
    })


# ==================== AI Enrichment API ====================

@api_bp.route('/enrich/<task_id>', methods=['POST'])
def enrich_task(task_id: str):
    """Run AI enrichment on all properties in a task (exchange rate, taxes, descriptions, etc.)."""
    task = db.session.get(ScrapingTask, task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    settings = Settings.get()
    llm_client = None
    if settings.llm_api_key:
        from app.services.llm_client import get_llm_client
        llm_client = get_llm_client(settings)

    app = current_app._get_current_object()
    thread = threading.Thread(target=run_enrichment_task, args=(app, task_id, llm_client))
    thread.daemon = True
    thread.start()

    return jsonify({'ok': True, 'message': 'Enrichment started'})


@api_bp.route('/enrich/property/<int:property_id>', methods=['POST'])
def enrich_single_property(property_id: int):
    """Run AI enrichment on a single property synchronously."""
    prop = db.session.get(Property, property_id)
    if not prop:
        return jsonify({'error': 'Property not found'}), 404

    settings = Settings.get()
    llm_client = None
    if settings.llm_api_key:
        from app.services.llm_client import get_llm_client
        llm_client = get_llm_client(settings)

    try:
        from app.services.ai_enrichment import AIEnrichmentService
        service = AIEnrichmentService(llm_client=llm_client)
        data = dict(prop.data)
        ptype_str = data.get('_property_type', '')
        ptype = PropertyType.OTHER
        for pt in PropertyType:
            if pt.value == ptype_str:
                ptype = pt
                break
        enriched = service.enrich(data, ptype)
        prop.data = enriched
        db.session.commit()
        return jsonify({'ok': True, 'property': prop.to_dict(bilingual=True)})
    except Exception as e:
        logger.error(f"Enrichment error: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== Blueprint PDF API ====================

@api_bp.route('/blueprint/upload', methods=['POST'])
def upload_blueprint():
    """Upload a blueprint PDF for analysis."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are accepted'}), 400

    settings = Settings.get()
    if not settings.llm_api_key:
        return jsonify({'error': 'LLM API not configured. Please set up in Settings page.'}), 400

    pdf_bytes = file.read()
    if len(pdf_bytes) > current_app.config.get('MAX_CONTENT_LENGTH', 50 * 1024 * 1024):
        return jsonify({'error': 'File too large (max 50MB)'}), 400

    task_id = str(uuid.uuid4())
    task = ScrapingTask(
        id=task_id,
        status='pending',
        property_type='blueprint',
        total=0,
    )
    db.session.add(task)
    db.session.commit()

    with _progress_lock:
        _progress[task_id] = {
            'status': 'pending',
            'phase': 'uploading',
            'progress': 0,
            'total': 0,
            'current_url': file.filename,
            'errors': [],
            'urls': [],
            'delay': 0,
        }

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=run_blueprint_task,
        args=(app, task_id, pdf_bytes, file.filename)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id, 'filename': file.filename})


@api_bp.route('/blueprint/floor_plan/<path:filename>')
def serve_floor_plan(filename):
    """Serve a floor plan image."""
    upload_folder = current_app.config.get('UPLOAD_FOLDER', 'uploads')
    floor_plan_dir = os.path.join(upload_folder, 'floor_plans')
    filepath = os.path.join(floor_plan_dir, filename)

    if not os.path.isfile(filepath):
        return jsonify({'error': 'Floor plan not found'}), 404

    return send_file(filepath)


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
        mapper = FieldMapper()

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
                        raw = result.to_dict()
                        ptype = detect_property_type(url, raw)
                        normalized = mapper.normalize(raw, ptype, source_url=url)

                        prop = Property(
                            task_id=task_id,
                            url=url,
                        )
                        prop.data = normalized
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
        if not settings.llm_api_key:
            return

        try:
            from app.services.translator import TranslatorService
            from app.services.llm_client import get_llm_client
            translator = TranslatorService(llm_client=get_llm_client(settings))

            properties = Property.query.filter_by(task_id=task_id).all()
            for prop in properties:
                if prop.translated_json:
                    continue  # Already translated

                try:
                    translated = translator.translate_property(prop.data)
                    if translated is not None:
                        prop.translated = translated
                        db.session.commit()
                        logger.info(f"Property {prop.id} translated successfully")
                    else:
                        logger.warning(f"Property {prop.id}: translation returned None (no changes)")
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
        mapper = FieldMapper()

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
                        raw = result.to_dict()
                        ptype = detect_property_type(url, raw)
                        normalized = mapper.normalize(raw, ptype, source_url=url)

                        prop = Property(task_id=task_id, url=url)
                        prop.data = normalized
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


def run_blueprint_task(app, task_id: str, pdf_bytes: bytes, filename: str) -> None:
    """Analyze a blueprint PDF in a background thread."""
    with app.app_context():
        prog = _progress.get(task_id)
        if not prog:
            return

        task = db.session.get(ScrapingTask, task_id)
        if not task:
            return

        task.status = 'running'
        prog['status'] = 'running'
        prog['phase'] = 'analyzing'
        db.session.commit()

        try:
            from app.services.llm_client import get_llm_client
            from app.services.pdf_analyzer import BlueprintAnalyzer

            settings = Settings.get()
            llm_client = get_llm_client(settings)
            upload_folder = app.config.get('UPLOAD_FOLDER', 'uploads')
            analyzer = BlueprintAnalyzer(llm_client, upload_folder)

            def on_progress(current, total, message):
                prog['progress'] = current
                prog['total'] = total
                prog['current_url'] = message
                task.total = total
                try:
                    db.session.commit()
                except Exception:
                    pass

            properties = analyzer.analyze_pdf(pdf_bytes, filename, progress_cb=on_progress)

            from app.services.ai_enrichment import AIEnrichmentService

            mapper = FieldMapper()
            enrich_service = AIEnrichmentService(llm_client=llm_client)
            for prop in properties:
                raw = BlueprintAnalyzer.property_to_dict(prop)
                ptype = detect_property_type('', raw)
                normalized = mapper.normalize(raw, ptype, source_url=f'blueprint://{filename}')
                normalized = enrich_service.enrich(normalized, ptype)

                db_prop = Property(
                    task_id=task_id,
                    url=f'blueprint://{filename}',
                )
                db_prop.data = normalized
                floor_plan_urls = [
                    f'/api/blueprint/floor_plan/{os.path.basename(p)}'
                    for p in prop.floor_plan_paths
                ]
                db_prop.image_urls = floor_plan_urls
                db.session.add(db_prop)

            task.status = 'completed'
            task.total = len(properties)
            task.completed_at = datetime.now(timezone.utc)
            task.errors = prog['errors']
            prog['status'] = 'completed'
            prog['progress'] = len(properties)
            prog['total'] = len(properties)
            db.session.commit()

            logger.info(f"Blueprint task {task_id} completed: {len(properties)} properties from {filename}")

        except Exception as e:
            logger.error(f"Blueprint task error: {e}", exc_info=True)
            task.status = 'failed'
            task.errors = [{'url': filename, 'error': str(e)}]
            task.completed_at = datetime.now(timezone.utc)
            prog['status'] = 'error'
            prog['errors'].append({'url': filename, 'error': str(e)})
            db.session.commit()

        finally:
            prog['current_url'] = ''

            def cleanup():
                time.sleep(30)
                with _progress_lock:
                    _progress.pop(task_id, None)
            threading.Thread(target=cleanup, daemon=True).start()


def run_enrichment_task(app, task_id: str, llm_client=None) -> None:
    """Run AI enrichment on all properties in a task."""
    with app.app_context():
        try:
            from app.services.ai_enrichment import AIEnrichmentService
            service = AIEnrichmentService(llm_client=llm_client)

            properties = Property.query.filter_by(task_id=task_id).all()
            for prop in properties:
                try:
                    data = dict(prop.data)
                    ptype_str = data.get('_property_type', '')
                    ptype = PropertyType.OTHER
                    for pt in PropertyType:
                        if pt.value == ptype_str:
                            ptype = pt
                            break
                    enriched = service.enrich(data, ptype)
                    prop.data = enriched
                    db.session.commit()
                    logger.info(f"Property {prop.id} enriched successfully")
                except Exception as e:
                    logger.error(f"Enrichment error for property {prop.id}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Enrichment task error: {e}")
