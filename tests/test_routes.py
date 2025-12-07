"""Tests for Flask routes"""

import pytest
from app import create_app


@pytest.fixture
def app():
    """Create application for testing"""
    app = create_app('testing')
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    return app.test_client()


class TestMainRoutes:
    """Test cases for main routes"""

    def test_index_page(self, client):
        """Test index page loads successfully"""
        response = client.get('/')
        assert response.status_code == 200
        assert 'SUUMO' in response.data.decode('utf-8')


class TestApiRoutes:
    """Test cases for API routes"""

    def test_scrape_no_urls(self, client):
        """Test scrape endpoint with no URLs"""
        response = client.post('/api/scrape', json={})
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data
        assert 'URL' in data['error']

    def test_scrape_empty_urls(self, client):
        """Test scrape endpoint with empty URL list"""
        response = client.post('/api/scrape', json={'urls': []})
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data

    def test_scrape_invalid_urls(self, client):
        """Test scrape endpoint with invalid URLs"""
        response = client.post('/api/scrape', json={
            'urls': ['https://example.com/invalid']
        })
        assert response.status_code == 400

        data = response.get_json()
        assert 'error' in data
        assert 'invalid_urls' in data

    def test_validate_no_urls(self, client):
        """Test validate endpoint with no URLs"""
        response = client.post('/api/validate', json={})
        assert response.status_code == 400

    def test_validate_urls(self, client):
        """Test validate endpoint with mixed URLs"""
        response = client.post('/api/validate', json={
            'urls': [
                'https://suumo.jp/chintai/bc_100/',
                'https://example.com/invalid'
            ]
        })
        assert response.status_code == 200

        data = response.get_json()
        assert data['valid_count'] == 1
        assert data['invalid_count'] == 1
        assert 'https://suumo.jp/chintai/bc_100/' in data['valid']
        assert 'https://example.com/invalid' in data['invalid']

    def test_get_formats(self, client):
        """Test formats endpoint"""
        response = client.get('/api/formats')
        assert response.status_code == 200

        data = response.get_json()
        assert 'formats' in data
        assert 'excel' in data['formats']
        assert 'csv' in data['formats']
        assert 'json' in data['formats']

    def test_scrape_status_not_found(self, client):
        """Test scrape status with non-existent task"""
        response = client.get('/api/scrape/status/non-existent-task-id')
        assert response.status_code == 404

    def test_download_not_found(self, client):
        """Test download with non-existent task"""
        response = client.get('/api/download/non-existent-task-id/excel')
        assert response.status_code == 404

    def test_favorites_no_credentials(self, client):
        """Test favorites endpoint with no credentials"""
        response = client.post('/api/favorites', json={})
        assert response.status_code == 400

    def test_favorites_missing_password(self, client):
        """Test favorites endpoint with missing password"""
        response = client.post('/api/favorites', json={
            'email': 'test@example.com'
        })
        assert response.status_code == 400
