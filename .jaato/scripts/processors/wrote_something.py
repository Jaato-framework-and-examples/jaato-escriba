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

The error strings are Spanish because the scribe reads them.
"""
from __future__ import annotations

from typing import Any, Dict, List

from jaato_sdk.cascade_authoring import ProcessorResult

TOOL = "store_memory"
SPAWN = "spawn_subagent"


def _document_claim(payload: Dict[str, Any], calls: list) -> List[str]:
    """A commissioned document has to have been commissioned.

    The scribe cannot write documentation itself — it hands the job to the
    `documentalista` with `spawn_subagent`. Measured 2026-09-10: asked for
    a document, it read the memories, stored one, and said "documento
    consolidado … guardado" without spawning anything. The person is then
    waiting for a file nobody is writing, and the only clue is its absence.

    Same shape as the memory check: the claim is in the payload, the truth
    is in the ledger, and the ledger is the one the model cannot rewrite.
    """
    if not payload.get("documento_encargado"):
        return []
    if any(c.get("name") == SPAWN and c.get("success") for c in calls):
        return []
    return [
        "Dices `documento_encargado: true` pero no has llamado a "
        "`spawn_subagent` en este turno. Un documento no se escribe solo ni "
        "lo escribes tú: entra en `escribano` y encárgaselo al "
        "`documentalista`, con el encargo de verdad en `task`. Si no piensas "
        "encargarlo, pon la bandera en false y no le digas a la persona que "
        "hay un documento — se quedaría esperando un fichero que nadie está "
        "escribiendo."
    ]


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    calls = list(getattr(context, "tool_calls", None) or [])
    # `success` is the framework's canonical indicator, computed as
    # `"error" not in result` (shared/completion_processors.py:148).  NOT
    # `result["status"]`, which is plugin-side and varies through provider
    # serialisation.
    stored = [tc for tc in calls
              if tc.get("name") == TOOL and tc.get("success")]

    doc = _document_claim(payload, calls)

    if stored:
        if payload.get("nada_que_anotar"):
            return {"errors": doc + [
                "Dices `nada_que_anotar: true` pero SÍ has guardado "
                f"{len(stored)} memoria(s) en este turno. Una de las dos cosas "
                "no es cierta: quita la bandera, o no guardes."
            ]}
        return {"errors": doc}

    if payload.get("nada_que_anotar"):
        return {"errors": doc}      # dicho y justificado: se acepta

    attempted = [tc for tc in calls if tc.get("name") == TOOL]
    if attempted:
        return {"errors": doc + [
            f"Intentaste guardar {len(attempted)} vez/veces y ninguna salió "
            "bien. Míra el error que te devolvió `store_memory`, corrige la "
            "llamada y vuelve a intentarlo antes de completar."
        ]}

    return {"errors": doc + [
        "No has anotado nada en este turno. Si la persona te ha contado algo "
        "que le serviría a una sesión futura que lo ha olvidado todo, entra "
        "en `escribano` y guárdalo con `store_memory` antes de completar.\n\n"
        "Y si de verdad no había nada —un saludo, un «sí», un «sigue»— dilo "
        "con `nada_que_anotar: true` y explica en `anotado` por qué. Lo que "
        "no vale es completar en silencio: eso no se distingue de haberlo "
        "olvidado, y es lo que hace que mañana no recuerdes esta conversación."
    ]}
