"""The captures it hands over have to be captures it looked at.

Gates the scribe's `signal_completion` on the one turn that commissions a
demonstration: every frame named in a `spawn_subagent` task must be one
this turn sent to `decide` INSIDE an `images` array.

WHY THIS EXISTS, and why it is not `capturas_reales` again.  That gate
guards the documenter -- a capture it describes must be one it opened.
This guards the step before: choosing. A selection made without looking
is twelve arbitrary frames, and everything downstream then works
perfectly on the wrong twelve. The document that comes out is honest
about what it shows and still useless, which is the expensive kind of
wrong: nothing fails, and the person reads a procedure assembled from
whichever screens happened to be picked.

MEASURED, 2026-10-17, twice, by two different agents.  `decide` was
called with no images at all: the ledger recorded 8 questions on 907
input tokens, then 1,288 on the next run -- a text-only call. The frames
were named in PROSE instead ("frame 0001.png"), so the decision model
scored screenshots it had never been sent, from their file names, and
answered with calibrated confidence for every one. With the images
actually attached the same 8 questions cost 14,500 tokens.

AND WHY IT IS NOT A FRAMEWORK CHECK.  Looked for, and it is not there to
be had: jaato already refuses both halves of the mismatch -- an image
sent and never referenced (`decisions.py:564`), and a `{{image:NAME}}`
naming an image not in the request (`decisions.py:430`). A call that
mentions no image at all is a legitimate text decision, and nothing
downstream can tell it from one that was meant to carry pictures. The
intent lives here, so the check does too.

WHAT IT CANNOT DO.  It checks that a handed-over frame was SENT to the
decision model, not that the score came back sensible. A model that
attaches all 76 and then hands over twelve at random passes this.

The error strings are Spanish because the scribe reads them.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Set

from jaato_sdk.cascade_authoring import ProcessorResult

#: A stored frame, as `demo.py` names them, anywhere in a path.
FRAME = re.compile(r"[\w./-]*demo/[\w.-]+/\d{4}\.(?:png|jpe?g)",
                   re.IGNORECASE)


def _text(value: Any) -> str:
    """One string for `value`, whichever carrier it arrived on."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _errored(value: Any) -> bool:
    """True when `isError` is set anywhere in a tool's answer.

    `fuentes_reales` explains the walk: the flag's position depends on
    the provider's serialization, so the whole structure is searched.
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


def _tail(path: str) -> str:
    """`<demo-id>/<frame>`, which is what two spellings of one frame share.

    The task names a frame however the model wrote it and `images` names
    it however the model wrote it there; both end in the demo's own
    directory and the file, and that pair is unique within a workspace.
    """
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


def _handed_over(calls: List[dict]) -> List[str]:
    """Every frame this turn named in a subagent's task, in order."""
    out: List[str] = []
    for call in calls:
        if call.get("name") != "spawn_subagent" or not _worked(call):
            continue
        task = (call.get("args") or {}).get("task")
        for found in FRAME.findall(_text(task) if task is not None else ""):
            if found not in out:
                out.append(found)
    return out


def _shown(calls: List[dict]) -> Set[str]:
    """Every frame this turn actually SENT to a decision model.

    The `images` array and nothing else: a frame named in the `state`,
    or in prose, is a frame the model was asked about and never shown.
    """
    seen: Set[str] = set()
    for call in calls:
        if call.get("name") != "decide" or not _worked(call):
            continue
        for image in ((call.get("args") or {}).get("images") or []):
            if isinstance(image, dict):
                path = image.get("path")
                if isinstance(path, str) and path:
                    seen.add(_tail(path))
    return seen


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    calls = list(getattr(context, "tool_calls", None) or [])
    handed = _handed_over(calls)
    if not handed:
        # Not a demonstration commission. Every other turn this scribe
        # takes passes through here and must not notice it.
        return {"errors": []}

    shown = _shown(calls)
    unseen = [f for f in handed if _tail(f) not in shown]
    if not unseen:
        return {"errors": []}

    if not shown:
        return {"errors": [
            f"Le has pasado al documentalista {len(handed)} captura(s) sin "
            f"haber mirado ninguna: en este turno no has mandado ni una "
            f"imagen a `decide`. Nombrarlas en el `state`, o escribir el "
            f"nombre del fichero en la pregunta, NO es enseñárselas — el "
            f"modelo puntúa lo que le mandas en el array `images`, y si no "
            f"le mandas nada te contesta igual, a ciegas y con la misma "
            f"seguridad. Vuelve a puntuarlas con `images`, una entrada por "
            f"captura con su `path`, y elige con esas notas."]}

    return {"errors": [
        f"{len(unseen)} de las capturas que le has pasado al documentalista "
        f"no las has mandado a `decide` en este turno: "
        f"{', '.join(unseen[:5])}. Se eligen mirándolas: una captura que no "
        f"has enseñado al modelo no tiene nota, y pasarla es elegirla a "
        f"ojo. Mándalas en el array `images` y quédate con las mejores, o "
        f"pásale sólo las que sí puntuaste."]}
