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


def call(t: int, name: str, seconds: float, agent: str = "escriba",
         **args) -> list:
    """One tool call's pair of rows, as `journal` writes them."""
    return [
        {"t": t, "kind": "tool.call_start", "agent_id": agent,
         "tool_name": name, "call_id": f"c{t}", "tool_class": "other",
         "tool_args": args},
        {"t": t + round(seconds * 1000), "kind": "tool.call_end",
         "agent_id": agent, "tool_name": name, "call_id": f"c{t}",
         "success": True, "result_status": "ok",
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
    {"t": 200, "kind": "session.open", "session_id": "20261018_223104"},
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
    *call(35000, "enter_tier", 0.1),
    *call(35200, "readFile", 0.5),
    *call(35900, "decide", 5.2, images={"n": 32}, questions={"n": 32}),
    *call(41300, "decide", 3.8, images={"n": 17}, questions={"n": 17}),
    *call(45300, "spawn_subagent", 0.2),
    *call(45600, "signal_completion", 0.1),
    turn(46000, 1, 13.0, spend=22413, cost=0.19),
    # The documenter, which OUTLIVES the turn that spawned it.
    {"t": 45500, "kind": "agent.created", "agent_id": "doc-1",
     "agent_name": "documentalista", "agent_type": "subagent",
     "profile_name": "documentalista", "parent_agent_id": "escriba",
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
      (by_label["demo 20261018_223104 · 3 piezas de voz"][0]["t0"],
       by_label["demo 20261018_223104 · 3 piezas de voz"][0]["t1"]),
      (1000, 33000))

# THE DERIVED SPAN, and the only one: time in the tier the turn opened
# in. It is the number the whole live-narration design turns on, so it
# is measured rather than argued about.
entry = by_label["en el tier de entrada"]
check("one entry-tier span per turn that switched", len(entry), 1)
check("and it ends at the first enter_tier",
      (entry[0]["t0"], entry[0]["t1"]), (33000, 35000))


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
check("a turn carries what it billed",
      any("22 413 tok" in ln and "0,19 $" in ln for ln in lines), True)
check("and a subagent what it used",
      any("documentalista" in ln and "9 004 tok" in ln for ln in lines), True)
# A CALL TOO SHORT TO FILL A CHARACTER STILL HAPPENED.
check("a span under one character is a tick, not a blank",
      any(_seq.TICK in ln and "enter_tier" in ln for ln in lines), True)
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

print("sequence OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
