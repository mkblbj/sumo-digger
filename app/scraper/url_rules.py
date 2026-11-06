# -*- coding: utf-8 -*-
"""SUUMO URL 分类与规范化。

买房详情以路径中的 ``nc_<编号>`` 为准，优先于分类页前缀（``/ms/chuko/`` 等
同时也是搜索页前缀）。同一房源的子页（概要、周边、店铺……）、跟踪参数和片段
都归回详情根地址；新盘保留标识销售期的 ``rnms`` 参数与 ``property/`` 路径。
租房沿用原有规则，不在这里改变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

SUUMO_HOSTS = {'suumo.jp', 'www.suumo.jp'}

_BUY_DETAIL_RE = re.compile(
    r'^/(?P<category>ms/chuko|ms/shinchiku|ikkodate|chukoikkodate|tochi|toushi)/'
    r'(?P<middle>(?:[^/]+/)*?)nc_(?P<pid>\d+)(?:/(?P<rest>.*))?$'
)

# 新盘：能区分销售期的查询参数
_PROJECT_IDENTITY_PARAMS = ('rnms',)


@dataclass(frozen=True)
class SuumoUrlInfo:
    kind: str                 # buy_detail | search | rental_detail | invalid
    url: str                  # 详情为规范化地址；其余为原始地址（去首尾空白）
    source_key: str = ''      # 买房详情的来源标识，如 suumo:ms-chuko:21853112
    category: str = ''        # ms-chuko / ms-shinchiku / ikkodate / chukoikkodate / tochi / toushi
    property_id: str = ''     # SUUMO 房源编号（nc_ 后的数字）


def _buy_detail(path: str, query: str) -> SuumoUrlInfo | None:
    m = _BUY_DETAIL_RE.match(path)
    if not m:
        return None
    category_path = m.group('category')
    category = category_path.replace('/', '-')
    pid = m.group('pid')
    root = f"/{category_path}/{m.group('middle')}nc_{pid}/"
    source_key = f'suumo:{category}:{pid}'

    if category == 'ms-shinchiku':
        rest = (m.group('rest') or '').strip('/')
        params = parse_qs(query, keep_blank_values=False)
        identity = [(k, params[k][0]) for k in _PROJECT_IDENTITY_PARAMS if params.get(k)]
        canonical_path = root + ('property/' if rest.split('/')[0] == 'property' else '')
        canonical = 'https://suumo.jp' + canonical_path
        if identity:
            canonical += '?' + urlencode(identity)
            source_key += ':' + ':'.join(f'{k}={v}' for k, v in identity)
        return SuumoUrlInfo('buy_detail', canonical, source_key, category, pid)

    return SuumoUrlInfo('buy_detail', 'https://suumo.jp' + root, source_key, category, pid)


def classify_suumo_url(url: str) -> SuumoUrlInfo:
    """把一条用户输入的 SUUMO 链接分成买房详情、搜索页、租房详情或无效。"""
    from app.scraper.search_parser import SuumoSearchParser
    from app.scraper.suumo import SuumoScraper

    raw = (url or '').strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return SuumoUrlInfo('invalid', raw)
    if parsed.scheme not in ('http', 'https') or (parsed.hostname or '') not in SUUMO_HOSTS:
        return SuumoUrlInfo('invalid', raw)

    detail = _buy_detail(parsed.path or '/', parsed.query)
    if detail:
        return detail
    if SuumoSearchParser.is_search_url(raw):
        return SuumoUrlInfo('search', raw)
    if SuumoScraper.validate_url(raw):
        return SuumoUrlInfo('rental_detail', raw)
    return SuumoUrlInfo('invalid', raw)


def canonical_buy_detail_url(url: str) -> str:
    """买房详情（含子页）→ 规范化详情地址；非买房详情返回空字符串。"""
    info = classify_suumo_url(url)
    return info.url if info.kind == 'buy_detail' else ''


_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def is_delisted_redirect(response) -> bool:
    """详情页跳转到楼栋页（/library/）：房源已下架或不存在。

    SUUMO 对已下架的租房详情返回 301，Location 是 http://suumo.jp/library/…；
    80 端口连不上，跟随跳转只会超时，所以详情请求不跟随跳转，在这里识别。
    """
    if response.status_code not in _REDIRECT_STATUSES:
        return False
    try:
        target = urlparse((response.headers.get('Location') or '').strip())
    except ValueError:
        return False
    host = target.hostname or ''
    return (not host or host in SUUMO_HOSTS) and target.path.startswith('/library/')
