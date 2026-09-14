"""escriba — a second brain that interviews by voice and remembers.

    python run_escriba.py

Push to talk. Tell it what you know. Ctrl-C says goodbye, and it
consolidates on the way out. Next time it wakes up knowing what you told
it.

------------------------------------------------------------------
What this file means to demonstrate
------------------------------------------------------------------
Every dealing with the framework is FOUR lines — two sessions and three
`ask` calls — and none of them is plumbing:

    async with IPCClient.session(profile="escriba", ...) as scribe:
        await scribe.ask(GREETING, on_media=mouth.speak)
        await scribe.ask("", attachments=[said], on_media=mouth.speak)

    async with IPCClient.session(profile="curator", ...) as curator:
        await curator.ask(DRAIN)

Two blocks rather than one nested pair, because they are two moments: the
curator takes no part in the conversation, it is what happens AFTER it —
and opening it beforehand cost 5.6 s of silence in front of the person.

`IPCClient.session` connects, configures and creates the session;
`Session.ask` owns the send-and-wait recipe (`first-of {TURN_COMPLETED,
SESSION_TERMINATED}`), so a turn cannot hang in this code.  Subscribing to
events, counting terminals, unsubscribing: none of it appears here
because none of it belongs to whoever writes the driver.

`on_media` is the symmetry that makes a spoken conversation possible:
`ask` returns what the model WROTE and `on_media` hands over what it
SAID, as it sounds.  The user's audio goes in through `attachments`.

Everything that is not SDK lives outside this file: `voice.py` (the
thread/asyncio bridge and the audio sink), `memory.py`, `enrichment.py`,
and underneath them two modules copied unchanged from
`jaato-cascade-audio-interchange` (`ptt_capture`, `pulse_playback`).
"""
from __future__ import annotations

import argparse
import contextlib
import os
import asyncio
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from jaato_sdk.media_identity import ATTACHMENT_ID_KEY
from jaato_sdk import AgentError, ClientType, EventType, IPCClient

import archive as _archive
import board as _board
import keys as _keys
import console
import enrichment
import memory
import ptt_capture
import voice

WORKSPACE = Path(__file__).resolve().parent

#: Opens the session.  A stage direction, not a question: the words of the
#: greeting belong to the persona (`agents/escriba.md`), and this only
#: tells it the microphone is now open.
GREETING = "[La sesión se abre. El usuario está a la escucha.]"

#: Wakes the curator.  Its rules — how many at a time, what gets validated
#: — are its own, not the driver's.
#:
#: "Judge", not "empty".  The first wording said «Vacía lo que haya en
#: crudo» and the model read it as what it looks like: empty them.  That
#: was not the cause of the 2026-09-09 incident — having `delete_memory`
#: on the whitelist was — but a verb that invites destruction has no
#: reason to be here.
DRAIN = "Juzga lo que haya en crudo."

#: How long the person may go WITHOUT STARTING to speak before the
#: conversation is treated as over.  It measures abandonment, not
#: duration: while the key is held the deadline restarts
#: (`voice.Ears.listen`), so a long explanation never exhausts it.
#:
#: Generous on purpose.  It is the safety net for someone getting up and
#: leaving; the DELIBERATE way to end is Ctrl-C, which consolidates too.
#: The default display: today's lines, unchanged.  `--tui` swaps it for a
#: `RichBoard`, and then NOTHING else may write — a stray print lands
#: inside the region `rich.Live` redraws and corrupts it.
_LINES = _board.LineBoard()


@contextlib.asynccontextmanager
async def _nothing():
    """Stand-in for the spinner when the board draws its own state."""
    yield


#: How often the panels are reconciled against disk.  Events paint at
#: once; this is what makes them TRUE afterwards — the curator writes from
#: its own session, the documenter from a subagent, and a `--forget` can
#: empty the store mid-run.  A board fed only by events drifts from all
#: three and then reports the drift with total confidence.
DISK_TICK_S = 2.0

SILENCE_S = 120.0

