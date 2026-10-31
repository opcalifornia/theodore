"""Theodore's tools as an MCP server, for a Claude Code session to drive.

`theodore mcp-serve` speaks MCP (JSON-RPC 2.0, one message per line on
stdio). Claude Code -- running on the editor's Claude plan -- calls the same
tools the API-driven agent uses, through the same safety rails (checkpoints,
read-backs, no deleting source media).

The two confirmations (deleting, starting a render) can't pop a dialog from
in here, so those tools take ``editor_confirmed``: without it they return
what would happen and ask Claude to get a yes in the chat first.
"""
from __future__ import annotations

import base64
import json
import sys
from typing import Optional

from theodore.agent import tools as agent_tools

PROTOCOL = "2025-06-18"
CONFIRM_ARG = "editor_confirmed"


def _needs_confirm(tool) -> bool:
    return tool.kind in agent_tools.CONFIRMED_KINDS or tool.name == "call_resolve_api"


def tool_specs() -> list[dict]:
    specs = []
    for t in agent_tools.TOOLS:
        schema = json.loads(json.dumps(t.schema))
        if _needs_confirm(t):
            schema.setdefault("properties", {})[CONFIRM_ARG] = {
                "type": "boolean",
                "description": "Set true ONLY after the editor said yes in the chat to exactly this action."}
        specs.append({"name": t.name, "description": t.description, "inputSchema": schema})
    return specs


class Server:
    def __init__(self, ctx: agent_tools.AgentContext):
        self.ctx = ctx
        self._confirmed = False
        ctx.confirm = lambda _description: self._confirmed

    def call(self, name: str, args: dict) -> dict:
        tool = agent_tools.TOOLS_BY_NAME.get(name)
        if tool is None:
            return {"content": [{"type": "text", "text": json.dumps({"ok": False, "error": f"No tool {name}"})}],
                    "isError": True}
        args = dict(args or {})
        self._confirmed = bool(args.pop(CONFIRM_ARG, False))
        if tool.kind in agent_tools.CONFIRMED_KINDS and not self._confirmed:
            data = {"ok": False, "needs_confirmation": True,
                    "action": f"{name}({json.dumps(args)[:300]})",
                    "next": "Ask the editor in plain words whether to do this. Only if they say yes, "
                            f"call {name} again with {CONFIRM_ARG}=true."}
            return {"content": [{"type": "text", "text": json.dumps(data)}], "isError": False}
        out = agent_tools.execute(tool, self.ctx, args)
        content = [{"type": "text", "text": json.dumps(out.data, default=str)[:60000]}]
        for img in out.images:
            content.append({"type": "image", "data": base64.b64encode(img).decode(), "mimeType": "image/png"})
        return {"content": content, "isError": not out.data.get("ok", True)}

    def handle(self, msg: dict) -> Optional[dict]:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None  # notifications (initialized, cancelled) need no answer
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "theodore", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": tool_specs()}
        elif method == "tools/call":
            p = msg.get("params", {})
            result = self.call(p.get("name"), p.get("arguments") or {})
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(ctx: agent_tools.AgentContext, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    server = Server(ctx)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            reply = server.handle(msg)
        except Exception as exc:  # noqa: BLE001 -- never kill the session over one call
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "result": {"content": [{"type": "text", "text": json.dumps(
                         {"ok": False, "error": f"{type(exc).__name__}: {exc}"})}], "isError": True}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
