"""Qué quedó sin consolidar de la última vez.

Nada de esto habla con el SDK: mira el almacén en disco y cuenta. Vive
fuera de `run_escriba.py` por lo mismo que `voice.py` — el driver enseña
dos sesiones y tres `ask`, no fontanería.

POR QUÉ CONTAR EN CRUDO Y NO UNA BANDERA.  Una bandera de «la última
sesión terminó bien» habría que escribirla al abrir y borrarla al
consolidar, y se desincroniza sola: un `kill -9` entre medias la deja
mintiendo. Las memorias en crudo SON la condición, no una señal de ella —
si hay alguna es que nadie la ha juzgado todavía, sin importar cómo
acabara la sesión anterior.

Y coge un caso que la bandera no ve: el curador juzga de ocho en ocho a
propósito, así que una sesión que terminó LIMPIA puede dejar cola si se
guardaron más de ocho cosas. Ahí no hubo ningún fallo y hay trabajo
pendiente igual.
"""
from __future__ import annotations

from pathlib import Path

import yaml

#: De dónde sale la ruta del almacén.  Del perfil y no de una constante
#: aquí, para que no haya dos sitios que decidan lo mismo: si se cambia
#: `storage_path`, esto lo sigue.
PERFIL = Path(".jaato/profiles/_base_escriba.yaml")


def _raiz(workspace: Path) -> Path:
    """El directorio del almacén, resuelto como lo resuelve el framework.

    `MemoryStorage` acepta un `.jsonl` por compatibilidad pero lo trata
    como RAÍZ: `x.jsonl` se vuelve el directorio `x/` con `raw/` y
    `curated.jsonl` dentro (storage.py:250-270, jaato#912). Se replica
    esa regla aquí para que el recuento no dependa de cómo esté escrita
    la ruta en el perfil.
    """
    perfil = yaml.safe_load((workspace / PERFIL).read_text())
    declarada = perfil["plugin_configs"]["memory"]["storage_path"]
    p = workspace / declarada
    return p.parent / p.stem if p.suffix == ".jsonl" else p


def sin_consolidar(workspace: Path) -> int:
    """Cuántas memorias esperan al curador.  0 si el almacén no existe."""
    raw = _raiz(workspace) / "raw"
    return len(list(raw.glob("*.json"))) if raw.is_dir() else 0
