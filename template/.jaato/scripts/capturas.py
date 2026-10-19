"""The captures this commission is for, in the prompt before the first turn.

Expanded into the documenter's persona with
`{{!py:scripts/capturas.py}}` — MANDATORY, and that is the whole design.
A commission that names a selection the documenter cannot read aborts
session preparation with `PrefetchError` instead of starting an agent
that will describe screens it never opened.

WHY THE LIST DOES NOT ARRIVE IN THE `task`.  It used to: the scribe
chose the captures and wrote their twelve paths into the sentence that
commissioned this agent.  That asks a conversational model to carry a
list of exact strings through prose, and it has been measured failing
four separate times — captures named without ever being looked at
(2026-10-17, twice), and a `decide` call with no images at all followed
by thirty reads of a directory that has never existed (2026-10-19).
`choosing` makes the selection now, deterministically, and writes it
down; this reads what was written.  A list nobody retypes is a list
nobody mistypes.

NOT EVERY DOCUMENT IS A DEMONSTRATION.  Most commissions are a topic,
and a topic has no captures — so no `capturas` param means there is
nothing to embed and this says nothing.  That is the one branch here,
and it is on a parameter being present, never on a file being missing:
a commission that SAYS it has a selection and does not is the failure
this exists to stop, and it is left to raise.

The returned text is Spanish because the documenter reads it and writes
in Spanish; the code around it is English, like the rest of the repo.
"""
from __future__ import annotations

import json
import pathlib

#: The parameter the driver sets: where the selection was written,
#: relative to the workspace.  Named rather than derived, so this script
#: knows nothing about where demonstrations live.
PARAM = "capturas"


def _when(at_ms: int) -> str:
    """The moment in the demonstration this capture was taken."""
    return f"{at_ms // 60000:02d}:{at_ms // 1000 % 60:02d}"


def render(context, args) -> str:
    named = (context.agent_params or {}).get(PARAM, "")
    if not named:
        return ""
    path = pathlib.Path(context.workspace_path) / named
    # NOT GUARDED, AND DELIBERATELY: a missing or unreadable selection
    # raises here, which aborts session preparation.  That is the gate.
    # Answering with an empty list instead would start a documenter with
    # no captures and nothing to say so.
    kept = json.loads(path.read_text(encoding="utf-8"))
    chosen = kept["chosen"]
    if not chosen:
        raise RuntimeError(
            f"capturas: «{named}» no elige ninguna captura; "
            "una demostración sin capturas no se puede documentar")
    lines = [f"{n}. `{c['path']}` ({_when(c['at_ms'])})"
             for n, c in enumerate(chosen, 1)]
    return (
        "## Las capturas de esta demostración\n\n"
        f"Son {len(chosen)} de las {kept['scored']} que se grabaron, "
        "elegidas por lo que avanzan el procedimiento y puestas en el "
        "orden en que ocurrieron. Ábrelas TODAS, una a una, con "
        "`readFile`, en este orden, y no abras ninguna otra:\n\n"
        + "\n".join(lines))
