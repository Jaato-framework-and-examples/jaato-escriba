"""The scripts that run inside a session: the gates, and the inventory.

A completion processor is the only thing in this project that can tell
the model it is wrong and be believed, so it is also the only thing whose
silence is indistinguishable from working.  `fuentes_reales` guards links
the PERSON will click: a URL that is almost right looks identical to one
that is right, from the model writing it and from the page rendering it,
and the person finds out by going there and arriving nowhere.

What is asserted here is the property, not the wording: that a source
survives only when this turn's own tool ledger contains it.

`wrote_something`, the other gate on this payload, has no suite yet.
Saying so here because a file with this name implies otherwise.

    python tests_scripts.py
"""
import importlib.util
import json
import sys
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "template/.jaato/scripts/processors/fuentes_reales.py"

spec = importlib.util.spec_from_file_location("fuentes_reales", SCRIPT)
fuentes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fuentes)

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<52} got {got!r}, want {want!r}")
        fail += 1


class Ledger:
    def __init__(self, *calls):
        self.tool_calls = list(calls)


def call(name, args=None, result=None, success=True):
    return {"name": name, "args": args or {}, "result": result or {},
            "success": success}


def errors(payload, *calls):
    return fuentes.validate(payload, Ledger(*calls))["errors"]


GITLAB = ("https://gitlab.averroes.lda/lda/arquitecturabackend/"
          "architecture-commons-libs/-/tree/"
          "2f78f59af93bc24d62ab806d4bcd776da475ccb6/mainframe-data-access")
NODE = call("mcp__kbwiki__kb_get_node", {"id": "mainframe-connection-pool"},
            {"sources": [f"- [architecture-commons-libs]({GITLAB}) — current"]})

# ------------------------------------------------------- nothing to check
check("no fuentes, nothing to say", errors({}), [])
check("an empty list likewise", errors({"fuentes": []}), [])

# ------------------------------------------------------------ a real link
check("a URL the turn's result carried is accepted",
      errors({"fuentes": [{"nombre": "el repo", "url": GITLAB}]}, NODE), [])

# A URL it FETCHED is one it read, even if the result says nothing useful.
check("a URL from the call's own arguments is accepted too",
      errors({"fuentes": [{"nombre": "p", "url": "https://example.org/a"}]},
             call("web_fetch", {"url": "https://example.org/a"}, {"text": "x"})),
      [])

# THE WHOLE RESULT, STRINGIFIED.  The SDK warns that a provider can
# re-encode `result` as a JSON string before the ledger is built, which
# breaks reaching into it; a substring check survives both carriers.
check("a result that arrived as a JSON string still counts",
      errors({"fuentes": [{"nombre": "el repo", "url": GITLAB}]},
             call("mcp__kbwiki__kb_get_node", {"id": "n"},
                  json.dumps({"sources": [GITLAB]}))),
      [])

# ------------------------------------------------------- an invented link
one = errors({"fuentes": [{"nombre": "el repo", "url": GITLAB + "X"}]}, NODE)
check("a URL that is one character off is refused", len(one), 1)
check("the refusal names it", GITLAB + "X" in one[0], True)
# ASKS FOR A DELETION, NOT A CORRECTION: a model told to fix a link it
# never had writes a better-looking one.
check("and says to remove it", "QUÍTALA" in one[0], True)

check("a URL from a FAILED call does not count",
      len(errors({"fuentes": [{"nombre": "p", "url": "https://example.org/a"}]},
                 call("web_fetch", {"url": "https://example.org/a"},
                      {"error": "timeout"}, success=False))),
      1)

# ----------------------------------------------------------- a wiki page
check("a node the turn opened is accepted",
      errors({"fuentes": [{"nombre": "la página",
                           "nodo": "mainframe-connection-pool"}]}, NODE), [])
other = errors({"fuentes": [{"nombre": "otra", "nodo": "no-abierta"}]}, NODE)
check("one it did not open is refused", len(other), 1)
check("and the refusal says which it DID open",
      "mainframe-connection-pool" in other[0], True)
check("a node from a listing is not an opened page",
      len(errors({"fuentes": [{"nombre": "x", "nodo": "algo"}]},
                 call("mcp__kbwiki__kb_list_nodes", {"id": "algo"},
                      {"total": 1}))),
      1)

# -------------------------------------------------------------- the shape
check("a source with neither url nor nodo is refused",
      len(errors({"fuentes": [{"nombre": "suelta"}]}, NODE)), 1)
both = errors({"fuentes": [{"nombre": "x", "url": GITLAB,
                            "nodo": "mainframe-connection-pool"}]}, NODE)
check("a source with both is refused", len(both), 1)
check("because a source is one thing or the other",
      "una cosa o la otra" in both[0], True)

# Every bad source is reported, not just the first: a refusal that names
# one of three sends the model round the loop three times.
check("all of them are named at once",
      len(errors({"fuentes": [{"nombre": "a", "url": "https://no.one/1"},
                               {"nombre": "b", "url": "https://no.one/2"},
                               {"nombre": "c", "nodo": "tampoco"}]}, NODE)), 3)

