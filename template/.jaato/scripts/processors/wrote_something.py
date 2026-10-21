"""Nothing is remembered unless it was actually written down.

Gates the scribe's `signal_completion`: the turn is not accepted unless
`store_memory` really ran, or the payload explicitly says there was
nothing worth keeping.

WHY A GATE AND NOT THE PERSONA.  The persona already told it to write
things down, and it did not: measured 2026-09-09, 0 tool calls across 5
turns and 5.6 minutes of real conversation.  An audio model announces the
tool instead of invoking it, and no wording fixes that — it is the same
lesson as the whitelist. Prose is a suggestion; a gate is a contract.

WHY THERE IS AN ESCAPE HATCH.  Forcing a memory on EVERY turn would force
one on "hello", on "yes" and on "go on", and the only way to satisfy that
is to invent something.  For a second brain that is the worst possible
failure — it already happened once with a persona example, and it made up
the user's life in its first sentence.  So the gate demands a memory OR an
explicit, reasoned declaration that there was nothing.  The declaration is
visible in the payload, so abuse is auditable rather than silent.

WHAT THE REFUSAL ASKS FOR IS PART OF THE GATE.  Measured 2026-10-12,
session 20261012_194750: the person said "no, hoy quiero cambiar de tema,
quiero hablar de la arquitectura RIP" -- a turn with one plainly durable
fact in it, which topic they are moving to.  The scribe stored nothing,
this gate refused, and the scribe satisfied it from the only other thing
in its context: it stored a memory about the PREVIOUS topic, said that
out loud, and closed.  The person heard their change of subject answered
with a recap of the subject they had just dropped.

So the refusal names the case now.  A gate that says "store something"
and nothing more is answered with whatever is nearest, and what is
nearest is the conversation that just ended.

The error strings are Spanish because the scribe reads them.
"""
from __future__ import annotations

from typing import Any, Dict, List

from jaato_sdk.cascade_authoring import ProcessorResult

TOOL = "store_memory"
ORDER = "encargar_documento"
SIGNAL = "signal_completion"


def _pending(call: dict) -> bool:
    """True for a call the ledger has no answer for YET.

    `build_ledger` writes `{"error": "no_response"}` itself for a call
    with no matching response, which is what the `signal_completion`
    being validated right now looks like: its own function_call is
    already in history and its result cannot be.
    """
    result = call.get("result")
    return isinstance(result, dict) and result.get("error") == "no_response"


def _this_turn(context: Any) -> List[dict]:
    """The calls made since the last thing the person said.

    `context.tool_calls` IS THE WHOLE SESSION AND ALWAYS WAS.  The
    framework builds it from `JaatoSession.get_history()` with no
    slicing (`shared/lifecycle_tools.py:949-951`), and `build_ledger`
    walks all of it.  Every "en este turno" in this file was a claim
    about a list that had never been cut to a turn.

    MEASURED, 2026-10-21, session 20261021_125148.  The escriba stored
    ONE memory in a two-hour conversation (the page's own counter:
    `memoria sin curar 1 +1, 0 al empezar`).  From that memory onwards
    every turn that honestly had nothing to keep was refused
    `flag_contradicts_memory` -- 14:32:26, 14:32:53 and 14:42:58 --
    because `stored` counted that one memory again on each of them.
    The model was right each time and the gate was wrong each time.

    AND THE INVERSE IS THE WORSE HALF.  `nothing_stored` is why this
    file exists: five turns, 5.6 minutes, nothing written down.  Once
    the session has one successful `store_memory` anywhere behind it,
    `stored` is never empty again, so that branch can never fire after
    the first memory.  The gate stopped doing its job at the first
    thing it ever caught.

    THE RULE IS THE FRAMEWORK'S, not one invented here.  A ledger entry
    carries `turn_index`, the index of the MESSAGE the call landed in,
    so a turn is cut by position: everything after the last `user`
    message.  That is `shared/spawn_processors.tool_calls_this_turn`,
    which the framework already computes for a spawn gate and leaves
    empty for us (`RenderContext.tool_calls_this_turn`, "[] everywhere
    else").  Copied and not imported, for `_told`'s reason: a processor
    is imported into the runner's own interpreter and `jaato_server`
    internals are not its to reach into.

    AN INJECTED PROMPT IS A USER MESSAGE, so `Person._tell` -- the
    driver's "the document is written" turn -- starts a turn here too.
    That is the behaviour this wants: what it says about that document
    is its own turn's business.
    """
    calls = list(getattr(context, "tool_calls", None) or [])
    history = list(context.session.get_history() or [])
    start = -1
    for index, message in enumerate(history):
        role = getattr(message, "role", None)
        if getattr(role, "value", role) == "user":
            start = index
    return [call for call in calls if call.get("turn_index", -1) > start]


