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

const IDS = ["pills", "session-at", "hright", "playback-where", "audit-btn", "theme-btn", "alert-slot",
             "days", "scroll", "jump", "ptt", "ptt-title", "ptt-sub", "ptt-time", "counts", "tabs",
             "panel", "engines", "reader-slot", "player"];
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
  fetch: () => Promise.resolve({ ok: true, text: () => Promise.resolve("") }),
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
const html = fs.readFileSync(path.join(__dirname, "web", "index.html"), "utf8");
const blocks = [...html.matchAll(/<script>\n([\s\S]*?)\n<\/script>/g)].map((m) => m[1]);
if (!blocks.length) { console.log("  FAIL could not find the application script in web/index.html"); process.exit(1); }
const ctx = vm.createContext(sandbox);
vm.runInContext(blocks[blocks.length - 1] +
  "\n;globalThis.__test = {S, onEvent, mockState, renderAll, renderPanel};", ctx);
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
T.onEvent("state", T.mockState());
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
check("both agents are named",
      ["escriba", "documentalista"].every((a) => engines.textContent.includes(a)), true);
check("both of the escriba's tiers are shown",
      ["voz", "escribano"].every((t) => engines.textContent.includes(t)), true);
check("the provider is text, not a tooltip",
      engines.textContent.includes("escriba · openrouter"), true);
check("with their models",
      ["openai/gpt-audio", "openai/gpt-4o-mini"].every((m) => engines.textContent.includes(m)), true);

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
let refs = showRefs();
check("the local group is labelled", refs.includes("de esta conversación"), true);
check("the wiki group is labelled", refs.includes("del wiki"), true);
check("a connected wiki lists its rows", refs.includes("Riego por goteo: patrones de montaje"), true);

// Unreachable is SAID, not shown as emptiness.
T.onEvent("wiki", { state: "error", rows: [], detail: "el wiki no responde" });
refs = showRefs();
check("an unreachable wiki says so", refs.includes("el wiki no responde"), true);
check("and does not pretend to be empty", refs.includes("nada que puedas ver"), false);
check("the local group is still there", refs.includes("de esta conversación"), true);

// Not wired is a third state, and also said.
T.onEvent("wiki", { state: "sin conectar", rows: [],
                    detail: "no hay ninguna fuente MCP configurada para el wiki" });
refs = showRefs();
check("an unwired wiki says so", refs.includes("no hay ninguna fuente MCP"), true);
T.S.tab = "memoria"; T.renderPanel();

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
check("while the turn runs it waits", rowText().includes("audio llegando…"), true);
T.onEvent("entry", { id: silentId, kind: "spoke", at: new Date().toISOString(),
                     text: "La sesión se ha cerrado.", audio: null, seconds: null,
                     repeats: 1, silent: true });
check("once it has ended it says so", rowText().includes("sin audio"), true);
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
