"""A demonstration is stored whole, bounded, and only in frames the model takes.

The properties that matter if this is wrong, none of which raises on its
own:

* a frame the `decide` tool would refuse reaches the disk anyway, and the
  demo fails at document time -- after the demo is over and the person
  has gone;
* a demo recorded by one person travels in a template to everybody else;
* an id from the wire names a directory outside the person's workspace;
* a demo with no ceiling fills the host's disk and takes every other
  person's recordings with it;
* two demos record at once and the narration lands in whichever opened
  last.
"""
import json
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo                                            # noqa: E402
import workspace as w                                  # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<44} got {got!r}, want {want!r}")
        fail += 1


def png(width: int, height: int) -> bytes:
    """A real PNG of the given size, from the standard library only.

    Built rather than fixtured because the thing under test reads the
    IHDR: a stub with the right magic and no header is a different test
    (and is one, below).
    """
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


# A PERSON'S RECORDING NEVER TRAVELS.  `demo/` has to be in the same
# exclusion list as `audio/` and `staged/`, or the first template built
# from a live workspace carries one person's screen to every other.
check("demo/ is runtime state", demo.ROOT.as_posix() in w.RUNTIME, True)

ws = Path(tempfile.mkdtemp(prefix="escriba-demo-"))
try:
    check("nothing is recording to start with", demo.open_id(ws), None)
    ident = demo.open_demo(ws)
    check("the open demo is the one just opened", demo.open_id(ws), ident)

    # ONE AT A TIME.  Two would fan one screen into two directories.
    try:
        demo.open_demo(ws)
        check("a second demo is refused", "opened", "refused")
    except demo.AlreadyOpen:
        pass

    folder = ws / demo.ROOT / ident

    # REFUSED AT THE DOOR, and nothing written.  1920 px wide is the
    # ordinary case -- a full-HD screen, unscaled -- and it is outside
    # the bounds `decide` enforces, so it must fail while the person is
    # still recording and can be told.
    try:
        demo.frame(ws, ident, png(1920, 1080), 0)
        check("an oversize frame is refused", "stored", "refused")
    except demo.NotAFrame as exc:
        check("the refusal says what to do", "scale or crop" in str(exc), True)
    check("and nothing was written", sorted(p.name for p in folder.iterdir()), [])

    # Not an image at all, and a PNG that is only a signature: two
    # different ways for a page to send rubbish, both refused.
    for bad, why in ((b"<html>not a frame</html>", "not an image"),
                     (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "a header-less PNG")):
        try:
            demo.frame(ws, ident, bad, 0)
            check(f"{why} is refused", "stored", "refused")
        except demo.NotAFrame:
            pass
    check("still nothing written", sorted(p.name for p in folder.iterdir()), [])

    # THE SERVER NAMES THE FILE, so no value from the wire becomes a
    # path component; and the PAGE's offset is kept, because the time a
    # POST arrived is the frame's time plus the upload.
    row = demo.frame(ws, ident, png(1568, 882), 2000)
    check("the server numbers the frame", row["file"], "0001.png")
    # AND WRITES THE WHOLE PATH DOWN, so that reading the index is all
    # anybody has to do to name a capture.  Workspace-relative, like
    # the manifest's `root` and `staging`'s `path`: the session resolves
    # it against ITS workspace, and an absolute path would be this
    # host's rather than the runner's.
    check("and writes the path nobody should have to build",
          row["path"], f"demo/{ident}/0001.png")
    check("which is where the file actually is",
          (ws / row["path"]).is_file(), True)
    check("the page's offset is kept", row["at_ms"], 2000)
    check("the size is measured", row["w"], 1568)
    demo.frame(ws, ident, png(800, 600), 4000)
    got = [(f["n"], f["file"], f["at_ms"]) for f in demo.frames(ws, ident)]
    check("frames come back in order", got,
          [(1, "0001.png", 2000), (2, "0002.png", 4000)])

    # A DEMO IN PROGRESS HAS NO MANIFEST, which is the only record of
    # whether it is open -- so there is no flag to leave stale.
    check("an open demo has no manifest", demo.manifest(ws, ident), None)

    # THE COUNT CEILING IS REPORTED, not enforced by truncation.
    was = demo.MAX_FRAMES
    demo.MAX_FRAMES = 2
    try:
        demo.frame(ws, ident, png(800, 600), 6000)
        check("the frame ceiling is enforced", "stored", "refused")
    except demo.TooLarge as exc:
        check("the refusal names the ceiling", "2" in str(exc), True)
    finally:
        demo.MAX_FRAMES = was
    check("and the demo still holds two", len(demo.frames(ws, ident)), 2)

    # THE BYTE CEILING COVERS THE WHOLE DEMO, not one upload: a stream
    # of legal frames is exactly what `staging`'s per-POST ceiling does
    # not bound.
    was_bytes = demo.MAX_BYTES
    demo.MAX_BYTES = 1024
    try:
        demo.frame(ws, ident, png(800, 600), 8000)
        check("the byte ceiling is enforced", "stored", "refused")
    except demo.TooLarge:
        pass
    finally:
        demo.MAX_BYTES = was_bytes

    man = demo.close_demo(ws, ident, b"OggS-not-really-audio", "webm")
    check("the manifest counts the frames", man["frames"], 2)
    check("the narration is stored beside them", man["audio"], "narration.webm")
    check("the root is workspace-relative", man["root"], f"demo/{ident}")
    check("the narration is on disk", (folder / "narration.webm").exists(), True)
    check("the manifest reads back", demo.manifest(ws, ident)["id"], ident)
    check("nothing is recording now", demo.open_id(ws), None)

    # A CLOSED DEMO TAKES NOTHING MORE.  Both ways in have to refuse, or
    # a late frame lands in a demo already written up.
    for label, call in (("a frame", lambda: demo.frame(ws, ident, png(800, 600), 1)),
                        ("a close", lambda: demo.close_demo(ws, ident, b"", ""))):
        try:
            call()
            check(f"{label} after close is refused", "accepted", "refused")
        except demo.NoSuchDemo:
            pass

    # TWO DEMOS IN THE SAME SECOND ARE TWO DEMOS.  With a
    # second-resolution stamp they were one: the second reopened the
    # first's directory, and once the first was closed that meant frames
    # appended to a demonstration already written up.  Opened back to
    # back deliberately, because that is the only way to reach it.
    ids = []
    for _ in range(3):
        one = demo.open_demo(ws)
        ids.append(one)
        demo.close_demo(ws, one, b"", "")
    check("three demos in a row are three ids", len(set(ids)), 3)
    check("and none inherited another's frames",
          [len(demo.frames(ws, i)) for i in ids], [0, 0, 0])

    # AN ID FROM THE WIRE IS MATCHED AGAINST ITS SHAPE, never sanitised:
    # it becomes a path component, and the only safe handling of such a
    # value is to refuse anything this module did not write.
    for hostile in ("../../etc", "..", "abc", "2026-10-17", "", "/etc/passwd"):
        try:
            demo.frame(ws, hostile, png(800, 600), 0)
            check(f"id {hostile!r} is refused", "accepted", "refused")
        except (demo.NoSuchDemo, demo.NotAFrame):
            pass
    check("and no directory was made for one",
          sorted(p.name for p in (ws / demo.ROOT).iterdir()),
          sorted([ident] + ids))
