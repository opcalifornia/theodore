"""The operator loop: understand -> inspect -> plan -> execute -> verify ->
correct -> summarise.

The model does the thinking; this module does the part that can't be left to
the model's good intentions:

* **Verification is enforced, not requested.** Every edit's result carries a
  state read-back computed by code (see ``tools.timeline_snapshot``). And if
  the model tries to finish after editing without having *looked* at the
  result (``export_frame``), it is sent back to do so -- twice at most, then
  the run ends flagged as unverified rather than looping forever.
* **A step budget**, so a confused run stops instead of editing endlessly.
* **Failures are data.** A tool error goes back to the model as a normal
  result and it adapts; nothing here raises into the conversation.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from theodore.agent import memory
from theodore.agent.prompts import SYSTEM_PROMPT
from theodore.agent.tools import TOOLS, TOOLS_BY_NAME, AgentContext, execute

DEFAULT_MODEL = os.environ.get("THEODORE_AGENT_MODEL", "claude-sonnet-5-5")
MAX_VERIFY_NUDGES = 2

VERIFY_NUDGE = (
    "You changed the timeline but haven't looked at the result. Before you finish: read the "
    "state back (get_timeline_info) and call export_frame at a frame that should show the "
    "change. If anything is black, misframed, the wrong length, or not what was asked, fix it "
    "and check again. Then summarise."
)


@dataclass
class AgentResult:
    final_text: str = ""
    steps: int = 0
    mutated: bool = False
    verified: bool = True
    stopped_reason: str = "done"      # done | max_steps | unverified
    checkpoints: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


def _assistant_blocks(content) -> list:
    out = []
    for b in content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


def _tool_result(tool_use_id: str, data: dict, images: list) -> dict:
    content = [{"type": "text", "text": json.dumps(data, default=str)}]
    for png in images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(png).decode()}})
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content,
            "is_error": not data.get("ok", True)}


def run_agent(
    instruction: str,
    ctx: AgentContext,
    client,
    model: str = DEFAULT_MODEL,
    max_steps: int = 40,
    on_event: Optional[Callable] = None,
) -> AgentResult:
    emit = on_event or (lambda *a, **k: None)
    ctx.instruction = instruction
    system = SYSTEM_PROMPT
    recalled = memory.prompt_block(ctx.project_name, ctx.memory_root) if ctx.project_name else ""
    if recalled:
        system += "\n\n" + recalled

    tools = [t.api_spec() for t in TOOLS]
    messages = [{"role": "user", "content": instruction}]
    result = AgentResult()
    nudges = 0

    for _ in range(max_steps):
        result.steps += 1
        response = client.messages.create(
            model=model, max_tokens=4096, system=system, tools=tools, messages=messages,
        )
        usage = getattr(response, "usage", None)
        if usage:
            result.input_tokens += getattr(usage, "input_tokens", 0)
            result.output_tokens += getattr(usage, "output_tokens", 0)

        messages.append({"role": "assistant", "content": _assistant_blocks(response.content)})
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        calls = [b for b in response.content if b.type == "tool_use"]
        if text:
            emit("say", text)

        if not calls:
            if ctx.mutated and not ctx.verified_since_mutation:
                if nudges < MAX_VERIFY_NUDGES:
                    nudges += 1
                    emit("nudge", "Asking the agent to verify its edit before finishing.")
                    messages.append({"role": "user", "content": VERIFY_NUDGE})
                    continue
                result.stopped_reason = "unverified"
            result.final_text = text
            break

        results = []
        for call in calls:
            tool = TOOLS_BY_NAME.get(call.name)
            emit("tool", call.name, call.input)
            if tool is None:
                out_data, images = {"ok": False, "error": f"Unknown tool {call.name!r}."}, []
            else:
                out = execute(tool, ctx, dict(call.input or {}))
                out_data, images = out.data, out.images
            emit("result", call.name, out_data, len(images))
            results.append(_tool_result(call.id, out_data, images))
        messages.append({"role": "user", "content": results})
    else:
        result.stopped_reason = "max_steps"

    result.mutated = ctx.mutated
    result.verified = ctx.verified_since_mutation
    result.checkpoints = [c.name for c in ctx.safety.checkpoints]
    if ctx.mutated and ctx.project_name:
        memory.record_action(ctx.project_name, instruction,
                             (result.final_text or result.stopped_reason)[:300], ctx.memory_root)
    return result
