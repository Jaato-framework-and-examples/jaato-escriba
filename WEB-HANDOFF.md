# escriba on the web — design handoff

For a design session. **This document does not design the UI.** It states what
the thing is, what it must show, what a person must be able to do, and which
decisions are already made and are not open. The visual and interaction design
is the design session's work.

The TUI (`--tui`, `richboard.py`) stays as legacy and keeps working. This is a
new front end beside it, not a replacement of the driver.

---

## 1. What the escriba is

A voice-driven "second brain". You talk to it; it asks questions back, out
loud, in peninsular Spanish. What it is told becomes memory; what it judges
worth keeping becomes a curated memory; topics it has brushed against get
searched outside and catalogued as references; and when a subject is ripe it
commissions a subagent that writes a markdown document about it.

It is a **voice** application first. Text is the transcript of a conversation
that happened out loud, not the medium.

Four agents, one per concern: `escriba` (the conversation), `curator`
(consolidates raw memories), `juez` (judges whether outside results are any
good), `documentalista` (writes the documents). Only the first talks to the
person.

---

## 2. Decided already — not open for design

These are settled; the design works inside them.

| | |
|---|---|
| **Shape** | BFF. A FastAPI process is the **only** jaato SDK client, over the daemon's unix socket (`IPCClient`, `ClientType.API`). The browser never reaches the daemon. |
| **Live updates** | SSE (`text/event-stream`), fan-out hub for state + one SSE response per turn for streamed output. |
| **Auth** | Not in the app. Caddy → oauth2-proxy → Keycloak in front; the app never sees or refreshes a token. |
| **No build step** | One static `index.html`. React and anything else as ESM imports from a CDN. No npm, no bundler, no node_modules. |
| **Audio archive** | Server-side, always. Both halves of every exchange are kept for auditing (`archive.py`). This is a deliberate decision and it does not move to the browser. |
| **Driver stays Python** | `memory.py`, `enrichment.py`, `archive.py`, the completion gates, the subagent spawn and the budget profiles are untouched. |

Why this shape: `board.py` already separates *what the conversation looks like
from outside* from *how it is drawn* — four implementations today (`NullBoard`,
`LineBoard`, `StateBoard`, `RichBoard`). The web front end is a **fifth board**
feeding the SSE hub. The data contract in §4 is therefore not invented for the
web; it already exists and is already tested headless.

---

## 3. Mirror the kb wiki's visual design

Same host, same user, and the design should read as the same family.

**Read these two files as the source of truth:**

- `/home/apanoia/Sources/Jaato-framework-and-examples/kb-for-coding-patterns-wiki/kbwiki/ui/static/index.html` — lines 15–251 are the whole style system.
- `.../kb-for-coding-patterns-wiki/docs/dashboard-redesign.md` — its own 114-line design spec, which is the output of a handoff like this one. Worth reading for format as much as content.

What is there, so you know what you are mirroring:

- **Tokens as CSS custom properties on `:root`.** Semantic roles, not colour
  names: `--bg --panel --line --line-strong --ink --ink-2 --muted --accent
  --accent-ink --accent-soft --accent-tint --wait --wait-soft --ok --ok-soft
  --bad --bad-soft --chip`.
- **Three-state theming.** Bare `:root` is light; `@media (prefers-color-scheme:
  dark)` guarded as `:root:not([data-theme="light"])`; and `:root[data-theme="dark"]`
  so an explicit choice wins in both directions. A reader toggle in the top bar.
- **Type.** `IBM Plex Sans` with a system fallback stack; `IBM Plex Mono` for
  monospace. Accent `#2F4FC9` light, `#7B9CFF` dark.
- **Component vocabulary** already in use: `card` / `card-head`, `chip`, `pill`,
  `dot`, `field`, `iconbtn`, `legend`, `md`, `mono`, `muted`, and state classes
  `ok` / `bad` / `wait`.

Mirror the system, not the screens — kbwiki is a pipeline dashboard and this is
a conversation. Where the escriba needs something kbwiki has no equivalent for
(§5: the microphone, playback, the audio id), design it in that language.

---

## 4. The state the UI renders

This is `board.Conversation`, which exists today. Field names are given because
the SSE payload should not rename them for no reason.

**The conversation** — `entries`, a bounded deque (400) of:

| field | |
|---|---|
| `at` | timestamp |
| `kind` | `said` (the person) · `spoke` (the escriba) · `anotado` (what it noted) · `note` (machinery: searching, wrote, …) |
| `text` | the words; empty for `said`, which carries only duration |
| `seconds` | length of a `said` recording |
| `audio` | the `att_…` id, when there is a recording |
| `repeats` | consecutive identical lines fold into a count rather than repeating |