finally:
    shutil.rmtree(ws)

# ---------------------------------------------------------------- routes
#
# THE ENDPOINTS, without an HTTP client, in the shape `tests_web_hub`
# uses for `/stage`: what is worth asserting is the part that spans two
# files and fails silently, and the handler's own mapping of this
# module's refusals onto codes the page branches on.
import asyncio                                         # noqa: E402
import io                                              # noqa: E402
from fastapi import HTTPException, Request              # noqa: E402
from starlette.datastructures import UploadFile as Upload  # noqa: E402

from escriba_web import app as _app                      # noqa: E402


class _Person:
    """The route's only input: a workspace, and what it was told.

    THE TELLING IS THE POINT OF THE ROUTES, not a side effect of them.
    A demonstration the escriba is never told about is frames on a disk:
    it cannot offer to document one it does not know finished, and the
    person is left to ask for something they have no name for.
    """

    def __init__(self, ws: Path) -> None:
        self.ws = ws
        self.told: list = []

    def demo_opened(self, ident: str) -> None:
        self.told.append(("opened", ident))

    async def demo_closed(self, manifest: dict) -> None:
        self.told.append(("closed", manifest["frames"]))


async def _routes(ws: Path) -> None:
    person = _Person(ws)

    async def ready(_request):
        return person
    _app.person_of = ready

    def code(exc: HTTPException) -> int:
        return exc.status_code

    opened = await _app.demo_open(None)
    ident = opened["id"]
    check("abrir answers with an id", bool(ident), True)

    try:
        await _app.demo_open(None)
        check("a second abrir is 409", "opened", "refused")
    except HTTPException as exc:
        check("a second abrir is 409", code(exc), 409)

    # A FRAME THE MODEL WOULD NOT TAKE IS 422, not 400 and not 500: the
    # page can act on it, and only while the demo is still running.
    big = Upload(filename="shot.png", file=io.BytesIO(png(1920, 1080)))
    try:
        await _app.demo_frame(None, ident, big, 0)
        check("an oversize frame is 422", "stored", "refused")
    except HTTPException as exc:
        check("an oversize frame is 422", code(exc), 422)
        check("and the 422 says what to do", "scale or crop" in exc.detail, True)

    # NO FILENAME IS READ. The upload names itself `../../etc/passwd`
    # and the stored frame is still the number this module chose --
    # which is why `/demo` needs no equivalent of `staging.safe_name`.
    sneaky = Upload(filename="../../etc/passwd", file=io.BytesIO(png(800, 600)))
    row = await _app.demo_frame(None, ident, sneaky, 1500)
    check("the upload's own name is ignored", row["file"], "0001.png")
    check("and the page's offset survives the form", row["at_ms"], 1500)

    for bad in ("../../etc", "nope"):
        try:
            await _app.demo_frame(None, bad,
                                  Upload(filename="f", file=io.BytesIO(png(800, 600))), 0)
            check(f"a frame for id {bad!r} is 404", "stored", "refused")
        except HTTPException as exc:
            check(f"a frame for id {bad!r} is 404", code(exc), 404)

    check("abrir tells the escriba", person.told[:1], [("opened", ident)])

    man = await _app.demo_close(
        None, ident, Upload(filename="a.webm", file=io.BytesIO(b"narration")), "webm")
    check("cerrar seals the manifest", man["frames"], 1)
    # FIRED, NOT AWAITED, so it has not run yet when the route returns:
    # the page gets its answer back without waiting on a whole turn.
    check("cerrar answers before the turn", ("closed", 1) in person.told, False)
    await asyncio.sleep(0)
    check("and then commissions the document", ("closed", 1) in person.told, True)
    try:
        await _app.demo_close(None, ident, None, "webm")
        check("a second cerrar is 404", "accepted", "refused")
    except HTTPException as exc:
        check("a second cerrar is 404", code(exc), 404)

    # A DEMO WITH NO MICROPHONE IS STILL A DEMO. Losing the frames
    # because the audio never arrived is the expensive way to report a
    # refused permission.
    second = (await _app.demo_open(None))["id"]
    await _app.demo_frame(None, second,
                          Upload(filename="f", file=io.BytesIO(png(800, 600))), 0)
    silent = await _app.demo_close(None, second, None, "")
    check("a demo with no narration closes", silent["audio"], "")
    check("and keeps its frames", silent["frames"], 1)

    # NOTHING NEW STARTS WHILE THE SERVER IS CLOSING, though a frame of
    # what is already recording is still taken: the same trade `/talk`
    # makes for a turn in flight.
    third = (await _app.demo_open(None))["id"]
    _app.CLOSING.set()
    try:
        try:
            await _app.demo_open(None)
            check("abrir while closing is 503", "opened", "refused")
        except HTTPException as exc:
            check("abrir while closing is 503", code(exc), 503)
        late = await _app.demo_frame(
            None, third, Upload(filename="f", file=io.BytesIO(png(800, 600))), 0)
        check("a frame while closing is still taken", late["n"], 1)
    finally:
        _app.CLOSING.clear()


