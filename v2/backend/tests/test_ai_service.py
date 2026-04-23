from __future__ import annotations

from app.services.ai_service import _create_anthropic_message


class _Msg:
    def __init__(self, text: str):
        self.content = [type("Part", (), {"text": text})()]


def test_create_anthropic_message_retries_on_compatibility_400():
    class _Messages:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                err = RuntimeError("invalid_request_error: cache_control is not allowed")
                err.status_code = 400
                raise err
            assert isinstance(kwargs.get("system"), str)
            return _Msg('{"ok": true}')

    class _Client:
        def __init__(self):
            self.messages = _Messages()

    client = _Client()
    out = _create_anthropic_message(
        client,
        model="claude-sonnet-4-6",
        max_tokens=200,
        system_prompt="sys",
        user_prompt="user",
    )
    assert out.content[0].text == '{"ok": true}'
    assert client.messages.calls == 2


def test_create_anthropic_message_does_not_retry_generic_400():
    class _Messages:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            err = RuntimeError("invalid_request_error: malformed request body")
            err.status_code = 400
            raise err

    class _Client:
        def __init__(self):
            self.messages = _Messages()

    client = _Client()
    try:
        _create_anthropic_message(
            client,
            model="claude-sonnet-4-6",
            max_tokens=200,
            system_prompt="sys",
            user_prompt="user",
        )
        assert False, "Expected RuntimeError for generic 400"
    except RuntimeError:
        pass
    assert client.messages.calls == 1
