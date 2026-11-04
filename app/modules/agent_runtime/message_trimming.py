"""Bound the context a checkpointed conversation carries (task T02, R-06).

Adapted from als-computing/forge_rsoxs_analysis src/forge/agents/message_trimming.py (BSD-3-Clause).
Copyright (c) 2026, The Regents of the University of California, through Lawrence
Berkeley National Laboratory. See the FORGE LICENSE for the full notice.

Kept from the donor: media blocks survive only in the current turn and are
replaced by a placeholder that names what was dropped; stale tool output is
stubbed newest-kept-first under a character budget; the current turn is never
trimmed; tool calls left without a result (a cancelled turn) are repaired; every
rewrite keeps the original message ``id`` so LangGraph's ``add_messages`` replaces
it in place. Trimming never deletes a message, so tool-call/tool-result pairs stay
valid.

Changed for F2W:

- A stubbed tool result keeps its evidence IDs and artifact references (read from
  the runtime's JSON tool-message content), and the ``ToolMessage.artifact`` stays
  untouched. Trimming bounds *model context*; archival evidence is never removed.
- Tool results whose call no longer exists (orphaned results) are dropped from the
  model view and removed from state with ``RemoveMessage``, so a provider never
  receives a result without its call.
- The orphan marker key is F2W-specific.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage

STALE_TOOL_RESULT_BUDGET_CHARS = 120_000
ORPHAN_REPAIR_MARKER = "f2w_orphan_repair"
_MIN_STUB_WORTH_IT = 400
_ORPHAN_ARGS_CAP_CHARS = 160
_MAX_KEPT_REFS = 50

_RESTORE_HINT = (
    "no longer in context -- the content was dropped after the turn it was attached to. Do not answer "
    "from your earlier description as if you could still see it; say it is no longer visible and ask "
    "for it to be re-attached."
)


def media_placeholder(block: dict[str, Any]) -> str:
    """Render one dropped media block as text naming it and how to restore it."""
    kind = "image" if block.get("type") == "image" else "document"
    if "base64" in block:
        what = f"{kind} {block.get('mime_type', 'unknown media type')}, {len(block['base64'])} base64 chars"
    else:
        what = f"{kind} url: {block.get('url', 'unknown')}"
    return f"[{what} -- {_RESTORE_HINT}]"


def _is_media_block(block: object) -> bool:
    if not isinstance(block, dict) or block.get("type") == "text":
        return False
    return "base64" in block or "url" in block


def strip_media_from_content(content: object) -> Any:
    """Replace media blocks with placeholders; returns ``content`` itself when unchanged."""
    if not isinstance(content, list) or not any(_is_media_block(b) for b in content):
        return content
    parts: list[str] = []
    for block in content:
        if _is_media_block(block):
            parts.append(media_placeholder(block))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict):
            parts.append(f"[{block.get('type')} block]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


def current_turn_start(messages: Sequence[BaseMessage]) -> int:
    """Index of the last human message (start of the current turn), or 0."""
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", None) == "human":
            return index
    return 0


def trim_stale_media(messages: Sequence[BaseMessage]) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Drop media from every message before the current turn.

    Returns:
        ``(for_model, replacements)``; replacements keep original ids and omit
        id-less messages (trimmed for the model only).
    """
    boundary = current_turn_start(messages)
    for_model: list[BaseMessage] = list(messages)
    replacements: list[BaseMessage] = []
    for index in range(boundary):
        message = messages[index]
        trimmed = strip_media_from_content(message.content)
        if trimmed is message.content:
            continue
        replacement = message.model_copy(update={"content": trimmed})
        for_model[index] = replacement
        if getattr(replacement, "id", None) is not None:
            replacements.append(replacement)
    return for_model, replacements


