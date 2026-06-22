from __future__ import annotations

from vllm_source_gateway.services.proxy import _consume_sse_events


def test_consume_sse_events_extracts_usage_within_buffer_limit() -> None:
    buffer, usage, overflowed = _consume_sse_events(
        buffer="",
        fragment='data: {"usage":{"prompt_tokens":7,"completion_tokens":9}}\n\n',
        max_buffer_bytes=1024,
        usage_extractor=lambda payload: (
            payload["usage"]["prompt_tokens"],
            payload["usage"]["completion_tokens"],
        )
        if "usage" in payload
        else None,
        latest_usage=None,
    )

    assert buffer == ""
    assert usage == (7, 9)
    assert overflowed is False


def test_consume_sse_events_reports_overflow_before_buffer_grows_unbounded() -> None:
    buffer, usage, overflowed = _consume_sse_events(
        buffer="data: " + ("a" * 40),
        fragment="b" * 40,
        max_buffer_bytes=32,
        usage_extractor=lambda payload: None,
        latest_usage=(3, 4),
    )

    assert buffer == ""
    assert usage == (3, 4)
    assert overflowed is True