def _attempt(calls: List[dict]) -> int:
    """Which try at `signal_completion` this is, inside this turn.

    Counted from the ledger and not from state of our own: a refused
    completion is itself a call with a result, and the one being
    validated right now is the pending one.  The framework's refusal
    budget is PER SESSION (`shared/lifecycle_tools.py:334-347`: the
    `LoadedProcessor` is "the framework's declared home for per-session
    processor state -- the `max_refusals` counter lives there"), and
    `_base_escriba.yaml` buys two.  Spending both inside one stubborn
    turn leaves the rest of a two-hour conversation ungated, which is
    what happened on 2026-10-21: the budget was spent by 14:32:53 and
    the refusal at 14:42:58 was accepted anyway -- its `anotado` is the
    note the person read.
    """
    return 1 + sum(1 for call in calls
                   if call.get("name") == SIGNAL and not _pending(call))


def _document_claim(payload: Dict[str, Any], calls: list) -> List[str]:
    """A commissioned document has to have been commissioned.

    The scribe cannot write documentation itself — it hands the job to the
    `documentalista` with `spawn_subagent`. Measured 2026-09-10: asked for
    a document, it read the memories, stored one, and said "documento
    consolidado … guardado" without spawning anything. The person is then
    waiting for a file nobody is writing, and the only clue is its absence.

    Same shape as the memory check: the claim is in the payload, the truth
    is in the ledger, and the ledger is the one the model cannot rewrite.

    HONESTY FIRST, AND THE ORDER IS MEASURED.  This used to offer "go
    and commission it" as the first remedy.  2026-10-19 16:12: the
    scribe claimed a document for a demonstration NOBODY HAD RECORDED --
    the person had only described what they were about to show -- and
    this refusal sent it off to commission one, which it did, naming an
    abstract profile that cannot start.  A claim that is false at its
    root is fixed by withdrawing it, not by making it true.  So the
    refusal leads with the payload, which is the only thing a completion
    gate can actually ask for: the turn's work is over by the time this
    runs.
    """
    asked = any(c.get("name") == ORDER and c.get("success") for c in calls)

    if not payload.get("documento_encargado"):
        # It said it commissioned nothing. Then it must not be TELLING the
        # person otherwise — measured twice: "documento consolidado …
        # guardado" and "he anotado un documento sobre apicultura", both
        # with no spawn, both while the flag was simply absent. Omitting the
        # flag was the way to make the claim without owning it, so the flag
        # is required now and this catches the remaining contradiction.
        #
        # A narrow keyword check, deliberately: it fires only when the
        # sentence the scribe will SAY OUT LOUD announces a document that
        # does not exist. The cost of a false positive is one retry; the
        # cost of a miss is a person waiting for a file forever.
        said = str(payload.get("anotado") or "").lower()
        claims = any(k in said for k in (
            "documento", "documentación", "lo he escrito", "lo he guardado en"))
        if claims and not asked:
            return [
                "Tu `anotado` habla de un documento, pero "
                "`documento_encargado` es false y no has llamado a "
                "`encargar_documento`: no hay ningún documento. Lo primero "
                "es decir la verdad: quita del `anotado` lo que suene a que "
                "existe un fichero. Y SOLO si la persona lo pidió de verdad "
                "en este turno, pídelo con `encargar_documento` — guardar "
                "una memoria sobre el tema no es escribirlo."
            ]
        return []
    if asked:
        return []
    return [
        "Dices `documento_encargado: true` pero no has llamado a "
        "`encargar_documento` en este turno. Lo primero es que el `anotado` "
        "y la bandera digan la verdad: ponla en false y no le digas a la "
        "persona que hay un documento, porque se quedaría esperando un "
        "fichero que nadie está escribiendo. Pídelo con "
        "`encargar_documento` SOLO si ella lo pidió de verdad."
    ]

