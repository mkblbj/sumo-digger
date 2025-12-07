"""Data Export Module (Excel/CSV/JSON)"""

import io
import json
import logging
from typing import List, Dict, Any, Union
from enum import Enum

import pandas as pd

logger = logging.getLogger(__name__)


class ExportFormat(Enum):
    """Export format enum"""
    EXCEL = "excel"
    CSV = "csv"
    JSON = "json"


class ExportError(Exception):
    """Custom exception for export errors"""
    pass


class DataExporter:
    """Data exporter class"""

    EXCEL_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    CSV_MIME_TYPE = "text/csv"
    JSON_MIME_TYPE = "application/json"

    @staticmethod
    def export_to_excel(data: List[Dict[str, Any]]) -> io.BytesIO:
        """
        Export data to Excel format

        Args:
            data: List of data to export

        Returns:
            Excel file byte stream
        """
        if not data:
            raise ExportError("No data to export")

        try:
            df = pd.DataFrame(data)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Property Data')
            output.seek(0)
            logger.info(f"Excel export successful: {len(data)} records")
            return output
        except Exception as e:
            logger.error(f"Excel export error: {e}")
            raise ExportError(f"Excel export error: {e}")

    @staticmethod
    def export_to_csv(data: List[Dict[str, Any]], encoding: str = 'utf-8-sig') -> io.BytesIO:
        """
        Export data to CSV format

        Args:
            data: List of data to export
            encoding: Character encoding (default is UTF-8 with BOM)

        Returns:
            CSV file byte stream
        """
        if not data:
            raise ExportError("No data to export")

        try:
            df = pd.DataFrame(data)
            output = io.BytesIO()
            df.to_csv(output, index=False, encoding=encoding)
            output.seek(0)
            logger.info(f"CSV export successful: {len(data)} records")
            return output
        except Exception as e:
            logger.error(f"CSV export error: {e}")
            raise ExportError(f"CSV export error: {e}")

    @staticmethod
    def export_to_json(data: List[Dict[str, Any]], indent: int = 2) -> io.BytesIO:
        """
        Export data to JSON format

        Args:
            data: List of data to export
            indent: JSON indent (default is 2)

        Returns:
            JSON file byte stream
        """
        if not data:
            raise ExportError("No data to export")

        try:
            output = io.BytesIO()
            json_str = json.dumps(data, ensure_ascii=False, indent=indent)
            output.write(json_str.encode('utf-8'))
            output.seek(0)
            logger.info(f"JSON export successful: {len(data)} records")
            return output
        except Exception as e:
            logger.error(f"JSON export error: {e}")
            raise ExportError(f"JSON export error: {e}")

    @classmethod
    def export(
        cls,
        data: List[Dict[str, Any]],
        format: Union[ExportFormat, str]
    ) -> tuple:
        """
        Export data in specified format

        Args:
            data: List of data to export
            format: Export format

        Returns:
            Tuple of (file stream, MIME type, file extension)
        """
        if isinstance(format, str):
            format = ExportFormat(format.lower())

        if format == ExportFormat.EXCEL:
            return (
                cls.export_to_excel(data),
                cls.EXCEL_MIME_TYPE,
                ".xlsx"
            )
        elif format == ExportFormat.CSV:
            return (
                cls.export_to_csv(data),
                cls.CSV_MIME_TYPE,
                ".csv"
            )
        elif format == ExportFormat.JSON:
            return (
                cls.export_to_json(data),
                cls.JSON_MIME_TYPE,
                ".json"
            )
        else:
            raise ExportError(f"Unsupported format: {format}")

    @staticmethod
    def get_supported_formats() -> List[str]:
        """Get list of supported export formats"""
        return [f.value for f in ExportFormat]
