"""Lo que el escriba sabe, en el momento de despertar.

Se expande en la persona con `{{!py:scripts/inventario.py}}`: corre en la
preparación de la sesión, ANTES del primer turno, y su salida entra en el
prompt del sistema.  Sin round-trip al modelo y sin herramientas nuevas
en el esquema — que importa aquí, porque cada herramienta del esquema es
un nombre que el modelo de voz puede leer en voz alta.

POR QUÉ NO BASTA CON LA INYECCIÓN AUTOMÁTICA DE MEMORIAS.  El plugin ya
inyecta pistas «💡 Available Memories», pero `enrich_prompt` las elige
POR PALABRAS CLAVE del prompt (memory/plugin.py:847-861).  El turno que
abre la sesión es una acotación —«la sesión se abre»— sin ningún término
del dominio, así que no engancha con nada y el escriba despertaría ciego.
Para poder proponer un tema con huecos hay que ponerle el inventario
delante, y eso es esto.

REPARTO.  Aquí se calcula lo que es CONTABLE — qué temas hay, cuántas
piezas, cuándo se tocó cada uno por última vez.  Cuál de ellos está flojo
lo juzga el escriba: es una valoración, no una cuenta.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

#: Cuántos temas finos se detallan con sus descripciones.  Bastantes para
#: que el escriba pueda elegir, pocos para no llenarle el prompt.
TEMAS_DETALLADOS = 4
#: Cuántas descripciones se muestran de cada tema fino.
PIEZAS_POR_TEMA = 3


def _antiguedad(iso: str | None) -> tuple[float, str]:
    """(días desde entonces, cómo se dice) para un timestamp ISO."""
    if not iso:
        return (float("inf"), "sin fecha")
    t = datetime.fromisoformat(iso)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    dias = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    if dias < 1:    return (dias, "hoy")
    if dias < 2:    return (dias, "ayer")
    if dias < 14:   return (dias, f"hace {int(dias)} días")
    if dias < 60:   return (dias, f"hace {int(dias / 7)} semanas")
    return (dias, f"hace {int(dias / 30)} meses")


def render(context, args) -> str:
    plugin = context.registry.get_plugin("memory")
    if plugin is None:
        raise RuntimeError(
            "inventario: el plugin 'memory' no está cargado; el escriba no "
            "puede despertar sin saber qué sabe.")
    storage = plugin._storage
    if storage is None:
        raise RuntimeError(
            "inventario: el almacén de memoria no está inicializado "
            "(set_workspace_path no llegó a correr).")

    memorias = [m for m in storage.load_curated()
                if getattr(m, "maturity", "") == "validated"]

    if not memorias:
        return ("NADA. No sabes absolutamente nada de esta persona: es la "
                "primera vez que despiertas, o aún no se ha validado nada de "
                "lo que anotaste.\n\n"
                "No tienes NINGÚN tema que ofrecer. No nombres ninguno — "
                "cualquier tema que se te ocurra ahora te lo estarías "
                "inventando, porque no hay nada de donde sacarlo. Saluda, di "
                "que empezáis de cero, y pregunta por dónde quiere empezar.")

    por_tema: dict[str, list] = defaultdict(list)
    for m in memorias:
        for t in (getattr(m, "tags", None) or ["sin-tema"]):
            por_tema[t].append(m)

    def ultima(ms) -> tuple[float, str]:
        return min((_antiguedad(getattr(m, "timestamp", None)) for m in ms),
                   key=lambda p: p[0])

    filas = sorted(((t, len(ms), ultima(ms)) for t, ms in por_tema.items()),
                   key=lambda r: (r[1], -r[2][0]))

    ancho = max(len(t) for t, _, _ in filas)
    lineas = [f"{len(memorias)} piezas validadas. Esta es la lista COMPLETA "
              f"de lo que sabes de esta persona: un tema que no salga aquí no "
              f"te lo ha contado nunca, así que no lo nombres.", ""]
    for tema, n, (_, cuando) in sorted(filas, key=lambda r: -r[1]):
        lineas.append(f"    {tema.ljust(ancho)}   {n:>3}   {cuando}")

    finos = [f for f in filas if f[1] <= max(2, filas[0][1])][:TEMAS_DETALLADOS]
    if finos:
        lineas += ["", "Lo más flojo, por si quieres tirar de ahí:", ""]
        for tema, n, (_, cuando) in finos:
            lineas.append(f"  · {tema} — {n} pieza(s), lo último {cuando}:")
            for m in por_tema[tema][:PIEZAS_POR_TEMA]:
                lineas.append(f"      «{getattr(m, 'description', '')}»")

    return "\n".join(lineas)
