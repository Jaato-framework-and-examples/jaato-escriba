"""The waterfall, over a journal shaped like a real demonstration.

WHY SYNTHETIC ROWS AND NOT A RECORDED JOURNAL.  The interesting cases
cost money to produce and cannot be produced on demand: a subagent that
outlives the turn that spawned it, a `decide` call with images in it, a
turn that opens in the tier nobody wanted to pay for.  The row SHAPES
here were copied from a journal a real session wrote (2026-10-18
11:15:47), so what is invented is the story and not the format.

THE TWO LEGS THAT CAUGHT REAL DEFECTS, both invisible in that recorded
session because it had one agent and no tools worth timing:

  · a subagent reports no `turn.completed` of its own, so its calls had
    no turn to nest under and were drawn at the top level, beside the
    conversation that commissioned them.
  · the renderer asked `span in turns[agent]`, which compares DICTS BY
    VALUE -- two spans that happened to match would have been confused
    for one another.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sequence as _seq                                  # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label.ljust(52)} got {got!r}, want {want!r}")
        fail += 1
    else:
        print(f"  ok   {label}")


def call(t: int, tool: str, seconds: float, agent: str = "escriba",
         status: str = "ok", **args) -> list:
    """One tool call's pair of rows, as `journal` writes them.

    `tool` and not `name`: `enter_tier`'s own argument is called `name`,
    which this helper would otherwise swallow.
    """
    return [
        {"t": t, "kind": "tool.call_start", "agent_id": agent,
         "tool_name": tool, "call_id": f"c{t}", "tool_class": "other",
         "tool_args": args},
        {"t": t + round(seconds * 1000), "kind": "tool.call_end",
         "agent_id": agent, "tool_name": tool, "call_id": f"c{t}",
         "success": True, "result_status": status,
         "duration_seconds": seconds, "backgrounded": False},
    ]


def turn(t: int, n: int, seconds: float, agent: str = "escriba",
         spend: int = 0, cost: float = 0.0) -> dict:
    """A turn's single row: it arrives at the END, carrying its length."""
    return {"t": t, "kind": "turn.completed", "agent_id": agent,
            "turn_number": n, "duration_seconds": seconds,
            "finish_reason": "tool_use", "completion_gap": None,
            "usage": {"prompt_tokens": 0, "output_tokens": 0,
                      "total_tokens": 0, "spend_total_tokens": spend,
                      "cost_usd": cost}}


ROWS = [
    {"t": 0, "kind": "journal.open", "wall": "2026-10-18T22:31:04.100"},
    {"t": 200, "kind": "session.open", "session_id": "20261018_223104",
     "initial_tier": "voz"},
    {"t": 1000, "kind": "demo.open", "ident": "20261018_223104"},
    {"t": 1500, "kind": "demo.voice", "ident": "x", "ms": 500,
     "held": 0.5, "heard": 0.5},
    {"t": 2000, "kind": "demo.voice", "ident": "x", "ms": 500,
     "held": 1.0, "heard": 1.0},
    {"t": 2500, "kind": "demo.voice", "ident": "x", "ms": 500,
     "held": 1.5, "heard": 1.5},
    {"t": 33000, "kind": "demo.close", "ident": "20261018_223104",
     "frames": 16, "narrated": True},
    # Turn one: it opens in `voz` and spends two seconds there before
    # asking for the tier that can actually see.
    # A REDUNDANT SWITCH FIRST, as a real session produced: the model
    # asks for the tier it is already in, the framework answers
    # `already_at_tier`, and nothing moves.
    *call(34000, "enter_tier", 0.05, status="already_at_tier", name="voz"),
    *call(35000, "enter_tier", 0.1, status="switched", name="escribano"),
    *call(35200, "readFile", 0.5),
    *call(35900, "decide", 5.2, images={"n": 32}, questions={"n": 32}),
    *call(41300, "decide", 3.8, images={"n": 17}, questions={"n": 17}),
    *call(45300, "spawn_subagent", 0.2),
    *call(45600, "signal_completion", 0.1),
    turn(46000, 1, 13.0, spend=22413, cost=0.19),
    # The documenter, which OUTLIVES the turn that spawned it.
    # `parent_agent_id` IS "main", NOT the id every other event uses for
    # the same agent -- measured on a live session, where `turn.completed`
    # and `tool.call_start` both say `escriba`. Resolving one to the
    # other is what keeps a subagent under the turn that spawned it.
    {"t": 45500, "kind": "agent.created", "agent_id": "doc-1",
     "agent_name": "documentalista", "agent_type": "subagent",
     "profile_name": "documentalista", "parent_agent_id": "main",
     "session_id": "20261018_223104"},
    *call(45800, "readFile", 4.1, agent="doc-1"),
    *call(51800, "writeNewFile", 1.2, agent="doc-1"),
    {"t": 53000, "kind": "agent.completed", "agent_id": "doc-1",
     "success": True, "turns_used": 3,
     "token_usage": {"total_tokens": 9004, "prompt_tokens": 9004,
                     "output_tokens": 0}},
    turn(53000, 2, 7.0, spend=4120, cost=0.03),
]

