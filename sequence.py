"""The journal, read as a waterfall: what took the time, and what it cost.

`journal` writes rows; this is the first renderer over them, and the one
an architecture decision actually needs — nested spans with durations
beside them, so "where did twenty seconds go" is answered by looking
rather than by arguing.  A live view is the same data drawn differently
and is deliberately not this.

    python -m sequence <workspace>/audio/<stamp>/journal.jsonl

WHAT A SPAN IS MADE OF, and why none of it is guessed:

    tool call   `tool.call_start` opens it; `tool.call_end` closes it and
                carries `duration_seconds`, which the FRAMEWORK measured.
                Paired by `call_id`, never by proximity.
    turn        `turn.completed` carries `duration_seconds` and arrives at
                the END, so the start is the end minus the duration. That
                is arithmetic on one row, not a guess about two.
    subagent    `agent.created` opens it and `agent.completed` closes it.
                Its calls arrive on the SAME client as the parent's, so
                `agent_id` is the only thing that separates them and
                `parent_agent_id` is the only thing that connects them.
    demo        `demo.open` to `demo.close`, with the narration's extent
                from the `demo.voice` rows between them.

NESTING IS BY THE AGENT CHAIN, NOT BY CONTAINMENT, and the difference is
load-bearing.  A subagent outlives the turn that spawned it — the
documenter is still writing while the scribe speaks — so a stack that
nested purely by "whose interval contains whose" would put the scribe's
SECOND turn inside the documenter.  So a call nests under a turn of its
OWN agent, an agent nests under the turn that created it, and an overrun
is drawn as the overrun it is.

WHAT IS NOT DERIVED.  There is no "tier" span: `enter_tier` is a tool
call like any other and is drawn as one.  What IS derived is the one
interval that decides the open design question — the time inside a turn
BEFORE its first `enter_tier`, which is time spent in the tier the turn
opened in.  It is labelled as that measurement and not as a verdict.

READING THE COST.  Spend comes from `turn.completed`, never from summing
`context.updated`: measured on a real session, the provider-sourced
context rows carry `spend_total_tokens: null`, and the framework's own
docstring puts the undercount from summing `total_tokens` at 59%.  For
the same reason `percent_used` is not drawn at all here: at one instant a
provider row said 10.20% and a turn row 23.25%, because they measure
different things, and a waterfall has nowhere honest to put a number
that needs a sentence of explanation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

#: How wide the bars are drawn, in characters.
BAR = 44

#: How wide a label column is before it is truncated.
LABEL = 38

#: Drawn for a span with a model or a tool behind it, and for one that
#: belongs to a subagent — two glyphs, because "who was working" is the
#: question a reader asks first and colour is not available here.
FILL = "█"
SUB_FILL = "░"

#: A span too short to fill one character still happened, and a blank
#: line would say it did not.
TICK = "▪"


def _secs(ms: float) -> str:
    """How long something took, in the unit that can actually say it.

    MILLISECONDS UNDER A SECOND, because one decimal place cannot: a
    44 ms `enter_tier` and a 4 ms one both printed "0,0 s", which reads
    as "no time at all" rather than as "fast" — and the difference
    between those two is the whole reason a tier switch gets a bar.

    Spanish decimal comma above a second, like everything else the
    person reads. The page formats the same numbers the same way; the
    rule is short enough that one copy each is cheaper than putting
    presentation on the wire.
    """
    if ms < 1000:
        return f"{round(ms)} ms"
    return f"{ms / 1000:.1f}".replace(".", ",") + " s"


def _thousands(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _detail(row: Dict[str, Any]) -> str:
    """The one fact worth putting beside a span, from the row that made it.

    THE IMAGE COUNT IS HERE BECAUSE OF WHAT IT COST: five runs were spent
    arguing about whether `decide` was sent any images, and the answer
    was one hop inside `tool_args` all along.
    """
    args = row.get("tool_args") or {}
    images = args.get("images")
    if isinstance(images, dict) and "n" in images:
        return f"images {images['n']}"
    usage = row.get("usage") or {}
    spent = usage.get("spend_total_tokens")
    cost = usage.get("cost_usd")
    bits = []
    if isinstance(spent, int):
        bits.append(f"{_thousands(spent)} tok")
    if isinstance(cost, (int, float)):
        bits.append(f"{cost:.2f}".replace(".", ",") + " $")
    if bits:
        return " · ".join(bits)
    tokens = (row.get("token_usage") or {}).get("total_tokens")
    if isinstance(tokens, int):
        return f"{_thousands(tokens)} tok"
    if row.get("kind") == "demo.close":
        return f"{row.get('frames')} capturas"
    return ""


def _called(row: Dict[str, Any]) -> str:
    """What to call a tool call, which for `enter_tier` is where it went.

    A BAR LABELLED `enter_tier` AND NOTHING ELSE names the act and hides
    its only interesting part: a reader cannot tell whether the session
    went somewhere silent or came back to be heard, which is the whole
    question a tier switch raises. The destination is in the call's own
    arguments, kept whole by `journal.ARGS_ARE_NAMES`.

    READ BY VALUE AND NOT BY KEY, for that module's reason: the
    argument's name belongs to the framework and this does not need to
    know it.
    """
    name = row.get("tool_name") or "?"
    if name != "enter_tier":
        return name
    where = [v for v in (row.get("tool_args") or {}).values()
             if isinstance(v, str) and v]
    return f"{name} → {where[0]}" if where else name


def spans(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every span the journal implies, each with its parent already chosen.

    Returned in the order they STARTED, with ties broken by the longer
    span first, so a turn is listed before the calls inside it.
    """
    out: List[Dict[str, Any]] = []
    #: Every tier change that ACTUALLY HAPPENED, in order: (when, where).
    #: Read from `result_status`, which the framework sets to `switched`
    #: or `already_at_tier` -- so a call that asked for the tier it was
    #: already in is known to be a no-op rather than assumed to be one.
    switches: List[Tuple[int, str]] = []
    #: `call_id` -> the span it opened, so its end can close it.
    open_calls: Dict[str, Dict[str, Any]] = {}
    #: `agent_id` -> the span of that agent's life, for the subagents.
    agents: Dict[str, Dict[str, Any]] = {}
    #: `agent_id` -> that agent's turn spans, for a call to nest under.
    turns: Dict[str, List[Dict[str, Any]]] = {}
    demo: Optional[Dict[str, Any]] = None
    voiced = 0

    for row in rows:
        kind, t = row.get("kind"), row.get("t")
        if not isinstance(t, int):
            continue
        agent = row.get("agent_id")

        if kind == "tool.call_start":
            span = {"t0": t, "t1": t, "label": _called(row),
                    "agent": agent, "detail": _detail(row), "sub": False}
            open_calls[row.get("call_id") or f"anon-{t}"] = span
            out.append(span)
        elif kind == "tool.call_end":
            span = open_calls.pop(row.get("call_id") or "", None)
            if span is None:
                continue
            # THE FRAMEWORK'S OWN MEASUREMENT WINS over the difference
            # between two of our stamps: it timed the call inside the
            # runner, and our two rows also carry the trip to this
            # process.  The stamps are the fallback only because a call
            # that never ended has no measurement at all.
            measured = row.get("duration_seconds")
            span["t1"] = (t if not isinstance(measured, (int, float))
                          else span["t0"] + round(measured * 1000))
            if row.get("tool_name") == "enter_tier":
                status = row.get("result_status")
                where = span["label"].split("→")[-1].strip()
                if status == "already_at_tier":
                    # SAID, NOT HIDDEN. The model asks for the tier it is
                    # already in often enough that the profile measured
                    # it -- 74% of `enter_tier(voz)` before the persona
                    # rule, 30% after -- and a bar that looks like a
                    # switch and was not is the drawing lying quietly.
                    span["detail"] = "ya estaba ahí"
                elif status == "switched":
                    switches.append((span["t0"], where))
        elif kind == "turn.completed":
            seconds = row.get("duration_seconds")
            length = round(seconds * 1000) if isinstance(
                seconds, (int, float)) else 0
            span = {"t0": t - length, "t1": t, "agent": agent,
                    "label": f"turno {row.get('turn_number')}",
                    "detail": _detail(row), "sub": False, "is_turn": True}
            turns.setdefault(agent, []).append(span)
            out.append(span)
        elif kind == "agent.created":
            if row.get("parent_agent_id") is None:
                continue        # the scribe itself; it has no block
            span = {"t0": t, "t1": t, "agent": row.get("agent_id"),
                    "parent_agent": row.get("parent_agent_id"),
                    "label": row.get("profile_name") or row.get("agent_name")
                    or "subagente", "detail": "", "sub": True}
            agents[row.get("agent_id")] = span
            out.append(span)
        elif kind == "agent.completed":
            span = agents.get(agent)
            if span is not None:
                span["t1"] = t
                span["detail"] = _detail(row)
        elif kind == "demo.open":
            demo = {"t0": t, "t1": t, "agent": None, "sub": False,
                    "label": f"demo {row.get('ident')}", "detail": ""}
            out.append(demo)
        elif kind == "demo.voice":
            voiced += 1
            if demo is not None:
                demo["t1"] = max(demo["t1"], t)
        elif kind == "demo.close":
            if demo is not None:
                demo["t1"] = t
                demo["detail"] = _detail(row) + f" · {voiced} piezas de voz"

    # THE DERIVED ONE, and the only one: time inside a turn before its
    # first `enter_tier` is time spent in the tier the turn OPENED in.
    #
    # IT NAMES THAT TIER, from the `session.open` row the driver writes
    # out of the session's own frozen profile. "en el tier de entrada"
    # was true and useless -- a person reading it cannot tell whether
    # two seconds went to the model they can hear or to one they cannot,
    # which is the only reason the span is drawn at all.
    entry = next((r.get("initial_tier") for r in rows
                  if r.get("kind") == "session.open"), None)
    switches.sort()

    def tier_at(when: int) -> Optional[str]:
        """Which tier was active then.

        REPLAYED, NOT ASSUMED. This used to label every turn with the
        SESSION's initial tier, which is only true of the first one: a
        tier is left by `exit_on: completion` or by another
        `enter_tier`, so by turn five the session may be anywhere. The
        switches are in the journal, so the answer is read rather than
        guessed.
        """
        been = [where for at, where in switches if at <= when]
        return been[-1] if been else entry

    for agent, mine in turns.items():
        for turn in mine:
            # THE FIRST REAL SWITCH, not the first `enter_tier`: a call
            # that lands on the tier it is already in changes nothing,
            # so the time before it was not "time before switching".
            first = next((at for at, _ in switches
                          if turn["t0"] <= at <= turn["t1"]), None)
            if first is None:
                continue
            was = tier_at(turn["t0"])
            out.append({"t0": turn["t0"], "t1": first, "agent": agent,
                        "label": f"en {was}" if was else "en el tier inicial",
                        "sub": False,
                        "detail": "antes de cambiar de tier"})

    # A SUBAGENT STILL WORKING IS NOT A SUBAGENT THAT TOOK NO TIME.
    # `agent.completed` is what closes its block, and tonight none of
    # three arrived -- so the documenter drew as a 0 ms tick above forty
    # seconds of its own turns. Where the end is unknown, it is the last
    # thing that agent was seen doing, which is a measurement rather
    # than a guess about when it will finish.
    for agent, block in agents.items():
        if block["t1"] > block["t0"]:
            continue
        seen = [s["t1"] for s in out if s.get("agent") == agent]
        block["t1"] = max(seen) if seen else block["t0"]

    out.sort(key=lambda s: (s["t0"], -(s["t1"] - s["t0"])))
    _parent(out, turns, agents)
    return out


