"""Data Export Module (Excel/CSV/JSON) with customer schema-aware field ordering."""

import io
import json
import logging
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Union
from enum import Enum

import pandas as pd

from app.schema.property_types import PropertyType
from app.schema.field_definitions import (
    get_field_definitions, get_key_to_label, Section,
)

logger = logging.getLogger(__name__)


class ExportFormat(Enum):
    EXCEL = "excel"
    CSV = "csv"
    JSON = "json"


class ExportError(Exception):
    pass


def _detect_ptype(row: Dict[str, Any]) -> Optional[PropertyType]:
    """Detect PropertyType from a data row."""
    ptype_str = row.get('_property_type', '')
    for pt in PropertyType:
        if pt.value == ptype_str:
            return pt
    return None


def _build_ordered_row(row: Dict[str, Any], ptype: Optional[PropertyType]) -> OrderedDict:
    """Re-order a single data row by customer schema, with Chinese labels as keys."""
    if not ptype:
        clean = OrderedDict()
        for k, v in row.items():
            if not k.startswith('_'):
                clean[k] = v
        return clean

    label_map = get_key_to_label(ptype)
    sections = get_field_definitions(ptype)
    ordered = OrderedDict()
    seen = set()

    for section in sections:
        for field in section.fields:
            if field.key in seen:
                continue
            seen.add(field.key)
            display = label_map.get(field.key, field.key)
            ordered[display] = row.get(field.key)

    for k, v in row.items():
        if k.startswith('_') or k in seen:
            continue
        ordered[k] = v

    return ordered


class DataExporter:
    """Data exporter with customer schema field ordering."""

    EXCEL_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    CSV_MIME_TYPE = "text/csv"
    JSON_MIME_TYPE = "application/json"

    @staticmethod
    def export_to_excel(data: List[Dict[str, Any]]) -> io.BytesIO:
        if not data:
            raise ExportError("No data to export")

        try:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                groups: Dict[str, List[OrderedDict]] = {}
                for row in data:
                    ptype = _detect_ptype(row)
                    label = ptype.value if ptype else '其他'
                    ordered = _build_ordered_row(row, ptype)
                    groups.setdefault(label, []).append(ordered)

                for sheet_name, rows in groups.items():
                    safe_name = sheet_name[:31]
                    df = pd.DataFrame(rows)
                    df.to_excel(writer, index=False, sheet_name=safe_name)

            output.seek(0)
            logger.info(f"Excel export successful: {len(data)} records")
            return output
        except ExportError:
            raise
        except Exception as e:
            logger.error(f"Excel export error: {e}")
            raise ExportError(f"Excel export error: {e}")

    @staticmethod
    def export_to_csv(data: List[Dict[str, Any]], encoding: str = 'utf-8-sig') -> io.BytesIO:
        if not data:
            raise ExportError("No data to export")

        try:
            ordered_data = []
            for row in data:
                ptype = _detect_ptype(row)
                ordered_data.append(_build_ordered_row(row, ptype))

            df = pd.DataFrame(ordered_data)
            output = io.BytesIO()
            df.to_csv(output, index=False, encoding=encoding)
            output.seek(0)
            logger.info(f"CSV export successful: {len(data)} records")
            return output
        except ExportError:
            raise
        except Exception as e:
            logger.error(f"CSV export error: {e}")
            raise ExportError(f"CSV export error: {e}")

    @staticmethod
    def export_to_json(data: List[Dict[str, Any]], indent: int = 2) -> io.BytesIO:
        if not data:
            raise ExportError("No data to export")

        try:
            structured = []
            for row in data:
                ptype = _detect_ptype(row)
                ordered = _build_ordered_row(row, ptype)
                structured.append(ordered)

            output = io.BytesIO()
            json_str = json.dumps(structured, ensure_ascii=False, indent=indent, default=str)
            output.write(json_str.encode('utf-8'))
            output.seek(0)
            logger.info(f"JSON export successful: {len(data)} records")
            return output
        except ExportError:
            raise
        except Exception as e:
            logger.error(f"JSON export error: {e}")
            raise ExportError(f"JSON export error: {e}")

    @classmethod
    def export(cls, data: List[Dict[str, Any]], format: Union[ExportFormat, str]) -> tuple:
        if isinstance(format, str):
            format = ExportFormat(format.lower())

        if format == ExportFormat.EXCEL:
            return (cls.export_to_excel(data), cls.EXCEL_MIME_TYPE, ".xlsx")
        elif format == ExportFormat.CSV:
            return (cls.export_to_csv(data), cls.CSV_MIME_TYPE, ".csv")
        elif format == ExportFormat.JSON:
            return (cls.export_to_json(data), cls.JSON_MIME_TYPE, ".json")
        else:
            raise ExportError(f"Unsupported format: {format}")

    @staticmethod
    def get_supported_formats() -> List[str]:
        return [f.value for f in ExportFormat]