ROWS.sort(key=lambda r: r["t"])
by_label = {}
for span in _seq.spans(ROWS):
    by_label.setdefault(span["label"], []).append(span)


# ------------------------------------------------------------ the spans
check("a turn's start is its end minus what it lasted",
      by_label["turno 1"][0]["t0"], 33000)
check("a tool call is closed by its own call_id",
      by_label["decide"][0]["t1"] - by_label["decide"][0]["t0"], 5200)
check("the demonstration spans open to close",
      (by_label["demo 20261018_223104"][0]["t0"],
       by_label["demo 20261018_223104"][0]["t1"]),
      (1000, 33000))

# THE DERIVED SPAN, and the only one: time in the tier the turn opened
# in. It is the number the whole live-narration design turns on, so it
# is measured rather than argued about.
entry = by_label["en voz"]
check("one entry-tier span per turn that switched", len(entry), 1)
check("and it ends at the first enter_tier",
      (entry[0]["t0"], entry[0]["t1"]), (33000, 35000))
# IT NAMES THE TIER, from the session's own `initial_tier`. "en el tier
# de entrada" was true and useless: a reader cannot tell from it whether
# two seconds went to a model they can hear or one they cannot.
check("and the entry span names which tier that was",
      [s["label"] for s in _seq.spans(
          [r for r in ROWS if r.get("kind") != "session.open"])
       if s["label"].startswith("en ")], ["en el tier inicial"])

# A TIER SWITCH SAYS WHERE IT WENT. Labelled `enter_tier` alone it names
# the act and hides its only interesting part -- whether the session
# went somewhere silent or came back to be heard.
check("enter_tier says which tier it entered",
      "enter_tier → escribano" in by_label, True)
# A CALL THAT CHANGED NOTHING SAYS SO. The model asks for the tier it is
# already in often enough that the profile measured it, and a bar that
# looks like a switch and was not is the drawing lying quietly.
check("and a redundant one says it changed nothing",
      by_label["enter_tier → voz"][0]["detail"], "ya estaba ahí")
# AND THE ENTRY SPAN ENDS AT THE FIRST REAL SWITCH, not at the first
# `enter_tier`: measured on a live session, a turn opened in `voz`,
# asked for `voz`, and the drawing called the two seconds before that
# no-op "antes de cambiar de tier".
check("the entry span ends at the switch, not at the no-op",
      (entry[0]["t0"], entry[0]["t1"]), (33000, 35000))
check("and another tool is still called what it is",
      "readFile" in by_label, True)


# ----------------------------------------------------------- the nesting
def depth(label, which=0):
    return by_label[label][which]["depth"]


check("a turn sits at the top", depth("turno 1"), 0)
check("its calls sit inside it", depth("decide"), 1)
# A SUBAGENT REPORTS NO TURN OF ITS OWN, so its calls have no turn to
# nest under -- they belong to its block, not to the top level.
check("the documenter hangs off the turn that spawned it",
      depth("documentalista"), 1)
check("and its own calls hang off the documenter",
      depth("writeNewFile"), 2)
# AND THE OVERRUN IS DRAWN AS AN OVERRUN. The documenter finishes at
# 53 s, after turn 1 ended at 46 s, so a renderer nesting by "whose
# interval contains whose" would have put turn 2 INSIDE the documenter.
check("a turn after the overrun is still a turn", depth("turno 2"), 0)
# A SUBAGENT STILL WORKING IS NOT ONE THAT TOOK NO TIME. `agent.completed`
# closes the block; tonight none of three arrived, and the documenter drew
# as a 0 ms tick above forty seconds of its own turns.
unfinished = [r for r in ROWS if r.get("kind") != "agent.completed"]
still = [s for s in _seq.spans(unfinished) if s["label"] == "documentalista"]
check("an unfinished subagent runs to the last thing it did",
      still[0]["t1"], 53000)
check("even though the documenter was still working",
      by_label["documentalista"][0]["t1"] > by_label["turno 1"][0]["t1"], True)
check("a subagent's work is marked as a subagent's",
      (by_label["writeNewFile"][0]["sub"], by_label["decide"][0]["sub"]),
      (True, False))


