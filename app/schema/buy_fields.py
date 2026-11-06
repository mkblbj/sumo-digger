# -*- coding: utf-8 -*-
"""买房详情字段解析：售价、月费、面积、建蔽率/容积率、楼层与构造。

输入是 SUUMO 物件表格里的原文（已去掉提示节点与链接文字），输出统一为
日元整数、平方米浮点数和百分数数值。无法确定的值返回 None，由调用方保留原文。
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

_MISSING_TEXT = {'', '-', '―', '－', 'ー', '—'}
_FREE_TEXT = {'無', 'なし', '無し', '無料', '0円'}
_RANGE_RE = re.compile(r'[~〜]|台$|台\(|・\d')
_PRICE_RE = re.compile(r'^(?:(\d+(?:\.\d+)?)億)?(?:(\d+(?:\.\d+)?)万)?(\d+)?円')
_AREA_RE = re.compile(r'(\d+(?:\.\d+)?)\s*m2?')
_FLOOR_TOTAL_RE = re.compile(r'(\d+)\s*階建')
_FLOOR_CURRENT_RE = re.compile(r'^\s*(地下)?(\d+)\s*階')
_STRUCTURE_TOKEN_RE = re.compile(r'^(.*?)(?=地下|地上|\d|$)')
_PERCENT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%')


def nfkc(text) -> str:
    """全角数字、括号、斜线、半角片假名等统一成 NFKC 形式。"""
    return unicodedata.normalize('NFKC', str(text if text is not None else '')).strip()


def _compact(text) -> str:
    return re.sub(r'\s+', '', nfkc(text)).replace(',', '')


def _num(value: str):
    n = float(value)
    return int(n) if n.is_integer() else n


def parse_price_yen(text) -> Tuple[str, Optional[int]]:
    """售价原文 → (状态, 日元)。状态为 fixed / range / undecided / missing。

    例：5990万円 → 59,900,000；1億2,500万円 → 125,000,000；未定、区间价返回 None。
    """
    t = _compact(text)
    if t in _MISSING_TEXT:
        return 'missing', None
    if '未定' in t:
        return 'undecided', None
    core = re.sub(r'\(.*?\)', '', t)
    if _RANGE_RE.search(core):
        return 'range', None
    m = _PRICE_RE.match(core)
    if not m or not any(m.groups()):
        return 'missing', None
    oku, man, yen = m.groups()
    total = 0.0
    if oku:
        total += float(oku) * 100_000_000
    if man:
        total += float(man) * 10_000
    if yen:
        total += float(yen)
    return 'fixed', int(round(total))


def parse_monthly_fee(text) -> Optional[int]:
    """月费原文 → 日元/月。2万800円／月 → 20,800；明确免费 → 0；缺失、待定或区间 → None。"""
    t = _compact(text)
    if t in _MISSING_TEXT:
        return None
    if t in _FREE_TEXT or t.startswith(('無料', 'なし')):
        return 0
    if '未定' in t or _RANGE_RE.search(re.sub(r'\(.*?\)', '', t)):
        return None
    m = re.match(r'^(?:(\d+(?:\.\d+)?)万)?(\d+)?円', t)
    if not m or not any(m.groups()):
        return None
    man, yen = m.groups()
    return int(round((float(man) * 10_000 if man else 0) + (float(yen) if yen else 0)))


def parse_area_sqm(text) -> Tuple[str, Optional[float]]:
    """面积原文 → (状态, 平方米)。35.67m²（壁芯） → 35.67；43.94m²～151.39m² → range。"""
    t = re.sub(r'(?<=\d),(?=\d{3})', '', nfkc(text))  # 千分位：1,234.56m² → 1234.56
    if re.sub(r'\s+', '', t) in _MISSING_TEXT:
        return 'missing', None
    values = _AREA_RE.findall(t)
    if not values:
        return 'missing', None
    if len(values) > 1 and re.search(r'[~〜]', t):
        return 'range', None
    return 'fixed', float(values[0])


def parse_coverage_far(text) -> Tuple[Optional[float], Optional[float], bool]:
    """建ぺい率・容积率原文 → (建蔽率%, 容积率%, 是否带附加条件)。

    支持「建ペい率：60％、容積率：80％」与「60％・188％」两种写法。
    """
    t = nfkc(text)
    if re.sub(r'\s+', '', t) in _MISSING_TEXT:
        return None, None, False
    cov = re.search(r'建[ぺペ]い率\s*:?\s*(\d+(?:\.\d+)?)\s*%', t)
    far = re.search(r'容積率\s*:?\s*(\d+(?:\.\d+)?)\s*%', t)
    all_percents = _PERCENT_RE.findall(t)
    # 括号里是附带条件（如「角地緩和により70％」），不当作建蔽率/容积率本身
    percents = _PERCENT_RE.findall(re.sub(r'\(.*?\)', '', t))
    if cov or far:
        coverage = _num(cov.group(1)) if cov else None
        ratio = _num(far.group(1)) if far else None
    elif len(percents) >= 2:
        coverage, ratio = _num(percents[0]), _num(percents[1])
    else:
        return None, None, bool(t)
    has_conditions = len(all_percents) > 2 or '(' in t
    return coverage, ratio, has_conditions


def split_floor_structure(text) -> Tuple[Optional[int], Optional[int], str]:
    """「9階/RC11階建」→ (9, 11, 'RC')；「木造2階建」→ (None, 2, '木造')。"""
    t = nfkc(text)
    if re.sub(r'\s+', '', t) in _MISSING_TEXT:
        return None, None, ''
    floor_part, _, building_part = t.partition('/') if '/' in t else ('', '', t)
    current = None
    if floor_part:
        m = _FLOOR_CURRENT_RE.match(floor_part)
        if m:
            current = -int(m.group(2)) if m.group(1) else int(m.group(2))
    # 「SRC14階地下1階建」：地下层数不能当成总层数
    total_match = _FLOOR_TOTAL_RE.search(re.sub(r'地下\s*\d+\s*階?', '', building_part))
    total = int(total_match.group(1)) if total_match else None
    token_match = _STRUCTURE_TOKEN_RE.match(building_part.strip())
    token = token_match.group(1).strip() if token_match else ''
    return current, total, token


def parse_int(text) -> Optional[int]:
    m = re.search(r'(\d+)', _compact(text))
    return int(m.group(1)) if m else None


def parse_listed_fees(text, period: str) -> List[dict]:
    """「町会費：300円／月、インターネット：1100円／月」→ [{name, raw, amount, period}]。"""
    t = nfkc(text)
    if re.sub(r'\s+', '', t) in _MISSING_TEXT:
        return []
    items = []
    for chunk in re.split(r'[、,\n]+', t):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition(':')
        fee = {'name': name.strip() if sep else '', 'raw': chunk, 'period': period}
        amount = parse_monthly_fee(value if sep else chunk)
        if amount is not None:
            fee['amount'] = amount
        items.append(fee)
    return items


def is_missing_text(text) -> bool:
    return re.sub(r'\s+', '', nfkc(text)) in _MISSING_TEXT


def is_planned_completion(text, today=None) -> bool:
    """完成时期是否为预计（写「予定」或晚于当前年月）。"""
    from datetime import date
    t = nfkc(text)
    if '予定' in t:
        return True
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月', t)
    if not m:
        return False
    d = today or date.today()
    return (int(m.group(1)), int(m.group(2))) > (d.year, d.month)
