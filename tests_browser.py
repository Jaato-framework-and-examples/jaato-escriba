"""What the page actually DOES when the person clicks, in a real browser.

THE ONE THING THE OTHER SUITES CANNOT SEE.  `tests_web.js` runs the
page's own script against a DOM stub and `tests_web_hub.py` drives the
routes directly, so between them every line of the upload path was
covered and the feature was dead on arrival anyway: `web/index.html`
declared `send` twice at the top level, the later declaration replaced
the earlier, and so every upload called the RECORDER -- posting an
empty `audio` part to `/talk`, never once calling `/stage`.  Both
halves read correctly where they were written.  The guard that compared
the page's field name against the route's parameter compared two
STRINGS and never asked whether the function containing one of them
runs.

So this file asserts reachability and nothing else: that a click lands
on the endpoint it is supposed to land on, carrying what it is supposed
to carry.  It serves the REAL page against a stub that records what
arrives -- no daemon, no model, no workspace.

    python tests_browser.py

A BROWSER IS NAMED, NEVER SEARCHED FOR.  Playwright's own chromium is
what it uses; `ESCRIBA_BROWSER=/path/to/chrome` names a different one.
If neither is there the legs are SAID to be unrun rather than passing
quietly -- an upload test that silently did not run is how this defect
reached production in the first place.
"""
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8731

fail = 0


def check(label: str, got, want) -> None:
    global fail
    if got != want:
        print(f"  FAIL {label:<46} got {got!r}, want {want!r}")
        fail += 1


#: Every request the page made, as (method, path, body).
SEEN = []

#: The least a snapshot can hold and still draw the whole page.  Not a
#: fixture of rows: what is under test is where a click GOES, and a row
#: the person did not create cannot be clicked into existence.
STATE = {
    "entries": [], "counts": {}, "status": {"status": "listening", "query": None},
    "session_at": "09:17", "who": "probe", "stage_mb": 25, "days": [],
    "engines": {}, "wiki": {"state": "sin conectar", "rows": [], "detail": "sin conectar"},
    "items": {"memoria": [], "referencias": [], "documentos": []},
    "locale": "es", "locales": ["es", "en"],
}