# ------------------------------------------------------------ the drawing
art = _seq.render(ROWS)
lines = art.splitlines()
check("the wall clock heads it", "2026-10-18T22:31:04.100" in lines[0], True)
check("and the total is the span, not the sum", "52,0 s" in lines[0], True)

# THE IMAGE COUNT IS BESIDE THE CALL, which is the fact five live runs
# were spent arguing about.
shown = [ln for ln in lines if "decide" in ln]
check("each decide says how many images it was sent",
      [("images 32" in shown[0]), ("images 17" in shown[1])], [True, True])
# SPEND COMES FROM THE TURN, never from summing context rows.
check("the demonstration's counts are a detail, not a label",
      by_label["demo 20261018_223104"][0]["detail"],
      "16 capturas · 3 piezas de voz")
check("a turn carries what it billed",
      any("22 413 tok" in ln and "0,19 $" in ln for ln in lines), True)
check("and a subagent what it used",
      any("documentalista" in ln and "9 004 tok" in ln for ln in lines), True)
# A CALL TOO SHORT TO FILL A CHARACTER STILL HAPPENED.
# A FAST CALL IS FAST, NOT ZERO. One decimal place printed "0,0 s" for
# both a 31 ms tier switch and a 7 ms memory write, which reads as "no
# time at all" -- measured on a real session, where four rows in a row
# said it.
check("a sub-second span is reported in milliseconds",
      _seq._secs(31), "31 ms")
check("and a longer one in seconds, with a comma", _seq._secs(5200), "5,2 s")
check("a span under one character is a tick, not a blank",
      any(_seq.TICK in ln and "enter_tier" in ln for ln in lines), True)
check("and the drawing carries the destination too",
      any("enter_tier → escribano" in ln for ln in lines), True)
check("a subagent is drawn in its own glyph",
      any(_seq.SUB_FILL in ln for ln in lines), True)
check("an empty journal says so, rather than drawing nothing",
      _seq.render([]), "nothing in this journal yet")

# THE LIST IS A TREE, and the bars carry the time. Ordered purely by
# start, the documenter's `writeNewFile` printed two levels deep
# directly beneath `turno 2` -- an indentation that named the wrong
# parent, which is worse than no indentation at all.
order = [s["label"] for s in _seq._depth_first(_seq.spans(ROWS))]
after = order[order.index("documentalista") + 1:]
check("a parent is followed by what sits inside it",
      after[:2], ["readFile", "writeNewFile"])
check("and a turn is not interrupted by another agent's work",
      order.index("turno 2") > order.index("writeNewFile"), True)

if "--show" in sys.argv:
    print()
    print(art)

# ------------------------------------------------- the shape on the wire
#
# `parent` CANNOT TRAVEL: the spans point at each other, so a span graph
# is a cycle and `json.dumps` would recurse until it gave up. The nesting
# survives as `depth` plus the order, which is all a drawing needs.
import json                                              # noqa: E402

wire = _seq.outline(ROWS)
check("the outline serialises at all", bool(json.dumps(wire)), True)
check("and carries no parent to recurse into",
      [k for s in wire["spans"] for k in s if k == "parent"], [])
check("every span is relative to the first one",
      min(s["t0"] for s in wire["spans"]), 0)
check("the text renderer and the wire agree on the order",
      [s["label"] for s in wire["spans"]],
      [s["label"] for s in _seq._depth_first(_seq.spans(ROWS))])


# ------------------------------------------------- page against server
#
# TWO FILES THAT FAIL SILENTLY WHEN THEY DISAGREE, in the shape
# `tests_demo` guards the demonstration with: the drawer opens, draws
# nothing, and says the conversation had no turns.
import re as _re                                         # noqa: E402

page = (Path(__file__).resolve().parent / "web" / "index.html").read_text(
    encoding="utf-8")
check("the page calls the route the server serves",
      'api("/secuencia")' in page, True)
for field in ("wall", "total", "spans", "depth", "detail", "sub"):
    check(f"the page reads the {field} the outline sends",
          _re.search(rf"\.{field}\b", page) is not None, True)
# IT DRAWS WHAT THE SERVER NESTED and computes nothing about structure:
# a second implementation of the nesting rules in JavaScript would be a
# second set of answers, and the two would disagree exactly when a run
# was interesting enough to open the drawer.
drawn = page[page.index("async function drawSequence"):
             page.index("async function openSequence")]
check("and derives no nesting of its own",
      [w for w in ("parent_agent", "call_id", "turn.completed",
                   "agent.created") if w in drawn], [])

print("sequence OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
