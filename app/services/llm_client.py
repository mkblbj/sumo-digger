"""Unified LLM client supporting Gemini, OpenAI Responses, and chat-compatible APIs."""

import base64
import json
import logging
import re
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Provider constants
PROVIDER_GEMINI = 'gemini'
PROVIDER_OPENAI = 'openai'
PROVIDER_COMPATIBLE = 'compatible'
PROVIDER_AUTO = 'auto'

OPENAI_RESPONSES_MODEL_PREFIXES = ('gpt-', 'o1', 'o3', 'o4', 'o5')


def _is_openai_responses_model(model: str) -> bool:
    """Return True for models that should use OpenAI's Responses API."""
    return (model or '').lower().startswith(OPENAI_RESPONSES_MODEL_PREFIXES)


def detect_provider(base_url: str, model: str) -> str:
    """Auto-detect LLM provider from base_url and model name.

    Priority:
    1. Gemini domain or gemini-* model -> Gemini native SDK
    2. api.openai.com or GPT/o-series model -> OpenAI Responses API
       (custom base_url is still allowed for OpenAI-compatible Responses proxies)
    3. Custom URL + non-GPT model -> chat-completions compatible API
    4. Fallback -> chat-completions compatible API
    """
    base = (base_url or '').lower().rstrip('/')
    mdl = (model or '').lower()

    if 'generativelanguage.googleapis.com' in base:
        return PROVIDER_GEMINI
    if mdl.startswith('gemini'):
        return PROVIDER_GEMINI

    if 'api.openai.com' in base:
        return PROVIDER_OPENAI

    if _is_openai_responses_model(mdl):
        return PROVIDER_OPENAI

    if base:
        return PROVIDER_COMPATIBLE

    return PROVIDER_COMPATIBLE


class LLMClient:
    """Unified LLM client with automatic provider detection.

    Supports three backends:
      - gemini:     google-genai SDK (native Gemini API, best Vision support)
      - openai:     openai SDK using the new Responses API (/responses)
      - compatible: openai SDK using legacy Chat Completions (/chat/completions)
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 provider: str = PROVIDER_AUTO):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.provider = provider if provider != PROVIDER_AUTO else detect_provider(base_url, model)

        self._gemini_client = None
        self._openai_client = None

    def _get_gemini(self):
        if self._gemini_client is None:
            from google.genai import Client
            kwargs = {'api_key': self.api_key}
            if self.base_url and 'generativelanguage.googleapis.com' not in self.base_url:
                from google.genai.types import HttpOptions
                kwargs['http_options'] = HttpOptions(base_url=self.base_url)
            self._gemini_client = Client(**kwargs)
        return self._gemini_client

    def _get_openai(self):
        if self._openai_client is None:
            from openai import OpenAI
            kwargs = {'api_key': self.api_key}
            if self.base_url:
                kwargs['base_url'] = self.base_url
            self._openai_client = OpenAI(**kwargs)
        return self._openai_client

    # ------------------------------------------------------------------
    # Core API: text chat
    # ------------------------------------------------------------------

    def chat(self, messages: list, temperature: float = 0.3,
             max_tokens: int = 8192) -> str:
        """Send a text chat request and return the response text.

        Args:
            messages: OpenAI-style message list [{"role": ..., "content": ...}]
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response

        Returns:
            Response text string
        """
        if self.provider == PROVIDER_GEMINI:
            return self._gemini_chat(messages, temperature, max_tokens)
        if self.provider == PROVIDER_OPENAI:
            return self._openai_responses_chat(messages, temperature, max_tokens)
        return self._openai_chat(messages, temperature, max_tokens)

    # ------------------------------------------------------------------
    # Core API: vision chat (text + images)
    # ------------------------------------------------------------------

    def chat_with_vision(self, prompt: str, images: List[bytes],
                         mime_type: str = 'image/png',
                         system_prompt: Optional[str] = None,
                         temperature: float = 0.3,
                         max_tokens: int = 8192) -> str:
        """Send a vision request with images and return the response text.

        Args:
            prompt: Text prompt to accompany the images
            images: List of image bytes
            mime_type: MIME type of the images
            system_prompt: Optional system instruction
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response

        Returns:
            Response text string
        """
        if self.provider == PROVIDER_GEMINI:
            return self._gemini_vision(prompt, images, mime_type,
                                       system_prompt, temperature, max_tokens)
        if self.provider == PROVIDER_OPENAI:
            return self._openai_responses_vision(prompt, images, mime_type,
                                                 system_prompt, temperature, max_tokens)
        return self._openai_vision(prompt, images, mime_type,
                                   system_prompt, temperature, max_tokens)

    # ------------------------------------------------------------------
    # Core API: test connection
    # ------------------------------------------------------------------

    def test_connection(self) -> Tuple[bool, str]:
        """Test the LLM connection. Returns (success, reply_or_error)."""
        try:
            reply = self.chat(
                messages=[{"role": "user", "content": "テスト。「OK」とだけ返してください。"}],
                max_tokens=10,
            )
            return True, reply
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Gemini native implementation
    # ------------------------------------------------------------------

    def _gemini_chat(self, messages: list, temperature: float,
                     max_tokens: int) -> str:
        client = self._get_gemini()

        system_instruction = None
        contents = []
        for msg in messages:
            role = msg['role']
            content = msg['content']
            if role == 'system':
                system_instruction = content
                continue
            gemini_role = 'model' if role == 'assistant' else 'user'
            contents.append({"role": gemini_role, "parts": [{"text": content}]})

        config = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system_instruction:
            config["system_instruction"] = system_instruction

        response = client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        return response.text

    def _gemini_vision(self, prompt: str, images: List[bytes],
                       mime_type: str, system_prompt: Optional[str],
                       temperature: float, max_tokens: int) -> str:
        client = self._get_gemini()

        parts = []
        for img_bytes in images:
            parts.append({
                "inline_data": {
                    "data": base64.b64encode(img_bytes).decode('utf-8'),
                    "mime_type": mime_type,
                }
            })
        parts.append({"text": prompt})

        config = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if system_prompt:
            config["system_instruction"] = system_prompt

        response = client.models.generate_content(
            model=self.model,
            contents=[{"role": "user", "parts": parts}],
            config=config,
        )
        return response.text

    # ------------------------------------------------------------------
    # OpenAI Responses API implementation
    # ------------------------------------------------------------------

    def _openai_responses_chat(self, messages: list, temperature: float,
                               max_tokens: int) -> str:
        client = self._get_openai()
        if not hasattr(client, 'responses'):
            raise RuntimeError(
                "Installed openai package does not support Responses API. "
                "Run: pip install -U openai"
            )

        instructions, response_input = self._messages_to_responses_input(messages)
        response = client.responses.create(
            model=self.model,
            instructions=instructions,
            input=response_input,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        return self._response_output_text(response)

    def _openai_responses_vision(self, prompt: str, images: List[bytes],
                                 mime_type: str, system_prompt: Optional[str],
                                 temperature: float, max_tokens: int) -> str:
        client = self._get_openai()
        if not hasattr(client, 'responses'):
            raise RuntimeError(
                "Installed openai package does not support Responses API. "
                "Run: pip install -U openai"
            )

        content_parts = [{"type": "input_text", "text": prompt}]
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode('utf-8')
            content_parts.append({
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{b64}",
            })

        response = client.responses.create(
            model=self.model,
            instructions=system_prompt,
            input=[{"role": "user", "content": content_parts}],
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        return self._response_output_text(response)

    @classmethod
    def _messages_to_responses_input(cls, messages: list) -> Tuple[Optional[str], list]:
        instructions = []
        response_input = []

        for msg in messages:
            role = msg.get('role', 'user')
            content = cls._content_to_text(msg.get('content', ''))
            if role == 'system':
                instructions.append(content)
                continue

            response_input.append({
                "role": "assistant" if role == 'assistant' else "user",
                "content": [{"type": "input_text", "text": content}],
            })

        return ('\n\n'.join(instructions) if instructions else None), response_input

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False)

    @staticmethod
    def _response_output_text(response: Any) -> str:
        output_text = getattr(response, 'output_text', None)
        if output_text is not None:
            return output_text

        if isinstance(response, dict):
            output_text = response.get('output_text')
            if output_text is not None:
                return output_text
            output_items = response.get('output', [])
        else:
            output_items = getattr(response, 'output', []) or []

        chunks = []
        for item in output_items:
            content_items = item.get('content', []) if isinstance(item, dict) else getattr(item, 'content', [])
            for content in content_items or []:
                if isinstance(content, dict):
                    text = content.get('text')
                else:
                    text = getattr(content, 'text', None)
                if text:
                    chunks.append(text)
        return ''.join(chunks)

    # ------------------------------------------------------------------
    # Legacy OpenAI-compatible Chat Completions implementation
    # ------------------------------------------------------------------

    def _openai_chat(self, messages: list, temperature: float,
                     max_tokens: int) -> str:
        client = self._get_openai()
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def _openai_vision(self, prompt: str, images: List[bytes],
                       mime_type: str, system_prompt: Optional[str],
                       temperature: float, max_tokens: int) -> str:
        client = self._get_openai()

        content_parts = []
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode('utf-8')
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            })
        content_parts.append({"type": "text", "text": prompt})

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content


# ------------------------------------------------------------------
# Convenience: parse JSON from LLM response
# ------------------------------------------------------------------

def extract_json(text: str) -> Optional[dict]:
    """Extract a JSON object from LLM response text, handling markdown fences."""
    text = text.strip()

    if text.startswith('```'):
        lines = text.split('\n')
        end = len(lines)
        for j in range(len(lines) - 1, 0, -1):
            if lines[j].strip().startswith('```'):
                end = j
                break
        text = '\n'.join(lines[1:end])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Last resort: try to repair a truncated JSON object.
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        return repaired

    return None


def _repair_truncated_json(content: str) -> Optional[dict]:
    """Recover a truncated JSON object by progressively closing brackets.

    Handles responses cut off mid-array/value, e.g.
    '{"a": ["x"], "b": ["y"'  ->  {"a": ["x"], "b": ["y"]}
    """
    content = content.strip()
    start = content.find('{')
    if start == -1:
        return None
    content = content[start:]

    # Direct close attempts first.
    for suffix in ('', '"', '"]', ']', '"]}', ']}', '}', '"}', '""}'):
        for closing in ('', '}', ']}', '"]}'):
            candidate = content + suffix + closing
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

    # Trim back to the last complete quoted string, then close the structure.
    pos = len(content)
    while True:
        pos = content.rfind('"', 0, pos)
        if pos <= 0:
            break
        head = content[:pos + 1]
        for closing in (']}', '}', '"]}'):
            try:
                parsed = json.loads(head + closing)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

    return None


# ------------------------------------------------------------------
# Factory: create client from app Settings
# ------------------------------------------------------------------

def get_llm_client(settings=None) -> LLMClient:
    """Create an LLMClient from application Settings.

    Args:
        settings: Settings object. If None, loads from database.

    Returns:
        Configured LLMClient instance
    """
    if settings is None:
        from app.models import Settings
        settings = Settings.get()

    provider = getattr(settings, 'llm_provider', None) or PROVIDER_AUTO

    return LLMClient(
        base_url=settings.llm_base_url or '',
        api_key=settings.llm_api_key or '',
        model=settings.llm_model or 'gemini-2.5-flash',
        provider=provider,
    )