class Stub(BaseHTTPRequestHandler):
    """The BFF, reduced to what the page needs to come up."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):          # the suite's output is the record
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, (HERE / "web/index.html").read_bytes(), "text/html")
        elif self.path.startswith("/strings/"):
            self._send(200, (HERE / "i18n/es.json").read_bytes(), "application/json")
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            snap = dict(STATE, strings=json.loads((HERE / "i18n/es.json").read_text()))
            self.wfile.write(f"event: state\ndata: {json.dumps(snap)}\n\n".encode())
            self.wfile.flush()
            while True:                 # the page expects it to stay open
                time.sleep(0.5)
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except OSError:
                    return
        else:
            self._send(404, b"", "text/plain")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN.append(("POST", self.path, body))
        if self.path == "/talk":        # answered as a stream, like the real one
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(200, b"{}", "application/json")


def browser(p):
    """The browser to drive, or a reason there is none."""
    named = os.environ.get("ESCRIBA_BROWSER")
    try:
        return p.chromium.launch(
            executable_path=named,
            args=["--use-fake-device-for-media-stream",
                  "--use-fake-ui-for-media-stream"]), None
    except Exception as exc:                                   # noqa: BLE001
        return None, str(exc).strip().splitlines()[0]


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  note  the browser legs were NOT run: playwright is not "
              "installed here (pip install playwright && playwright install "
              "chromium)")
        print("browser OK (0 of 5 legs run)")
        return 0

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with sync_playwright() as p:
        br, why = browser(p)
        if br is None:
            print(f"  note  the browser legs were NOT run: {why}")
            print("  note  name one with ESCRIBA_BROWSER=/usr/bin/google-chrome")
            print("browser OK (0 of 5 legs run)")
            return 0
        pg = br.new_context(permissions=["microphone"]).new_page()
        pg.goto(f"http://127.0.0.1:{PORT}/")
        pg.wait_for_timeout(1200)

        # ----------------------------------------- the upload reaches /stage
        # THE DEFECT THIS FILE EXISTS FOR.  Not "the handler is correct" --
        # that was true throughout -- but "the handler is the one the
        # button runs".
        pg.get_by_role("button", name="referencias").first.click()
        pg.wait_for_timeout(200)
        pg.get_by_role("button", name="aportados").first.click()
        pg.wait_for_timeout(200)
        n = len(SEEN)
        with pg.expect_file_chooser() as chooser:
            pg.locator("#stage-btn").click()
        chooser.value.set_files({"name": "riego.md", "mimeType": "text/markdown",
                                 "buffer": b"# riego por goteo\n"})
        pg.wait_for_timeout(1500)
        posts = SEEN[n:]
        check("a chosen document is posted once", len(posts), 1)
        if posts:
            where, body = posts[0][1], posts[0][2]
            check("a chosen document is posted to /stage", where, "/stage")
            check("it is posted as the field the route declares",
                  b'name="document"' in body, True)
            check("it carries the file, not an empty part",
                  b"# riego por goteo" in body, True)
            check("the page reports no failure",
                  pg.evaluate("(document.querySelector('.upload .bad')||{})"
                              ".textContent || ''"), "")

        # ------------------------------------- the space bar reaches a button
        # A focused control is activated by the space bar; push-to-talk used
        # to call preventDefault() on every Space there was, on the way down
        # AND on the way up, so no button in this page could be reached from
        # a keyboard at all.
        opened = {"picker": False}
        pg.on("filechooser", lambda fc: opened.__setitem__("picker", True))
        pg.locator("#stage-btn").focus()
        pg.keyboard.press(" ")
        pg.wait_for_timeout(800)
        check("space activates the focused button", opened["picker"], True)

        # ------------------------------ a press too short to be speech sends
        # `stopRec` refuses a sub-300ms press and used to perform it anyway:
        # `recorder.stop()` fires `onstop`, and `onstop` IS the sender.
        # OFF THE BUTTON FIRST.  Focus is still on `#stage-btn` from the
        # leg above, where Space means "activate this" and deliberately
        # does not record -- so without this the press never starts a
        # recording and the leg passes while asserting nothing.
        pg.evaluate("document.activeElement && document.activeElement.blur()")
        n = len(SEEN)
        pg.keyboard.down(" ")           # not on a button: push-to-talk
        pg.wait_for_timeout(120)
        pg.keyboard.up(" ")
        pg.wait_for_timeout(1500)
        check("a press too short to be speech posts nothing",
              [r[1] for r in SEEN[n:]], [])

        # ------------------------------------- and a real one still speaks
        # THE OTHER DIRECTION, and it is the one that matters: the leg
        # above passes just as happily if the space bar no longer records
        # at all, which is what exempting a focused control from the
        # binding could easily have done.  Push-to-talk IS the
        # application.
        n = len(SEEN)
        pg.keyboard.down(" ")
        pg.wait_for_timeout(700)
        pg.keyboard.up(" ")
        pg.wait_for_timeout(2000)
        spoke = SEEN[n:]
        check("a real press still posts the recording",
              [r[1] for r in spoke], ["/talk"])
        if spoke:
            check("and it carries audio, not an empty part",
                  len(spoke[0][2]) > 1000, True)

        # ---------------------------------- including ON the talk button
        # The talk button is a BUTTON, so the exemption above would have
        # covered it too and left Space doing nothing there -- the one
        # place in the page a person would most expect it to work.  It is
        # held rather than clicked, so the global binding is its keyboard
        # equivalent and the exemption must not reach it.
        pg.locator("#ptt").focus()
        n = len(SEEN)
        pg.keyboard.down(" ")
        pg.wait_for_timeout(700)
        pg.keyboard.up(" ")
        pg.wait_for_timeout(2000)
        check("space records with the talk button focused too",
              [r[1] for r in SEEN[n:]], ["/talk"])
        br.close()

    srv.shutdown()
    print("browser OK" if not fail else f"{fail} failure(s)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
