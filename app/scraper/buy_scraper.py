# -*- coding: utf-8 -*-
"""SUUMO Buy Property (中古マンション/中古一戸建て) Scraping Module"""

import re
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class BuyScraperError(Exception):
    """Custom exception for buy scraping errors"""
    pass


@dataclass
class BuyPropertyData:
    """Data class for buy property information"""
    url: str = ""
    property_type: str = ""  # 中古マンション or 中古一戸建て
    property_name: str = ""
    price: str = ""
    layout: str = ""
    exclusive_area: str = ""
    land_area: str = ""
    building_area: str = ""
    building_type: str = ""
    age: str = ""
    location: str = ""
    access_info: List[str] = field(default_factory=list)
    floor: str = ""
    direction: str = ""
    management_fee: str = ""
    repair_reserve: str = ""
    current_status: str = ""
    delivery_time: str = ""
    transaction_type: str = ""
    features: str = ""
    table_data: Dict[str, str] = field(default_factory=dict)
    image_urls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for export"""
        d = {
            'URL': self.url,
            '物件種別': self.property_type,
            '物件名': self.property_name,
            '販売価格': self.price,
            '間取り': self.layout,
            '専有面積': self.exclusive_area,
        }
        if self.land_area:
            d['土地面積'] = self.land_area
        if self.building_area:
            d['建物面積'] = self.building_area
        d.update({
            '建物種別': self.building_type,
            '築年数': self.age,
            '所在地': self.location,
            'アクセス1': self.access_info[0] if len(self.access_info) > 0 else 'なし',
            'アクセス2': self.access_info[1] if len(self.access_info) > 1 else 'なし',
            'アクセス3': self.access_info[2] if len(self.access_info) > 2 else 'なし',
        })
        if self.floor:
            d['階'] = self.floor
        if self.direction:
            d['向き'] = self.direction
        if self.management_fee:
            d['管理費'] = self.management_fee
        if self.repair_reserve:
            d['修繕積立金'] = self.repair_reserve
        d.update({
            '現況': self.current_status,
            '引渡し時期': self.delivery_time,
            '取引態様': self.transaction_type,
            '部屋の特徴・設備': self.features,
        })
        # Add all table_data entries that aren't already captured
        for k, v in self.table_data.items():
            if k not in d:
                d[k] = v
        return d


class BuyScraper:
    """SUUMO Buy Property Scraper (中古マンション + 中古一戸建て)"""

    # Match ms (condo) and ikkodate (detached house) URLs
    BUY_URL_PATTERN = re.compile(r'^https?://suumo\.jp/(ms|ikkodate)/.*')

    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    REQUEST_TIMEOUT = 10

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    @staticmethod
    def is_buy_url(url: str) -> bool:
        """Check if URL is a buy property URL"""
        return bool(BuyScraper.BUY_URL_PATTERN.match(url))

    @staticmethod
    def detect_type(url: str) -> str:
        """Detect property type from URL"""
        if '/ms/' in url:
            return '中古マンション'
        elif '/ikkodate/' in url:
            return '中古一戸建て'
        return '不明'

    @staticmethod
    def clean_text(text: Optional[str]) -> str:
        if not text:
            return ""
        return ' '.join(text.split())

    def scrape_property(self, url: str) -> Optional[BuyPropertyData]:
        """Scrape a buy property page"""
        try:
            response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            if response.status_code != 200:
                logger.error(f"HTTP {response.status_code} for {url}")
                return None

            soup = BeautifulSoup(response.content, 'html.parser')
            prop_type = self.detect_type(url)

            data = BuyPropertyData(
                url=url,
                property_type=prop_type,
            )

            # Property name
            h1 = soup.find('h1', class_='section_h1-header-title')
            if not h1:
                h1 = soup.find('h1')
            data.property_name = self.clean_text(h1.text) if h1 else ''

            # Price
            price_el = soup.find('div', class_='property_view_main-emphasis')
            if not price_el:
                price_el = soup.find('span', class_='dottable-value--emphasis')
            data.price = self.clean_text(price_el.text) if price_el else ''

            # Extract all table data first (robust)
            data.table_data = self._extract_table_data(soup)

            # Populate from table_data
            data.layout = data.table_data.get('間取り', '')
            data.exclusive_area = data.table_data.get('専有面積', '')
            data.land_area = data.table_data.get('土地面積', '')
            data.building_area = data.table_data.get('建物面積', '')
            data.building_type = data.table_data.get('建物種別', '') or data.table_data.get('構造', '')
            data.age = data.table_data.get('築年月', '') or data.table_data.get('築年数', '')
            data.floor = data.table_data.get('所在階', '') or data.table_data.get('階建', '')
            data.direction = data.table_data.get('向き', '')
            data.management_fee = data.table_data.get('管理費', '') or data.table_data.get('管理費等', '')
            data.repair_reserve = data.table_data.get('修繕積立金', '')
            data.current_status = data.table_data.get('現況', '')
            data.delivery_time = data.table_data.get('引渡し時期', '') or data.table_data.get('引渡時期', '')
            data.transaction_type = data.table_data.get('取引態様', '')

            # Also try extracting from div-based layout (similar to rental)
            for label_text, attr in [
                ('間取り', 'layout'),
                ('専有面積', 'exclusive_area'),
                ('向き', 'direction'),
                ('建物種別', 'building_type'),
                ('築年数', 'age'),
            ]:
                if not getattr(data, attr):
                    tag = soup.find('div', string=label_text)
                    if tag:
                        next_div = tag.find_next('div')
                        if next_div:
                            setattr(data, attr, self.clean_text(next_div.text))

            # Location
            loc_container = soup.find('div', class_='property_view_detail--location')
            if loc_container:
                loc_text = loc_container.find('div', class_='property_view_detail-text')
                data.location = self.clean_text(loc_text.text) if loc_text else ''
            if not data.location:
                data.location = data.table_data.get('所在地', '')

            # Access
            access_tags = soup.find_all('div', class_='property_view_detail-text', limit=3)
            data.access_info = [self.clean_text(t.text) for t in access_tags]

            # Features
            feat_tag = soup.find('div', {'id': 'bkdt-option'})
            data.features = feat_tag.get_text(strip=True) if feat_tag else ''

            # Images
            data.image_urls = self._extract_image_urls(soup)

            logger.info(f"Successfully scraped buy property: {url}")
            return data

        except requests.RequestException as e:
            logger.error(f"Request error for {url}: {e}")
            raise BuyScraperError(f"Request error: {e}")
        except Exception as e:
            logger.error(f"Scraping error for {url}: {e}")
            raise BuyScraperError(f"Scraping error: {e}")

    def _extract_table_data(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract all key-value pairs from tables"""
        data: Dict[str, str] = {}
        for row in soup.select('tr'):
            headers = row.find_all('th')
            values = row.find_all('td')
            if len(headers) == 2 and len(values) >= 2:
                data[headers[0].get_text(strip=True)] = values[0].get_text(strip=True)
                data[headers[1].get_text(strip=True)] = values[1].get_text(strip=True)
            elif len(headers) == 1 and len(values) >= 1:
                key = headers[0].get_text(strip=True)
                if values[0].find('li'):
                    val = ' '.join(li.get_text(strip=True) for li in values[0].find_all('li'))
                else:
                    val = values[0].get_text(strip=True)
                data[key] = val

        # Also extract from dottable format (used in some buy pages)
        for dt in soup.select('dt.dottable-title, div.dottable-title'):
            dd = dt.find_next_sibling('dd') or dt.find_next('dd')
            if dd:
                data[dt.get_text(strip=True)] = dd.get_text(strip=True)

        return data

    def _extract_image_urls(self, soup: BeautifulSoup) -> List[str]:
        """Extract image URLs"""
        urls = []
        seen = set()
        for img in soup.select('div.property_view_gallery img, div.property_view_photo img, div.bukkenGallery img'):
            src = img.get('data-src') or img.get('src') or ''
            if src and 'suumo.jp' in src and src not in seen:
                src = re.sub(r'/resize/', '/original/', src)
                urls.append(src)
                seen.add(src)
        og = soup.find('meta', property='og:image')
        if og:
            src = og.get('content', '')
            if src and src not in seen:
                urls.append(src)
                seen.add(src)
        if not urls:
            for img in soup.select('img[src*="img.suumo.jp"]'):
                src = img.get('src', '')
                if src and src not in seen and 'icon' not in src.lower() and 'logo' not in src.lower():
                    urls.append(src)
                    seen.add(src)
        return urls