def _parent(out: List[Dict[str, Any]], turns, agents) -> None:
    """Choose each span's parent, by the agent chain.

    A call belongs to a turn of its OWN agent; an agent belongs to the
    turn that created it; a turn belongs to its agent's block, if it has
    one.  Nothing here asks "whose interval contains whose" across
    agents, which is what keeps a documenter that outlives its
    commission from swallowing the turn that came after it.
    """
    # THE ROOT AGENT: the one with turns of its own and no block, which
    # is exactly one in a session. It is needed because the framework
    # does NOT name it the same way twice -- measured on a live run,
    # `turn.completed` and `tool.call_start` carry `agent_id: "escriba"`
    # while `agent.created` reports the subagent's `parent_agent_id` as
    # `"main"`. Resolving the second to the first is unambiguous, and
    # without it every subagent hung at the top level beside the
    # conversation that commissioned it.
    root = next((a for a in turns if a not in agents), None)

    def holding(agent, at) -> Optional[Dict[str, Any]]:
        inside = [s for s in turns.get(agent, []) if s["t0"] <= at <= s["t1"]]
        return max(inside, key=lambda s: s["t0"], default=None)

    for span in out:
        if span.get("parent_agent"):                      # a subagent block
            # THE ALIAS IS RESOLVED HERE AND NOWHERE ELSE. `holding`
            # stays strict: a subagent's own calls belong to its own
            # turns, and letting the root stand in for a missing agent
            # there sent the documenter's `readFile` calls to the
            # scribe's turn instead of to the documenter.
            owner = (span["parent_agent"] if span["parent_agent"] in turns
                     else root)
            span["parent"] = holding(owner, span["t0"])
        elif span.get("is_turn"):                         # a turn
            span["parent"] = agents.get(span.get("agent"))
        elif span.get("agent"):                           # a call
            # OR THE AGENT'S OWN BLOCK, for a subagent: the documenter
            # reports no `turn.completed` of its own, so its calls have
            # no turn to sit in and would otherwise be drawn at the top
            # level, beside the conversation that commissioned them.
            span["parent"] = (holding(span["agent"], span["t0"])
                              or agents.get(span["agent"]))
        else:                                             # the demo
            span["parent"] = None

    for span in out:
        depth, up = 0, span["parent"]
        while up is not None and depth < 8:
            depth += 1
            up = up.get("parent")
        span["depth"] = depth
        if span.get("parent", {}) is not None and span.get("parent"):
            span["sub"] = span["sub"] or span["parent"].get("sub", False)


