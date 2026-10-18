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

MANIFEST = {"id": "20261017_223855_438804", "frames": 49,
            "root": "demo/20261017_223855_438804",
            "audio": "narration.webm", "bytes": 18393684}


async def _said(person, manifest) -> list:
    """Every prompt `demo_closed` sends, with no daemon behind it."""
    out: list = []
    real, _driver._turn = _driver._turn, (
        lambda scribe, prompt, att, relay, **kw: out.append(prompt) or _ok(None))
    person._scribe = object()
    try:
        await person.demo_closed(dict(manifest))
    finally:
        _driver._turn = real
    return out


async def _ok(value):
    return value


async def _timeout():
    raise asyncio.TimeoutError


async def _commission() -> None:
    root = Path(tempfile.mkdtemp(prefix="escriba-demo-"))
    try:
        person = Person("demo-sub-0001", root=root,
                        identity={"username": "dani", "email": "dani@ejemplo.es"})
        said = await _said(person, MANIFEST)

        check("commissioning and announcing are two turns", len(said), 2)
        check("the first names the index, not the folder",
              "frames.jsonl" in said[0], True)
        # CHOOSE FIRST, HAND OVER SECOND.  The scribe picks the frames
        # and the documentalista is given their NAMES; a commission that
        # points at the folder leaves it with nothing it can open,
        # because it has no way to list a directory.
        check("the commission puts choosing before handing over",
              said[0].index("PRIMERO") < said[0].index("DESPUÉS"), True)
        check("and says to name the paths, not the folder",
              "caminos en orden" in said[0], True)
        check("and what handing over a folder costs",
              "no encontrará nada" in said[0], True)
        check("the first keeps quiet, because it is not heard there",
              "No hables en este turno" in said[0], True)
        check("the narration is named by file, so it can be opened",
              "narration.webm" in said[0], True)
        check("neither turn notes anything, so nothing is enriched",
              [p.count("nada_que_anotar: true") for p in said], [1, 1])

        # THE SECOND TURN ENTERS NO TIER.  The session's `initial` is
        # `voz`, so a turn that enters nothing is heard by construction --
        # which is the whole reason this is a turn and not a sentence
        # ordered inside the first one.
        announced = said[1] if len(said) > 1 else ""
        check("the second turn speaks in voz", "`voz`" in announced, True)
        check("and is told to stay out of escribano",
              "No entres en `escribano`" in announced, True)

        # A demonstration recorded with the microphone refused is still a
        # sequence of screens.  What must not happen is a prompt naming a
        # narration file that is not there.
        quiet = await _said(person, dict(MANIFEST, audio=""))
        check("a demo with no narration says so", "sin micrófono" in quiet[0], True)
        check("and names no audio file", "narration" in quiet[0], False)

        # NOTHING COMMISSIONED AND COMMISSIONED-BUT-UNANNOUNCED ARE NOT
        # THE SAME FAILURE.  The first means the document will never
        # exist; announcing it then would be a lie.
        tried: list = []
        real, _driver._turn = _driver._turn, (
            lambda scribe, prompt, att, relay, **kw:
            tried.append(prompt) or _timeout())
        person._scribe = object()
        try:
            await person.demo_closed(dict(MANIFEST))
        finally:
            _driver._turn = real
        check("a commission that times out is not announced", len(tried), 1)

        # AND THE LIVE BUFFER IS RELEASED, on the ordinary ending.  What
        # it held was the recent past of a recording in progress, and
        # the whole narration has just landed in the demo's own
        # directory -- so keeping a lossy window of it in memory until
        # the next demonstration would be holding the worse copy.
        person._demo_voice = ("20261017_223855_438804", _live.Narration())
        await _said(person, MANIFEST)
        check("closing releases the live narration", person._demo_voice, None)
    finally:
        shutil.rmtree(root)


asyncio.run(_commission())

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
check("and names it in its own instructions", "`vista`" in _e_persona, True)
check("decide is preloaded, not left to discovery",
      "decision(mode:preload, tools:[decide])" in " ".join(_escriba["plugins"]), True)

# `decide` is NOT auto-approved by its plugin, deliberately ("a call
# spends money and sends workspace content to a decision model"), and the
# policy is defaultPolicy: deny -- absent from the whitelist, every call
# is refused.
_allowed = (((_escriba.get("plugin_configs") or {}).get("permission") or {})
            .get("policy") or {}).get("whitelist", {}).get("tools") or []
check("and the scribe's whitelist lets it through", "decide" in _allowed, True)

# ---- the documentalista does not
check("the documentalista binds no decision model",
      _documentalista.get("decision_models"), None)
_d_allowed = (((_documentalista.get("plugin_configs") or {}).get("permission") or {})
              .get("policy") or {}).get("whitelist", {}).get("tools") or []
check("and cannot call decide", "decide" in _d_allowed, False)
check("its instructions tell it to OPEN what it is handed",
      "Ábrelas todas" in _d_persona, True)
check("and to link what it describes, which is what gets checked",
      "Enlaza cada captura" in _d_persona, True)

# ---- the two numbers the scribe must not invent
#
# A `score` takes 2..10 levels: the first cut said "de 0 a 10" -- eleven --
# and every call came back "score takes 2..10 levels, got 11". And nothing
# told it to pass `images`, so it called decide with `images=0`: the ledger
# recorded 8 questions on 907 input tokens, a text-only call, and Jev
# scored the frames from their FILE NAMES. With the images attached the
# same 8 questions cost 14,500.
from jaato_sdk.plugins.model_provider.decisions import (  # noqa: E402
    MAX_IMAGES, MAX_SCORE_LEVELS, MIN_SCORE_LEVELS)

check("the SDK still allows 64 images a call", MAX_IMAGES, 64)
check("and 2..10 score levels", (MIN_SCORE_LEVELS, MAX_SCORE_LEVELS), (2, 10))
check("the scribe batches by the question limit, not the image one",
      "32 capturas" in _e_persona, True)
check("asks for a legal number of levels",
      "1 a 10" in _e_persona and "de 0 a 10" not in _e_persona, True)
check("attaches the images rather than only naming them",
      "`images`" in _e_persona and '"path"' in _e_persona, True)
check("says what an imageless call costs",
      "Sin `images` no se ve nada" in _e_persona, True)
check("and caps what reaches the documentalista",
      "12 mejores" in _e_persona, True)

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
    person._demo_voice = None

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
    # THE RING IS MADE ON THE FIRST PIECE, asked of the DIRECTORY rather
    # than of what the process remembers -- so a server restarted
    # mid-recording does not refuse the rest of a demonstration that is
    # plainly going on.  Nothing told this `Person` the demo opened.
    check("without anybody having announced the demo",
          person._demo_voice[0], ident)

    # AND A CLOSED DEMONSTRATION STOPS BEING ONE, so narration arriving
    # after the manifest is sealed is refused rather than appended to a
    # recording nobody is making any more.
    demo.close_demo(ws, ident, b"", "webm")
    person._demo_voice = None
    await refused("narration after the close is 404",
                  _app.demo_voice(Req(b"\x01\x02", rate=48000, channels=1),
                                  ident), 404)


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
