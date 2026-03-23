# -*- coding: utf-8 -*-
"""SUUMO Buy Property (买卖类物件) Scraping Module"""

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
    total_floors: str = ""
    direction: str = ""
    structure: str = ""
    management_fee: str = ""
    repair_reserve: str = ""
    current_status: str = ""
    delivery_time: str = ""
    transaction_type: str = ""
    features: str = ""
    total_units: str = ""
    management_method: str = ""
    management_company: str = ""
    constructor: str = ""
    renovation: str = ""
    zoning: str = ""
    building_coverage: str = ""
    floor_area_ratio: str = ""
    land_rights: str = ""
    private_road: str = ""
    restrictions: str = ""
    seismic: str = ""
    table_data: Dict[str, str] = field(default_factory=dict)
    image_urls: List[str] = field(default_factory=list)
    video_urls: List[str] = field(default_factory=list)
    vr_links: List[str] = field(default_factory=list)

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
            d['所在階'] = self.floor
        if self.total_floors:
            d['階建'] = self.total_floors
        if self.direction:
            d['向き'] = self.direction
        if self.structure:
            d['構造'] = self.structure
        if self.management_fee:
            d['管理費'] = self.management_fee
        if self.repair_reserve:
            d['修繕積立金'] = self.repair_reserve
        if self.total_units:
            d['総戸数'] = self.total_units
        if self.management_method:
            d['管理形態'] = self.management_method
        if self.management_company:
            d['管理会社'] = self.management_company
        if self.constructor:
            d['施工会社'] = self.constructor
        if self.renovation:
            d['リフォーム'] = self.renovation
        if self.zoning:
            d['用途地域'] = self.zoning
        if self.building_coverage:
            d['建ぺい率'] = self.building_coverage
        if self.floor_area_ratio:
            d['容積率'] = self.floor_area_ratio
        if self.land_rights:
            d['土地権利'] = self.land_rights
        if self.private_road:
            d['私道負担'] = self.private_road
        if self.restrictions:
            d['法令上の制限'] = self.restrictions
        if self.seismic:
            d['耐震構造'] = self.seismic
        d.update({
            '現況': self.current_status,
            '引渡し時期': self.delivery_time,
            '取引態様': self.transaction_type,
            '部屋の特徴・設備': self.features,
        })
        if self.video_urls:
            d['_video_urls'] = self.video_urls
        if self.vr_links:
            d['_vr_links'] = self.vr_links
        # Add all table_data entries that aren't already captured
        for k, v in self.table_data.items():
            if k not in d:
                d[k] = v
        return d


