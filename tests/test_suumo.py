"""Tests for SUUMO scraper module"""

import pytest
import requests
from bs4 import BeautifulSoup

from app.schema.mapper import FieldMapper
from app.schema.property_types import PropertyType
from app.scraper.suumo import SuumoScraper, SuumoScraperError, PropertyData

RENTAL_URL = 'https://suumo.jp/chintai/jnc_000110935824/?bc=100535562071'
# 已下架的租房详情：SUUMO 301 到 http 的楼栋页（线上任务 5144f250 的实际跳转）
GONE_URL = 'https://suumo.jp/chintai/jnc_000110800080/?bc=100534661075'
LIBRARY_URL = 'http://suumo.jp/library/tf_12/sc_12223/to_1000901917/?bs=040'

# 租房详情 jnc_000110935824 的基本信息表：間取り与専有面積在同一行（两组 th/td）
_AREA_ROW_PAGE = """
<html><body>
  <div class="section_h1">
    <h1 class="section_h1-header-title">テスト物件</h1>
    <table class="property_view_table">
      <tr>
        <th class="property_view_table-title">間取り</th>
        <td class="property_view_table-body">ワンルーム</td>
        <th class="property_view_table-title">専有面積</th>
        <td class="property_view_table-body">{area}</td>
      </tr>
    </table>
  </div>
</body></html>
"""


def _response(status_code, content=b'', location=None):
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = content
    if location:
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
        if allow_redirects and item.is_redirect:
            raise requests.ConnectTimeout("HTTPConnectionPool(host='suumo.jp', port=80): connect timeout")
        return item


class TestSuumoScraper:
    """Test cases for SuumoScraper class"""

    def test_validate_url_valid_chintai(self):
        """Test valid chintai (rental) URL"""
        url = "https://suumo.jp/chintai/bc_100000000000/"
        assert SuumoScraper.validate_url(url) is True

    def test_validate_url_valid_juhan(self):
        """Test valid juhan (resale) URL"""
        url = "https://suumo.jp/juhan/bc_100000000000/"
        assert SuumoScraper.validate_url(url) is True

    def test_validate_url_valid_http(self):
        """Test valid HTTP URL (should work with http too)"""
        url = "http://suumo.jp/chintai/bc_100000000000/"
        assert SuumoScraper.validate_url(url) is True

    def test_validate_url_invalid_domain(self):
        """Test invalid domain"""
        url = "https://example.com/chintai/bc_100000000000/"
        assert SuumoScraper.validate_url(url) is False

    def test_validate_url_invalid_path(self):
        """Test invalid path (not chintai or juhan)"""
        url = "https://suumo.jp/mansion/bc_100000000000/"
        assert SuumoScraper.validate_url(url) is False

    def test_validate_url_empty(self):
        """Test empty URL"""
        assert SuumoScraper.validate_url("") is False

    def test_validate_url_none(self):
        """Test None URL"""
        assert SuumoScraper.validate_url(None) is False

    def test_clean_text_with_whitespace(self):
        """Test text cleaning with multiple spaces and newlines"""
        text = "  Hello   World\n\n  Test  "
        result = SuumoScraper.clean_text(text)
        assert result == "Hello World Test"

    def test_clean_text_empty(self):
        """Test text cleaning with empty string"""
        assert SuumoScraper.clean_text("") == ""

    def test_clean_text_none(self):
        """Test text cleaning with None"""
        assert SuumoScraper.clean_text(None) == ""


class TestPropertyData:
    """Test cases for PropertyData class"""

    def test_to_dict_basic(self):
        """Test PropertyData to_dict conversion"""
        data = PropertyData(
            url="https://suumo.jp/chintai/bc_100000000000/",
            property_name="Test Property",
            rent_cost="10万円",
            management_fee="5000円",
            deposit="1ヶ月",
            key_money="1ヶ月",
            layout="2LDK",
            area="50m2",
            access_info=["駅A 徒歩5分", "駅B 徒歩10分"]
        )

        result = data.to_dict()

        assert result['URL'] == "https://suumo.jp/chintai/bc_100000000000/"
        assert result['物件名'] == "Test Property"
        assert result['賃料'] == "10万円"
        assert result['管理費・共益費'] == "5000円"
        assert result['敷金'] == "1ヶ月"
        assert result['礼金'] == "1ヶ月"
        assert result['間取り'] == "2LDK"
        assert result['専有面積'] == "50m2"
        assert result['アクセス1'] == "駅A 徒歩5分"
        assert result['アクセス2'] == "駅B 徒歩10分"
        assert result['アクセス3'] == ""

    def test_to_dict_empty_access_info(self):
        """Test PropertyData with empty access info"""
        data = PropertyData(
            url="https://suumo.jp/chintai/bc_100000000000/",
            access_info=[]
        )

        result = data.to_dict()

        assert result['アクセス1'] == ""
        assert result['アクセス2'] == ""
        assert result['アクセス3'] == ""

    def test_to_dict_table_data(self):
        """Test PropertyData with table data"""
        data = PropertyData(
            url="https://suumo.jp/chintai/bc_100000000000/",
            table_data={
                '損保': '要加入',
                '駐車場': 'あり',
                '仲介手数料': '1ヶ月'
            }
        )

        result = data.to_dict()

        assert result['損保'] == '要加入'
        assert result['駐車場'] == 'あり'
        assert result['仲介手数料'] == '1ヶ月'


