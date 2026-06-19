"""Tests for the unified LLM client routing."""

from types import SimpleNamespace

from app.services.llm_client import (
    LLMClient,
    PROVIDER_COMPATIBLE,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
    detect_provider,
)


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text='OK')


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='CHAT_OK'))]
        )


class FakeOpenAIClient:
    def __init__(self):
        self.responses = FakeResponses()
        self.chat = SimpleNamespace(completions=FakeChatCompletions())


def test_detect_provider_routes_gpt_model_with_custom_base_to_responses():
    provider = detect_provider('https://s2mg.toiroworld.com/v1', 'gpt-5.4-mini')

    assert provider == PROVIDER_OPENAI


def test_detect_provider_keeps_non_gpt_custom_base_chat_compatible():
    provider = detect_provider('https://example.test/v1', 'custom-model')

    assert provider == PROVIDER_COMPATIBLE


def test_detect_provider_routes_gemini_model_to_gemini():
    provider = detect_provider('https://example.test/v1', 'gemini-2.5-flash')

    assert provider == PROVIDER_GEMINI


def test_gpt_text_chat_uses_responses_api(monkeypatch):
    fake = FakeOpenAIClient()
    client = LLMClient(
        base_url='https://s2mg.toiroworld.com/v1',
        api_key='test-key',
        model='gpt-5.4-mini',
    )
    monkeypatch.setattr(client, '_get_openai', lambda: fake)

    reply = client.chat(
        messages=[
            {'role': 'system', 'content': 'system prompt'},
            {'role': 'user', 'content': 'hello'},
        ],
        temperature=0.2,
        max_tokens=123,
    )

    assert reply == 'OK'
    assert len(fake.responses.calls) == 1
    assert fake.chat.completions.calls == []

    call = fake.responses.calls[0]
    assert call['model'] == 'gpt-5.4-mini'
    assert call['instructions'] == 'system prompt'
    assert call['max_output_tokens'] == 123
    assert call['temperature'] == 0.2
    assert call['input'][0]['role'] == 'user'
    assert call['input'][0]['content'][0] == {'type': 'input_text', 'text': 'hello'}


def test_gpt_vision_uses_responses_image_input(monkeypatch):
    fake = FakeOpenAIClient()
    client = LLMClient(
        base_url='https://s2mg.toiroworld.com/v1',
        api_key='test-key',
        model='gpt-5.4-mini',
    )
    monkeypatch.setattr(client, '_get_openai', lambda: fake)

    reply = client.chat_with_vision(
        prompt='read this',
        images=[b'abc'],
        mime_type='image/png',
        system_prompt='vision system',
        max_tokens=456,
    )

    assert reply == 'OK'
    call = fake.responses.calls[0]
    content = call['input'][0]['content']
    assert call['instructions'] == 'vision system'
    assert call['max_output_tokens'] == 456
    assert content[0] == {'type': 'input_text', 'text': 'read this'}
    assert content[1]['type'] == 'input_image'
    assert content[1]['image_url'].startswith('data:image/png;base64,')


def test_compatible_provider_still_uses_chat_completions(monkeypatch):
    fake = FakeOpenAIClient()
    client = LLMClient(
        base_url='https://example.test/v1',
        api_key='test-key',
        model='custom-model',
    )
    monkeypatch.setattr(client, '_get_openai', lambda: fake)

    reply = client.chat(messages=[{'role': 'user', 'content': 'hello'}], max_tokens=12)

    assert reply == 'CHAT_OK'
    assert fake.responses.calls == []
    assert len(fake.chat.completions.calls) == 1
    assert fake.chat.completions.calls[0]['max_tokens'] == 12
