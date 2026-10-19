"""Choosing which captures of a demonstration are worth describing.

A demonstration is hundreds of screenshots and a documenter can be given
a dozen: every capture it opens stays in the conversation as an image and
is resent on every turn, so opening them all spends the budget before a
line is written.  Choosing is therefore not an optimisation, it is what
makes the document possible at all.

WHY THIS IS NOT THE SCRIBE'S JOB ANY MORE, which is the whole reason this
module exists.  The choosing is five mechanical steps -- join a folder to
a filename, cut the list into batches the call accepts, write one question
per capture, keep the best twelve, put them back in time order -- and not
one of them needs judgement.  Only the scoring does, and that is the
decision model's.  Asking a conversational model to do the other four has
been measured failing FOUR times:

  2026-10-17, twice, by two different agents: `decide` called with no
  images at all, the frames named in prose instead, and the decision
  model scoring screenshots it had never been sent -- with calibrated
  confidence for every one.  A completion gate caught that twice and
  could not prevent it; this module makes it unreachable instead, and
  the gate was removed once nothing could reach it.

  2026-10-19, 06:50: the same call made correctly, which is the point --
  it is not that the model cannot, it is that it does not reliably.

  2026-10-19, 10:28: `decide` with twelve questions for twenty-nine
  captures and no `images` key, Jev answering blind at confidence 0.0,
  followed by thirty `readFile` calls on a `frames/` subdirectory that
  has never existed.  Nothing was documented.

So the steps that are mechanical are done here, where they are
deterministic and can be tested, and the model is asked only the question
a model is for.

THE TEXTS ARE NOT HERE.  What the decision model reads is prompt, and
prompt lives in `i18n` with every other word addressed to a model, so
`questions` takes them from its caller.  This module decides SHAPE.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import demo as _demo

#: What one `decide` call accepts: 32 questions and 64 images.  The
#: binding constraint is the questions, because this asks one per
#: capture and sends one image per question.
BATCH = 32

#: How many captures the documenter is given.  Twelve is the number the
#: documenter's own persona explains, and it is small on purpose.
KEEP = 12

#: The scale, lowest first.  `levels` is an array of STRINGS, two to ten
#: of them -- not a count, and not numbers.  Read off the live schema
#: rather than guessed at: `jaato-scaffold` prints the base tool, and the
#: per-session shape comes from `describe_plugin_tool`.
LEVELS = [str(n) for n in range(1, 11)]

#: The decision model, bound by NAME in the profile's `decision_models:`
#: so it can move without touching this.
MODEL = "vista"

#: The selection, written beside the index it was made from.
CHOSEN = "elegidas.json"


def name(frame: Dict[str, Any]) -> str:
    """What one capture is called in a question and in its image.

    THE SAME STRING FOR BOTH, because they are joined by it: the image
    travels as `{"name": N, "path": ...}` and the question refers to it
    as `{{image:N}}`, and an image sent and never referred to is refused
    by the framework -- as is a reference to an image that was not sent.
    """
    return f"F{frame['n']:04d}"


def batches(frames: Sequence[Dict[str, Any]],
            size: int = BATCH) -> List[List[Dict[str, Any]]]:
    """The captures, cut into calls that fit."""
    return [list(frames[i:i + size]) for i in range(0, len(frames), size)]


def questions(batch: Sequence[Dict[str, Any]], state: str,
              asking: str) -> Dict[str, Any]:
    """One `decide` call: every capture scored, every image referred to.

    THE REFERENCE IS APPENDED, not substituted into `asking`: the text
    comes from `i18n` like every other word addressed to a model, and a
    `{image}` placeholder in a catalogue entry would be a second thing
    that has to stay true across two languages.  Where in the
    instructions it sits does not matter; that it is there does.

    The question about a capture refers to that capture and to no other,
    so every image in the call is referred to exactly once and the
    pairing cannot drift.  Putting the references in `state` instead
    would work and would put thirty-two of them in one string, where one
    missing is a refusal naming an id rather than a question with no
    picture.
    """
    return {
        "model": MODEL,
        "state": state,
        "questions": [{"id": name(f), "type": "score",
                       "instructions": f"{asking} {{{{image:{name(f)}}}}}",
                       "levels": list(LEVELS)} for f in batch],
        "images": [{"name": name(f), "path": f["path"]} for f in batch],
    }


def scores(batch: Sequence[Dict[str, Any]],
           answers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each capture with what it scored.

    `value` IS AN INDEX INTO `levels`, NOT A LEVEL.  Measured against a
    live call on 2026-10-19: ten levels labelled "1".."10", a
    distribution peaked on the first of them, and `value` 0.56 -- the
    probability-weighted mean of the INDICES 0..9, which the answer's
    own `legend` maps back to the labels.  So it runs 0 to
    `len(LEVELS) - 1` and a reader expecting one to ten is reading it
    wrong.

    That costs nothing here because nothing compares it to a threshold:
    it is used to RANK, and the expectation over the levels is exactly
    what makes a ranking of near-equals meaningful where the winning
    level alone would tie them.

    A capture with no answer is a question that was asked and not
    returned.  It is not scored zero and it is not dropped silently: the
    caller is handed the gap, because a selection made from a partial
    answer is exactly the "works perfectly on the wrong twelve" failure
    the gate downstream exists to prevent.
    """
    out = []
    for frame in batch:
        answer = answers.get(name(frame))
        if answer is None:
            raise Unanswered(f"{name(frame)} was asked and not answered")
        out.append(dict(frame, score=float(answer["value"]),
                        confidence=answer.get("confidence")))
    return out


def best(scored: Sequence[Dict[str, Any]],
         keep: int = KEEP) -> List[Dict[str, Any]]:
    """The `keep` best captures, back in the order they happened.

    BY SCORE TO CHOOSE, BY TIME TO READ.  A procedure is steps in order,
    so a document assembled in score order is the same screens telling a
    story nobody performed.  Fewer captures than `keep` keeps them all,
    which needs no case of its own: that is what the slice does.
    """
    picked = sorted(scored, key=lambda f: f["score"], reverse=True)[:keep]
    return sorted(picked, key=lambda f: (f["at_ms"], f["n"]))


class Unanswered(RuntimeError):
    """A question was asked and its answer did not come back."""


def write(workspace: Path, ident: str,
          chosen: Sequence[Dict[str, Any]], scored: int) -> Path:
    """Record the selection beside the demonstration it belongs to.

    A FILE AND NOT A PROMPT, which is the point of the whole change: the
    documenter's own prefetch reads this at session-prep, so the twelve
    paths reach it without passing through anybody's sentence.  `scored`
    is how many captures were looked at to produce them, which is the
    one thing the list itself cannot say.
    """
    path = _demo.folder(workspace, ident) / CHOSEN
    path.write_text(json.dumps(
        {"id": ident, "scored": scored, "chosen": list(chosen)},
        indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def chosen(workspace: Path, ident: str) -> List[Dict[str, Any]]:
    """The selection, or nothing if none was ever made."""
    path = _demo.folder(workspace, ident) / CHOSEN
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["chosen"]
