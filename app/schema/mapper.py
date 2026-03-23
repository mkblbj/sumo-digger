"""Field mapper: convert raw scraper / PDF output into customer-spec normalized data.

Responsibilities:
  1. Detect property type from URL + raw data
  2. Map Japanese field names -> customer Chinese field keys
  3. Convert enum values (JP -> CN)
  4. Convert layout notation (1LDK -> 一居室)
  5. Clean numeric values (strip units)
  6. Produce an ordered dict with ALL required fields (missing = None)
"""

from __future__ import annotations

import re
import logging
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from app.schema.property_types import PropertyType
from app.schema.field_definitions import (
    get_field_definitions, get_all_keys, get_key_to_label, Section,
)
from app.schema import enums as E

logger = logging.getLogger(__name__)

# ── Japanese -> customer key mapping ─────────────────────────────────────
# Left: possible Japanese key from SUUMO scraper / table_data
# Right: customer dot-key

_JP_KEY_MAP: Dict[str, str] = {
    # basic
    '所在地': 'basic.address',
    '住所': 'basic.address',
    '物件名': 'detail.building_name',
    '販売価格': 'basic.price_jpy',
    '価格': 'basic.price_jpy',
    '賃料': 'rent.rent',
    '間取り': 'basic.layout_cn',
    '間取り詳細': 'basic.layout_cn',
    '専有面積': 'basic.area',
    '面積': 'basic.area',
    '土地面積': 'basic.land_area',
    '建物面積': 'basic.building_area',
    '延べ面積': 'building.total_area',
    '延床面積': 'building.total_area',
    '建物種別': 'detail.sub_type',
    '築年月': 'basic.built_month',
    '築年数': 'basic.built_month',
    '階': 'detail.floor',
    '所在階': 'detail.floor',
    '階建': 'detail.total_floors',
    '向き': 'detail.orientation',
    '構造': 'detail.structure',
    '管理費': 'analysis_or_detail.management_fee',
    '管理費等': 'analysis_or_detail.management_fee',
    '管理費・共益費': 'analysis_or_detail.management_fee',
    '共益費': 'rent.common_service_fee',
    '修繕積立金': 'analysis_or_detail.repair_fee',
    '修繕費': 'analysis_or_detail.repair_fee',
    '敷金': 'rent.deposit',
    '礼金': 'rent.key_money',
    '保証金': 'rent.deposit',
    '敷引・償却': '_raw.depreciation',
    '現況': 'detail.property_status',
    '引渡し時期': 'deal.delivery_time',
    '引渡時期': 'deal.delivery_time',
    '取引態様': 'detail.transaction_form',
    '契約期間': 'rent.contract_term',
    '部屋の特徴・設備': 'amenities.facilities',
    '設備・条件': 'amenities.facilities',
    '駐車場': 'detail.parking',
    '総戸数': 'detail.total_units',
    '総棟数': 'building.total_units',
    '総区画数': 'detail.total_lots',
    '区画数': 'detail.total_lots',
    '建ぺい率': 'land.building_coverage_pct',
    '容積率': 'land.far_pct',
    '用途地域': 'land.zoning',
    '地目': '_raw.land_category',
    '私道負担': 'land.private_road_burden',
    '権利形態': 'land.rights',
    '土地権利': 'land.rights',
    '管理方式': 'management.method',
    '管理会社': 'management.company',
    '施工会社': 'building.constructor',
    '施工': 'building.constructor',
    'リフォーム': 'building.renovation',
    'リノベーション': 'building.renovation',
    '耐震構造': 'building.seismic_type',
    '耐震': 'building.seismic_type',
    '法令上の制限': 'land.restrictions',
    'その他制限事項': 'land.restrictions',
    '制限事項': 'land.restrictions',
    '販売価格（税込）': 'basic.price_jpy',
    '售价（日元）': 'basic.price_jpy',
    '物件价格': 'analysis.property_price',
    '専有面積(壁芯)': 'detail.exclusive_area',
    '専有面積（壁芯）': 'detail.exclusive_area',
    '专有面积': 'detail.exclusive_area',
    'バルコニー面積': 'detail.other_areas',
    'テラス面積': 'detail.other_areas',
    'ルーフバルコニー面積': 'detail.other_areas',
    '専用庭面積': 'detail.other_areas',
    '阳台面积': 'detail.other_areas',
    '所在楼层': 'detail.floor',
    '总楼层': 'detail.total_floors',
    '管理方式（通勤）': 'management.method',
    '管理形態': 'management.method',
    '物件介紹': 'ai.description_candidates',
    '物件介绍': 'ai.description_candidates',
    '备注': 'detail.remark',
    '其他费用': 'analysis_or_detail.other_fees',
    '区画整理': '_raw.lot_readjustment',
    '都市計画': '_raw.city_plan',
    '接道状況': '_raw.road_access',
    'セットバック': '_raw.setback',
    '利回り': 'basic.estimated_roi_pct',
    '想定利回り': 'basic.estimated_roi_pct',
    '物件番号': '_raw.property_number',
    'SUUMO物件コード': '_raw.suumo_code',
    '情報公開日': '_raw.publish_date',
    '情報更新日': '_raw.update_date',
    '次回更新予定日': '_raw.next_update',
    '次回更新日': '_raw.next_update',
    # Access from rental scraper: stored separately
    'アクセス1': '_access.0',
    'アクセス2': '_access.1',
    'アクセス3': '_access.2',
    '駅徒歩': '_access.raw',
}