#: What opens a document when you press enter on one.  Named, not
#: discovered: if it is missing the panel says so rather than quietly
#: opening something else — a viewer you did not ask for, rendering a
#: document you did, is worse than a sentence explaining why nothing
#: happened.  `leaf` renders the markdown the documentalista writes.
VIEWER = "leaf"


class SessionGone(RuntimeError):
    """The scribe's session ended; there is nothing left to talk to."""


#: Terminal markers in an AgentError that mean the session is gone rather
#: than merely unhappy.  `NudgeExhausted` is deliberately NOT here: that
#: turn did its work and the next one still runs.
_GONE = ("runner RPC closed", "RunnerCallError", "session terminated",
         "SessionTerminated", "not found")


def _session_is_gone(exc: Exception) -> bool:
    return any(m.lower() in str(exc).lower() for m in _GONE)


async def _turn(scribe, prompt: str, said, mouth, log=None, tui=None) -> None:
    """One turn, tolerating a turn that does its work but never closes.

    `complete` and not `ask`, now that the scribe is completion-gated: its
    turn ends at `signal_completion`, and `complete` is what waits for
    that and hands back the typed payload.

    AND IT IS ALLOWED TO FAIL.  The audio model reliably does the work and
    unreliably reports it done: it stores the memory, writes a line saying
    it stored it, and never calls `signal_completion`.  The profile asks
    for four nudges (`max_completion_nudges`, jaato#919) rather than the
    default two, which took the same five turns from 2 closed to 4 — but
    it does not close every one.

    The memory is what matters and it is already on disk; the close is
    bookkeeping.  Killing a conversation over it would trade the thing
    that works for the thing that does not.  The person has already heard
    the reply either way — speech streams through `on_media` during the
    turn, before the failure.
    """
    # The spinner stops on the FIRST audio chunk — the moment the person
    # starts hearing the answer — not when the turn settles seconds later.
    # In TUI mode the board owns the screen and draws the state itself;
    # a spinner writing escape codes underneath `rich.Live` is the exact
    # corruption the single-writer rule exists to prevent.
    spinner = console.Spinner("escriba is thinking…") if tui is None else None

    speaking = False

    def sink(ev) -> None:
        # Playback BLOCKS the event loop — `finish()` waits for the audio
        # to finish sounding — so the spinner cannot animate through it.
        # Replace it with a static line rather than leaving the screen
        # dead for the length of the reply.
        nonlocal speaking
        if spinner is not None:
            spinner.stop()
        if not speaking:
            speaking = True
            (tui or _LINES).speaking()
        mouth.speak(ev)

    # TWO sources for what it said, because neither covers both cases.
    #
    # The final audio chunk carries the provider's transcript of its own
    # speech (jaato#869) — but it is EMPTY when the model also wrote text.
    # And `complete` hands back the typed payload, not the text, so unlike
    # `ask` there is no return value to fall back on.  Reading only the
    # chunk printed "(spoke)" over turns whose words were sitting in
    # history all along.
    written: List[str] = []
    unsubscribe = scribe.client.subscribe(
        EventType.AGENT_OUTPUT,
        lambda ev: written.append(getattr(ev, "text", "") or "")
        if getattr(ev, "source", None) == "model" else None)

    def spoken() -> str:
        return mouth.last() or "".join(written).strip() or "(spoke)"

    try:
        if tui is not None:
            tui.thinking(True)
        async with (spinner or _nothing()):
            payload = await scribe.complete(prompt, attachments=
                                            None if said is None else [said],
                                            on_media=sink)
    except AgentError as exc:
        (tui or _LINES).spoke(spoken())
        # A turn that did its work and never said so is survivable — see
        # above.  A session that ENDED is not: every later turn would go to
        # a session that no longer exists, and the driver would sit
        # listening into a corpse.  That is what a hang looked like on
        # 2026-09-09: the runner RPC closed on an oversized frame, the
        # session terminated, and the loop kept waiting for speech nobody
        # could answer.  Tell them, and let the caller wind down cleanly so
        # the consolidation still runs.
        if _session_is_gone(exc):
            (tui or _LINES).note(f"   ↳ the session ended: {str(exc)[:90]}")
            raise SessionGone(str(exc)) from exc
        (tui or _LINES).note(f"   ↳ (turn not closed: {str(exc)[:60]})")
        return
    finally:
        unsubscribe()
    words = spoken()
    spoke_rec = mouth.recordings[-1] if getattr(mouth, "recordings", None) else None
    (tui or _LINES).spoke(words, (payload or {}).get("anotado", ""),
                          audio=(spoke_rec or {}).get("sha"))
    # One row per turn, written AFTER the turn closed so it records what
    # happened rather than what was attempted.  `said` carries the id we
    # minted for the utterance; `mouth.recordings` carries what we kept of
    # the reply.  Neither is reconstructible later: the attachment is gone
    # from history by the next turn and model media never entered it.
    if log is not None:
        log(heard=(said or {}).get(ATTACHMENT_ID_KEY),
            heard_seconds=(said or {}).get("seconds"),
            spoke=mouth.recordings[-1] if getattr(mouth, "recordings", None) else None,
            transcript=words if words != "(spoke)" else None,
            anotado=(payload or {}).get("anotado"))
        if getattr(mouth, "recordings", None):
            mouth.recordings.clear()


