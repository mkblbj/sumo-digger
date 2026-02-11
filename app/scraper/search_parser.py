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
        # Condo search: /jj/bukken/ichiran/ or /ms/chuko/...
        re.compile(r'^https?://suumo\.jp/jj/bukken/ichiran/'),
        re.compile(r'^https?://suumo\.jp/ms/chuko/'),
        # Detached house search
        re.compile(r'^https?://suumo\.jp/ikkodate/chuko/'),
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

        # Rental: cassetteitem links
        for a in soup.select('a.js-cassette_link_href[href*="/chintai/"], a.js-cassette_link[href*="/chintai/"]'):
            href = a.get('href', '')
            if href and '/chintai/bc_' in href or '/chintai/jnc_' in href:
                url = self._normalize_url(href)
                if url:
                    urls.append(url)

        # Buy (ms / ikkodate): various link patterns
        for a in soup.select('a[href*="/ms/"], a[href*="/ikkodate/"]'):
            href = a.get('href', '')
            # Only detail pages, not search/ichiran pages
            if href and '/ichiran/' not in href and 'detail' in href.lower() or re.search(r'/(ms|ikkodate)/\d+/', href):
                url = self._normalize_url(href)
                if url:
                    urls.append(url)

        # Generic fallback: look for property detail links
        if not urls:
            for a in soup.select('a[href*="suumo.jp"]'):
                href = a.get('href', '')
                # Match individual property pages (contain bc_ or jnc_ or numeric IDs)
                if href and re.search(r'/(chintai|ms|ikkodate)/(bc_|jnc_|\d+/)', href):
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