ws2 = Path(tempfile.mkdtemp(prefix="escriba-demo-routes-"))
try:
    asyncio.run(_routes(ws2))
finally:
    shutil.rmtree(ws2)

# ------------------------------------------------ the commission turns
#
# THE SPLIT IS THE PROPERTY, and nothing in one file shows it.  The
# commission runs in `escribano`, which `escriba.yaml` tells the model is
# the tier where "AQUI NO SE OYE"; the sentence to the person has to be
# spoken in `voz`.  A single turn asking for both put the words in the
# wrong tier, and the page sat on "audio llegando..." with a transcript
# row carrying `audio: None` (measured, session 20261017_223809).
#
# And the first turn has to NAME the frames.  The documentalista's
# `file_edit` is subset to `[writeNewFile, updateFile, readFile]`, so a
# prompt pointing at the folder sends it to read a folder: 98 failed
# `readFile` calls on the same session, then the `tool_calls` ceiling.
import escriba_web.driver as _driver                     # noqa: E402
from escriba_web.driver import Person                     # noqa: E402
import demo_live as _live                                 # noqa: E402
import journal as _journal                                # noqa: E402
import backchannel as _backchannel                        # noqa: E402
import choosing                                           # noqa: E402
import transcribe as _transcribe                          # noqa: E402
from escriba_web import hub as _hub                        # noqa: E402

MANIFEST = {"id": "20261017_223855_438804", "frames": 49,
            "root": "demo/20261017_223855_438804",
            "audio": "narration.webm", "bytes": 18393684}


async def _said(person, manifest, anotado="de qué iba") -> tuple:
    """Every prompt `demo_closed` sends, and the document it starts.

    NO DAEMON AND NO DOCUMENT.  `_document` is the driver's own work --
    a decision call and another agent's session -- and it runs in a held
    task; these legs are about the TURN, so it is stubbed and its own
    legs are below.
    """
    out: list = []
    started: list = []
    real, _driver._turn = _driver._turn, (
        lambda scribe, prompt, att, relay, **kw:
        out.append(prompt) or _ok({"anotado": anotado}))
    person._scribe = object()
    person._document = (
        lambda m, brief: started.append((m["id"], brief)) or _ok(None))
    try:
        await person.demo_closed(dict(manifest))
        # The task is created, not awaited: let it run.
        await asyncio.sleep(0)
    finally:
        _driver._turn = real
    return out, started


async def _ok(value):
    return value


async def _timeout():
    raise asyncio.TimeoutError


async def _commission() -> None:
    root = Path(tempfile.mkdtemp(prefix="escriba-demo-"))
    try:
        person = Person("demo-sub-0001", root=root,
                        identity={"username": "dani", "email": "dani@ejemplo.es"})
        said, started = await _said(person, MANIFEST)

        # ONE TURN, WHERE THERE WERE TWO.  The first used to commission
        # the document in `escribano` and the second to say so in `voz`.
        # The commissioning left the model entirely -- see `choosing` --
        # so what is left is the half that was always a judgement: what
        # the recording is OF.
        check("closing a demonstration costs one turn", len(said), 1)
        check("spoken where she can hear it", "`voz`" in said[0], True)
        check("and told to stay out of the silent tier",
              "No entres en `escribano`" in said[0], True)
        # THE SCRIBE IS NOT ASKED TO COMMISSION ANYTHING ANY MORE, and
        # the prompt has to say so: an escriba told a document is coming
        # and left holding `spawn_subagent` is an escriba that may
        # commission a second one.
        check("it is told the document is already under way",
              "ya se está preparando" in said[0], True)
        check("and asked for no captures", "capturas" in said[0]
              and "elegir capturas" in said[0], True)
        check("nothing is named for it to open", "frames.jsonl" in said[0],
              False)
        check("the one thing it must produce is the sentence",
              "`anotado`" in said[0], True)
        # AND THE SENTENCE IS ABOUT THE DEMONSTRATION, NOT THE DOCUMENT.
        # `wrote_something` refuses an `anotado` that announces a
        # document when no `spawn_subagent` ran -- for a good reason,
        # since that was a person waiting for a file nobody was writing.
        # The driver commissions this one, so the escriba never spawns,
        # so an `anotado` mentioning the document would be refused on
        # every demonstration.  The brief it must write is what the
        # recording SHOWS; the document is something it says out loud.
        check("and the note is told to leave the document out",
              "no hables del documento" in said[0], True)

        # AND THAT SENTENCE IS WHAT THE DOCUMENT IS COMMISSIONED WITH.
        # It is read off the turn's own payload rather than asked for in
        # a second turn, because the escriba has already written it.
        check("the document is started", [i for i, _ in started],
              [MANIFEST["id"]])
        check("with the escriba's own sentence", started[0][1], "de qué iba")

        # NOTHING TO SAY IS NOT A REASON NOT TO WRITE.  A turn that
        # closed without annotating still recorded a demonstration, and
        # the document is the person's either way.
        _, anyway = await _said(person, MANIFEST, anotado="")
        check("a turn that annotated nothing still starts it",
              len(anyway), 1)
        check("with an empty brief rather than a missing one",
              anyway[0][1], "")

        # A TURN THAT NEVER CLOSED COMMISSIONS NOTHING.  The escriba has
        # not said what this is, and the person has been told nothing --
        # so starting a document now would announce work nobody can
        # describe.
        tried: list = []
        started_anyway: list = []
        real, _driver._turn = _driver._turn, (
            lambda scribe, prompt, att, relay, **kw:
            tried.append(prompt) or _timeout())
        person._scribe = object()
        person._document = (
            lambda m, brief: started_anyway.append(m) or _ok(None))
        try:
            await person.demo_closed(dict(MANIFEST))
            await asyncio.sleep(0)
        finally:
            _driver._turn = real
        check("a brief that times out is one turn", len(tried), 1)
        check("and starts no document", started_anyway, [])

        # AND THE LIVE BUFFER IS RELEASED, on the ordinary ending.  What
        # it held was the recent past of a recording in progress, and
        # the whole narration has just landed in the demo's own
        # directory -- so keeping a lossy window of it in memory until
        # the next demonstration would be holding the worse copy.
        person._following = _driver.Mic("demo", MANIFEST["id"],
                                       _live.Narration())
        await _said(person, MANIFEST)
        check("closing releases the live narration", person._following,
              None)
    finally:
        shutil.rmtree(root)


