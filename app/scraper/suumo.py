# -*- coding: utf-8 -*-
"""SUUMO Property Scraping Module"""

import re
import logging
from typing import Dict, Any, Optional, List
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
    deposit: str = ""      # 敷金
    key_money: str = ""    # 礼金
    guarantee_money: str = ""
    depreciation: str = ""
    layout: str = ""
    area: str = ""
    direction: str = ""
    building_type: str = ""
    age: str = ""
    access_info: List[str] = field(default_factory=list)
    location: str = ""
    features: str = ""
    table_data: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Excel export"""
        return {
            'URL': self.url,
            '物件名': self.property_name,
            '賃料・初期費用': self.rent_cost,
            '管理費・共益費': self.management_fee,
            '敷金': self.deposit,
            '礼金': self.key_money,
            '保証金': self.guarantee_money,
            '敷引・償却': self.depreciation,
            '間取り': self.layout,
            '専有面積': self.area,
            '向き': self.direction,
            '建物種別': self.building_type,
            '築年数': self.age,
            'アクセス1': self.access_info[0] if len(self.access_info) > 0 else 'なし',
            'アクセス2': self.access_info[1] if len(self.access_info) > 1 else 'なし',
            'アクセス3': self.access_info[2] if len(self.access_info) > 2 else 'なし',
            '所在地': self.location,
            '部屋の特徴・設備': self.features,
            '損保': self.table_data.get('損保', '情報が見つかりません'),
            '駐車場': self.table_data.get('駐車場', '情報が見つかりません'),
            '仲介手数料': self.table_data.get('仲介手数料', '情報が見つかりません'),
            '保証会社(初期)': self.table_data.get('保証会社', '情報が見つかりません'),
            '保証会社(月々)': self.table_data.get('保証会社', '情報が見つかりません'),
            'ほか初期費用': self.table_data.get('ほか初期費用', '情報が見つかりません'),
            'ほか諸費用': self.table_data.get('ほか諸費用', '情報が見つかりません'),
            '備考': self.table_data.get('備考', '情報が見つかりません'),
            '統合': self._create_integrated_info()
        }

    def _create_integrated_info(self) -> str:
        """Create integrated info string"""
        initial_cost = self.table_data.get('ほか初期費用', '情報が見つかりません')
        other_cost = self.table_data.get('ほか諸費用', '情報が見つかりません')
        remarks = self.table_data.get('備考', '情報が見つかりません')
        return f"{initial_cost} | {other_cost} | {remarks}"


class SuumoScraper:
    """SUUMO Property Information Scraper"""

    SUUMO_URL_PATTERN = re.compile(r'^https?://suumo\.jp/(?:chintai|juhan)/.*')

    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    REQUEST_TIMEOUT = 10

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    @staticmethod
    def validate_url(url: str) -> bool:
        """Validate if URL is a SUUMO property URL"""
        if not url:
            return False
        return bool(SuumoScraper.SUUMO_URL_PATTERN.match(url))

    @staticmethod
    def clean_text(text: Optional[str]) -> str:
        """Clean up whitespace and newlines from text"""
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
            table_data = self._extract_table_data(soup)

            # Extract deposit and key_money separately
            deposit, key_money = self._extract_deposit_key_money(soup)

            property_data = PropertyData(
                url=url,
                property_name=self._extract_property_name(soup),
                rent_cost=self._extract_rent_cost(soup),
                management_fee=self._extract_management_fee(soup),
                deposit=deposit,
                key_money=key_money,
                guarantee_money=self._extract_guarantee_money(soup),
                depreciation=self._extract_depreciation(soup),
                layout=self._extract_layout(soup),
                area=self._extract_area(soup),
                direction=self._extract_direction(soup),
                building_type=self._extract_building_type(soup),
                age=self._extract_age(soup),
                access_info=self._extract_access_info(soup),
                location=self._extract_location(soup),
                features=self._extract_features(soup),
                table_data=table_data
            )

            logger.info(f"Successfully scraped: {url}")
            return property_data

        except requests.RequestException as e:
            logger.error(f"Request error for {url}: {e}")
            raise SuumoScraperError(f"Request error: {e}")
        except Exception as e:
            logger.error(f"Scraping error for {url}: {e}")
            raise SuumoScraperError(f"Scraping error: {e}")

    def _extract_property_name(self, soup: BeautifulSoup) -> str:
        """Extract property name"""
        tag = soup.find('h1', class_='section_h1-header-title')
        return self.clean_text(tag.text) if tag else '物件名が見つかりません'

    def _extract_rent_cost(self, soup: BeautifulSoup) -> str:
        """Extract rent cost"""
        tag = soup.find('div', class_='property_view_main-emphasis')
        return self.clean_text(tag.text) if tag else '賃料・初期費用が見つかりません'

    def _extract_management_fee(self, soup: BeautifulSoup) -> str:
        """Extract management fee"""
        tag = soup.find('div', class_='property_data-title', string='管理費・共益費')
        if tag:
            body_tag = tag.find_next('div', class_='property_data-body')
            return self.clean_text(body_tag.text) if body_tag else '管理費・共益費が見つかりません'
        return '管理費・共益費が見つかりません'

    def _extract_deposit_key_money(self, soup: BeautifulSoup) -> tuple:
        """Extract deposit and key money separately

        Returns:
            Tuple of (deposit, key_money)
        """
        tag = soup.find('div', class_='property_data-title', string='敷金/礼金')
        if tag:
            body_tag = tag.find_next('div', class_='property_data-body')
            if body_tag:
                text = self.clean_text(body_tag.text)
                # Parse "敷金 / 礼金" format (e.g., "- / 6.45万円" or "1ヶ月 / 1ヶ月")
                if '/' in text:
                    parts = text.split('/')
                    deposit = parts[0].strip() if len(parts) > 0 else '-'
                    key_money = parts[1].strip() if len(parts) > 1 else '-'
                    return (deposit, key_money)
                return (text, text)
        return ('情報なし', '情報なし')

    def _extract_guarantee_money(self, soup: BeautifulSoup) -> str:
        """Extract guarantee money"""
        tag = soup.find('div', class_='property_data-title', string='保証金')
        if tag:
            body_tag = tag.find_next('div', class_='property_data-body')
            return self.clean_text(body_tag.text) if body_tag else '保証金が見つかりません'
        return '保証金が見つかりません'

    def _extract_depreciation(self, soup: BeautifulSoup) -> str:
        """Extract depreciation"""
        tag = soup.find('div', class_='property_data-title', string='敷引・償却')
        if tag:
            body_tag = tag.find_next('div', class_='property_data-body')
            return self.clean_text(body_tag.text) if body_tag else '敷引・償却が見つかりません'
        return '敷引・償却が見つかりません'

    def _extract_layout(self, soup: BeautifulSoup) -> str:
        """Extract layout"""
        tag = soup.find('div', string='間取り')
        if tag:
            next_tag = tag.find_next('div')
            return self.clean_text(next_tag.text) if next_tag else '間取りが見つかりません'
        return '間取りが見つかりません'

    def _extract_area(self, soup: BeautifulSoup) -> str:
        """Extract area"""
        tag = soup.find('div', string='専有面積')
        if tag:
            next_tag = tag.find_next('div')
            return self.clean_text(next_tag.text) if next_tag else '専有面積が見つかりません'
        return '専有面積が見つかりません'

    def _extract_direction(self, soup: BeautifulSoup) -> str:
        """Extract direction"""
        tag = soup.find('div', string='向き')
        if tag:
            next_tag = tag.find_next('div')
            return self.clean_text(next_tag.text) if next_tag else '向きが見つかりません'
        return '向きが見つかりません'

    def _extract_building_type(self, soup: BeautifulSoup) -> str:
        """Extract building type"""
        tag = soup.find('div', string='建物種別')
        if tag:
            next_tag = tag.find_next('div')
            return self.clean_text(next_tag.text) if next_tag else '建物種別が見つかりません'
        return '建物種別が見つかりません'

    def _extract_age(self, soup: BeautifulSoup) -> str:
        """Extract age"""
        tag = soup.find('div', string='築年数')
        if tag:
            next_tag = tag.find_next('div')
            return self.clean_text(next_tag.text) if next_tag else '築年数が見つかりません'
        return '築年数が見つかりません'

    def _extract_access_info(self, soup: BeautifulSoup) -> List[str]:
        """Extract access information"""
        tags = soup.find_all('div', class_='property_view_detail-text', limit=3)
        return [self.clean_text(tag.text) for tag in tags]

    def _extract_location(self, soup: BeautifulSoup) -> str:
        """Extract location"""
        container = soup.find('div', class_='property_view_detail--location')
        if container:
            tag = container.find('div', class_='property_view_detail-text')
            return self.clean_text(tag.text) if tag else '所在地が見つかりません'
        return '所在地のコンテナが見つかりません'

    def _extract_features(self, soup: BeautifulSoup) -> str:
        """Extract features"""
        tag = soup.find('div', {'id': 'bkdt-option'})
        return tag.get_text(strip=True) if tag else '特徴・設備が見つかりません'

    def _extract_table_data(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract table data"""
        extracted_data: Dict[str, str] = {}

        for row in soup.select('tr'):
            headers = row.find_all('th')
            values = row.find_all('td')

            if len(headers) == 2 and len(values) >= 2:
                key1 = headers[0].get_text(strip=True)
                key2 = headers[1].get_text(strip=True)
                value1 = values[0].get_text(strip=True)
                value2 = values[1].get_text(strip=True)
                extracted_data[key1] = value1
                extracted_data[key2] = value2
            elif len(headers) == 1 and len(values) >= 1:
                key = headers[0].get_text(strip=True)
                if values[0].find('li'):
                    value = ' '.join(li.get_text(strip=True) for li in values[0].find_all('li'))
                else:
                    value = values[0].get_text(strip=True)
                extracted_data[key] = value

        return extracted_data
