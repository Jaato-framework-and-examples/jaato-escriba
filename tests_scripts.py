"""The scripts that run inside a session: the gates, and the inventory.

A completion processor is the only thing in this project that can tell
the model it is wrong and be believed, so it is also the only thing whose
silence is indistinguishable from working.  `fuentes_reales` guards links
the PERSON will click: a URL that is almost right looks identical to one
that is right, from the model writing it and from the page rendering it,
and the person finds out by going there and arriving nowhere.

What is asserted here is the property, not the wording: that a source
survives only when this turn's own tool ledger contains it.

`wrote_something`, the other gate on this payload, is here too. Its
property is narrower than it looks: not "a memory was stored" -- the
model can always satisfy that -- but WHICH refusal the model is handed,
because a refusal that says only "store something" is answered with
whatever is nearest, and what is nearest is the conversation that just
ended.

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

# --------------------------------------- a lookup the wiki refused
# THE PRODUCTION FAILURE, session 20261012_220833.  An MCP tool reports
# its own failure INSIDE the result, as `isError`, and the framework's
# `success` does not read it (jaato#1654) -- so the call that found no
# page arrives here looking exactly like the call that found one.  The
# model reached this gate with an invented id, was refused, opened the
# invented id because the refusal told it to, and was then let through.
MISS = call("mcp__kbwiki__kb_get_node", {"id": "rip-host-record-contract"},
            {"tool": "kb_get_node", "isError": True,
             "content": ["no node 'rip-host-record-contract'"]})
gone = errors({"fuentes": [{"nombre": "la página",
                            "nodo": "rip-host-record-contract"}]}, MISS)
check("a node whose lookup the wiki refused is not opened", len(gone), 1)
check("and the refusal says the wiki denied it",
      "no existe ninguna página" in gone[0], True)
# THE RETRY INVITATION IS GONE.  "Open it and then cite it" is an
# instruction a model can satisfy while still being wrong, and with the
# flag above unread that is exactly what it did.
check("the refusal no longer tells it to open the id",
      "kb_get_node" in gone[0], False)

check("an isError that arrived as a JSON string is still caught",
      len(errors({"fuentes": [{"nombre": "p", "nodo": "rip-x"}]},
                 call("mcp__kbwiki__kb_get_node", {"id": "rip-x"},
                      json.dumps({"isError": True, "content": ["no node"]})))),
      1)

# ------------------------------------------------- a page the librarian cited
# An id the librarian names in `[ref:<id>]` is an id that exists: it read
# the page to answer.  Accepting those is what turns the failure above
# from a dropped source into the right link, because the correct id was
# in the turn all along -- cited twice, while the model wrote it stripped.
SAID = call("mcp__kbwiki__ask_librarian", {"prompt": "¿y el contrato?"},
            {"content": ["Según [ref:feat-rip-host-record-contract], el "
                         "copybook se genera en el build padre."]})
check("a node the librarian cited is accepted without opening it",
      errors({"fuentes": [{"nombre": "el contrato",
                           "nodo": "feat-rip-host-record-contract"}]}, SAID),
      [])
stripped = errors({"fuentes": [{"nombre": "el contrato",
                                "nodo": "rip-host-record-contract"}]}, SAID)
check("the same id with its prefix removed is refused", len(stripped), 1)
check("and the refusal hands it the one that was cited",
      "feat-rip-host-record-contract" in stripped[0], True)

# CITATIONS COME FROM ANSWERS, NEVER FROM ARGUMENTS.  Reading them out of
# `args` too -- as URLs deliberately are -- would let the model cite any
# id it liked by writing it into its own prompt first.
check("a ref the model wrote in its own prompt is not a citation",
      len(errors({"fuentes": [{"nombre": "x", "nodo": "inventado"}]},
                 call("mcp__kbwiki__ask_librarian",
                      {"prompt": "háblame de [ref:inventado]"},
                      {"content": ["No tengo nada sobre eso."]}))),
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

# ------------------------------------------------------ the memory gate
WROTE = HERE / "template/.jaato/scripts/processors/wrote_something.py"
_spec = importlib.util.spec_from_file_location("wrote_something", WROTE)
wrote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wrote)


def gate(payload, *calls):
    return wrote.validate(payload, Ledger(*calls))["errors"]


STORED = call("store_memory", {"tags": ["rip"]}, {"id": "m1"})
PAYLOAD = {"anotado": "algo", "documento_encargado": False}

# ------------------------------------------------- the ledger, not the claim
check("a turn that stored something is accepted", gate(PAYLOAD, STORED), [])
check("a turn that stored nothing is refused", len(gate(PAYLOAD)), 1)
# SAID AND JUSTIFIED IS ACCEPTED. Forcing a memory on every turn would
# force one on "hello", and the only way to satisfy that is to invent.
check("and saying there was nothing is accepted",
      gate({**PAYLOAD, "nada_que_anotar": True}), [])
# BOTH AT ONCE IS NOT: one of the two is untrue, and which one matters.
check("but claiming nothing while having stored is refused",
      len(gate({**PAYLOAD, "nada_que_anotar": True}, STORED)), 1)
# A STORE THAT FAILED IS NOT A STORE, and the refusal has to say so
# differently: "you did not write" sends the model to write again,
# "your write failed" sends it to read the error.
failed = gate(PAYLOAD, call("store_memory", {}, {"error": "x"}, success=False))
check("a store that failed is told apart from no store at all",
      "ninguna salió bien" in failed[0], True)

# --------------------------------------------- WHAT THE REFUSAL ASKS FOR
# MEASURED 2026-10-12, session 20261012_194750. The person said they were
# dropping the subject and moving to RIP architecture. The scribe stored
# nothing, this gate refused with "store something", and the scribe
# satisfied it from the only other thing in context: it stored a memory
# about the PREVIOUS topic and recited it out loud. The person heard
# their change of subject answered with a recap of what they had dropped.
#
# The property is the refusal's CONTENT, because the content is what the
# model acts on. Asserted on the behaviour it has to steer -- store the
# new direction, not the old subject, and do not say the old one again --
# rather than on a form of words.
asked = gate(PAYLOAD)[0]
check("the refusal names a change of subject as the thing to store",
      "CAMBIADO DE TEMA" in asked, True)
check("and says the new direction is what to keep",
      "en qué quiere trabajar ahora" in asked, True)
check("and that the old subject is not what to fill the turn with",
      "ya está anotado" in asked, True)
# IT WAS SAID ALOUD, which is the half the person actually suffered: the
# recap was spoken, not merely stored.
check("and that it must not be recited again either",
      "en voz alta" in asked, True)
# AND THE ESCAPE HATCH IS STILL OFFERED, after all that. A refusal that
# only demands is answered by inventing something.
check("while still offering the honest way out",
      "nada_que_anotar: true" in asked, True)

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

# ------------------------------------------------ capturas_reales
#
# THE DOCUMENT THAT PROMPTED IT, reproduced: eight captures described in
# confident prose, none of them ever opened, every `decide` call carrying
# `images=0`.  `docs_are_real` passed it -- the files existed, the links
# resolved, the citations were real.  Everything was real except what it
# said.
import shutil                                            # noqa: E402
import tempfile                                          # noqa: E402

CAP = HERE / "template/.jaato/scripts/processors/capturas_reales.py"
_spec = importlib.util.spec_from_file_location("capturas_reales", CAP)
capturas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capturas)


class Space:
    """A workspace with a tool ledger, which `capturas_reales` needs both of."""

    def __init__(self, ws, *calls):
        self.workspace_path = str(ws)
        self.tool_calls = list(calls)


def _demo_space():
    ws = Path(tempfile.mkdtemp(prefix="escriba-caps-"))
    frames = ws / "demo" / "20261017_231345_581922"
    frames.mkdir(parents=True)
    for n in range(1, 4):
        (frames / f"{n:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    page = ws / "docs" / "demo" / "index.md"
    page.parent.mkdir(parents=True)
    return ws, page, frames


def caps(payload, ws, *calls):
    return capturas.validate(payload, Space(ws, *calls))["errors"]


_ws, _page, _frames = _demo_space()
try:
    REL = "../../demo/20261017_231345_581922"
    _page.write_text(
        f"# Demo\n\n![Captura 1]({REL}/0001.png)\n\nSe observa la "
        f"interfaz inicial.\n\n![Captura 2]({REL}/0002.png)\n")
    paid = {"ficheros": ["docs/demo/index.md"], "raiz": "docs/demo/index.md"}

    check("nothing claimed, nothing to check", caps({}, _ws), [])

    # THE MEASURED FAILURE: decide was called, and answered, with no
    # images -- the frames were named in the state and never sent.
    blind = call("decide", {"model": "vista", "state": "{{image:F0001}}",
                            "images": []},
                 {"answers": [{"id": "F0001", "level": 7}]})
    got = caps(paid, _ws, blind)
    check("a capture only NAMED to decide is not a capture it saw",
          len(got), 1)
    check("and the refusal names the ones it has not seen",
          "0001.png" in got[0] and "0002.png" in got[0], True)
    check("and says what sending it actually means",
          "`images`" in got[0], True)

    # The same call with the frames actually attached IS a look.
    seeing = call("decide", {"model": "vista", "state": "{{image:F0001}}",
                             "images": [
                                 {"name": "F0001",
                                  "path": "demo/20261017_231345_581922/0001.png"},
                                 {"name": "F0002",
                                  "path": "demo/20261017_231345_581922/0002.png"}]},
                  {"answers": []})
    check("a capture sent inside `images` is one it saw",
          caps(paid, _ws, seeing), [])

    # So is one it opened itself.
    opened = [call("readFile",
                   {"path": f"demo/20261017_231345_581922/{n:04d}.png"},
                   {"content": "..."}) for n in (1, 2)]
    check("a capture opened with readFile is one it saw",
          caps(paid, _ws, *opened), [])

    # A call that FAILED is not a look, however it is reported.
    check("a readFile that failed is not a look",
          len(caps(paid, _ws, call("readFile", {"path": f"{REL}/0001.png"},
                                   {"error": "nope"}, success=False))), 1)
    check("nor is one the tool flagged isError",
          len(caps(paid, _ws, call("decide", {"images": [
              {"name": "F", "path": "demo/20261017_231345_581922/0001.png"}]},
              {"isError": True}))), 1)

    # Links that are not captures are none of this gate's business.
    _page.write_text("# Demo\n\n[otra página](otra.md)\n![logo](logo.png)\n")
    check("a link that is not a capture is ignored", caps(paid, _ws), [])

    # A file it never wrote is `docs_are_real`'s refusal, not this one's:
    # the same mistake reported twice in different words reads as two.
    check("a missing document is left to the gate that owns it",
          caps({"ficheros": ["docs/demo/falta.md"]}, _ws), [])
finally:
    shutil.rmtree(_ws)

# capturas_elegidas: REMOVED 2026-10-19, with its legs.
#
# It refused a demonstration handed over on captures the scribe had
# never sent to `decide`, which was a real failure measured twice.  The
# scribe does not hand over captures any more: the DRIVER chooses them
# and the documenter's prefetch reads the selection, so the property is
# now true by construction rather than gated after the fact --
# `choosing.questions` cannot build a call without one image per
# question, `choosing.scores` raises on an answer that did not come
# back, and `choosing.best` picks only from what was scored.  Those
# three have legs in `tests_choosing.py`.
#
# An inert gate reads as protection that is not there, so it is gone
# rather than left standing.  `capturas_reales` is untouched and still
# keeps the documenter from describing a capture it never opened.

# ------------------------------------------------ scripts/capturas.py
#
# THE GATE, AND IT IS THE FRAMEWORK'S RATHER THAN OURS.  The documenter's
# persona expands this with the MANDATORY `{{!py:...}}` form, so a
# `render` that raises aborts session preparation -- which is the only
# way to stop an agent starting with no captures and describing screens
# it never opened.  Everything below is about which cases raise.
import types                                              # noqa: E402

PRE = HERE / "template/.jaato/scripts/capturas.py"
_pspec = importlib.util.spec_from_file_location("capturas_prefetch", PRE)
prefetch = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(prefetch)


def _ctx(ws, **params):
    return types.SimpleNamespace(agent_params=params, workspace_path=str(ws))


def _selection(ws, chosen, scored=29):
    (ws / "demo" / "d1").mkdir(parents=True, exist_ok=True)
    (ws / "demo" / "d1" / "elegidas.json").write_text(json.dumps(
        {"id": "d1", "scored": scored, "chosen": chosen}), encoding="utf-8")
    return "demo/d1/elegidas.json"


_pre_ws = Path(tempfile.mkdtemp(prefix="prefetch-"))
try:
    # NOT EVERY DOCUMENT IS A DEMONSTRATION.  Most commissions are a
    # topic and have no captures, so the absence of the PARAMETER is the
    # ordinary case and says nothing at all.
    check("a commission with no captures embeds nothing",
          prefetch.render(_ctx(_pre_ws), []), "")

    named = _selection(_pre_ws, [
        {"n": 3, "path": "demo/d1/0003.png", "at_ms": 6000, "score": 9.1},
        {"n": 9, "path": "demo/d1/0009.png", "at_ms": 125000, "score": 8.4}])
    text = prefetch.render(_ctx(_pre_ws, capturas=named), [])
    check("the paths reach the prompt without passing through a sentence",
          [l for l in text.splitlines() if l.startswith(("1.", "2."))],
          ["1. `demo/d1/0003.png` (00:06)", "2. `demo/d1/0009.png` (02:05)"])
    # HOW MANY WERE LOOKED AT, because "twelve of twenty-nine" and
    # "twelve because there were twelve" are different documents.
    check("and how many they were chosen from", "2 de las 29" in text, True)

    # A COMMISSION THAT SAYS IT HAS A SELECTION AND DOES NOT is the exact
    # failure this exists to stop, so it raises rather than embedding
    # nothing: an empty expansion would start the documenter blind.
    try:
        prefetch.render(_ctx(_pre_ws, capturas="demo/nope/elegidas.json"), [])
        check("a missing selection aborts the session", True, False)
    except OSError:
        check("a missing selection aborts the session", True, True)

    # AND SO DOES ONE THAT CHOSE NOTHING.  A file that parses and holds
    # no captures is the same agent with the same nothing to look at.
    empty = _selection(_pre_ws, [])
    try:
        prefetch.render(_ctx(_pre_ws, capturas=empty), [])
        check("a selection of nothing aborts it too", True, False)
    except RuntimeError as exc:
        check("a selection of nothing aborts it too, and says so",
              "sin capturas" in str(exc), True)
finally:
    shutil.rmtree(_pre_ws)


print("scripts OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