asyncio.run(_commission())


# ------------------------------------------- the call that kept going out
#                                                              without eyes
#
# THE ONE STEP THAT HAS NEVER RELIABLY WORKED.  Four measured failures,
# all of them the same shape: a `decide` built by a conversational model
# with the images left off, so the decision model scored screenshots it
# had never been sent and answered with calibrated confidence for every
# one.  A completion gate caught it AFTER the fact and could not
# prevent it.  Built here instead, where it is deterministic -- so these legs
# are the statement of record that the pictures go with the questions.
async def _eyes() -> None:
    root = Path(tempfile.mkdtemp(prefix="escriba-choose-"))
    try:
        person = Person("demo-eyes-0001", root=root,
                        identity={"username": "dani"})
        ws = person.ws
        ident = demo.open_demo(ws)
        # MORE CAPTURES THAN ARE KEPT, because that is the only shape
        # where choosing means anything: `KEEP` of them survive and the
        # rest do not.
        for n in range(choosing.KEEP + 2):
            demo.frame(ws, ident, png(800, 600), n * 2000)

        calls: list = []
        #: The two dullest are the first two; everything else scores
        #: above them, so the selection is "all but those".
        scores = {f"F{n:04d}": 9.0 for n in range(1, choosing.KEEP + 3)}
        scores["F0001"], scores["F0002"] = 1.0, 2.0

        class Answer:
            ok, success = True, True
            result = {"answers": {k: {"type": "score", "value": v}
                                  for k, v in scores.items()}}

        class Client:
            async def invoke_plugin_tool(self, plugin, tool, args, **kw):
                calls.append((plugin, tool, args, kw))
                return Answer()

        person._scribe = type("S", (), {"client": Client()})()
        kept = await person._choose(ident)

        check("one call for a demonstration this size", len(calls), 1)
        plugin, tool, args, kw = calls[0]
        check("it is the decision plugin's own tool", (plugin, tool),
              ("decision", "decide"))
        check("run under the escriba's own profile", kw["profile"], "escriba")
        # THE WHOLE POINT.  Without `images` the decision model scores
        # file names, and answers anyway.
        check("every capture is SENT, not described", len(args["images"]),
              choosing.KEEP + 2)
        check("one question each", len(args["questions"]), choosing.KEEP + 2)
        check("and the paths are the ones the index wrote",
              [i["path"] for i in args["images"]][:3],
              [f"demo/{ident}/000{n}.png" for n in (1, 2, 3)])

        # AND IT ANSWERS NO PERMISSION OF ITS OWN.  The escriba's
        # whitelist names `decide`, so the gate never asks -- measured,
        # `"method": "whitelist"`.  An answerer here could only matter
        # by overriding a decision to take `decide` off that list.
        check("the driver answers no permission on the escriba's behalf",
              "on_permission" in kw, False)

        # BY SCORE TO CHOOSE, BY TIME TO READ.
        check("the dullest are dropped", [f["n"] for f in kept],
              list(range(3, choosing.KEEP + 3)))
        check("and what is kept is in the order it happened",
              [f["at_ms"] for f in kept][:2], [4000, 6000])
        check("written where the documenter's prefetch will look",
              [f["n"] for f in choosing.chosen(ws, ident)],
              list(range(3, choosing.KEEP + 3)))
        row = [r for r in person.journal.rows() if r["kind"] == "demo.chosen"]
        check("with a row saying how many were looked at", row[0]["scored"],
              choosing.KEEP + 2)
        check("and how many survived", row[0]["kept"], choosing.KEEP)

        # ---- A DOCUMENT NOBODY ACCEPTED IS NOT A DOCUMENT -------------
        #
        # `complete` returns None for a budget ceiling, a refused
        # completion and a stopped session alike, and raises for none of
        # them.  MEASURED 2026-10-19 (documentalista 20261019_135350):
        # the documenter wrote a file, had its payload refused by
        # `docs_are_real` for citing four memory ids that do not exist,
        # burned the rest of its budget on 63 `selectReferences` calls
        # against a memory id, ended `budget_exhausted` -- and the
        # escriba announced "el documento ya está listo, con las 12
        # capturas clave".
        class Ended:
            reason = "budget_exhausted"

        class Writer:
            terminus = Ended()

            async def complete(self, *a, **kw):
                return None

            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        import jaato_sdk as _sdk
        real_session = _sdk.IPCClient.session
        _sdk.IPCClient.session = staticmethod(lambda **kw: Writer())
        try:
            await person._commission({"id": ident, "frames": 3, "audio": "",
                                      "root": f"demo/{ident}"}, "de qué iba",
                                     kept)
            check("a document nobody accepted is not written", True, False)
        except _driver.Unwritten as exc:
            check("an unaccepted completion is reported, with its reason",
                  "budget_exhausted" in str(exc), True)
        finally:
            _sdk.IPCClient.session = real_session

        # ---- THREE NARRATION STATES, AND THEY ARE NOT THE SAME --------
        #
        # The documenter is a TEXT-AND-VISION model: it cannot hear.
        # Naming it the `.webm` was naming a file it has no way to open,
        # so everything the person explained while recording reached it
        # as nothing.  MEASURED 2026-10-19: the first document this
        # system produced had twelve captures and no prose about any of
        # them, because there was nothing to write prose from.
        seen: list = []

        class Ok(Writer):
            async def complete(self, prompt, *a, **kw):
                seen.append(prompt)
                return {"raiz": "docs/x/index.md"}

        _sdk.IPCClient.session = staticmethod(lambda **kw: Ok())
        base = {"id": ident, "frames": 3, "root": f"demo/{ident}"}
        try:
            await person._commission(dict(base, audio="n.webm"), "b", kept,
                                     "demo/x/n.txt")
            await person._commission(dict(base, audio="n.webm"), "b", kept, "")
            await person._commission(dict(base, audio=""), "b", kept, "")
        finally:
            _sdk.IPCClient.session = real_session
        check("a transcript is named as a file it can read",
              "demo/x/n.txt" in seen[0], True)
        # AND NEITHER SIDE IS ALIGNED FOR IT.  Which words explain a
        # screen is a judgement -- a sentence often lands seconds after
        # the frame it explains -- so both carry a clock and the model
        # does the matching.  A window of N seconds would be a constant
        # fitted to one speaker, which is the mistake BACKCHANNEL.md
        # records twice.
        check("and both clocks are handed over, not one alignment",
              "su momento" in seen[0], True)
        check("narration it cannot read is said to be unreadable",
              "no puedes oírla" in seen[1], True)
        check("so it is told not to claim what was explained",
              "no lo sabes" in seen[1], True)
        check("and silence is said to be silence",
              "sin micrófono" in seen[2], True)

        # NOTHING TO TRANSCRIBE IS NOT A FAILURE.  A demonstration with
        # the microphone refused, and a deployment that named no
        # `--transcribe` model, both reach here with nothing to read.
        # THE SHARED TRANSCRIBER IS BORROWED, not constructed: one is
        # held per host and `self.scribe` IS that instance, so building
        # a new one here passed it as a model name.  Caught before it
        # ever ran.
        asked: list = []

        class Lent:
            def timed(self, recording, language=None):
                asked.append((recording.name, language))
                return [(0.0, 2.0, "esto son las fuentes")]

        person.scribe = Lent()
        folder = demo.folder(ws, ident)
        (folder / "n.webm").write_bytes(b"x")
        said = person._narrate(dict(base, audio="n.webm"))
        check("the transcript is written beside the recording",
              said, f"demo/{ident}/n.txt")
        check("with a clock on its line",
              (ws / said).read_text(encoding="utf-8").strip(),
              "[00:00] esto son las fuentes")
        check("asked of the shared transcriber, in this person's language",
              asked, [("n.webm", "es")])
        # WRITTEN ONCE AND KEPT: re-transcribing the same bytes with a
        # different model would silently change what the record says a
        # person said.
        person._narrate(dict(base, audio="n.webm"))
        check("and not transcribed twice", len(asked), 1)

        person.scribe = None
        check("no transcriber, no transcript",
              person._narrate(dict(base, audio="n.webm")), "")
        check("no narration, no transcript",
              person._narrate(dict(base, audio="")), "")

        # The timed rendering itself: a clock on every line, and a piece
        # the model returned empty is not a line.
        check("the transcript carries a clock per line",
              _transcribe.timeline([(0.0, 2.0, "Hola"),
                                    (62.5, 65.0, "y aquí las fuentes"),
                                    (70.0, 71.0, "")]),
              "[00:00] Hola\n[01:02] y aquí las fuentes")

        # A CALL THAT DID NOT RUN AND A CALL THAT WAS REFUSED ARE NOT THE
        # SAME FAILURE, and neither may pass for a selection.
        class Refused(Answer):
            success = False
            result = {"error": "denied"}

        class NoClient(Client):
            async def invoke_plugin_tool(self, *a, **kw):
                return Refused()

        person._scribe = type("S", (), {"client": NoClient()})()
        try:
            await person._choose(ident)
            check("a refused call is not a selection", True, False)
        except RuntimeError as exc:
            check("a refused call says so", "refused" in str(exc), True)
    finally:
        shutil.rmtree(root)


