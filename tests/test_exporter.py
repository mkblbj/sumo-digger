"""Tests for data exporter module"""

import json
import pytest
import pandas as pd
from app.exporters.exporter import DataExporter, ExportFormat, ExportError


class TestDataExporter:
    """Test cases for DataExporter class"""

    @pytest.fixture
    def sample_data(self):
        """Sample data for export tests"""
        return [
            {
                'URL': 'https://suumo.jp/chintai/bc_1/',
                '物件名': 'テスト物件1',
                '賃料・初期費用': '10万円',
            },
            {
                'URL': 'https://suumo.jp/chintai/bc_2/',
                '物件名': 'テスト物件2',
                '賃料・初期費用': '12万円',
            }
        ]

    def test_export_to_excel(self, sample_data):
        """Test Excel export"""
        result = DataExporter.export_to_excel(sample_data)

        # Check that we got a BytesIO object
        assert hasattr(result, 'read')

        # Read and verify content
        df = pd.read_excel(result)
        assert len(df) == 2
        assert 'URL' in df.columns
        assert '物件名' in df.columns
        assert df['物件名'].iloc[0] == 'テスト物件1'

    def test_export_to_csv(self, sample_data):
        """Test CSV export"""
        result = DataExporter.export_to_csv(sample_data)

        # Check that we got a BytesIO object
        assert hasattr(result, 'read')

        # Read and verify content
        content = result.read().decode('utf-8-sig')
        assert 'テスト物件1' in content
        assert 'テスト物件2' in content

    def test_export_to_json(self, sample_data):
        """Test JSON export"""
        result = DataExporter.export_to_json(sample_data)

        # Check that we got a BytesIO object
        assert hasattr(result, 'read')

        # Read and verify content
        content = result.read().decode('utf-8')
        parsed = json.loads(content)
        assert len(parsed) == 2
        assert parsed[0]['物件名'] == 'テスト物件1'

    def test_export_generic_excel(self, sample_data):
        """Test generic export method with Excel format"""
        file_stream, mime_type, extension = DataExporter.export(sample_data, 'excel')

        assert mime_type == DataExporter.EXCEL_MIME_TYPE
        assert extension == '.xlsx'

    def test_export_generic_csv(self, sample_data):
        """Test generic export method with CSV format"""
        file_stream, mime_type, extension = DataExporter.export(sample_data, 'csv')

        assert mime_type == DataExporter.CSV_MIME_TYPE
        assert extension == '.csv'

    def test_export_generic_json(self, sample_data):
        """Test generic export method with JSON format"""
        file_stream, mime_type, extension = DataExporter.export(sample_data, 'json')

        assert mime_type == DataExporter.JSON_MIME_TYPE
        assert extension == '.json'

    def test_export_with_enum(self, sample_data):
        """Test export with ExportFormat enum"""
        file_stream, mime_type, extension = DataExporter.export(sample_data, ExportFormat.EXCEL)

        assert mime_type == DataExporter.EXCEL_MIME_TYPE

    def test_export_empty_data_raises_error(self):
        """Test export with empty data raises error"""
        with pytest.raises(ExportError):
            DataExporter.export_to_excel([])

        with pytest.raises(ExportError):
            DataExporter.export_to_csv([])

        with pytest.raises(ExportError):
            DataExporter.export_to_json([])

    def test_get_supported_formats(self):
        """Test get_supported_formats returns correct formats"""
        formats = DataExporter.get_supported_formats()

        assert 'excel' in formats
        assert 'csv' in formats
        assert 'json' in formats
        assert len(formats) == 3
