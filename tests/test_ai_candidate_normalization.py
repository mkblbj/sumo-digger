"""Tests for AI-generated title/description candidate normalization."""

import json

from app.routes import _normalize_summary_items
from app.schema.property_types import PropertyType
from app.services.ai_enrichment import AIEnrichmentService


class FakeLLM:
    def __init__(self, replies):
        self._replies = iter(replies)

    def chat(self, **_kwargs):
        return next(self._replies)


def test_title_candidates_extract_title_from_objects_and_limit_to_30_chars():
    long_title = '元町駅近顶层一居室通勤生活非常便利适合单身入住带电梯安防完善超长标题'
    service = AIEnrichmentService(llm_client=FakeLLM([
        json.dumps([
            {'title': long_title, 'description': '不应进入标题字段'},
        ], ensure_ascii=False),
    ]))
    data = {'basic.address': '兵库县神户市中央区元町通5'}

    service._gen_title_candidates(data, PropertyType.RENTAL)

    assert data['basic.property_name'] == [long_title[:30]]
    assert all(len(title) <= 30 for title in data['basic.property_name'])


def test_description_candidates_extract_description_from_objects():
    description = '这套房源位于元町通，交通便利，适合重视通勤效率与生活便利性的租客。'
    service = AIEnrichmentService(llm_client=FakeLLM([
        json.dumps([
            {'title': '站前一居室', 'description': description},
        ], ensure_ascii=False),
    ]))
    data = {'basic.address': '兵库县神户市中央区元町通5'}

    service._gen_descriptions(data, PropertyType.RENTAL)

    assert data['ai.description_candidates'] == [description]


def test_enrichment_cleans_existing_object_candidates_without_llm_call():
    class NoCallLLM:
        def chat(self, **_kwargs):
            raise AssertionError('clean cached candidates without calling LLM')

    long_title = '元町駅近顶层一居室通勤生活非常便利适合单身入住带电梯安防完善超长标题'
    data = {
        'basic.tags': ['近车站'],
        'basic.property_name': [
            {'title': long_title, 'description': '不应进入标题字段'},
            {'title': '花隈駅旁一居室'},
            {'title': '通勤便利单身公寓'},
        ],
        'ai.description_candidates': [
            {'title': '标题', 'description': '介绍一'},
            {'description': '介绍二'},
            {'description': '介绍三'},
        ],
    }
    service = AIEnrichmentService(llm_client=NoCallLLM())

    service.enrich(data, PropertyType.RENTAL)

    assert data['basic.property_name'] == [long_title[:30], '花隈駅旁一居室', '通勤便利单身公寓']
    assert data['ai.description_candidates'] == ['介绍一', '介绍二', '介绍三']


def test_route_summary_normalizer_extracts_description_from_object_items():
    items = _normalize_summary_items(
        [{'title': '站前一居室', 'description': '交通便利，生活配套完善。'}],
        limit=3,
        preferred_key='description',
    )

    assert items == ['交通便利，生活配套完善。']


def test_route_summary_normalizer_extracts_stringified_object_and_limits_title():
    title = '元町駅近顶层一居室通勤生活非常便利适合单身入住带电梯安防完善超长标题'
    items = _normalize_summary_items(
        [f"{{'title': '{title}', 'description': '介绍文本'}}"],
        limit=3,
        preferred_key='title',
        max_chars=30,
    )

    assert items == [title[:30]]
