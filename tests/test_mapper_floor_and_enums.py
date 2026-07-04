"""Tests for mapper fixes: apartment/tower floor totals, structure enums,
built_month absolute preference, sub-type, layout_raw, other_fees, remark."""

import pytest

from app.schema.mapper import FieldMapper, _map_enum
from app.schema.property_types import PropertyType
from app.schema import enums as E


# ── Item 1: apartment/tower total_floors must take the 階建 total, not first digit ──

@pytest.mark.parametrize("raw,expected_total", [
    ({'階': '7階', '階建': '7階/15階建'}, 15),
    ({'階': '40階', '階建': '地下2地上60階建'}, 60),
    ({'階': '1階', '階建': '1階/2階建'}, 2),
])
def test_mansion_total_floors_uses_building_total(raw, expected_total):
    mapper = FieldMapper()
    out = mapper.normalize(dict(raw), PropertyType.MANSION)
    assert out.get('detail.total_floors') == expected_total


# ── Item 2: 構造 abbreviations 鉄筋コン / 鉄骨鉄筋コン must map ──

def test_structure_abbreviation_rc():
    assert _map_enum('鉄筋コン', E.STRUCTURE_JP_MAP, E.STRUCTURE) == 'RC造 钢筋混凝土造'


def test_structure_abbreviation_src_wins_over_rc():
    # SRC must be checked before RC so the 鉄筋コン substring doesn't win first.
    assert _map_enum('鉄骨鉄筋コン', E.STRUCTURE_JP_MAP, E.STRUCTURE) == 'SRC造 钢骨钢筋混凝土造'


def test_structure_abbreviation_end_to_end():
    mapper = FieldMapper()
    out = mapper.normalize({'構造': '鉄筋コン'}, PropertyType.MANSION)
    assert out.get('detail.structure') == 'RC造 钢筋混凝土造'


# ── Item 3: built_month must prefer the absolute 築年月 over relative 築年数 ──

@pytest.mark.parametrize("raw", [
    {'築年数': '築45年', '築年月': '1981年3月'},   # relative key first
    {'築年月': '1981年3月', '築年数': '築45年'},   # absolute key first
])
def test_built_month_prefers_absolute_year_month(raw):
    mapper = FieldMapper()
    out = mapper.normalize(dict(raw), PropertyType.MANSION)
    assert out.get('basic.built_month') == '1981年3月'


# ── Item 4: SUB_TYPE_JP_MAP タウンハウス → 一户建 ──

def test_sub_type_townhouse_maps_to_detached():
    assert _map_enum('タウンハウス', E.SUB_TYPE_JP_MAP, E.SUB_TYPE_ALL) == '一户建'


# ── Item 5: preserve raw 間取り as basic.layout_raw (layout_cn keeps CN) ──

def test_layout_raw_preserves_ldk_original():
    mapper = FieldMapper()
    out = mapper.normalize({'間取り': '1LDK'}, PropertyType.RENTAL)
    assert out.get('basic.layout_raw') == '1LDK'
    assert out.get('basic.layout_cn') == '一居室'


def test_layout_raw_preserves_one_room_original():
    mapper = FieldMapper()
    out = mapper.normalize({'間取り': 'ワンルーム'}, PropertyType.RENTAL)
    assert out.get('basic.layout_raw') == 'ワンルーム'
    assert out.get('basic.layout_cn') == '一居室'
