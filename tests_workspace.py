"""One root, one directory per person, and nothing of theirs overwritten.

The properties that matter if this is wrong: somebody reads somebody
else's second brain, or somebody's memories are replaced by a seed file.
Neither raises an exception on its own, so they are asserted here.
"""
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

print("workspaces OK" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