asyncio.run(_eyes())

# ------------------------------------------------ who chooses the frames
#
# SELECTING AND WRITING ARE TWO JOBS, AND THE SPLIT IS LOAD-BEARING.
# The documentalista had `decide` first. Measured 2026-10-17, twice: given
# the index, the scores and "reading the chosen frames is not optional",
# it scored them and wrote without opening one -- the first time
# inventing what the screens showed, the second, with that blocked,
# saying nothing about them at all. So the scribe chooses and hands over
# NAMES, and the step that kept being skipped no longer exists.
#
# Each half reads fine alone. The failure when they disagree is silent:
# a decision model bound under one name and called under another is a
# refusal, and a scribe that hands over a folder instead of names leaves
# the documentalista with nothing it can open.
import yaml as _yaml                                     # noqa: E402

_here = Path(__file__).resolve().parent


def _profile(name):
    return _yaml.safe_load(
        (_here / f"template/.jaato/profiles/_base_{name}.yaml")
        .read_text(encoding="utf-8"))


def _agent(name):
    return (_here / f"template/.jaato/agents/{name}.md").read_text(encoding="utf-8")


_escriba, _documentalista = _profile("escriba"), _profile("documentalista")
_e_persona, _d_persona = _agent("escriba"), _agent("documentalista")

# ---- the scribe chooses
check("the scribe binds exactly one decision model",
      sorted(_escriba.get("decision_models") or {}), ["vista"])
