"""What the documenter claims it wrote has to be on disk, and its citations real.

Gates `signal_completion` for the documenter. Four checks, all mechanical:

1. every path in `ficheros` exists
2. `raiz` is one of them
3. every relative markdown link inside those files resolves to a file
4. every id in `fuentes` is a memory or a reference that EXISTS

WHY A GATE.  "Accurate documentation" is not checkable in general — but
the three ways it goes wrong here are.  A model that writes prose and
reports paths it never created, a tree whose pages link to each other by
guessed filenames, and a citation to `mem_20260910_0012` when no such
memory exists all pass a schema and read perfectly.  The fourth check is
the one that matters most: a fabricated citation is worse than no citation,
because it looks like provenance.

Nothing here judges the PROSE.  That is not mechanical and this does not
pretend otherwise; what it guarantees is that the artefact exists, hangs
together, and points at real sources.

Error strings are Spanish: the documenter reads them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from jaato_sdk.cascade_authoring import ProcessorResult

#: `[texto](destino)` — sólo enlaces relativos; http(s), mailto y anclas no
#: se comprueban aquí (los externos los verifica el juez al catalogarlos).
LINK = re.compile(r"\[[^\]]*\]\(([^)#][^)]*)\)")

MEMORY_STORE = Path(".jaato/memory/escriba")
CATALOGUE = Path(".jaato/references")


def _known_ids(ws: Path) -> set:
    """Ids que existen de verdad: memorias (crudas y curadas) y referencias."""
    ids = set()
    raw = ws / MEMORY_STORE / "raw"
    if raw.is_dir():
        for f in raw.glob("*.json"):
            try:
                ids.add(json.loads(f.read_text())["id"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
    curated = ws / MEMORY_STORE / "curated.jsonl"
    if curated.is_file():
        for line in curated.read_text().splitlines():
            if line.strip():
                try:
                    ids.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    cat = ws / CATALOGUE
    if cat.is_dir():
        ids |= {f.stem for f in cat.glob("auto-*.json")}
    return ids


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    ws = Path(getattr(context, "workspace_path", "") or ".")
    errors: List[str] = []

    files = [str(f).strip() for f in (payload.get("ficheros") or [])]
    missing = [f for f in files if not (ws / f).is_file()]
    if missing:
        errors.append(
            f"{len(missing)} fichero(s) que dices haber escrito no están: "
            f"{', '.join(missing[:5])}. Escríbelos de verdad, o quítalos de "
            f"`ficheros`. Decir que existe algo que no existe deja al escriba "
            f"ofreciéndole a una persona un documento que no puede abrir."
        )

    root = str(payload.get("raiz") or "").strip()
    if root and root not in files:
        errors.append(
            f"`raiz` ({root}) no está en `ficheros`. La raíz es por donde se "
            f"empieza a leer: tiene que ser uno de los ficheros escritos."
        )

    # enlaces internos: cada destino relativo tiene que existir
    broken = []
    for f in files:
        p = ws / f
        if not p.is_file():
            continue
        for target in LINK.findall(p.read_text(errors="replace")):
            t = target.strip()
            if t.startswith(("http://", "https://", "mailto:")):
                continue
            if not (p.parent / t).exists():
                broken.append(f"{f} -> {t}")
    if broken:
        errors.append(
            f"{len(broken)} enlace(s) interno(s) no llevan a ninguna parte: "
            f"{', '.join(broken[:5])}. Un árbol de documentos con enlaces "
            f"roto es peor que un documento solo: enlaza a los ficheros que "
            f"has escrito, con la ruta relativa correcta."
        )

    known = _known_ids(ws)
    cited = [str(s).strip() for s in (payload.get("fuentes") or [])]
    invented = [c for c in cited if c not in known]
    if invented:
        errors.append(
            f"{len(invented)} id(s) citados que no existen: "
            f"{', '.join(invented[:5])}. Cita solo memorias y referencias que "
            f"hayas recuperado de verdad — el id viene en lo que te devuelve "
            f"`retrieve_memories` o `listReferences`. Una cita inventada es "
            f"peor que ninguna, porque parece procedencia."
        )

    return {"errors": errors}