def _depth_first(ordered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The spans again, each one followed by what sits inside it.

    THE BARS CARRY THE TIME, so the LIST does not have to: ordered purely
    by start, a child whose parent overran was separated from it by
    whatever began in between -- the documenter's `writeNewFile` printed
    two levels deep directly under `turno 2`, which is a tree the
    indentation flatly denies.  Siblings keep their order in time.
    """
    kids: Dict[int, List[Dict[str, Any]]] = {}
    roots: List[Dict[str, Any]] = []
    for span in ordered:
        parent = span.get("parent")
        if parent is None:
            roots.append(span)
        else:
            kids.setdefault(id(parent), []).append(span)

    out: List[Dict[str, Any]] = []

    def walk(span: Dict[str, Any]) -> None:
        out.append(span)
        for kid in sorted(kids.get(id(span), []), key=lambda s: s["t0"]):
            walk(kid)

    for root in sorted(roots, key=lambda s: s["t0"]):
        walk(root)
    return out


def outline(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The waterfall as DATA, ready to draw in any surface.

    ONE SPAN BUILDER AND ONE ORDERING, shared by the text renderer below
    and by the page: a second implementation in JavaScript would be a
    second set of answers to "what nests under what", and the two would
    disagree exactly when a run was interesting enough to look at.  So
    the page is handed this and draws it.

    `parent` is NOT in the result and cannot be: the spans point at each
    other, so a span graph is a cycle and `json.dumps` would recurse
    until it gave up.  The nesting survives as `depth` plus the order,
    which is all a drawing needs.
    """
    ordered = _depth_first(spans(rows))
    opened = next((r for r in rows if r.get("kind") == "journal.open"), {})
    if not ordered:
        return {"wall": opened.get("wall"), "total": 0, "spans": []}
    first = min(s["t0"] for s in ordered)
    last = max(s["t1"] for s in ordered)
    return {
        "wall": opened.get("wall"),
        "total": max(last - first, 1),
        "spans": [{"label": s["label"], "depth": s["depth"],
                   "t0": s["t0"] - first, "t1": s["t1"] - first,
                   "detail": s["detail"], "sub": bool(s["sub"])}
                  for s in ordered],
    }


def render(rows: List[Dict[str, Any]], width: int = BAR) -> str:
    """The waterfall, as text."""
    drawn = outline(rows)
    ordered = drawn["spans"]
    if not ordered:
        return "nothing in this journal yet"

    first, total = 0, drawn["total"]
    lines = [f"{drawn['wall'] or 'sin hora'}   ·   {_secs(total)} en total",
             ""]
    for span in ordered:
        label = ("  " * span["depth"]) + span["label"]
        label = (label[:LABEL - 1] + "…") if len(label) > LABEL else label
        start = round((span["t0"] - first) / total * width)
        length = round((span["t1"] - span["t0"]) / total * width)
        glyph = SUB_FILL if span["sub"] else FILL
        bar = (" " * start) + (glyph * length if length else TICK)
        lines.append(f"{label.ljust(LABEL)} {bar.ljust(width + 1)} "
                     f"{_secs(span['t1'] - span['t0']).rjust(7)}"
                     f"{'  ' + span['detail'] if span['detail'] else ''}")
    return "\n".join(lines)


def read(path: Path) -> List[Dict[str, Any]]:
    """One journal's rows, skipping a torn last line."""
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main(argv: List[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0])
        print("\n  python -m sequence <path to journal.jsonl>")
        return 2
    path = Path(argv[0])
    if not path.is_file():
        print(f"no journal at {path}")
        return 1
    print(render(read(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
