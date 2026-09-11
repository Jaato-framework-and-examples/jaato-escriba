# jaato-escriba

**One person's second brain. It asks out loud, and next time it remembers
what it was told.**

```bash
python run_escriba.py                 # talk
python run_escriba.py --tui           # talk, with a live view
                                      #   j/k move between panels
                                      #   enter opens the full list
                                      #   esc closes it
python run_escriba.py --forget        # start over, forgetting everything
```

Push to talk. Ctrl-C says goodbye, and it consolidates on the way out.

> The agent speaks peninsular Spanish, so everything the model reads —
> personas, tier descriptions, completion-schema `description` fields — is
> written in Spanish. Docs, code and comments are English, like the
> sibling repos.

> **Status.** Working end to end: it converses, writes down what it is
> told, curates on the way out, searches outside for what it just learned,
> is offered those findings again when it writes on the same topic, and
> commissions a documenter that writes markdown from the memories on
> request. Eleven framework defects found building it are fixed and verified
> here; one remains open; see Provenance.

---

## The shape

```mermaid
flowchart TB
    you(["you"])

    subgraph S["escriba session · never completes"]
        direction TB
        voz["<b>voz</b> · openai/gpt-audio<br/>audio bidirectional<br/>hears and speaks"]
        esc["<b>escribano</b> · gpt-4o-mini<br/>exit_on: completion<br/>writes and looks up"]
        voz -- "enter_tier" --> esc
        esc -- "returns on its own" --> voz
    end

    mem[("the scribe's memory<br/>raw/ · curated.jsonl")]
    cur["<b>curator</b> · gpt-4o-mini<br/>validates or discards"]
    jz["<b>juez</b> · gpt-4o-mini<br/>is this result relevant?"]
    ref[("references<br/>auto-*.json")]
    doc["<b>documentalista</b> · gpt-4o-mini<br/>spawned ON REQUEST<br/>writes and reports"]
    md[/"docs/&lt;topic&gt;/*.md"/]

    you -- "audio/wav" --> voz
    voz -- "audio" --> you
    esc -- "store_memory (raw)" --> mem
    esc -. "gate: no memory, no turn" .-> esc
    mem -- "once the talk ends" --> cur
    cur -- "maturity: validated" --> mem
    mem -- "auto-injected on waking" --> voz

    mem -- "tags become search keys" --> jz
    jz -- "accepted" --> ref
    ref -- "enrich_tool_result" --> esc
    esc -- "the voice offers it" --> voz

    esc -- "spawn_subagent(profile)" --> doc
    mem -- "retrieve_memories" --> doc
    ref -- "selectReferences · once" --> doc
    doc -- "writeNewFile" --> md
    doc -. "gate: files exist, links resolve,<br/>cited ids exist" .-> doc
    md -- "the voice says where" --> voz
```

**The hand-off, measured.** `enter_tier(escribano)` is the real
mechanism — 62 switches across the sessions built so far. There is no
`exit_tier`: the way back is `exit_on: completion`, and it fires reliably
— 10 entries produced 10 automatic returns in the last verification,
exactly 1:1.

What went wrong was the model asking to come back anyway, from a tier the
framework had already left. 74% of `enter_tier(voz)` calls returned
`already_at_tier` — pure wasted round-trips, and before jaato#934 each one
could spend a nudge. A persona rule took that to 30%; the rest was my own
tier `description`, which said "para decir algo, vuelve a voz" and renders
verbatim into the `enter_tier` schema — so the text the model reads *while
choosing* was inviting the call the persona forbade. Fixed at that layer:
one such call in six turns.

**Why two tiers and not one.** The tool schema is session-wide; what the
tier changes is which model is at the wheel when the decision to call a
tool is made. An audio model ANNOUNCES the tool instead of invoking it —
measured in `jaato-cascade-audio-interchange`: it said *"voy a abrir el
parte"* and called nothing. And every name in the schema is a word it may
read out loud. So one hears and speaks, and another writes.

