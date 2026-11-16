"""大模型调用的重试、全局并发上限和熔断。

线上多个抓取任务同时跑时，中转接口限流，标题等 AI 字段大批留空。
这里验证：临时失败会退避重试，鉴权等错误不重试；空回复照常返回
（地标等短输出平时就常为空，重试只会拖慢抓取）；所有任务共用一个并发上限；
持续失败时熔断，排队中和重试中的调用也立即停下；单次请求有超时上限。
"""

import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from app.services import llm_client as llm_module
from app.services.llm_client import LLMClient, LLMGate, LLMResponseFailed, LLMUnavailable, is_transient_llm_error


class StatusError(Exception):
    def __init__(self, status_code, message='error'):
        super().__init__(message)
        self.status_code = status_code


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


def make_gate(clock, **kwargs):
    opts = dict(max_concurrency=2, retry_delays=(3, 10, 30), breaker_threshold=3,
                breaker_cooldown=300, sleep=clock.sleep, clock=clock.time, jitter=0)
    opts.update(kwargs)
    return LLMGate(**opts)


def scripted(outcomes):
    """依次返回或抛出 outcomes 里的值，记录调用次数。"""
    calls = {'n': 0}

    def fn():
        i = calls['n']
        calls['n'] += 1
        item = outcomes[i]
        if isinstance(item, Exception):
            raise item
        return item

    return fn, calls


def test_rate_limit_is_retried_with_backoff_then_succeeds():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, calls = scripted([StatusError(429), StatusError(503), 'OK'])

    assert gate.call(fn) == 'OK'
    assert calls['n'] == 3
    assert clock.sleeps == [3, 10]


def test_empty_reply_is_returned_without_retry():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, calls = scripted([''])

    assert gate.call(fn) == ''
    assert calls['n'] == 1
    assert clock.sleeps == []


def test_empty_replies_do_not_open_breaker():
    clock = FakeClock()
    gate = make_gate(clock, breaker_threshold=1)
    for _ in range(5):
        fn, _ = scripted([''])
        gate.call(fn)
    ok, ok_calls = scripted(['OK'])
    assert gate.call(ok) == 'OK'
    assert ok_calls['n'] == 1


def test_auth_or_bad_request_errors_are_not_retried():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, calls = scripted([StatusError(401, 'invalid api key')])

    with pytest.raises(StatusError):
        gate.call(fn)
    assert calls['n'] == 1
    assert clock.sleeps == []


def test_connection_and_timeout_errors_are_retried():
    clock = FakeClock()
    gate = make_gate(clock)

    class APITimeoutError(Exception):
        pass

    fn, calls = scripted([ConnectionResetError('connection reset by peer'), APITimeoutError('Request timed out.'), 'OK'])

    assert gate.call(fn) == 'OK'
    assert calls['n'] == 3


def test_httpx_transport_errors_are_transient():
    request = httpx.Request('POST', 'https://relay.example/v1/responses')
    assert is_transient_llm_error(httpx.ConnectError('All connection attempts failed', request=request))
    assert is_transient_llm_error(httpx.RemoteProtocolError('Server disconnected without sending a response.', request=request))
    assert is_transient_llm_error(httpx.ReadTimeout('timed out', request=request))


def test_relay_busy_message_is_retried():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, calls = scripted([RuntimeError('当前分组上游负载已饱和，请稍后再试'), 'OK'])

    assert gate.call(fn) == 'OK'
    assert calls['n'] == 2


def test_gives_up_after_all_retries_and_raises_last_error():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, calls = scripted([StatusError(429)] * 4)

    with pytest.raises(StatusError):
        gate.call(fn)
    assert calls['n'] == 4
    assert clock.sleeps == [3, 10, 30]


def test_breaker_opens_after_repeated_failures_and_fails_fast_until_cooldown():
    clock = FakeClock()
    gate = make_gate(clock, retry_delays=(1,), breaker_threshold=2, breaker_cooldown=300)
    always_busy, calls = scripted([StatusError(429)] * 100)

    for _ in range(2):
        with pytest.raises(StatusError):
            gate.call(always_busy)
    assert calls['n'] == 4

    # 熔断期间直接失败，不调用接口、不等待
    sleeps_before = list(clock.sleeps)
    with pytest.raises(LLMUnavailable):
        gate.call(always_busy)
    assert calls['n'] == 4
    assert clock.sleeps == sleeps_before

    # 冷却过后放行，成功一次就恢复
    clock.now += 301
    ok, ok_calls = scripted(['OK'])
    assert gate.call(ok) == 'OK'
    assert ok_calls['n'] == 1
    ok2, _ = scripted(['OK2'])
    assert gate.call(ok2) == 'OK2'


def test_success_resets_failure_count():
    clock = FakeClock()
    gate = make_gate(clock, retry_delays=(1,), breaker_threshold=2)
    fail, _ = scripted([StatusError(500)] * 2)
    with pytest.raises(StatusError):
        gate.call(fail)
    ok, _ = scripted(['OK'])
    gate.call(ok)
    fail2, _ = scripted([StatusError(500)] * 2)
    with pytest.raises(StatusError):
        gate.call(fail2)
    # 只连续失败了 1 次，不应熔断
    ok3, ok3_calls = scripted(['OK'])
    assert gate.call(ok3) == 'OK'
    assert ok3_calls['n'] == 1


def test_breaker_opening_during_backoff_stops_the_retry():
    clock = FakeClock()
    gate = make_gate(clock, breaker_threshold=1)
    # 等待重试期间，其他任务的调用连续失败把熔断打开了
    clock.on_sleep = gate._record_failure
    fn, calls = scripted([StatusError(429), 'OK'])

    with pytest.raises(LLMUnavailable):
        gate.call(fn)
    assert calls['n'] == 1


