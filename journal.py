"""What happened, at every layer, with one clock over all of it.

The sequencer needs to put a frame landing in the browser, a tier change,
a `decide` call and a document being written on ONE axis.  Nothing in the
repo could do that: `manifest.jsonl` is the audit record (per TURN, stamped
to the second), the board is the person's transcript, and of the 172 typed
events the framework emits this client subscribed to exactly two.  So this
is the missing substrate -- one append-only file per conversation, and
every drawing is a renderer over it.

WHY NOT IN `manifest.jsonl`.  That file is evidence: `archive` says "a
manifest that can be rewritten is not evidence", its rows are rendered
into the person's own transcript (`driver._turn_entries`), it is
deliberately outside everything `--forget` erases, and its size is charged
against the person's audio ceiling.  Hundreds of millisecond-stamped
developer rows per demonstration would bury the rows an auditor asks for
and could evict somebody's recordings to make room for an instrument.  It
sits in the same DIRECTORY -- co-located with the conversation it
describes, outside `.jaato/` for `archive`'s reasons -- and in its own
file.

ONE CLOCK, AND IT IS THE DRIVER'S.  There are three in play: the page's
`at_ms` (its own `performance.now()` origin), this process's wall clock,
and the daemon's event stamps.  Every row here carries `t`, milliseconds
since this journal opened, taken from `time.monotonic()` -- so the axis
cannot be bent by the host's clock being set, and the single
`journal.open` row carries the wall time that `t` is an offset from.  The
page's series stays SEPARATE and declared rather than being pretended
into this one: a demonstration's frames are already on disk in
`frames.jsonl` with their own `at_ms`, so the renderer joins them to the
`demo.open` row instead of this file holding a second copy of them.

SHAPES, NOT VALUES, and the reason is not tidiness.  `TOOL_CALL_START`
carries `tool_args`, so a journal that recorded values would hold the
whole of every document the documenter writes -- a second copy of the
person's work, in a file written for a developer's convenience.  So
`tool_args` is descended into as a shape: a string becomes its length, a
list becomes its length, and the one number this was built to make
visible -- `images: {"n": 32}` on a `decide` call -- survives.  Five runs
were spent arguing about that number; it would have been in row one.

WHAT IS KEPT IS NAMED, event by event, in `WATCHED`.  Not every field of
every event: `formatted_text`, `payload`, `function_calls`,
`prompt_lines` and `error_message` are the model's and the person's
words, and none of them is on the list.  A field the framework adds later
is simply not recorded, which is safe rather than broken.

READING THE NUMBERS.  `usage` is the framework's own `UsageBreakdown`,
recorded whole because every field of it is a number.  Its docstring
carries a trap worth repeating here, because this file is what a renderer
will read: `total_tokens` is the LAST response's total, which is the
end-of-turn CONTEXT SIZE, while `spend_total_tokens` is what was BILLED
across the turn.  A turn with a tool call has two or more billed
responses, so a renderer summing `total_tokens` undercounts spend --
measured by the framework at 59% of actual.  Sum the `spend_*` fields.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from jaato_sdk import EventType

#: Beside `manifest.jsonl`, in the conversation's own archive directory.
NAME = "journal.jsonl"

#: The fields descended into as a shape instead of recorded.  Both of the
#: events carrying one are tool calls, and a tool's arguments are where
#: the content is.
SHAPED = ("tool_args",)

#: The tools whose arguments are NAMES, kept verbatim rather than shaped.
#:
#: THE ONE EXCEPTION, AND WHY IT IS SAFE.  `enter_tier` takes the name of
#: a tier -- `voz`, `escribano` -- and a tier name is not content: the
#: profile publishes it, the framework renders it into the tool's own
#: schema for the model to read, and the page already shows all of them
#: to the person in the engines panel.  Shaped, it became `{"chars": 9}`,
#: which is how a drawing ended up with a bar labelled `enter_tier` and
#: no way to tell which tier was entered -- the single most useful fact
#: about that call.
#:
#: KEPT WHOLE RATHER THAN BY KEY, deliberately: the argument's NAME is
#: the framework's and could be `tier` or `tier_name`, and a journal
#: that guessed wrong would record nothing while looking correct.  What
#: this module knows is which TOOL's arguments are safe, which is a
#: property of the tool and not of the framework's spelling.
ARGS_ARE_NAMES = ("enter_tier",)

#: What is kept from each event this watches, field by field.
#:
#: THIRTEEN EVENTS AND NOT ALL 172.  "Subscribe to everything and see
#: what arrives" is the right PROBE and the wrong instrument: it would
#: record the memory plugin's list payloads and the permission flow's
#: prompt lines, which are the person's own material, to draw lanes that
#: need none of it.  Each one here earns its place:
#:
#:   tool calls      the spine -- every bar on every drawing, with
#:                   `duration_seconds` given rather than computed, and
#:                   `enter_tier` among them, which is how the TIER lane
#:                   exists at all: the framework emits no tier event.
#:   agent lifecycle `parent_agent_id` is what separates the documenter's
#:                   lane from the scribe's and ties it to the turn that
#:                   spawned it -- a subagent's calls arrive on this same
#:                   client and are otherwise indistinguishable.
#:   turn + context  where the time and the tokens went.
#:   budget          `tier_changes` is a SECOND tier signal, and the only
#:                   one that says a ladder moved under pressure.
#:   incidents       a provider refusal that a turn survives leaves no
#:                   other trace a renderer can reach.
#:   permission      `decide` is not auto-approved, so under a deny
#:                   default a missing grant IS the explanation for a
#:                   selection that never happened.
WATCHED: Dict[Any, Tuple[str, ...]] = {
    EventType.TOOL_CALL_START: (
        "agent_id", "tool_name", "call_id", "tool_class", "tool_args"),
    EventType.TOOL_CALL_END: (
        "agent_id", "tool_name", "call_id", "success", "result_status",
        "duration_seconds", "backgrounded"),
    EventType.AGENT_CREATED: (
        "agent_id", "agent_name", "agent_type", "profile_name",
        "parent_agent_id", "session_id"),
    EventType.AGENT_COMPLETED: (
        "agent_id", "success", "token_usage", "turns_used"),
    EventType.AGENT_STATUS_CHANGED: ("agent_id", "status"),
    EventType.AGENT_ERROR: (
        "agent_id", "error_type", "classification", "attempt",
        "framework_retries_exhausted"),
    EventType.TURN_PROGRESS: (
        "agent_id", "percent_used", "tokens_remaining", "pending_tool_calls"),
    EventType.TURN_COMPLETED: (
        "agent_id", "turn_number", "duration_seconds", "finish_reason",
        "completion_gap", "usage"),
    EventType.CONTEXT_UPDATED: (
        "agent_id", "percent_used", "tokens_remaining", "turns", "source",
        "usage"),
    EventType.BUDGET_RUNG_FIRED: (
        "at_percent", "action", "origin", "pressure", "driving_dimension",
        "tier_changes"),
    EventType.INCIDENT_RAISED: (
        "kind", "cause", "site", "provider", "model", "tier"),
    EventType.PERMISSION_REQUESTED: (
        "agent_id", "request_id", "tool_name", "tool_class", "tool_args"),
    EventType.PERMISSION_RESOLVED: (
        "agent_id", "request_id", "tool_name", "granted", "method"),
}


def _shape(value: Any) -> Any:
    """What a value LOOKS like, never what it says.

    TOTAL BY CONSTRUCTION: every branch returns, and the last one answers
    with a type name rather than the object -- so what reaches
    `json.dumps` is always serialisable and never a value nobody meant to
    write down.  That is why nothing here is wrapped in a `try`: the
    failure it would be hiding cannot be reached.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return {"chars": len(value)}
    if isinstance(value, (list, tuple, set)):
        return {"n": len(value)}
    if isinstance(value, dict):
        return {str(k): _shape(v) for k, v in sorted(value.items(), key=str)}
    return {"type": type(value).__name__}