class BuyScraper:
    """SUUMO Buy Property Scraper for buy/sell detail pages."""

    # Match condo / detached house / used house / land / investment detail pages
    BUY_URL_PATTERN = re.compile(r'^https?://suumo\.jp/(ms|ikkodate|chukoikkodate|tochi|toushi)/.*')

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
        if '/chukoikkodate/' in url:
            return '中古一戸建て'
        elif '/ikkodate/' in url:
            return '一戸建て'
        elif '/tochi/' in url:
            return '土地'
        elif '/toushi/' in url:
            return '投資物件'
        elif '/ms/' in url and '/shinchiku/' in url:
            return '新築マンション'
        elif '/ms/' in url:
            return '中古マンション'
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
            data.floor = data.table_data.get('所在階', '') or data.table_data.get('階', '')
            data.total_floors = data.table_data.get('階建', '') or data.table_data.get('総階数', '')
            data.direction = data.table_data.get('向き', '')
            data.structure = data.table_data.get('構造', '')
            data.management_fee = data.table_data.get('管理費', '') or data.table_data.get('管理費等', '')
            data.repair_reserve = data.table_data.get('修繕積立金', '')
            data.current_status = data.table_data.get('現況', '')
            data.delivery_time = data.table_data.get('引渡し時期', '') or data.table_data.get('引渡時期', '')
            data.transaction_type = data.table_data.get('取引態様', '')
            data.total_units = data.table_data.get('総戸数', '') or data.table_data.get('総区画数', '')
            data.management_method = data.table_data.get('管理形態', '') or data.table_data.get('管理方式', '')
            data.management_company = data.table_data.get('管理会社', '')
            data.constructor = data.table_data.get('施工会社', '') or data.table_data.get('施工', '')
            data.renovation = data.table_data.get('リフォーム', '') or data.table_data.get('リノベーション', '')
            data.zoning = data.table_data.get('用途地域', '')
            data.building_coverage = data.table_data.get('建ぺい率', '')
            data.floor_area_ratio = data.table_data.get('容積率', '')
            data.land_rights = data.table_data.get('土地権利', '') or data.table_data.get('権利形態', '')
            data.private_road = data.table_data.get('私道負担', '')
            data.restrictions = (
                data.table_data.get('法令上の制限', '')
                or data.table_data.get('その他制限事項', '')
                or data.table_data.get('制限事項', '')
            )
            data.seismic = data.table_data.get('耐震構造', '') or data.table_data.get('耐震', '')

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

            data.location = self._extract_location(soup, data.table_data)
            data.access_info = self._extract_access_info(soup, data.table_data)

            # Features
            data.features = self._extract_features(soup)

            # Images
            data.image_urls = self._extract_image_urls(soup)
            data.video_urls = self._extract_video_urls(soup)
            data.vr_links = self._extract_vr_links(soup)

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
                data[headers[0].get_text(strip=True)] = values[0].get_text('\n', strip=True)
                data[headers[1].get_text(strip=True)] = values[1].get_text('\n', strip=True)
            elif len(headers) == 1 and len(values) >= 1:
                key = headers[0].get_text(strip=True)
                if values[0].find('li'):
                    val = '\n'.join(li.get_text(' ', strip=True) for li in values[0].find_all('li'))
                else:
                    val = values[0].get_text('\n', strip=True)
                data[key] = val

        # Also extract from dottable format (used in some buy pages)
        for dt in soup.select('dt.dottable-title, div.dottable-title'):
            dd = dt.find_next_sibling('dd') or dt.find_next('dd')
            if dd:
                data[dt.get_text(strip=True)] = dd.get_text('\n', strip=True)

        return data

    def _extract_location(self, soup: BeautifulSoup, table_data: Dict[str, str]) -> str:
        selectors = [
            'div.property_view_detail--location div.property_view_detail-text',
            '.property_view_detail-text[property="address"]',
            '.property_view_detail--location',
        ]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag:
                text = self.clean_text(tag.get_text(' ', strip=True))
                if text:
                    return text
        return (
            table_data.get('所在地', '')
            or table_data.get('住所', '')
            or table_data.get('建物所在地', '')
        )

    def _extract_access_info(self, soup: BeautifulSoup, table_data: Dict[str, str]) -> List[str]:
        lines: List[str] = []
        selectors = [
            '.property_view_detail--traffic .property_view_detail-text',
            '.property_view_detail--traffic li',
            '.property_view_detail-box .property_view_detail-text',
        ]
        for selector in selectors:
            for tag in soup.select(selector):
                text = self.clean_text(tag.get_text(' ', strip=True))
                if text and text not in lines and len(lines) < 3:
                    lines.append(text)
            if lines:
                return lines[:3]

        for key in ('交通', '沿線・駅', '駅徒歩'):
            raw = table_data.get(key, '')
            if raw:
                return [
                    self.clean_text(line)
                    for line in str(raw).split('\n')
                    if self.clean_text(line)
                ][:3]

        for tag in soup.find_all('div', class_='property_view_detail-text', limit=6):
            text = self.clean_text(tag.get_text(' ', strip=True))
            if text and ('線' in text or '駅' in text or '徒歩' in text):
                lines.append(text)
        deduped: List[str] = []
        for line in lines:
            if line not in deduped:
                deduped.append(line)
        return deduped[:3]

    def _extract_features(self, soup: BeautifulSoup) -> str:
        feat_tag = soup.find('div', {'id': 'bkdt-option'})
        if not feat_tag:
            feat_tag = soup.select_one('.property_view_option, .property_view_option-list')
        if not feat_tag:
            return ''
        items = feat_tag.find_all('li')
        if items:
            return '、'.join(self.clean_text(li.get_text(' ', strip=True)) for li in items if li.get_text(strip=True))
        return self.clean_text(feat_tag.get_text(' ', strip=True))

    def _normalize_asset_url(self, url: str) -> str:
        if not url:
            return ''
        if url.startswith('//'):
            return f'https:{url}'
        if url.startswith('/'):
            return f'https://suumo.jp{url}'
        return url

    def _extract_image_urls(self, soup: BeautifulSoup) -> List[str]:
        """Extract image URLs"""
        urls: List[str] = []
        seen = set()

        for img in soup.select(
            'div.property_view_gallery img, div.property_view_photo img, div.bukkenGallery img, '
            '.slick-slide img, .carousel img, img[src*="img.suumo.jp"], img[data-src*="img.suumo.jp"]'
        ):
            src = self._normalize_asset_url(img.get('data-src') or img.get('src') or '')
            if src and 'suumo.jp' in src and src not in seen:
                src = re.sub(r'/resize/', '/original/', src)
                urls.append(src)
                seen.add(src)
        og = soup.find('meta', property='og:image')
        if og:
            src = self._normalize_asset_url(og.get('content', ''))
            if src and src not in seen:
                urls.append(src)
                seen.add(src)
        if not urls:
            for img in soup.select('img[src*="img.suumo.jp"]'):
                src = self._normalize_asset_url(img.get('src', ''))
                if src and src not in seen and 'icon' not in src.lower() and 'logo' not in src.lower():
                    urls.append(src)
                    seen.add(src)
        return urls

    def _extract_video_urls(self, soup: BeautifulSoup) -> List[str]:
        urls: List[str] = []
        seen = set()
        for tag in soup.select('video source[src], video[src], iframe[src*="youtube"], iframe[src*="youtu.be"], iframe[src*="video"]'):
            src = self._normalize_asset_url(tag.get('src') or '')
            if src and src not in seen:
                urls.append(src)
                seen.add(src)
        for tag in soup.select('[data-video-url], [data-video-src]'):
            src = self._normalize_asset_url(tag.get('data-video-url') or tag.get('data-video-src') or '')
            if src and src not in seen:
                urls.append(src)
                seen.add(src)
        return urls

    def _extract_vr_links(self, soup: BeautifulSoup) -> List[str]:
        urls: List[str] = []
        seen = set()
        for tag in soup.select('a[href]'):
            href = self._normalize_asset_url(tag.get('href', ''))
            text = self.clean_text(tag.get_text(' ', strip=True))
            haystack = f'{href} {text}'.lower()
            if not href:
                continue
            if any(token in haystack for token in ('vr', 'パノラマ', 'matterport', '3d', 'virtual')):
                if href not in seen:
                    urls.append(href)
                    seen.add(href)
        return urls
