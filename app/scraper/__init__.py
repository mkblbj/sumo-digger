"""Scraper module"""

from app.scraper.suumo import SuumoScraper, SuumoScraperError, PropertyData
from app.scraper.auth import SuumoAuth, SuumoAuthError, get_favorites_with_login

__all__ = [
    'SuumoScraper',
    'SuumoScraperError',
    'PropertyData',
    'SuumoAuth',
    'SuumoAuthError',
    'get_favorites_with_login'
]
