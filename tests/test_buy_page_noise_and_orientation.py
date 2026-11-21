"""买房详情页：底部资料请求表单不进物件字段；一户建朝向取自「特徴ピックアップ」。

2026-11-21 客户反馈：塔楼备注出现「この情報を他の問い合わせにも自動で反映（30分で破棄）」。
这是页面底部资料请求表单（form#contact_form）里「その他」一行的复选框文字，
旧逻辑扫全页 tr，把它当成物件信息，再被 _build_remark 当作备考写进备注。
SUUMO 一户建的物件概要没有「向き」栏，部分房源在「特徴ピックアップ」里标了「南向き」等。
"""

from pathlib import Path

from bs4 import BeautifulSoup

from app.schema.mapper import FieldMapper, detect_property_type
from app.scraper.buy_scraper import BuyScraper

FIXTURES = Path(__file__).parent / 'fixtures' / 'suumo_buy'

MS_CHUKO_URL = 'https://suumo.jp/ms/chuko/tokyo/sc_taito/nc_21853112/'
CHUKO_IKKODATE_URL = 'https://suumo.jp/chukoikkodate/tokyo/sc_nakano/nc_21983513/'
TOCHI_URL = 'https://suumo.jp/tochi/kanagawa/sc_kawasakishiasao/nc_21998102/'

FORM_NOISE = 'この情報を他の問い合わせにも自動で反映（30分で破棄）'

# 真实页面资料请求表单的结构（2026-11-21 千葉市緑区页面，个人信息栏只留表头）
CONTACT_FORM = """
<form name="bukken_shiryou_JJ010FG210ActionForm" id="contact_form" method="post" action="/jj/bukken/shiryou/JJ010FG210/">
<table summary="" class="mt10 pCell10 bdclps w920 formtable"><tbody class="vat tal">
<tr><th class="thSideDYellow"><div class="cf"><p class="b">お名前</p><p class="fr">必須</p></div></th>
<td><input type="text" name="name1"><p>姓名</p></td></tr>
<tr><th class="thSideDYellow bdCell bdGrayL bdGrayT vam tal" rowspan="2"><div class="cf"><div class="fl"><p class="b">その他</p></div></div></th>
<td class="w478 thSideLYellow bdCell bdGrayT vam jscError" nowrap><div class="fl mT6 mr10">
<span class="db fl"><input type="checkbox" name="autoInsertFlg" value="1" checked="checked" id="autoInsertFlg" class="cbf"></span>
<span class="db ofh zm1"><label for="autoInsertFlg">この情報を他の問い合わせにも自動で反映（30分で破棄）</label></span>
<div class="mT3 lh1_4 fgDGray">※一部物件でのみ利用可能となります</div></div></td></tr>
<tr><th class="thSideDYellow"><p class="b">お問い合わせ内容</p></th><td><textarea name="biko"></textarea><p>【その他ご希望、ご要望等があればご記入ください。】</p></td></tr>
</tbody></table>
</form>
"""


def _pickup_section(tags):
    return (
        '<div class="mt30"><div class="secTitleOuterK"><h3 class="secTitleInnerK">特徴ピックアップ</h3></div>'
        f'<div class="mt10"> {" / ".join(tags)} </div></div>'
    )


def _pickup_script(tags):
    items = ','.join(f'"{t}"' for t in tags)
    return f'<script>var bukkenInfo = {{ kikaku : ["N020002"], tokuchoPickupList : [{items}], projectCd : "1" }};</script>'


def _with_extra(fixture, *blocks):
    html = (FIXTURES / fixture).read_text(encoding='utf-8')
    return html.replace('</body>', ''.join(blocks) + '</body>')


class _Resp:
    def __init__(self, content):
        self.status_code = 200
        self.content = content


class _Session:
    def __init__(self, content):
        self.content = content
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=True):
        return _Resp(self.content)


def _scrape_html(html, url):
    scraper = BuyScraper(retry_delay=0)
    scraper.session = _Session(html.encode('utf-8'))
    result = scraper.scrape_property(url)
    raw = result.to_dict()
    ptype = detect_property_type(url, raw)
    return result, FieldMapper().normalize(raw, ptype, source_url=url)


def test_contact_form_rows_are_not_listing_fields():
    soup = BeautifulSoup(_with_extra('ms_chuko.html', CONTACT_FORM), 'html.parser')
    table = BuyScraper()._extract_table_data(soup)

    assert 'その他' not in table
    assert 'お名前必須' not in table
    assert 'お問い合わせ内容' not in table
    assert table['向き'] == '南'  # 物件表照常读取


def test_form_text_never_becomes_remark():
    for fixture, url in (
        ('ms_chuko.html', MS_CHUKO_URL),
        ('chukoikkodate.html', CHUKO_IKKODATE_URL),
        ('tochi.html', TOCHI_URL),
    ):
        _, data = _scrape_html(_with_extra(fixture, CONTACT_FORM), url)
        assert FORM_NOISE not in str(data.get('detail.remark') or ''), fixture
        assert all(FORM_NOISE not in str(v) for v in data.values() if isinstance(v, str)), fixture


def test_pickup_tags_from_section_or_script():
    tags = ['南向き', 'システムキッチン', '前道６ｍ以上']
    scraper = BuyScraper()
    by_section = scraper._extract_pickup_tags(BeautifulSoup(_pickup_section(tags), 'html.parser'))
    by_script = scraper._extract_pickup_tags(BeautifulSoup(_pickup_script(tags), 'html.parser'))

    assert by_section == tags
    assert by_script == tags
    assert scraper._extract_pickup_tags(BeautifulSoup('<div></div>', 'html.parser')) == []


def test_detached_house_orientation_from_pickup_tag():
    html = _with_extra('chukoikkodate.html', _pickup_section(['システムキッチン', '南向き', '南面バルコニー']))
    _, data = _scrape_html(html, CHUKO_IKKODATE_URL)
    assert data['detail.orientation'] == '南'

    html = _with_extra('chukoikkodate.html', _pickup_script(['東南向き', '陽当り良好']))
    _, data = _scrape_html(html, CHUKO_IKKODATE_URL)
    assert data['detail.orientation'] == '东南'


def test_orientation_stays_empty_without_tag():
    # 「南面バルコニー」「陽当り良好」不是朝向；道路方位（私道負担・道路「北4.7ｍ幅」）也不推断
    html = _with_extra('chukoikkodate.html', _pickup_section(['南面バルコニー', '陽当り良好']))
    _, data = _scrape_html(html, CHUKO_IKKODATE_URL)
    assert data['detail.orientation'] is None


def test_listing_table_orientation_wins_over_pickup():
    html = _with_extra('ms_chuko.html', _pickup_section(['東南向き']))
    _, data = _scrape_html(html, MS_CHUKO_URL)
    assert data['detail.orientation'] == '南'
