"""One root, one directory per person, and nothing of theirs overwritten.

The properties that matter if this is wrong: somebody reads somebody
else's second brain, or somebody's memories are replaced by a seed file.
Neither raises an exception on its own, so they are asserted here.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace as w                                  # noqa: E402

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<38} got {got!r}, want {want!r}")
        fail += 1


root = Path(tempfile.mkdtemp(prefix="escriba-ws-"))
try:
    # THE ROOT IS NOT OURS TO CREATE.  Left to `mkdir(parents=True)` it
    # appears with whatever the umask says — measured 0775 — and on a
    # shared host every local account can then list the directory names,
    # each of which is a sanitised authenticated principal.  A missing
    # root is a misconfiguration, and a typo'd one must not quietly
    # become a second tree that looks provisioned.
    absent = root / "no-such-root"
    try:
        w.provision("alice@example.com", root=absent)
        check("a missing root is refused", "provisioned", "refused")
    except w.NotProvisioned:
        pass
    check("and is not created either", absent.exists(), False)

    alice = w.provision("alice@example.com", root=root)
    bob = w.provision("bob@example.com", root=root)

    check("each person gets their own tree", alice.parent != bob.parent, True)
    check("both inside the one root",
          root in alice.parents and root in bob.parents, True)
    check("a workspace is <root>/<caller>/workspace", alice.name, "workspace")
    check("provisioned with profiles",
          (alice / ".jaato" / "profiles").is_dir(), True)
    # WITHOUT THIS THE SESSION NEVER OPENS.  The daemon resolves
    # `profile="escriba"` inside the selected set; with no
    # JAATO_PROFILE_SET it looks only at the top level, finds
    # `_base_escriba` and not `escriba`, and refuses with
    # ProfileNotFoundError.  Every developer workspace had a hand-written
    # `.env` from before any of this, so the gap was invisible until a
    # person was provisioned by the real path.
    env = (alice / ".env").read_text()
    check("the profile set is selected", "JAATO_PROFILE_SET=openrouter_gpt_audio" in env, True)
    check("and no credential rides along", "sk-" in env or "api" in env.lower().replace("pass://", ""), False)

    # An existing `.env` belongs to whoever wrote it.
    (alice / ".env").write_text("JAATO_PROFILE_SET=mine\n")
    w.provision("alice@example.com", root=root)
    check("an existing .env is left alone",
          (alice / ".env").read_text().strip(), "JAATO_PROFILE_SET=mine")

    # WHO A DIRECTORY BELONGS TO, where an operator can read it and the
    # session cannot rewrite it.  On the deployed host the principal is
    # Keycloak's `sub`, so `ls` shows a UUID and nothing about a person;
    # the readable name must therefore exist somewhere, and NOT in the
    # path, which anyone who can list the root can read.
    named = w.provision("f81d4fae-7dec-11d0", root=root,
                        identity={"username": "dani", "email": "d@example.com"})
    ident = json.loads((named.parent / w.IDENTITY).read_text())
    check("the identity is recorded", ident["username"], "dani")
    check("with the principal it was provisioned for",
          ident["principal"], "f81d4fae-7dec-11d0")
    check("beside the workspace, not inside it",
          (named / w.IDENTITY).exists(), False)
    # Everything under the workspace is writable by the confined session,
    # so a record the session could author is not a record.
    check("outside what the session may write",
          named in (named.parent / w.IDENTITY).parents, False)

    # Said rather than inherited from a umask.  The root above already
    # keeps other accounts out; this stops depending on that staying true.
    import stat as _stat
    check("the person's directory is private",
          oct(_stat.S_IMODE((named.parent).stat().st_mode)), "0o700")
    check("and so is the identity record",
          oct(_stat.S_IMODE((named.parent / w.IDENTITY).stat().st_mode)), "0o600")

    # Written once: a rename does not change who it was created for.
    w.provision("f81d4fae-7dec-11d0", root=root, identity={"username": "otro"})
    again = json.loads((named.parent / w.IDENTITY).read_text())
    check("and never rewritten", again["username"], "dani")

    check("provisioned with personas",
          (alice / ".jaato" / "agents" / "escriba.md").is_file(), True)

    # Nothing the framework writes while running may ride the template into
    # the next person's workspace: that would be one person's memories,
    # sessions or documents showing up in another's.
    carried = sorted(p.relative_to(alice).as_posix()
                     for p in alice.rglob("*") if p.is_file())
    leaked = [c for c in carried
              if c.startswith(("docs/", "audio/", ".jaato/memory/",
                               ".jaato/references/", ".jaato/sessions/",
                               ".jaato/logs/", ".jaato/forgotten/"))]
    check("no runtime state copied", leaked, [])
    check("no __pycache__ copied",
          [c for c in carried if "__pycache__" in c], [])

    # Second call must RETURN the workspace, never re-lay the template over
    # it: everything the person has ever said lives in there.
    (alice / ".jaato" / "memory").mkdir(parents=True)
    kept = alice / ".jaato" / "memory" / "curated.jsonl"
    kept.write_text("una memoria\n")
    check("idempotent", w.provision("alice@example.com", root=root), alice)
    check("their store survives re-provisioning", kept.read_text(),
          "una memoria\n")

    # WHO A DIRECTORY BELONGS TO IS READ BACK, and from outside the
    # workspace.  The page's header shows this, so if it came from
    # anywhere under `{workspace}/` the confined session could author
    # the name its own person is shown.  A record that is absent or
    # unparseable reads as "we do not know" rather than raising, because
    # a workspace provisioned before the proxy passed any name has one.
    check("the identity is read beside the workspace",
          w.identity(alice.parent).get("principal"), "alice@example.com")
    check("and an absent record is not an error",
          w.identity(root / "nobody"), {})
    (bob.parent / w.IDENTITY).write_text("{ not json")
    check("nor is an unparseable one", w.identity(bob.parent), {})

    # Two principals that sanitise alike are still two people.
    check("near-collisions stay apart",
          w.provision("a b", root=root) != w.provision("a-b", root=root), True)

    # A principal is authenticated, not trusted.  Sanitising leaves one path
    # segment, so none of these can reach outside the root -- and `..` or a
    # name that empties out cannot become the directory itself.
    for hostile in ("../../etc", "/etc/passwd", "a/../../b", "..", "....//"):
        ws = w.provision(hostile, root=root)
        check(f"{hostile!r} stays inside the root", root in ws.parents, True)
        check(f"{hostile!r} is one segment",
              len(ws.relative_to(root).parts), 2)

    # A template that cannot make a workspace must say so, not make a broken
    # one: a session opening in a workspace with no profile fails far away
    # from the cause.
    for bad in (root / "nope", Path(tempfile.mkdtemp(prefix="escriba-empty-"))):
        try:
            w.provision("x", root=root, template=bad)
            check(f"refuses template {bad.name}", "accepted", "refused")
        except w.NotProvisioned:
            pass

    try:
        w.provision("   ", root=root)
        check("refuses a blank principal", "accepted", "refused")
    except w.NotProvisioned:
        pass
finally:
    shutil.rmtree(root, ignore_errors=True)

# ONE STORE, NAMED THE SAME WAY BY EVERY AGENT THAT TOUCHES IT.  These
# paths are a contract between the agent that writes memories and the
# ones that read them, and nothing in the framework checks it: a profile
# pointing at a store nobody writes is valid, loads cleanly, and answers
# "no memories" forever.  It happened — the escriba was moved to the
# plugin's default path and the curator was left on the old one, so the
# scribe wrote to `.jaato/memories`, the curator read
# `.jaato/memory/escriba`, and a person's whole conversation was
# consolidated into nothing.
import yaml as _yaml
stores = {}
for prof in sorted((w.TEMPLATE / ".jaato" / "profiles").glob("*.yaml")):
    cfg = (_yaml.safe_load(prof.read_text()) or {}).get("plugin_configs") or {}
    mem = cfg.get("memory") or {}
    if "storage_path" in mem:
        stores[prof.stem] = (mem["storage_path"], mem.get("global_storage_path"))
check("more than one agent uses the store", len(stores) > 1, True)
check(f"they all name the same one {stores}", len(set(stores.values())), 1)

# AUTHORED ASSETS FOLLOW THE TEMPLATE; THE PERSON'S DATA NEVER DOES.
#
# `provision` used to return an existing workspace untouched, so every
# profile fix, persona correction and new schema shipped after somebody's
# first sign-in reached nobody — and nothing else would ever update
# those files, because `.jaato/profiles/` is write-denied to the session
# that lives there.  Found the day a telemetry opt-out shipped and the
# one person already on the host kept exporting.
#
# The first version of the fix replaced the template's TOP-LEVEL entry,
# which is `.jaato/` — and a workspace's `.jaato/` also holds their
# memories, their sessions and their logs.  It deleted all of it.  These
# checks are why that never shipped.
import time as _time
land = Path(tempfile.mkdtemp(prefix="escriba-refresh-"))
tpl = Path(tempfile.mkdtemp(prefix="escriba-tpl2-")) / "t"
_sh2 = __import__("shutil")
_sh2.copytree(w.TEMPLATE, tpl)
ws = w.provision("carol@example.com", root=land, template=tpl)
prof = ws / ".jaato" / "profiles" / "_base_escriba.yaml"

mine = {".jaato/memories/curated.jsonl": '{"mine": true}',
        ".jaato/references/auto-x.json": "{}",
        ".jaato/sessions/s.json": "{}",
        ".jaato/logs/main/session.log": "x",
        "docs/suyo.md": "# mío",
        "audio/20260101_000000/manifest.jsonl": "{}"}
for rel, body in mine.items():
    f = ws / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)

(tpl / ".jaato" / "profiles" / "_base_escriba.yaml").write_text(
    prof.read_text() + "\n# UN ARREGLO POSTERIOR\n")
(tpl / ".jaato" / "profiles" / "_base_viejo.yaml").write_text("name: viejo\n")
told = []
w.provision("carol@example.com", root=land, template=tpl, on_refresh=told.append)

check("a later fix reaches an existing person",
      "UN ARREGLO POSTERIOR" in prof.read_text(), True)
check("and a profile added later arrives",
      (ws / ".jaato" / "profiles" / "_base_viejo.yaml").is_file(), True)
check("the refresh is reported, not silent", bool(told), True)
for rel in mine:
    check(f"kept: {rel}", (ws / rel).is_file(), True)

# A profile RENAMED away must not linger, resolvable by a name this repo
# no longer has.
(tpl / ".jaato" / "profiles" / "_base_viejo.yaml").unlink()
w.provision("carol@example.com", root=land, template=tpl)
check("a profile removed from the template goes too",
      (ws / ".jaato" / "profiles" / "_base_viejo.yaml").exists(), False)

# An unchanged template is a no-op: every session open must not rewrite
# forty files and churn their mtimes.
was = prof.stat().st_mtime_ns
_time.sleep(0.02)
quiet = []
w.provision("carol@example.com", root=land, template=tpl, on_refresh=quiet.append)
check("an unchanged template rewrites nothing", prof.stat().st_mtime_ns, was)
check("and reports nothing", quiet, [])
_sh2.rmtree(land)

# A WORKSPACE THAT PREDATES STAMPING IS REFRESHED, NOT ASSUMED CURRENT.
# The first version inferred "brand new" from a missing stamp, so every
# workspace provisioned before this existed — the one person already on
# the deployed host included — was stamped as current and never
# refreshed.  Exactly the workspaces the whole change was written for.
old = Path(tempfile.mkdtemp(prefix="escriba-old-"))
oldws = w.provision("dave@example.com", root=old, template=tpl)
(oldws.parent / w.STAMP).unlink()                 # as it was before stamps
(tpl / ".jaato" / "profiles" / "_base_juez.yaml").write_text("name: _base_juez\n")
seen = []
w.provision("dave@example.com", root=old, template=tpl, on_refresh=seen.append)
check("a workspace with no stamp is refreshed", bool(seen), True)
check("and gets the template's current content",
      (oldws / ".jaato" / "profiles" / "_base_juez.yaml").read_text().strip(),
      "name: _base_juez")
_sh2.rmtree(old); _sh2.rmtree(tpl.parent)

# A template that cannot say which set to use is refused BEFORE anything
# is copied: a half-provisioned workspace that looks complete and cannot
# open a session is worse than a refusal naming the reason.
import shutil as _sh
two = Path(tempfile.mkdtemp(prefix="escriba-tpl-"))
_sh.copytree(w.TEMPLATE, two / "t", dirs_exist_ok=False)
(two / "t" / ".jaato" / "profiles" / "another_set").mkdir()
land = Path(tempfile.mkdtemp(prefix="escriba-root-"))
try:
    w.provision("bob@example.com", root=land, template=two / "t")
    check("two profile sets are refused", "provisioned", "refused")
except w.NotProvisioned as exc:
    check("the refusal names both sets", "another_set" in str(exc), True)
check("and nothing was left behind", list(land.iterdir()), [])
_sh.rmtree(two); _sh.rmtree(land)

print("workspaces OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
