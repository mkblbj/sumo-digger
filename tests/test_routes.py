"""Tests for Flask routes"""

import pytest
from app import create_app, _fix_zombie_tasks
from app.models import ScrapingTask, Property, db


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


class TestStartupRecovery:
    """Test stale task recovery on application startup"""

    def test_zombie_task_with_saved_properties_is_completed(self, app):
        """A restarted search task with persisted results should stay usable."""
        with app.app_context():
            task = ScrapingTask(
                id='zombie-with-results',
                status='running',
                property_type='search',
                total=500,
            )
            db.session.add(task)
            prop = Property(
                task_id=task.id,
                url='https://suumo.jp/chintai/bc_100000000001/',
                data_json='{"name":"saved property"}',
            )
            db.session.add(prop)
            db.session.commit()

            _fix_zombie_tasks(app)

            refreshed = db.session.get(ScrapingTask, task.id)
            assert refreshed.status == 'completed'
            assert refreshed.completed_at is not None
            assert refreshed.result_count == 1
            assert refreshed.errors[0]['error'] == 'Task interrupted by server restart'

    def test_failed_restart_task_with_saved_properties_is_completed(self, app):
        """Previously failed restart-interrupted tasks should be repaired too."""
        with app.app_context():
            task = ScrapingTask(
                id='failed-with-results',
                status='failed',
                property_type='search',
                total=500,
            )
            task.errors = [{'url': 'system', 'error': 'Task interrupted by server restart'}]
            db.session.add(task)
            prop = Property(
                task_id=task.id,
                url='https://suumo.jp/chintai/bc_100000000002/',
                data_json='{"name":"saved property"}',
            )
            db.session.add(prop)
            db.session.commit()

            _fix_zombie_tasks(app)

            refreshed = db.session.get(ScrapingTask, task.id)
            assert refreshed.status == 'completed'
            assert refreshed.completed_at is not None
            assert refreshed.result_count == 1
            assert refreshed.errors[0]['error'] == 'Task interrupted by server restart'

    def test_zombie_task_without_saved_properties_is_failed(self, app):
        """A restarted task with no persisted results is still a hard failure."""
        with app.app_context():
            task = ScrapingTask(
                id='zombie-empty',
                status='collecting',
                property_type='search',
                total=500,
            )
            db.session.add(task)
            db.session.commit()

            _fix_zombie_tasks(app)

            refreshed = db.session.get(ScrapingTask, task.id)
            assert refreshed.status == 'failed'
            assert refreshed.completed_at is not None
            assert refreshed.result_count == 0
            assert refreshed.errors[0]['error'] == 'Task interrupted by server restart'
