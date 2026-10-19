"""Which twelve captures a demonstration is documented from.

WHAT THIS SUITE IS FOR.  Every step here used to be done by a
conversational model inside one turn, and the failures it produced were
silent: a selection made without looking is twelve arbitrary frames, and
everything downstream then works perfectly on the wrong twelve.  The
document comes out honest about what it shows and useless, which is the
expensive kind of wrong -- nothing raises, and the person finds out by
following a procedure that is not the one they performed.

So the mechanical half now lives in `choosing`, where it can be asserted
rather than hoped for.  No decision model is called: the answers below
are written by hand, which is the only way a ranking rule can be a
statement of record instead of a sample.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import choosing                                            # noqa: E402
import demo                                                # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<48} got {got!r}, want {want!r}")
        fail += 1


def frames(n: int):
    return [{"n": i, "file": f"{i:04d}.png", "path": f"demo/d1/{i:04d}.png",
             "at_ms": i * 2000} for i in range(1, n + 1)]


# ---- what fits in one call -------------------------------------------
#
# THE CALL'S OWN CEILING, not a number chosen here: 32 questions and 64
# images, and this asks one question per capture with one image each, so
# the questions bind first.
check("a demonstration under the ceiling is one call",
      [len(b) for b in choosing.batches(frames(29))], [29])
check("and over it is cut into calls that fit",
      [len(b) for b in choosing.batches(frames(70))], [32, 32, 6])
check("nothing recorded is nothing asked", choosing.batches([]), [])

# ---- every image referred to, exactly once ---------------------------
#
# The framework refuses BOTH halves of the mismatch -- an image sent and
# never referred to, and a `{{image:NAME}}` naming an image not sent --
# so the pairing is the thing that must not drift.  Asserted by building
# the call and reading both sides of it.
call = choosing.questions(frames(3), "el estado", "¿cuánto avanza?")
check("the model is bound by name, never by id", call["model"], "vista")
check("one question per capture", len(call["questions"]), 3)
check("one image per capture", len(call["images"]), 3)
named = {i["name"] for i in call["images"]}
check("and the questions name exactly those images",
      {q["id"] for q in call["questions"]}, named)
check("each question refers to its own capture and no other",
      ["{{image:%s}}" % q["id"] in q["instructions"]
       for q in call["questions"]], [True, True, True])
check("the image travels as a path, never as bytes",
      sorted(call["images"][0]), ["name", "path"])
check("which is the path the index wrote down",
      call["images"][0]["path"], "demo/d1/0001.png")

# ---- the scale -------------------------------------------------------
#
# `levels` is an array of STRINGS, two to ten, lowest first.  Read off
# the live schema with `describe_plugin_tool`, not guessed: a count
# would be accepted by nothing and a list of ints by nothing either.
levels = call["questions"][0]["levels"]
check("ten levels", len(levels), 10)
check("as strings", all(isinstance(v, str) for v in levels), True)
check("lowest first", (levels[0], levels[-1]), ("1", "10"))

# ---- reading the answers ---------------------------------------------
batch = frames(4)
answers = {"F0001": {"type": "score", "value": 2.0, "confidence": 0.9},
           "F0002": {"type": "score", "value": 9.4, "confidence": 0.8},
           "F0003": {"type": "score", "value": 7.1, "confidence": 0.7},
           "F0004": {"type": "score", "value": 9.9, "confidence": 0.6}}
scored = choosing.scores(batch, answers)
check("every capture comes back with its score",
      [f["score"] for f in scored], [2.0, 9.4, 7.1, 9.9])
# AN INDEX INTO `levels`, NOT A LEVEL, and never compared to anything
# but another one of itself.  Measured on a live call: ten levels
# labelled "1".."10" and a `value` of 0.56.  So these legs rank, and
# nothing anywhere asks whether a score is "high".
check("the score is kept as given, not rounded to a level",
      scored[1]["score"], 9.4)
check("and a run of them is ordered, never thresholded",
      choosing.best(choosing.scores(batch, {
          k: {"type": "score", "value": v}
          for k, v in zip(["F0001", "F0002", "F0003", "F0004"],
                          [0.11, 0.52, 0.33, 0.49])}), keep=2),
      [f for f in choosing.scores(batch, {
          k: {"type": "score", "value": v}
          for k, v in zip(["F0001", "F0002", "F0003", "F0004"],
                          [0.11, 0.52, 0.33, 0.49])}) if f["n"] in (2, 4)])

# A QUESTION ASKED AND NOT ANSWERED IS NOT A ZERO.  Scoring it zero
# would quietly drop a capture that may have been the best one, and the
# selection would look complete.
try:
    choosing.scores(batch, {k: v for k, v in answers.items() if k != "F0003"})
    check("a missing answer is refused", True, False)
except choosing.Unanswered as exc:
    check("a missing answer is refused, by name", "F0003" in str(exc), True)

# ---- choosing, and the order they are read in ------------------------
#
# BY SCORE TO CHOOSE, BY TIME TO READ.  A procedure is steps in order,
# so a document assembled in score order shows the same screens telling
# a story nobody performed.
best = choosing.best(scored, keep=2)
check("the best two are chosen", [f["n"] for f in best], [2, 4])
check("and handed over in the order they happened",
      [f["at_ms"] for f in best], [4000, 8000])
check("fewer captures than asked for keeps them all",
      len(choosing.best(scored, keep=99)), 4)

# ---- the file the documenter's prefetch reads ------------------------
root = Path(tempfile.mkdtemp(prefix="choosing-"))
try:
    ident = demo.open_demo(root)
    path = choosing.write(root, ident, best, scored=4)
    check("the selection lands beside the demonstration",
          path.parent, demo.folder(root, ident))
    check("and reads back in the order it was written",
          [f["n"] for f in choosing.chosen(root, ident)], [2, 4])
    kept = json.loads(path.read_text(encoding="utf-8"))
    # HOW MANY WERE LOOKED AT is the one thing the list cannot say, and
    # it is the difference between "twelve chosen from twenty-nine" and
    # "twelve because there were twelve".
    check("with how many were scored to produce it", kept["scored"], 4)
    check("and which demonstration it belongs to", kept["id"], ident)
    # A DEMONSTRATION NOBODY CHOSE FOR ANSWERS EMPTY, not raises: the
    # prefetch asks this of every commission, and a document that is not
    # a demonstration has no selection by construction.
    demo.close_demo(root, ident, b"", "webm")
    other = demo.open_demo(root)
    check("a demonstration with no selection has none",
          choosing.chosen(root, other), [])
finally:
    __import__("shutil").rmtree(root)

print("choosing OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
