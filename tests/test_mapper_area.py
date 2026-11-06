"""面积清洗：「m2」是一个单位，末尾的 2 不能并进数值（20m2 → 20，不是 202）。"""

import pytest

from app.schema.mapper import FieldMapper, clean_number
from app.schema.property_types import PropertyType


@pytest.mark.parametrize('text,expected', [
    ('20m2', 20),
    ('21.04m2', 21.04),
    ('25.5 m2', 25.5),
    ('158.6m2（47.97坪）', 158.6),
    ('20m²', 20),
    ('21.04㎡', 21.04),
])
def test_clean_number_strips_square_metre_unit_whole(text, expected):
    assert clean_number(text) == expected


def test_rental_area_written_as_m2_keeps_its_value():
    out = FieldMapper().normalize(
        {'専有面積': '21.04m2'}, PropertyType.RENTAL,
        source_url='https://suumo.jp/chintai/jnc_000110935824/?bc=100535562071',
    )
    assert out['basic.area'] == 21.04
