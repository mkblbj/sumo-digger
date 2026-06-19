"""Normalize AI-generated candidate lists into display-ready strings."""

import ast
import json
import re
from typing import Any, List, Optional


_KEY_FALLBACKS = {
    'title': ['title', 'name', 'text', 'content', 'value'],
    'description': ['description', 'summary', 'text', 'content', 'value'],
}


def normalize_text_candidates(values: Any, limit: int,
                              preferred_key: Optional[str] = None,
                              max_chars: Optional[int] = None) -> List[str]:
    """Return clean string candidates from strings, dicts, or serialized dicts."""
    normalized: List[str] = []
    seen = set()
    for text in _extract_texts(values, preferred_key):
        text = _clean_text(text)
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def merge_candidate_values(existing: Any, new_values: Any, limit: int = 3,
                           preferred_key: Optional[str] = None,
                           max_chars: Optional[int] = None) -> List[str]:
    """Merge existing and new candidate values with the same cleanup rules."""
    return normalize_text_candidates(
        normalize_text_candidates(existing, limit=limit, preferred_key=preferred_key, max_chars=max_chars)
        + normalize_text_candidates(new_values, limit=limit, preferred_key=preferred_key, max_chars=max_chars),
        limit=limit,
        preferred_key=preferred_key,
        max_chars=max_chars,
    )


def has_n_candidates(value: Any, n: int,
                     preferred_key: Optional[str] = None,
                     max_chars: Optional[int] = None) -> bool:
    return len(normalize_text_candidates(value, limit=n, preferred_key=preferred_key, max_chars=max_chars)) >= n


def _extract_texts(value: Any, preferred_key: Optional[str]) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        parsed = _parse_serialized(value)
        if parsed is not None:
            return _extract_texts(parsed, preferred_key)
        return [value]

    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            result.extend(_extract_texts(item, preferred_key))
        return result

    if isinstance(value, dict):
        for key in _candidate_keys(preferred_key):
            item = value.get(key)
            if item not in (None, '', [], {}):
                return _extract_texts(item, None)
        return []

    return [str(value)]


def _candidate_keys(preferred_key: Optional[str]) -> List[str]:
    if not preferred_key:
        return ['text', 'content', 'value', 'title', 'description', 'name', 'summary']
    keys = _KEY_FALLBACKS.get(preferred_key, [preferred_key, 'text', 'content', 'value'])
    return list(dict.fromkeys([preferred_key] + keys))


def _parse_serialized(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in '[{':
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(stripped)
        except (ValueError, SyntaxError, TypeError):
            continue
    return None


def _clean_text(text: Any) -> str:
    return re.sub(r'\s+', ' ', str(text)).strip()
