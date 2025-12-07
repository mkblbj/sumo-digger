"""SUUMO Authentication Module"""

import logging
import time
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


class SuumoAuthError(Exception):
    """Custom exception for authentication errors"""
    pass


class SuumoAuth:
    """SUUMO Authentication and Favorites Retrieval"""

    LOGIN_URL = "https://suumo.jp/jj/common/common/JJ901FK101/showLogin/"
    FAVORITES_URL = "https://suumo.jp/jj/common/service/JJ901FM201/?ar=050&cts=01"

    ELEMENT_TIMEOUT = 10
    LOGIN_WAIT_TIME = 5

    def __init__(self, headless: bool = True):
        """
        Args:
            headless: Whether to run in headless mode
        """
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None

    def _create_driver(self) -> webdriver.Chrome:
        """Create WebDriver"""
        options = Options()

        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--incognito")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            return driver
        except Exception as e:
            logger.error(f"Failed to create WebDriver: {e}")
            raise SuumoAuthError(f"Failed to create WebDriver: {e}")

    def login(self, email: str, password: str) -> bool:
        """
        Login to SUUMO

        Args:
            email: Email address
            password: Password

        Returns:
            True if login successful
        """
        if not email or not password:
            raise SuumoAuthError("Email and password are required")

        try:
            self.driver = self._create_driver()
            self.driver.get(self.LOGIN_URL)

            # Enter email
            email_input = WebDriverWait(self.driver, self.ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "mainEmail"))
            )
            email_input.clear()
            email_input.send_keys(email)

            # Enter password and login
            password_input = WebDriverWait(self.driver, self.ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "passwordText"))
            )
            password_input.clear()
            password_input.send_keys(password)
            password_input.send_keys(Keys.RETURN)

            # Wait for login to complete
            time.sleep(self.LOGIN_WAIT_TIME)

            # Check login success
            if "login" in self.driver.current_url.lower():
                error_elements = self.driver.find_elements(By.CLASS_NAME, "error")
                if error_elements:
                    error_msg = error_elements[0].text
                    self.close()  # Close driver before raising
                    raise SuumoAuthError(f"Login failed: {error_msg}")

            logger.info("Successfully logged in to SUUMO")
            return True

        except TimeoutException as e:
            logger.error(f"Timeout during login: {e}")
            self.close()  # Ensure driver is closed on error
            raise SuumoAuthError("Login page load timeout")
        except WebDriverException as e:
            logger.error(f"WebDriver error during login: {e}")
            self.close()  # Ensure driver is closed on error
            raise SuumoAuthError(f"Browser error: {e}")
        except SuumoAuthError:
            # Re-raise SuumoAuthError (already handled above)
            raise
        except Exception as e:
            logger.error(f"Unexpected error during login: {e}")
            self.close()  # Ensure driver is closed on unexpected error
            raise SuumoAuthError(f"Unexpected error: {e}")

    def get_favorite_urls(self) -> List[str]:
        """
        Get favorite property URLs

        Returns:
            List of property URLs
        """
        if not self.driver:
            raise SuumoAuthError("Please login first")

        try:
            self.driver.get(self.FAVORITES_URL)

            # Get favorite property links
            elements = WebDriverWait(self.driver, self.ELEMENT_TIMEOUT).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, ".cassette_item-title a")
                )
            )

            urls = []
            for element in elements:
                href = element.get_attribute('href')
                if href:
                    urls.append(href)

            logger.info(f"Found {len(urls)} favorite properties")
            return urls

        except TimeoutException:
            logger.warning("No favorite properties found or timeout")
            return []
        except Exception as e:
            logger.error(f"Error getting favorite URLs: {e}")
            raise SuumoAuthError(f"Favorites fetch error: {e}")

    def close(self):
        """Close WebDriver"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("WebDriver closed")
            except Exception as e:
                logger.error(f"Error closing WebDriver: {e}")
            finally:
                self.driver = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def get_favorites_with_login(email: str, password: str, headless: bool = True) -> List[str]:
    """
    Helper function to login and get favorite property URLs

    Args:
        email: Email address
        password: Password
        headless: Whether to run in headless mode

    Returns:
        List of property URLs
    """
    with SuumoAuth(headless=headless) as auth:
        auth.login(email, password)
        return auth.get_favorite_urls()