def _build_cn_label_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for ptype in PropertyType:
        for key, label in get_key_to_label(ptype).items():
            mapping.setdefault(label, key)
    return mapping


_CN_LABEL_MAP = _build_cn_label_map()

# ── Layout conversion (JP notation -> Chinese) ──────────────────────────

_ROOM_COUNT_MAP = {
    1: '一居室', 2: '两居室', 3: '三居室', 4: '四居室',
    5: '五居室', 6: '六居室', 7: '七居室',
}


def convert_layout(jp_layout: str) -> str:
    """Convert Japanese layout like '1LDK' to Chinese like '一居室'."""
    if not jp_layout:
        return ''
    jp_layout = jp_layout.strip().upper()
    m = re.match(r'^(\d+)\s*[SLDK+R]+', jp_layout)
    if m:
        n = int(m.group(1))
        return _ROOM_COUNT_MAP.get(n, f'{n}居室')
    # ワンルーム / 1R
    if 'ワンルーム' in jp_layout or jp_layout in ('1R', 'R'):
        return '一居室'
    return jp_layout


# ── Numeric cleaning ─────────────────────────────────────────────────────

_UNIT_RE = re.compile(r'[万億円㎡m²坪帖畳階建戸室棟%％]')
_MAN_RE = re.compile(r'([\d,.]+)\s*万')
_FLOOR_TOTAL_RE = re.compile(r'(\d+)\s*階建')
_FLOOR_CURRENT_RE = re.compile(r'(\d+)\s*階(?:部分)?(?!建)')


def clean_number(text: str, *, to_man_yen: bool = False) -> Optional[Any]:
    """Extract numeric value from text, stripping Japanese units.

    to_man_yen: if True, converts "万円" to raw yen (x10000).
    """
    if not text or text == '-':
        return None
    text = str(text).strip().replace(',', '').replace('、', '')
    if to_man_yen:
        m = _MAN_RE.search(text)
        if m:
            try:
                return int(float(m.group(1)) * 10000)
            except ValueError:
                pass
    cleaned = _UNIT_RE.sub('', text).strip()
    number_match = re.search(r'-?\d+(?:\.\d+)?', cleaned)
    if number_match:
        cleaned = number_match.group(0)
    if not cleaned:
        return None
    try:
        if '.' in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return None


