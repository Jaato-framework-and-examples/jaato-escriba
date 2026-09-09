"""Ningún candidato se queda sin juzgar.

Gatea `signal_completion` del juez: cada URL que se le dio tiene que
salir en `aceptadas` o en `descartadas`, exactamente en una.

POR QUÉ HACE FALTA UN GATE Y NO BASTA LA PERSONA.  Sin esto, la manera
más barata de contestar es rendir dos aceptadas y callar sobre las ocho
restantes: valida contra el esquema, parece un trabajo hecho, y las ocho
desaparecen sin que nadie sepa si se miraron. Y como la lista de
descartadas es lo que impide volver a juzgar la misma URL mañana, un
descarte que no se escribe se paga en cada búsqueda futura.

La lista de candidatos NO se lee del payload —que es lo que el modelo
escribe y por tanto lo que podría maquillar— sino de `agent_params`, que
es lo que el driver le pasó al crear la sesión.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from jaato_sdk.cascade_authoring import ProcessorResult


def _urls(items: Any) -> List[str]:
    return [str(x.get("url", "")).strip()
            for x in (items or []) if isinstance(x, dict)]


def validate(payload: Dict[str, Any], context: Any) -> ProcessorResult:
    # `agent_params` es Dict[str, str] por contrato, así que los
    # candidatos llegan como JSON y se parsean. UNA sola ruta: si un día
    # llegara ya como lista, esto fallaría ruidosamente en vez de
    # recorrer caracteres en silencio, que es como se descubrió.
    crudo = (getattr(context, "agent_params", None) or {}).get("urls")
    try:
        dados = [str(u).strip() for u in json.loads(crudo)] if crudo else []
    except (TypeError, ValueError) as exc:
        return {"faults": [
            f"agent_params['urls'] no es JSON parseable ({type(exc).__name__}), "
            f"así que no se puede comprobar la cobertura. Es un fallo de quien "
            f"creó esta sesión, no tuyo: no lo arregla otro intento."
        ]}
    if not dados:
        # Sin candidatos declarados no hay nada que cuadrar. No es un fallo
        # del juez: es que el driver no los pasó, y no lo puede arreglar él.
        return {"faults": [
            "agent_params no trae `urls`, así que no se puede comprobar que "
            "hayas juzgado todos los candidatos. Es un fallo de quien creó "
            "esta sesión, no tuyo: no lo arregla otro intento."
        ]}

    aceptadas, descartadas = _urls(payload.get("aceptadas")), _urls(payload.get("descartadas"))
    vistas = aceptadas + descartadas
    errores: List[str] = []

    faltan = [u for u in dados if u not in vistas]
    if faltan:
        errores.append(
            f"{len(faltan)} candidato(s) sin juzgar: {', '.join(faltan[:5])}"
            f"{' …' if len(faltan) > 5 else ''}. Cada URL de la lista tiene "
            f"que salir en `aceptadas` o en `descartadas`. Si no vale, "
            f"descártala con su motivo — descartar es una respuesta, callar no."
        )

    repetidas = [u for u in set(vistas) if vistas.count(u) > 1]
    if repetidas:
        errores.append(
            f"{len(repetidas)} URL(s) en las dos listas a la vez: "
            f"{', '.join(repetidas[:5])}. Cada una va en una sola."
        )

    inventadas = [u for u in vistas if u not in dados]
    if inventadas:
        errores.append(
            f"{len(inventadas)} URL(s) que no estaban entre los candidatos: "
            f"{', '.join(inventadas[:5])}. Copia las URLs tal cual de la "
            f"lista; no las retoques ni añadas otras."
        )

    return {"errors": errores}
