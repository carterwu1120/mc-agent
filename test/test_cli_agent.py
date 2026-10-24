import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from agent.brain.cli_agent import (
    CLIAgentClient,
    CLIProviderError,
    _claude_input_tokens,
    _codex_input_tokens,
)
from agent.brain import build_client


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_final_decision_and_stdin(monkeypatch, provider):
    decision = '{"action":"plan","commands":["mine iron 3"]}'
    seen = {}

    async def spawn(*args, **kwargs):
        if "auth" in args:
            process = AsyncMock()
            process.returncode = 0
            process.communicate.return_value = (b'{"loggedIn":true,"authMethod":"claude.ai"}', b"")
            return process
        seen.update(args=args, kwargs=kwargs)
        if provider == "codex":
            Path(args[args.index("--output-last-message") + 1]).write_text(decision, encoding="utf-8")
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (json.dumps({"result": decision}).encode(), b"progress")
        seen["process"] = process
        return process

    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    result = asyncio.run(CLIAgentClient(provider).chat([{"role": "user", "content": "挖鐵 $(echo secret)"}], "JSON only"))
    assert result == decision
    assert "挖鐵" in seen["process"].communicate.call_args.args[0].decode()
    assert all("挖鐵" not in arg for arg in seen["args"])
    assert not Path(seen["kwargs"]["cwd"]).exists()
    if provider == "codex":
        configs = [
            seen["args"][index + 1]
            for index, arg in enumerate(seen["args"])
            if arg == "-c"
        ]
        assert 'model_reasoning_effort="low"' in configs


def test_codex_accepts_per_request_reasoning_effort(monkeypatch):
    seen = {}

    async def spawn(*args, **kwargs):
        seen["args"] = args
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"action":"wait"}', encoding="utf-8"
        )
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = (b"", b"")
        return process

    monkeypatch.setenv("CLI_SESSION_REUSE", "false")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    result = asyncio.run(
        CLIAgentClient("codex").chat([], reasoning_effort="medium")
    )

    assert result == '{"action":"wait"}'
    configs = [
        seen["args"][index + 1]
        for index, arg in enumerate(seen["args"])
        if arg == "-c"
    ]
    assert 'model_reasoning_effort="medium"' in configs


def test_invalid_default_reasoning_effort_is_rejected(monkeypatch):
    monkeypatch.setenv("CLI_REASONING_EFFORT_DEFAULT", "light")
    with pytest.raises(ValueError, match="reasoning effort"):
        CLIAgentClient("codex")


