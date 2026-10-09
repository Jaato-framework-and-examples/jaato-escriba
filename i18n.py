"""Every user-facing string, in one place, for both halves of the app.

ONE CATALOGUE, TWO READERS.  The page draws the chrome and this driver
writes notes into the same transcript, so a string that existed twice —
once in `index.html` and once in Python — would be two translations of
one sentence, drifting the first time either is edited.  The catalogues
are JSON because both readers can load them: Python imports them here,
and the page is HANDED the active one in its snapshot rather than
fetching it, so a locale change arrives by the same route as everything
else the page redraws from.

NO FALLBACK TO A HARDCODED STRING.  A missing key raises in the tests
and renders as the key itself at runtime — visible, greppable, and
obviously wrong.  A silent fallback to Spanish would make an untranslated
English page look finished, which is the failure that keeps a
half-translated product shipping for years.

THE DEFAULT IS SPANISH because that is what this deployment speaks and
what every existing memory, document and archived conversation is
written in.  A person may switch at any moment, including mid
conversation: the escriba's recalled memories are then in a language
they are no longer speaking, which is accepted — the alternative is
pinning somebody to the language they first signed in with.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent / "i18n"

#: The default, and the one every other catalogue is checked against.
DEFAULT = "es"


def available() -> List[str]:
    """Every locale with a catalogue, the default first."""
    found = sorted(p.stem for p in HERE.glob("*.json"))
    return [DEFAULT] + [x for x in found if x != DEFAULT]


def catalogue(locale: str) -> Dict[str, str]:
    """One locale's strings, whole.

    Handed to the page as-is.  Not merged over the default: a merge
    would paper over a missing key with Spanish text in an English page,
    which reads as a translation someone forgot to review rather than as
    the bug it is.  `tests_i18n.py` is what keeps them complete.
    """
    path = HERE / f"{_known(locale)}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _known(locale: str) -> str:
    """The locale, or the default — never a path fragment.

    `locale` reaches here from an HTTP request, so it is checked against
    the catalogues that exist rather than used to build a filename.
    """
    return locale if locale in available() else DEFAULT


def t(locale: str, key: str, **params) -> str:
    """One string, with `{named}` placeholders filled.

    A key with no entry renders as the key: loud, and cheaper to find
    than a sentence that silently stayed in the wrong language.
    """
    text = catalogue(locale).get(key)
    if text is None:
        return key
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        # A placeholder the caller did not supply.  The raw string is
        # more use than an exception thrown while drawing a page.
        return text
