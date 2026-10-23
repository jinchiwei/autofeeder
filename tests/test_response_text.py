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