def _told(context: Any, verdict: str, codes: List[str], **facts: Any) -> None:
    """One greppable line per call, in SHAPES and never in words.

    IN THE RUNNER'S LOG, not the session's: a processor is
    imported into the runner's own interpreter, so this carries
    `jaato_server.shared.dynamic_instructions` and lands in
    `<workspace>/.jaato/logs/runner-<stamp>.log`.  Verified live on
    2026-10-21.

    WHY THIS EXISTS, MEASURED.  2026-10-20, session 20261020_204128: the
    escriba answered with TWO spoken replies, because this gate refused
    `signal_completion` twice and the model answered each self-correction
    out loud in `voz` -- the one tier the person hears.  The record could
    say THAT it refused and with what payload (the journal keeps
    `signal_completion`'s args and `result_status`) and NOTHING anywhere
    could say which processor refused or why: the framework publishes no
    event for a refusal, and none of its 172 typed events mentions one.
    So "why did the gate fire" was unanswerable after the fact, which is
    what turned a one-line diagnosis into an afternoon.

    A CODE PER BRANCH, NOT THE ERROR TEXT.  The strings are long Spanish
    instructions written for a model about to retry; logging them whole
    makes the log unreadable and duplicates what is already in this
    file.  The code names the branch, the facts say why it was taken.

    SHAPES AND NOT WORDS, which is `journal.py`'s rule and the right one
    here too: `anotado` is the model's account of what the person said.
    Counts, flags and lengths answer everything this has to answer, and
    none of them is anybody's conversation.

    REFUSALS AT WARNING, ACCEPTANCES AT INFO.  A refusal costs the
    person an audible reply, so it has to be visible whatever the level
    is set to; how often the gate passed is useful and not urgent.

    AND THIS IS WHAT MAKES EXHAUSTION VISIBLE.  The framework downgrades
    errors to warnings once `max_refusals` is spent (`on_exhausted:
    allow`) and reports nothing about having done so.  A line here
    saying this processor produced errors, beside a journal row saying
    the completion was `completed`, is the only way to tell a turn that
    SATISFIED the gate from one that merely outlasted it.
    """
    shape = " ".join(f"{k}={v}" for k, v in sorted(facts.items()))
    line = "escriba.gate wrote_something %s codes=%s %s"
    if verdict == "refused":
        context.logger.warning(line, verdict, ",".join(codes), shape)
    else:
        context.logger.info(line, verdict, ",".join(codes) or "-", shape)


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    # THIS TURN'S CALLS, not the session's.  See `_this_turn`: every
    # "en este turno" below was false until 2026-10-21.
    calls = _this_turn(context)
    # `success` is the framework's canonical indicator, computed as
    # `"error" not in result` (shared/completion_processors.py:148).  NOT
    # `result["status"]`, which is plugin-side and varies through provider
    # serialisation.
    stored = [tc for tc in calls
              if tc.get("name") == TOOL and tc.get("success")]

    doc = _document_claim(payload, calls)

    attempted = [tc for tc in calls if tc.get("name") == TOOL]
    flag = bool(payload.get("nada_que_anotar"))

    # ONE EXIT, so every refusal is logged by construction rather than
    # by each branch remembering to.  The branches are the ones that
    # were already here; what is new is that each one names itself.
    own: List[str] = []
    codes: List[str] = ["doc_claim"] if doc else []

    if stored and flag:
        codes.append("flag_contradicts_memory")
        own = [
            "Dices `nada_que_anotar: true` pero SÍ has guardado "
            f"{len(stored)} memoria(s) en este turno. Una de las dos cosas "
            "no es cierta: quita la bandera, o no guardes."
        ]
    elif stored or flag:
        # Satisfied: a memory was written, or there was nothing worth
        # keeping and the payload says so and justifies it.
        pass
    elif attempted:
        codes.append("store_failed")
        own = [
            f"Intentaste guardar {len(attempted)} vez/veces y ninguna salió "
            "bien. Míra el error que te devolvió `store_memory`, corrige la "
            "llamada y vuelve a intentarlo antes de completar."
        ]
    else:
        codes.append("nothing_stored")
        own = [
            "No has anotado nada en este turno. Si la persona te ha contado algo "
            "que le serviría a una sesión futura que lo ha olvidado todo, entra "
            "en `escribano` y guárdalo con `store_memory` antes de completar.\n\n"
            "Y si lo que ha pasado es que ha CAMBIADO DE TEMA, eso es justo lo "
            "que hay que guardar: en qué quiere trabajar ahora. No rellenes este "
            "turno con lo que estabais hablando antes — aquello ya está anotado, "
            "repetirlo lo cuenta dos veces, y deja sin guardar lo único nuevo que "
            "has oído. Tampoco se lo cuentes otra vez en voz alta: acaba de "
            "decirte que quiere dejarlo.\n\n"
            "Y si de verdad no había nada —un saludo, un «sí», un «sigue»— dilo "
            "con `nada_que_anotar: true` y explica en `anotado` por qué. Lo que "
            "no vale es completar en silencio: eso no se distingue de haberlo "
            "olvidado, y es lo que hace que mañana no recuerdes esta conversación."
        ]

    errors = doc + own
    # ONE REFUSAL PER TURN, NOT ONE PER RETRY.  The ceiling the profile
    # buys is spent per session, so a turn that answers the same way
    # twice would take the whole conversation's budget with it and
    # leave every later turn ungated.  After the first refusal of a
    # turn the same words go out as `warnings[]`, which the framework
    # keeps in the audit trail and never blocks on -- so the turn
    # stands, the budget is not touched again, and the record still
    # says the gate was unhappy.  `_told` logs the real verdict either
    # way: `refused` is what this processor decided, and `attempt`
    # says whether the framework was going to act on it.
    attempt = _attempt(calls)
    _told(context, "refused" if errors else "accepted", codes,
          stored=len(stored), attempted=len(attempted), flag=flag,
          doc_claim=bool(doc), attempt=attempt, calls=len(calls),
          documento_encargado=bool(payload.get("documento_encargado")),
          anotado_chars=len(str(payload.get("anotado") or "")))
    if errors and attempt > 1:
        return {"errors": [], "warnings": errors}
    return {"errors": errors}
