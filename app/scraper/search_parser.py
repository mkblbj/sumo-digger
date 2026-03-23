# -*- coding: utf-8 -*-
"""
SUUMO Search Result Page Parser.

MVP: user pastes a SUUMO search result URL, we paginate through and
collect individual property detail URLs up to a configurable limit.
"""

import re
import logging
from typing import List
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 100
ABSOLUTE_MAX_RESULTS = 1000


class SearchParserError(Exception):
    pass


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

    def __init__(self, max_results: int = DEFAULT_MAX_RESULTS, delay: float = 2.0):
        self.max_results = min(max_results, ABSOLUTE_MAX_RESULTS)
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    @classmethod
    def is_search_url(cls, url: str) -> bool:
        return any(p.match(url) for p in cls.SEARCH_PATTERNS)

    def collect_urls(self, search_url: str, progress_callback=None) -> List[str]:
        """
        Collect property detail URLs from a SUUMO search result page.
        Paginates automatically until max_results or no more pages.

        Args:
            search_url: SUUMO search result page URL
            progress_callback: optional fn(collected_count, page_number)

        Returns:
            List of property detail URLs
        """
        if not self.is_search_url(search_url):
            raise SearchParserError(f"Not a recognized SUUMO search URL: {search_url}")

        collected: List[str] = []
        seen = set()
        page = 1

        while len(collected) < self.max_results:
            page_url = self._build_page_url(search_url, page)
            logger.info(f"Fetching search page {page}: {page_url}")

            try:
                resp = self.session.get(page_url, timeout=self.TIMEOUT)
                if resp.status_code != 200:
                    logger.warning(f"Search page returned {resp.status_code}")
                    break

                soup = BeautifulSoup(resp.content, 'html.parser')
                detail_urls = self._extract_detail_urls(soup)

                if not detail_urls:
                    logger.info(f"No more results on page {page}")
                    break

                for url in detail_urls:
                    if url not in seen and len(collected) < self.max_results:
                        seen.add(url)
                        collected.append(url)

                if progress_callback:
                    progress_callback(len(collected), page)

                # Check if there's a next page
                if not self._has_next_page(soup):
                    break

                page += 1

                # Polite delay
                import time
                time.sleep(self.delay)

            except requests.RequestException as e:
                logger.error(f"Search page request error: {e}")
                raise SearchParserError(f"Failed to fetch search page: {e}")

        logger.info(f"Collected {len(collected)} property URLs from {page} pages")
        return collected

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
        """Extract property detail URLs from a search result page."""
        urls = []
        buy_segments = ('ms', 'ikkodate', 'chukoikkodate', 'tochi', 'toushi')

        # Strategy 1: rental detail links from cassette cards
        for a in soup.select('a.js-cassette_link_href[href], a.js-cassette_link[href]'):
            href = a.get('href', '')
            if href and ('/chintai/bc_' in href or '/chintai/jnc_' in href):
                url = self._normalize_url(href)
                if url:
                    urls.append(url)

        # Strategy 2: property title links on buy/sell result pages
        for a in soup.select('.property_unit-title a[href], h2.property_unit-title a[href]'):
            href = a.get('href', '')
            if (
                href
                and '/ichiran/' not in href
                and '/jj/' not in href
                and '/rooms/' not in href
                and any(f'/{segment}/' in href for segment in buy_segments)
            ):
                url = self._normalize_url(href)
                if url:
                    urls.append(url)

        # Strategy 3: generic fallback for buy/sell detail pages using nc_ marker
        if not urls:
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                if (
                    href
                    and '/ichiran/' not in href
                    and '/jj/' not in href
                    and '/rooms/' not in href
                    and 'nc_' in href
                    and any(f'/{segment}/' in href for segment in buy_segments)
                ):
                    url = self._normalize_url(href)
                    if url:
                        urls.append(url)

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

        # Also check for pagination with page numbers
        pager = soup.select('.pagination_set a, .paginate_set a, .pagination a')
        if pager:
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