def test_extract_table_data_reads_basic_property_table():
    html = """
    <html><body>
      <div class="section_h1">
        <table>
          <tr><th>所在地</th><td>東京都新宿区早稲田鶴巻町</td></tr>
          <tr><th>駅徒歩</th><td>東京メトロ東西線/早稲田駅 歩10分</td></tr>
          <tr><th>階</th><td>1階/3階建</td></tr>
        </table>
      </div>
    </body></html>
    """
    scraper = SuumoScraper()
    data = scraper._extract_table_data(BeautifulSoup(html, 'html.parser'))
    assert data['所在地'] == '東京都新宿区早稲田鶴巻町'
    assert data['駅徒歩'] == '東京メトロ東西線/早稲田駅 歩10分'
    assert data['階'] == '1階/3階建'


def test_two_column_row_keeps_area_superscript_as_square_metre_sign():
    html = _AREA_ROW_PAGE.format(area='20m<sup>2</sup>')
    data = SuumoScraper()._extract_table_data(BeautifulSoup(html, 'html.parser'))
    assert data['間取り'] == 'ワンルーム'
    assert data['専有面積'] == '20m²'


@pytest.mark.parametrize('area_html,expected', [
    ('20m<sup>2</sup>', 20),
    ('21.04m<sup>2</sup>', 21.04),
])
def test_scraped_rental_area_is_not_inflated(area_html, expected):
    scraper = SuumoScraper()
    page = _AREA_ROW_PAGE.format(area=area_html).encode('utf-8')
    scraper.session = _Session([_response(200, page)])

    result = scraper.scrape_property(RENTAL_URL)
    normalized = FieldMapper().normalize(result.to_dict(), PropertyType.RENTAL, source_url=RENTAL_URL)

    assert normalized['basic.area'] == expected


def test_delisted_listing_redirected_to_building_page_is_gone():
    scraper = SuumoScraper()
    scraper.session = _Session([_response(301, location=LIBRARY_URL)])

    with pytest.raises(SuumoScraperError) as exc:
        scraper.scrape_property(GONE_URL)

    assert getattr(exc.value, 'error_type', None) == 'gone'
    assert str(exc.value) == '房源已下架或不存在（HTTP 301）'
    assert scraper.session.calls == 1


def test_missing_listing_is_gone():
    scraper = SuumoScraper()
    scraper.session = _Session([_response(404)])

    with pytest.raises(SuumoScraperError) as exc:
        scraper.scrape_property(GONE_URL)

    assert getattr(exc.value, 'error_type', None) == 'gone'
    assert str(exc.value) == '房源已下架或不存在（HTTP 404）'


def test_other_redirect_is_neither_followed_nor_called_gone():
    scraper = SuumoScraper()
    scraper.session = _Session([_response(302, location='http://suumo.jp/chintai/tokyo/')])

    assert scraper.scrape_property(RENTAL_URL) is None
    assert scraper.session.calls == 1


def test_connection_failure_is_temporary():
    scraper = SuumoScraper()
    scraper.session = _Session([requests.ReadTimeout('Read timed out. (read timeout=15)')])

    with pytest.raises(SuumoScraperError) as exc:
        scraper.scrape_property(RENTAL_URL)

    assert getattr(exc.value, 'error_type', None) == 'temporary'


def test_page_parse_failure_is_structure_change(monkeypatch):
    scraper = SuumoScraper()
    page = _AREA_ROW_PAGE.format(area='20m<sup>2</sup>').encode('utf-8')
    scraper.session = _Session([_response(200, page)])

    def broken_layout(soup):
        raise ValueError('unexpected layout')

    monkeypatch.setattr(scraper, '_extract_table_data', broken_layout)

    with pytest.raises(SuumoScraperError) as exc:
        scraper.scrape_property(RENTAL_URL)

    assert getattr(exc.value, 'error_type', None) == 'structure'
