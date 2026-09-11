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
import asyncio
import sys
from pathlib import Path
from typing import List

from jaato_sdk.media_identity import ATTACHMENT_ID_KEY
from jaato_sdk import AgentError, ClientType, EventType, IPCClient

import archive as _archive
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
SILENCE_S = 120.0


class SessionGone(RuntimeError):
    """The scribe's session ended; there is nothing left to talk to."""


#: Terminal markers in an AgentError that mean the session is gone rather
#: than merely unhappy.  `NudgeExhausted` is deliberately NOT here: that
#: turn did its work and the next one still runs.
_GONE = ("runner RPC closed", "RunnerCallError", "session terminated",
         "SessionTerminated", "not found")


def _session_is_gone(exc: Exception) -> bool:
    return any(m.lower() in str(exc).lower() for m in _GONE)


async def _turn(scribe, prompt: str, said, mouth, log=None) -> None:
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
    spinner = console.Spinner("escriba is thinking…")

    speaking = False

    def sink(ev) -> None:
        # Playback BLOCKS the event loop — `finish()` waits for the audio
        # to finish sounding — so the spinner cannot animate through it.
        # Replace it with a static line rather than leaving the screen
        # dead for the length of the reply.
        nonlocal speaking
        spinner.stop()
        if not speaking:
            speaking = True
            console.log("· speaking…")
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
        async with spinner:
            payload = await scribe.complete(prompt, attachments=
                                            None if said is None else [said],
                                            on_media=sink)
    except AgentError as exc:
        console.log(f"escriba: {spoken()}")
        # A turn that did its work and never said so is survivable — see
        # above.  A session that ENDED is not: every later turn would go to
        # a session that no longer exists, and the driver would sit
        # listening into a corpse.  That is what a hang looked like on
        # 2026-09-09: the runner RPC closed on an oversized frame, the
        # session terminated, and the loop kept waiting for speech nobody
        # could answer.  Tell them, and let the caller wind down cleanly so
        # the consolidation still runs.
        if _session_is_gone(exc):
            console.log(f"   ↳ the session ended: {str(exc)[:90]}")
            raise SessionGone(str(exc)) from exc
        console.log(f"   ↳ (turn not closed: {str(exc)[:60]})")
        return
    finally:
        unsubscribe()
    words = spoken()
    console.log(f"escriba: {words}")
    if payload and payload.get("anotado"):
        console.log(f"   ↳ {payload['anotado']}")
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


async def main(assume_yes: bool = False) -> int:
    # Opened before anything can speak or be heard.  Audio is
    # CLIENT-audience: it reaches this process, plays, and is gone unless
    # written down here (`jaato_session.py:8719`).  The inbound half is
    # the same bargain from the other side — the framework mints an id
    # "so the caller knows the id it sent, and can name the file it
    # archived".  Both halves are ours; this is where we keep them.
    tape = _archive.Archive(WORKSPACE)
    mouth = voice.Tongue(archive=tape)
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
        console.log(f"· {pending} memories left uncurated last time — "
                    f"judging them before we start")
        async with IPCClient.session(profile="curator", agent="curator",
                                     **conn) as curator:
            await curator.ask(DRAIN)
        console.log("· consolidated; I can start knowing it")

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
    console.log(f"· waking with {held['curated']} validated memories "
                f"({held['raw']} raw)")

    with voice.Ears(archive=tape) as ears:
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
            tape.identify(scribe.session_id,
                          getattr(scribe.client, "client_id", None))
            console.log(f"· recording to {tape.dir}")

            # An eye on what gets stored: each new memory kicks off, in the
            # background, a search outside, a judge deciding whether the
            # results are any good, and the `references` catalogue — which
            # from then on offers them by itself when the conversation
            # brushes the topic again.  Background tasks: the conversation
            # does not wait for something that at best matters next turn.
            # `console.log` and not `print`: the observer writes from a
            # background task, and a bare print lands on top of the
            # spinner's line and mangles both.
            watcher = enrichment.Observer(conn, WORKSPACE,
                                          log=console.log)
            watcher.attach(scribe.client)

            # `complete` and not `ask`, now that the scribe is
            # completion-gated: its turn ends at `signal_completion`, and
            # `complete` is what waits for that and hands back the typed
            # payload.  `ask` would return on whichever terminal came
            # first and throw the payload away.
            await _turn(scribe, GREETING, None, mouth, log=tape.turn)

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
                    console.log(f"· heard {said.pop('seconds', 0.0):.1f}s")
                    await _turn(scribe, "", said, mouth, log=tape.turn)
                console.log("· nobody on the other side")
            except SessionGone:
                console.log("· the conversation cannot continue — consolidating "
                            "what we have")
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.log("\n· goodbye")

            # Let whatever it was searching finish before closing.
            await watcher.drain()

        # And now, with the conversation closed and nobody waiting, the
        # curator: consolidating what was learned is what will make it
        # wake up knowing it next time.  Here its cost is nobody's.
        console.log("· consolidating")
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
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation for --forget")
    args = parser.parse_args()

    if args.forget and _forget(args.yes) != 0:
        sys.exit(1)

    try:
        sys.exit(asyncio.run(main(args.yes)))
    except ptt_capture.SourceMuted as exc:
        sys.exit(f"microphone muted: {exc}")
    except KeyboardInterrupt:
        # A Ctrl-C INSIDE the conversation is caught in there and
        # consolidates.  This only covers an interrupt before or after it.
        sys.exit(130)
