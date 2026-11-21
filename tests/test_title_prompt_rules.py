"""Item 6: title prompt hard rules (no big place names, must contain 租房,
Japanese layout original) and feeding layout_raw into the LLM context."""

import json

from app.schema.property_types import PropertyType
from app.services.ai_enrichment import AIEnrichmentService


class CapturingLLM:
    """Records every chat() call and returns a canned reply."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


def _all_text(call):
    return '\n'.join(m['content'] for m in call['messages'])


def test_title_prompt_has_hard_rules_and_layout_raw():
    llm = CapturingLLM(json.dumps(['站前一居室租房', '町名租房好房', '通勤便利租房'],
                                  ensure_ascii=False))
    service = AIEnrichmentService(llm_client=llm)
    data = {
        'basic.address': '大阪府大阪市天王寺区舟橋町1-2',
        'basic.city_ward': '大阪府大阪市天王寺区',
        'basic.layout_raw': '1LDK',
        'basic.layout_cn': '一居室',
    }

    service._gen_title_candidates(data, PropertyType.RENTAL)

    assert llm.calls, "LLM should be called"
    system_text = llm.calls[0]['messages'][0]['content']
    # (1) hard rules present
    assert '禁止' in system_text
    assert '町' in system_text
    assert '租房' in system_text
    assert '1LDK' in system_text or '原文' in system_text
    # (2) layout_raw value fed into the prop context (user message)
    full = _all_text(llm.calls[0])
    assert '1LDK' in full


def test_buy_title_prompt_starts_place_at_town_level():
    """2026-11-21 客户要求：所有物件标题的地名从町名/丁目开始，买房不能再以「千葉市緑区」开头。"""
    for ptype in (PropertyType.HOUSE, PropertyType.MANSION, PropertyType.LAND, PropertyType.INVESTMENT):
        llm = CapturingLLM(json.dumps(['土気町翻新3LDK一户建', 'あすみが丘7丁目一户建', '土気町宽敞一户建'],
                                      ensure_ascii=False))
        service = AIEnrichmentService(llm_client=llm)
        service._gen_title_candidates({'basic.address': '千葉県千葉市緑区土気町'}, ptype)

        system_text = llm.calls[0]['messages'][0]['content']
        assert '町名/丁目' in system_text, ptype
        assert '禁止' in system_text and '市/区' in system_text, ptype
        assert '租房' not in system_text, ptype  # 租房专有规则不套到买房
