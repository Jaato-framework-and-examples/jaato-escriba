"""The catalogues stay complete, and the page stops hardcoding strings.

A half-translated interface is the normal end state of this kind of
work, and it survives because nothing fails when a key is missing.
These are the checks that make it fail.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import i18n

HERE = Path(__file__).resolve().parent
FAILED = []


def check(what, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {what}" + ("" if ok else f"   got {got!r}, want {want!r}"))
    if not ok:
        FAILED.append(what)


# ------------------------------------------------- every locale is whole
base = i18n.catalogue(i18n.DEFAULT)
check("the default catalogue has entries", len(base) > 50, True)

for loc in i18n.available():
    other = i18n.catalogue(loc)
    check(f"{loc} has every key the default has", sorted(other) == sorted(base), True)
    empty = [k for k, v in other.items() if not str(v).strip()]
    check(f"{loc} has no blank strings", empty, [])
    # A placeholder dropped in translation is how "3 memories" becomes
    # "memories": the sentence still reads, and the number is gone.
    for key, text in base.items():
        want = set(re.findall(r"\{(\w+)\}", text))
        got = set(re.findall(r"\{(\w+)\}", other[key]))
        if want != got:
            check(f"{loc}:{key} keeps its placeholders {sorted(want)}", sorted(got), sorted(want))

# ------------------------------------------- an unknown locale is refused
# `locale` arrives from an HTTP request and must never reach the
# filesystem as a path fragment.
check("a traversal attempt resolves to the default",
      i18n.t("../../etc/passwd", "panel.memory"), base["panel.memory"])
check("so does a locale with no catalogue", i18n.t("de", "panel.memory"),
      base["panel.memory"])

# --------------------------------------------------- a missing key is loud
check("a missing key renders as itself", i18n.t("es", "no.such.key"), "no.such.key")

# ------------------------------------------- the page does not hardcode
# THE REGRESSION THIS GUARDS.  Every Spanish string the page draws must
# come from the catalogue, or an English reader gets a page that is half
# translated and looks finished.  The mock fixtures are exempt: they are
# demo CONTENT behind `?mock=1`, not chrome, and translating invented
# conversation about a balcony would be theatre.
page = (HERE / "web" / "index.html").read_text(encoding="utf-8")
body = page.split("function mockState()")[0]
spanish = re.compile(r'"[^"\n]*[áéíóúñ¿¡][^"\n]*"')
stragglers = sorted({m for m in spanish.findall(body)
                     if not m.startswith('"//')})
check("no Spanish string is left hardcoded in the page", stragglers, [])

print("i18n OK" if not FAILED else f"{len(FAILED)} failure(s)")
raise SystemExit(1 if FAILED else 0)
