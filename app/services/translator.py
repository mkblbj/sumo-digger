"""LLM-based Japanese to Chinese translation service."""

import json
import logging
from typing import Dict, Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# Keys whose values should NOT be translated (addresses, station names, numbers)
NO_TRANSLATE_KEYS = {
    '住所', '所在地', 'アクセス1', 'アクセス2', 'アクセス3',
    '最寄り駅1', '最寄り駅2', '最寄り駅3',
    '賃料', '賃料・初期費用', '管理費', '敷金', '礼金',
    '販売価格', '価格',
    '専有面積', '土地面積', '建物面積',
    '築年月', '築年数',
    'URL', '物件画像',
}

# Keys whose values are descriptive and SHOULD be translated
TRANSLATE_KEYS = {
    '物件名', '物件種別', '間取り詳細', '構造', '建物種別',
    '設備・条件', '備考', '特徴', 'その他', '周辺環境',
    '入居条件', '契約条件', '物件概要', '取引態様',
    '現況', '引渡し時期', 'リフォーム', '建物構造',
}


class TranslatorService:
    """Translate property data from Japanese to Chinese using OpenAI-compatible API."""

    SYSTEM_PROMPT = (
        "你是一个专业的日语到中文翻译器。用于翻译日本房产信息。\n"
        "规则：\n"
        "1. 仅翻译描述性文本（物件名、设备条件、备注等）\n"
        "2. 不翻译：地址、站名、价格、面积、日期等数值信息，保留日语原文\n"
        "3. 保持简洁专业\n"
        "4. 返回 JSON 对象，key 不变，value 是翻译后的中文\n"
        "5. 无法翻译的保留原文"
    )

    def __init__(self, base_url: str, api_key: str, model: str = 'gemini-2.5-flash'):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def translate_property(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Translate a property data dict.
        Returns a new dict with translated values where applicable.
        """
        # Separate fields into translatable and non-translatable
        to_translate = {}
        result = dict(data)  # Start with a copy

        for key, value in data.items():
            if not value or not isinstance(value, str):
                continue
            # Skip keys that shouldn't be translated
            if key in NO_TRANSLATE_KEYS:
                continue
            # Include keys that should be translated, or unknown descriptive-looking keys
            if key in TRANSLATE_KEYS or (
                key not in NO_TRANSLATE_KEYS
                and not key.startswith('_')
                and len(str(value)) > 2
                and not self._looks_numeric(str(value))
            ):
                to_translate[key] = value

        if not to_translate:
            return result

        try:
            translated = self._call_llm(to_translate)
            if translated:
                result.update(translated)
        except Exception as e:
            logger.error(f"Translation API error: {e}")
            # Return original data on failure

        return result

    def _call_llm(self, fields: Dict[str, str]) -> Optional[Dict[str, str]]:
        """Call LLM to translate a batch of fields."""
        user_msg = json.dumps(fields, ensure_ascii=False, indent=2)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=2000,
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON from response (handle markdown code blocks)
        if content.startswith('```'):
            # Strip ```json ... ```
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1])

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse LLM response as JSON: {content[:200]}")
            return None

    @staticmethod
    def _looks_numeric(s: str) -> bool:
        """Check if a string looks like a numeric/price/area value."""
        cleaned = s.replace(',', '').replace('.', '').replace('万', '').replace('円', '').replace('m', '').replace('²', '').strip()
        return cleaned.isdigit() if cleaned else False