check("decide is preloaded, not left to discovery",
      "decision(mode:preload, tools:[decide])" in " ".join(_escriba["plugins"]), True)

# `decide` is NOT auto-approved by its plugin, deliberately ("a call
# spends money and sends workspace content to a decision model"), and the
# policy is defaultPolicy: deny -- absent from the whitelist, every call
# is refused.
_allowed = (((_escriba.get("plugin_configs") or {}).get("permission") or {})
            .get("policy") or {}).get("whitelist", {}).get("tools") or []
check("and the scribe's whitelist lets it through", "decide" in _allowed, True)

# ---- and it no longer spawns anything
#
# EVERY DOCUMENT FAILURE ON 2026-10-19 WAS ON THE SPAWN PATH: two
# subagents dead on an empty key (jaato#1731), two documents out of one
# commission, and twice the ABSTRACT base profile named, which carries
# no key at all (jaato#1751).  The driver's own sibling session worked
# every time it ran.  So the model asks and names no profile -- there is
# none for it to get wrong -- and `spawn_subagent` is not offered at
# all, because a tool in the schema is a tool that gets called.
check("the scribe loads no subagent plugin",
      any("subagent" in p for p in _escriba["plugins"]), False)
check("and spawning is not whitelisted either",
      "spawn_subagent" in _allowed, False)
check("what it has instead is the host tool",
      "encargar_documento" in _allowed, True)
check("and its instructions name that and no profile",
      "encargar_documento(" in _e_persona
      and "spawn_subagent" not in _e_persona, True)

# ---- and no longer chooses
#
# THE PROCEDURE LEFT THE PROSE.  It used to be five numbered steps here,
# and the legs below used to read them back out of the persona -- which
# asserted that the instructions SAID the right numbers, never that
# anything did them.  They are in `choosing` now, so they are checked
# against the SDK's own limits a few lines down, where a drift raises
# instead of reading fine.
check("the scribe is told the document is not its to commission",
      "NO lo encargas tú" in _e_persona, True)
check("and not to spawn a second one",
      "encargarás un segundo documento" in _e_persona, True)
check("what is left to it is the sentence",
      "Lo que sí es tuyo es la frase" in _e_persona, True)
check("the index is no longer its to read",
      "frames.jsonl" in _e_persona, False)

# ---- the documentalista does not choose either
check("the documentalista binds no decision model",
      _documentalista.get("decision_models"), None)
_d_allowed = (((_documentalista.get("plugin_configs") or {}).get("permission") or {})
              .get("policy") or {}).get("whitelist", {}).get("tools") or []
check("and cannot call decide", "decide" in _d_allowed, False)
check("its instructions tell it to OPEN what it is handed",
      "Ábrelas todas" in _d_persona or "Ábrelas TODAS" in _d_persona, True)
check("and to link what it describes, which is what gets checked",
      "Enlaza cada captura" in _d_persona, True)
# THE CAPTURES ARRIVE BEFORE ITS FIRST TURN, mandatory, so a commission
# whose selection cannot be read aborts session preparation instead of
# starting an agent with nothing to look at.
check("and its captures are expanded into the prompt, not written into it",
      "{{!py:scripts/capturas.py}}" in _d_persona, True)

# ---- the two numbers that must not drift from the call that takes them
#
# A `score` takes 2..10 levels: the first cut asked for eleven and every
# call came back "score takes 2..10 levels, got 11". And a call with no
# images scores file names and answers anyway: the ledger recorded 8
# questions on 907 input tokens where the same 8 with pictures cost
# 14,500.  Both were prose in a persona and both shipped wrong; they are
# constants now, so this reads them against the SDK's own.
from jaato_sdk.plugins.model_provider.decisions import (  # noqa: E402
    MAX_IMAGES, MAX_SCORE_LEVELS, MIN_SCORE_LEVELS)

check("the SDK still allows 64 images a call", MAX_IMAGES, 64)
check("and 2..10 score levels", (MIN_SCORE_LEVELS, MAX_SCORE_LEVELS), (2, 10))
check("a batch fits the images a call takes",
      choosing.BATCH <= MAX_IMAGES, True)
check("and the scale is one the call will accept",
      MIN_SCORE_LEVELS <= len(choosing.LEVELS) <= MAX_SCORE_LEVELS, True)
check("the documenter is given fewer captures than one call scores",
      choosing.KEEP < choosing.BATCH, True)

# ------------------------------------------- the narration, while it runs
#
# THE LIVE PATH, which exists because `MediaRecorder` cannot be cut into
# pieces anything can decode: only the first chunk of a webm carries the
# header.  So the narration the escriba can hear DURING a demonstration
# comes off a second consumer of the same microphone, as raw samples,
# and the blob the person keeps is untouched.
#
# WHAT IS ASSERTED HERE IS THE BOUND AND THE SHAPE, not when anybody
# listens: a window is a ceiling on memory and deciding when to look at
# it is a separate question that does not live in this module either.
n = _live.Narration(window=1.0)
n.add(b"\x00\x00" * 8000, 16000, 1)
check("what has arrived is what is held", (n.held, n.heard), (0.5, 0.5))
n.add(b"\x01\x00" * 16000, 16000, 1)
check("the window is a ceiling on what is held", n.held, 1.0)
check("and not on what the demonstration has said", n.heard, 1.5)
check("the oldest is what falls off",
      n.tail(1.0)[:2], b"\x01\x00")

wav = n.wav(1.0)
check("the tail frames as a WAV", wav[:4] + wav[8:12], b"RIFFWAVE")
check("of exactly the samples held", len(wav) - 44, 16000 * 2)
check("nothing heard frames as nothing", _live.Narration().wav(1.0), b"")

# A RATE THAT MOVES MID-DEMONSTRATION IS A WIRING MISTAKE, not audio to
# resample: one page holds one `AudioContext` for the length of a
# recording, so its `sampleRate` cannot change under us.
try:
    n.add(b"\x00\x00", 44100, 1)
    check("a rate that changes mid-demo raises", "accepted", "refused")
