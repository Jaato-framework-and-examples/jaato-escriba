"""escriba on the web: the backend for `web/index.html`.

Three modules, split where the failures are different kinds:

    hub.py      the fan-out, one per person. The failure here is sending
                a transcript to the wrong browser, so it is the piece
                with its own test.
    driver.py   one person's session, workspace and archive. The turn
                itself is `run_escriba`'s, imported.
    app.py      routing, and nothing else.
"""
