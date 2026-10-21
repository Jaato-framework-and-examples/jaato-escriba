"""A link the person will click has to be one the escriba actually read.

Gates the scribe's `signal_completion`: every entry of `fuentes` must name
something this turn really saw — a URL that appears verbatim in what a
tool returned (or in what it was asked to fetch), or a wiki node it
really opened OR WAS CITED.

WHY A GATE AND NOT THE PERSONA.  The persona can ask for a URL copied
`tal cual`; it cannot make a model copy forty characters of commit sha
without dropping one.  And the failure is invisible from the inside: a
URL that is almost right looks exactly like a URL that is right, both to
the model writing it and to the page rendering it, and the person finds
out by clicking and getting nothing.  Prose is a suggestion; the ledger
is a fact, and the ledger is the one thing the model cannot rewrite.

WHY IT ASKS FOR A DELETION RATHER THAN A CORRECTION.  The refusal says to
REMOVE the source it cannot support, not to try again: a model told to
fix a link it never had will invent a better-looking one.  Dropping a
source costs the person a convenience; a wrong link costs them a trip.

THE SEARCH IS OVER THE WHOLE RESULT, STRINGIFIED, and deliberately not
over a field inside it.  The SDK says so of the ledger: a provider's
serialization can re-encode `result` as a JSON string before the ledger
builder sees it, "breaking inner-field access", which is also why
`success` is read rather than `result["status"]`.  A substring check
survives both carriers.

-----------------------------------------------------------------------
WHAT THIS GATE LET THROUGH ONCE, and the two changes that close it.

Session 20261012_220833.  The librarian cited
`[ref:feat-rip-host-integration-host-record-contract]`.  The model wrote
that id as the source's `nombre` and, as its `nodo`, the same id with
`feat-` removed.  This gate refused it, correctly.  The model then did
what the refusal INVITED — "ábrela con `kb_get_node` antes de citarla" —
and called `kb_get_node` on the invented id.  kbwiki answered that no
such node exists; jaato recorded that failed lookup as a successful call
(jaato#1654), so on the retry the id was in the opened set and this gate
passed it.  Four dead links reached the person.

1. OPENED NOW MEANS OPENED.  `success` is not enough, because for an MCP
   tool it is not a measurement: the flag lives in the result as
   `isError`, and the framework's success rule does not read it.  So the
   result is searched for that flag as well.  This is a measurement of
   the tool's own answer, not a second opinion about it.

2. THE REFUSAL NO LONGER OFFERS A RETRY.  "Open it and then cite it" is
   an instruction a model can satisfy without ever being right, and it
   contradicted the paragraph above it.  What replaces it is the list of
   ids the turn actually has, because the right one is usually in it —
   it was here, twice, in the librarian's own citations.

WHY A CITATION COUNTS AS WELL AS AN OPEN.  The librarian answers with
`[ref:<id>]` for pages it read on the escriba's behalf, and an id it
cites is an id that exists.  Accepting those turns this failure from a
dropped source into a working link, which is the whole point of knowing
the answer was present.  CITATIONS ARE READ FROM RESULTS ONLY, never
from arguments: a model that may cite what it put in its own prompt can
launder any id it likes through `ask_librarian`.  URLs keep reading
arguments too, for the stated reason — a page it fetched is a page it
read — and that asymmetry is deliberate.

The error strings are Spanish because the scribe reads them.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from jaato_sdk.cascade_authoring import ProcessorResult

#: The calls that OPEN a wiki page, as `escriba_web/wiki.py` has them.
#: A node is "really opened" by the same evidence the panel uses, so the
#: two cannot disagree about which pages this turn saw.  NOTE: until
#: jaato#1654 lands the panel is the weaker of the two, because
#: `ToolCallEndEvent` carries no result payload and so cannot see the
#: `isError` this module reads.
OPENS = ("mcp__kbwiki__kb_get_node", "mcp__kbwiki__kb_get_node_content")

#: How the librarian names a page it read: `[ref:<id>]`.
REF = re.compile(r"\[ref:([^\]\s]+)\]")




def _this_turn(context: Any) -> List[dict]:
    """The calls made since the last thing the person said.

    `context.tool_calls` IS THE WHOLE SESSION AND ALWAYS WAS -- built
    from `JaatoSession.get_history()` with no slicing
    (`shared/lifecycle_tools.py:949-951`).  This file's own promise is
    "algo que ESTE turno ha visto de verdad", and the schema says the
    same to the model (`SÓLO lo que has abierto en ESTE turno`); both
    were describing a list nobody had cut to a turn.

    SO THIS GATE WAS THE PERMISSIVE KIND OF WRONG, which is why it
    caught nothing on 2026-10-21 while its sibling refused three times:
    a URL read an hour ago still answered for a citation made now.  The
    link would have worked -- the escriba did read that page once --
    but "it is in the ledger somewhere" is not the question `fuentes`
    asks, and a node opened at 12:54 vouching for a claim at 14:42 is
    exactly the drift the whole file exists to refuse.

    THE RULE IS THE FRAMEWORK'S: a ledger entry carries `turn_index`,
    the index of the MESSAGE the call landed in, so a turn is cut by
    position -- everything after the last `user` message.  That is
    `shared/spawn_processors.tool_calls_this_turn`, which the framework
    computes for a spawn gate and leaves `[]` for us.  Copied and not
    imported, for the reason `_told` gives.
    """
    calls = list(getattr(context, "tool_calls", None) or [])
    history = list(context.session.get_history() or [])
    start = -1
    for index, message in enumerate(history):
        role = getattr(message, "role", None)
        if getattr(role, "value", role) == "user":
            start = index
    return [call for call in calls if call.get("turn_index", -1) > start]



def _text(value: Any) -> str:
    """One string for `value`, whichever carrier it arrived on."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _errored(value: Any) -> bool:
    """True when `isError` is set anywhere in a tool's answer.

    THE WHOLE STRUCTURE, not a known position in it.  The MCP envelope
    puts the flag at the top of the result, but the result reaches the
    ledger wrapped by a provider that may nest or re-encode it, and this
    module already assumes as much for URLs.  A walk answers on every
    carrier without this file having to guess which one it got.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    if isinstance(value, dict):
        return bool(value.get("isError")) or any(
            _errored(v) for v in value.values())
    if isinstance(value, list):
        return any(_errored(v) for v in value)
    return False


def _worked(call: dict) -> bool:
    """Whether the call both ran AND the tool answered without an error."""
    return bool(call.get("success")) and not _errored(call.get("result"))


def _seen(calls: List[dict]) -> str:
    """Everything this turn's working calls asked for and got back.

    Arguments as well as results: a page the escriba fetched is a page it
    read, and the URL it passed is the one it should cite.
    """
    parts = []
    for call in calls:
        if not _worked(call):
            continue
        for side in ("args", "result"):
            value = call.get(side)
            if value is None:
                continue
            parts.append(_text(value))
    return "\n".join(parts)


def _said(calls: List[dict]) -> str:
    """Only what the tools ANSWERED, never what they were asked.

    A citation is evidence because the librarian wrote it.  One the
    escriba wrote itself, in a prompt, is the claim under test.
    """
    return "\n".join(_text(call["result"]) for call in calls
                     if _worked(call) and call.get("result") is not None)


def _opened(calls: List[dict]) -> List[str]:
    """The wiki node ids this turn opened and got a page back for."""
    return [str((call.get("args") or {}).get("id") or "")
            for call in calls
            if call.get("name") in OPENS and _worked(call)]


def _refused(calls: List[dict]) -> List[str]:
    """The ids this turn tried to open and the wiki would not give."""
    return [str((call.get("args") or {}).get("id") or "")
            for call in calls
            if call.get("name") in OPENS and not _worked(call)]


def _cited(said: str) -> List[str]:
    """The node ids the librarian named in its answers."""
    return REF.findall(said)


def _once(ids: List[str]) -> List[str]:
    """The ids, deduplicated, in the order the turn produced them."""
    out: List[str] = []
    for value in ids:
        if value and value not in out:
            out.append(value)
    return out


def _told(context: Any, verdict: str, codes: List[str], **facts: Any) -> None:
    """One greppable line per call, in SHAPES and never in words.

    IN THE RUNNER'S LOG, not the session's: a processor is
    imported into the runner's own interpreter, so this carries
    `jaato_server.shared.dynamic_instructions` and lands in
    `<workspace>/.jaato/logs/runner-<stamp>.log`.  Verified live on
    2026-10-21.

    The sibling of `wrote_something._told`, and its docstring carries the
    measurement that put both here: on 2026-10-20 two processors refused
    a completion twice between them, the person heard the escriba answer
    the self-correction out loud, and NOTHING in the record could say
    which of the two had refused or why.  The framework publishes no
    event for a processor refusal.

    DELIBERATELY A COPY AND NOT AN IMPORT.  `explain completion` is
    explicit that a processor is imported into the runner's own
    interpreter and must not `sys.path.insert` — the slot serves several
    sessions in turn, so a shared helper module reachable only that way
    would change the next session's imports too.  Eight lines twice is
    the cheaper of the two mistakes.
    """
    shape = " ".join(f"{k}={v}" for k, v in sorted(facts.items()))
    line = "escriba.gate fuentes_reales %s codes=%s %s"
    if verdict == "refused":
        context.logger.warning(line, verdict, ",".join(codes), shape)
    else:
        context.logger.info(line, verdict, ",".join(codes) or "-", shape)


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    fuentes = payload.get("fuentes") or []
    if not fuentes:
        # SAID, not silent. "No sources to check" and "this gate never
        # ran" are different facts, and only one of them is fine.
        _told(context, "accepted", [], fuentes=0)
        return {"errors": []}

    # THIS TURN'S CALLS, not the session's -- see `_this_turn`.
    calls = _this_turn(context)
    seen = _seen(calls)
    have = _once(_opened(calls) + _cited(_said(calls)))
    tried = _refused(calls)
    errors: List[str] = []
    codes: List[str] = []

    for source in fuentes:
        name = str(source.get("nombre") or "?")
        url, node = source.get("url"), source.get("nodo")
        if url and node:
            codes.append("url_and_node")
            errors.append(
                f"«{name}» lleva `url` y `nodo` a la vez. Una fuente es una "
                "cosa o la otra: si es una página del wiki va su id en "
                "`nodo` y nada más; si es de fuera, su dirección en `url`.")
            continue
        if not url and not node:
            codes.append("neither")
            errors.append(
                f"«{name}» no lleva ni `url` ni `nodo`, así que no lleva a "
                "ninguna parte. Pon la dirección si la leíste, el id de la "
                "página si es del wiki, y si no tienes ninguna de las dos "
                "quita la fuente: un nombre suelto no le sirve a nadie.")
            continue
        if url and url not in seen:
            codes.append("url_unseen")
            errors.append(
                f"«{name}»: la dirección `{url}` no aparece en nada de lo "
                "que han devuelto tus herramientas en este turno. QUÍTALA. "
                "No la corrijas de memoria ni la rehagas a ojo: una "
                "dirección casi correcta se ve igual que una correcta, y "
                "la persona lo descubre pinchando. Si la fuente importa, "
                "ábrela y vuelve a mirarla.")
        if node and node not in have:
            # Said apart, because "the wiki says it does not exist" ends
            # the retry loop and "you have not cited it" does not.
            why = (f"has intentado abrir `{node}` y el wiki ha contestado "
                   "que no existe ninguna página con ese id"
                   if node in tried else
                   f"`{node}` no es ninguna de las páginas que has abierto "
                   "ni de las que te han citado en este turno")
            codes.append("node_refused" if node in tried else "node_unseen")
            tienes = (f" Las que sí tienes: {', '.join(have)}."
                      if have else " En este turno no tienes ninguna.")
            errors.append(
                f"«{name}»: {why}.{tienes} QUÍTALA, o pon una de esas "
                "copiada entera y letra por letra: un identificador "
                "parecido no es el mismo, y la persona lo descubre "
                "pinchando.")
    # ONE REFUSAL PER TURN, NOT ONE PER RETRY -- the same bargain
    # `wrote_something` makes, and for the same measured reason: the
    # ceiling is spent per session, so two tries at one turn would
    # leave every later turn in a two-hour conversation ungated.  What
    # stands after the first refusal is a turn whose `fuentes` may hold
    # one bad link; what the alternative loses is the gate itself.
    _told(context, "refused" if errors else "accepted", codes,
          fuentes=len(fuentes), seen=len(seen), have=len(have),
          tried=len(tried), calls=len(calls))
    return {"errors": errors}