def test_codex_reuses_persisted_session(monkeypatch, tmp_path):
    session_id = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    decision = '{"action":"plan","commands":["mine diamond 3"]}'
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        Path(args[args.index("--output-last-message") + 1]).write_text(
            decision, encoding="utf-8"
        )
        process = AsyncMock()
        process.returncode = 0
        stdout = b""
        if "resume" not in args:
            stdout = (json.dumps({
                "type": "thread.started", "thread_id": session_id,
            }) + "\n").encode()
        process.communicate.return_value = (stdout, b"")
        return process

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLI_SESSION_REUSE", "true")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = CLIAgentClient("codex")

    assert asyncio.run(client.chat([])) == decision
    assert asyncio.run(client.chat([])) == decision
    assert "--ephemeral" not in calls[0]
    assert "--json" in calls[0]
    assert calls[1][1:3] == ("exec", "resume")
    assert session_id in calls[1]
    saved = json.loads((tmp_path / "cli_session.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["provider"] == "codex"
    assert saved["model"] is None
    assert saved["session_id"] == session_id
    assert saved["turns"] == 2

    # A new process-level client resumes the same per-bot session from disk.
    restarted = CLIAgentClient("codex")
    assert restarted._session_id == session_id


def test_codex_replaces_missing_persisted_session(monkeypatch, tmp_path):
    old_id = "11111111-1111-4111-8111-111111111111"
    new_id = "22222222-2222-4222-8222-222222222222"
    (tmp_path / "cli_session.json").write_text(json.dumps({
        "schema_version": 2, "provider": "codex", "model": None,
        "session_id": old_id, "turns": 1, "input_tokens": 10,
    }), encoding="utf-8")
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        process = AsyncMock()
        if "resume" in args:
            process.returncode = 1
            process.communicate.return_value = (
                b"", f"no rollout found for thread id {old_id}".encode()
            )
        else:
            process.returncode = 0
            Path(args[args.index("--output-last-message") + 1]).write_text(
                '{"action":"wait"}', encoding="utf-8"
            )
            process.communicate.return_value = ((json.dumps({
                "type": "thread.started", "thread_id": new_id,
            }) + "\n").encode(), b"")
        return process

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = CLIAgentClient("codex")

    assert asyncio.run(client.chat([])) == '{"action":"wait"}'
    assert "resume" in calls[0]
    assert "resume" not in calls[1]
    saved = json.loads((tmp_path / "cli_session.json").read_text(encoding="utf-8"))
    assert saved["session_id"] == new_id


def test_claude_reuses_persisted_session(monkeypatch, tmp_path):
    decisions = []

    async def spawn(*args, **kwargs):
        process = AsyncMock()
        process.returncode = 0
        if "auth" in args:
            process.communicate.return_value = (
                b'{"loggedIn":true,"authMethod":"claude.ai"}', b""
            )
        else:
            decisions.append(args)
            process.communicate.return_value = (
                b'{"result":"{\\"action\\":\\"wait\\"}","is_error":false}', b""
            )
        return process

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLI_SESSION_REUSE", "true")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = CLIAgentClient("claude")

    asyncio.run(client.chat([]))
    asyncio.run(client.chat([]))
    first_id = decisions[0][decisions[0].index("--session-id") + 1]
    assert "--no-session-persistence" not in decisions[0]
    assert decisions[1][decisions[1].index("--resume") + 1] == first_id
    assert json.loads((tmp_path / "cli_session.json").read_text())["session_id"] == first_id


def test_claude_replaces_missing_persisted_session(monkeypatch, tmp_path):
    old_id = "33333333-3333-4333-8333-333333333333"
    (tmp_path / "cli_session.json").write_text(json.dumps({
        "schema_version": 2, "provider": "claude", "model": None,
        "session_id": old_id, "turns": 1, "input_tokens": 0,
    }), encoding="utf-8")
    decisions = []

    async def spawn(*args, **kwargs):
        process = AsyncMock()
        process.returncode = 0
        if "auth" in args:
            process.communicate.return_value = (
                b'{"loggedIn":true,"authMethod":"claude.ai"}', b""
            )
        else:
            decisions.append(args)
            if "--resume" in args:
                response = {
                    "result": f"No conversation found with session ID: {old_id}",
                    "is_error": True,
                }
            else:
                response = {"result": '{"action":"wait"}', "is_error": False}
            process.communicate.return_value = (json.dumps(response).encode(), b"")
        return process

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = CLIAgentClient("claude")

    assert asyncio.run(client.chat([])) == '{"action":"wait"}'
    assert "--resume" in decisions[0]
    new_id = decisions[1][decisions[1].index("--session-id") + 1]
    assert new_id != old_id
    saved = json.loads((tmp_path / "cli_session.json").read_text(encoding="utf-8"))
    assert saved["session_id"] == new_id


@pytest.mark.parametrize("provider, fresh_flag", [
    ("codex", "--ephemeral"),
    ("claude", "--no-session-persistence"),
])
def test_session_reuse_can_be_disabled(monkeypatch, provider, fresh_flag):
    decision = '{"action":"wait"}'
    calls = []

    async def spawn(*args, **kwargs):
        process = AsyncMock()
        process.returncode = 0
        if "auth" in args:
            process.communicate.return_value = (
                b'{"loggedIn":true,"authMethod":"claude.ai"}', b""
            )
        else:
            calls.append(args)
            if provider == "codex":
                Path(args[args.index("--output-last-message") + 1]).write_text(
                    decision, encoding="utf-8"
                )
            process.communicate.return_value = (
                json.dumps({"result": decision}).encode(), b""
            )
        return process

    monkeypatch.setenv("CLI_SESSION_REUSE", "false")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(CLIAgentClient(provider).chat([]))
    assert fresh_flag in calls[0]


def test_failure_does_not_leak_diagnostics(monkeypatch):
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = (b"", b"secret-api-key")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match="exited with code 1") as error:
        asyncio.run(CLIAgentClient("claude").chat([]))
    assert "secret" not in str(error.value)


def test_usage_limit_is_classified_and_blocks_repeat_subprocess(monkeypatch, tmp_path):
    reset_at = 4102444800
    event = {
        "type": "task_complete",
        "error": {
            "message": "You've hit your usage limit.",
            "codex_error_info": "usage_limit_exceeded",
        },
        "rate_limits": {"primary": {"used_percent": 100, "resets_at": reset_at}},
    }
    process = AsyncMock()
    process.returncode = 1
    process.communicate.return_value = ((json.dumps(event) + "\n").encode(), b"")
    spawn = AsyncMock(return_value=process)
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = CLIAgentClient("codex")

    with pytest.raises(CLIProviderError, match="usage_limit_exceeded") as error:
        asyncio.run(client.chat([]))
    assert error.value.retry_at == reset_at
    assert not client.is_available()
    with pytest.raises(CLIProviderError, match="provider is temporarily paused|retry after"):
        asyncio.run(client.chat([]))
    assert spawn.call_count == 1
    saved = json.loads((tmp_path / "cli_session.json").read_text())
    assert saved["cooldown_until"] == reset_at


def test_session_rotates_after_turn_limit(monkeypatch, tmp_path):
    first_id = "44444444-4444-4444-8444-444444444444"
    second_id = "55555555-5555-4555-8555-555555555555"
    calls = []

    async def spawn(*args, **kwargs):
        calls.append(args)
        session_id = first_id if len(calls) == 1 else second_id
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"action":"wait"}', encoding="utf-8"
        )
        process = AsyncMock()
        process.returncode = 0
        process.communicate.return_value = ((json.dumps({
            "type": "thread.started", "thread_id": session_id,
        }) + "\n").encode(), b"")
        return process

    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLI_SESSION_MAX_TURNS", "1")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    client = CLIAgentClient("codex")

    asyncio.run(client.chat([]))
    asyncio.run(client.chat([]))
    assert "resume" not in calls[1]
    assert client._session_id == second_id