def _kept_references(content: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    kept = {}
    for key in ("evidence_ids", "artifact_refs"):
        values = payload.get(key)
        if isinstance(values, list) and values:
            kept[key] = [str(v) for v in values[:_MAX_KEPT_REFS]]
    return kept


def _tool_result_stub(message: BaseMessage, content: str) -> str:
    name = getattr(message, "name", None) or "a tool"
    stub: dict[str, Any] = {
        "trimmed": True,
        "note": (f"{name}'s output from an earlier turn was dropped to fit the context window "
                 f"({len(content):,} characters). Call it again if you still need it."),
    }
    stub.update(_kept_references(content))
    return json.dumps(stub, sort_keys=True)


def trim_stale_tool_results(
    messages: Sequence[BaseMessage], *, budget_chars: int = STALE_TOOL_RESULT_BUDGET_CHARS,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Stub stale tool output beyond ``budget_chars``, newest kept first; current turn untouched.

    Stubs keep evidence IDs and artifact refs; ``ToolMessage.artifact`` is preserved.
    """
    boundary = current_turn_start(messages)
    for_model: list[BaseMessage] = list(messages)
    replacements: list[BaseMessage] = []
    spent = 0
    for index in range(boundary - 1, -1, -1):
        message = messages[index]
        if getattr(message, "type", None) != "tool" or not isinstance(message.content, str):
            continue
        size = len(message.content)
        if spent + size <= budget_chars or size < _MIN_STUB_WORTH_IT:
            spent += size
            continue
        replacement = message.model_copy(update={"content": _tool_result_stub(message, message.content)})
        for_model[index] = replacement
        if getattr(replacement, "id", None) is not None:
            replacements.append(replacement)
    return for_model, replacements


def is_orphan_repair(message: object) -> bool:
    """Whether ``message`` is a repaired earlier message rather than a new one."""
    return bool((getattr(message, "response_metadata", None) or {}).get(ORPHAN_REPAIR_MARKER))


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b if isinstance(b, str) else str(b.get("text", "")) for b in content
             if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text")]
    return "\n".join(p for p in parts if p)


def _describe_call(call: Mapping[str, Any]) -> str:
    args = json.dumps(call.get("args", {}), sort_keys=True, default=str)
    if len(args) > _ORPHAN_ARGS_CAP_CHARS:
        args = args[: _ORPHAN_ARGS_CAP_CHARS - 3] + "..."
    return f"{call.get('name', 'a tool')}({args})"


def repair_orphaned_tool_calls(
    messages: Sequence[BaseMessage], *, keep_pending_ids: Iterable[str] = (),
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Rewrite every AI tool call that never got a result so the model can be called.

    The rebuilt message keeps its id and text and every answered call; unanswered
    calls are removed and named in a note. Calls in ``keep_pending_ids`` are left
    alone (for example, calls waiting on an approval).
    """
    answered = {getattr(m, "tool_call_id", None) for m in messages if getattr(m, "type", None) == "tool"}
    pending = set(keep_pending_ids)
    for_model: list[BaseMessage] = list(messages)
    replacements: list[BaseMessage] = []
    for index, message in enumerate(messages):
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        missing = [c for c in message.tool_calls if c.get("id") not in answered and c.get("id") not in pending]
        if not missing:
            continue
        kept = [c for c in message.tool_calls if c.get("id") in answered or c.get("id") in pending]
        text = _text_of(message.content)
        note = ("[Cancelled before it returned: " + ", ".join(_describe_call(c) for c in missing)
                + ". No result exists; call it again if it is still needed.]")
        replacement = AIMessage(id=message.id, name=message.name, content=f"{text}\n\n{note}" if text else note,
                                tool_calls=kept, response_metadata={ORPHAN_REPAIR_MARKER: True})
        for_model[index] = replacement
        if message.id is not None:
            replacements.append(replacement)
    return for_model, replacements


def drop_orphaned_tool_results(
    messages: Sequence[BaseMessage],
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Remove tool results whose originating call is absent.

    Returns:
        ``(for_model, removals)``: the conversation without orphaned results and
        ``RemoveMessage`` entries for those that have an id.
    """
    called = {c.get("id") for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])}
    for_model: list[BaseMessage] = []
    removals: list[BaseMessage] = []
    for message in messages:
        if getattr(message, "type", None) == "tool" and getattr(message, "tool_call_id", None) not in called:
            if message.id is not None:
                removals.append(RemoveMessage(id=message.id))
            continue
        for_model.append(message)
    return for_model, removals


def count_media_blocks(messages: Iterable[BaseMessage]) -> int:
    """Count raw media blocks across a conversation."""
    return sum(
        sum(1 for b in m.content if _is_media_block(b))
        for m in messages if isinstance(getattr(m, "content", None), list)
    )


def prepare_for_model(
    messages: Sequence[BaseMessage], *, budget_chars: int = STALE_TOOL_RESULT_BUDGET_CHARS,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Apply every repair and trim in the donor's order; returns ``(for_model, state_updates)``."""
    for_model, updates = repair_orphaned_tool_calls(messages)
    for_model, removals = drop_orphaned_tool_results(for_model)
    updates.extend(removals)
    for_model, media = trim_stale_media(for_model)
    for_model, text = trim_stale_tool_results(for_model, budget_chars=budget_chars)
    by_id: dict[Any, BaseMessage] = {}
    for message in [*updates, *media, *text]:
        by_id[message.id] = message
    return for_model, list(by_id.values())
