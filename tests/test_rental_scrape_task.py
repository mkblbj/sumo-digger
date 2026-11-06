"""租房抓取任务：详情抓取失败的原因带类型返回（gone 已下架 / temporary 暂时访问失败 / structure 页面结构变更）。"""

import pytest

from app import create_app
from app.models import ScrapingTask, db
from app.routes import _progress, run_scraping_task
from app.scraper.suumo import SuumoScraper, SuumoScraperError

GONE_URL = 'https://suumo.jp/chintai/jnc_000110800080/?bc=100534661075'


@pytest.fixture
def app(monkeypatch):
    app = create_app('testing')
    monkeypatch.setattr('app.routes._get_enrichment_service', lambda: None)
    monkeypatch.setattr('app.routes.time.sleep', lambda *_: None)
    return app


def test_rental_scrape_error_keeps_its_type(app, monkeypatch):
    def delisted(self, url):
        raise SuumoScraperError('房源已下架或不存在（HTTP 301）', 'gone')

    monkeypatch.setattr(SuumoScraper, 'scrape_property', delisted)
    with app.app_context():
        db.session.add(ScrapingTask(id='t-rental', status='pending', property_type='rental', total=1))
        db.session.commit()
    _progress['t-rental'] = {
        'status': 'pending', 'urls': [GONE_URL], 'search_urls': [], 'delay': 1,
        'max_results': None, 'progress': 0, 'total': 1, 'current_url': '', 'errors': [],
        'phase': 'scraping',
    }

    run_scraping_task(app, 't-rental')

    with app.app_context():
        task = db.session.get(ScrapingTask, 't-rental')
        assert task.status == 'completed'
        assert task.errors == [
            {'url': GONE_URL, 'error': '房源已下架或不存在（HTTP 301）', 'error_type': 'gone'},
        ]
