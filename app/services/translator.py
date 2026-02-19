"""LLM-based Japanese to Chinese translation service."""

import json
import logging
import re
import time
from typing import Dict, Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# Keys whose values should NOT be translated
NO_TRANSLATE_KEYS = {
    '物件名',
    '住所', '所在地', 'アクセス1', 'アクセス2', 'アクセス3',
    '最寄り駅1', '最寄り駅2', '最寄り駅3',
    '駅徒歩',
    '賃料', '賃料・初期費用', '管理費', '管理費・共益費', '敷金', '礼金',
    '保証金', '敷引・償却',
    '販売価格', '価格',
    '専有面積', '土地面積', '建物面積',
    '築年月', '築年数', '階', '階建',
    '間取り', '間取り詳細', '向き',
    'URL', '物件画像',
    'SUUMO物件コード', '物件番号',
    '情報公開日', '情報更新日', '次回更新予定日', '次回更新日',
}

# Regex: skip values that are clearly numeric/date/short codes
_SKIP_VALUE_RE = re.compile(
    r'^[\d\s,./\-年月日万円㎡m²階建]+$'  # pure numbers/dates
    r'|^\d+[A-Z]{0,5}$'                   # like "2LDK", "3DK"
    r'|^[東西南北]+$'                       # directions
    r'|^-$'
)


class TranslatorService:
    """Translate property data from Japanese to Chinese using OpenAI-compatible API."""

    SYSTEM_PROMPT = (
        "你是日语到中文翻译器，翻译日本房产信息的描述性文本。\n"
        "规则：\n"
        "1. 只翻译描述性内容，数值/地址/站名保留原文\n"
        "2. 返回纯 JSON 对象，key 不变，value 是中文翻译\n"
        "3. 无法翻译的保留原文\n"
        "4. 不要包含 markdown 代码块标记"
    )

    def __init__(self, base_url: str, api_key: str, model: str = 'gemini-2.5-flash'):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def translate_property(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Translate a property data dict. Single LLM call."""
        to_translate = {}
        result = dict(data)

        for key, value in data.items():
            if not value or not isinstance(value, str):
                continue
            if key in NO_TRANSLATE_KEYS or key.startswith('_'):
                continue
            v = str(value).strip()
            if len(v) <= 3:  # too short to bother
                continue
            if _SKIP_VALUE_RE.match(v):
                continue
            to_translate[key] = value

        if not to_translate:
            logger.info("No translatable fields found")
            return None

        logger.info(f"Translating {len(to_translate)} fields: {list(to_translate.keys())}")
        t0 = time.time()

        try:
            translated = self._call_llm(to_translate)
            elapsed = time.time() - t0
            logger.info(f"Translation done in {elapsed:.1f}s, got {len(translated) if translated else 0} fields")
            if not translated:
                return None
            result.update(translated)
            return result
        except Exception as e:
            logger.error(f"Translation API error: {e}")
            return None

    def _call_llm(self, fields: Dict[str, str]) -> Optional[Dict[str, str]]:
        """Single LLM call to translate all fields."""
        user_msg = json.dumps(fields, ensure_ascii=False, indent=2)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=8192,
        )

        content = response.choices[0].message.content.strip()
        logger.debug(f"LLM raw response ({len(content)} chars)")

        # Strip markdown code blocks
        if content.startswith('```'):
            lines = content.split('\n')
            end = len(lines)
            for j in range(len(lines) - 1, 0, -1):
                if lines[j].strip().startswith('```'):
                    end = j
                    break
            content = '\n'.join(lines[1:end])

        # Try direct parse
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try to fix truncated JSON
        fixed = self._try_fix_json(content)
        if fixed is not None:
            logger.info("Recovered truncated JSON")
            return fixed

        logger.warning(f"Could not parse LLM JSON: {content[:200]}")
        return None

    @staticmethod
    def _try_fix_json(content: str) -> Optional[Dict[str, str]]:
        """Try to recover a truncated JSON object."""
        content = content.strip()
        if not content.startswith('{'):
            return None

        for suffix in ['"}', '""}', '}']:
            try:
                return json.loads(content + suffix)
            except json.JSONDecodeError:
                continue

        # Find last complete "key": "value", and close there
        pos = len(content)
        while True:
            pos = content.rfind('",', 0, pos)
            if pos <= 0:
                break
            try:
                return json.loads(content[:pos + 1] + '\n}')
            except json.JSONDecodeError:
                continue

        return None