def test_legacy_unbounded_session_is_not_resumed(monkeypatch, tmp_path):
    legacy_id = "66666666-6666-4666-8666-666666666666"
    (tmp_path / "cli_session.json").write_text(json.dumps({
        "provider": "codex", "model": None, "session_id": legacy_id,
    }), encoding="utf-8")
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    client = CLIAgentClient("codex")
    assert client._session_id is None


def test_codex_exec_turn_usage_is_accumulated_when_not_cumulative():
    stdout = (json.dumps({
        "type": "turn.completed",
        "usage": {"input_tokens": 68474, "cached_input_tokens": 65920},
    }) + "\n").encode()
    assert _codex_input_tokens(stdout) == (68474, False)


def test_claude_usage_includes_cached_context_tokens():
    envelope = {"usage": {
        "input_tokens": 100,
        "cache_creation_input_tokens": 200,
        "cache_read_input_tokens": 300,
    }}
    assert _claude_input_tokens(envelope) == (600, False)


def test_timeout_cleans_up(monkeypatch):
    process = AsyncMock()
    process.returncode = None
    process.pid = 123
    process.kill = lambda: None
    process.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]
    killer = AsyncMock()
    monkeypatch.setattr("agent.brain.cli_agent.os.killpg", lambda *args: None, raising=False)
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=[process, killer]))
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(CLIAgentClient("claude").chat([]))
    assert process.communicate.call_count == 2


def test_defaults_to_codex_without_api_keys(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = build_client()
    assert client.provider == "codex"
    assert client.model is None


def test_switch_to_claude(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", " Claude ")
    monkeypatch.setenv("LLM_MODEL", " sonnet ")
    client = build_client()
    assert client.provider == "claude"
    assert client.model == "sonnet"


@pytest.mark.parametrize("provider", ["gemini", "openai", "vertex", "ollama", "typo", ""])
def test_reject_old_providers_instead_of_api_fallback(monkeypatch, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    with pytest.raises(ValueError, match="codex or claude"):
        build_client()


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_real_subprocess_subscription_contract(monkeypatch, tmp_path, provider):
    # Exercise real stdin/stdout, UTF-8, cwd isolation and final-output extraction
    # without making a model request or depending on a local CLI login.
    script = tmp_path / "fake cli.py"
    script.write_text('''import json, os, pathlib, sys
args = sys.argv[1:]
assert "OPENAI_API_KEY" not in os.environ
assert "ANTHROPIC_API_KEY" not in os.environ
assert "ANTHROPIC_AUTH_TOKEN" not in os.environ
assert "CLAUDE_CODE_USE_VERTEX" not in os.environ
if "auth" in args:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}))
    sys.exit(0)
payload = sys.stdin.buffer.read().decode("utf-8")
assert "鑽石" in payload
assert "JSON only" in payload
decision = json.dumps({"action": "plan", "commands": ["mine diamond 3"]})
if "exec" in args:
    assert 'forced_login_method="chatgpt"' in args
    assert "--ignore-user-config" in args
    pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(decision, encoding="utf-8")
    print("CLI progress, not the final decision")
else:
    assert json.loads(args[args.index("--settings") + 1])["forceLoginMethod"] == "claudeai"
    assert args[args.index("--tools") + 1] == ""
    print(json.dumps({"result": decision, "is_error": False}))
''', encoding="utf-8")
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_VERTEX"):
        monkeypatch.setenv(variable, "must-not-be-inherited")
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: [sys.executable, str(script)])
    response = asyncio.run(CLIAgentClient(provider).chat([{"role": "user", "content": "挖鑽石"}], "JSON only"))
    assert json.loads(response) == {"action": "plan", "commands": ["mine diamond 3"]}
    # Only the child's environment is filtered.
    assert os.environ["OPENAI_API_KEY"] == "must-not-be-inherited"


@pytest.mark.parametrize("envelope", [{"is_error": True, "result": "quota"}, {"result": ""}])
def test_claude_error_or_empty_result(monkeypatch, envelope):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.side_effect = [
        (b'{"loggedIn":true,"authMethod":"claude.ai"}', b""),
        (json.dumps(envelope).encode(), b""),
    ]
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError):
        asyncio.run(CLIAgentClient("claude").chat([]))


@pytest.mark.parametrize("auth", [
    {"loggedIn": True, "authMethod": "api_key"},
    {"loggedIn": True, "authMethod": "console"},
    {"loggedIn": False},
])
def test_claude_refuses_non_subscription_before_inference(monkeypatch, auth):
    process = AsyncMock()
    process.returncode = 0
    process.communicate.return_value = (json.dumps(auth).encode(), b"")
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("agent.brain.cli_agent._executable", lambda _: ["fake-cli"])
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="subscription login required"):
        asyncio.run(CLIAgentClient("claude").chat([]))
    assert spawn.call_count == 1
    assert "auth" in spawn.call_args.args
