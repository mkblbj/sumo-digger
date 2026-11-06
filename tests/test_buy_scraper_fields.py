"""买房详情：按真实页面结构抽取售价、面积、楼层、月费、土地条件与图片。

期望值来自对应 SUUMO 公开页面的人工核对（见 docs 买房优化方案的样本表）。
"""

from pathlib import Path

import pytest
import requests

from app.schema.buy_fields import parse_monthly_fee, parse_price_yen
from app.schema.mapper import FieldMapper, detect_property_type
from app.scraper.buy_scraper import BuyScraper, BuyScraperError

FIXTURES = Path(__file__).parent / 'fixtures' / 'suumo_buy'

MS_CHUKO_URL = 'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/'
IKKODATE_URL = 'https://suumo.jp/ikkodate/saitama/sc_kitaadachigun/nc_21842968/'
CHUKO_IKKODATE_URL = 'https://suumo.jp/chukoikkodate/tokyo/sc_nakano/nc_21983513/'
TOCHI_URL = 'https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/nc_21998102/'


class _Resp:
    def __init__(self, status_code=200, content=b''):
        self.status_code = status_code
        self.content = content


def _redirect(location, status_code=301):
    resp = requests.Response()
    resp.status_code = status_code
    resp.headers['Location'] = location
    return resp


