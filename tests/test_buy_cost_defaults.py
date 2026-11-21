"""买房司法书士费：客户要求默认 60000，不再让大模型估（旧提示词写「通常10-15万」）。

实际总支出按 60000 计，和日宜找房草稿里的司法书士费一致。
"""

import json

from app.schema.property_types import PropertyType
from app.services.ai_enrichment import SCRIVENER_FEE_DEFAULT, AIEnrichmentService


class TaxLLM:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps({'不动产取得税': 300000, '登录免许税': 80000, '印花税': 10000,
                           '司法书士费': 150000, '固定资产税': 50000, '都市计划税': 10000},
                          ensure_ascii=False)


def _service(llm=None):
    service = AIEnrichmentService(llm_client=llm)
    service._rate = 0.05  # 不访问汇率接口
    return service


def test_scrivener_fee_defaults_without_llm():
    for ptype in (PropertyType.HOUSE, PropertyType.MANSION, PropertyType.LAND, PropertyType.INVESTMENT):
        data = {'basic.price_jpy': 18_990_000}
        _service().enrich(data, ptype)
        assert SCRIVENER_FEE_DEFAULT == 60000
        assert data['analysis.scrivener_fee'] == 60000, ptype


def test_rental_has_no_scrivener_fee():
    data = {'rent.rent': 80000}
    _service().enrich(data, PropertyType.RENTAL)
    assert 'analysis.scrivener_fee' not in data


def test_llm_not_asked_for_scrivener_and_total_uses_default():
    llm = TaxLLM()
    service = _service(llm)
    data = {'basic.price_jpy': 18_990_000}
    service._calc_investment_analysis(data, PropertyType.HOUSE)
    service._gen_tax_estimates(data, PropertyType.HOUSE)

    asked = '\n'.join(m['content'] for m in llm.calls[0]['messages'])
    assert '司法书士费' not in asked
    assert data['analysis.scrivener_fee'] == 60000
    brokerage = data['analysis.brokerage_fee']
    assert data['analysis.total_spend'] == 18_990_000 + 300000 + 80000 + 10000 + 60000 + brokerage
