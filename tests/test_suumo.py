"""Tests for SUUMO scraper module"""

import pytest
from app.scraper.suumo import SuumoScraper, PropertyData


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
        assert result['賃料・初期費用'] == "10万円"
        assert result['管理費・共益費'] == "5000円"
        assert result['敷金'] == "1ヶ月"
        assert result['礼金'] == "1ヶ月"
        assert result['間取り'] == "2LDK"
        assert result['専有面積'] == "50m2"
        assert result['アクセス1'] == "駅A 徒歩5分"
        assert result['アクセス2'] == "駅B 徒歩10分"
        assert result['アクセス3'] == "なし"

    def test_to_dict_empty_access_info(self):
        """Test PropertyData with empty access info"""
        data = PropertyData(
            url="https://suumo.jp/chintai/bc_100000000000/",
            access_info=[]
        )

        result = data.to_dict()

        assert result['アクセス1'] == "なし"
        assert result['アクセス2'] == "なし"
        assert result['アクセス3'] == "なし"

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
