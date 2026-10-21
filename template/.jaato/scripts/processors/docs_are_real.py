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

import yaml

from jaato_sdk.cascade_authoring import ProcessorResult

#: `[texto](destino)` — sólo enlaces relativos; http(s), mailto y anclas no
#: se comprueban aquí (los externos los verifica el juez al catalogarlos).
LINK = re.compile(r"\[[^\]]*\]\(([^)#][^)]*)\)")

#: Where the store path COMES FROM, rather than what it is.  This file
#: used to carry `.jaato/memory/escriba` as a constant while every
#: profile said `.jaato/memories`, so `_known_ids` read an empty
#: directory and every memory citation was reported invented.
#:
#: MEASURED, 2026-10-21, documentalista session 20261021_160428.  The
#: documenter cited `mem_20261020_204340_4691`, which `retrieve_memories`
#: had returned to it in that same session and which is on disk in
#: `curated.jsonl`.  This gate refused it three times; the refusal says
#: "cita solo memorias que hayas recuperado de verdad", so the model went
#: and recuperated it again -- 71 calls of `retrieve_memories` -- until
#: the 100-tool-call ceiling ended the session `budget_exhausted` with
#: the document written and never signed off.  The person was told the
#: document had not been completed, and nothing could say why.
#:
#: The id set was not EMPTY, which is what made it look reasonable: the
#: catalogue path was right, so references validated and only memories
#: failed.  The refusal named one bad id beside several good ones.
#:
#: THIS IS THE SECOND TIME THE SAME CONTRACT BROKE IN HALF.  On
#: 2026-10-09 the escriba moved to `.jaato/memories` and the curator
#: stayed behind; `_base_documentalista.yaml` carries that account and
#: `tests_workspace.py` grew a guard for it.  The guard compares
#: PROFILES to each other and this file is a script, so it was out of
#: scope and sat wrong for twelve days.  Both halves are fixed now: the
#: path is read rather than repeated, and the guard covers scripts.
PROFILE = Path(".jaato/profiles/_base_documentalista.yaml")

CATALOGUE = Path(".jaato/references")


def _store_root(ws: Path) -> Path:
    """El directorio del almacén, resuelto como lo resuelve el framework.

    The documenter's OWN profile, because this gates the documenter and
    that is the store its `memory` plugin was configured with.  Every
    profile that names a store names the same one -- `tests_workspace.py`
    refuses a template where they disagree -- so reading one is reading
    the contract.

    `MemoryStorage` accepts a `.jsonl` path for backwards compatibility
    but treats it as a ROOT: `x.jsonl` becomes the directory `x/` holding
    `raw/` and `curated.jsonl` (storage.py:250-270, jaato#912).  Mirrored
    here, as `memory._store_root` mirrors it, so this does not depend on
    how the path happens to be spelled.

    NOTHING IS GUESSED IF THE PROFILE CANNOT BE READ.  This raises, and
    a processor that raises is a FAULT rather than a refusal: the
    framework blocks it once, exempt from the refusal budget, and is
    advisory afterwards.  A gate that fell back to a path of its own is
    how the twelve days above happened.
    """
    profile = yaml.safe_load((ws / PROFILE).read_text())
    declared = profile["plugin_configs"]["memory"]["storage_path"]
    root = ws / declared
    return root.parent / root.stem if root.suffix == ".jsonl" else root


def _known_ids(ws: Path) -> set:
    """Ids que existen de verdad: memorias (crudas y curadas) y referencias."""
    ids = set()
    store = _store_root(ws)
    raw = store / "raw"
    if raw.is_dir():
        for f in raw.glob("*.json"):
            try:
                ids.add(json.loads(f.read_text())["id"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
    curated = store / "curated.jsonl"
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
