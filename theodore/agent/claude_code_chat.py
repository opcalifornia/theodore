"""The panel chat, run by Claude Code on the editor's Claude plan.

One long-lived `claude -p` session (stream-json in and out) holds the whole
conversation. Its only tools are Theodore's, served by `theodore mcp-serve`;
Claude Code's own file/shell tools are switched off. This module relays the
editor's lines in and translates Claude Code's events into the event stream
the Resolve panel already renders (ready / delta / tool / result / done).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from typing import Callable, Optional

from theodore import llm
from theodore.agent import memory, prompts

SERVER = "theodore"
PREFIX = f"mcp__{SERVER}__"


def _mcp_config(theodore_project: str) -> str:
    args = ["-m", "theodore.cli", "mcp-serve"] + (["--project", theodore_project] if theodore_project else [])
    cfg = {"mcpServers": {SERVER: {"command": sys.executable, "args": args,
                                   "env": {"PYTHONUNBUFFERED": "1"}}}}
    fd, path = tempfile.mkstemp(prefix="theodore-mcp-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(cfg, fh)
    return path


def start(project_name: str, theodore_project: str, model: str) -> subprocess.Popen:
    cli = llm.claude_cli()
    if not cli:
        raise llm.LLMError("Claude Code isn't installed.")
    system = prompts.SYSTEM_PROMPT
    recalled = memory.prompt_block(project_name or "")
    if recalled:
        system += "\n" + recalled
    cmd = [cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--include-partial-messages", "--model", model, "--system-prompt", system,
           "--mcp-config", _mcp_config(theodore_project), "--strict-mcp-config",
           "--tools", "", "--allowedTools", f"mcp__{SERVER}"]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, env=llm.subscription_env(),
                            cwd=tempfile.gettempdir())


def _tool_result_ok(block: dict) -> tuple[bool, Optional[str], bool]:
    content = block.get("content")
    text = ""
    has_image = False
    if isinstance(content, list):
        for c in content:
            if c.get("type") == "text":
                text += c.get("text", "")
            elif c.get("type") == "image":
                has_image = True
    elif isinstance(content, str):
        text = content
    try:
        data = json.loads(text)
        return bool(data.get("ok", True)) and not block.get("is_error"), data.get("error"), has_image
    except ValueError:
        return not block.get("is_error"), (text[:300] if block.get("is_error") else None), has_image


def run(project_name: str, theodore_project: str, model: str, out: Callable, read_line: Callable,
        ready_text: str) -> None:
    proc = start(project_name, theodore_project, model)
    events: list = []
    cond = threading.Condition()

    def pump():
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with cond:
                events.append(ev)
                cond.notify()
        with cond:
            events.append(None)
            cond.notify()

    threading.Thread(target=pump, daemon=True).start()
    out("ready", text=ready_text, project=project_name, via="claude-code")

    def next_event():
        with cond:
            while not events:
                cond.wait()
            return events.pop(0)

    while True:
        line = read_line()
        if line is None or line.lower() in ("exit", "quit"):
            break
        if not line:
            continue
        proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": line}}) + "\n")
        proc.stdin.flush()
        streamed = False
        names: dict = {}
        while True:
            ev = next_event()
            if ev is None:
                err = proc.stderr.read()[-800:] if proc.stderr else ""
                out("error", text=f"Claude Code stopped unexpectedly. {err}".strip())
                out("done")
                return
            kind = ev.get("type")
            if kind == "stream_event":
                inner = ev.get("event", {})
                delta = inner.get("delta", {})
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    out("delta", text=delta.get("text", ""))
                    streamed = True
                elif inner.get("type") == "message_stop" and streamed:
                    out("say_end")
                    streamed = False
            elif kind == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        name = block.get("name", "")
                        names[block.get("id")] = name
                        out("tool", name=name[len(PREFIX):] if name.startswith(PREFIX) else name,
                            input=block.get("input", {}))
            elif kind == "user":
                content = ev.get("message", {}).get("content", [])
                for block in content if isinstance(content, list) else []:
                    if block.get("type") == "tool_result":
                        ok, error, frame = _tool_result_ok(block)
                        name = names.get(block.get("tool_use_id"), "")
                        out("result", name=name[len(PREFIX):] if name.startswith(PREFIX) else name,
                            ok=ok, error=error, frame=frame)
            elif kind == "result":
                if ev.get("is_error"):
                    out("error", text=str(ev.get("result") or "Claude Code reported an error."))
                if streamed:
                    out("say_end")
                out("done")
                break
    try:
        proc.stdin.close()
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
