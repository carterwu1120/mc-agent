"""CLI planning adapters; Minecraft tools remain owned by PlanExecutor."""
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import time
import uuid

from .base import LLMClient


class CLIProviderError(RuntimeError):
    """A classified, safe-to-log provider failure."""

    def __init__(
        self,
        provider: str,
        category: str,
        message: str,
        *,
        retry_at: float | None = None,
    ):
        super().__init__(f"{provider} CLI {category}: {message}")
        self.provider = provider
        self.category = category
        self.retry_at = retry_at


class _CLIProcessError(RuntimeError):
    """Keep raw child output private while retaining it for classification."""

    def __init__(self, provider: str, returncode: int, stderr: bytes, stdout: bytes = b""):
        super().__init__(f"{provider} CLI exited with code {returncode}")
        self.provider = provider
        self.returncode = returncode
        self.stderr = stderr.decode("utf-8", errors="replace")[-4000:]
        self.stdout = stdout.decode("utf-8", errors="replace")[-16000:]


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _json_events(raw: str):
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _event_diagnostics(error: _CLIProcessError) -> tuple[str | None, str | None, float | None]:
    """Extract structured Codex/Claude failure data without logging raw output."""
    category = None
    message = None
    reset_candidates: list[float] = []
    for event in (*_json_events(error.stdout), *_json_events(error.stderr)):
        for item in _walk_dicts(event):
            value = item.get("codex_error_info") or item.get("codexErrorInfo")
            if isinstance(value, str):
                category = value.lower()
            candidate = item.get("message")
            if category and isinstance(candidate, str):
                message = candidate
            resets_at = item.get("resets_at") or item.get("resetsAt")
            if isinstance(resets_at, (int, float)) and resets_at > time.time():
                reset_candidates.append(float(resets_at))
    combined = f"{error.stdout}\n{error.stderr}"
    lowered = combined.lower()
    if not category:
        if "usage_limit_exceeded" in lowered or "hit your usage limit" in lowered:
            category = "usage_limit_exceeded"
        elif any(text in lowered for text in (
            "not logged in", "login required", "unauthorized", "authentication failed",
            "invalid_grant", "401",
        )):
            category = "authentication_error"
        elif any(text in lowered for text in (
            "timed out", "connection reset", "connection refused", "network is unreachable",
            "temporary failure in name resolution", "dns", "503", "service unavailable",
        )):
            category = "network_error"

    retry_at = min(reset_candidates) if reset_candidates else None
    if category == "usage_limit_exceeded" and retry_at is None:
        match = re.search(r"try again at\s+(\d{1,2}):(\d{2})\s*([ap]m)", combined, re.I)
        if match:
            hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
            now = datetime.now().astimezone()
            retry = now.replace(hour=hour, minute=int(match.group(2)), second=0, microsecond=0)
            if retry <= now:
                retry += timedelta(days=1)
            retry_at = retry.timestamp()
    return category, message, retry_at


