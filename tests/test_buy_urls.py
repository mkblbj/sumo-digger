"""SUUMO 买房链接识别、子页归并与搜索页收集。"""

from pathlib import Path

import pytest
import requests

from app import create_app
from app.routes import _classify_urls
from app.scraper.search_parser import SuumoSearchParser
from app.scraper.url_rules import classify_suumo_url

FIXTURES = Path(__file__).parent / 'fixtures' / 'suumo_buy'

BUY_DETAIL_SAMPLES = [
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/',
    'https://suumo.jp/ikkodate/saitama/sc_kitaadachigun/nc_21842968/',
    'https://suumo.jp/chukoikkodate/tokyo/sc_nakano/nc_21983513/',
    'https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/nc_21998102/',
    'https://suumo.jp/ms/shinchiku/tokyo/sc_shinjuku/nc_67735307/property/?rnms=403',
    'https://suumo.jp/ms/chuko/osaka/sc_sakaishiminami/nc_21620138/',
    'https://suumo.jp/chukoikkodate/saitama/sc_irumagun/nc_77577833/',
]


@pytest.mark.parametrize('url', BUY_DETAIL_SAMPLES)
def test_buy_detail_samples_go_to_detail_path(url):
    detail_urls, search_urls, invalid_urls = _classify_urls([url])

    assert search_urls == []
    assert invalid_urls == []
    assert len(detail_urls) == 1


@pytest.mark.parametrize('url', [
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/bukkengaiyo/?fmlg=t001',
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/kankyo/?fmlg=t001',
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/tenpo/',
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/?fmlg=t001#top',
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112',
])
def test_same_property_subpages_fold_into_detail(url):
    info = classify_suumo_url(url)

    assert info.kind == 'buy_detail'
    assert info.url == 'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/'
    assert info.source_key == 'suumo:ms-chuko:21853112'


def test_detail_and_subpage_pasted_together_scrape_once():
    detail_urls, search_urls, _ = _classify_urls([
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/',
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/bukkengaiyo/?fmlg=t001',
    ])

    assert detail_urls == ['https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/']
    assert search_urls == []


def test_source_keys_by_category():
    assert classify_suumo_url(BUY_DETAIL_SAMPLES[3]).source_key == 'suumo:tochi:21998102'
    assert classify_suumo_url(BUY_DETAIL_SAMPLES[2]).source_key == 'suumo:chukoikkodate:21983513'
    assert classify_suumo_url(BUY_DETAIL_SAMPLES[1]).source_key == 'suumo:ikkodate:21842968'


def test_new_condo_keeps_sale_period_identity():
    info = classify_suumo_url(BUY_DETAIL_SAMPLES[4] + '&utm_source=x#categoryL01')

    assert info.kind == 'buy_detail'
    assert info.url == 'https://suumo.jp/ms/shinchiku/tokyo/sc_shinjuku/nc_67735307/property/?rnms=403'
    assert info.source_key == 'suumo:ms-shinchiku:67735307:rnms=403'


@pytest.mark.parametrize('url', [
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/',
    'https://suumo.jp/ms/chuko/tokyo/sc_taito/?page=2',
    'https://suumo.jp/jj/bukken/ichiran/JJ010FJ001/?ar=030&bs=011&ta=13&sc=13106',
])
def test_buy_search_pages_stay_search(url):
    detail_urls, search_urls, invalid_urls = _classify_urls([url])

    assert search_urls == [url]
    assert detail_urls == []
    assert invalid_urls == []


def test_rental_urls_keep_previous_classification():
    detail_urls, search_urls, invalid_urls = _classify_urls([
        'https://suumo.jp/chintai/bc_100/',
        'https://suumo.jp/chintai/jnc_000012345678/?bc=100',
        'https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13',
        'https://example.com/ms/chuko/tokyo/sc_taito/nc_21853112/',
    ])

    assert detail_urls == [
        'https://suumo.jp/chintai/bc_100/',
        'https://suumo.jp/chintai/jnc_000012345678/?bc=100',
    ]
    assert search_urls == ['https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&bs=040&ta=13']
    assert invalid_urls == ['https://example.com/ms/chuko/tokyo/sc_taito/nc_21853112/']


class _FakeResponse:
    def __init__(self, status_code=200, content=b''):
        self.status_code = status_code
        self.content = content


class _FakeSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requested = []
        self.headers = {}

    def get(self, url, timeout=None):
        self.requested.append(url)
        page = self.pages.pop(0) if self.pages else _FakeResponse(200, b'<html></html>')
        if isinstance(page, Exception):
            raise page
        return page


def _search_html():
    return (FIXTURES / 'search_ms_chuko_taito.html').read_bytes()


def test_search_page_collects_only_result_cards_in_order():
    parser = SuumoSearchParser(delay=0)
    parser.session = _FakeSession([_FakeResponse(200, _search_html()), _FakeResponse(200, _search_html())])

    urls = parser.collect_urls('https://suumo.jp/ms/chuko/tokyo/sc_taito/')

    assert urls == [
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_22007163/',
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_22083654/',
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_22003124/',
    ]
    # 第二页与第一页完全重复 → 视为重复页停止，不再继续翻页
    assert parser.session.requested == [
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/',
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/?page=2',
    ]


