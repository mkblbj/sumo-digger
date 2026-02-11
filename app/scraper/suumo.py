# -*- coding: utf-8 -*-
"""SUUMO Property Scraping Module

Selectors are based on SUUMO's actual HTML as of 2026-02.
Key structures:
  - h1.section_h1-header-title  → property name
  - span.property_view_note-emphasis  → rent
  - div.property_view_note-list  → rent details + deposit/key money
  - table tr > th + td  → ALL structured data (layout, area, access, location, etc.)
  - img[src*="img01.suumo.com/front/gazo/fr/bukken/"]  → property images
"""

import re
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class SuumoScraperError(Exception):
    """Custom exception for scraping errors"""
    pass


@dataclass
class PropertyData:
    """Data class for property information"""
    url: str = ""
    property_name: str = ""
    rent_cost: str = ""
    management_fee: str = ""
    deposit: str = ""
    key_money: str = ""
    guarantee_money: str = ""
    depreciation: str = ""
    layout: str = ""
    area: str = ""
    direction: str = ""
    building_type: str = ""
    age: str = ""
    floor: str = ""
    access_info: List[str] = field(default_factory=list)
    location: str = ""
    features: str = ""
    table_data: Dict[str, str] = field(default_factory=dict)
    image_urls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for export.
        Uses original SUUMO key names (e.g. 築年月 vs 築年数, 階建 vs 階).
        All table_data fields are merged to ensure complete 物件概要.
        """
        d: Dict[str, Any] = {
            'URL': self.url,
            '物件名': self.property_name,
            '賃料': self.rent_cost,
            '管理費・共益費': self.management_fee,
            '敷金': self.deposit,
            '礼金': self.key_money,
            '保証金': self.guarantee_money,
            '敷引・償却': self.depreciation,
            '間取り': self.layout,
            '専有面積': self.area,
            '向き': self.direction,
            '建物種別': self.building_type,
        }
        # Use original SUUMO key name for age/floor fields
        if '築年月' in self.table_data:
            d['築年月'] = self.age
        else:
            d['築年数'] = self.age

        if '階建' in self.table_data:
            d['階建'] = self.floor
        else:
            d['階'] = self.floor

        d['アクセス1'] = self.access_info[0] if len(self.access_info) > 0 else ''
        d['アクセス2'] = self.access_info[1] if len(self.access_info) > 1 else ''
        d['アクセス3'] = self.access_info[2] if len(self.access_info) > 2 else ''
        d['所在地'] = self.location
        d['部屋の特徴・設備'] = self.features

        # Merge remaining table_data (物件概要) not already covered
        skip = {
            '所在地', '駅徒歩', '間取り', '専有面積', '向き', '建物種別',
            '築年数', '築年月', '階', '階建',
        }
        for k, v in self.table_data.items():
            if k not in skip and k not in d:
                d[k] = v
        return d


class SuumoScraper:
    """SUUMO Property Information Scraper"""

    SUUMO_URL_PATTERN = re.compile(r'^https?://suumo\.jp/(?:chintai|juhan|ms|ikkodate)/.*')

    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    REQUEST_TIMEOUT = 15

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    @staticmethod
    def validate_url(url: str) -> bool:
        if not url:
            return False
        return bool(SuumoScraper.SUUMO_URL_PATTERN.match(url))

    @staticmethod
    def clean_text(text: Optional[str]) -> str:
        if not text:
            return ""
        return ' '.join(text.split())

    def scrape_property(self, url: str) -> Optional[PropertyData]:
        """Extract property information from a single URL"""
        if not self.validate_url(url):
            logger.warning(f"Invalid URL format: {url}")
            return None

        try:
            response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            if response.status_code != 200:
                logger.error(f"HTTP error {response.status_code} for {url}")
                return None

            soup = BeautifulSoup(response.content, 'html.parser')

            # 1) Extract ALL table data first (most reliable source)
            table_data = self._extract_table_data(soup)

            # 2) Extract rent/fees from the note area
            rent, mgmt, deposit, key_money, guarantee, depreciation = self._extract_note_area(soup)

            # 3) Extract access from table
            access_info = self._extract_access_from_table(table_data)

            # 4) Build PropertyData, using table_data as fallback
            data = PropertyData(
                url=url,
                property_name=self._extract_property_name(soup),
                rent_cost=rent,
                management_fee=mgmt,
                deposit=deposit,
                key_money=key_money,
                guarantee_money=guarantee,
                depreciation=depreciation,
                layout=table_data.get('間取り', ''),
                area=table_data.get('専有面積', ''),
                direction=table_data.get('向き', ''),
                building_type=table_data.get('建物種別', ''),
                age=table_data.get('築年数', '') or table_data.get('築年月', ''),
                floor=table_data.get('階', '') or table_data.get('階建', ''),
                access_info=access_info,
                location=table_data.get('所在地', ''),
                features=self._extract_features(soup),
                table_data=table_data,
                image_urls=self._extract_image_urls(soup, url),
            )

            logger.info(f"Successfully scraped: {url} ({len(data.image_urls)} images)")
            return data

        except requests.RequestException as e:
            logger.error(f"Request error for {url}: {e}")
            raise SuumoScraperError(f"Request error: {e}")
        except Exception as e:
            logger.error(f"Scraping error for {url}: {e}")
            raise SuumoScraperError(f"Scraping error: {e}")

    # ── Extraction methods ──────────────────────────────────

    def _extract_property_name(self, soup: BeautifulSoup) -> str:
        tag = soup.find('h1', class_='section_h1-header-title')
        if not tag:
            tag = soup.find('h1')
        return self.clean_text(tag.text) if tag else ''

    def _extract_note_area(self, soup: BeautifulSoup) -> Tuple[str, str, str, str, str, str]:
        """Parse the property_view_note area for rent, management fee, deposit, etc.

        Returns: (rent, management_fee, deposit, key_money, guarantee, depreciation)
        """
        rent = ''
        mgmt = ''
        deposit = ''
        key_money = ''
        guarantee = ''
        depreciation = ''

        # Rent: span.property_view_note-emphasis
        rent_el = soup.find('span', class_='property_view_note-emphasis')
        if not rent_el:
            # Fallback: old selector
            rent_el = soup.find('div', class_='property_view_main-emphasis')
        if rent_el:
            rent = self.clean_text(rent_el.text)

        # The note area contains two div.property_view_note-list blocks:
        # First: "9.4万円\n管理費・共益費: 3000円"
        # Second: "敷金: 18.8万円\n礼金: -\n保証金: -\n敷引・償却: -"
        note_lists = soup.select('div.property_view_note-list')

        for note_list in note_lists:
            text = note_list.get_text('\n', strip=True)

            # Parse management fee
            mgmt_match = re.search(r'管理費[・共益費]*[:：]\s*(.+)', text)
            if mgmt_match:
                mgmt = mgmt_match.group(1).strip()

            # Parse deposit/key money/guarantee/depreciation
            for line in text.split('\n'):
                line = line.strip()
                if line.startswith('敷金'):
                    deposit = re.sub(r'^敷金[:：]\s*', '', line).strip()
                elif line.startswith('礼金'):
                    key_money = re.sub(r'^礼金[:：]\s*', '', line).strip()
                elif line.startswith('保証金'):
                    guarantee = re.sub(r'^保証金[:：]\s*', '', line).strip()
                elif line.startswith('敷引') or line.startswith('償却'):
                    depreciation = re.sub(r'^敷引・?償却[:：]\s*', '', line).strip()

        # Fallback: old property_data selectors
        if not deposit and not key_money:
            tag = soup.find('div', class_='property_data-title', string='敷金/礼金')
            if tag:
                body = tag.find_next('div', class_='property_data-body')
                if body:
                    parts = self.clean_text(body.text).split('/')
                    deposit = parts[0].strip() if parts else ''
                    key_money = parts[1].strip() if len(parts) > 1 else ''

        return (rent, mgmt, deposit, key_money, guarantee, depreciation)

    def _extract_access_from_table(self, table_data: Dict[str, str]) -> List[str]:
        """Extract access info from table data.
        The '駅徒歩' key contains newline-separated access lines.
        """
        access_text = table_data.get('駅徒歩', '')
        if not access_text:
            return []
        # Split by newlines (the td content has multiple lines)
        lines = [self.clean_text(line) for line in access_text.split('\n') if line.strip()]
        return lines[:3]  # Max 3

    def _extract_features(self, soup: BeautifulSoup) -> str:
        tag = soup.find('div', {'id': 'bkdt-option'})
        if tag:
            # Get list items if present
            items = tag.find_all('li')
            if items:
                return '、'.join(li.get_text(strip=True) for li in items)
            return tag.get_text(strip=True)
        return ''

    # Keys from page form/nav tables that should be excluded
    _JUNK_TABLE_KEYS = {
        '', 'メールアドレス', '半角英数', '電話番号', 'お名前', '全国へ',
        '賃料(管理費)', '敷/礼/保証/敷引・償却',
    }

    def _extract_table_data(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract ALL key-value pairs from property info tables on the page."""
        data: Dict[str, str] = {}

        # Only look at tables within the main content area to avoid nav/form tables
        # Try to scope to main property area first
        main_area = soup.find('div', class_='section_h1') or soup

        for row in main_area.select('tr'):
            headers = row.find_all('th')
            values = row.find_all('td')

            if len(headers) == 2 and len(values) >= 2:
                k1 = headers[0].get_text(strip=True)
                k2 = headers[1].get_text(strip=True)
                v1 = values[0].get_text(strip=True)
                v2 = values[1].get_text(strip=True)
                if k1 and k1 not in self._JUNK_TABLE_KEYS:
                    data[k1] = v1
                if k2 and k2 not in self._JUNK_TABLE_KEYS:
                    data[k2] = v2
            elif len(headers) == 1 and len(values) >= 1:
                k = headers[0].get_text(strip=True)
                if not k or k in self._JUNK_TABLE_KEYS:
                    continue
                td = values[0]
                if td.find('li'):
                    v = ' '.join(li.get_text(strip=True) for li in td.find_all('li'))
                else:
                    v = td.get_text('\n', strip=True)
                # Skip overly long values (likely form content)
                if len(v) > 500:
                    continue
                data[k] = v

        return data

    def _extract_image_urls(self, soup: BeautifulSoup, page_url: str = '') -> List[str]:
        """Extract property image URLs.

        Images on SUUMO follow the pattern:
          img01.suumo.com/front/gazo/fr/bukken/{bc_suffix}/{bc_id}/{bc_id}_{type}{size}.jpg
        Where:
          _o.jpg = original, _t.jpg = thumbnail
          types: g=exterior, c=layout, r=room, 1-11=photos, s1-s4=??

        We want originals (_o.jpg), skip thumbnails and kaisha images.
        """
        urls = []
        seen = set()

        # Extract bc ID from URL for filtering
        bc_match = re.search(r'bc[=_](\d+)', page_url)
        bc_id = bc_match.group(1) if bc_match else ''

        for img in soup.find_all('img'):
            src = img.get('data-src') or img.get('src') or ''
            if not src:
                continue

            # Only property images (bukken path), not kaisha (company) images
            if '/bukken/' not in src:
                continue
            if 'suumo' not in src:
                continue

            # Skip thumbnails (_t.jpg), keep originals (_o.jpg)
            if src.endswith('t.jpg') or '_t.' in src:
                continue

            # If we know the bc_id, filter for it
            if bc_id and bc_id not in src:
                continue

            if src not in seen:
                seen.add(src)
                # Ensure https
                if src.startswith('//'):
                    src = 'https:' + src
                urls.append(src)

        # Fallback: if no originals found, try with thumbnails
        if not urls:
            for img in soup.find_all('img'):
                src = img.get('data-src') or img.get('src') or ''
                if '/bukken/' in src and 'suumo' in src and src not in seen:
                    if bc_id and bc_id not in src:
                        continue
                    seen.add(src)
                    if src.startswith('//'):
                        src = 'https:' + src
                    urls.append(src)

        return urls
