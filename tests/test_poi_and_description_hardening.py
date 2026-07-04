"""Item 9: POI generation retry + three-key enforcement + truncated JSON repair.
Item 10: description fallback branch must apply the Chinese check."""

import json

from app.services.ai_enrichment import AIEnrichmentService
from app.services.llm_client import extract_json


class SequenceLLM:
    """Returns a queued sequence of replies, records call count."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        idx = len(self.calls) - 1
        if idx < len(self._replies):
            return self._replies[idx]
        return self._replies[-1]


# ── Item 9a/9c: missing 'parks' key must trigger a retry ──

def test_poi_retries_when_a_key_is_missing():
    first = json.dumps({
        'schools': ['A大学【0.5km 步行6分钟】'],
        'business_districts': ['B商店街【0.3km 步行4分钟】'],
        # parks missing
    }, ensure_ascii=False)
    second = json.dumps({
        'schools': ['A大学【0.5km 步行6分钟】'],
        'business_districts': ['B商店街【0.3km 步行4分钟】'],
        'parks': ['C公园【0.4km 步行5分钟】'],
    }, ensure_ascii=False)
    llm = SequenceLLM([first, second])
    service = AIEnrichmentService(llm_client=llm)
    data = {'basic.address': '大阪府大阪市天王寺区舟橋町1', 'basic.nearby_landmark': '天王寺'}

    service._gen_poi_lists(data)

    assert len(llm.calls) >= 2, "missing key should trigger a retry"
    assert data.get('poi.parks') == ['C公园【0.4km 步行5分钟】']
    # max_tokens raised
    assert llm.calls[0]['max_tokens'] >= 2500


def test_poi_retry_is_capped():
    # Every reply keeps missing 'parks' -> should stop after bounded retries.
    bad = json.dumps({'schools': [], 'business_districts': []}, ensure_ascii=False)
    llm = SequenceLLM([bad, bad, bad, bad, bad])
    service = AIEnrichmentService(llm_client=llm)
    data = {'basic.address': '大阪府大阪市天王寺区舟橋町1'}

    service._gen_poi_lists(data)

    # initial + at most 2 retries = 3 calls
    assert len(llm.calls) <= 3


# ── Item 9d: extract_json repairs truncated JSON ──

def test_extract_json_repairs_truncated_object():
    truncated = '{"schools": ["A大学"], "business_districts": ["B街"], "parks": ["C公园"'
    parsed = extract_json(truncated)
    assert isinstance(parsed, dict)
    assert parsed.get('schools') == ['A大学']
    assert parsed.get('parks') == ['C公园']


def test_poi_recovers_from_truncated_json():
    truncated = ('{"schools": ["A大学【0.5km 步行6分钟】"], '
                 '"business_districts": ["B商店街【0.3km 步行4分钟】"], '
                 '"parks": ["C公园【0.4km 步行5分钟】"')  # missing closing ]}
    llm = SequenceLLM([truncated])
    service = AIEnrichmentService(llm_client=llm)
    data = {'basic.address': '大阪府大阪市天王寺区舟橋町1'}

    service._gen_poi_lists(data)

    assert data.get('poi.schools') == ['A大学【0.5km 步行6分钟】']
    assert data.get('poi.parks') == ['C公园【0.4km 步行5分钟】']


# ── Item 10: description fallback must not cache Japanese text ──

def test_summary_fallback_rejects_japanese_text():
    from app.routes import _summary_fallback_items

    jp_text = 'この物件は駅から徒歩5分で、周辺には買い物施設が充実しており、非常に便利な立地です。'
    items = _summary_fallback_items(jp_text, 'summary', limit=3,
                                    preferred_key='description', max_chars=None)
    # Japanese fallback must not be accepted for caching.
    assert items == []


def test_summary_fallback_accepts_chinese_text():
    from app.routes import _summary_fallback_items

    cn_text = '这套房源距离车站步行5分钟，周边购物设施齐全，生活非常便利。'
    items = _summary_fallback_items(cn_text, 'summary', limit=3,
                                    preferred_key='description', max_chars=None)
    assert items == [cn_text]
