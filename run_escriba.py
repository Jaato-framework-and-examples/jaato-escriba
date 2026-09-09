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

import asyncio
import sys
from pathlib import Path

from jaato_sdk import ClientType, IPCClient

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


async def main() -> int:
    mouth = voice.Tongue()
    conn = dict(workspace_path=str(WORKSPACE),
                env_file=str(WORKSPACE / ".env"),
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
        print(f"· {pending} memories left uncurated last time — "
              f"judging them before we start")
        async with IPCClient.session(profile="curator", agent="curator",
                                     **conn) as curator:
            await curator.ask(DRAIN)
        print("· consolidated; I can start knowing it")

    with voice.Ears() as ears:
        # The curator is not opened for the conversation, and that is not
        # an oversight: measured, opening it here cost 1.6 s of session
        # plus 4.0 s of turn, 64% of the 8.8 s it used to take to say the
        # first word.  That price is only paid when there is something to
        # judge (above), and then it buys something, because it runs
        # before the inventory.
        async with IPCClient.session(profile="escriba", agent="escriba",
                                     **conn) as scribe:

            # An eye on what gets stored: each new memory kicks off, in the
            # background, a search outside, a judge deciding whether the
            # results are any good, and the `references` catalogue — which
            # from then on offers them by itself when the conversation
            # brushes the topic again.  Background tasks: the conversation
            # does not wait for something that at best matters next turn.
            watcher = enrichment.Observer(conn, WORKSPACE)
            watcher.attach(scribe.client)

            await scribe.ask(GREETING, on_media=mouth.speak)
            print(f"escriba: {mouth.last() or '(spoke)'}")

            # The prompt goes EMPTY on a spoken turn: the question IS the
            # attachment.  Text beside it would be a second question the
            # persona has to choose between.
            #
            # Ctrl-C is caught HERE and not outside: interrupting is the
            # normal way to say goodbye, and everything learned in the
            # conversation is lost entirely if consolidation never runs.
            try:
                while (said := await ears.listen(SILENCE_S)) is not None:
                    await scribe.ask("", attachments=[said],
                                     on_media=mouth.speak)
                    print(f"escriba: {mouth.last() or '(spoke)'}")
                print("· nobody on the other side")
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n· goodbye")

            # Let whatever it was searching finish before closing.
            await watcher.drain()

        # And now, with the conversation closed and nobody waiting, the
        # curator: consolidating what was learned is what will make it
        # wake up knowing it next time.  Here its cost is nobody's.
        print("· consolidating")
        async with IPCClient.session(profile="curator", agent="curator",
                                     **conn) as curator:
            await curator.ask(DRAIN)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ptt_capture.SourceMuted as exc:
        sys.exit(f"microphone muted: {exc}")
    except KeyboardInterrupt:
        # A Ctrl-C INSIDE the conversation is caught in there and
        # consolidates.  This only covers an interrupt before or after it.
        sys.exit(130)