except _live.ShapeChanged:
    check("a rate that changes mid-demo raises", "refused", "refused")


async def _live_route(ws: Path) -> None:
    """The route, and the lazy ring behind it."""
    person = Person.__new__(Person)
    person.ws = ws
    person._following = None
    # NO TRANSCRIBER, which is a deployment that named no `--transcribe`
    # model.  The acknowledgements are read off a transcript, so this is
    # the case where the escriba follows a demonstration without ever
    # interrupting it -- and the point of the legs below is that it says
    # so rather than looking broken.
    person.scribe = None
    person._ears = None
    person._listening = None
    # A REAL JOURNAL, not a stand-in: the narration arriving is one of
    # the three things the daemon cannot see, so the row it writes is
    # part of what these routes are for.
    person.journal = _journal.Journal(ws)

    class Req:
        def __init__(self, body=b"", **query):
            self.query_params = {k: str(v) for k, v in query.items()}
            self._body = body

        async def body(self):
            return self._body

    async def ready(_request):
        return person
    _app.person_of = ready

    ident = demo.open_demo(ws)

    async def refused(label, call, want):
        try:
            await call
            check(label, "accepted", want)
        except HTTPException as exc:
            check(label, exc.status_code, want)

    await refused("a rate nothing reads is refused",
                  _app.demo_voice(Req(rate=7, channels=1), ident), 400)
    await refused("half a sample is refused",
                  _app.demo_voice(Req(b"\x01", rate=48000, channels=1), ident),
                  400)
    # A DEMONSTRATION THAT IS NOT OPEN IS A 404, the same answer a frame
    # for one gets: the page has an id and the server does not agree it
    # is recording, which is a state and not a malformed request.
    await refused("narration for a demo nobody is recording is 404",
                  _app.demo_voice(Req(b"\x01\x02", rate=48000, channels=1),
                                  "20200101_000000_000000"), 404)

    await _app.demo_voice(Req(b"\x01\x02" * 480, rate=48000, channels=1), ident)
    check("a piece of live narration is held",
          len(person.demo_heard(1.0)) - 44, 960)
    # ON THE JOURNAL'S CLOCK, which is what lets the microphone lane sit
    # beside lanes the daemon reports: the page's own `at_ms` series is
    # joined at the `demo.open` row instead of being mixed into this one.
    voiced = [r for r in person.journal.rows() if r["kind"] == "demo.voice"]
    check("and the sequence records it arriving", len(voiced), 1)
    # STATED, NOT DEFAULTED.  `transcribe`'s rule is that a model is
    # named or nothing is, so a deployment with none gets no
    # acknowledgements -- and the row is how somebody finds out why the
    # escriba never spoke, instead of wondering.
    deaf = [r for r in person.journal.rows() if r["kind"] == "backchannel.deaf"]
    check("with no transcriber, the silence is recorded", len(deaf), 1)
    check("and names the reason", deaf[0]["why"], "no transcriber")
    check("and says which microphone went unheard", deaf[0]["source"], "demo")
    check("and nothing is left listening", person._listening, None)
    # THE ONE STEP NOBODY HAD EVER RUN.  `tests_backchannel` covers the
    # detector and `tests_web.js` covers the page, and `_acknowledge` is
    # the function between them -- so a `kind=` field colliding with
    # `Journal.note`'s own first parameter shipped, and every
    # acknowledgement died on a TypeError raised OUTSIDE
    # `_backchannels`'s `try`, inside a task nothing awaited.  Measured
    # 2026-10-19 on the deployed box: one transcription, no rows,
    # nothing in the log, and an escriba that had never spoken once.
    person.locale = "es"
    person.hub = _hub.Hub(lambda: {})
    person._acknowledge("demo", ident,
                        _backchannel.Boundary(at=2.8, kind="idea", room=0.4))
    spoke = [r for r in person.journal.rows() if r["kind"] == "backchannel.said"]
    check("an acknowledgement reaches the record", len(spoke), 1)
    # THE ROW'S OWN KIND IS THE ROW'S, and the boundary's kind travels
    # under a name of its own. These two legs are the collision.
    check("the row keeps its own kind", spoke[0]["kind"], "backchannel.said")
    # AND SAYS WHICH MICROPHONE IT WAS.  A demonstration's id and a
    # press's stream id are both opaque strings; a reader of the record
    # cannot tell them apart by looking, so the row says which it is
    # rather than leaving it to be inferred from the id's shape.
    check("and which microphone it was listening to", spoke[0]["source"],
          "demo")
    check("and the boundary's kind rides beside it", spoke[0]["edge"], "idea")
    check("naming a clip the page can actually fetch",
          (_app.VOICE / "es" / f"{spoke[0]['key']}.ogg").is_file(), True)
    check("with how much of the demo has been heard", voiced[0]["heard"], 0.01)
    # THE RING IS MADE ON THE FIRST PIECE, asked of the DIRECTORY rather
    # than of what the process remembers -- so a server restarted
    # mid-recording does not refuse the rest of a demonstration that is
    # plainly going on.  Nothing told this `Person` the demo opened.
    check("without anybody having announced the demo",
          person._following.ident, ident)
    check("and it knows what it is following", person._following.source,
          "demo")

    # AND A CLOSED DEMONSTRATION STOPS BEING ONE, so narration arriving
    # after the manifest is sealed is refused rather than appended to a
    # recording nobody is making any more.
    demo.close_demo(ws, ident, b"", "webm")
    person._following = None
    await refused("narration after the close is 404",
                  _app.demo_voice(Req(b"\x01\x02", rate=48000, channels=1),
                                  ident), 404)

    # ---- THE SAME EAR, POINTED AT A PRESS OF THE BUTTON ---------------
    #
    # `POST /voz` was built to let the ROOM hear somebody, and it carries
    # the identical samples in the identical shape for the whole length
    # of every press, with or without a guest to hear them -- `startRec`
    # opens that tap unconditionally.  So a long question is followed the
    # way a long narration is, and the only thing this needed was
    # pointing at audio that was already arriving.
    person.voice("abcd", b"\x01\x02" * 480, 48000, 1, "quien", "id-1")
    check("a press is followed too", person._following.ident, "abcd")
    check("and the record will say which microphone it was",
          person._following.source, "turn")
    check("with the samples in the ring",
          len(person._following.ring.wav(1.0)) - 44, 960)
    # AND IT IS NOT A DEMONSTRATION, which holds the same slot.  Handing
    # somebody's question back as the narration of a demo would be a lie
    # that sounds exactly like audio.
    check("but a press is not narration", person.demo_heard(1.0), b"")

    # THE NEWEST PRESS WINS.  `voice_end` is the ordinary ending and the
    # page sends it on every press it completes -- but a page that was
    # closed mid-press sends nothing at all, and the next press is what
    # must not then go unheard.
    person.voice("efgh", b"\x03\x04" * 480, 48000, 1, "quien", "id-1")
    check("a later press takes the microphone", person._following.ident,
          "efgh")
    person.voice_end("abcd", "id-1")
    check("and an ending for the older one changes nothing",
          person._following.ident, "efgh")

    # RELEASED BEFORE THE TURN IT JUST BECAME BEGINS TO ANSWER.  A decode
    # takes seconds and the reply starts at once, so a listener left
    # running would acknowledge over the answer.
    slow = asyncio.create_task(asyncio.sleep(30))
    person._listening = slow
    person.voice_end("efgh", "id-1")
    check("releasing the button lets the microphone go", person._following,
          None)
    await asyncio.sleep(0.01)
    check("and stops anything still decoding", slow.cancelled(), True)

    # A DEMONSTRATION KEEPS THE MICROPHONE.  The page will not start a
    # turn while one is recording -- but a GUEST's page cannot see the
    # host's demonstration at all, and a guest's press arrives here by
    # the same route the host's does.
    second = demo.open_demo(ws)
    await _app.demo_voice(Req(b"\x05\x06" * 480, rate=48000, channels=1),
                          second)
    check("a demonstration takes it", person._following.source, "demo")
    person.voice("ijkl", b"\x07\x08" * 480, 48000, 1, "otro", "id-2")
    check("and keeps it against a press", person._following.ident, second)
    check("whose samples never joined the narration",
          len(person.demo_heard(1.0)) - 44, 960)


