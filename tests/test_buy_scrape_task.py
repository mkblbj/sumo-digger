"""买房抓取任务：详情直走详情路径，错误带类型，结果经 /api/properties 原样序列化。"""

import json
import os
from pathlib import Path

import pytest

from app import create_app
from app.models import ScrapingTask, db
from app.routes import _progress, run_scraping_task
from app.scraper.buy_scraper import BuyScraper, BuyScraperError
from app.scraper.search_parser import SuumoSearchParser, SearchParserError

FIXTURES = Path(__file__).parent / 'fixtures' / 'suumo_buy'

PAGES = {
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/': 'ms_chuko.html',
    'https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/nc_21998102/': 'tochi.html',
}


class _Resp:
    status_code = 200

    def __init__(self, content):
        self.content = content


@pytest.fixture
def app(monkeypatch):
    app = create_app('testing')
    monkeypatch.setattr('app.routes._get_enrichment_service', lambda: None)
    monkeypatch.setattr('app.routes.time.sleep', lambda *_: None)
    return app


def _fake_fetch(self, url):
    if url not in PAGES:
        raise BuyScraperError('房源已下架或不存在（HTTP 404）', 'gone')
    return _Resp((FIXTURES / PAGES[url]).read_bytes())


def _run(app, urls, search_urls=()):
    with app.app_context():
        task = ScrapingTask(id='t-buy', status='pending', property_type='buy', total=len(urls))
        db.session.add(task)
        db.session.commit()
    _progress['t-buy'] = {
        'status': 'pending', 'urls': list(urls), 'search_urls': list(search_urls), 'delay': 1,
        'max_results': None, 'progress': 0, 'total': len(urls), 'current_url': '', 'errors': [],
        'phase': 'scraping',
    }
    run_scraping_task(app, 't-buy')


def test_task_output_carries_sale_metadata_and_typed_errors(app, monkeypatch):
    monkeypatch.setattr(BuyScraper, '_fetch', _fake_fetch)
    gone_url = 'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_99999999/'

    _run(app, list(PAGES) + [gone_url])

    client = app.test_client()
    body = client.get('/api/properties/t-buy?per_page=0').get_json()
    props = {p['_source_url']: p for p in body['properties']}
    condo = props['https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/']
    assert condo['basic.price_jpy'] == 59_900_000
    assert condo['_sale']['source_key'] == 'suumo:ms-chuko:21853112'
    assert condo['_image_urls'][0].endswith('/21853112_0001.jpg')
    land = props['https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/nc_21998102/']
    assert land['basic.land_area'] == 158.6
    assert body['task']['status'] == 'completed'
    assert body['task']['errors'] == [{
        'url': gone_url, 'error': '房源已下架或不存在（HTTP 404）', 'error_type': 'gone',
    }]


def test_search_issues_are_reported_with_partial_results(app, monkeypatch):
    monkeypatch.setattr(BuyScraper, '_fetch', _fake_fetch)

    def fake_collect(self, search_url, progress_callback=None):
        if 'sc_taito' in search_url:
            self.issues = [{'url': search_url + '?page=2', 'error': '搜索页暂时访问失败（HTTP 503）',
                            'error_type': 'temporary'}]
            return ['https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/']
        self.issues = []
        raise SearchParserError('搜索页不存在（HTTP 404）', 'gone')

    monkeypatch.setattr(SuumoSearchParser, 'collect_urls', fake_collect)

    _run(app, [], search_urls=[
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/',
        'https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/',
    ])

    with app.app_context():
        task = db.session.get(ScrapingTask, 't-buy')
        assert task.status == 'completed'
        assert task.result_count == 1
        assert task.errors == [
            {'url': 'https://suumo.jp/ms/chuko/tokyo/sc_taito/?page=2',
             'error': '搜索页暂时访问失败（HTTP 503）', 'error_type': 'temporary'},
            {'url': 'https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/',
             'error': '搜索页不存在（HTTP 404）', 'error_type': 'gone'},
        ]


def test_dump_properties_contract_fixture(app, monkeypatch, tmp_path):
    """把真实序列化输出写成 JSON，供日宜找房 suumo-ingest 测试读取同一份契约样本。"""
    monkeypatch.setattr(BuyScraper, '_fetch', _fake_fetch)
    _run(app, list(PAGES))

    body = app.test_client().get('/api/properties/t-buy?per_page=0').get_json()
    contract = FIXTURES / 'properties_contract.json'
    current = json.dumps(body['properties'], ensure_ascii=False, indent=2, sort_keys=True)
    if os.environ.get('SUMO_WRITE_CONTRACT') == '1':
        contract.write_text(current + '\n', encoding='utf-8')
    if not contract.exists() or contract.read_text(encoding='utf-8') != current + '\n':
        pytest.fail(f'契约样本已变化，请用 SUMO_WRITE_CONTRACT=1 重新生成并同步到日宜找房: {contract}')
