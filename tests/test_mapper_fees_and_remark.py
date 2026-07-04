"""Tests for fee extraction (鍵交換/保証会社/損保 → other_fees) and remark summary."""

from app.schema.mapper import FieldMapper
from app.schema.property_types import PropertyType


# ── Item 7: 鍵交換/保証会社/損保 collected into analysis_or_detail.other_fees ──

def test_other_fees_collect_key_exchange_and_insurance():
    mapper = FieldMapper()
    out = mapper.normalize(
        {'鍵交換': '22000円', '保証会社': '家賃50%', '損保': '20000円'},
        PropertyType.RENTAL,
    )
    fees = out.get('analysis_or_detail.other_fees')
    assert isinstance(fees, list)
    names = [f.get('name') for f in fees if isinstance(f, dict)]
    # 鍵交換 (钥匙更换) must be present with numeric amount
    key_fee = next(
        (f for f in fees if isinstance(f, dict) and '鍵交換' in str(f.get('name'))
         or '钥匙' in str(f.get('name'))),
        None,
    )
    assert key_fee is not None
    assert key_fee.get('amount') == 22000
    # 損保 (火灾保险) should be collected too
    assert any('損保' in str(n) or '保険' in str(n) or '保险' in str(n) for n in names)


# ── Item 8: detail.remark built as a Chinese fee summary ──

def test_remark_chinese_summary_from_fees():
    mapper = FieldMapper()
    out = mapper.normalize(
        {'鍵交換': '3000円', '損保': '20000円'},
        PropertyType.RENTAL,
    )
    remark = out.get('detail.remark')
    assert isinstance(remark, str) and remark
    assert '钥匙更换费' in remark


def test_percentage_fee_not_mistaken_for_yen_amount():
    """『家賃50%』类相对费用不能被当成 50 円绝对金额(否则 remark 误导、总额虚算)。"""
    mapper = FieldMapper()
    out = mapper.normalize(
        {'保証会社': '家賃50%', '鍵交換': '22000円'},
        PropertyType.RENTAL,
    )
    fees = out.get('analysis_or_detail.other_fees')
    guarantee = next(
        (f for f in fees if isinstance(f, dict) and '担保' in str(f.get('name'))),
        None,
    )
    assert guarantee is not None
    # 百分比/家賃相对值不应写入 amount(会被 _sum_other_fees 错误计入总额)
    assert guarantee.get('amount') is None, f"相对费用不应有 amount, 实际={guarantee.get('amount')}"
    # raw 原文保留
    assert guarantee.get('raw') == '家賃50%'
    # remark 应展示原文『家賃50%』, 绝不能出现误导的『50円』
    remark = out.get('detail.remark') or ''
    assert '家賃50%' in remark
    assert '50円' not in remark, f"remark 不得把百分比写成 50円: {remark}"