class _Session:
    """按顺序返回响应。允许跟随跳转时像线上一样：跳去 http://suumo.jp（80 端口）连接超时。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=True):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if allow_redirects and getattr(item, 'is_redirect', False):
            raise requests.ConnectTimeout("HTTPConnectionPool(host='suumo.jp', port=80): connect timeout")
        return item


def _scrape(fixture, url):
    scraper = BuyScraper(retry_delay=0)
    scraper.session = _Session([_Resp(200, (FIXTURES / fixture).read_bytes())])
    result = scraper.scrape_property(url)
    raw = result.to_dict()
    ptype = detect_property_type(url, raw)
    data = FieldMapper().normalize(raw, ptype, source_url=url)
    return result, data


def _strings(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


@pytest.mark.parametrize('text,expected', [
    ('5990万円', 59_900_000),
    ('1億2,500万円', 125_000_000),
    ('1億円', 100_000_000),
    ('8,980万円', 89_800_000),
    ('980万円（税込）', 9_800_000),
])
def test_parse_fixed_prices(text, expected):
    assert parse_price_yen(text) == ('fixed', expected)


@pytest.mark.parametrize('text,status', [
    ('未定', 'undecided'),
    ('価格未定', 'undecided'),
    ('3998万円～4298万円', 'range'),
    ('4000万円台', 'range'),
    ('', 'missing'),
    ('-', 'missing'),
])
def test_parse_non_fixed_prices(text, status):
    assert parse_price_yen(text) == (status, None)


@pytest.mark.parametrize('text,expected', [
    ('2万800円／月', 20_800),
    ('8300円／月（委託(巡回)）', 8_300),
    ('1万円/月', 10_000),
    ('無', 0),
    ('なし', 0),
    ('-', None),
    ('未定', None),
    ('', None),
])
def test_parse_monthly_fees(text, expected):
    assert parse_monthly_fee(text) == expected


def test_used_condo_fields_match_listing():
    result, data = _scrape('ms_chuko.html', MS_CHUKO_URL)

    assert data['_property_type'] == '买卖-公寓塔楼'
    assert data['basic.price_jpy'] == 59_900_000
    assert data['basic.area'] == 35.67
    assert data['detail.exclusive_area'] == 35.67
    assert data['detail.floor'] == 9
    assert data['detail.total_floors'] == 11
    assert data['detail.structure'] == 'RC造 钢筋混凝土造'
    assert data['analysis_or_detail.management_fee'] == 8_300
    assert data['analysis_or_detail.repair_fee'] == 20_800
    # 页面只写「委託(巡回)」，未区分全部/部分委托：不推断，留给审核人选择
    assert data['management.method'] is None
    assert '管理方式：委託(巡回)（未注明全部或部分委托）' in data['_sale']['review_notes']
    assert data['basic.layout_raw'] == '1LDK'
    assert data['basic.layout_cn'] == '一居室'
    assert data['basic.built_month'] == '2006年1月'
    assert data['detail.orientation'] == '南'
    assert data['detail.total_units'] == 26
    assert data['land.rights'] == '所有权'
    assert data['land.zoning'] == '商业地区'
    assert data['deal.delivery_text'] == '即可交付'
    assert data['basic.address'] == '東京都台東区蔵前４'
    assert data['detail.building_name'] == 'サンソフィア蔵前'
    assert data['detail.access'] == [
        '都営浅草線「蔵前」歩5分',
        '東京メトロ銀座線「田原町」歩9分',
        'ＪＲ総武線「浅草橋」歩13分',
    ]
    assert data['basic.unit_price'] == round(59_900_000 / 35.67)
    assert not any('ヒント' in s or '乗り換え案内' in s or '支払シミュレーション' in s for s in _strings(data))


def test_used_condo_images_come_from_listing_gallery():
    result, data = _scrape('ms_chuko.html', MS_CHUKO_URL)

    images = result.image_urls
    assert len(images) >= 10
    assert images[0] == 'https://img01.suumo.com/front/gazo/bukken/030/N010000/img/112/21853112/21853112_0001.jpg'
    assert all(u.startswith('https://img01.suumo.com/front/gazo/bukken/') and '/21853112/' in u for u in images)
    assert len(images) == len(set(images))


def test_used_condo_sale_metadata():
    _, data = _scrape('ms_chuko.html', MS_CHUKO_URL)

    sale = data['_sale']
    assert sale['version'] == 1
    assert sale['source_key'] == 'suumo:ms-chuko:21853112'
    assert sale['granularity'] == 'unit'
    assert sale['price'] == {'status': 'fixed', 'raw': '5990万円', 'jpy': 59_900_000}
    assert sale['area'] == {'status': 'fixed', 'basis': 'exclusive', 'raw': '35.67m²（壁芯）', 'sqm': 35.67}


def test_new_detached_house_land_and_building_areas_separate():
    _, data = _scrape('ikkodate_new.html', IKKODATE_URL)

    assert data['_property_type'] == '买卖-一户建'
    assert data['basic.price_jpy'] == 39_980_000
    assert data['basic.land_area'] == 120.39
    assert data['basic.building_area'] == 96.88
    assert data['basic.layout_raw'] == '4LDK'
    assert data['detail.structure'] == 'W造 木造'
    assert data['detail.total_floors'] == 2
    assert data.get('detail.floor') is None
    assert data['land.building_coverage_pct'] == 60
    assert data['land.far_pct'] == 80
    assert data['land.zoning'] == '第一种低层住宅专用地区'
    assert data['land.rights'] == '所有权'
    assert data['basic.built_month'] == '2026年10月下旬'
    assert data['deal.delivery_text'] == '其他'
    assert data['deal.delivery_time'] == '2026年11月下旬予定'
    assert data['detail.building_name'] == '内宿台４（内宿駅）'
    assert data['basic.unit_price'] == round(39_980_000 / 96.88)
    assert data['_sale']['granularity'] == 'unit'
    assert data['_sale']['area']['basis'] == 'building'


def test_used_detached_house_fields():
    result, data = _scrape('chukoikkodate.html', CHUKO_IKKODATE_URL)

    assert data['_property_type'] == '买卖-一户建'
    assert data['basic.price_jpy'] == 89_800_000
    assert data['basic.land_area'] == 83.13
    assert data['basic.building_area'] == 85.28
    assert data['basic.built_month'] == '2004年3月'
    assert data['land.building_coverage_pct'] == 60
    assert data['land.far_pct'] == 188
    assert data['land.zoning'] == '第一种中高层住宅专用地区'
    assert data['land.restrictions'] == '準防火地域'
    assert data['deal.delivery_text'] == '协商'
    assert data['detail.building_name'] == '中野区上高田４丁目中古戸建'
    assert len(result.image_urls) >= 10


def test_land_fields_match_listing():
    _, data = _scrape('tochi.html', TOCHI_URL)

    assert data['_property_type'] == '买卖-土地'
    assert data['basic.price_jpy'] == 44_800_000
    assert data['basic.land_area'] == 158.6
    assert data['land.rights'] == '所有权'
    assert data['land.building_coverage_pct'] == 50
    assert data['land.far_pct'] == 80
    assert data['land.zoning'] == '第一种低层住宅专用地区'
    assert data['land.private_road_burden'] == '無、南4ｍ幅（接道幅10.1ｍ）'
    assert data['land.restrictions'] == '高度地区、建築基準法第22条区域'
    assert data['detail.land_or_invest_status'] == '附带老屋'
    assert data['deal.delivery_text'] == '协商'
    assert data['basic.unit_price'] == round(44_800_000 / 158.6)
    assert data['_sale']['granularity'] == 'lot'
    assert data['detail.total_lots'] is None
    assert data['_sale']['area'] == {'status': 'fixed', 'basis': 'land', 'raw': '158.6m²（47.97坪）', 'sqm': 158.6}


def test_listing_gone_is_typed():
    scraper = BuyScraper(retry_delay=0)
    scraper.session = _Session([_Resp(404)])

    with pytest.raises(BuyScraperError) as exc:
        scraper.scrape_property(MS_CHUKO_URL)
    assert exc.value.error_type == 'gone'
    assert scraper.session.calls == 1


def test_redirect_to_building_page_is_gone_without_following():
    # 与租房下架相同的跳转：301 到 http 的楼栋页（/library/），跟随只会在 80 端口超时
    scraper = BuyScraper(retry_delay=0)
    library = 'http://suumo.jp/library/tf_13/sc_13106/to_1000000000/'
    scraper.session = _Session([_redirect(library) for _ in range(BuyScraper.RETRIES + 1)])

    with pytest.raises(BuyScraperError) as exc:
        scraper.scrape_property(MS_CHUKO_URL)
    assert exc.value.error_type == 'gone'
    assert str(exc.value) == '房源已下架或不存在（HTTP 301）'
    assert scraper.session.calls == 1


def test_other_redirect_is_not_followed():
    scraper = BuyScraper(retry_delay=0)
    search = 'http://suumo.jp/ms/chuko/tokyo/sc_taito/'
    scraper.session = _Session([_redirect(search, 302) for _ in range(BuyScraper.RETRIES + 1)])

    with pytest.raises(BuyScraperError) as exc:
        scraper.scrape_property(MS_CHUKO_URL)
    assert exc.value.error_type == 'temporary'
    assert str(exc.value) == '详情页暂时访问失败（HTTP 302）'


def test_temporary_failures_retry_with_limit():
    scraper = BuyScraper(retry_delay=0)
    scraper.session = _Session([_Resp(503), requests.Timeout('slow'), _Resp(503)])

    with pytest.raises(BuyScraperError) as exc:
        scraper.scrape_property(MS_CHUKO_URL)
    assert exc.value.error_type == 'temporary'
    assert scraper.session.calls == 3


def test_temporary_failure_then_success():
    scraper = BuyScraper(retry_delay=0)
    scraper.session = _Session([_Resp(503), _Resp(200, (FIXTURES / 'tochi.html').read_bytes())])

    result = scraper.scrape_property(TOCHI_URL)
    assert result.price == '4480万円'


def test_page_without_listing_tables_is_structure_change():
    scraper = BuyScraper(retry_delay=0)
    scraper.session = _Session([_Resp(200, b'<html><body><h1>SUUMO</h1></body></html>')])

    with pytest.raises(BuyScraperError) as exc:
        scraper.scrape_property(MS_CHUKO_URL)
    assert exc.value.error_type == 'structure'


@pytest.mark.parametrize('text,expected', [
    ('9階/SRC14階地下1階建', (9, 14, 'SRC')),
    ('12階/RC15階地下2階建', (12, 15, 'RC')),
    ('SRC地下1地上14階建', (None, 14, 'SRC')),
    ('9階/RC11階建', (9, 11, 'RC')),
])
def test_floor_structure_ignores_basement_levels(text, expected):
    from app.schema.buy_fields import split_floor_structure
    assert split_floor_structure(text) == expected


def test_area_with_thousands_separator():
    from app.schema.buy_fields import parse_area_sqm
    assert parse_area_sqm('1,234.56m²（登記）') == ('fixed', 1234.56)


def test_tax_included_price_label_still_maps_after_nfkc():
    raw = {'販売価格(税込)': '3,980万円', '専有面積': '52.1m²', '管理方式(通勤)': '自主管理'}
    data = FieldMapper().normalize(raw, detect_property_type(MS_CHUKO_URL, raw), source_url=MS_CHUKO_URL)
    assert data['basic.price_jpy'] == 39_800_000
    assert data['management.method'] == '自主管理'


def test_coverage_far_ignores_conditional_values_in_parentheses():
    from app.schema.buy_fields import parse_coverage_far
    assert parse_coverage_far('60％(角地緩和により70％)・200％') == (60, 200, True)


def test_basement_current_floor_is_negative_in_combined_text():
    from app.schema.buy_fields import split_floor_structure
    assert split_floor_structure('地下1階/RC11階建') == (-1, 11, 'RC')


@pytest.mark.parametrize('text', ['2027年3月予定', '2099年1月'])
def test_planned_completion_is_not_written_as_built_date(text):
    raw = {'価格': '4980万円', '専有面積': '60.5m²', '完成時期(築年月)': text}
    data = FieldMapper().normalize(raw, detect_property_type(MS_CHUKO_URL, raw), source_url=MS_CHUKO_URL)
    assert data['basic.built_month'] is None
    assert data['_sale']['completion'] == {'raw': text, 'planned': True}
    assert f'预计完成：{text}' in data['_sale']['review_notes']


def test_non_suumo_sources_keep_legacy_mapping():
    raw = {'価格': '5990万円', '専有面積': '35.67'}
    data = FieldMapper().normalize(raw, detect_property_type('', {'物件種別': '中古マンション'}), source_url='')
    assert '_sale' not in data
    assert data['basic.price_jpy'] == 59_900_000
