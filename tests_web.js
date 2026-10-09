// What the page draws, asserted without a browser.
//
// The front end is the one part of this repo a test could not reach, and
// it shipped two defects in its first hour for exactly that reason: every
// region was drawn only by the hub's first `state`, so a page whose
// backend was down rendered an empty header and an empty knowledge column
// — indistinguishable from a broken build.  Neither raised.
//
// This runs the page's own script, extracted from `web/index.html` so
// there is one copy of it, against a DOM stub holding only what the script
// touches.  It cannot tell you whether the page LOOKS right; that needs
// eyes and `?mock=1`.  It can tell you that the render paths run, that an
// empty state says "unknown" rather than "zero", and that an entry
// arriving late patches the row it belongs to.
//
//     node tests_web.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let fail = 0;
const check = (label, got, want) => {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    console.log(`  FAIL ${label.padEnd(46)} got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
    fail++;
  }
};

// ----------------------------------------------------------- the DOM stub
class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attrs = {};
    this.className = "";
    this._text = "";
    this.hidden = false;
    this.disabled = false;
  }
  get textContent() {
    return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text;
  }
  set textContent(v) { this.children = []; this._text = String(v); }
  append(...nodes) {
    for (const n of nodes) { this.children.push(n); n.parent = this; }
    if (nodes.length) this._text = "";
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener() {}
  focus() {}
  get classList() {
    const self = this;
    const has = (c) => self.className.split(/\s+/).includes(c);
    return {
      contains: has,
      add: (c) => { if (!has(c)) self.className = (self.className + " " + c).trim(); },
      remove: (c) => { self.className = self.className.split(/\s+/).filter((x) => x !== c).join(" "); },
      toggle: (c, on) => { on ? self.classList.add(c) : self.classList.remove(c); },
    };
  }
  set innerHTML(v) { this._text = ""; }
  walk(out = []) { out.push(this); for (const c of this.children) if (c.walk) c.walk(out); return out; }
  querySelector(sel) {
    const m = /^\[data-day="(.*)"\]$/.exec(sel);
    if (!m) return null;
    return this.walk().find((n) => n.dataset && n.dataset.day === m[1]) || null;
  }
  // only ever read, never laid out
  get scrollHeight() { return this.children.length * 50; }
  get clientHeight() { return 400; }
  get offsetTop() { return 0; }
}

// READ OFF THE PAGE, not listed here.  A hand-kept list falls behind the
// moment the markup gains an element, and the failure is a null deref
// inside a renderer rather than anything that names the real cause.
const PAGE_HTML = fs.readFileSync(path.join(__dirname, "web", "index.html"), "utf8");
const IDS = [...new Set([...PAGE_HTML.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]))];
const byId = new Map(IDS.map((id) => [id, new El("div")]));
byId.get("player").paused = true;

// The player records what it was asked to sound, so autoplay can be
// asserted without a speaker.
const played = [];
const player = byId.get("player");
player.play = () => { played.push(player.src); return Promise.resolve(); };
player.pause = () => {};

const store = new Map();
const sandbox = {
  console,
  document: {
    documentElement: new El("html"),
    getElementById: (id) => byId.get(id) || null,
    createElement: (t) => new El(t),
    createElementNS: (_ns, t) => new El(t),
  },
  addEventListener: () => {},
  localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) },
  location: { search: "" },
  navigator: {},
  performance: { now: () => 0 },
  // A SYNCHRONOUS thenable, so the page's boot — which now waits for the
  // string catalogue before drawing anything — completes before the
  // assertions below, which are plain top-level code.  It serves the
  // real i18n/es.json, so the page under test draws the strings the
  // server actually ships.
  fetch: (url) => {
    const body = String(url).startsWith("strings/")
      ? JSON.parse(fs.readFileSync(path.join(__dirname, "i18n", "es.json"), "utf8"))
      : null;
    const done = (v) => ({ then: (f) => done(f ? f(v) : v), catch: () => done(v) });
    return done({ ok: true, json: () => body, text: () => "" });
  },
  setTimeout, clearTimeout, setInterval, clearInterval,
  URLSearchParams,
  TextDecoder,
  Blob: class {},
  FormData: class {},
  EventSource: class { constructor() { this.handlers = {}; } addEventListener(k, f) { this.handlers[k] = f; } },
  ResizeObserver: class { observe() {} disconnect() {} },
  IntersectionObserver: class { observe() {} disconnect() {} },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

// ONE copy of the script: read out of the page rather than duplicated here.
const html = PAGE_HTML;
const blocks = [...html.matchAll(/<script>\n([\s\S]*?)\n<\/script>/g)].map((m) => m[1]);
if (!blocks.length) { console.log("  FAIL could not find the application script in web/index.html"); process.exit(1); }
const ctx = vm.createContext(sandbox);
vm.runInContext(blocks[blocks.length - 1] +
  "\n;globalThis.__test = {S, onEvent, mockState, renderAll, renderPanel, renderEngines};", ctx);
const T = sandbox.__test;

// ------------------------------------------------ the page before the hub
// The defect this file exists for: with no backend, every one of these was
// empty, and nothing raised.
const pills = byId.get("pills");
check("four pills are drawn before any data", pills.children.length, 4);
check("exactly one is active",
      pills.children.filter((p) => p.className.includes(" on-")).length, 1);
// Read through a placeholder rather than off the element: when the render
// has not run there is nothing to read, and a test that dies on the first
// missing node reports one failure instead of the five that are real.
const blank = { textContent: "", className: "" };
const activePill = () => pills.children.find((p) => p.className.includes(" on-")) || blank;
check("the active one is `escuchando`", activePill().textContent.includes("escuchando"), true);
check("its detail reads `tu turno`", (pills.children[0] || blank).textContent.includes("tu turno"), true);
check("the counts grid is drawn", byId.get("counts").children.length, 4);
check("the tabs are drawn", byId.get("tabs").children.length, 3);

// Unknown is not zero.  A page that has spoken to nothing must not claim
// the person has no documents.
const cellText = (i) => (byId.get("counts").children[i] || blank).textContent;
check("an unknown count reads —", cellText(0).includes("—"), true);
check("documents are unknown, not zero", cellText(3).includes("—"), true);
check("no delta is drawn against an unobserved start", cellText(0).includes("±0"), false);
check("no claim about the queue direction",
      cellText(1).includes("la cola"), false);
check("no engine is claimed before the hub", byId.get("engines").children.length, 0);
check("tab counts are unknown too", (byId.get("tabs").children[0] || blank).textContent.includes("—"), true);

// ----------------------------------------- a browser that has no microphone
// The stub has no `isSecureContext`, which is exactly the page served over
// http from another machine.  It must say so AT LOAD and disable the
// button, rather than let somebody hold it down, speak, and be told
// afterwards — and it must not blame a permission the browser never
// offered.
const alertText = () => byId.get("alert-slot").textContent;
check("an insecure origin is reported at load", alertText().includes("Micrófono no disponible"), true);
check("it names the origin, not the permission", alertText().includes("https"), true);
check("it does not send them to the permission", alertText().includes("Revisa el permiso"), false);
check("the button is disabled", byId.get("ptt").disabled, true);
check("and says why", byId.get("ptt-sub").textContent, "requiere https o localhost");

// ------------------------------------------------------- with a snapshot
// The real catalogue, not a copy: the page under test draws the strings
// the server actually ships, so a key renamed in one and not the other
// fails here.
const ES = JSON.parse(fs.readFileSync(path.join(__dirname, "i18n", "es.json"), "utf8"));
T.onEvent("state", Object.assign(T.mockState(), { strings: ES, locale: "es" }));
check("the snapshot is marked loaded", T.S.loaded, true);
check("every entry is held by id", T.S.byId.size, T.S.order.length);
check("the transcript drew its days", byId.get("scroll").children.length, 3);
check("now the counts are real", cellText(0).includes("142"), true);
check("and the delta against the start", cellText(0).includes("+5"), true);
check("a rising uncurated queue says so", cellText(1).includes("la cola crece"), true);
check("tab counts are real", (byId.get("tabs").children[0] || blank).textContent.includes("3"), true);
check("the days column is drawn", byId.get("days").children.length, 5);

// Entries are grouped by LOCAL day.  A UTC key would file a late-evening
// turn under the wrong heading east of Greenwich.
const dayKeys = byId.get("scroll").children.map((c) => c.dataset.day);
check("days are ordered oldest first", [...dayKeys].sort(), dayKeys);
const today = new Date();
const localToday = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, "0"),
                    String(today.getDate()).padStart(2, "0")].join("-");
check("today's entries are under today's local date", dayKeys[dayKeys.length - 1], localToday);

// -------------------------------------------------------------- engines
// Every tier, not just the live one: `voz` is the only tier the person
// hears and `escribano` is where the escriba writes in silence, so one
// name would hide the switch that explains the quiet.
const engines = byId.get("engines");
check("the block is drawn", engines.textContent.includes("Motor"), true);
check("and is now a tab beside Contexto", engines.textContent.includes("Contexto"), true);
check("both agents are named",
      ["escriba", "documentalista"].every((a) => engines.textContent.includes(a)), true);
check("both of the escriba's tiers are shown",
      ["voz", "escribano"].every((t) => engines.textContent.includes(t)), true);
check("the provider is text, not a tooltip",
      engines.textContent.includes("escriba · openrouter"), true);
check("with their models",
      ["openai/gpt-audio", "openai/gpt-4o-mini"].every((m) => engines.textContent.includes(m)), true);

// -------------------------------------------------------------- consumo
// The second tab answers a DIFFERENT question from Motor: not who is
// answering, but how full the window is and what has been spent.  The
// mock payload is deliberately partial — no cache_creation_tokens, no
// cost — because the rules worth guarding are about what is ABSENT.
const showConsumo = () => { T.S.engTab = "contexto"; T.renderEngines();
                            return engines.textContent; };
const con = showConsumo();
check("the window group is drawn", con.includes("ventana"), true);
check("with the percentage, comma-decimal as Spanish writes it",
      con.includes("43,2 %"), true);
check("and the turns", con.includes("turnos"), true);

// jaato#1444: `percent_used` and `tokens_remaining` are already net of
// what each request reserves for its own output, so the reservation is
// NAMED. Without it the reader sees 43% beside a window of 128 000 and
// arithmetic that cannot be made to work.
check("the output reservation is named", con.includes("reservado para la respuesta"), true);
check("and why the figures already exclude it",
      con.includes("ya descuentan esta reserva"), true);

// The DECLARED dimensions decide what is shown, read off `limits` rather
// than a list in the page, so one added to the profile later appears.
check("every declared ceiling is shown",
      ["coste", "turnos", "llamadas", "tiempo"].every((d) => con.includes(d)), true);
check("money keeps its cents", con.includes("10,00 $"), true);
check("spent against it", con.includes("1,43 $"), true);
check("time reads as time, not as a token count", con.includes("1 m 58 s"), true);
check("the next rung is the deadline that matters", con.includes("80 %"), true);

// ABSENT IS NOT ZERO. The mock reports no cache WRITES and no cost: a
// provider with no prompt cache must not read as a cache that never
// hits, and a session with no pricing table must not read as free.
check("a dimension nothing measured is omitted",
      con.includes("escrito en caché"), false);
check("and never drawn as a zero", /escrito en caché[^]*?0/.test(con), false);
check("an unmeasured cost is not rendered as free", con.includes("coste medido"), false);
check("but a measured one is shown", con.includes("leído de caché"), true);
// jaato#1047: reasoning is a SUBSET of output, never added to it.
check("reasoning is labelled as part of the output",
      con.includes("de ella, razonamiento"), true);

// An empty reading must not blank a populated panel: that reads as
// "nothing spent", which is a lie a stale figure does not tell.
T.onEvent("consumo", { window: null, spend: null });
check("an empty reading is ignored, not applied",
      showConsumo().includes("1,43 $"), true);

T.S.engTab = "motor"; T.renderEngines();

// ------------------------------------------------------------ languages
// The selector is drawn from the locales the SERVER lists, so adding a
// catalogue to i18n/ adds a flag with no change to the page.
const EN = JSON.parse(fs.readFileSync(path.join(__dirname, "i18n", "en.json"), "utf8"));
const LOCALES = [{ code: "es", name: "español", flag: "\u{1F1EA}\u{1F1F8}" },
                 { code: "en", name: "English", flag: "\u{1F1EC}\u{1F1E7}" }];
T.onEvent("state", Object.assign(T.mockState(), { strings: ES, locale: "es", locales: LOCALES }));
check("one flag per locale", byId.get("langs").children.length, 2);

// The whole chrome follows the catalogue, not just the panel it was
// last touched in: a half-switched page is the usual end state of this
// work and the one thing worth guarding.
T.onEvent("state", Object.assign(T.mockState(), { strings: EN, locale: "en", locales: LOCALES }));
check("the pills switch", byId.get("pills").textContent.includes("listening"), true);
check("the tabs switch", byId.get("tabs").textContent.includes("memory"), true);
check("the counts switch", byId.get("counts").textContent.includes("curated memory"), true);
// The sandbox is an insecure origin, so `pttFace` overrides the title
// with the mic warning — which is itself a catalogue string, so it is
// still the switch being asserted.
check("the push-to-talk switches", byId.get("ptt-title").textContent,
      EN["alert.insecure.title"]);
check("the day column switches", byId.get("nav-head").textContent, "Days");
check("the theme button switches", byId.get("theme-btn").textContent.startsWith("theme:"), true);
check("and the engine tabs", byId.get("engines").textContent.includes("Engine"), true);
check("no Spanish is left in the header",
      byId.get("hright").textContent.includes("audio en"), false);

// A key with no entry renders as the key: loud, and greppable. A
// fallback to Spanish would make an unfinished English page look done.
T.onEvent("state", Object.assign(T.mockState(), { strings: {}, locale: "en", locales: LOCALES }));
check("a missing catalogue shows keys, not Spanish",
      byId.get("ptt-title").textContent, "alert.insecure.title");

T.onEvent("state", Object.assign(T.mockState(), { strings: ES, locale: "es", locales: LOCALES }));

// ------------------------------------------------------------- autoplay
// A reply sounds by itself, because a voice assistant that waits to be
// asked twice is not one.  But ONLY a new one: everything in the
// snapshot above has already been heard, and a reconnect re-sends the
// world.
check("a snapshot sounds nothing", played.length, 0);

const liveId = 500001;
T.onEvent("entry", { id: liveId, kind: "spoke", at: new Date().toISOString(),
                     text: "Con seis macetas…", audio: null, seconds: 9, repeats: 1 });
check("text with no audio sounds nothing", played.length, 0);
T.onEvent("entry", { id: liveId, kind: "spoke", at: new Date().toISOString(),
                     text: "Con seis macetas…", audio: "att_aaaaaaaaaaaaaaaa", seconds: 9, repeats: 1 });
check("the audio arriving sounds the reply", played, ["/audio/att_aaaaaaaaaaaaaaaa"]);

// The same row republished — a repeat, a reconnect, a late `seconds` —
// must not sound it a second time.
T.onEvent("entry", { id: liveId, kind: "spoke", at: new Date().toISOString(),
                     text: "Con seis macetas…", audio: "att_aaaaaaaaaaaaaaaa", seconds: 11, repeats: 1 });
check("a republished row is not sounded again", played.length, 1);

// The person's own voice is never sounded back at them unasked.
T.onEvent("entry", { id: 500002, kind: "said", at: new Date().toISOString(),
                     text: "", audio: "att_bbbbbbbbbbbbbbbb", seconds: 6, repeats: 1 });
check("their own utterance is not played back", played.length, 1);
const afterAutoplay = played.length;

// -------------------------------------------- two origins, two groups
// A reference found here and one the person is granted in the wiki are
// different kinds of thing, and the difference must not need reading a
// row to notice.  The header is also the only honest place for a wiki
// that did not answer: an empty list and an unreachable server look
// identical and mean opposite things.
T.onEvent("status", { status: "listening", query: null });
const panel = byId.get("panel");
const showRefs = () => { T.S.tab = "referencias"; T.S.sel = null; T.renderPanel(); return panel.textContent; };
const showWiki = () => { T.S.tab = "referencias"; T.S.refTab = "wiki"; T.S.sel = null;
                         T.renderPanel(); return panel.textContent; };
let refs = showRefs();
check("both origins are offered as tabs",
      refs.includes("de esta conversación") && refs.includes("del wiki"), true);
check("the local tab lists what was found here",
      refs.includes("Nombres de Gatos") || refs.includes("Riego por gravedad"), true);
check("and not the wiki's rows beside them",
      refs.includes("Riego por goteo: patrones de montaje"), false);
check("the wiki tab is reachable from here", refs.includes("del wiki"), true);
check("a connected wiki lists its rows under its own tab",
      showWiki().includes("Riego por goteo: patrones de montaje"), true);

// The wiki tab carries its STATE where a count would go, and says it
// again in place of the list: an empty list and a server that did not
// answer look identical and mean opposite things.
T.onEvent("wiki", { state: "error", rows: [], detail: "el wiki no responde" });
refs = showWiki();
check("an unreachable wiki says so", refs.includes("el wiki no responde"), true);
check("and does not pretend to be empty", refs.includes("nada que puedas ver"), false);
check("its state is on the tab too", refs.includes("error"), true);
check("the local tab is still offered", refs.includes("de esta conversación"), true);

T.onEvent("wiki", { state: "sin conectar", rows: [],
                    detail: "no hay ninguna fuente MCP configurada para el wiki" });
check("an unwired wiki says so", showWiki().includes("no hay ninguna fuente MCP"), true);
check("switching back shows what was found here",
      (() => { T.S.refTab = "local"; T.renderPanel();
               return panel.textContent.includes("no hay ninguna fuente MCP"); })(), false);
T.S.tab = "memoria"; T.renderPanel();

// ------------------------------------------------ a turn that ends badly
// The page sets "pensando…" itself when the upload starts, and only a
// status from the server takes it off.  A turn that ended with an alert
// and no status left the pill thinking forever — an escriba that had
// already given up, indistinguishable from one still working.
T.onEvent("status", { status: "thinking", query: null });
check("the pill is thinking", T.S.status.status, "thinking");
T.onEvent("alert", { kind: "waking", detail: "todavía está despertando" });
T.onEvent("status", { status: "listening", query: null });
check("a failed turn still ends the status", T.S.status.status, "listening");
check("and the reason is shown",
      byId.get("alert-slot").textContent.includes("despertando"), true);
check("as waking, not as a fault",
      byId.get("alert-slot").textContent.includes("conexión"), false);

// ------------------------------------------ the person's own words
// The transcript arrives AFTER the row is drawn — the recording is
// transcribed beside the turn — so it takes the same patch-by-id path a
// late audio ref does.
T.onEvent("entry", { id: 500020, kind: "said", at: new Date().toISOString(),
                     text: "", seconds: 6, audio: "att_eeeeeeeeeeeeeeee", repeats: 1,
                     transcribing: true });
check("a said row starts with no words",
      byId.get("scroll").textContent.includes("quiero hablar sobre gatos"), false);
check("but says the words are coming",
      byId.get("scroll").textContent.includes("transcribiendo…"), true);
T.onEvent("entry", { id: 500020, kind: "said", at: new Date().toISOString(),
                     text: "quiero hablar sobre gatos", seconds: 6,
                     audio: "att_eeeeeeeeeeeeeeee", repeats: 1 });
check("the transcript lands on the row",
      byId.get("scroll").textContent.includes("quiero hablar sobre gatos"), true);
// Scoped to ITS row: the snapshot has another utterance still being
// transcribed, and a whole-transcript check would be answered by that
// one and prove nothing about this.
const rowOf = (id) => byId.get("scroll").walk().find((n) => n.dataset && String(n.dataset.id) === String(id));
check("and the placeholder goes",
      rowOf(500020).textContent.includes("transcribiendo…"), false);

// With no transcriber there is nothing to wait for, and a placeholder
// for words that are never coming is the "audio llegando…" defect again.
T.onEvent("entry", { id: 500021, kind: "said", at: new Date().toISOString(),
                     text: "", seconds: 4, audio: "att_ffffffffffffffff", repeats: 1,
                     transcribing: false });
const spinners = rowOf(500021).walk().filter((n) => n.className === "spin").length;
check("no spinner where nothing is transcribing", spinners, 0);
check("and one where something is",
      rowOf(500020) && byId.get("scroll").walk().filter((n) => n.className === "spin").length >= 1, true);

// ------------------------------------------------- an unknown duration
// Entries archived before the record carried durations have none, and
// "0:00" beside a counter already at 0:08 is a length nobody measured.
T.onEvent("entry", { id: 500010, kind: "spoke", at: new Date().toISOString(),
                     text: "sin duración", audio: "att_cccccccccccccccc",
                     seconds: null, repeats: 1, silent: false });
const transcript = () => byId.get("scroll").textContent;
check("an unknown length is not printed as zero", transcript().includes("escuchar 0:00"), false);
check("the control still offers to play", transcript().includes("escuchar"), true);
T.onEvent("entry", { id: 500011, kind: "spoke", at: new Date().toISOString(),
                     text: "con duración", audio: "att_dddddddddddddddd",
                     seconds: 14, repeats: 1, silent: false });
check("a known length is shown", transcript().includes("escuchar 0:14"), true);

// ------------------------------------------------------ a silent reply
// Waiting and never-coming look identical in the data — both are a
// `spoke` row with no audio — and only the driver can tell them apart.
// A row that says "audio llegando…" forever is how it was found.
const soundedBefore = played.length;     // counted, not assumed
const silentId = 500003;
T.onEvent("entry", { id: silentId, kind: "spoke", at: new Date().toISOString(),
                     text: "La sesión se ha cerrado.", audio: null, seconds: null,
                     repeats: 1, silent: false });
const rowText = () => byId.get("scroll").textContent;
const rowOfId = (id) => byId.get("scroll").walk()
  .find((n) => n.dataset && String(n.dataset.id) === String(id)) || blank;
check("while the turn runs it waits", rowText().includes("audio llegando…"), true);
T.onEvent("entry", { id: silentId, kind: "spoke", at: new Date().toISOString(),
                     text: "La sesión se ha cerrado.", audio: null, seconds: null,
                     repeats: 1, silent: true });
check("once it has ended it says so", rowText().includes("sin audio"), true);

// A turn with no words AND no speech.  The driver used to substitute the
// string "(spoke)", which a person read as something the escriba said.
T.onEvent("entry", { id: 500004, kind: "spoke", at: new Date().toISOString(),
                     text: "", audio: null, seconds: null, repeats: 1, silent: true });
check("an empty turn says it said nothing",
      rowOfId(500004).textContent.includes("no dijo nada en este turno"), true);
check("and invents no words", rowOfId(500004).textContent.includes("(spoke)"), false);
check("and stops waiting", rowText().includes("audio llegando…"), false);
check("a silent row is never autoplayed", played.length, soundedBefore);

// --------------------------------------------- what arrives after the row
// The audio ref lands well after the text, and it must patch the row that
// is already on screen rather than append a second one.
const spoke = [...T.S.byId.values()].find((e) => e.kind === "spoke" && e.audio);
const before = T.S.order.length;
T.onEvent("entry", { id: spoke.id, kind: "spoke", at: spoke.at, text: spoke.text,
                     seconds: spoke.seconds, audio: "att_newaudio", repeats: 1 });
check("a late update does not append", T.S.order.length, before);
check("it patches the row it names", T.S.byId.get(spoke.id).audio, "att_newaudio");

const fresh = { id: 999999, kind: "note", at: new Date().toISOString(), text: "nuevo", repeats: 1 };
T.onEvent("entry", fresh);
check("an unknown id appends", T.S.order.length, before + 1);
check("and is held by id", T.S.byId.get(999999).text, "nuevo");

// A status the hub never sent must not be inferred from the entries: the
// page had audio in hand the whole time and is not speaking.
check("status is still the hub's", T.S.status.status, "listening");
check("still exactly one pill active",
      pills.children.filter((p) => p.className.includes(" on-")).length, 1);

console.log(fail ? `${fail} failure(s)` : "web page renders OK");
process.exit(fail ? 1 : 0);