def _forget(assume_yes: bool) -> int:
    """Move memories and references aside, on purpose and out loud.

    Nothing is unlinked: both halves move under `.jaato/forgotten/<stamp>/`
    and the path is printed.  Erasing a second brain should not be a thing
    a person has to get right on the first try, and a rename costs nothing
    against the alternative.

    The confirmation shows the COUNTS first, because "erase everything" and
    "erase the nineteen things you told me over three weeks" are the same
    command and very different decisions.
    """
    held = memory.counts(WORKSPACE)
    total = held["raw"] + held["curated"]
    refs = len(list((WORKSPACE / enrichment.CATALOGUE).glob("auto-*.json"))) \
        if (WORKSPACE / enrichment.CATALOGUE).is_dir() else 0
    if not total and not refs:
        print("· nothing to forget: the store is already empty")
        return 0

    print(f"· about to forget {held['curated']} validated memories, "
          f"{held['raw']} still raw, and {refs} references")
    if not assume_yes:
        try:
            if input("  type «olvida» to confirm: ").strip() != "olvida":
                print("· left alone")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\n· left alone")
            return 1

    where = memory.forget(WORKSPACE)
    moved = enrichment.forget(WORKSPACE)
    print(f"· forgotten. Moved, not deleted: {where.parent if where else '—'}")
    print(f"  ({total} memories, {moved} references — delete that directory "
          f"when you are sure)")
    return 0


def _read_document(view, typing, path: str) -> None:
    """Hand the terminal to the viewer for one document, then take it back.

    The same move `jaato-tui` makes to run an editor over its own display
    (`pt_display.py:_open_workspace_file` -> `prompt_toolkit`'s
    `run_in_terminal`), assembled from the two halves that own a terminal
    here: the board stops drawing, the key reader gives back the line
    discipline.  IN THAT ORDER, as `in_terminal` does it — a display still
    refreshing onto a terminal that has already gone cooked writes its
    frame into whatever the child has just drawn.

    Runs on the reader thread, which is what makes it safe: see
    `keys.Keys.paused`.
    """
    exe = shutil.which(VIEWER)
    if exe is None:
        view.note(f"· {VIEWER} no está instalado; no puedo abrir {path}")
        return
    with view.suspended(), typing.paused():
        subprocess.call([exe, path])


