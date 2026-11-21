# -*- coding: utf-8 -*-
"""买房物件表 → 客户点号字段 + `_sale` v1 元数据。

在 FieldMapper._map_keys 之后、枚举转换之前运行：读取 BuyScraper 输出的原文标签，
按类型固定售价、面积、楼层、月费、土地条件的口径与优先级。网页没有明示的值保持 None，
原文与状态写入 `_sale` 供后台审核。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.schema.buy_fields import (
    is_missing_text, is_planned_completion, nfkc, parse_area_sqm, parse_coverage_far, parse_int,
    parse_listed_fees, parse_monthly_fee, parse_price_yen, split_floor_structure,
)
from app.schema.field_definitions import get_all_keys
from app.schema.property_types import PropertyType
from app.scraper.url_rules import classify_suumo_url

SALE_META_VERSION = 1

# 面积口径：公寓用专有面积，一户建与投资用建筑面积，土地用土地面积
_AREA_BASIS = {
    PropertyType.MANSION: ('exclusive', ('専有面積',)),
    PropertyType.HOUSE: ('building', ('建物面積',)),
    PropertyType.LAND: ('land', ('土地面積',)),
    PropertyType.INVESTMENT: ('building', ('建物面積', '専有面積', '土地面積')),
}

_AREA_TARGETS = (
    ('専有面積', ('basic.area', 'detail.exclusive_area')),
    ('建物面積', ('basic.building_area',)),
    ('土地面積', ('basic.land_area',)),
)

# 网页写「-」表示未登载：这些文本字段不保留「-」
_TEXT_KEYS = (
    'land.restrictions', 'land.private_road_burden', 'land.rights', 'land.zoning',
    'building.renovation', 'building.constructor', 'management.company', 'detail.parking',
    'detail.orientation', 'detail.land_or_invest_status', 'detail.other_areas', 'detail.sub_type',
)

# 「-」不能被数值清洗成 0
_COUNT_KEYS = ('detail.total_units', 'detail.total_lots', 'building.total_units')

# 只认明确的管理方式；「委託(巡回)」未区分全部或部分委托，留给审核人选择
_MANAGEMENT_TOKEN_RE = re.compile(r'自主管理|一部委託|全部委託')
_ENTRUSTED_RE = re.compile(r'委託(?:\([^)]*\))?')
_FREE_BUILD_CONDITION = {'無', 'なし', '無し'}
# 「特徴ピックアップ」里的朝向标签（南向き、東南向き、全居室南向き）；「南面バルコニー」「陽当り良好」不算
_PICKUP_ORIENTATION_RE = re.compile(r'^(?:全居室)?(南東|南西|北東|北西|東南|西南|東北|西北|南|東|西|北)向き$')


def _text(raw: Dict[str, Any], *labels: str) -> str:
    for label in labels:
        value = raw.get(label)
        if isinstance(value, str) and not is_missing_text(value):
            return value.strip()
    return ''


def _delivery(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = nfkc(text)
    if '即' in t:
        return '即可交付', None
    if '相談' in t:
        return '协商', None
    return '其他', text


def _current_floor(text: str) -> Optional[int]:
    m = re.match(r'^\s*(地下)?(\d+)\s*階', nfkc(text))
    if not m:
        return None
    return -int(m.group(2)) if m.group(1) else int(m.group(2))


def _fees(raw: Dict[str, Any]) -> List[dict]:
    fees = parse_listed_fees(_text(raw, '諸費用'), 'monthly')
    fund = _text(raw, '修繕積立基金')
    if fund:
        item = {'name': '修繕積立基金', 'raw': fund, 'period': 'once'}
        status, amount = parse_price_yen(fund)
        if status == 'fixed':
            item['amount'] = amount
        fees.append(item)
    return fees


def normalize_buy_fields(data: Dict[str, Any], raw: Dict[str, Any], ptype: PropertyType,
                         source_url: str = '') -> Dict[str, Any]:
    url_info = classify_suumo_url(source_url) if source_url else None
    is_buy_detail = bool(url_info and url_info.kind == 'buy_detail')
    price_raw = _text(raw, '価格', '販売価格', '販売価格(税込)', '販売価格（税込）')
    basis, area_labels = _AREA_BASIS.get(ptype, ('building', ('建物面積',)))
    area_raw = _text(raw, *area_labels)
    if not is_buy_detail or (not price_raw and not area_raw):
        return data  # 非 SUUMO 买房详情（PDF 等）：保持原有口径，不生成 `_sale`

    valid = set(get_all_keys(ptype))

    def put(key: str, value: Any) -> None:
        if key in valid:
            data[key] = value

    notes: List[str] = []

    # 售价：只认物件表的价格栏；区间、待定写 None，原文进 `_sale`
    price_status, price_jpy = parse_price_yen(price_raw)
    put('basic.price_jpy', price_jpy)

    # 面积：各类型字段分别填，统一平方米
    for label, keys in _AREA_TARGETS:
        value_raw = _text(raw, label)
        if value_raw:
            _, sqm_value = parse_area_sqm(value_raw)
            for key in keys:
                put(key, sqm_value)
    area_status, sqm = parse_area_sqm(area_raw)

    # 楼层与构造：9階/RC11階建 → 所在 9 层、总 11 层、RC；一户建只有总层数
    current, total, token = split_floor_structure(_text(raw, '所在階/構造・階建'))
    building_text = _text(raw, '構造・階建て', '構造・工法')
    if building_text:
        _, total2, token2 = split_floor_structure(building_text)
        total = total if total is not None else total2
        token = token or token2
    floor_text = _text(raw, '所在階')
    if floor_text:
        parsed_floor = _current_floor(floor_text)
        current = parsed_floor if parsed_floor is not None else current
    if ptype == PropertyType.HOUSE:
        current = None
        data.pop('detail.floor', None)
    if current is not None:
        put('detail.floor', current)
    if total is not None:
        put('detail.total_floors', total)
    if token and is_missing_text(data.get('detail.structure') or ''):
        put('detail.structure', token)

    # 朝向：物件表「向き」优先。一户建物件概要没有这一栏，取「特徴ピックアップ」的朝向标签；
    # 不按道路方位推断（北侧临路的房子常常朝南）
    if is_missing_text(str(data.get('detail.orientation') or '')):
        for tag in _text(raw, '特徴ピックアップ').split('/'):
            m = _PICKUP_ORIENTATION_RE.match(nfkc(tag).strip())
            if m:
                put('detail.orientation', m.group(1))
                break

    # 建成（预计完成）年月：保留原文，后台按「YYYY年M月」解析
    built = _text(raw, '完成時期(築年月)', '築年月')
    completion = None
    if built and is_planned_completion(built):
        # 预计完成：不写进建成日期，原文单独记录待核对
        put('basic.built_month', None)
        put('building.built_month', None)
        completion = {'raw': built, 'planned': True}
        notes.append(f'预计完成：{built}')
    elif built:
        put('basic.built_month', built)
        put('building.built_month', built)

    # 交付：即引渡可 / 相談 归入选项，具体日期文本单独记录
    delivery = _text(raw, '引渡可能時期', '引き渡し時期', '引渡し時期', '引渡時期')
    if delivery:
        delivery_text, delivery_time = _delivery(delivery)
        put('deal.delivery_text', delivery_text)
        put('deal.delivery_time', delivery_time)

    # 月费：管理费、修缮积立金各自读取；明确免费记 0，缺失、待定保持 None
    management_raw = _text(raw, '管理費', '管理費等')
    if management_raw or '管理費' in raw:
        put('analysis_or_detail.management_fee', parse_monthly_fee(management_raw))
        token_match = _MANAGEMENT_TOKEN_RE.search(nfkc(management_raw))
        if token_match and is_missing_text(data.get('management.method') or ''):
            put('management.method', token_match.group(0))
        elif not token_match and 'management.method' in valid and is_missing_text(data.get('management.method') or ''):
            entrusted = _ENTRUSTED_RE.search(nfkc(management_raw))
            if entrusted:
                notes.append(f'管理方式：{entrusted.group(0)}（未注明全部或部分委托）')
    if '修繕積立金' in raw:
        put('analysis_or_detail.repair_fee', parse_monthly_fee(_text(raw, '修繕積立金')))
    fees = _fees(raw)

    # 土地：建蔽率与容积率分别取值，附带条件保留原文复核
    coverage_text = _text(raw, '建ぺい率・容積率')
    if coverage_text:
        coverage, far, has_conditions = parse_coverage_far(coverage_text)
        if coverage is not None:
            put('land.building_coverage_pct', coverage)
        if far is not None:
            put('land.far_pct', far)
        if has_conditions:
            notes.append(f'建ぺい率・容積率：{coverage_text}')
    if isinstance(data.get('land.zoning'), str):
        data['land.zoning'] = nfkc(data['land.zoning'])
    building_condition = _text(raw, '建築条件')
    if building_condition and nfkc(building_condition) not in _FREE_BUILD_CONDITION:
        notes.append(f'建築条件：{building_condition}')

    for key in _TEXT_KEYS + _COUNT_KEYS:
        if isinstance(data.get(key), str) and is_missing_text(data[key]):
            data[key] = None

    # 销售单位：多户、多区画或新盘销售期是楼盘级资料，留在待核对草稿
    sale_units = parse_int(_text(raw, '販売戸数', '販売区画数'))
    if (is_buy_detail and url_info.category == 'ms-shinchiku') or (sale_units or 0) > 1:
        granularity = 'project'
    elif ptype == PropertyType.LAND:
        granularity = 'lot'
    else:
        granularity = 'unit'
    if (sale_units or 0) > 1:
        notes.append(f'販売戸数：{sale_units}')

    # 单价：明确售价与对应面积均为正数时才计算（日元/㎡）
    if price_status == 'fixed' and price_jpy and area_status == 'fixed' and sqm and sqm > 0:
        put('basic.unit_price', round(price_jpy / sqm))

    sale = {
        'version': SALE_META_VERSION,
        'source_key': url_info.source_key if is_buy_detail else '',
        'granularity': granularity,
        'price': {'status': price_status, 'raw': price_raw, 'jpy': price_jpy},
        'area': {'status': area_status, 'basis': basis, 'raw': area_raw, 'sqm': sqm},
    }
    if completion:
        sale['completion'] = completion
    if fees:
        sale['fees'] = fees
    if notes:
        sale['review_notes'] = notes
    data['_sale'] = sale
    return data
