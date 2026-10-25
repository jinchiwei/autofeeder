"""Opus 5.5 always thinks, so responses can open with a thinking block."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backends.anthropic_backend import response_text


def block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


def test_skips_a_leading_thinking_block():
    resp = SimpleNamespace(content=[block("thinking", thinking=""), block("text", text='{"ok": 1}')],
                           stop_reason="end_turn")
    assert response_text(resp) == '{"ok": 1}'


def test_joins_multiple_text_blocks_in_order():
    resp = SimpleNamespace(content=[block("text", text="a"), block("thinking", thinking=""),
                                    block("text", text="b")], stop_reason="end_turn")
    assert response_text(resp) == "ab"


def test_a_thinking_only_response_is_a_clear_error_not_an_attributeerror():
    resp = SimpleNamespace(content=[block("thinking", thinking="")], stop_reason="max_tokens")
    with pytest.raises(RuntimeError, match="max_tokens"):
        response_text(resp)


def test_empty_content_is_a_clear_error():
    with pytest.raises(RuntimeError):
        response_text(SimpleNamespace(content=[], stop_reason="refusal"))


class FakeClient:
    """Stands in for client.messages; returns queued responses and records models."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.models = []
        self.messages = self

    def create(self, **kwargs):
        self.models.append(kwargs["model"])
        return self._queue.pop(0)


def refused(category="bio"):
    return SimpleNamespace(stop_reason="refusal", content=[],
                           stop_details=SimpleNamespace(category=category))


def ok(text="{}"):
    return SimpleNamespace(stop_reason="end_turn", content=[block("text", text=text)])


def test_a_declined_request_is_retried_once_on_the_fallback_model():
    from backends.anthropic_backend import create_message
    client = FakeClient(refused(), ok('{"headline": "x"}'))
    r = create_message(client, "opus-4-8", model="opus-5-5", max_tokens=10, messages=[])
    assert client.models == ["opus-5-5", "opus-4-8"]
    assert response_text(r) == '{"headline": "x"}'


def test_no_fallback_configured_returns_the_refusal_unchanged():
    from backends.anthropic_backend import create_message
    client = FakeClient(refused())
    r = create_message(client, None, model="opus-5-5", messages=[])
    assert client.models == ["opus-5-5"] and r.stop_reason == "refusal"


def test_a_normal_response_makes_exactly_one_call():
    from backends.anthropic_backend import create_message
    client = FakeClient(ok())
    create_message(client, "opus-4-8", model="opus-5-5", messages=[])
    assert client.models == ["opus-5-5"]


def test_never_falls_back_to_the_same_model():
    from backends.anthropic_backend import create_message
    client = FakeClient(refused())
    create_message(client, "opus-5-5", model="opus-5-5", messages=[])
    assert client.models == ["opus-5-5"]