def test_inflight_success_does_not_close_open_breaker():
    clock = FakeClock()
    gate = make_gate(clock, breaker_threshold=1)
    gate._record_failure()
    gate._record_success()  # 熔断前就已发出的调用，在冷却期内成功返回

    fn, calls = scripted(['OK'])
    with pytest.raises(LLMUnavailable):
        gate.call(fn)
    assert calls['n'] == 0


def test_queued_caller_does_not_call_after_breaker_opens():
    gate = LLMGate(max_concurrency=1, retry_delays=(), breaker_threshold=1, breaker_cooldown=300, jitter=0)
    started, release = threading.Event(), threading.Event()

    def holder():
        started.set()
        release.wait(2)
        return 'A'

    holder_thread = threading.Thread(target=gate.call, args=(holder,))
    holder_thread.start()
    assert started.wait(2)

    queued_calls, outcome = [], {}

    def queued():
        queued_calls.append(1)
        return 'B'

    def run_queued():
        try:
            outcome['value'] = gate.call(queued)
        except LLMUnavailable as exc:
            outcome['error'] = exc

    queued_thread = threading.Thread(target=run_queued)
    queued_thread.start()
    time.sleep(0.1)  # 排队等槽位
    gate._record_failure()  # 这时熔断打开
    release.set()
    holder_thread.join(2)
    queued_thread.join(2)

    assert queued_calls == []
    assert 'error' in outcome


def test_open_breaker_fails_fast_without_waiting_for_a_slot():
    gate = LLMGate(max_concurrency=1, retry_delays=(), breaker_threshold=1, breaker_cooldown=300, jitter=0)
    started, release = threading.Event(), threading.Event()

    def holder():
        started.set()
        release.wait(2)
        return 'A'

    holder_thread = threading.Thread(target=gate.call, args=(holder,))
    holder_thread.start()
    assert started.wait(2)
    gate._record_failure()

    begin = time.monotonic()
    with pytest.raises(LLMUnavailable):
        gate.call(lambda: 'B')
    waited = time.monotonic() - begin
    release.set()
    holder_thread.join(2)

    assert waited < 0.5, "熔断时不应排队等名额"


def test_concurrency_is_capped_across_threads():
    gate = LLMGate(max_concurrency=2, retry_delays=(), breaker_threshold=99, breaker_cooldown=1, jitter=0)
    lock = threading.Lock()
    state = {'active': 0, 'peak': 0}

    def slow():
        with lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
        time.sleep(0.05)
        with lock:
            state['active'] -= 1
        return 'OK'

    threads = [threading.Thread(target=gate.call, args=(slow,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state['peak'] == 2


class FakeResponses:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self.replies.pop(0)


def responses_client(gate, replies):
    client = LLMClient(base_url='https://relay.example/v1', api_key='k', model='gpt-6-luna', gate=gate)
    fake = FakeResponses(replies)
    client._get_openai = lambda: SimpleNamespace(responses=fake)
    return client, fake


def test_failed_responses_status_is_retried():
    clock = FakeClock()
    gate = make_gate(clock)
    client, fake = responses_client(gate, [
        SimpleNamespace(status='failed', error=SimpleNamespace(message='upstream overloaded'), output_text=''),
        SimpleNamespace(status='completed', output_text='OK'),
    ])

    assert client.chat([{'role': 'user', 'content': 'hi'}]) == 'OK'
    assert fake.calls == 2
    assert clock.sleeps == [3]


def test_incomplete_response_is_returned_without_retry():
    clock = FakeClock()
    gate = make_gate(clock)
    client, fake = responses_client(gate, [
        SimpleNamespace(status='incomplete', incomplete_details=SimpleNamespace(reason='max_output_tokens'), output_text=''),
    ])

    assert client.chat([{'role': 'user', 'content': 'hi'}]) == ''
    assert fake.calls == 1
    assert clock.sleeps == []


def test_failed_status_error_is_transient():
    assert is_transient_llm_error(LLMResponseFailed('upstream overloaded'))


def test_client_chat_goes_through_gate():
    clock = FakeClock()
    gate = make_gate(clock)
    client = LLMClient(base_url='https://relay.example/v1', api_key='k', model='gpt-6-luna', gate=gate)
    replies = [StatusError(429), 'OK']

    def fake_dispatch(messages, temperature, max_tokens):
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client._dispatch_chat = fake_dispatch

    assert client.chat([{'role': 'user', 'content': 'hi'}]) == 'OK'
    assert clock.sleeps == [3]


def test_default_gate_is_shared_by_all_clients():
    a = LLMClient(base_url='', api_key='k', model='gpt-6-luna')
    b = LLMClient(base_url='', api_key='k', model='gpt-6-luna')

    assert a.gate is b.gate


def test_openai_client_has_timeout_and_leaves_retries_to_gate(monkeypatch):
    import openai
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(openai, 'OpenAI', FakeOpenAI)
    LLMClient(base_url='https://relay.example/v1', api_key='k', model='gpt-6-luna')._get_openai()

    assert seen['timeout'] == llm_module.REQUEST_TIMEOUT_SECONDS
    assert seen['max_retries'] == 0
    assert seen['base_url'] == 'https://relay.example/v1'


def test_gemini_client_has_timeout(monkeypatch):
    from google import genai
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(genai, 'Client', FakeClient)
    LLMClient(base_url='', api_key='k', model='gemini-2.5-flash')._get_gemini()

    assert seen['http_options'].timeout == llm_module.REQUEST_TIMEOUT_SECONDS * 1000
