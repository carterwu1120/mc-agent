"""CLI planning adapters; Minecraft tools remain owned by PlanExecutor."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import uuid

from .base import LLMClient


class _CLIProcessError(RuntimeError):
    """Keep stderr available for recovery checks without exposing it to callers."""

    def __init__(self, provider: str, returncode: int, stderr: bytes):
        super().__init__(
            f"{provider} CLI exited with code {returncode}; "
            "check CLI subscription login/configuration"
        )
        self.stderr = stderr.decode("utf-8", errors="replace")[-4000:]


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
        self._session_id: str | None = None
        self._session_file: Path | None = None
        data_dir = os.getenv("BOT_DATA_DIR", "").strip()
        if self.session_reuse and data_dir:
            self._session_file = Path(data_dir) / "cli_session.json"
            self._session_id = self._load_session_id()
        self._lock = asyncio.Lock()

    def _load_session_id(self) -> str | None:
        if self._session_file is None:
            return None
        try:
            saved = json.loads(self._session_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return None
        if not isinstance(saved, dict):
            return None
        if saved.get("provider") != self.provider or saved.get("model") != self.model:
            return None
        return _valid_session_id(saved.get("session_id"))

    def _save_session_id(self, session_id: str) -> None:
        self._session_id = session_id
        if self._session_file is None:
            return
        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._session_file.with_name(
            f".{self._session_file.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(json.dumps({
            "provider": self.provider,
            "model": self.model,
            "session_id": session_id,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._session_file)

    def _forget_session(self) -> None:
        self._session_id = None
        if self._session_file is not None:
            try:
                self._session_file.unlink()
            except FileNotFoundError:
                pass

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
            raise _CLIProcessError(self.provider, process.returncode, _stderr)
        return stdout

    async def _invoke(self, prompt: bytes, directory: str, session_id: str | None) -> tuple[str, str | None]:
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
                    self.provider, 1, diagnostic.encode("utf-8", errors="replace")
                )
                if session_id and _session_is_missing(missing_error):
                    raise missing_error
                raise RuntimeError(
                    "claude CLI reported an unsuccessful run; check CLI login/configuration"
                )
            result = envelope.get("result", "").strip()
        if not result:
            raise RuntimeError(f"{self.provider} CLI returned an empty decision")
        return result, candidate_session_id

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        prompt = json.dumps({"instructions": system or "", "messages": messages}, ensure_ascii=False)
        prompt = (
            "Act as a Minecraft planning component. Follow the instructions in the JSON below. "
            "Use only the supplied game state. Return only the requested JSON decision. "
            "Do not use shell, filesystem, web or other tools.\n" + prompt
        )
        async with self._lock:
            with tempfile.TemporaryDirectory(prefix="mc-agent-") as directory:
                session_id = self._session_id if self.session_reuse else None
                try:
                    result, new_session_id = await self._invoke(
                        prompt.encode("utf-8"), directory, session_id
                    )
                except _CLIProcessError as error:
                    if not session_id or not _session_is_missing(error):
                        raise
                    # A deleted/corrupt CLI history must not permanently wedge the bot.
                    self._forget_session()
                    result, new_session_id = await self._invoke(
                        prompt.encode("utf-8"), directory, None
                    )
                if new_session_id:
                    self._save_session_id(new_session_id)
                return result