def _value(name: str, value: Any, tool: Any = None) -> Any:
    """One field, as it goes into the row.

    Three kinds, and the field's NAME decides which: an argument bag is a
    shape; the framework's own `UsageBreakdown` is a model of numbers and
    goes in whole; everything else on `WATCHED`'s lists was chosen BY
    NAME as an identifier or a measurement, so it travels as it is.

    `tool` is the call's own name where there is one, and it is read for
    exactly one purpose: `ARGS_ARE_NAMES` above.
    """
    if name in SHAPED:
        if tool in ARGS_ARE_NAMES and isinstance(value, dict):
            return dict(value)
        return _shape(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump()
    return value


class Journal:
    """One conversation's sequence, appended to as it happens."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / NAME
        self._origin = time.monotonic()
        self.note("journal.open",
                  wall=datetime.now().isoformat(timespec="milliseconds"))

    def note(self, kind: str, **fields: Any) -> None:
        """Append one row.

        `t` IS THE AXIS: milliseconds since this journal opened, from
        `time.monotonic()`, so two rows can be compared even if the
        host's wall clock moved between them.  The wall time it counts
        from is in the `journal.open` row and nowhere else, because a
        wall stamp per row would be a second clock inviting somebody to
        subtract two of them.
        """
        row = {"t": round((time.monotonic() - self._origin) * 1000),
               "kind": kind, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def watch(self, client: Any) -> Callable[[], None]:
        """Record every watched event from `client`; answer the way back.

        ONE SUBSCRIPTION PER EVENT, and the unsubscribes are returned
        together: a session that is dropped and reopened (`Person._resume`)
        gets a new client, and a journal still listening to the old one
        would be recording a conversation nobody is having.
        """
        offs: List[Callable[[], None]] = []
        for event, names in WATCHED.items():
            offs.append(client.subscribe(
                event,
                lambda ev, e=event, n=names: self._event(e, n, ev)))

        def off() -> None:
            for one in offs:
                if callable(one):
                    one()
        return off

    def _event(self, event: Any, names: Tuple[str, ...], ev: Any) -> None:
        tool = getattr(ev, "tool_name", None)
        fields = {name: _value(name, getattr(ev, name), tool)
                  for name in names if hasattr(ev, name)}
        self.note(event.value, **fields)

    def rows(self) -> List[Dict[str, Any]]:
        """Everything appended so far, in order.

        For a renderer and for the tests.  A line that will not parse is
        SKIPPED rather than fatal, the same bargain `demo.frames` makes
        for the same reason: the write that was in flight at a power cut
        should cost its own row and not the whole sequence.  That torn
        line is the LAST one by construction -- a write that did not
        finish raised, so this process appended nothing after it -- which
        is why skipping is enough and no record marker is needed.
        """
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
