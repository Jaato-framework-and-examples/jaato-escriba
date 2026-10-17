"""A capture the document describes has to be one this turn actually saw.

Gates the documenter's `signal_completion` when the commission was a
demonstration: every frame linked from the written documents must be one
the turn either sent to `decide` in an `images` array or opened with
`readFile`.

WHY A GATE AND NOT THE PERSONA.  This is `fuentes_reales`' argument,
with the failure mode one step worse.  The persona can say "open the
twelve you chose"; it cannot make a model do it, and a model that does
not writes the document anyway.

MEASURED, 2026-10-17, the first run with `decide` wired in.  The
documenter read `frames.jsonl`, called `decide` three times, and wrote
`docs/demo/index.md` describing all eight captures: "se observa la
interfaz inicial", "se realizan ajustes en la configuración", "se
seleccionan más opciones".  It had opened no capture at all -- every
`readFile` was the index -- and the `decide` calls carried `images=0`,
so the decision model scored the frames from their FILE NAMES (the
ledger: 8 questions, 907 input tokens, a text-only call).  The frames
were grey bars and a moving rectangle.  `docs_are_real` passed it: the
files it claimed to have written were on disk, the links resolved, the
citations existed.  Everything was real except what it said.

A document that invents a procedure is worse than one that fails,
because a failure is visible and this is not: it reads exactly like a
true document, and the person finds out by following it.

WHAT IT CANNOT DO.  It checks that a capture was SEEN, not that the
prose matches it -- that is not mechanical, and `docs_are_real` says the
same of its own prose.  So the persona is told to LINK every capture it
describes, and this gate only sees the linked ones.  A model narrating a
capture it never names escapes it, which is why the rule is also said
where the model reads it.

The error strings are Spanish because the documenter reads them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from jaato_sdk.cascade_authoring import ProcessorResult

#: A stored frame, as `demo.py` names them: `0001.png`, `0007.jpg`.
FRAME = re.compile(r"^\d{4}\.(?:png|jpe?g)$", re.IGNORECASE)

#: Markdown's two ways of pointing at a file, images and plain links.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

#: The tools that can put a capture in front of somebody this turn.
SAW = ("readFile", "decide")


def _text(value: Any) -> str:
    """One string for `value`, whichever carrier it arrived on."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _errored(value: Any) -> bool:
    """True when `isError` is set anywhere in a tool's answer.

    `fuentes_reales` explains the walk: the flag's position depends on a
    provider's serialization, so the whole structure is searched.
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


def _is_frame(path: str) -> bool:
    """Whether `path` names a stored demonstration frame."""
    parts = Path(path).parts
    return bool(parts) and FRAME.match(parts[-1]) is not None


def _looked_at(calls: List[dict], ws: Path) -> Set[Path]:
    """Every frame this turn put in front of a model, resolved.

    A `readFile` IS a look: the frame comes back as multimodal content.
    A `decide` is a look only for the frames in its `images` array --
    naming one in the state without sending it is precisely the call
    that answered about eight frames nobody had seen.
    """
    seen: Set[Path] = set()
    for call in calls:
        if call.get("name") not in SAW or not _worked(call):
            continue
        args = call.get("args") or {}
        candidates = [args.get("path")]
        for image in (args.get("images") or []):
            if isinstance(image, dict):
                candidates.append(image.get("path"))
        for raw in candidates:
            if isinstance(raw, str) and raw and _is_frame(raw):
                here = Path(raw)
                seen.add(here if here.is_absolute() else (ws / here))
    return {p.resolve() for p in seen}


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    ws = Path(getattr(context, "workspace_path", "") or ".")
    files = [str(f).strip() for f in (payload.get("ficheros") or [])]
    if not files:
        return {"errors": []}

    seen = _looked_at(list(getattr(context, "tool_calls", None) or []), ws)
    unseen: List[str] = []
    for name in files:
        page = ws / name
        if not page.is_file():
            # `docs_are_real` owns that refusal; one missing file should
            # not be reported twice in different words.
            continue
        for target in LINK.findall(page.read_text(errors="replace")):
            link = target.strip().split()[0] if target.strip() else ""
            if not link or link.startswith(("http://", "https://", "mailto:")):
                continue
            if not _is_frame(link):
                continue
            where = (page.parent / link).resolve()
            if where not in seen and link not in unseen:
                unseen.append(link)

    if not unseen:
        return {"errors": []}
    return {"errors": [
        f"{len(unseen)} captura(s) que describes no las has mirado en este "
        f"turno: {', '.join(unseen[:5])}. Una captura sólo la has visto si "
        f"la abriste con `readFile`, o si la mandaste a `decide` DENTRO del "
        f"array `images` —nombrarla en el `state` con {{{{image:...}}}} y no "
        f"mandarla es una pregunta sobre algo que el modelo no ha visto, y "
        f"te contesta igual—. Ábrelas y escribe lo que haya en ellas, o "
        f"quítalas del documento. Describir una pantalla que no has visto "
        f"sale igual de bien escrito que describir una que sí, y la persona "
        f"lo descubre siguiendo el procedimiento."]}
