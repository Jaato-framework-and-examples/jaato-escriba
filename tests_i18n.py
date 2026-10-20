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

# ------------------------------- the notes the enrichment observer writes
#
# NOT ONLY THE PAGE.  `enrichment.py` writes to the person's transcript
# too, and for months it wrote in ENGLISH into a Spanish conversation --
# with `note.searching` sitting translated in the catalogue, unused,
# because the code printed its own string instead.  The page had a guard
# and this module had none, which is exactly why this one drifted.
#
# Observed 2026-10-18 on the deployed box: "· «aplicación_web bundles
# documentación»: 1 of 8 could not be read — docs.trellix.com: HTTP
# Error 404: Not Found".
notes = (HERE / "enrichment.py").read_text(encoding="utf-8")
literal = re.findall(r'note\(\s*f?"[^"]*·', notes)
check("no note in enrichment is a hardcoded string", literal, [])
# AND EVERY KEY IT NAMES EXISTS.  A `say` of a key nobody wrote is a
# KeyError in a background task, which is the quietest failure here:
# the search simply stops happening.
used = set(re.findall(r'say\(\s*"([\w.]+)"', notes))
check("every key enrichment names is in the catalogue",
      sorted(k for k in used if k not in base), [])
check("and it names some", len(used) > 4, True)

# ------------------------------- the Art. 50(1) sentence, per language
# THE ONE USER-FACING STRING THAT IS NOT IN THIS CATALOGUE, and the only
# place that rule gives way.  `regulatory.disclosure_text` lives in the
# profile because the DAEMON chooses the wording and the `announcement`
# audit record quotes what it chose as the text as delivered -- so a
# translation of ours, rendered beside it, would make the ledger a
# statement about a sentence nobody was shown.
#
# Which is exactly why it is checked HERE: the profile and this
# catalogue are two lists of languages that have to agree, and nothing
# else makes them.  Adding a third language to the interface without a
# sentence for it would otherwise ship silently and tell that person in
# Spanish -- recorded, correctly but uselessly, as a `fallback`.
import yaml                                                  # noqa: E402

PROFILE = HERE / "template/.jaato/profiles/_base_escriba.yaml"
told = (yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
        ["regulatory"]["disclosure_text"])
check("the announcement is declared per locale, not as one string",
      isinstance(told, dict), True)
for loc in i18n.available():
    check(f"{loc} has an Art. 50(1) sentence of its own",
          bool(str(told.get(loc, "")).strip()), True)
# `*` IS WHAT EVERY OTHER LOCALE GETS, including a client that declares
# none.  Without it the framework's own English sentence is delivered to
# somebody reading a Spanish page, which is what `jaato-scaffold
# validate` reports as `disclosure_text_no_fallback`.
check("and every other locale is covered", bool(str(told.get("*", "")).strip()), True)
check("by the default language's own wording, not a third one",
      told["*"], told[i18n.DEFAULT])

print("i18n OK" if not FAILED else f"{len(FAILED)} failure(s)")
raise SystemExit(1 if FAILED else 0)