async def _reconcile(view, stop: asyncio.Event) -> None:
    """Re-read what is on disk until told to stop.

    Cheap: two directory listings and a line count, every couple of
    seconds.  Deliberately NOT driven by events — the point is to catch
    what no event reaches this process for.
    """
    while not stop.is_set():
        try:
            held = memory.counts(WORKSPACE)
            view.memories(held["curated"], held["raw"])
            cat = WORKSPACE / enrichment.CATALOGUE
            view.catalogue(len(list(cat.glob("auto-*.json"))) if cat.is_dir() else 0)
            # The lists behind the panels.  Read here rather than kept from
            # events because the writers are elsewhere: the curator has its
            # own session, the documenter is a subagent, and everything
            # from previous days was written before this process existed.
            view.listing("memoria", memory.recent(WORKSPACE))
            view.listing("referencias", enrichment.catalogue_entries(WORKSPACE))
            docs = WORKSPACE / "docs"
            view.listing("documentos", [
                # The absolute path travels WITH the row rather than being
                # rebuilt from `text` at the far end: the panel is the only
                # thing that knows which root the listing was read from.
                {"text": str(f.relative_to(WORKSPACE)), "path": str(f),
                 "at": datetime.fromtimestamp(f.stat().st_mtime)
                            .strftime("%Y-%m-%d %H:%M")}
                for f in sorted(docs.rglob("*.md"),
                                key=lambda f: -f.stat().st_mtime)[:40]
            ] if docs.is_dir() else [])
        except OSError:
            pass          # a store being rewritten under us is not fatal
        try:
            await asyncio.wait_for(stop.wait(), DISK_TICK_S)
        except asyncio.TimeoutError:
            continue