**Why the curator is indispensable.** Everything the scribe writes is born
RAW, and a raw memory is reachable by no tag search and is not injected on
waking (`list_memory_tags`: *"pending_curation … which no tag search can
reach"*). Without something to promote it to `validated`, the second brain
remembers nothing. The curator is not tidying: it is what makes
"remembers" true.

**Why the documenter is spawned and not driven.** Every other agent here
runs on a schedule the driver owns: the curator at the edges of the
conversation, the judge whenever a memory is stored. A document is
different — it is asked for, in the middle of talking, in words. So the
scribe spawns it, `spawn_subagent` returns immediately, and the
conversation carries on while it writes. Nothing in `run_escriba.py`
mentions it: the driver would have to poll or block, and both are worse
than letting the agent that took the request also place it.

That also decides where its rules live. The documenter's profile carries
its own tool subset, its own permission whitelist, its own persona
(`default_agent`) and its own ceiling — because the caller passes only a
`task`, and everything else has to be true of the profile before the call
is made.

**Why every turn now completes.** Declaring a
`completion_payload_schema` enables `signal_completion`, and until
2026-09-09 calling it left the session unusable:
[jaato#913](https://github.com/Jaato-framework-and-examples/jaato/issues/913)
(its answer was never written into history, so the next turn replayed a
`tool_call` with no response and the provider rejected it with a 400) and
[jaato#845](https://github.com/Jaato-framework-and-examples/jaato/issues/845)
(neither resume verb carried an attachment, so a voice session that
completed could never be spoken to again). Both are fixed — #915 and #914
— and both were verified here before this was switched on: three
consecutive completions on one session, and a completed session woken
with an audio attachment.

Completing every turn is the only thing that makes the scribe write. The
persona asked it to and it did not: measured, **0 tool calls across 5
turns and 5.6 minutes** of real conversation, because an audio model
announces a tool instead of invoking it. A completion processor reads the
tool-call ledger — which the model cannot rewrite — and refuses a turn
that stored nothing without saying why. With the gate: **4 memories
across 5 turns.** Prose is a suggestion; a gate is a contract.

It costs almost nothing: 1.40 s per turn with completion against 1.30 s
without, because the session stays warm rather than cold-restarting.

**And the gate has an escape hatch, deliberately.** Forcing a memory on
every turn would force one on "hello" and on "go on", and the only way to
satisfy that is to invent one — which for a second brain is the worst
possible failure, and has already happened once here. So the gate demands
a memory OR an explicit `nada_que_anotar: true` with a reason, which is
visible in the payload and therefore auditable.

**Why every turn needs a nudge.** The audio tier never calls
`signal_completion` unprompted: measured across 43 sessions, every turn
ends in text and waits to be re-prompted. So the nudge is load-bearing on
every turn rather than an exception path.

That was survivable until it met a second fact: the budget was per
SESSION. `_completion_nudges_fired` was zeroed only in `__init__`, and
`_begin_turn_completion_state` deliberately refused to reset it, because
resetting it once cost the framework its only bound on the nudge loop
(jaato#767 — a session that turned 735 times in 40 seconds). Sound
reasoning, resting on "a completion-gated session is one-shot by
construction" — true until #913/#915 made a completed session revivable,
which is exactly what lets this one be gated *and* multi-turn.

So one nudge per turn drained a session-lifetime budget and the
conversation died at turn N+1. That is
[jaato#934](https://github.com/Jaato-framework-and-examples/jaato/issues/934),
found here and fixed in #936 with the distinction the old code
approximated by never resetting: `_completion_nudge_turn_pending` is
latched when a nudge fires, so a **nudge-originated** turn keeps the
counter (the loop still terminates) while a turn the caller started clears
it (a conversation is not rationed). Verified: six turns, all six close,
with the budget at 2 — before the fix, turns 3-6 died.

The profile asks for 3: one for the ordinary "you have not closed the
turn", one spare for a turn where the memory gate rejects once and the
payload has to be fixed.

**And the payload no longer carries ceremony.** The schema used to require
`agent`, a `const` echoing which agent was completing — which the
framework already knows. The model kept omitting it: 22 rejections from
that alone, each costing a retry *and* a nudge. Removed; nothing
downstream read it.

Memories land either way. The driver still tolerates the remainder — the
memory is already on disk, the person has already heard the reply, and
killing a conversation over bookkeeping would trade the part that works
for the part that does not.

## One conversation, end to end

```mermaid
sequenceDiagram
    autonumber
    participant T as you
    participant D as run_escriba.py
    participant E as escriba session
    participant C as curator session
    participant M as documentalista<br/>(subagent, on request)

    D->>D: any memories left uncurated?
    opt yes
        Note over D,C: said out loud — not an invisible action
        D->>C: "judge whatever is raw"
        C-->>D: validated
    end

    D->>E: create session
    Note right of E: the inventory is rendered HERE:<br/>which is why the drain runs first
    E-->>T: greeting + double question · 3.8 s

    loop while somebody is there
        T->>D: push and talk
        D->>E: ask("", attachments=[wav])
        E-->>T: spoken reply
        opt "write me a document about X"
            E->>M: spawn_subagent(profile="documentalista")
            Note right of M: BACKGROUND — the conversation<br/>does not stop for it
            E-->>T: "I have commissioned it"
            M->>M: memories + references -> docs/X/*.md
            M-->>E: the paths it wrote
            E-->>T: what it contains, and where
        end
    end

    Note over T,D: Ctrl-C, or 120 s without starting to speak
    D->>C: "judge whatever is raw"
```

## Who opens the conversation

The driver does, with a stage direction — `[La sesión se abre…]` — and the
words belong to the persona. The scribe greets and asks **two questions at
once**, the only time it is allowed to: whether you want to teach it
something new, or whether you carry on with a specific topic **it picks
itself** from what it already knows, naming what it is missing about it.

To pick that topic it has to know what it knows on waking, and the
automatic memory injection cannot do it: `enrich_prompt` selects hints BY
KEYWORD from the prompt (`memory/plugin.py:847-861`), and a stage
direction has none. The inventory is computed before the first turn with a
prefetch:

    .jaato/agents/escriba.md      {{!py:scripts/inventario.py}}
    .jaato/scripts/inventario.py  render(context, args) -> str

It runs during session preparation, reaches the memory plugin through
`context.registry`, and renders the topics with their counts and their
age. No model round-trip and **no new tools in the schema**.

It counts what is COUNTABLE — which topics exist, how many pieces, when
each was last touched. Which one is thin is judged by the scribe, because
that is an assessment and not a count.

**The inventory is the complete list of what it knows, and it is told so.**
A worked example with realistic content in a persona is ammunition for
confabulation: the first version carried a sample greeting about
hydroponics, and with an empty inventory the model recited it and
embellished it — *"I noted you use a hydroponic system, but I don't know
which nutrients you add"* — inventing the user's life in its first
sentence. For a second brain that is the worst possible failure. Fixed by
removing the example (the shape is described in prose, which cannot be
recited) and putting the rule where the data is: each prefetch branch
states its own.

## What it finds outside

```mermaid
flowchart LR
    sm["store_memory<br/><i>tags = keys</i>"] --> obs["Observer<br/><i>inside the driver</i>"]
    obs --> ddg["DuckDuckGo<br/>8 candidates"]
    ddg --> judge["<b>juez</b><br/>one turn, one verdict"]
    judge -- "accepted" --> cat[/".jaato/references/auto-*.json"/]
    judge -- "discarded" --> disc[/"discarded_references.json<br/>so they are never re-judged"/]
    cat --> rel["references reload"]
    rel --> enr["enrich_tool_result<br/>matches tags"]
    enr --> esc2["the escribano sees it<br/>while writing on that topic"]
    esc2 --> offer["the voice OFFERS it:<br/>«¿le echo un ojo?»"]
```

The usual split: **searching and writing the catalogue is mechanical** and
happens in Python; **deciding whether a result is any good is a
judgement** and belongs to the judge. The catalogue JSON is not asked of
the model — an LLM drafting config files invents fields and drops braces.

**The verdict is two lists**, accepted and discarded, and a completion
processor reconciles every URL against the ones it was handed: each in one
list and only one, none invented. Without that gate the cheapest answer is
to return two accepted and say nothing about the rest — it validates just
the same and looks like finished work. And the discards are not paperwork:
they are what stops the same URL being re-judged tomorrow.

**How the reference reaches the scribe.** Through the plugin itself:
`references` implements `enrich_tool_result`, which matches tags against
tool results, and the result of `store_memory` carries the tags. NOT
through `enrich_prompt`, which matches against the words of the prompt —
and on a spoken turn the prompt is empty, because the question is the
attachment.

That path was dead until
[jaato#922](https://github.com/Jaato-framework-and-examples/jaato/issues/922),
found here: enrichment only reached dict results through a six-name
allowlist, and `store_memory` calls its text `message`. Fixed in #924 —
the session now renders the whole dict as a text view instead of guessing
which key holds the text. Verified on a spoken turn, where `enrich_prompt`
cannot help:

    [REFERENCES] enrich [tool:store_memory]: tag matches:
                 {auto-testjaato1: ['jaato', 'harness', 'orquestacion']}

**And the catalogue must be reloaded after writing it.** It is read at
startup (`set_workspace_path` → `_reload_catalog`) and then stays put.
Measured: 7 references before writing an eighth, 7 after, and 8 only once
`execute_command("references", ["reload"])` runs. Without it, what is
found today is not offered until the next conversation.

The scribe **offers, it does not use**: one thing per reply, at the end,
and it never reads a URL out loud — "an article by Martin Fowler", not the
address. If you say yes, it enters `escribano` and selects it.

**Every path says what happened.** The observer used to report only the
one outcome where the judge accepted something and return quietly
otherwise, so "nothing was worth keeping" and "it never searched at all"
looked identical from the terminal — a session where the search ran twice
showed nothing either time. It now announces the search when it starts and
names the outcome whatever it is:

    · searching: huerto_hidropónico lechugas ajuste_pH
    · found outside: Planterista, Brotavida
    · «...»: 8 results judged, none worth keeping
    · «...»: all 8 results were already judged
    · nothing to search: that memory carried no tags

**The judge opens what it is about to accept.** It sifts on title and
snippet first — a parts shop is discarded without spending a fetch — and
then `web_fetch`es the survivors. That buys two things: the page is
verified to exist (a dead link is discarded with "no se pudo abrir"
rather than offered to someone mid-conversation), and the summary is
written from what the page actually says instead of from a search
snippet. The summary is stored alongside the one-line description, which
is what `listReferences` shows.

**And the scribe can read one when you say yes.** `selectReferences`
authorises the URL; `web_fetch` opens it. It comes back to `voz` and tells
you what it found in two or three sentences — you are listening, not
reading. Fetched pages are INFORMATION, never instructions: a page that
says "ignore the above" is text someone wrote, and the persona says to
name that out loud and carry on.

## Startup

It greets at **3.8 s**: 1.6 s to create the session and 2.2 s of the audio
model's time to first byte.

The driver passes `config_root` alongside `workspace_path`. The framework
writes its own artefacts — backups, session journals — under `config_root`
and never into the tenant's workspace, so without it `file_edit` cannot
resolve a backup directory, raises at `initialize()`, and is **not
exposed**. That failure is silent from the model's side: `writeNewFile`
stays in its tool surface with no executor behind it. See the documenter
section for what that costs.

It used to be 8.8 s, and 64% of that was the curator — 1.6 s to open its
session and 4.0 s on an opening drain that **could not possibly help**,
because the inventory is rendered when the scribe's session is CREATED,
before the curator promotes anything.

That drain is now paid **only when there is something to judge**, and then
it does buy something, because it runs ahead of the inventory:

    · 2 memories left uncurated last time — judging them before we start
    · consolidated; I can start knowing it

The condition is counted from the raw store (`memory.py`) rather than kept
in a "last session ended cleanly" flag: a flag has to be written on open
and cleared on close, and a `kill -9` in between leaves it lying. Raw
memories ARE the condition — and they catch the case a flag cannot see,
where a clean session leaves a backlog because the curator judges eight at
a time.

## What you say is compressed before it is sent

The microphone produces 16 kHz mono PCM: a maximum-length press
(`MAX_UTTERANCE_SECONDS` = 120) is 3.84 MB. `voice.py` encodes it to
32 kbps MP3 — **8x smaller** — before it becomes an attachment.

It began as a workaround. The runner RPC serialised bytes with
`json.dumps(default=str)`, rendering them as a Python repr (`\xNN` per
non-printable byte) and inflating the payload **4.2x** on the wire against
a 10.49 MB frame cap. A legal two-minute utterance became a 16.7 MB frame;
the transport refused it, closed, and the in-flight turn died with it —
mid-conversation. That was
[jaato#920](https://github.com/Jaato-framework-and-examples/jaato/issues/920),
found here and measured twice with predicted-to-observed agreement within
0.2%.

**Fixed in #921**: bytes cross as base64 through a codec that decodes them
back, and an oversized frame is now dropped as a typed error for its own
call rather than desynchronising the whole channel. Verified — the exact
recording that killed a real session now completes uncompressed, in 23 s.

Compression stays for **headroom, not necessity**. The cap still exists,
and an utterance sits in history until the turn that consumed it is
evicted: 5.12 MB per press as WAV against 0.64 MB as MP3. Verified that
32 kbps costs nothing that matters — the model still understood the same
recording and stored the right memory from it.

If ffmpeg is missing, escriba refuses to start rather than quietly sending
PCM. The failure that would cause is far from its cause.

## While it is thinking

A spoken turn is ten to twenty-five seconds during which you have released
the key and nothing is on screen — no way to tell thinking from hung. A
spinner runs for exactly that window and stops on the **first audio
chunk**, which is the moment you start hearing the answer rather than the
moment the turn settles seconds later.

Every line is stamped `[HH:MM:SS]`, including the spinner's, because a
voice conversation is mostly silence — the model thinking, a search
running behind — and "how long did that take" is the first question anyone
asks of a transcript. A multi-line reply gets the stamp once and the rest
indented under it, so it stays one entry:

    [07:55:34] · searching: varroa apicultura control_de_plagas
    [07:55:39] escriba: He anotado que tienes cuatro colmenas…
               ¿Te gustaría que te diera más información?
    [07:55:39]    ↳ He anotado que la persona tiene cuatro colmenas…
    [07:55:57] · found outside: Penn State Extension, Véto-pharma…

`console.py` also owns `log`, because a spinner and a bare `print` cannot
share a terminal: the print lands on the spinner's line and mangles both.
Everything that writes during a turn — the observer's search reports, from
a background task — goes through it. On a pipe or a file the spinner is
off entirely, so logs and captured test output stay clean.

## Documentation, on request

Ask for a document and the scribe hands the job to the `documentalista`
with `spawn_subagent`. It writes markdown under `docs/<topic>/`, `index.md`
first, a tree with relative links when the material warrants one. It runs
in the BACKGROUND, so asking for a document does not stop the
conversation; when it finishes, the scribe says what it contains and where
in two sentences, and the driver prints each exact path as the file lands
(`· wrote docs/…`) — a path has to be read, not heard.

**"Accurate" is enforced, not hoped for.** The documenter may use only the
memories and the references — never what a model happens to know about the
world — and a completion processor checks four things mechanically:

    every file it claims to have written exists
    `raiz` is one of them
    every relative link inside them resolves
    every cited id (mem_… / auto-…) EXISTS

The fourth is the one that matters. A fabricated citation reads exactly
like provenance and is worse than no citation at all.

**And the scribe cannot claim a document it did not commission.** Measured
2026-09-10: asked for one, it read the memories, stored one, and said
"documento consolidado … guardado" — having spawned nothing. The person is
then waiting for a file nobody is writing, and the only clue is its
absence. So `documento_encargado` in the payload is cross-checked against
`spawn_subagent` in the tool ledger, the same trick as the memory gate: the
claim is in the payload, the truth is in the ledger, and the ledger is the
one the model cannot rewrite.

**Verified 2026-09-10.** The hand-off runs: the scribe commissions, the
subagent reads the memories, selects the reference catalogue once, and
writes `docs/<topic>/index.md`. Four separate things had to be right, and
each was wrong first:

| | |
|---|---|
| `config_root` on the session | Without it `file_edit` refuses to initialise and is **not exposed** — while `writeNewFile` still reaches the model. A tool advertised with no executor returns nothing, so the model retries forever. 127 attempts before it was killed by hand. |
| `profile=` on the spawn | Omitted, `spawn_subagent` inherits the parent's plugins and gets **no persona** — a nameless agent with no `file_edit`. Now unrepresentable: the schema requires it ([jaato#944](https://github.com/Jaato-framework-and-examples/jaato/issues/944), found here). |
| `permission` in `plugins:` | `plugin_configs.permission` for an undeclared plugin loads, validates, and does nothing ([jaato#950](https://github.com/Jaato-framework-and-examples/jaato/issues/950), found here, fixed). |
| `writeNewFile` on the documenter's whitelist | It was once not enough: a subagent was judged by the policy the PARENT initialized, so the grant had to be repeated on the scribe — which has no `file_edit` and cannot call it ([jaato#957](https://github.com/Jaato-framework-and-examples/jaato/issues/957), found here, **fixed**). The child's own profile governs now, and the scribe's whitelist describes the scribe again. |

The last one was the trap worth remembering, and it is worth remembering
even fixed: for two days the child profile named its tools, declared
`permission`, granted exactly the two it needed — everything an author
would do — and none of it governed the session it described. A grant that
belongs to the documenter had to be written on the scribe. `validate` could
not see it, because the relationship that mattered was not local to either
file.

**One tool at a time.** `runtime_limits.max_parallel_tools: 1`, because the
documenter emitted three `selectReferences` calls in a single turn, two
byte-identical. The first selected; the twin was told "no sources matched
criteria" because its sibling had just done the work — and the model read
its own race as a failure and repeated it **192 times**. `listReferences`
was the answer it never asked: it reports `selected: true` per source. The
persona now says so, and the tool pool is serialised so the race cannot
happen.

**Every profile carries a ceiling.** `budget_control` with `finalize` at
80% and `abort` at 100%, on all four. `abort` is the load-bearing rung:
`finalize` injects "wrap up with what you have", which a looping model can
ignore — this one ignored 192 consecutive failures without emitting a word.
They did **not** bind a subagent at first
([jaato#955](https://github.com/Jaato-framework-and-examples/jaato/issues/955),
found here, **fixed**): a run declaring `tool_calls: 100` reached 196,
logged nothing, and outlived its driver by 76 seconds — stopped by
`kill -TERM` on the pool slot.

**Per-agent trace logs.** `trace: {session_log, provider_log}` on the
documenter, workspace-relative — the global `/tmp/rich_client_trace.log`
interleaves every workspace on the machine, and diagnosing this meant
reading another project's session at the same timestamps and attributing
its lines to mine.

The paths use the explicit `{agent}` placeholder
([jaato#961](https://github.com/Jaato-framework-and-examples/jaato/issues/961)),
so they land as `logs/main/…` and `logs/subagent_1/…`. Under the implicit
form — a plain path, agent id appended before the extension — the provider
log split per agent but the session log did not, so the scribe's decisions
and the documenter's shared one file and every query had to be filtered by
the `agent=` field to mean anything. Measured: the child's file now holds
only the child's lines; `main/session.log` still carries some of the
child's, so it is a superset rather than a clean split.

## What it keeps of the audio

Both halves of every conversation are recorded under `audio/<stamp>/`,
because **nobody else is going to**. Audio is CLIENT-audience by
construction — `jaato_session.py:8719`: *"the model produced it, so
replaying it back into the model's own history would be both redundant
and, for audio, meaningless."* It reaches this process, it plays, and it is
gone. The inbound half is the same bargain from the other side: the
framework mints an id at ingest *"so the caller knows the id it sent, and
can name the file it archived"*. Both are the client's to keep, and the
client is this repo.

|                          | named by | verifiable from the file |
|---|---|---|
| `in_att_<id>.mp3`        | the digest the daemon minted | **yes** |
| `out_att_<sha>.wav`      | a digest we compute | **yes** |
| `manifest.jsonl`         | one row per turn | — |

**The inbound id is not ours to choose.** It is a digest of the bytes on
the wire, so the `.mp3` and the journal's `[Media evicted … ref att_…]`
marker can be matched by anyone holding both, with no mapping to trust:

    python -c "import hashlib,sys; print('att_'+hashlib.sha256(
        open(sys.argv[1],'rb').read()).hexdigest()[:16])" in_att_9f2c1ab73e0d4455.mp3

That is why the archive holds the MP3 that was SENT and not the WAV it came
from — the daemon hashes what it receives (`jaato_session.py:4746`), so
keeping anything else would give a file whose digest does not match the
marker, breaking the one property the scheme rests on.

**The outbound half had no such property and now does.** Model media
carries `model:<agent>:<n>` — a POSITION, not content, and the counter
restarts each session, so `model:escriba:3` is ambiguous across days and
recomputable from nothing. The manifest therefore carries the client id and
the session id that qualify it, and this repo hashes the reassembled audio
itself so the outbound half can be verified the same way as the inbound.

**It lives at the workspace root, not under `.jaato/`.** That directory is
the framework's namespace — profiles, agents, schemas, the memory store,
the session journals. Recordings are not framework assets: they are
client-owned, kept precisely because model media is client-audience and
nobody else keeps it. They sit beside `docs/`, which is the same kind of
thing for the same reason.

**`--forget` does not touch it**, and now cannot: that flag erases
`.jaato/memory` and `.jaato/references`, and the archive is outside
`.jaato/` entirely. An audit archive a `--forget` could reach would not be
an audit archive. Asserted by a test, not left to intention.

**Whether it was HEARD is recorded too, now.** Each `spoke` row carries a
`playback` block — how long `paplay` was waited on against how long the
audio runs, and its exit code. Audio that "finished" in a fraction of its
own duration was not played, and that case used to be indistinguishable
from success: the archive records what ARRIVED, and the recording is
written whether or not a speaker ever saw it. A playback failure also
reaches the display instead of `print`, which a live view swallows —
under `--tui` a silent failure looked exactly like working audio.

**What this still cannot tell you.** That the transcript matches what was
SAID: on a turn where the model both writes and speaks, `spoken_words`
returns `""` under the #869 rule, so the provider's transcript of its own
audio never arrives and `Tongue.spoken` gets nothing. Our turns are that
kind.

## Starting over

`--forget` clears the slate: every memory, the reference catalogue and the
discard list. It shows what it is about to lose first, because *"erase
everything"* and *"erase the nineteen things you told me over three
weeks"* are the same command and very different decisions:

    · about to forget 19 validated memories, 3 still raw, and 8 references
      type «olvida» to confirm:

**Nothing is deleted.** Both halves move under
`.jaato/forgotten/<timestamp>/` and the path is printed, so a mistake is a
`mv` away from being undone rather than gone. Delete that directory when
you are sure. `--yes` skips the prompt for unattended use.

## How it ends

**Ctrl-C** is the normal goodbye, and it consolidates before exiting.

The silence deadline (`SILENCE_S`, 120 s) is the safety net for someone
getting up and leaving. It measures **abandonment, not duration**: while
the key is held it restarts, so a long explanation never exhausts it.

Counting it the other way was a real bug. An utterance is delivered when
the key is RELEASED, not when speech starts
(`ptt_capture.py:426-432`), so a single `wait_for` on the queue measures
"how long you take to finish talking". Someone who paused ten seconds to
think and explained for forty delivered at fifty, and with the deadline at
forty-five the conversation was closed WHILE they were still speaking.
`Ears.listen` now checks whether a press is open — or a closed cut not yet
delivered, which is the window between releasing and arriving — and
restarts the deadline instead of giving up.

## The files

| | |
|---|---|
| `run_escriba.py` | The driver. All the SDK is two sessions and three turns; the judge and the documenter are spawned, not driven. |
| `voice.py` | Ears and mouth: the thread↔asyncio bridge and the audio sink. |
| `console.py` | The terminal side: the thinking spinner, and the only safe way to write while it runs. |
| `board.py` | What the conversation looks like from outside, as state. No `rich`, no terminal. |
| `richboard.py` | The live view. The only module that imports `rich`. |
| `keys.py` | One reader for the terminal's keys, because there can only be one. |
| `memory.py` | What was left uncurated last time. |
| `archive.py` | Keeps both halves of the audio, and the manifest that ties them to the turn. |
| `enrichment.py` | Search, judge and catalogue what is outside. |
| `ptt_capture.py`, `pulse_playback.py` | Copied unchanged from `jaato-cascade-audio-interchange`. They know nothing about jaato. |
| `.jaato/agents/*.md` | The four personas: escriba, curator, juez, documentalista. |
| `.jaato/profiles/` | Provider-agnostic `_base_*` plus the `openrouter_gpt_audio` set. |

Everything that is not SDK lives outside the driver on purpose:
`run_escriba.py` should read as what it means to demonstrate.

## What the SDK takes care of

`IPCClient.session()` connects, configures and creates the session.
`Session.ask` owns the send-and-wait recipe (`first-of {TURN_COMPLETED,
SESSION_TERMINATED}`), so a turn cannot hang here. Subscribing to events,
counting terminals and unsubscribing do not appear in this repo because
they do not belong to whoever writes the driver.

`ask` and `complete` divide by whether the profile is completion-gated.
`ask` returns on whichever terminal comes first and hands back text;
`complete` waits for `signal_completion` and hands back the typed
payload. The scribe and the judge are gated, so they use `complete`; the
curator is not, so it uses `ask`. Before #913 that choice did not exist
for a conversation — completing once made the session unusable.

The symmetry that makes speaking possible: `ask` returns what the model
WROTE and `on_media` hands over what it SAID, as it sounds. The user's
audio goes in through `attachments`, and on a spoken turn the prompt is
EMPTY — the question IS the attachment.

## Incident 2026-09-09 — read before touching permissions

On its first run the curator **deleted three of the user's real
memories**, two of them personal, with 22 and 18 uses. They were recovered
intact from the session journal, which is luck and not design.

Two causes, both structural:

1. **`delete_memory` was on the whitelist**, and the persona said "prefer
   discarding to deleting". Prose is a suggestion; the whitelist is the
   contract. On the re-test with the tool removed, the model **tried to
   delete again** and was denied — which is the proof of which of the two
   layers governs.
2. **`allowed_scopes: ["project"]` isolated nothing.** It is a *write-side
   gate*: it applies only on store (`memory/plugin.py:1064`).
   `retrieve_memories` and `delete_memory` never consult it, and the
   global tier points by default at `~/.jaato/memories.jsonl` — the whole
   machine's store.

Fixed by removing `delete_memory` from the whitelist and redirecting
`global_storage_path` into the workspace.

**The whitelist is not a boundary.** A plugin can mark tools as
auto-approved, and those bypass the policy: `memory` does it for
`store_memory` (`plugin.py:740`) and `references` for all four of its own
(`plugin.py:4356`). `delete_memory` was blocked only because it is not on
that list — luck, not design. **The real boundary is `tools:[...]` in
`plugins:`**, which keeps the tool out of the registry. It has bitten
three times in this repo.

**Corollary 2026-09-10 — and it points the other way.** The whitelist is
not a boundary, but it *is* load-bearing for a subagent, and not the one
you would expect: the child is judged by the policy the **parent**
initialized. The documenter's profile granted `writeNewFile` to itself and
was denied `method=default` fourteen times; granting it on the scribe's
profile — which has no `file_edit` and cannot call it — is what let the
write through
([jaato#957](https://github.com/Jaato-framework-and-examples/jaato/issues/957)).
So both statements hold at once: `tools:[...]` is what keeps a tool out of
reach, and the parent's whitelist is what lets a child's gated tool run.

Corollary, same day: with `store_memory` within reach, the curator
discarded a good memory and stored it again raw with `content` and
`description` swapped — every drain discarded and recreated it, so it was
never validated. The curator can now neither write nor delete: it judges
what is already written.

## Provenance — verified against the INSTALLED framework

Much of the design comes from `jaato-cascade-audio-interchange`, whose
`KNOWN_ISSUES.md` is a snapshot against an older server. Against the
installed **0.10.0** (`68e2cdfc`):

| | actual state in 0.10.0 |
|---|---|
| **#822** a tiers-only profile without top-level `model`/`provider` will not start | **FIXED.** `runner_spawn.py:455-464` documents the chain `profile.provider → model_tiers[initial].provider → JAATO_PROVIDER`. Verified: this profile, tiers-only, creates a session in 1.5 s. |
| **#845** neither `wake` nor `inject_prompt` carries an attachment | **FIXED** (#914). Verified: a completed session woken with an audio attachment ran the next turn. |
| **#913** the answer to `signal_completion` never reaches history | **FIXED** (#915), found here. Verified: three consecutive completions on one session, where the second used to 400. |
| **#920** runner RPC sends bytes as a Python repr, 4.2x oversize | **FIXED** (#921), found here. Verified: the recording that killed a real session now completes uncompressed. |
| **#922** tool-result enrichment never fires for `store_memory` | **FIXED** (#924), found here. The session renders the whole dict as a text view instead of guessing the key. Verified on a spoken turn: `enrich [tool:store_memory]: tag matches: {auto-testjaato1: [jaato, harness, orquestacion]}`. |
| **#919** the nudge budget is a daemon constant, not a profile knob | **FIXED** (#927), found here. Verified: at 4 nudges the same five turns close 4 of 5 instead of 2 of 5. |
| **#912** a `.jsonl` `storage_path` is reinterpreted as a directory | **OPEN**, found here. |
| **#934** the nudge budget is rationed per SESSION, not per turn | **FIXED** (#936), found here. A conversation spent its whole allowance in the first few turns. |
| **#938** `registry.get_plugin_for_tool` iterates `self._exposed` without a snapshot | **FIXED** (#941), found here. The first `spawn_subagent` killed the parent's turn with `RuntimeError: Set changed size during iteration`. |
| **#944** `spawn_subagent` lets `profile` be omitted, silently inheriting the parent's plugins with no instructions — and `allow_inline`, the knob against it, was never read | **FIXED** (#946), found here. `profile` is now `required` in the schema when inline spawning is off, which is the new default. |
| **#947** a profile with no `budget_control` is unbounded on every dimension and nothing says so | **FIXED** (#948), found here. `validate` now flags it — and flags limits that no rung enforces. |
| **#950** `plugin_configs` for a plugin absent from `plugins:` loads, validates, and does nothing | **FIXED** (#952), found here. A permission whitelist was inert for 55 ASKs with no diagnostic. |
| **#951** no permission decision was observable: only the ASK branch traced, and terminal decisions went to an in-memory list | **FIXED** (#953), found here. Every branch now logs `allowed=`/`method=`/`reason=` with the agent attributed — `agent=subagent:documentalista`. It turned three sessions of guessing into one `grep`. |
| **#955** `budget_control` does not bind a subagent | **FIXED** (#960), found here. 196 tool calls under `tool_calls: 100` with `abort` at 100%, nothing logged, and the loop outlived its driver. |
| **#957** a subagent is judged by the PARENT's permission whitelist, not its own profile's | **FIXED** (#958), found here. Split out of #951 once its own description proved unreliable. |

One of those reports was partly wrong, and the cause is worth recording:
#951's description listed "whitelist on the parent profile — no change,
hypothesis falsified". It had never been applied. A duplicate
`permission:` key under `plugin_configs` in `_base_escriba.yaml` meant
YAML's last-wins silently discarded the block, and `validate` saw nothing
to complain about — both keys are individually well-formed. An untested
edit was reported upstream as a negative result. **Parse a profile after
editing it and assert the effective value; do not trust the diff.**

Not verified, inherited from that repo: that `temperature: 0.0` makes
gpt-audio loop (818 s measured) and that the `-mini` cannot leave the
seseo. Those are measurements of model behaviour, not of the framework.

## Requirements

`rich` (only for `--tui`), `parec`, `paplay`, `pactl`, `pw-metadata`, a jaato daemon on
`/tmp/jaato.sock`, and OpenRouter credentials in
`~/.jaato/openrouter_auth.json` (`openrouter-auth`). The microphone is
`ptt_capture.py`'s `wraith_mic`.
