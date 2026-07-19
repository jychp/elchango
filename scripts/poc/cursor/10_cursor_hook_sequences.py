#!/usr/bin/env python3
"""Inspect sanitized Cursor hook sequences without retaining agent content.

Purpose:
  Test whether documented Cursor lifecycle hooks can drive elChango's deck
  state across generations, compaction, and parallel subagents.

Method:
  Read JSON Lines from --input, retain only bounded lifecycle metadata, and
  reduce each conversation to gray, blue, green, or orange/error evidence.
  --self-check runs deterministic synthetic sequences instead.

Safety:
  This POC is read-only. It discards prompts, thoughts, responses, summaries,
  tool data, and all unknown fields. It does not install hooks.

Interpretation:
  A passing self-check proves the reducer's conservative sequence behavior.
  It does not prove that hook conversation IDs equal native Cursor composer
  IDs. That requires the operator's live test.

Limitations:
  Cursor hook delivery is external and may change. Anonymous duplicate
  subagent events cannot be distinguished, so installed hooks should preserve
  subagent_id when Cursor supplies it.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ALLOWED_FIELDS = {
    "hook_event_name",
    "conversation_id",
    "generation_id",
    "status",
    "subagent_id",
}
SUPPORTED_EVENTS = {
    "sessionStart",
    "beforeSubmitPrompt",
    "preCompact",
    "subagentStart",
    "subagentStop",
    "afterAgentThought",
    "afterAgentResponse",
    "stop",
    "sessionEnd",
}


@dataclass
class Turn:
    generation_id: str | None = None
    state: str = "idle"
    detail: str = "session started"
    active_subagents: int = 0
    child_failed: bool = False
    deferred_status: str | None = None


def sanitize(payload: dict[str, Any]) -> dict[str, str]:
    return {
        key: value
        for key, value in payload.items()
        if key in ALLOWED_FIELDS and isinstance(value, str) and value
    }


def terminal(status: str | None) -> tuple[str, str]:
    if status == "completed":
        return "done", "parent completed"
    if status in {"error", "aborted"}:
        return "error", f"parent {status}"
    raise ValueError(f"unsupported stop status: {status!r}")


def apply(turn: Turn, event: dict[str, str]) -> Turn:
    name = event.get("hook_event_name")
    if name not in SUPPORTED_EVENTS:
        raise ValueError(f"unsupported event: {name!r}")
    generation = event.get("generation_id")
    if (
        name != "beforeSubmitPrompt"
        and turn.generation_id
        and generation
        and generation != turn.generation_id
    ):
        return turn

    if name == "sessionStart":
        return Turn(generation_id=generation)
    if name == "beforeSubmitPrompt":
        if turn.generation_id != generation:
            turn = Turn(generation_id=generation)
        turn.deferred_status = None
        turn.state, turn.detail = "working", "prompt submitted"
    elif name in {"preCompact", "afterAgentThought", "afterAgentResponse"}:
        if turn.state in {"done", "error"} and turn.deferred_status is None:
            return turn
        turn.deferred_status = None
        turn.state, turn.detail = "working", name
    elif name == "subagentStart":
        turn.active_subagents += 1
        turn.state, turn.detail = "working", "subagent working"
    elif name == "subagentStop":
        if not turn.active_subagents:
            return turn
        turn.active_subagents -= 1
        turn.child_failed |= event.get("status") in {"error", "aborted"}
        if turn.active_subagents == 0 and turn.deferred_status:
            state, detail = terminal(turn.deferred_status)
            turn.state = "error" if turn.child_failed else state
            turn.detail = "child failed or aborted" if turn.child_failed else detail
            turn.deferred_status = None
        else:
            turn.state, turn.detail = "working", "subagent activity continues"
    elif name == "stop":
        if turn.active_subagents:
            turn.deferred_status = event.get("status")
            turn.state, turn.detail = "working", "waiting for subagents"
        else:
            turn.state, turn.detail = terminal(event.get("status"))
    elif name == "sessionEnd":
        return Turn(generation_id=generation, detail="session ended")
    return turn


def reduce_events(events: Iterable[dict[str, Any]]) -> dict[str, Turn]:
    conversations: dict[str, Turn] = {}
    for raw in events:
        event = sanitize(raw)
        conversation_id = event.get("conversation_id")
        if not conversation_id:
            raise ValueError("event is missing conversation_id")
        conversations[conversation_id] = apply(
            conversations.get(conversation_id, Turn()),
            event,
        )
    return conversations


def self_check() -> None:
    events = [
        {
            "hook_event_name": "beforeSubmitPrompt",
            "conversation_id": "conversation",
            "generation_id": "generation",
            "prompt": "discard me",
        },
        {
            "hook_event_name": "preCompact",
            "conversation_id": "conversation",
            "generation_id": "generation",
            "summary": "discard me",
        },
        {
            "hook_event_name": "subagentStart",
            "conversation_id": "conversation",
            "generation_id": "generation",
            "subagent_id": "one",
        },
        {
            "hook_event_name": "stop",
            "conversation_id": "conversation",
            "generation_id": "generation",
            "status": "completed",
        },
        {
            "hook_event_name": "subagentStop",
            "conversation_id": "conversation",
            "generation_id": "generation",
            "status": "completed",
        },
    ]
    reduced = reduce_events(events)["conversation"]
    assert reduced.state == "done"
    assert all("prompt" not in sanitize(event) for event in events)
    print("PASS: sanitized generation, compaction, subagent, and terminal sequence")
    print("VERDICT: REDUCER_SUPPORTED_LIVE_ID_CORRELATION_UNPROVEN")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-check", action="store_true")
    group.add_argument("--input", type=Path, help="Read-only JSON Lines input")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    assert args.input is not None
    with args.input.open(encoding="utf-8") as source:
        result = reduce_events(json.loads(line) for line in source if line.strip())
    output = {
        conversation_id: {
            "generation_id": turn.generation_id,
            "state": turn.state,
            "detail": turn.detail,
            "active_subagents": turn.active_subagents,
        }
        for conversation_id, turn in result.items()
    }
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        for conversation_id, state in output.items():
            print(f"{conversation_id}: {state['state']} ({state['detail']})")
        print("VERDICT: OBSERVATIONS_REDUCED_ID_CORRELATION_REQUIRES_LIVE_TEST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