async def main(assume_yes: bool = False, tui: bool = False) -> int:
    # Opened before anything can speak or be heard.  Audio is
    # CLIENT-audience: it reaches this process, plays, and is gone unless
    # written down here (`jaato_session.py:8719`).  The inbound half is
    # the same bargain from the other side — the framework mints an id
    # "so the caller knows the id it sent, and can name the file it
    # archived".  Both halves are ours; this is where we keep them.
    # One writer, chosen here.  `LineBoard` prints exactly what this driver
    # always printed; `RichBoard` draws instead and then nothing else may
    # write to the terminal at all.
    if tui:
        import richboard
        view = richboard.RichBoard()
    else:
        view = _LINES
    live = tui

    # `ESCRIBA_NO_ARCHIVE=1` takes the recorder out of the path entirely —
    # no minting, no chunk accumulation, no WAV written — leaving the audio
    # path as it was before any of this existed.  Present so "is the
    # archive doing this?" is one run rather than a bisect: a question
    # about audio timing that takes a checkout to ask does not get asked.
    tape = None if os.environ.get("ESCRIBA_NO_ARCHIVE") else _archive.Archive(WORKSPACE)
    mouth = voice.Tongue(archive=tape,
                         on_problem=lambda m: view.note(f"· AUDIO: {m}"))
    if tape is None:
        view.note("· ESCRIBA_NO_ARCHIVE: recording nothing this run")
    conn = dict(workspace_path=str(WORKSPACE),
                env_file=str(WORKSPACE / ".env"),
                # The framework writes its own artefacts — backups, session
                # journals — under config_root, never into the tenant's
                # workspace.  Without it `file_edit` refuses to initialise
                # and is silently NOT exposed, so the documentalista gets
                # `writeNewFile` in its tool surface with no executor behind
                # it: every call returns nothing and the model retries for
                # as long as someone lets it.
                config_root=str(WORKSPACE / ".jaato"),
                # API: a headless driver.  The server strips
                # `signal_completion` from root sessions of a
                # TERMINAL/WEB/CHAT client, and we do not use it here —
                # but declaring the real identity is what makes the filter
                # apply the right thing.
                client_type=ClientType.API)

    with view:
        # Whatever was left unjudged last time, and ONLY if something was.
      #
      # It runs before opening the scribe on purpose: its inventory is
      # rendered when its session is CREATED, so this is the only thing that
      # can get last time's memories into today's greeting.  The other way
      # round — which is how it started — the drain finished after the
      # inventory was already built, and served no purpose at all.
      #
      # Conditional, because unconditional cost 5.6 s of silence on every
      # start to do nothing 99% of the time.  And said out loud in the
      # terminal: it is work done before greeting, and it has no reason to
      # be invisible.
      pending = memory.uncurated_count(WORKSPACE)
      if pending:
          view.note(f"· {pending} memories left uncurated last time — "
                    f"judging them before we start")
          async with IPCClient.session(profile="curator", agent="curator",
                                       **conn) as curator:
              await curator.ask(DRAIN)
          view.note("· consolidated; I can start knowing it")

      # What the driver believes the store holds, said out loud BEFORE the
      # session opens.  The inventory is rendered inside the daemon and
      # nothing records what it produced, so when the scribe greeted with
      # "empezamos de cero" over a store holding one validated memory
      # (2026-09-11 12:39:51, curated.jsonl written 18 s earlier) there was
      # no way to tell whether the prefetch saw nothing or the model ignored
      # what it saw.  One line here makes that a visible contradiction
      # instead of a silent one: if this says 1 and the greeting says "de
      # cero", the prefetch is the half to look at.
      held = memory.counts(WORKSPACE)
      view.memories(held["curated"], held["raw"])

      # WHO OWNS STDIN is decided here, once.  With a live view there is a
      # key reader already, so the microphone must not start a second one:
      # two loops on one descriptor split every keystroke between them, and
      # the half that reached the wrong one was silently dropped.
      with voice.Ears(archive=tape, on_state=view.listening,
                      read_keys=not live) as ears:
          # The curator is not opened for the conversation, and that is not
          # an oversight: measured, opening it here cost 1.6 s of session
          # plus 4.0 s of turn, 64% of the 8.8 s it used to take to say the
          # first word.  That price is only paid when there is something to
          # judge (above), and then it buys something, because it runs
          # before the inventory.
          async with IPCClient.session(profile="escriba", agent="escriba",
                                       **conn) as scribe:
              # The outbound half has no journal ref — model media never
              # enters history, so nothing upstream names it.  These two are
              # what make `model:<agent>:<n>` locatable afterwards: the
              # counter restarts each session, so the stream id alone is
              # ambiguous across days.
              if tape is not None:
                  tape.identify(scribe.session_id,
                                getattr(scribe.client, "client_id", None))
                  view.recording_to(str(tape.dir))

              # The panels reconcile against disk on a slow tick, because
              # three writers this process never sees touch that state: the
              # curator from its own session, the documenter from a
              # subagent, and `--forget` from the command line.
              # ONLY with a live display.  The tick exists to keep PANELS
              # honest; line mode has no panel to be wrong, and there every
              # call is a printed line — the first run of this reconciled
              # faithfully and repeated "· waking with 3 validated memories"
              # every two seconds for the length of the conversation.
              # ONE reader for the terminal.  The view claims what it uses
              # and hands the rest to the microphone — in WSL that is the
              # Space that drives push-to-talk, and two loops reading one
              # descriptor would mean whichever called `read` first ate the
              # byte.  On wraith the mic reads no keys at all, so the
              # fallback is simply never used.
              def _enter() -> None:
                  """Go deeper — except on a document, which opens.

                  A document's detail view would be its path and its date,
                  both of which the list already shows.  So for that one
                  panel `enter` means READ IT, and the depth that would
                  have shown nothing is simply not there.
                  """
                  st = view.state
                  if st.open_panel == "documentos" and not st.open_item:
                      chosen = view.selected_item()
                      if chosen and chosen.get("path"):
                          _read_document(view, typing, chosen["path"])
                          return
                  view.open()

              bindings = {
                  "j": lambda: view.move(1), "k": lambda: view.move(-1),
                  "down": lambda: view.move(1), "up": lambda: view.move(-1),
                  "enter": _enter,
                  "esc": view.close, "q": view.close,
              }
              # Space, only where Space is a key.  BOUND, not forwarded: it
              # used to reach the microphone through a `fallback` resolved
              # with `getattr(ears, "key", None)`, and no such method
              # existed — so it was `None`, and every Space this reader won
              # went nowhere.  A name that must exist fails loudly.
              if ears.keyboard_driven:
                  bindings[" "] = ears.toggle
              typing = _keys.Keys(bindings) if live else contextlib.nullcontext()

              stop_tick = asyncio.Event()
              ticker = (asyncio.create_task(_reconcile(view, stop_tick))
                        if live else None)
              typing.__enter__()

              # An eye on what gets stored: each new memory kicks off, in the
              # background, a search outside, a judge deciding whether the
              # results are any good, and the `references` catalogue — which
              # from then on offers them by itself when the conversation
              # brushes the topic again.  Background tasks: the conversation
              # does not wait for something that at best matters next turn.
              # The BOARD and not a print: the observer writes from a
              # background task, and whatever is drawing — a spinner's line
              # or `rich.Live`'s region — is mangled by anything that writes
              # underneath it.  One writer, and the board is it.
              watcher = enrichment.Observer(conn, WORKSPACE,
                                            board=view)
              watcher.attach(scribe.client)

              # `complete` and not `ask`, now that the scribe is
              # completion-gated: its turn ends at `signal_completion`, and
              # `complete` is what waits for that and hands back the typed
              # payload.  `ask` would return on whichever terminal came
              # first and throw the payload away.
              await _turn(scribe, GREETING, None, mouth, log=(tape.turn if tape is not None else None),
                          tui=view if live else None)

              # The prompt goes EMPTY on a spoken turn: the question IS the
              # attachment.  Text beside it would be a second question the
              # persona has to choose between.
              #
              # Ctrl-C is caught HERE and not outside: interrupting is the
              # normal way to say goodbye, and everything learned in the
              # conversation is lost entirely if consolidation never runs.
              try:
                  while (said := await ears.listen(SILENCE_S)) is not None:
                      # Acknowledge the utterance BEFORE the turn: the
                      # person has just released the key, and the encode
                      # plus the model's first token is several seconds of
                      # otherwise-unexplained silence.
                      view.heard(said.pop("seconds", 0.0), said.get(ATTACHMENT_ID_KEY))
                      await _turn(scribe, "", said, mouth, log=(tape.turn if tape is not None else None),
                                  tui=view if live else None)
                  view.note("· nobody on the other side")
              except SessionGone:
                  view.note("· the conversation cannot continue — consolidating "
                            "what we have")
              except (KeyboardInterrupt, asyncio.CancelledError):
                  view.note("· goodbye")

              stop_tick.set()
              if ticker is not None:
                  await ticker
              typing.__exit__(None, None, None)

              # Let whatever it was searching finish before closing.
              await watcher.drain()

          # And now, with the conversation closed and nobody waiting, the
          # curator: consolidating what was learned is what will make it
          # wake up knowing it next time.  Here its cost is nobody's.
          view.note("· consolidating")
          async with IPCClient.session(profile="curator", agent="curator",
                                       **conn) as curator:
              await curator.ask(DRAIN)
      return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="escriba — a second brain that interviews by voice.")
    parser.add_argument("--forget", action="store_true",
                        help="move every memory and reference aside and start "
                             "from scratch (recoverable: they are moved under "
                             ".jaato/forgotten/, not deleted)")
    parser.add_argument("--tui", action="store_true",
                        help="draw a live view instead of printing lines: "
                             "the conversation in one panel, what it has "
                             "learnt in a sidebar. Nothing else may write to "
                             "the terminal while it runs")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation for --forget")
    args = parser.parse_args()

    if args.forget and _forget(args.yes) != 0:
        sys.exit(1)

    try:
        sys.exit(asyncio.run(main(args.yes, tui=args.tui)))
    except ptt_capture.SourceMuted as exc:
        sys.exit(f"microphone muted: {exc}")
    except KeyboardInterrupt:
        # A Ctrl-C INSIDE the conversation is caught in there and
        # consolidates.  This only covers an interrupt before or after it.
        sys.exit(130)
