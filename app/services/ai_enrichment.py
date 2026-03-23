"""AI enrichment service: fill computed/AI-generated fields per customer spec.

Covers:
  - Exchange rate conversion (JPY -> CNY)
  - Geocoding (address -> lat/lng)
  - Investment analysis calculations
  - AI-generated fields (title candidates, descriptions, tags, landmarks, POI)
"""

import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

import requests

from app.schema.property_types import PropertyType
from app.schema.field_definitions import get_key_to_label
from app.services.llm_client import LLMClient, extract_json

logger = logging.getLogger(__name__)

_JAPANESE_KANA_RE = re.compile(r'[\u3040-\u30ff]')

# ── Exchange rate ────────────────────────────────────────────────────────

_FALLBACK_JPY_CNY_RATE = 0.048  # ~1 JPY = 0.048 CNY (approx 2026)


def fetch_jpy_cny_rate() -> float:
    """Fetch current JPY->CNY exchange rate. Falls back to hardcoded value."""
    try:
        resp = requests.get(
            'https://api.exchangerate-api.com/v4/latest/JPY',
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = data.get('rates', {}).get('CNY', _FALLBACK_JPY_CNY_RATE)
            logger.info(f"Exchange rate JPY->CNY: {rate}")
            return float(rate)
    except Exception as e:
        logger.warning(f"Exchange rate fetch failed, using fallback: {e}")
    return _FALLBACK_JPY_CNY_RATE


# ── Investment calculations ──────────────────────────────────────────────

def calc_brokerage_fee(price_jpy: int) -> int:
    """中介费 = (售价 x 3% + 60000) x 1.1"""
    return int((price_jpy * 0.03 + 60000) * 1.1)


def calc_unit_price(price_jpy: int, area: float) -> Optional[float]:
    """单价 = 售价 / 面积"""
    if area and area > 0:
        return round(price_jpy / area, 2)
    return None


def calc_annual_roi(annual_rent: int, price_jpy: int) -> Optional[float]:
    """年回报率 = 年租金 / 售价 x 100%"""
    if price_jpy and price_jpy > 0:
        return round(annual_rent / price_jpy * 100, 2)
    return None


# ── Main enrichment service ──────────────────────────────────────────────

class AIEnrichmentService:
    """Fill AI/computed fields on a normalized property dict."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client
        self._rate: Optional[float] = None

    @property
    def exchange_rate(self) -> float:
        if self._rate is None:
            self._rate = fetch_jpy_cny_rate()
        return self._rate

    def enrich(self, data: Dict[str, Any], property_type: PropertyType) -> Dict[str, Any]:
        """Run all enrichment steps on normalized data. Modifies in place and returns."""
        self._calc_price_cny(data)
        self._calc_unit_price(data, property_type)
        self._calc_investment_analysis(data, property_type)
        self._calc_rental_initial_fees(data, property_type)
        self._geocode_address(data)

        if self.llm:
            self._ai_generate_fields(data, property_type)

        return data

    # ── Price conversions ────────────────────────────────────────────

    def _calc_price_cny(self, data: Dict[str, Any]) -> None:
        price_jpy = data.get('basic.price_jpy')
        if price_jpy and isinstance(price_jpy, (int, float)):
            data['basic.price_cny'] = int(price_jpy * self.exchange_rate)

    def _calc_unit_price(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        price = data.get('basic.price_jpy')
        if not price:
            return
        price = int(price)

        area = None
        if ptype == PropertyType.LAND:
            area = data.get('basic.land_area')
        elif ptype in (PropertyType.HOUSE, PropertyType.INVESTMENT):
            area = data.get('basic.building_area')
        else:
            area = data.get('basic.area') or data.get('detail.exclusive_area')

        if area:
            data['basic.unit_price'] = calc_unit_price(price, float(area))

    def _geocode_address(self, data: Dict[str, Any]) -> None:
        """Fill latitude/longitude from address using public geocoding when missing."""
        if data.get('basic.longitude') and data.get('basic.latitude'):
            return
        address = data.get('basic.address')
        if not address:
            return
        try:
            resp = requests.get(
                'https://msearch.gsi.go.jp/address-search/AddressSearch',
                params={'q': address},
                timeout=8,
            )
            if resp.status_code != 200:
                return
            payload = resp.json()
            if not payload:
                return
            coords = payload[0].get('geometry', {}).get('coordinates')
            if isinstance(coords, list) and len(coords) >= 2:
                data['basic.longitude'] = float(coords[0])
                data['basic.latitude'] = float(coords[1])
                return
        except Exception as e:
            logger.warning(f"Geocoding failed: {e}")

        # Fallback: OpenStreetMap Nominatim, useful when GSI returns no result.
        try:
            resp = requests.get(
                'https://nominatim.openstreetmap.org/search',
                params={'q': address, 'format': 'jsonv2', 'limit': 1},
                headers={'User-Agent': 'sumo-digger/1.0'},
                timeout=8,
            )
            if resp.status_code != 200:
                return
            payload = resp.json()
            if not payload:
                return
            item = payload[0]
            lon = item.get('lon')
            lat = item.get('lat')
            if lon and lat:
                data['basic.longitude'] = float(lon)
                data['basic.latitude'] = float(lat)
        except Exception as e:
            logger.warning(f"Nominatim geocoding failed: {e}")

    # ── Investment analysis ──────────────────────────────────────────

    def _calc_investment_analysis(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        if ptype == PropertyType.RENTAL:
            return

        price = data.get('basic.price_jpy')
        if not price:
            return
        price = int(price)

        data['analysis.property_price'] = price

        brokerage = calc_brokerage_fee(price)
        data['analysis.brokerage_fee'] = brokerage

        acq_tax = data.get('analysis.acquisition_tax')
        reg_tax = data.get('analysis.registration_tax')
        stamp = data.get('analysis.stamp_tax')
        scriv = data.get('analysis.scrivener_fee')

        if all(v is not None for v in [acq_tax, reg_tax, stamp, scriv]):
            data['analysis.total_spend'] = (
                price + int(acq_tax) + int(reg_tax) + int(stamp) + int(scriv) + brokerage
            )

        # Annual cost
        fixed_tax = _to_int(data.get('analysis.fixed_asset_tax'))
        city_tax = _to_int(data.get('analysis.city_planning_tax'))
        mgmt = _to_int(data.get('analysis_or_detail.management_fee'))
        repair = _to_int(data.get('analysis_or_detail.repair_fee'))
        trust = _to_int(data.get('analysis.trust_fee'))
        other = _sum_other_fees(data.get('analysis_or_detail.other_fees'))

        annual_cost_parts = [fixed_tax, city_tax, mgmt, repair, trust, other]
        if any(p for p in annual_cost_parts):
            data['analysis.annual_total_cost'] = sum(p or 0 for p in annual_cost_parts)

        monthly_rent = _to_int(data.get('analysis.monthly_rent_income'))
        if monthly_rent:
            annual_rent = monthly_rent * 12
            data['analysis.annual_rent_income'] = annual_rent
            roi = calc_annual_roi(annual_rent, price)
            if roi is not None:
                data['analysis.annual_roi_pct'] = roi

    # ── Rental initial fees ──────────────────────────────────────────

    def _calc_rental_initial_fees(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        if ptype != PropertyType.RENTAL:
            return

        rent = _to_int(data.get('rent.rent')) or _to_int(data.get('rent.initial_rent'))
        if rent and not data.get('rent.initial_rent'):
            data['rent.initial_rent'] = rent

        deposit = _to_int(data.get('rent.deposit'))
        key_money = _to_int(data.get('rent.key_money'))
        brokerage = _to_int(data.get('analysis.brokerage_fee'))
        other = _sum_other_fees(data.get('analysis_or_detail.other_fees'))

        parts = [rent, deposit, key_money, brokerage, other]
        if rent:
            data['rent.initial_total_fee'] = sum(p or 0 for p in parts)

    # ── AI-generated fields (requires LLM) ───────────────────────────

    def _ai_generate_fields(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        """Generate AI fields using LLM. Each is a separate call for resilience."""
        address = data.get('basic.address', '')

        if not data.get('basic.nearby_landmark') and address:
            self._gen_landmark(data, address)

        if not data.get('basic.tags'):
            self._gen_tags(data, ptype)

        if not _has_n_candidates(data.get('basic.property_name'), 3):
            self._gen_title_candidates(data, ptype)

        if not _has_n_candidates(data.get('ai.description_candidates'), 3):
            self._gen_descriptions(data, ptype)

        if not data.get('poi.schools') or not data.get('poi.business_districts') or not data.get('poi.parks'):
            self._gen_poi_lists(data)

        if not data.get('basic.estimated_rent') and ptype != PropertyType.RENTAL:
            self._gen_estimated_rent(data, ptype)

        if ptype != PropertyType.RENTAL:
            self._gen_tax_estimates(data, ptype)

    def _gen_landmark(self, data: Dict[str, Any], address: str) -> None:
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": "你是日本地理专家。根据给定的日本地址，返回2-6字的周边标志性地点名（如附近的大学、公园、车站或景点）。只输出地点名，不要其他内容。"},
                    {"role": "user", "content": address},
                ],
                temperature=0.3, max_tokens=50,
            ).strip()
            if text and len(text) <= 20:
                data['basic.nearby_landmark'] = text
        except Exception as e:
            logger.warning(f"Landmark generation failed: {e}")

    def _gen_tags(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        prop_info = self._build_prop_summary(data, ptype)
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": (
                        "你是日本房产信息编辑，必须只使用简体中文输出。\n"
                        "根据物件信息生成 5-10 个左右的房源标签，每个标签 2-4 个字。\n"
                        "标签要适合发布展示，优先突出交通、朝向、装修、景观、楼层、学区、配套、投资亮点。\n"
                        "允许保留极少量必要的日本站名/地名原文，但标签主体严禁使用日语假名。\n"
                        "返回 JSON 数组格式，如[\"近车站\",\"采光好\",\"新装修\"]。只输出 JSON 数组。"
                    )},
                    {"role": "user", "content": prop_info},
                ],
                temperature=0.5, max_tokens=200,
            )
            parsed = extract_json(text)
            if isinstance(parsed, list):
                tags = self._normalize_text_candidates(parsed, limit=10)
                if self._needs_chinese_retry(tags):
                    retry_text = self.llm.chat(
                        messages=[
                            {"role": "system", "content": "重新生成 5-10 个左右的房源标签。必须是简体中文，每个标签 2-4 个字，只输出 JSON 数组，禁止日语假名。"},
                            {"role": "user", "content": prop_info},
                        ],
                        temperature=0.3, max_tokens=200,
                    )
                    retry_parsed = extract_json(retry_text)
                    if isinstance(retry_parsed, list):
                        tags = self._normalize_text_candidates(retry_parsed, limit=10)
                if tags:
                    data['basic.tags'] = tags
        except Exception as e:
            logger.warning(f"Tag generation failed: {e}")

    def _gen_title_candidates(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        prop_info = self._build_prop_summary(data, ptype)
        title_rule = self._title_template_for_type(ptype)
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": (
                        "你是日本房产信息编辑。必须使用简体中文生成标题，严禁使用日语作为标题主体语言。\n"
                        "保留日本地名/站名原文，但其他描述必须是简体中文。\n"
                        "请根据物件信息生成 3 个标题候选，返回 JSON 数组。\n"
                        "标题规则：20-40 字，不加句号，不要解释文字。\n"
                        f"优先遵循格式：{title_rule}"
                    )},
                    {"role": "user", "content": prop_info},
                ],
                temperature=0.6, max_tokens=300,
            )
            parsed = extract_json(text)
            if isinstance(parsed, list) and parsed:
                titles = self._normalize_text_candidates(parsed, limit=3)
                if self._needs_chinese_retry(titles):
                    retry_text = self.llm.chat(
                        messages=[
                            {"role": "system", "content": (
                                "重新生成 3 个标题候选。必须使用简体中文，禁止日语假名作为主体描述。"
                                f"格式参考：{title_rule}。只输出 JSON 数组。"
                            )},
                            {"role": "user", "content": prop_info},
                        ],
                        temperature=0.4, max_tokens=300,
                    )
                    retry_parsed = extract_json(retry_text)
                    if isinstance(retry_parsed, list):
                        titles = self._normalize_text_candidates(retry_parsed, limit=3)
                if titles:
                    data['basic.property_name'] = _merge_candidate_values(data.get('basic.property_name'), titles, limit=3)
        except Exception as e:
            logger.warning(f"Title generation failed: {e}")

    def _gen_descriptions(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        prop_info = self._build_prop_summary(data, ptype)
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": (
                        "你是日本房产信息编辑。必须使用简体中文写 3 个版本的房源介绍短文，"
                        "每个 200-300 字。地名/站名可保留日文，其他描述必须为简体中文。语气专业简洁。\n"
                        "返回JSON数组格式：[\"版本1...\", \"版本2...\", \"版本3...\"]"
                    )},
                    {"role": "user", "content": prop_info},
                ],
                temperature=0.7, max_tokens=4096,
            )
            parsed = extract_json(text)
            if isinstance(parsed, list) and len(parsed) >= 1:
                descriptions = self._normalize_text_candidates(parsed, limit=3)
                if self._needs_chinese_retry(descriptions):
                    retry_text = self.llm.chat(
                        messages=[
                            {"role": "system", "content": "重新生成 3 个介绍版本。所有描述必须为简体中文，仅地名和站名可保留日文。只输出 JSON 数组。"},
                            {"role": "user", "content": prop_info},
                        ],
                        temperature=0.4, max_tokens=4096,
                    )
                    retry_parsed = extract_json(retry_text)
                    if isinstance(retry_parsed, list):
                        descriptions = self._normalize_text_candidates(retry_parsed, limit=3)
                if descriptions:
                    data['ai.description_candidates'] = _merge_candidate_values(
                        data.get('ai.description_candidates'),
                        descriptions,
                        limit=3,
                    )
        except Exception as e:
            logger.warning(f"Description generation failed: {e}")

    def _gen_estimated_rent(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        prop_info = self._build_prop_summary(data)
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": (
                        "你是日本不动产评估专家。根据物件信息估算月租金（日元）。"
                        "只返回一个纯数字（不要单位），如 85000。"
                    )},
                    {"role": "user", "content": prop_info},
                ],
                temperature=0.3, max_tokens=50,
            ).strip()
            import re
            m = re.search(r'\d+', text.replace(',', ''))
            if m:
                data['basic.estimated_rent'] = int(m.group())
                if not data.get('analysis.monthly_rent_income'):
                    data['analysis.monthly_rent_income'] = int(m.group())
        except Exception as e:
            logger.warning(f"Rent estimation failed: {e}")

    def _gen_poi_lists(self, data: Dict[str, Any]) -> None:
        address = data.get('basic.address')
        landmark = data.get('basic.nearby_landmark')
        if not address and not landmark:
            return
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": (
                        "你是日本房产周边配套整理助手。请严格基于房源地址与周边地标，整理步行15分钟内的周边配套。"
                        "学校仅保留大学、语言学校、专门学校；商圈和公园名称尽量保留日文。"
                        "学校、商圈、公园设施各最多5条，不足可少于5条，不要编造超出15分钟步行范围的地点。"
                        "每条格式为：名称【距离xxkm 步行xx分钟】。返回JSON对象，包含 schools、business_districts、parks 三个数组。"
                    )},
                    {"role": "user", "content": f"地址: {address or '-'}\n附近标志: {landmark or '-'}"},
                ],
                temperature=0.4, max_tokens=1200,
            )
            parsed = extract_json(text)
            if isinstance(parsed, dict):
                if isinstance(parsed.get('schools'), list) and not data.get('poi.schools'):
                    data['poi.schools'] = parsed['schools'][:5]
                if isinstance(parsed.get('business_districts'), list) and not data.get('poi.business_districts'):
                    data['poi.business_districts'] = parsed['business_districts'][:5]
                if isinstance(parsed.get('parks'), list) and not data.get('poi.parks'):
                    data['poi.parks'] = parsed['parks'][:5]
        except Exception as e:
            logger.warning(f"POI generation failed: {e}")

    def _gen_tax_estimates(self, data: Dict[str, Any], ptype: PropertyType) -> None:
        """Use LLM to estimate Japanese taxes if not already set."""
        price = data.get('basic.price_jpy')
        if not price:
            return

        needs = []
        tax_keys = [
            ('analysis.acquisition_tax', '不动产取得税'),
            ('analysis.registration_tax', '登录免许税'),
            ('analysis.stamp_tax', '印花税'),
            ('analysis.scrivener_fee', '司法书士费'),
            ('analysis.fixed_asset_tax', '固定资产税'),
            ('analysis.city_planning_tax', '都市计划税'),
        ]
        for key, label in tax_keys:
            if not data.get(key):
                needs.append((key, label))

        if not needs:
            return

        labels_str = '、'.join(label for _, label in needs)
        try:
            text = self.llm.chat(
                messages=[
                    {"role": "system", "content": (
                        "你是日本不动产税务专家。根据物件价格估算各项税费（日元整数）。\n"
                        "返回JSON对象，key是费用名称，value是整数金额。不要单位。\n"
                        "参考规则：不动产取得税约为评估额x3-4%，登录免许税约为评估额x0.4-2%，"
                        "印花税按合同金额分段（1000万以下1万、5000万以下2万等），"
                        "司法书士费通常10-15万，固定资产税约评估额x1.4%，都市计划税约评估额x0.3%。"
                    )},
                    {"role": "user", "content": f"物件价格: {price}日元\n请估算: {labels_str}"},
                ],
                temperature=0.2, max_tokens=500,
            )
            parsed = extract_json(text)
            if isinstance(parsed, dict):
                for key, label in needs:
                    val = parsed.get(label)
                    if val is not None:
                        try:
                            data[key] = int(val)
                        except (ValueError, TypeError):
                            pass

                # Recalculate total_spend after tax estimates
                self._calc_investment_analysis(data, ptype)
        except Exception as e:
            logger.warning(f"Tax estimation failed: {e}")

    def _build_prop_summary(self, data: Dict[str, Any], ptype: Optional[PropertyType] = None) -> str:
        """Build a concise text summary of property for LLM context."""
        skip = {'media.images', 'media.videos', 'media.vr_links',
                'ai.description_candidates', 'poi.schools',
                'poi.business_districts', 'poi.parks'}
        label_map = get_key_to_label(ptype or PropertyType.OTHER) if ptype else {}
        lines = []
        for k, v in data.items():
            if k.startswith('_') or k in skip or not v:
                continue
            label = label_map.get(k, k)
            lines.append(f"{label}: {v}")
        return '\n'.join(lines[:40])

    def _title_template_for_type(self, ptype: PropertyType) -> str:
        templates = {
            PropertyType.RENTAL: '地区 + 特点 + 租房 + 户型 + 类型 + 特点短句',
            PropertyType.MANSION: '地区 + 特点 + 户型 + 类型（塔楼/公寓） + 特点短句',
            PropertyType.HOUSE: '地区 + 特点 + 户型 + 一户建 + 特点短句',
            PropertyType.LAND: '地区 + 特点 + 土地面积 + 土地 + 特点短句',
            PropertyType.INVESTMENT: '地区 + 特点 + 户型/类型 + 投资物件 + 特点短句',
            PropertyType.OTHER: '地区 + 特点 + 物件类型 + 特点短句',
        }
        return templates.get(ptype, templates[PropertyType.OTHER])

    def _normalize_text_candidates(self, values: Any, limit: int) -> List[str]:
        items = _normalize_candidate_list(values)
        deduped: List[str] = []
        seen = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def _needs_chinese_retry(self, items: List[str]) -> bool:
        if not items:
            return False
        for item in items:
            kana_count = len(_JAPANESE_KANA_RE.findall(item))
            if kana_count >= 3:
                return True
        return False


# ── Helpers ──────────────────────────────────────────────────────────────

def _to_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _sum_other_fees(fees: Any) -> int:
    """Sum numeric values from other_fees array."""
    if not fees or not isinstance(fees, list):
        return 0
    total = 0
    for item in fees:
        if isinstance(item, dict):
            for v in item.values():
                try:
                    total += int(v)
                except (ValueError, TypeError):
                    pass
        elif isinstance(item, (int, float)):
            total += int(item)
    return total


def _normalize_candidate_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]

    result: List[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _has_n_candidates(value: Any, n: int) -> bool:
    return len(_normalize_candidate_list(value)) >= n


def _merge_candidate_values(existing: Any, new_values: Any, limit: int = 3) -> List[str]:
    merged: List[str] = []
    seen = set()
    for item in _normalize_candidate_list(existing) + _normalize_candidate_list(new_values):
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged
