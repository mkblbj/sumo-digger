"""Unified LLM client supporting Gemini, OpenAI Responses, and chat-compatible APIs."""

import base64
import json
import logging
import os
import random
import re
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


class EmptyLLMResponse(RuntimeError):
    """模型返回了空内容。中转接口繁忙时常见，按临时失败重试。"""


class LLMUnavailable(RuntimeError):
    """连续失败后熔断：冷却期内直接失败，不再排队等待重试，免得拖慢抓取。"""


# 临时失败：限流、超时、网关错误、中转接口繁忙。鉴权、参数错误、余额不足等不在其中，直接抛出
_TRANSIENT_STATUS = (408, 409, 425, 429)
_TRANSIENT_CLASS_HINTS = ('RateLimit', 'Timeout', 'Connection', 'ServiceUnavailable', 'InternalServer', 'Overloaded')
_TRANSIENT_MESSAGE_HINTS = (
    'rate limit', 'too many requests', 'timeout', 'timed out', 'temporarily', 'overloaded',
    'unavailable', 'try again', 'busy', 'resource_exhausted', 'bad gateway', 'gateway time',
    'connection reset', 'connection aborted', 'connection error', 'remote end closed',
    '负载', '繁忙', '稍后', '限流', '超时',
)


def _status_of(exc: BaseException) -> Optional[int]:
    for attr in ('status_code', 'code', 'status'):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    value = getattr(getattr(exc, 'response', None), 'status_code', None)
    return value if isinstance(value, int) else None


def is_transient_llm_error(exc: BaseException) -> bool:
    """判断大模型调用失败是否值得重试。有 HTTP 状态码时以状态码为准。"""
    if isinstance(exc, (EmptyLLMResponse, ConnectionError, TimeoutError)):
        return True
    status = _status_of(exc)
    if status is not None:
        return status in _TRANSIENT_STATUS or status >= 500
    if any(hint in type(exc).__name__ for hint in _TRANSIENT_CLASS_HINTS):
        return True
    message = str(exc).lower()
    return any(hint in message for hint in _TRANSIENT_MESSAGE_HINTS)


class LLMGate:
    """所有 LLMClient 共用的调用闸门。

    - 限制同时进行的调用数：多个抓取任务同时跑时，中转接口会限流或回空内容。
    - 临时失败和空回复按 retry_delays 退避重试，其他错误直接抛出。
    - 连续 breaker_threshold 次重试用尽就熔断 breaker_cooldown 秒，期间直接失败，
      避免接口长时间不可用时每次调用都等满重试，把抓取拖慢几十倍。
    """

    def __init__(self, max_concurrency: int = 2, retry_delays=(3, 10, 30),
                 breaker_threshold: int = 5, breaker_cooldown: float = 300,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, jitter: float = 0.2):
        self._slots = threading.BoundedSemaphore(max(1, int(max_concurrency)))
        self.retry_delays = tuple(retry_delays)
        self.breaker_threshold = max(1, int(breaker_threshold))
        self.breaker_cooldown = breaker_cooldown
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    def call(self, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if self._clock() < self._open_until:
                raise LLMUnavailable('大模型接口连续失败，冷却期内暂停调用')
        attempt = 0
        while True:
            try:
                with self._slots:
                    text = fn()
                if text is None or not str(text).strip():
                    raise EmptyLLMResponse('大模型返回空内容')
            except Exception as exc:
                if not is_transient_llm_error(exc):
                    raise
                if attempt >= len(self.retry_delays):
                    self._record_failure()
                    raise
                delay = self.retry_delays[attempt]
                if self._jitter:
                    delay *= 1 + random.uniform(-self._jitter, self._jitter)
                attempt += 1
                logger.warning(f"LLM call failed ({type(exc).__name__}), retry {attempt}/{len(self.retry_delays)} in {delay:.1f}s: {exc}")
                self._sleep(delay)
                continue
            self._record_success()
            return text

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.breaker_threshold:
                self._open_until = self._clock() + self.breaker_cooldown
                logger.error(f"LLM calls failed {self._failures} times in a row; pausing for {self.breaker_cooldown}s")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# 进程内所有任务共用。LLM_MAX_CONCURRENCY 可按中转接口的并发额度调整
_DEFAULT_GATE = LLMGate(max_concurrency=_env_int('LLM_MAX_CONCURRENCY', 2))

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
                 provider: str = PROVIDER_AUTO, gate: Optional[LLMGate] = None):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.provider = provider if provider != PROVIDER_AUTO else detect_provider(base_url, model)
        self.gate = gate or _DEFAULT_GATE

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
        return self.gate.call(lambda: self._dispatch_chat(messages, temperature, max_tokens))

    def _dispatch_chat(self, messages: list, temperature: float, max_tokens: int) -> str:
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
        return self.gate.call(lambda: self._dispatch_vision(
            prompt, images, mime_type, system_prompt, temperature, max_tokens))

    def _dispatch_vision(self, prompt: str, images: List[bytes], mime_type: str,
                         system_prompt: Optional[str], temperature: float,
                         max_tokens: int) -> str:
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