def extract_floor_info(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Extract current floor and total floors from strings like 地下1階付11階建4階部分."""
    if not text:
        return None, None
    raw = str(text).strip().replace(',', '').replace('、', '')

    total = None
    current = None

    total_match = _FLOOR_TOTAL_RE.search(raw)
    if total_match:
        try:
            total = int(total_match.group(1))
        except ValueError:
            total = None

    current_matches = _FLOOR_CURRENT_RE.findall(raw)
    if current_matches:
        try:
            nums = [int(v) for v in current_matches]
            if total is not None:
                nums = [v for v in nums if v != total] or nums
            current = nums[-1]
        except ValueError:
            current = None

    return current, total


# ── Enum conversion ──────────────────────────────────────────────────────

def _map_enum(value: str, jp_map: Dict[str, str], valid: List[str]) -> str:
    """Map a JP enum value to CN using jp_map, fallback to 'other' or original."""
    if not value:
        return ''
    value = value.strip()
    if value in valid:
        return value
    mapped = jp_map.get(value)
    if mapped:
        return mapped
    for jp, cn in jp_map.items():
        if jp in value:
            return cn
    return value


# ── Property type detection ──────────────────────────────────────────────

def detect_property_type(url: str = '', raw_data: Optional[Dict] = None) -> PropertyType:
    """Infer customer property type from URL pattern and/or raw data."""
    url = url.lower() if url else ''

    # URL-based detection
    if '/chintai/' in url:
        return PropertyType.RENTAL
    if '/tochi/' in url or '/land/' in url:
        return PropertyType.LAND
    if '/toushi/' in url or '/invest/' in url:
        return PropertyType.INVESTMENT
    if '/ms/' in url:
        return PropertyType.MANSION
    if '/ikkodate/' in url:
        return PropertyType.HOUSE

    # Data-based detection
    if raw_data:
        ptype = raw_data.get('物件種別', '') or raw_data.get('_property_type', '')
        ptype_lower = ptype.lower()
        if ptype == PropertyType.RENTAL.value or '賃貸' in ptype or 'rental' in ptype_lower:
            return PropertyType.RENTAL
        if ptype == PropertyType.LAND.value or '土地' in ptype or 'land' in ptype_lower:
            return PropertyType.LAND
        if ptype == PropertyType.MANSION.value or 'マンション' in ptype or '公寓' in ptype or 'mansion' in ptype_lower:
            return PropertyType.MANSION
        if ptype == PropertyType.HOUSE.value or '一戸建' in ptype or '一户建' in ptype or 'house' in ptype_lower:
            return PropertyType.HOUSE
        if ptype == PropertyType.INVESTMENT.value or '投資' in ptype or '投资' in ptype or 'invest' in ptype_lower:
            return PropertyType.INVESTMENT
        if ptype == PropertyType.OTHER.value or '其他' in ptype or 'other' in ptype_lower:
            return PropertyType.OTHER

        # Heuristic: has 賃料 -> rental
        if '賃料' in raw_data or '房租' in raw_data or 'rent.rent' in raw_data:
            return PropertyType.RENTAL
        # Has 販売価格 -> buy
        if '販売価格' in raw_data or '売価' in raw_data:
            if '土地面積' in raw_data and '建物面積' not in raw_data:
                return PropertyType.LAND
            if '利回り' in raw_data or '想定利回り' in raw_data:
                return PropertyType.INVESTMENT
            if '一戸建' in str(raw_data.get('建物種別', '')):
                return PropertyType.HOUSE
            return PropertyType.MANSION

    return PropertyType.OTHER


# ── Main mapper class ────────────────────────────────────────────────────

class FieldMapper:
    """Normalize raw scraper/PDF data to customer-spec field schema."""

    def normalize(self, raw_data: Dict[str, Any], property_type: PropertyType,
                  source_url: str = '') -> Dict[str, Any]:
        """Full normalization pipeline.

        Returns an OrderedDict keyed by customer dot-keys, with ALL fields
        present (missing ones set to None).
        """
        mapped = self._map_keys(raw_data, property_type)
        mapped = self._convert_enums(mapped, property_type)
        mapped = self._convert_layout(mapped)
        mapped = self._normalize_special_fields(mapped)
        mapped = self._clean_numbers(mapped, property_type)
        mapped = self._assemble_access(mapped, raw_data)
        mapped = self._extract_city_ward(mapped)
        mapped = self._set_property_type(mapped, property_type)

        ordered = self._order_fields(mapped, property_type)
        ordered['_source_url'] = source_url
        ordered['_property_type'] = property_type.value
        ordered['_raw'] = {k: v for k, v in raw_data.items() if k.startswith('_') or k == 'URL'}
        return ordered

    # ── Step 1: Map JP keys to customer dot-keys ─────────────────────

    def _map_keys(self, raw: Dict[str, Any], ptype: PropertyType) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        valid_keys = set(get_all_keys(ptype))

        for jp_key, value in raw.items():
            if jp_key.startswith('_'):
                continue
            dot_key = _JP_KEY_MAP.get(jp_key) or _CN_LABEL_MAP.get(jp_key)
            if dot_key and not dot_key.startswith('_'):
                if dot_key in valid_keys:
                    if dot_key not in result or not result[dot_key]:
                        result[dot_key] = value
            elif jp_key in valid_keys:
                if jp_key not in result or not result[jp_key]:
                    result[jp_key] = value

        # Carry over image URLs
        if 'media.images' not in result:
            images = raw.get('_image_urls') or raw.get('物件画像')
            if images:
                result['media.images'] = images
        if 'media.videos' not in result:
            videos = raw.get('_video_urls')
            if videos:
                result['media.videos'] = videos
        if 'media.vr_links' not in result:
            vr_links = raw.get('_vr_links')
            if vr_links:
                result['media.vr_links'] = vr_links

        facilities = result.get('amenities.facilities')
        if isinstance(facilities, str):
            result['amenities.facilities'] = [
                item.strip()
                for item in re.split(r'[、,，/\n]+', facilities)
                if item and item.strip()
            ]

        return result

    # ── Step 2: Convert enum values ──────────────────────────────────

    def _convert_enums(self, data: Dict[str, Any], ptype: PropertyType) -> Dict[str, Any]:
        _enum_fields = {
            'detail.structure': (E.STRUCTURE_JP_MAP, E.STRUCTURE),
            'detail.orientation': (E.ORIENTATION_JP_MAP, E.ORIENTATION),
            'land.rights': (E.LAND_RIGHTS_JP_MAP, E.LAND_RIGHTS),
            'deal.delivery_text': (E.DELIVERY_TEXT_JP_MAP, E.DELIVERY_TEXT),
            'land.zoning': (E.ZONING_JP_MAP, E.ZONING),
            'building.seismic_type': (E.SEISMIC_JP_MAP, E.SEISMIC_TYPE),
            'management.method': (E.MANAGEMENT_METHOD_JP_MAP, E.MANAGEMENT_METHOD),
            'detail.property_status': (E.PROPERTY_STATUS_JP_MAP, E.PROPERTY_STATUS),
            'detail.transaction_form': (E.TRANSACTION_FORM_JP_MAP, E.TRANSACTION_FORM),
            'detail.sub_type': (E.SUB_TYPE_JP_MAP, E.SUB_TYPE_ALL),
        }
        for key, (jp_map, valid) in _enum_fields.items():
            if key in data and data[key]:
                data[key] = _map_enum(str(data[key]), jp_map, valid)
        if 'detail.land_or_invest_status' in data and data['detail.land_or_invest_status']:
            valid = E.INVESTMENT_STATUS if ptype == PropertyType.INVESTMENT else E.LAND_STATUS
            data['detail.land_or_invest_status'] = _map_enum(
                str(data['detail.land_or_invest_status']),
                E.LAND_OR_INVEST_STATUS_JP_MAP,
                valid,
            )
        return data

    # ── Step 3: Convert layout ───────────────────────────────────────

    def _convert_layout(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if 'basic.layout_cn' in data and data['basic.layout_cn']:
            data['basic.layout_cn'] = convert_layout(str(data['basic.layout_cn']))
        return data

    def _normalize_special_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        other_areas = data.get('detail.other_areas')
        if isinstance(other_areas, str):
            parsed_areas = []
            for chunk in re.split(r'[\n、]+', other_areas):
                text = chunk.strip()
                if not text:
                    continue
                num = clean_number(text)
                name = re.sub(r'[:：]?\s*[\d.,]+\s*(?:㎡|m²|m2)?', '', text).strip() or '其他面积'
                item: Dict[str, Any] = {'name': name}
                if num is not None:
                    item['area'] = num
                parsed_areas.append(item)
            if parsed_areas:
                data['detail.other_areas'] = parsed_areas
        return data

    # ── Step 4: Clean numeric values ─────────────────────────────────

    _PRICE_KEYS = {
        'basic.price_jpy', 'rent.rent', 'rent.deposit', 'rent.key_money',
        'rent.common_service_fee', 'rent.initial_rent',
        'analysis.property_price', 'analysis_or_detail.management_fee',
        'analysis_or_detail.repair_fee', 'analysis.trust_fee',
    }

    _AREA_KEYS = {
        'basic.area', 'basic.land_area', 'basic.building_area',
        'detail.exclusive_area', 'building.total_area',
        'land.building_coverage_pct', 'land.far_pct',
    }

    def _clean_numbers(self, data: Dict[str, Any], ptype: PropertyType) -> Dict[str, Any]:
        for key in self._PRICE_KEYS:
            if key in data and data[key] and isinstance(data[key], str):
                data[key] = clean_number(str(data[key]), to_man_yen=True)

        for key in self._AREA_KEYS:
            if key in data and data[key] and isinstance(data[key], str):
                data[key] = clean_number(str(data[key]))

        floor_source = None
        if isinstance(data.get('detail.floor'), str):
            floor_source = data.get('detail.floor')
        elif isinstance(data.get('detail.total_floors'), str):
            floor_source = data.get('detail.total_floors')

        if floor_source:
            current_floor, total_floors = extract_floor_info(floor_source)
            if current_floor is not None:
                data['detail.floor'] = current_floor
            if total_floors is not None:
                data['detail.total_floors'] = total_floors

        int_keys = {'detail.floor', 'detail.total_floors', 'detail.total_units',
                     'detail.total_lots', 'building.total_units'}
        for key in int_keys:
            if key in data and data[key] and isinstance(data[key], str):
                data[key] = clean_number(str(data[key]))

        return data

    # ── Step 5: Assemble access info ─────────────────────────────────

    def _assemble_access(self, data: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
        if 'detail.access' in data and data['detail.access']:
            val = data['detail.access']
            if isinstance(val, str):
                data['detail.access'] = [line.strip() for line in val.split('\n') if line.strip()]
            return data

        access_lines = []
        for i in range(3):
            k = f'_access.{i}'
            jp_k = f'アクセス{i+1}'
            v = data.pop(k, None) or raw.get(jp_k, '')
            if v and v != 'なし':
                access_lines.append(str(v).strip())
        raw_access = data.pop('_access.raw', None) or raw.get('駅徒歩', '')
        if raw_access and not access_lines:
            access_lines = [line.strip() for line in str(raw_access).split('\n') if line.strip()]

        if access_lines:
            data['detail.access'] = access_lines
        return data

    # ── Step 6: Extract city/ward from address ───────────────────────

    _CITY_RE = re.compile(
        r'^((?:東京都|北海道|(?:大阪|京都)府|.{2,3}県)'
        r'(?:[^\s市区町村]{1,5}[市区町村]))'
    )

    def _extract_city_ward(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if data.get('basic.city_ward'):
            return data
        address = data.get('basic.address', '') or ''
        m = self._CITY_RE.match(address)
        if m:
            data['basic.city_ward'] = m.group(1)
        return data

    # ── Step 7: Set property type label ──────────────────────────────

    def _set_property_type(self, data: Dict[str, Any], ptype: PropertyType) -> Dict[str, Any]:
        data['basic.property_type'] = ptype.value
        return data

    # ── Final: Order by customer spec, fill missing ──────────────────

    def _order_fields(self, data: Dict[str, Any], ptype: PropertyType) -> OrderedDict:
        sections = get_field_definitions(ptype)
        ordered = OrderedDict()
        seen_keys = set()

        for section in sections:
            for field in section.fields:
                if field.key in seen_keys:
                    continue
                seen_keys.add(field.key)
                ordered[field.key] = data.get(field.key)

        # Preserve extra keys not in the schema (prefixed data, etc.)
        for k, v in data.items():
            if k not in ordered:
                ordered[k] = v

        return ordered