def test_search_pagination_keeps_filters_and_stops_at_limit():
    parser = SuumoSearchParser(delay=0, max_results=2)
    parser.session = _FakeSession([_FakeResponse(200, _search_html())])

    urls = parser.collect_urls('https://suumo.jp/ms/chuko/tokyo/sc_taito/?kb=1&kt=9999999&page=1')

    assert len(urls) == 2
    assert parser.session.requested == ['https://suumo.jp/ms/chuko/tokyo/sc_taito/?kb=1&kt=9999999']
    assert parser._build_page_url('https://suumo.jp/ms/chuko/tokyo/sc_taito/?kb=1&kt=9999999', 3) == (
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/?kb=1&kt=9999999&page=3'
    )


def test_search_detail_page_markup_does_not_yield_subpages():
    detail_like = b'''<html><body>
      <a href="https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/kankyo/?fmlg=t001">kankyo</a>
      <a href="https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/bukkengaiyo/?fmlg=t001">gaiyo</a>
    </body></html>'''
    parser = SuumoSearchParser(delay=0)
    parser.session = _FakeSession([_FakeResponse(200, detail_like)])

    urls = parser.collect_urls('https://suumo.jp/ms/chuko/tokyo/sc_taito/')

    assert urls == []
    assert parser.issues == [{
        'url': 'https://suumo.jp/ms/chuko/tokyo/sc_taito/',
        'error': '搜索页未找到结果卡片，页面结构可能已变更',
        'error_type': 'structure',
    }]


def test_search_empty_result_is_reported_as_empty():
    empty = '<html><body><div class="pagination_set-hit">0 件</div></body></html>'.encode('utf-8')
    parser = SuumoSearchParser(delay=0)
    parser.session = _FakeSession([_FakeResponse(200, empty)])

    assert parser.collect_urls('https://suumo.jp/ms/chuko/tokyo/sc_taito/?kb=1') == []
    assert parser.issues[0]['error_type'] == 'empty'


def test_search_temporary_failure_retries_then_keeps_collected_pages():
    parser = SuumoSearchParser(delay=0)
    parser.session = _FakeSession([
        _FakeResponse(200, _search_html()),
        _FakeResponse(503),
        requests.ConnectionError('reset'),
        _FakeResponse(503),
    ])

    urls = parser.collect_urls('https://suumo.jp/ms/chuko/tokyo/sc_taito/')

    assert len(urls) == 3
    assert len(parser.session.requested) == 4
    assert parser.issues[-1]['error_type'] == 'temporary'
    assert parser.issues[-1]['url'] == 'https://suumo.jp/ms/chuko/tokyo/sc_taito/?page=2'


def test_search_first_page_gone_raises_with_type():
    from app.scraper.search_parser import SearchParserError

    parser = SuumoSearchParser(delay=0)
    parser.session = _FakeSession([_FakeResponse(404)])

    with pytest.raises(SearchParserError) as exc:
        parser.collect_urls('https://suumo.jp/ms/chuko/tokyo/sc_taito/')
    assert exc.value.error_type == 'gone'


def test_scrape_api_accepts_buy_detail_without_search_phase(monkeypatch):
    started = []

    class _NoThread:
        def __init__(self, target=None, args=()):
            started.append(args)
            self.daemon = True

        def start(self):
            pass

    monkeypatch.setattr('app.routes.threading.Thread', _NoThread)
    app = create_app('testing')
    client = app.test_client()

    resp = client.post('/api/scrape', json={'urls': [
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/',
        'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/bukkengaiyo/?fmlg=t001',
    ]})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['total'] == 1
    assert body['search_count'] == 0
    assert len(started) == 1


def test_rental_empty_search_without_hit_counter_is_empty():
    page = '<html><body><p>条件に該当する物件はありませんでした。</p></body></html>'.encode('utf-8')
    parser = SuumoSearchParser(delay=0)
    parser.session = _FakeSession([_FakeResponse(200, page)])

    assert parser.collect_urls('https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030') == []
    assert parser.issues[0]['error_type'] == 'empty'


def test_status_result_count_ignores_search_level_issues():
    from app.routes import _progress

    app = create_app('testing')
    _progress['t-count'] = {
        'status': 'running', 'phase': 'scraping', 'progress': 2, 'total': 2, 'current_url': '',
        'urls': ['https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_1/', 'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_2/'],
        'errors': [
            {'url': 'https://suumo.jp/ms/chuko/tokyo/sc_taito/?page=2', 'error': 'x', 'error_type': 'temporary'},
            {'url': 'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_2/', 'error': 'y', 'error_type': 'gone'},
        ],
    }
    try:
        body = app.test_client().get('/api/scrape/status/t-count').get_json()
    finally:
        _progress.pop('t-count', None)
    assert body['result_count'] == 1