# =========================================================== the inventory
# What the scribe wakes up KNOWING, computed before its first turn and
# expanded into the persona.  It decides what the greeting can offer, and
# for a while it could only offer the wrong thing: the only part of it
# carrying any substance was a list of the THINNEST topics, so every
# greeting proposed a one-piece topic from days ago while the afternoon's
# work sat in a table as a name and a count.
inv_spec = importlib.util.spec_from_file_location(
    "inventory", HERE / "template/.jaato/scripts/inventory.py")
inventory = importlib.util.module_from_spec(inv_spec)
inv_spec.loader.exec_module(inventory)


class Memory:
    def __init__(self, tags, description, days):
        self.tags = tags
        self.description = description
        self.maturity = "validated"
        self.timestamp = (datetime.now(timezone.utc)
                          - timedelta(days=days)).isoformat()


def render(*memories):
    class Storage:
        def load_curated(self):
            return list(memories)

    class Plugin:
        _storage = Storage()

    class Registry:
        def get_plugin(self, name):
            return Plugin() if name == "memory" else None

    class Context:
        registry = Registry()

    return inventory.render(Context(), {})


# Yesterday's thin topic, and today's thick one: the shape of the day
# this was found on.
out = render(
    Memory(["satisfaccion_usuario"], "el feedback fue muy positivo", 1),
    *[Memory(["CICS_transaction_gateway"], f"pieza {i} del pool", 0)
      for i in range(5)],
    Memory(["riego"], "el balcón da al oeste", 9),
    Memory(["gatos"], "dos gatos", 20))


def section(text: str, head: str, until: str = "") -> str:
    """What one section of the inventory holds, "" when it is absent.

    Returning "" rather than indexing past the end: a section that
    stopped being emitted should report as a missing section, not as an
    IndexError that buries the rest of the suite.
    """
    if head not in text:
        return ""
    rest = text.split(head, 1)[1]
    return rest.split(until, 1)[0] if until and until in rest else rest


recent = section(out, "Lo último que me contaste", "Lo más flojo")

check("what was last talked about is detailed",
      "Lo último que me contaste" in out, True)
check("naming today's topic", "CICS_transaction_gateway" in recent, True)
# WITH SUBSTANCE, which is the whole point: the table already carried the
# name, and a name is not enough to say "shall we carry on with X".
check("with something it can actually say", "del pool" in recent, True)
# THE GAP-DIGGING SURVIVES: it is the other thing a second brain is for
# and nothing else here does.
check("the thin topic is still offered", "Lo más flojo" in out, True)
check("and it is the oldest thin one",
      "gatos" in section(out, "Lo más flojo"), True)
# Not twice: a topic that is both the newest and the thinnest would
# otherwise fill the prompt with itself.
only = render(Memory(["solo_tema"], "lo único que sé", 0))
check("a topic detailed as recent is not repeated as thin",
      only.count("lo único que sé"), 1)

# The table leads with what is recent, not with what is biggest.
rows = [l for l in out.splitlines() if l.startswith("    ") and "pieza" not in l]
check("the table is newest first",
      rows[0].split()[0], "CICS_transaction_gateway")

# ============================== every script a profile names is SHIPPED
# A profile that names a script the deploy does not have is not a
# degraded agent: `signal_completion` comes back "completion_processor
# could not be located" on every single call, the turn can never close,
# and the model is nudged until the loop gives up.  Measured 2026-10-11,
# session 20261011_185946: five calls, five refusals, NudgeExhausted, a
# greeting spoken four times.
#
# The file existed on the author's machine.  `.gitignore` carried a bare
# `.jaato/`, which matches at every depth, so `template/.jaato/` was
# ignored too; files already tracked kept working and only the NEW one
# was dropped, without a word, by `git add -A`.  Existing on disk is
# therefore not the property to assert — being COMMITTED is.
import re
import subprocess

profiles = sorted((HERE / "template/.jaato/profiles").rglob("*.yaml"))
check("there are profiles to check", bool(profiles), True)

named = set()
for profile in profiles:
    body = yaml.safe_load(profile.read_text()) or {}
    for entry in body.get("completion_processors") or []:
        named.add(entry["script"])
    schema = body.get("completion_payload_schema")
    if schema:
        named.add(schema)
for persona in sorted((HERE / "template/.jaato/agents").glob("*.md")):
    named.update(re.findall(r"\{\{!py\??:([^}]+)\}\}", persona.read_text()))

check("the profiles name some scripts", bool(named), True)
for rel in sorted(named):
    check(f"{rel} exists", (HERE / "template/.jaato" / rel).is_file(), True)

tracked = subprocess.run(["git", "ls-files", "-z", "template/.jaato"],
                         cwd=HERE, capture_output=True, text=True)
if tracked.returncode != 0:
    print("  note  the committed check was NOT run: this is not a git "
          "checkout, so whether a named script would reach a deploy is "
          "unknown here")
else:
    files = set(tracked.stdout.split("\0"))
    for rel in sorted(named):
        check(f"{rel} is committed", f"template/.jaato/{rel}" in files, True)

print("scripts OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
