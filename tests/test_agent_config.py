from __future__ import annotations

from app.config import load_settings


def test_agent_provider_urls_and_localhost_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_HOST", raising=False)
    monkeypatch.setenv("CHATGPT_PROJECT_URL", "https://chatgpt.com/project/example")
    monkeypatch.setenv("CHATGPT_AGENT_URL", "https://chatgpt.com/g/g-agent")
    loaded = load_settings()

    assert loaded.app_host == "127.0.0.1"
    assert loaded.provider_new_chat_url("chatgpt") == "https://chatgpt.com/project/example"
    assert (
        loaded.provider_new_chat_url("chatgpt", mode="agent")
        == "https://chatgpt.com/g/g-agent"
    )


def test_empty_agent_workspace_roots_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ENABLED", "true")
    monkeypatch.setenv("AGENT_ALLOWED_WORKSPACE_ROOTS", "   ")

    try:
        load_settings()
    except ValueError as exc:
        assert "AGENT_ALLOWED_WORKSPACE_ROOTS" in str(exc)
    else:
        raise AssertionError("Expected empty AGENT_ALLOWED_WORKSPACE_ROOTS to fail.")