**Counts, with movement since the session opened** — `curated`, `raw`,
`curated_at_start`, `raw_at_start`, `catalogue`, `found`, `selected`,
`documents`. The delta is load-bearing: `8` does not say whether a queue is
draining or filling, and a run that silently stored 83 duplicates looked
exactly like a healthy one until someone counted.

**Three collections** — `items`, keyed by panel: `memoria`, `referencias`,
`documentos`. Each row carries at least `text` and `at`; memories add `id`,
`tags`, `content`, `confidence`, `uses`; references add `url`; documents add
`path`. **References carry no timestamp** — adding one needs a field at
catalogue time in `enrichment._catalogue()`.

**Status, one at a time** — `listening`, `thinking`, `speaking`, `searching`
(carries the query). Plus `archive_dir`, where this run's audio is being kept.

Panels are fed by a 2-second disk reconciliation, not by events, because the
writers are elsewhere: the curator has its own session, the documenter is a
subagent, and everything from previous days was written before this process
started. A view fed only by events is missing exactly what it is for.

---

## 5. What a person must be able to do

Requirements, not designs.

1. **Talk.** Push to talk: begin recording, end it, and know unambiguously
   which state it is in. In the browser this is `getUserMedia` +
   `MediaRecorder`; it replaces PulseAudio and the wraith bridge entirely.
2. **Hear the reply**, and know when audio is playing versus when the escriba
   is still thinking. These were confused in the TUI and it mattered.
3. **Read the transcript** as it arrives, streamed. A reply is a paragraph, not
   a line; it wraps.
4. **See what it has learnt** — the three collections and their counts, with
   movement since the session opened.
5. **Open any item** and read its detail — a memory's body, tags, confidence
   and use count; a reference's URL.
6. **Read a document.** The documentalista writes markdown; it must be
   readable in the page. (The TUI hands the terminal to `leaf`; the web has no
   such excuse.)
7. **Replay any recording**, from the transcript, by its `att_…` id — both what
   the person said and what the escriba said. This is the audit surface: the id
   is a sha256 prefix of the bytes, so a recording can be shown to match what
   the session received.
8. **Move around by time.** Conversations run long; a person needs to get back
   to "yesterday" without scrolling through everything.
9. **Know when something is wrong** — a session ended by a budget ceiling, a
   muted microphone, a failed playback. Silence is the failure mode to avoid:
   under the TUI a silent audio failure looked exactly like working audio.

---

## 6. Constraints worth knowing before designing

- **Spanish, user-facing.** Everything a person reads in the app is peninsular
  Spanish, as the TUI is (`escuchando`, `pensando…`, `memoria`, `referencias`,
  `documentos`). Code, comments and this document are English.
- **Status is exclusive.** One of listening / thinking / speaking / searching,
  never two. The TUI's status line got this wrong once and read "pensando…"
  through a whole closing drain.
- **Latency is real.** Model audio can arrive seconds after its transcript.
  Whatever the design does about "speaking", it must not assume text and audio
  are simultaneous.
- **Repetition is signal.** A loop emitting one line over and over is
  information. `repeats` folds it; do not just let it scroll.
- **Long conversations.** 400 entries live in the view; the durable record is
  the per-run manifest under `audio/`, which is complete.
- **Two voices, asymmetric.** The person's turns have duration and no text
  until transcribed; the escriba's have text and often audio. They are not
  mirror images and probably should not look like they are.

---

## 7. Open — the design session should decide or ask

1. **One screen or several?** kbwiki has numbered screens. The escriba may be
   one conversation screen with the collections alongside, or a conversation
   plus a knowledge area. Not predetermined.
2. **Where does the microphone live** and what makes its state unmistakable at
   a glance, hands-free, while a person is talking rather than looking.
3. **How a recording is offered for replay** inline in a transcript without
   turning every line into a player.
4. **Whether the audit id is visible** — `att_7f3c…` is meaningful to an
   auditor and noise to everyone else.
5. **Date navigation** (§5.8) — the TUI never got this built, so there is no
   precedent to mirror and no existing design to honour.
6. **Playback location.** Model speech could play in the browser (lower
   latency, removes the bridge that once added 30 seconds) or stay server-side.
   Browser playback moves the *heard* timestamp client-side, and that timestamp
   is part of what the archive records for an auditor. This is a real
   trade-off, not an oversight.

---

## 8. Pointers

| | |
|---|---|
| this repo | `/home/apanoia/Sources/Jaato-framework-and-examples/jaato-escriba` |
| the data contract | `board.py` — `Conversation`, `Entry`, `PANELS` |
| today's renderer, for reference only | `richboard.py` |
| the driver | `run_escriba.py` |
| the audio archive and its manifest | `archive.py`, and `audio/<stamp>/manifest.jsonl` |
| what everything means and why | `README.md` (long, and worth it) |
| the stack to mirror | `../kb-for-coding-patterns-wiki/kbwiki/ui/` |
