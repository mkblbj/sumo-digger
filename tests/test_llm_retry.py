"""大模型调用的重试、全局并发上限和熔断。

线上多个抓取任务同时跑时，中转接口限流或返回空内容，标题等 AI 字段大批留空。
这里验证：临时失败会退避重试，空回复也重试，鉴权等错误不重试，
所有任务共用一个并发上限，持续失败时熔断、不再拖慢抓取。
"""

import threading
import time

import pytest

from app.services.llm_client import EmptyLLMResponse, LLMClient, LLMGate, LLMUnavailable


class StatusError(Exception):
    def __init__(self, status_code, message='error'):
        super().__init__(message)
        self.status_code = status_code


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


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


def test_empty_reply_is_retried():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, calls = scripted(['', None, '  ', '["标题"]'])

    assert gate.call(fn) == '["标题"]'
    assert calls['n'] == 4


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


def test_empty_reply_after_all_retries_raises_empty_response():
    clock = FakeClock()
    gate = make_gate(clock)
    fn, _ = scripted([''] * 4)

    with pytest.raises(EmptyLLMResponse):
        gate.call(fn)


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
