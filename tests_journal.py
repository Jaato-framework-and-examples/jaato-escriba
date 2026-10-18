"""The sequence: one clock, named fields, and shapes instead of content.

WHAT THIS SUITE IS FOR.  The journal is read by a renderer and by nobody
else, so every defect in it is silent: a field that stopped arriving, a
clock that went backwards, a document that leaked into a developer's
file.  None of those shows up as a failure anywhere — the drawing just
says something untrue, convincingly.

THE LEG THAT MATTERS MOST is `tool_args`.  A journal that recorded values
would hold the whole of every document the documenter writes, and nothing
about the file would look wrong.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import journal as _journal                               # noqa: E402
from jaato_sdk import EventType                          # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label.ljust(52)} got {got!r}, want {want!r}")
        fail += 1
    else:
        print(f"  ok   {label}")


# ------------------------------------------------------------- the shape
#
# A STRING IS ITS LENGTH, which is the whole privacy property: the
# arguments of `writeNewFile` are a document, and the arguments of
# `store_memory` are the person talking about themselves.
check("a string is its length", _journal._shape("procedimiento"), {"chars": 13})
check("a list is its length", _journal._shape([1, 2, 3]), {"n": 3})
check("a number is itself", _journal._shape(32), 32)
check("a bool stays a bool", _journal._shape(True), True)
check("nothing stays nothing", _journal._shape(None), None)
# THE NUMBER THIS WAS BUILT FOR.  Five runs were spent arguing about
# whether `decide` was sent any images; it is one hop into the arguments.
check("a decide call keeps its image COUNT and no path",
      _journal._shape({"model": "vista", "state": "x" * 400,
                       "questions": [1] * 8,
                       "images": [{"name": "a", "path": "demo/1/0001.png"}] * 32}),
      {"images": {"n": 32}, "model": {"chars": 5},
       "questions": {"n": 8}, "state": {"chars": 400}})
# AND A DOCUMENT CANNOT GET IN, however deep it is nested.
deep = _journal._shape({"path": "docs/x.md", "content": "secreto " * 500})
check("a document is a length, not a document", deep["content"], {"chars": 4000})
check("and nothing in the row is the text",
      "secreto" in json.dumps(deep), False)


# -------------------------------------------------------- what is WATCHED
#
# NAMED, EVENT BY EVENT, so a field the framework adds later is not
# recorded by accident.  These two are the ones a renderer cannot do
# without, and `parent_agent_id` is the only thing that separates the
# documenter's lane from the scribe's.
check("tool calls carry their arguments' shape",
      "tool_args" in _journal.WATCHED[EventType.TOOL_CALL_START], True)
check("and their own duration, given rather than computed",
      "duration_seconds" in _journal.WATCHED[EventType.TOOL_CALL_END], True)
check("a subagent is tied to the turn that spawned it",
      "parent_agent_id" in _journal.WATCHED[EventType.AGENT_CREATED], True)
# THE MODEL'S AND THE PERSON'S WORDS ARE ON NO LIST.  Asserted against
# every watched event at once, because the cost of one slipping in is a
# second copy of somebody's conversation.
WORDS = ("formatted_text", "payload", "function_calls", "prompt_lines",
         "error_message", "comment", "error_summary", "warnings")
leaked = sorted({f for fields in _journal.WATCHED.values()
                 for f in fields if f in WORDS})
check("no watched event records anybody's words", leaked, [])


# --------------------------------------------------------------- the file
class _Client:
    """Something to subscribe to, that hands back the way out again."""

    def __init__(self) -> None:
        self.subs: dict = {}
        self.dropped: list = []

    def subscribe(self, event, callback):
        self.subs[event] = callback
        return lambda: self.dropped.append(event)


class _Usage:
    """Stands in for the framework's `UsageBreakdown`: a model of numbers."""

    def model_dump(self) -> dict:
        return {"prompt_tokens": 14512, "output_tokens": 311,
                "total_tokens": 14823, "spend_total_tokens": 21044,
                "cost_usd": 0.11}


class _Start:
    kind = "x"

    def __init__(self) -> None:
        self.agent_id = "escriba"
        self.tool_name = "decide"
        self.call_id = "call_1"
        self.tool_class = "decision"
        self.tool_args = {"model": "vista", "images": [{"path": "p"}] * 32}


class _Turn:
    def __init__(self) -> None:
        self.agent_id = "escriba"
        self.turn_number = 2
        self.duration_seconds = 13.04
        self.finish_reason = "stop"
        self.completion_gap = None
        self.usage = _Usage()


with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    j = _journal.Journal(d)
    check("the journal lands beside the manifest", j.path.name, "journal.jsonl")

    rows = j.rows()
    check("it opens with one row", len(rows), 1)
    check("and that row is where the wall clock is",
          "wall" in rows[0] and rows[0]["kind"], "journal.open")

    client = _Client()
    off = j.watch(client)
    check("every watched event is subscribed",
          len(client.subs), len(_journal.WATCHED))

    client.subs[EventType.TOOL_CALL_START](_Start())
    client.subs[EventType.TURN_COMPLETED](_Turn())
    j.note("demo.open", ident="20261018_223104")

    rows = j.rows()
    check("every row is appended in order",
          [r["kind"] for r in rows[1:]],
          ["tool.call_start", "turn.completed", "demo.open"])

    call = rows[1]
    check("the tool call keeps its name", call["tool_name"], "decide")
    check("and the image count, through the shape",
          call["tool_args"]["images"], {"n": 32})
    check("and no path from it", "demo" in json.dumps(call["tool_args"]), False)
    # A FIELD THE EVENT DOES NOT CARRY IS SIMPLY ABSENT, rather than
    # None: `hasattr` decides, so a framework that renames one leaves a
    # row with a gap instead of a row full of nulls.
    check("a field the event lacks is absent, not null",
          "result_status" in call, False)

    turn = rows[2]
    check("usage goes in whole, because it is all numbers",
          turn["usage"]["spend_total_tokens"], 21044)
    check("and the duration the framework measured", turn["duration_seconds"],
          13.04)

    # ONE CLOCK, MONOTONIC: `t` never goes backwards, and it is the only
    # time on a row. A wall stamp per row would be a second clock
    # inviting somebody to subtract two of them.
    stamps = [r["t"] for r in rows]
    check("the axis never goes backwards", stamps, sorted(stamps))
    check("and no row carries a second clock",
          [r for r in rows[1:] if "wall" in r], [])

    off()
    check("and the way out drops every subscription",
          len(client.dropped), len(_journal.WATCHED))

    # A TORN LAST LINE COSTS ITSELF AND NOTHING BEFORE IT, which is the
    # shape a power cut leaves: the write that was in flight.  It is the
    # LAST line by construction -- a write that did not finish raised,
    # so this process appended nothing after it -- and that is why
    # `rows()` skipping a bad line is enough and no marker is needed.
    before = [r["kind"] for r in j.rows()]
    with j.path.open("a", encoding="utf-8") as fh:
        fh.write('{"t": 1, "kind": "tru')
    check("a torn last line costs itself", [r["kind"] for r in j.rows()],
          before)


print("journal OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
