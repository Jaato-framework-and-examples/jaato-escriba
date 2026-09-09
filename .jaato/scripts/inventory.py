"""What the scribe knows, at the moment of waking.

Expanded into the persona with `{{!py:scripts/inventory.py}}`: it runs
during session preparation, BEFORE the first turn, and its output goes
into the system prompt. No round-trip to the model and no new tools in the
schema — which matters here, because every tool in the schema is a name
the voice model may read out loud.

WHY THE AUTOMATIC MEMORY INJECTION IS NOT ENOUGH.  The plugin already
injects "💡 Available Memories" hints, but `enrich_prompt` chooses them BY
KEYWORD from the prompt (memory/plugin.py:847-861).  The turn that opens
the session is a stage direction — "the session opens" — with no domain
term in it, so it matches nothing and the scribe would wake up blind.  To
be able to propose a topic with gaps it needs the inventory put in front
of it, and that is this.

THE SPLIT.  What is COUNTABLE is computed here — which topics exist, how
many pieces, when each was last touched.  Which of them is thin is judged
by the scribe: that is an assessment, not a count.

The returned text is Spanish because the scribe reads it and speaks
Spanish; the code around it is English, like the rest of the repo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

#: How many thin topics get detailed with their descriptions.  Enough for
#: the scribe to choose from, few enough not to flood its prompt.
DETAILED_TOPICS = 4
#: How many descriptions are shown per thin topic.
PIECES_PER_TOPIC = 3


def _age(iso: str | None) -> tuple[float, str]:
    """(days since then, how it is said) for an ISO timestamp."""
    if not iso:
        return (float("inf"), "sin fecha")
    t = datetime.fromisoformat(iso)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
    if days < 1:    return (days, "hoy")
    if days < 2:    return (days, "ayer")
    if days < 14:   return (days, f"hace {int(days)} días")
    if days < 60:   return (days, f"hace {int(days / 7)} semanas")
    return (days, f"hace {int(days / 30)} meses")


def render(context, args) -> str:
    plugin = context.registry.get_plugin("memory")
    if plugin is None:
        raise RuntimeError(
            "inventory: the 'memory' plugin is not loaded; the scribe "
            "cannot wake up without knowing what it knows.")
    storage = plugin._storage
    if storage is None:
        raise RuntimeError(
            "inventory: the memory store is not initialised "
            "(set_workspace_path never ran).")

    memories = [m for m in storage.load_curated()
                if getattr(m, "maturity", "") == "validated"]

    if not memories:
        return ("NADA. No sabes absolutamente nada de esta persona: es la "
                "primera vez que despiertas, o aún no se ha validado nada de "
                "lo que anotaste.\n\n"
                "No tienes NINGÚN tema que ofrecer. No nombres ninguno — "
                "cualquier tema que se te ocurra ahora te lo estarías "
                "inventando, porque no hay nada de donde sacarlo. Saluda, di "
                "que empezáis de cero, y pregunta por dónde quiere empezar.")

    by_topic: dict[str, list] = defaultdict(list)
    for m in memories:
        for t in (getattr(m, "tags", None) or ["sin-tema"]):
            by_topic[t].append(m)

    def newest(ms) -> tuple[float, str]:
        return min((_age(getattr(m, "timestamp", None)) for m in ms),
                   key=lambda p: p[0])

    rows = sorted(((t, len(ms), newest(ms)) for t, ms in by_topic.items()),
                  key=lambda r: (r[1], -r[2][0]))

    width = max(len(t) for t, _, _ in rows)
    lines = [f"{len(memories)} piezas validadas. Esta es la lista COMPLETA "
             f"de lo que sabes de esta persona: un tema que no salga aquí no "
             f"te lo ha contado nunca, así que no lo nombres.", ""]
    for topic, n, (_, when) in sorted(rows, key=lambda r: -r[1]):
        lines.append(f"    {topic.ljust(width)}   {n:>3}   {when}")

    thin = [r for r in rows if r[1] <= max(2, rows[0][1])][:DETAILED_TOPICS]
    if thin:
        lines += ["", "Lo más flojo, por si quieres tirar de ahí:", ""]
        for topic, n, (_, when) in thin:
            lines.append(f"  · {topic} — {n} pieza(s), lo último {when}:")
            for m in by_topic[topic][:PIECES_PER_TOPIC]:
                lines.append(f"      «{getattr(m, 'description', '')}»")

    return "\n".join(lines)
