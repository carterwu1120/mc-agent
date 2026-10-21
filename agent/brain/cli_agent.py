"""One-shot CLI planning adapters; Minecraft tools remain owned by PlanExecutor."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile

from .base import LLMClient


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

    Calls are isolated: task_memory supplies continuity, never an implicit CLI session.
    """

    def __init__(self, provider: str, model: str | None = None):
        if provider not in ("codex", "claude"):
            raise ValueError(f"Unsupported CLI provider: {provider}")
        self.provider = provider
        self.model = model
        self.timeout = float(os.getenv("CLI_AGENT_TIMEOUT", "120"))
        if self.timeout <= 0:
            raise ValueError("CLI_AGENT_TIMEOUT must be positive")
        self._lock = asyncio.Lock()

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
            raise RuntimeError(f"{self.provider} CLI exited with code {process.returncode}; check CLI subscription login/configuration")
        return stdout

    async def chat(self, messages: list[dict], system: str | None = None) -> str:
        prompt = json.dumps({"instructions": system or "", "messages": messages}, ensure_ascii=False)
        prompt = (
            "Act as a Minecraft planning component. Follow the instructions in the JSON below. "
            "Use only the supplied game state. Return only the requested JSON decision. "
            "Do not use shell, filesystem, web or other tools.\n" + prompt
        )
        async with self._lock:
            with tempfile.TemporaryDirectory(prefix="mc-agent-") as directory:
                output = Path(directory) / "response.txt"
                command = _executable(self.provider)
                env = _subscription_environment()
                if self.provider == "claude":
                    status = await self._run(command + ["--setting-sources", "", "auth", "status", "--json"], directory, env)
                    try:
                        auth = json.loads(status)
                    except (ValueError, TypeError):
                        raise RuntimeError("Unable to verify Claude subscription login; run claude auth status") from None
                    if not isinstance(auth, dict) or not auth.get("loggedIn") or auth.get("authMethod") != "claude.ai":
                        raise RuntimeError("Claude subscription login required; run claude and /login with your Claude account. API/Console login is not accepted.")
                if self.provider == "codex":
                    command += ["exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                                "-c", 'forced_login_method="chatgpt"',
                                "--color", "never", "--output-last-message", str(output)]
                else:
                    command += ["--print", "--output-format", "json", "--tools", "",
                                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                                "--setting-sources", "", "--settings", '{"forceLoginMethod":"claudeai"}',
                                "--disable-slash-commands", "--no-session-persistence"]
                if self.model:
                    command += ["--model", self.model]
                if self.provider == "codex":
                    command += ["-"]
                stdout = await self._run(command, directory, env, prompt.encode("utf-8"))
                if self.provider == "codex":
                    result = output.read_text(encoding="utf-8").strip() if output.exists() else ""
                else:
                    envelope = json.loads(stdout.decode("utf-8"))
                    if envelope.get("is_error"):
                        raise RuntimeError("claude CLI reported an unsuccessful run; check CLI login/configuration")
                    result = envelope.get("result", "").strip()
                if not result:
                    raise RuntimeError(f"{self.provider} CLI returned an empty decision")
                return result