with tempfile.TemporaryDirectory() as tmp:
    ws = Path(tmp) / "ws"
    ws.mkdir()
    asyncio.run(_live_route(ws))


# ------------------------------------------------- page against server
#
# TWO FILES THAT FAIL SILENTLY WHEN THEY DISAGREE, in the shape
# `tests_web_hub` guards `/stage` with: each half reads correctly on its
# own, and the person sees a demonstration that records nothing.
import inspect                                          # noqa: E402
import re as _re                                         # noqa: E402

page = (Path(__file__).resolve().parent / "web" / "index.html").read_text(encoding="utf-8")
posted = set(_re.findall(r'body\.append\("([^"]+)"', page))

for handler, skip in ((_app.demo_frame, {"request", "ident"}),
                      (_app.demo_close, {"request", "ident"})):
    want = {n for n, prm in inspect.signature(handler).parameters.items()
            if n not in skip and prm.annotation is not Request}
    check(f"the page posts every field {handler.__name__} declares",
          sorted(want - posted), [])

# The URLs, which no signature covers: a route renamed on one side is a
# 404 the page reports as "the demonstration could not be recorded".
for path in ("/demo/abrir", '"/demo/" + S.demo.id + "/fotograma"',
             '"/demo/" + id + "/cerrar"',
             '"/demo/" + ident + "/voz?rate="'):
    check(f"the page calls {path}", path in page, True)

# THE LIVE NARRATION IS BRACKETED BY THE DEMONSTRATION, which is the one
# thing that cannot be read from either side alone: a tap left running
# posts into a demo the server has closed, and a tap never started
# leaves the escriba deaf with nothing reporting it.
check("the demo's tap is started with the recorder",
      _re.search(r"demoRec\.start\(\);\s*\n(\s*//[^\n]*\n)*\s*tapOn\(",
                 page) is not None, True)
check("and stopped on the ordinary ending as well as the teardown",
      len(_re.findall(r"demoTap\.stop\(\); demoTap = null;", page)), 2)

# AND THE TWO TAPS HAVE TWO DESTINATIONS.  They were one holder with the
# room's URL written into it; the demonstration's narration must not be
# fanned out to a guest as a side effect of the escriba listening, so a
# single destination is the defect this shape exists to prevent.
check("a tap is told where it posts", "async function tapOn(dest)" in page, True)
check("and the room's tap is no longer the only one",
      "let pttTap = null;" in page, True)

# THE BOUNDS THE PAGE SCALES TO ARE THE ONES THE SERVER ENFORCES, and
# neither is ours: they are TypeSafe's, read from the SDK, and the vendor
# documents them as liable to change between releases. An SDK upgrade
# that moves them would otherwise leave the page scaling to a number
# nothing accepts -- every frame refused, discovered by a person who has
# just recorded a demonstration for nothing.
from jaato_sdk.plugins.model_provider import decision_images as _di  # noqa: E402

for name, theirs in (("FRAME_MAX_SIDE", _di.MAX_SIDE), ("FRAME_MIN_SIDE", _di.MIN_SIDE)):
    found = _re.search(rf"const {name} = (\d+);", page)
    check(f"the page's {name} is the SDK's", found and int(found.group(1)), theirs)

# The cadence is a declared constant rather than a literal in the timer,
# because it is the measurement this first cut exists to produce.
check("the page declares one frame cadence",
      bool(_re.search(r"const FRAME_MS = \d+;", page)), True)

print("demos OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
