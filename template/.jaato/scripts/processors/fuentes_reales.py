"""A link the person will click has to be one the escriba actually read.

Gates the scribe's `signal_completion`: every entry of `fuentes` must name
something this turn really saw — a URL that appears verbatim in what a
tool returned (or in what it was asked to fetch), or a wiki node it
really opened.

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

The error strings are Spanish because the scribe reads them.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from jaato_sdk.cascade_authoring import ProcessorResult

#: The calls that OPEN a wiki page, as `escriba_web/wiki.py` has them.
#: A node is "really opened" by the same evidence the panel uses, so the
#: two cannot disagree about which pages this turn saw.
OPENS = ("mcp__kbwiki__kb_get_node", "mcp__kbwiki__kb_get_node_content")


def _seen(calls: List[dict]) -> str:
    """Everything this turn's successful calls asked for and got back.

    Arguments as well as results: a page the escriba fetched is a page it
    read, and the URL it passed is the one it should cite.
    """
    parts = []
    for call in calls:
        if not call.get("success"):
            continue
        for side in ("args", "result"):
            value = call.get(side)
            if value is None:
                continue
            parts.append(value if isinstance(value, str)
                         else json.dumps(value, ensure_ascii=False, default=str))
    return "\n".join(parts)


def _nodes(calls: List[dict]) -> List[str]:
    """The wiki node ids this turn opened successfully."""
    return [str((call.get("args") or {}).get("id") or "")
            for call in calls
            if call.get("name") in OPENS and call.get("success")]


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    fuentes = payload.get("fuentes") or []
    if not fuentes:
        return {"errors": []}

    calls = list(getattr(context, "tool_calls", None) or [])
    seen, nodes = _seen(calls), _nodes(calls)
    errors: List[str] = []

    for source in fuentes:
        name = str(source.get("nombre") or "?")
        url, node = source.get("url"), source.get("nodo")
        if url and node:
            errors.append(
                f"«{name}» lleva `url` y `nodo` a la vez. Una fuente es una "
                "cosa o la otra: si es una página del wiki va su id en "
                "`nodo` y nada más; si es de fuera, su dirección en `url`.")
            continue
        if not url and not node:
            errors.append(
                f"«{name}» no lleva ni `url` ni `nodo`, así que no lleva a "
                "ninguna parte. Pon la dirección si la leíste, el id de la "
                "página si es del wiki, y si no tienes ninguna de las dos "
                "quita la fuente: un nombre suelto no le sirve a nadie.")
            continue
        if url and url not in seen:
            errors.append(
                f"«{name}»: la dirección `{url}` no aparece en nada de lo "
                "que han devuelto tus herramientas en este turno. QUÍTALA. "
                "No la corrijas de memoria ni la rehagas a ojo: una "
                "dirección casi correcta se ve igual que una correcta, y "
                "la persona lo descubre pinchando. Si la fuente importa, "
                "ábrela y vuelve a mirarla.")
        if node and node not in nodes:
            opened = ", ".join(n for n in nodes if n) or "ninguna"
            errors.append(
                f"«{name}»: no has abierto la página `{node}` en este "
                f"turno (abiertas: {opened}). Quítala, o ábrela con "
                "`kb_get_node` antes de citarla.")
    return {"errors": errors}
