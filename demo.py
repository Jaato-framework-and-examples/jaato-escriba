"""A demonstration the PERSON records, for the escriba to write up.

`staging` takes one document somebody wants discussed.  This takes the
other shape of contribution: minutes of their screen and their voice,
captured while they show how something is done, so that what comes out
is a procedure somebody else can follow.

WHY ITS OWN AREA AND NOT `staged/`.  A demonstration is hundreds of
frames.  Staging declares one catalogue entry per file in
`.jaato/references`, which is right for a handful of documents and would
bury both that catalogue and the panel reading it under one afternoon's
recording.  So a demo is ONE thing with many parts -- a directory, a
manifest, and at most one reference for the whole of it.

WHY THE SERVER NAMES THE FILES.  `staging.safe_name` exists because a
browser chooses a document's name and that name becomes a path.  Nothing
here needs the browser's name: frames arrive in order and are numbered,
so the oldest trick there is has nothing to work on.  A guard that
cannot be reached beats a guard that holds.

WHY THE BOUNDS ARE JAATO'S OWN.  A frame is stored only if
`decision_images` accepts it -- the same `read_image_info` and
`image_limit_problem` the `decide` tool calls before sending.  Those
limits are TypeSafe's, the vendor documents them as liable to change
between releases, and a copy of them here would be a second table to
keep in step with the first.  Read from the SDK, a frame on disk is a
frame the model will take, by construction.  And it is refused AT THE
DOOR, while the person is still recording and can be told, rather than
at document time when the demo is over and they have gone.

THE CEILING IS PER DEMO, not per upload.  `staging.MAX_BYTES` bounds one
POST, which is the right unit for a document and the wrong one for a
stream: at a frame every two seconds, nothing would stop an afternoon
from filling the host's disk, and the first thing to break is every
other person's recordings.  Sized against the figure `pyproject`'s `stt`
extra argues from -- 200 MB of recordings per person on a host with 2 GB
free -- so one demo costs about what one person's whole archive does,
not ten times it.

WHAT IS OPEN IS WHAT HAS NO MANIFEST.  `demo.json` is written when the
demo closes, so its absence IS the open state -- there is no flag to
leave stale and no second place to disagree with.  A demo whose browser
went away mid-recording (laptop shut, share stopped from the browser's
own bar) stays open and readable rather than becoming a directory of
PNGs with no times in it, because `frames.jsonl` is appended as each
frame lands instead of composed at the end.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jaato_sdk.plugins.model_provider.decision_images import (
    ImageHeaderError, image_limit_problem, read_image_info)

#: Where a person's demonstrations land.  A sibling of `audio/` and
#: `staged/`, and like both of them in `workspace.RUNTIME`: it is the
#: person's own recording and must never travel in a template.
ROOT = Path("demo")

#: One demo's directory, and its id: it sorts, it reads as a time, and it
#: needs nothing from the browser.
#:
#: MICROSECONDS, where `archive`'s stamp of the same shape stops at
#: seconds.  There the stamp names a folder several files share; here it
#: IS the identity, and `tests_demo` caught what that difference costs:
#: two demos opened in the same second got one id, and because the
#: directory was made with `exist_ok=True` the second silently reopened
#: the first -- which, once the first had been closed, means frames
#: appended to a demonstration already written up.  `open_demo` now
#: refuses to reuse a directory at all rather than absorbing the clash.
STAMP = "%Y%m%d_%H%M%S_%f"

#: Appended as each frame lands; see the module docstring.
FRAMES = "frames.jsonl"

#: Written once, at close.  Its presence is the closed state.
MANIFEST = "demo.json"

#: The narration, stored in whatever container the browser produced.  It
#: is not transcoded here: `archive` converts a turn's audio because the
#: provider takes mp3, and what becomes of this one is the next layer's
#: decision, not storage's.
AUDIO = "narration"

#: Half an hour at one frame every two seconds.  A ceiling on COUNT as
#: well as bytes, because the two fail differently: bytes protect the
#: disk, and a count protects the document -- nobody writes a procedure
#: out of two thousand screenshots, and a demo that long is a mistake
#: worth stopping while it can still be re-recorded.
MAX_FRAMES = 900

#: What one demo may occupy, frames and narration together.
MAX_BYTES = 192 * 1024 * 1024


class TooLarge(ValueError):
    """The demo is over `MAX_BYTES` or `MAX_FRAMES`, and silence would
    look like a bug.  Reported, never enforced by truncation: a demo cut
    off halfway documents half a procedure and says so nowhere."""


class NotAFrame(ValueError):
    """The bytes are not an image the `decide` tool would accept.

    Carries jaato's own words, because they already say what to do about
    it ("scale or crop it first") and a second phrasing here would be a
    second thing to keep true.
    """


class NoSuchDemo(KeyError):
    """No demo with that id, or it is already closed."""


class AlreadyOpen(RuntimeError):
    """One demo at a time.

    The same shape as `FloorTaken`: not a failure of anything, just a
    press that raced one already recording.  Two demos at once would
    fan one screen into two directories and leave the narration in
    whichever opened last.
    """


def _dir(workspace: Path, ident: str) -> Path:
    """One demo's directory.

    The id is matched against its shape rather than sanitised, the same
    treatment `app._recording` gives an attachment id: it becomes a path
    component, it arrives over the wire, and the only safe handling of
    such a value is to refuse anything that is not exactly what this
    module writes.
    """
    if not ident.isascii() or not ident.replace("_", "").isdigit():
        raise NoSuchDemo(ident)
    return workspace / ROOT / ident


def open_id(workspace: Path) -> Optional[str]:
    """The demo currently recording, if there is one.

    Read from the directory rather than held in memory, so a restart
    mid-demo does not lose track of it -- the frames are on disk either
    way, and a server that forgot would open a second demo beside the
    first.
    """
    root = workspace / ROOT
    if not root.is_dir():
        return None
    for folder in sorted(root.iterdir(), reverse=True):
        if folder.is_dir() and not (folder / MANIFEST).exists():
            return folder.name
    return None


def open_demo(workspace: Path) -> str:
    """Start recording, and answer with the id the frames will carry."""
    if open_id(workspace) is not None:
        raise AlreadyOpen("ya hay una demostración grabándose")
    ident = datetime.now().strftime(STAMP)
    folder = _dir(workspace, ident)
    folder.parent.mkdir(parents=True, exist_ok=True)
    # `exist_ok=False`, AND NOT HANDLED.  At microsecond resolution a
    # collision cannot happen at human timescales, so one would mean the
    # clock went backwards or the id stopped being the identity -- and
    # absorbing that is how the bug above got in.  It raises.
    folder.mkdir()
    return ident


def _used(folder: Path) -> tuple[int, int]:
    """How many frames this demo holds and how many bytes it occupies.

    Measured from the directory, not accumulated in a counter: the
    counter would be a second account of the same fact, and the one on
    disk is the one that matters to the disk.
    """
    files = [f for f in folder.iterdir() if f.is_file()]
    frames = len([f for f in files if f.suffix in (".png", ".jpg", ".jpeg")])
    return frames, sum(f.stat().st_size for f in files)


def frame(workspace: Path, ident: str, data: bytes, at_ms: int) -> Dict[str, Any]:
    """Store one frame of a demo in progress.

    `at_ms` is the page's own offset from the demo's start, in
    milliseconds, and is kept as the page gave it: the server's clock
    knows when the POST arrived, which is the frame's time plus however
    long the upload took, and a procedure aligning narration to
    screenshots wants the former.
    """
    folder = _dir(workspace, ident)
    if not folder.is_dir() or (folder / MANIFEST).exists():
        raise NoSuchDemo(ident)
    try:
        info = read_image_info(data)
    except ImageHeaderError as exc:
        raise NotAFrame(str(exc)) from exc
    problem = image_limit_problem(info, len(data))
    if problem:
        raise NotAFrame(problem)

    count, size = _used(folder)
    if count >= MAX_FRAMES:
        raise TooLarge(f"{count} frames; the ceiling is {MAX_FRAMES}")
    if size + len(data) > MAX_BYTES:
        raise TooLarge(f"{size + len(data)} bytes; the ceiling is {MAX_BYTES}")

    n = count + 1
    name = f"{n:04d}.{'png' if info.format == 'png' else 'jpg'}"
    (folder / name).write_bytes(data)
    row = {"n": n, "file": name, "at_ms": int(at_ms), "bytes": len(data),
           "w": info.width, "h": info.height}
    with (folder / FRAMES).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def close_demo(workspace: Path, ident: str, audio: bytes,
               suffix: str) -> Dict[str, Any]:
    """Stop recording: store the narration and write the manifest.

    The manifest is written LAST, because its existence is what marks
    the demo closed — so a crash between the audio and the manifest
    leaves a demo that is still open and can be closed again, rather
    than one that claims to be finished without its narration.
    """
    folder = _dir(workspace, ident)
    if not folder.is_dir() or (folder / MANIFEST).exists():
        raise NoSuchDemo(ident)
    count, size = _used(folder)
    if size + len(audio) > MAX_BYTES:
        raise TooLarge(f"{size + len(audio)} bytes; the ceiling is {MAX_BYTES}")

    name = ""
    if audio:
        name = AUDIO + ("." + suffix.strip(".").lower() if suffix else "")
        (folder / name).write_bytes(audio)

    manifest = {
        "id": ident,
        "closed_at": datetime.now().isoformat(timespec="seconds"),
        "frames": count,
        "audio": name,
        "bytes": size + len(audio),
        # Workspace-relative, like `staging`'s `path`: the session
        # resolves it against ITS workspace, and an absolute path would
        # be this host's rather than the runner's.
        "root": (ROOT / ident).as_posix(),
    }
    (folder / MANIFEST).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def frames(workspace: Path, ident: str) -> List[Dict[str, Any]]:
    """Every frame of one demo, in the order they were captured.

    Read from `frames.jsonl` rather than from the directory listing: the
    listing has the files and not their times, and the times are what a
    procedure aligns narration against.  A line that will not parse is
    skipped rather than fatal — one torn append at a power cut should
    cost its own frame and not the whole demonstration.
    """
    path = _dir(workspace, ident) / FRAMES
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def manifest(workspace: Path, ident: str) -> Optional[Dict[str, Any]]:
    """One closed demo's manifest, or None while it is still recording."""
    path = _dir(workspace, ident) / MANIFEST
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
