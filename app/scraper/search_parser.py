# -*- coding: utf-8 -*-
"""
SUUMO Search Result Page Parser.

MVP: user pastes a SUUMO search result URL, we paginate through and
collect individual property detail URLs up to a configurable limit.
"""

import re
import time
import logging
from typing import List
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup

from app.scraper.url_rules import canonical_buy_detail_url

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = None


class SearchParserError(Exception):
    """搜索页错误。error_type: gone(页面不存在) / temporary(暂时访问失败) / invalid(非搜索链接)。"""

    def __init__(self, message: str, error_type: str = 'temporary'):
        super().__init__(message)
        self.error_type = error_type


_EMPTY_HIT_RE = re.compile(r'^\s*0\s*件')
_NO_RESULT_TEXT_RE = re.compile(r'該当する物件(?:は|が)?(?:ありません|見つかりません)')


class SuumoSearchParser:
    """Parse SUUMO search result pages and collect property detail URLs."""

    # Supported search URL patterns
    SEARCH_PATTERNS = [
        # Rental search: /jj/chintai/ichiran/...
        re.compile(r'^https?://suumo\.jp/jj/chintai/ichiran/'),
        # Aggregated buy search entry points
        re.compile(r'^https?://suumo\.jp/jj/bukken/ichiran/'),
        # Direct search result pages by property category
        re.compile(r'^https?://suumo\.jp/ms/chuko/'),
        re.compile(r'^https?://suumo\.jp/ms/shinchiku/'),
        re.compile(r'^https?://suumo\.jp/ikkodate/chuko/'),
        re.compile(r'^https?://suumo\.jp/ikkodate/'),
        re.compile(r'^https?://suumo\.jp/chukoikkodate/'),
        re.compile(r'^https?://suumo\.jp/tochi/'),
        re.compile(r'^https?://suumo\.jp/toushi/'),
        # Generic search catch-all
        re.compile(r'^https?://suumo\.jp/.*/ichiran/'),
    ]

    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    TIMEOUT = 15

    # 暂时访问失败（超时、连接错误、5xx/429/403）按请求间隔重试的次数
    RETRIES = 2

    def __init__(self, max_results: int | None = DEFAULT_MAX_RESULTS, delay: float = 2.0):
        self.max_results = max_results if max_results and max_results > 0 else None
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        # 最近一次 collect_urls 的非致命问题：[{url, error, error_type}]，error_type 为 empty/structure/temporary
        self.issues: List[dict] = []

    @classmethod
    def is_search_url(cls, url: str) -> bool:
        return any(p.match(url) for p in cls.SEARCH_PATTERNS)

    def collect_urls(self, search_url: str, progress_callback=None) -> List[str]:
        """
        Collect property detail URLs from a SUUMO search result page.
        Paginates automatically until no more pages, unless max_results is set.

        只收录结果卡片里的详情，保持结果顺序；达到上限、到末页或遇到重复页时停止。
        第一页取不到时抛出带 error_type 的 SearchParserError；后续页暂时失败时
        保留已收集的结果，并把原因记入 self.issues。

        Args:
            search_url: SUUMO search result page URL
            progress_callback: optional fn(collected_count, page_number)

        Returns:
            List of property detail URLs
        """
        self.issues = []
        if not self.is_search_url(search_url):
            raise SearchParserError(f"Not a recognized SUUMO search URL: {search_url}", 'invalid')

        collected: List[str] = []
        seen = set()
        page = 1

        while self.max_results is None or len(collected) < self.max_results:
            page_url = self._build_page_url(search_url, page)
            logger.info(f"Fetching search page {page}: {page_url}")

            try:
                resp = self._fetch(page_url)
            except SearchParserError as e:
                if page == 1:
                    raise
                self.issues.append({'url': page_url, 'error': str(e), 'error_type': e.error_type})
                break

            soup = BeautifulSoup(resp.content, 'html.parser')
            detail_urls = self._extract_detail_urls(soup)

            if not detail_urls:
                if page == 1:
                    self.issues.append(self._empty_page_issue(soup, page_url))
                logger.info(f"No more results on page {page}")
                break

            added_this_page = 0
            for url in detail_urls:
                if url not in seen and (self.max_results is None or len(collected) < self.max_results):
                    seen.add(url)
                    collected.append(url)
                    added_this_page += 1

            # Avoid infinite pagination if SUUMO repeats the last/result page.
            if added_this_page == 0:
                logger.info(f"No new results on page {page}")
                break

            if progress_callback:
                progress_callback(len(collected), page)

            if not self._has_next_page(soup):
                break

            page += 1

            # Polite delay
            time.sleep(self.delay)

        logger.info(f"Collected {len(collected)} property URLs from {page} pages")
        return collected

    def _fetch(self, url: str):
        """GET 一页：404/410 视为不存在；超时、连接错误、5xx/429/403 按请求间隔有限重试。"""
        last_error = ''
        for attempt in range(self.RETRIES + 1):
            if attempt:
                time.sleep(self.delay)
            try:
                resp = self.session.get(url, timeout=self.TIMEOUT)
            except requests.RequestException as e:
                last_error = f'搜索页暂时访问失败：{e}'
                logger.warning(f"Search page request error ({attempt + 1}): {e}")
                continue
            if resp.status_code == 200:
                return resp
            if resp.status_code in (404, 410):
                raise SearchParserError(f'搜索页不存在（HTTP {resp.status_code}）', 'gone')
            last_error = f'搜索页暂时访问失败（HTTP {resp.status_code}）'
            logger.warning(f"Search page returned {resp.status_code} ({attempt + 1})")
        raise SearchParserError(last_error or '搜索页暂时访问失败', 'temporary')

    @staticmethod
    def _empty_page_issue(soup: BeautifulSoup, page_url: str) -> dict:
        hit = soup.select_one('.pagination_set-hit, .paginate_set-hit')
        empty_hit = bool(hit and _EMPTY_HIT_RE.match(hit.get_text(' ', strip=True)))
        # 租房等页面没有件数栏时，按页面的「没有符合条件的物件」提示判断
        if empty_hit or _NO_RESULT_TEXT_RE.search(soup.get_text(' ', strip=True)):
            return {'url': page_url, 'error': '搜索结果为空', 'error_type': 'empty'}
        return {'url': page_url, 'error': '搜索页未找到结果卡片，页面结构可能已变更', 'error_type': 'structure'}

    def _build_page_url(self, base_url: str, page: int) -> str:
        """Build paginated URL. SUUMO uses 'page' query parameter."""
        parsed = urlparse(base_url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        if page > 1:
            params['page'] = [str(page)]
        elif 'page' in params:
            del params['page']

        # Rebuild query string (flatten single-value lists)
        flat_params = {}
        for k, v_list in params.items():
            flat_params[k] = v_list[0] if len(v_list) == 1 else v_list

        new_query = urlencode(flat_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _extract_detail_urls(self, soup: BeautifulSoup) -> List[str]:
        """Extract property detail URLs from a search result page.

        买房只读结果卡片（.property_unit）的详情链接，并归并成规范化详情地址；
        不再在整页里兜底收集 nc_ 链接，避免把详情页的周边/概要/店铺子页当成结果。
        """
        urls = []

        # Strategy 1: rental detail links from cassette cards
        for a in soup.select('a.js-cassette_link_href[href], a.js-cassette_link[href]'):
            href = a.get('href', '')
            if href and ('/chintai/bc_' in href or '/chintai/jnc_' in href):
                url = self._normalize_url(href)
                if url:
                    urls.append(url)

        # Strategy 2: buy result cards → canonical detail URLs
        card_links = soup.select('.property_unit .property_unit-title a[href], h2.property_unit-title a[href]')
        if not card_links:
            card_links = soup.select('#js-bukkenList .property_unit a[href]')
        for a in card_links:
            detail = canonical_buy_detail_url(self._normalize_url(a.get('href', '')))
            if detail:
                urls.append(detail)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)

        return unique

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """Check if there is a next page in pagination."""
        # SUUMO pagination: look for "次へ" link or pagination-parts
        next_link = soup.find('a', string=re.compile(r'次へ'))
        if next_link:
            return True

        return False

    @staticmethod
    def _normalize_url(href: str) -> str:
        """Normalize a relative or absolute URL to full URL."""
        if not href:
            return ''
        if href.startswith('//'):
            return 'https:' + href
        if href.startswith('/'):
            return 'https://suumo.jp' + href
        if href.startswith('http'):
            return href
        return ''
