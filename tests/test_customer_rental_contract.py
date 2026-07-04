import json
from pathlib import Path

from app.schema.mapper import FieldMapper
from app.schema.property_types import PropertyType
from app.services.ai_enrichment import AIEnrichmentService


FIXTURE = Path(__file__).parent / "fixtures" / "customer_rental_contract_cases.json"


class StaticRateEnrichment(AIEnrichmentService):
    @property
    def exchange_rate(self) -> float:
        return 0.05

    def _geocode_address(self, data):
        return None


def load_cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_customer_rental_contract_cases():
    mapper = FieldMapper()
    service = StaticRateEnrichment(llm_client=None)

    for case in load_cases():
        normalized = mapper.normalize(case["raw"], PropertyType.RENTAL, source_url=case["source_url"])
        enriched = service.enrich(dict(normalized), PropertyType.RENTAL)
        for key, expected in case["expected"].items():
            assert enriched.get(key) == expected, f"{case['name']} {key}"
        assert enriched.get("basic.price_cny") == int(enriched["rent.rent"] * 0.05)
        facilities = enriched.get("amenities.facilities")
        if facilities:
            assert isinstance(facilities, list)