def _classify_process_error(error: _CLIProcessError) -> CLIProviderError:
    category, _raw_message, retry_at = _event_diagnostics(error)
    if category == "usage_limit_exceeded":
        if retry_at:
            when = datetime.fromtimestamp(retry_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            message = f"usage limit exceeded; paused until {when}"
        else:
            message = "usage limit exceeded; retry is temporarily paused"
        return CLIProviderError(error.provider, category, message, retry_at=retry_at)
    if category == "authentication_error":
        return CLIProviderError(
            error.provider, category,
            f"subscription login is invalid; run `{error.provider} login` and check login status",
        )
    if category == "network_error":
        return CLIProviderError(
            error.provider, category,
            "network or provider service is unavailable",
        )
    return CLIProviderError(
        error.provider,
        "process_error",
        f"process exited with code {error.returncode}; check CLI login/configuration",
    )


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _valid_session_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _codex_thread_id(stdout: bytes) -> str | None:
    """Extract the stable thread id from a codex exec --json event stream."""
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            session_id = _valid_session_id(event.get("thread_id"))
            if session_id:
                return session_id
    return None


def _codex_input_tokens(stdout: bytes) -> tuple[int | None, bool]:
    """Return input usage and whether it is already cumulative for the thread."""
    cumulative = None
    per_turn = None
    for event in _json_events(stdout.decode("utf-8", errors="replace")):
        for item in _walk_dicts(event):
            usage = item.get("total_token_usage") or item.get("totalTokenUsage")
            if isinstance(usage, dict):
                value = usage.get("input_tokens") or usage.get("inputTokens")
                if isinstance(value, (int, float)) and value >= 0:
                    cumulative = int(value)
            if item.get("type") == "turn.completed" and isinstance(item.get("usage"), dict):
                value = item["usage"].get("input_tokens") or item["usage"].get("inputTokens")
                if isinstance(value, (int, float)) and value >= 0:
                    per_turn = int(value)
    if cumulative is not None:
        return cumulative, True
    return per_turn, False


def _claude_input_tokens(envelope: dict) -> tuple[int | None, bool]:
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None, False
    total = 0
    found = False
    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            total += int(value)
            found = True
    return (total if found else None), False


def _session_is_missing(error: _CLIProcessError) -> bool:
    message = error.stderr.lower()
    return any(pattern in message for pattern in (
        "no saved session found",
        "session not found",
        "conversation not found",
        "unknown session",
        "failed to find thread",
        "no rollout found for thread id",
        "no conversation found with session id",
    ))


def _subscription_environment() -> dict[str, str]:
    """Keep CLI login credentials, but exclude inherited API billing overrides."""
    excluded = {
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    }
    return {key: value for key, value in os.environ.items() if key.upper() not in excluded}


def _executable(provider: str) -> list[str]:
    configured = os.getenv(f"{provider.upper()}_CLI_PATH")
    executable = shutil.which(configured or provider)
    if not executable:
        raise RuntimeError(f"{provider} CLI not found; install/login or set {provider.upper()}_CLI_PATH")
    path = Path(executable)
    if os.name == "nt" and path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # Launch npm's JS entrypoint directly: never interpolate prompts into cmd.exe.
        package = "@openai/codex/bin/codex.js" if provider == "codex" else "@anthropic-ai/claude-code/cli.js"
        entry = path.parent / "node_modules" / package
        node = path.parent / "node.exe"
        runtime = str(node) if node.exists() else shutil.which("node")
        if not entry.exists() or not runtime:
            raise RuntimeError(f"Cannot resolve npm launcher {path}; set {provider.upper()}_CLI_PATH to a native executable")
        return [runtime, str(entry)]
    return [executable]


class CLIAgentClient(LLMClient):
    """Use CLI authentication and return only the agent's final JSON decision.

    By default, each bot resumes its own persisted CLI session. Minecraft actions
    still pass only through the validated PlanExecutor command path.
    """

    def __init__(self, provider: str, model: str | None = None):
        if provider not in ("codex", "claude"):
            raise ValueError(f"Unsupported CLI provider: {provider}")
        self.provider = provider
        self.model = model
        self.timeout = float(os.getenv("CLI_AGENT_TIMEOUT", "120"))
        if self.timeout <= 0:
            raise ValueError("CLI_AGENT_TIMEOUT must be positive")
        self.session_reuse = _enabled("CLI_SESSION_REUSE")
        self.session_max_turns = _positive_int_env("CLI_SESSION_MAX_TURNS", 8)
        self.session_max_input_tokens = _positive_int_env("CLI_SESSION_MAX_INPUT_TOKENS", 200000)
        self.usage_limit_cooldown = _positive_int_env("CLI_USAGE_LIMIT_COOLDOWN", 3600)
        self._session_id: str | None = None
        self._session_file: Path | None = None
        self._session_turns = 0
        self._session_input_tokens = 0
        self._cooldown_until = 0.0
        self._cooldown_category: str | None = None
        data_dir = os.getenv("BOT_DATA_DIR", "").strip()
        if self.session_reuse and data_dir:
            self._session_file = Path(data_dir) / "cli_session.json"
            self._load_session()
        self._lock = asyncio.Lock()

    def _load_session(self) -> None:
        if self._session_file is None:
            return
        try:
            saved = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        if saved.get("provider") != self.provider or saved.get("model") != self.model:
            return
        cooldown_until = saved.get("cooldown_until")
        if isinstance(cooldown_until, (int, float)) and cooldown_until > time.time():
            self._cooldown_until = float(cooldown_until)
            self._cooldown_category = str(saved.get("cooldown_category") or "provider_cooldown")
        # Legacy files do not have bounded-session accounting. Starting fresh
        # avoids resuming an already enormous conversation after this upgrade.
        if saved.get("schema_version") != 2:
            return
        self._session_id = _valid_session_id(saved.get("session_id"))
        self._session_turns = max(0, int(saved.get("turns", 0) or 0))
        self._session_input_tokens = max(0, int(saved.get("input_tokens", 0) or 0))

    def _save_state(self) -> None:
        if self._session_file is None:
            return
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._session_file.with_name(
            f".{self._session_file.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(json.dumps({
            "schema_version": 2,
            "provider": self.provider,
            "model": self.model,
            "session_id": self._session_id,
            "turns": self._session_turns,
            "input_tokens": self._session_input_tokens,
            "cooldown_until": self._cooldown_until or None,
            "cooldown_category": self._cooldown_category,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._session_file)

    def _save_session_id(self, session_id: str) -> None:
        self._session_id = session_id
        self._save_state()

    def _forget_session(self) -> None:
        self._session_id = None
        self._session_turns = 0
        self._session_input_tokens = 0
        self._save_state()

    def reset_session(self, reason: str = "task_finished") -> None:
        if self._session_id:
            print(f"[CLI] rotate {self.provider} session: {reason}")
        self._forget_session()

    def _clear_expired_cooldown(self) -> None:
        if self._cooldown_until and time.time() >= self._cooldown_until:
            self._cooldown_until = 0.0
            self._cooldown_category = None
            self._save_state()

    def is_available(self) -> bool:
        self._clear_expired_cooldown()
        return not self._cooldown_until

    def unavailable_reason(self) -> str | None:
        if self.is_available():
            return None
        when = datetime.fromtimestamp(self._cooldown_until).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return f"{self._cooldown_category}; retry after {when}"

    def _check_rotation(self) -> None:
        if not self._session_id:
            return
        if self._session_turns >= self.session_max_turns:
            self.reset_session(f"turn limit {self._session_turns}/{self.session_max_turns}")
        elif self._session_input_tokens >= self.session_max_input_tokens:
            self.reset_session(
                f"input token limit {self._session_input_tokens}/{self.session_max_input_tokens}"
            )

    def _set_cooldown(self, error: CLIProviderError) -> None:
        if error.category != "usage_limit_exceeded":
            return
        self._cooldown_until = error.retry_at or (time.time() + self.usage_limit_cooldown)
        self._cooldown_category = error.category
        self._save_state()

    async def _run(self, command: list[str], directory: str, env: dict[str, str], data: bytes = b"") -> bytes:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=directory, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, start_new_session=os.name != "nt",
        )
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(data), timeout=self.timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await process.communicate()
            raise
        if process.returncode:
            # Diagnostics can contain credentials or prompt data; don't log them.
            raise _CLIProcessError(self.provider, process.returncode, _stderr, stdout)
        return stdout

    async def _invoke(
        self, prompt: bytes, directory: str, session_id: str | None
    ) -> tuple[str, str | None, tuple[int | None, bool]]:
        output = Path(directory) / "response.txt"
        command = _executable(self.provider)
        env = _subscription_environment()
        candidate_session_id: str | None = None

        if self.provider == "claude":
            status = await self._run(
                command + ["--setting-sources", "", "auth", "status", "--json"],
                directory,
                env,
            )
            try:
                auth = json.loads(status)
            except (ValueError, TypeError):
                raise RuntimeError(
                    "Unable to verify Claude subscription login; run claude auth status"
                ) from None
            if (
                not isinstance(auth, dict)
                or not auth.get("loggedIn")
                or auth.get("authMethod") != "claude.ai"
            ):
                raise RuntimeError(
                    "Claude subscription login required; run claude and /login with your "
                    "Claude account. API/Console login is not accepted."
                )

        if self.provider == "codex":
            if session_id:
                command += [
                    "exec", "resume", "--ignore-user-config", "--skip-git-repo-check",
                    "-c", 'forced_login_method="chatgpt"', "--json",
                    "--output-last-message", str(output),
                ]
            else:
                command += [
                    "exec", "--ignore-user-config", "--skip-git-repo-check",
                    "--sandbox", "read-only", "-c", 'forced_login_method="chatgpt"',
                    "--color", "never", "--output-last-message", str(output),
                ]
                if self.session_reuse:
                    command += ["--json"]
                else:
                    command += ["--ephemeral"]
            if self.model:
                command += ["--model", self.model]
            if session_id:
                command += [session_id, "-"]
            else:
                command += ["-"]
        else:
            command += [
                "--print", "--output-format", "json", "--tools", "",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--setting-sources", "", "--settings", '{"forceLoginMethod":"claudeai"}',
                "--disable-slash-commands",
            ]
            if self.session_reuse:
                if session_id:
                    command += ["--resume", session_id]
                else:
                    candidate_session_id = str(uuid.uuid4())
                    command += ["--session-id", candidate_session_id]
            else:
                command += ["--no-session-persistence"]
            if self.model:
                command += ["--model", self.model]

        stdout = await self._run(command, directory, env, prompt)
        if self.provider == "codex":
            result = output.read_text(encoding="utf-8").strip() if output.exists() else ""
            if self.session_reuse and not session_id:
                candidate_session_id = _codex_thread_id(stdout)
        else:
            envelope = json.loads(stdout.decode("utf-8"))
            if envelope.get("is_error"):
                diagnostic = str(envelope.get("result", ""))
                missing_error = _CLIProcessError(
                    self.provider, 1, diagnostic.encode("utf-8", errors="replace"), stdout
                )
                raise missing_error
            result = envelope.get("result", "").strip()
        if not result:
            raise RuntimeError(f"{self.provider} CLI returned an empty decision")
        usage = (
            _codex_input_tokens(stdout)
            if self.provider == "codex"
            else _claude_input_tokens(envelope)
        )
        return result, candidate_session_id, usage

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        prompt = json.dumps({"instructions": system or "", "messages": messages}, ensure_ascii=False)
        prompt = (
            "Act as a Minecraft planning component. Follow the instructions in the JSON below. "
            "Use only the supplied game state. Return only the requested JSON decision. "
            "Do not use shell, filesystem, web or other tools.\n" + prompt
        )
        async with self._lock:
            if not self.is_available():
                raise CLIProviderError(
                    self.provider,
                    self._cooldown_category or "provider_cooldown",
                    self.unavailable_reason() or "provider is temporarily paused",
                    retry_at=self._cooldown_until,
                )
            self._check_rotation()
            with tempfile.TemporaryDirectory(prefix="mc-agent-") as directory:
                session_id = self._session_id if self.session_reuse else None
                try:
                    result, new_session_id, usage = await self._invoke(
                        prompt.encode("utf-8"), directory, session_id
                    )
                except _CLIProcessError as error:
                    if session_id and _session_is_missing(error):
                        # A deleted/corrupt CLI history must not permanently wedge the bot.
                        self._forget_session()
                        try:
                            result, new_session_id, usage = await self._invoke(
                                prompt.encode("utf-8"), directory, None
                            )
                        except _CLIProcessError as retry_error:
                            classified = _classify_process_error(retry_error)
                            self._set_cooldown(classified)
                            raise classified from None
                    else:
                        classified = _classify_process_error(error)
                        self._set_cooldown(classified)
                        raise classified from None
                if new_session_id:
                    self._save_session_id(new_session_id)
                if self.session_reuse and self._session_id:
                    self._session_turns += 1
                    input_tokens, cumulative = usage
                    if input_tokens is not None:
                        if cumulative:
                            self._session_input_tokens = input_tokens
                        else:
                            self._session_input_tokens += input_tokens
                    self._save_state()
                return result
