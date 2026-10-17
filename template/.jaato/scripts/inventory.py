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

import pathlib
from collections import defaultdict
from datetime import datetime, timezone

#: How many thin topics get detailed with their descriptions.  Enough for
#: the scribe to choose from, few enough not to flood its prompt.
DETAILED_TOPICS = 4
#: How many RECENT topics get the same treatment.  Fewer, because what
#: the person was last doing is one or two things, not four.
RECENT_TOPICS = 3
#: How many descriptions are shown per detailed topic.
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


#: What the person can SHOW, as opposed to tell.
#:
#: HERE AND NOT IN THE PERSONA, because it is a count and the persona
#: cannot hold one.  The scribe has to be able to say "¿me lo enseñas?"
#: instead of "cuéntamelo", and it cannot offer a capability it was
#: never told exists -- measured 2026-10-17, when a person said they
#: wanted to show an application and the scribe answered by asking which
#: aspect they would like to DESCRIBE first.
def _demos(context) -> str:
    """How many demonstrations are recorded, and that there can be more."""
    offer = ("La persona puede ENSEÑARTE algo en vez de contártelo: hay un "
             "botón para grabar una demostración —su pantalla y lo que vaya "
             "explicando mientras maneja la aplicación—, y cuando la termina "
             "se documenta sola. Si lo que quiere es mostrarte cómo se hace "
             "algo, ofrécelo: «si quieres, grábamelo y lo documento».")
    root = getattr(context, "workspace_path", None)
    if not root:
        return offer
    folder = pathlib.Path(root) / "demo"
    if not folder.is_dir():
        return offer
    # A demonstration is finished when its manifest exists; one still
    # recording is not something to count or to offer to document.
    done = [d for d in folder.iterdir() if (d / "demo.json").is_file()]
    if not done:
        return offer
    # NAMED, NOT COUNTED.  The scribe cannot list a directory and nor can
    # the documentalista, so "they are in demo/" is not an address either
    # of them can act on: a demonstration it cannot name is one it cannot
    # be asked to document.  The newest few, because this text is read on
    # every waking and a person with fifty recordings should not pay for
    # the other forty-five.
    names = sorted(d.name for d in done)
    shown = names[-5:]
    more = ("" if len(names) == len(shown)
            else f" (y {len(names) - len(shown)} más, antiguas)")
    return (f"{offer}\n\nYa te ha grabado {len(done)} demostración(es). "
            f"Las últimas: {', '.join('demo/' + n for n in shown)}{more}. "
            "El índice de capturas de cada una es «<carpeta>/frames.jsonl»; "
            "ESE es el camino que le pasas al `documentalista`, porque no "
            "tiene con qué listar una carpeta.")


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
        # THE CAPABILITY SURVIVES THE EMPTY CASE. Knowing nothing about
        # the person is exactly when being shown something is worth
        # most, and this branch returns before everything below it.
        return ("NADA. No sabes absolutamente nada de esta persona: es la "
                "primera vez que despiertas, o aún no se ha validado nada de "
                "lo que anotaste.\n\n"
                "No tienes NINGÚN tema que OFRECER — cualquiera que se te "
                "ocurra ahora te lo estarías inventando. Pero puedes "
                "aprender lo que sea: saluda, di que empezáis de cero, y "
                "pregunta por dónde quiere empezar.\n\n" + _demos(context))

    by_topic: dict[str, list] = defaultdict(list)
    for m in memories:
        for t in (getattr(m, "tags", None) or ["sin-tema"]):
            by_topic[t].append(m)

    def newest(ms) -> tuple[float, str]:
        return min((_age(getattr(m, "timestamp", None)) for m in ms),
                   key=lambda p: p[0])

    rows = sorted(((t, len(ms), newest(ms)) for t, ms in by_topic.items()),
                  key=lambda r: (r[1], -r[2][0]))

    def pieces(topic: str) -> list:
        """That topic's memories, newest first."""
        return sorted(by_topic[topic],
                      key=lambda m: _age(getattr(m, "timestamp", None))[0])

    def detail(lines: list, chosen: list) -> None:
        for topic, n, (_, when) in chosen:
            lines.append(f"  · {topic} — {n} pieza(s), lo último {when}:")
            for m in pieces(topic)[:PIECES_PER_TOPIC]:
                lines.append(f"      «{getattr(m, 'description', '')}»")

    width = max(len(t) for t, _, _ in rows)
    lines = [f"{len(memories)} piezas validadas. Esto es todo lo que YA te "
             f"han contado: si un tema no sale aquí, no lo menciones como si "
             f"lo supieras.", "",
             "No es una lista de temas permitidos. Es lo que ya sabes, no el "
             "límite de lo que puedes aprender: cuando te cuenten algo que no "
             "está aquí, eso es exactamente para lo que existes.", ""]
    # NEWEST FIRST, not biggest first.  The table used to lead with the
    # topic holding the most pieces, which is the one least likely to be
    # what the conversation was about an hour ago.
    by_recency = sorted(rows, key=lambda r: r[2][0])
    for topic, n, (_, when) in by_recency:
        lines.append(f"    {topic.ljust(width)}   {n:>3}   {when}")

    # WHAT YOU WERE DOING, detailed rather than listed.  The table above
    # carries a name and a count, and a name is not enough to say "shall
    # we carry on with X" and mean anything by it.  Measured 2026-10-11:
    # every greeting offered the same thin topic from the day before,
    # because the thin section was the only part of this inventory with
    # any substance in it, and the day's own work -- several pieces stored
    # that afternoon -- was a row in a table.
    recent = by_recency[:RECENT_TOPICS]
    if recent:
        lines += ["", "Lo último que me contaste, que es por donde "
                      "seguíais:", ""]
        detail(lines, recent)

    # AND WHAT IS MISSING, which is the other thing a second brain is for
    # and nothing else here does.  Kept, but no longer the only thing it
    # wakes up knowing anything about.  A topic already detailed above is
    # not repeated.
    named = {t for t, _, _ in recent}
    thin = [r for r in rows
            if r[1] <= max(2, rows[0][1]) and r[0] not in named][:DETAILED_TOPICS]
    if thin:
        lines += ["", "Lo más flojo, por si quieres tirar de ahí:", ""]
        detail(lines, thin)

    lines += ["", _demos(context)]
    return "\n".join(lines)
