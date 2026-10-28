"""Only complete provider responses may produce executable tool calls."""

import json
from types import SimpleNamespace

import pytest

from agent_loop import exhaust
from llmcore import MixinSession, ToolClient, _parse_openai_sse


@pytest.mark.parametrize("reason,accepted", [
    ("tool_calls", True), ("length", False), (None, False),
])
def test_chat_tool_calls_require_successful_finish_reason(reason, accepted):
    events = [{"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": "call-1",
        "function": {"name": "file_read", "arguments": '{"path":"a.md"}'},
    }]}, "finish_reason": None}]}]
    if reason:
        events.append({"choices": [{"delta": {}, "finish_reason": reason}]})
    lines = [("data: " + json.dumps(event)).encode() for event in events]
    blocks = exhaust(_parse_openai_sse(lines))
    calls = [block for block in blocks if block["type"] == "tool_use"]
    assert bool(calls) is accepted
    if accepted:
        assert calls[0]["input"] == {"path": "a.md"}
    else:
        assert any("!!!Error:" in block.get("text", "") for block in blocks)


@pytest.mark.parametrize("end,accepted", [
    ("response.completed", True), ("response.failed", False),
    ("response.incomplete", False), (None, False),
])
def test_responses_requires_completed_event(end, accepted):
    events = [{"type": "response.output_text.delta", "delta": "response text"}]
    if end:
        events.append({"type": end, "response": {}})
    blocks = exhaust(_parse_openai_sse(
        [("data: " + json.dumps(event)).encode() for event in events], "responses"))
    assert blocks[0]["type"] == "text"
    assert ("!!!Error:" not in blocks[0]["text"]) is accepted


def test_text_protocol_error_cannot_dispatch_partial_tools():
    def ask(prompt):
        yield '<tool_use>{"name":"file_read","arguments":{"path":"a.md"}}</tool_use>'
        yield "!!!Error: incomplete SSE response"
    client = ToolClient(SimpleNamespace(name="fixture", ask=ask))
    response = exhaust(client.chat([{"role": "user", "content": "read"}], tools=[]))
    assert response.tool_calls == []
    assert "!!!Error:" in response.content


def test_literal_error_marker_inside_valid_text_is_not_transport_failure():
    def ask(prompt):
        yield 'The log contains "!!!Error:" but this response completed normally.'

    client = ToolClient(SimpleNamespace(name="fixture", ask=ask))
    response = exhaust(client.chat([{"role": "user", "content": "inspect"}], tools=[]))
    assert response.content.startswith('The log contains')


def test_mixin_does_not_duplicate_a_partially_visible_attempt():
    def failed(*args, **kwargs):
        yield "partial output"
        yield "\n\n!!!Error: incomplete SSE response"
        return [{"type": "text", "text": "failed"}]

    def successful(*args, **kwargs):
        yield "complete output"
        return [{"type": "text", "text": "complete output"}]

    mixin = MixinSession.__new__(MixinSession)
    mixin._retries = 1
    mixin._base_delay = 0
    mixin._spring_sec = 300
    mixin._sessions = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
    mixin._orig_raw_asks = [failed, successful]
    mixin._cur_idx = 0
    mixin._switched_at = 0

    generator = mixin._raw_ask()
    chunks = []
    while True:
        try:
            chunks.append(next(generator))
        except StopIteration as exc:
            blocks = exc.value
            break

    assert chunks == ["partial output", "\n\n!!!Error: incomplete SSE response"]
    assert blocks == [{"type": "text", "text": "failed"}]
    assert mixin._cur_idx == 0


def test_mixin_retries_when_failure_was_not_partially_visible():
    def failed(*args, **kwargs):
        yield "!!!Error: HTTP 503"
        return [{"type": "text", "text": "failed"}]

    def successful(*args, **kwargs):
        yield "complete output"
        return [{"type": "text", "text": "complete output"}]

    mixin = MixinSession.__new__(MixinSession)
    mixin._retries = 1
    mixin._base_delay = 0
    mixin._spring_sec = 300
    mixin._sessions = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
    mixin._orig_raw_asks = [failed, successful]
    mixin._cur_idx = 0
    mixin._switched_at = 0

    generator = mixin._raw_ask()
    chunks = []
    while True:
        try:
            chunks.append(next(generator))
        except StopIteration as exc:
            blocks = exc.value
            break

    assert chunks == ["complete output"]
    assert blocks == [{"type": "text", "text": "complete output"}]


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_malformed_streamed_tool_arguments_emit_error(mode):
    if mode == "responses":
        events = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "function_call", "name": "file_read", "call_id": "c1"}},
            {"type": "response.function_call_arguments.done", "output_index": 0,
             "arguments": '{"path":'},
            {"type": "response.completed", "response": {}},
        ]
    else:
        events = [
            {"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "c1",
                "function": {"name": "file_read", "arguments": '{"path":'},
            }]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
    generator = _parse_openai_sse(
        [("data: " + json.dumps(event)).encode() for event in events], mode)
    chunks = []
    while True:
        try:
            chunks.append(next(generator))
        except StopIteration as exc:
            blocks = exc.value
            break
    assert any("!!!Error:" in chunk for chunk in chunks)
    assert all(block["type"] != "tool_use" for block in blocks)
