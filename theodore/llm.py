"""Where Theodore's Claude calls go: the editor's Claude plan, or the API.

The editor runs Claude Code signed in with their claude.ai account. Calls made
through it (`claude -p`, headless) run on that plan -- no API credits, no
other subscriptions. So by default every Claude call Theodore makes goes
through Claude Code; the Anthropic API is the fallback when Claude Code isn't
installed, or when THEODORE_LLM=api.

`ClaudeCodeClient` mimics the slice of the Anthropic SDK that Theodore's
single-shot passes use (``client.messages.create(...)`` returning
``.content[].text`` and ``.usage``), so call_json and everything built on it
work unchanged, images included.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from types import SimpleNamespace
from typing import Optional

BACKEND_ENV = "THEODORE_LLM"   # auto | claude_code | api
TIMEOUT = 900


class LLMError(RuntimeError):
    pass


def claude_cli() -> Optional[str]:
    return shutil.which("claude") or next(
        (p for p in (os.path.expanduser("~/.local/bin/claude"), "/opt/homebrew/bin/claude",
                     "/usr/local/bin/claude") if os.path.exists(p)), None)


def backend() -> str:
    choice = (os.environ.get(BACKEND_ENV) or "auto").strip().lower()
    if choice in ("api", "claude_code"):
        return choice
    return "claude_code" if claude_cli() else "api"


def subscription_env() -> dict:
    """Environment for a Claude Code child that uses the signed-in plan: an
    API key in the environment would make it bill the API instead."""
    env = dict(os.environ)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    # GUI-launched processes (the Resolve panel) get a bare PATH.
    env["PATH"] = os.pathsep.join([os.path.dirname(claude_cli() or ""), env.get("PATH", ""),
                                   "/opt/homebrew/bin", "/usr/local/bin"])
    return env


def _flatten(messages: list) -> list:
    """Claude Code's stream input takes user turns only. Fold earlier turns
    (e.g. call_json's JSON-repair retry) into one user message."""
    if len(messages) == 1:
        content = messages[0]["content"]
        return content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
    blocks = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            blocks.append({"type": "text", "text": f"[{m['role']}]\n{content}"})
        else:
            blocks += [b if b.get("type") != "text" else {"type": "text", "text": f"[{m['role']}]\n{b['text']}"}
                       for b in content]
    return blocks


class _Messages:
    def __init__(self, cli: str):
        self.cli = cli

    def create(self, *, model: str, max_tokens: int = 4096, system: str = "", messages: list,
               tools=None, **_ignored):
        if tools:
            raise LLMError("Tool use goes through the Claude Code agent session, not single calls.")
        payload = {"type": "user", "message": {"role": "user", "content": _flatten(messages)}}
        cmd = [self.cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
               "--verbose", "--model", model, "--tools", "", "--max-turns", "1",
               "--system-prompt", system or "You are a helpful assistant."]
        try:
            done = subprocess.run(cmd, input=json.dumps(payload) + "\n", capture_output=True, text=True,
                                  timeout=TIMEOUT, env=subscription_env())
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"Claude Code didn't answer within {TIMEOUT}s.") from exc
        result = None
        for line in done.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "result":
                result = ev
        if result is None or result.get("is_error"):
            detail = (result or {}).get("result") or done.stderr.strip()[-600:] or done.stdout.strip()[-600:]
            raise LLMError(f"Claude Code call failed: {detail}")
        usage = result.get("usage") or {}
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=result.get("result") or "")],
            usage=SimpleNamespace(input_tokens=int(usage.get("input_tokens") or 0)
                                  + int(usage.get("cache_read_input_tokens") or 0)
                                  + int(usage.get("cache_creation_input_tokens") or 0),
                                  output_tokens=int(usage.get("output_tokens") or 0)),
            stop_reason=result.get("stop_reason"))


class ClaudeCodeClient:
    """Drop-in for ``anthropic.Anthropic()`` in single-shot passes."""

    def __init__(self, cli: Optional[str] = None):
        cli = cli or claude_cli()
        if not cli:
            raise LLMError("Claude Code (the `claude` command) isn't installed.")
        self.messages = _Messages(cli)


def make_client():
    """The client every Theodore Claude call should use."""
    if backend() == "claude_code":
        return ClaudeCodeClient()
    import anthropic
    from theodore import config
    return anthropic.Anthropic(api_key=config.require_anthropic_key())
