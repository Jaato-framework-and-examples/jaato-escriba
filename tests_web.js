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
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
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
//: The context the page created, captured so the legs below can see what
//: was scheduled on it.
let audio = null;

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
  // The voice arrives base64'd in an SSE frame and is decoded to samples
  // in the page; Node's vm context has the typed arrays but not this.
  atob: (b64) => Buffer.from(b64, "base64").toString("binary"),
  AudioContext: class {
    constructor() {
      this.state = "running";
      this.currentTime = 0;
      this.destination = {};
      this.started = [];
      audio = this;
    }
    resume() { this.state = "running"; }
    createBuffer(channels, length, rate) {
      const data = Array.from({ length: channels }, () => new Float32Array(length));
      return { numberOfChannels: channels, length, sampleRate: rate,
               duration: length / rate, getChannelData: (c) => data[c] };
    }
    createBufferSource() {
      const ctx = this;
      // `stopped` is recorded, not swallowed: whether a scheduled piece
      // was cut before it sounded is the difference between hearing a
      // reply and hearing its first half.
      const row = { at: null, buffer: null, stopped: false };
      return { buffer: null, connect() {},
               start(at) { row.at = at; row.buffer = this.buffer;
                           ctx.started.push(row); },
               stop() { row.stopped = true; } };
    }
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
// NO TWO TOP-LEVEL FUNCTIONS MAY SHARE A NAME, and this is here because
// two did.  `send(file)` uploaded a document and `send()` posted a
// recording; both were declared at the top level of this one script, so
// the later declaration replaced the earlier and EVERY upload called the
// recorder instead — posting an empty audio part to `/talk` and never
// once calling `/stage`.  JavaScript does this silently: the page parses,
// both functions read correctly where they are written, and nothing in
// this suite noticed, because a test that renders the panel never calls
// the handler the button is wired to.
//
// A rename is the fix for that instance.  This is the fix for the class.
{
  const decl = /^(?:async )?function ([A-Za-z_$][\w$]*)\s*\(/gm;
  const seen = new Map();
  const twice = [];
  for (const m of blocks[blocks.length - 1].matchAll(decl)) {
    if (seen.has(m[1])) twice.push(m[1]);
    seen.set(m[1], true);
  }
  check("no top-level function name is declared twice", twice, []);
}

const ctx = vm.createContext(sandbox);
vm.runInContext(blocks[blocks.length - 1] +
  "\n;globalThis.__test = {S, onEvent, mockState, renderAll, renderPanel, " +
  "renderEngines, keyOf, onSpeech, maybeAutoplay, wake, VOICE, rows, api, " +
  "renderGuest, renderScratch, renderTranscript, pttFace, paintSequence, " +
  "seqGrip, sayBack, BACK, demoFace, demoUnavailable, renderDays, " +
  "dayIsHere, openReader, closeReader, READ, ZOOMS, renderDisclosure};",
  ctx);
const T = sandbox.__test;

// --------------------------------------- what they are talking to (50(1))
// The sentence is the framework's, read off the audit record, so the page
// renders it VERBATIM or renders nothing: a reworded, truncated or
// translated copy would make the ledger's `text` a statement about
// something nobody was shown.
const aviso = byId.get("disclosure");
check("nothing is claimed before a snapshot says so", aviso.hidden, true);
T.S.disclosure = "Estás interactuando con un sistema de IA operado por X, "
  + "no con un ser humano.";
T.renderDisclosure();
check("the announcement is shown", aviso.hidden, false);
check("and shown word for word", aviso.textContent, T.S.disclosure);
// NOT MARKUP.  The provider's name reaches this string from a profile,
// and `textContent` is what keeps a name from becoming an element.
T.S.disclosure = "<b>IA</b>";
T.renderDisclosure();
check("a provider name cannot carry markup in", aviso.textContent, "<b>IA</b>");
T.S.disclosure = "";
T.renderDisclosure();
check("and an undeclared session claims nothing", aviso.hidden, true);

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

// The header names the person the backend says is connected. It is the
// one thing on the page that answers "whose second brain am I looking
// at?", which on a multi-tenant deployment is not a cosmetic question.
check("the header names the connected person",
      byId.get("who").textContent, "ana.ruiz@ejemplo.es");
check("and says what that name is on hover",
      byId.get("who").title.includes(ES["header.who.title"].split(":")[0]), true);
check("the full name survives the truncation",
      byId.get("who").title.includes("ana.ruiz@ejemplo.es"), true);

// Entries are grouped by LOCAL day.  A UTC key would file a late-evening
// turn under the wrong heading east of Greenwich.
const dayKeys = byId.get("scroll").children.map((c) => c.dataset.day);
check("days are ordered oldest first", [...dayKeys].sort(), dayKeys);
const today = new Date();
const localToday = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, "0"),
                    String(today.getDate()).padStart(2, "0")].join("-");
check("today's entries are under today's local date", dayKeys[dayKeys.length - 1], localToday);

// A DAY ALREADY ON SCREEN IS NOT FETCHED AGAIN.
//
// `d.in_view` comes from the `state` snapshot and is a photograph: it is
// published on session open, reopen and a locale change, and an `entry`
// landing never updates it.  MEASURED 2026-10-19 15:42 — the page loaded
// before the session opened, so today was marked `archivo`; the greeting
// arrived as an entry; clicking "hoy" took the ARCHIVE route for a day
// that was live and merged the same greeting a second time.  Archived
// ids are negative by construction so they cannot collide with live
// ones, which is exactly why `byId` could not recognise it.
//
// So the question is asked of the rows the page actually holds.  The
// stale flag is simulated here by saying today is NOT in view while
// today's rows are on screen — which is the state the live page was in.
{
  T.S.days = [{ date: localToday, count: 0, in_view: false }];
  T.S.loadedDays = new Set();
  T.renderDays();
  const btn = byId.get("days").children[0];
  check("a day whose rows are on screen is not drawn as archive",
        btn.className.includes("archived"), false);
  // The press itself, not only the label: a row can land between the
  // render and the click, and the fetch is what duplicates.
  let fetched = false;
  const realFetch = sandbox.fetch;
  sandbox.fetch = () => { fetched = true; return Promise.reject(new Error("no")); };
  btn.onclick();
  sandbox.fetch = realFetch;
  check("and clicking it fetches nothing", fetched, false);
}

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
      refs.includes("riego-goteo-patrones"), false);
check("the wiki tab is reachable from here", refs.includes("del wiki"), true);
// A WIKI ROW IS A PAGE THE ESCRIBA OPENED, and the tool-call events it
// is read from carry the node's id and nothing else -- no url, no title,
// no body.  `host(undefined)` returns its argument, so every one of
// these rows used to print "undefined" where a host would go.
const fromWiki = showWiki();
check("a connected wiki lists its rows under its own tab",
      fromWiki.includes("riego-goteo-patrones"), true);
check("and says when each page was opened", fromWiki.includes("09:18"), true);
check("and never the word undefined", fromWiki.includes("undefined"), false);

// OPENING ONE. A wiki page has no address of its own, so the link exists
// only where the deployment told us how to build one, and the page knows
// no kbwiki route -- it substitutes into the pattern the server sent.
const openWiki = (id) => {
  T.S.tab = "referencias"; T.S.refTab = "wiki";
  T.S.sel = T.keyOf("referencias", { id, text: id, origin: "wiki" });
  T.renderPanel();
  const found = [];
  (function walk(n) {
    for (const c of n.children || []) { if (c.tagName === "A") found.push(c); walk(c); }
  })(panel);
  return found;
};
T.onEvent("wiki", { state: "ok", rows: [
  { id: "riego-goteo-patrones", text: "riego-goteo-patrones", origin: "wiki",
    at: "09:18", tags: [] }] });
T.S.wikiPage = null;
check("with no pattern, a page is not a link", openWiki("riego-goteo-patrones").length, 0);
T.S.wikiPage = "https://kb.example/#/knowledge/{id}";
const anchors = openWiki("riego-goteo-patrones");
check("with one, it is", anchors.length, 1);
check("built by substitution, not by knowing the route",
      anchors[0].attrs.href || anchors[0].href,
      "https://kb.example/#/knowledge/riego-goteo-patrones");
T.S.wikiPage = null;
T.S.sel = null;

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

// ------------------------------------------- and a third: what the
// person staged themselves.  The three tabs are a FILTER over one list
// (each row carries its `origin`), so what must hold is that a tab
// shows only its own rows — and that a staged row can be told apart at
// all: keyed on `url`, as references were, every staged row is the same
// row, because a staged document has no URL.
const showStaged = () => { T.S.tab = "referencias"; T.S.refTab = "staged"; T.S.sel = null;
                           T.renderPanel(); return panel.textContent; };
T.S.refTab = "local";
refs = showRefs();
check("the staged origin is offered too", refs.includes(ES["refs.staged"]), true);
check("and its rows are not among the judge's finds",
      refs.includes("presupuesto-riego.md"), false);
let mine = showStaged();
check("the staged tab lists the staged document",
      mine.includes("presupuesto-riego.md"), true);
check("and not the judge's finds", mine.includes("Riego por gravedad"), false);
check("a staged document shows its size", mine.includes("18,0 KB"), true);
// Its file is gone: the catalogue still offers the document, so the row
// says so rather than drawing a zero that reads as an empty document.
check("a missing file is said", mine.includes(ES["stage.missing"]), true);
check("and never drawn as zero bytes", mine.includes("0 B"), false);
check("the panel offers a way to add one", mine.includes(ES["stage.add"]), true);
check("and no other tab does",
      (() => { T.S.refTab = "local"; return showRefs(); })().includes(ES["stage.add"]), false);
check("the ceiling is the server's, not the page's", T.S.stageMb, 25);
check("a staged row is identified by its id",
      T.keyOf("referencias", { id: "staged-3798125eb6", url: "" }), "staged-3798125eb6");
T.S.refTab = "staged"; T.S.sel = "staged-3798125eb6"; T.renderPanel();
check("its detail names the file in the workspace",
      panel.textContent.includes("staged/presupuesto-riego.md"), true);
T.S.sel = null;

T.S.tab = "memoria"; T.S.refTab = "local"; T.renderPanel();

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

// ---------------------------------------- the voice, while it is arriving
// Half a second of silence, as the server sends it: 12000 stereo-free
// samples at 24 kHz, two bytes each.
const piece = (n) => Buffer.alloc(n * 2).toString("base64");

// BEFORE ANY INTERACTION there is no context, because a browser would not
// have allowed one. Nothing plays, and — the part that matters — nothing
// is recorded as heard, so the row's own autoplay is still the remedy.
T.onSpeech({ stream: "s1", rate: 24000, channels: 1, pcm: piece(12000) });
check("no voice sounds before the person has touched the page", audio, null);
T.onSpeech({ stream: "s1", final: true, audio: "sha-early" });
check("and it is not counted as heard", T.S.sounded.has("sha-early"), false);

T.wake();
check("a press creates the context", audio !== null, true);
T.onSpeech({ stream: "s2", rate: 24000, channels: 1, pcm: piece(12000) });
T.onSpeech({ stream: "s2", rate: 24000, channels: 1, pcm: piece(12000) });
check("both pieces are scheduled", audio.started.length, 2);
check("the first at once", audio.started[0].at, 0);
check("the second where the first ends", audio.started[1].at, 0.5);
check("at the stream's own rate", audio.started[0].buffer.sampleRate, 24000);
check("whole samples, one channel", audio.started[0].buffer.length, 12000);

T.onSpeech({ stream: "s2", final: true, audio: "sha-heard" });
check("what sounded is recorded as heard", T.S.sounded.has("sha-heard"), true);

// A SECOND UTTERANCE IN THE SAME TURN QUEUES; IT DOES NOT CUT.
//
// One turn can speak several times — the model answers, calls a tool,
// answers again — and each utterance is its own stream.  MEASURED on a
// live page 2026-10-19 16:41: three replies in one turn, each cutting
// the one before off mid-sentence, because a new stream id hushed
// whatever was scheduled.  Frames arrive far faster than real time, so
// the previous utterance was still queued in the FUTURE when the next
// one stopped it.
{
  const before = audio.started.length;
  T.onSpeech({ stream: "s3", rate: 24000, channels: 1, pcm: piece(12000) });
  check("the next utterance is scheduled, not dropped",
        audio.started.length, before + 1);
  check("and starts where the last one ended, rather than at zero",
        audio.started[before].at, 1.0);
  check("nothing already queued was stopped",
        audio.started.slice(0, before).some((s) => s.stopped), false);
}

// CUTTING IS STILL RIGHT when the previous stream never ENDED: a turn
// that timed out or a session that went away leaves audio answering a
// question nobody is waiting on.
{
  T.onSpeech({ stream: "s4", rate: 24000, channels: 1, pcm: piece(12000) });
  const live = audio.started.length;
  T.onSpeech({ stream: "s5", rate: 24000, channels: 1, pcm: piece(12000) });
  check("an unfinished stream is cut off by the next",
        audio.started[live - 1].stopped, true);
  check("and the new one starts from now",
        audio.started[audio.started.length - 1].at, 0);
}
T.onSpeech({ stream: "s5", final: true, audio: "sha-last" });

// AND THEREFORE NOT PLAYED AGAIN. The file arrives named a moment later;
// playing it then would repeat the reply the person just heard.
T.S.playing = null;
T.S.live = true;
T.maybeAutoplay({ id: "e-heard", kind: "spoke", audio: "sha-heard" });
check("the file is not autoplayed after the live voice", T.S.playing, null);
T.maybeAutoplay({ id: "e-unheard", kind: "spoke", audio: "sha-other" });
check("one that was not heard live still is", T.S.playing, "e-unheard");

// NEVER OVER THE PERSON: the escriba talking while the microphone is open
// would be recording itself.
T.S.recording = true;
const scheduled = audio.started.length;
T.onSpeech({ stream: "s3", rate: 24000, channels: 1, pcm: piece(12000) });
check("nothing sounds while the microphone is open",
      audio.started.length, scheduled);
T.S.recording = false;

// --------------------------------------------------- somebody else's room
//
// A host invites a colleague in for a few minutes to supply a detail.
// What is asserted here is the half of that which lives in the page: that
// every call it makes goes to the right session, that neither party hears
// their own voice back, and that the transcript says who spoke.

// ONE PLACE DECIDES WHOSE SESSION A CALL IS ABOUT. Spread over four call
// sites, this is how a guest ends up posting an utterance into their own
// escriba while watching somebody else's transcript — both halves looking
// correct, and the turn landing in the wrong life.
T.S.room = null;
check("with no room, a call is about its owner", T.api("/talk"), "/talk");
check("and so is a recording", T.api("/audio/att_1"), "/audio/att_1");
T.S.room = "r00m1d";
check("in a room, the call goes to the guest twin",
      T.api("/talk"), "/invitado/talk?room=r00m1d");
check("a path that already has a query keeps it",
      T.api("/voz?stream=abcd"), "/invitado/voz?stream=abcd&room=r00m1d");
check("and a recording is read from the host's archive",
      T.api("/audio/att_1"), "/invitado/audio/att_1?room=r00m1d");
T.S.room = null;

// NEVER YOUR OWN VOICE BACK AT YOU. While the key is down nothing plays
// at all, but the last pieces of an utterance arrive after it comes up.
T.S.selfId = "mine0000";
let heardSoFar = audio.started.length;
T.onSpeech({ stream: "m1", rate: 48000, channels: 1, pcm: piece(1000),
             by: "yo", by_id: "mine0000" });
check("a browser does not play its owner back at them",
      audio.started.length, heardSoFar);
T.onSpeech({ stream: "m2", rate: 48000, channels: 1, pcm: piece(1000),
             by: "marta.ruiz", by_id: "theirs00" });
check("but it does play the other person",
      audio.started.length, heardSoFar + 1);

// AND THE ESCRIBA IS HEARD BY EVERYBODY. Its frames carry no speaker at
// all, which is what tells them apart from a person's: the suppression
// above keys on `by_id`, so the one voice nobody in the room can get any
// other way is the one voice that cannot be suppressed.
heardSoFar = audio.started.length;
T.onSpeech({ stream: "m4", rate: 24000, channels: 1, pcm: piece(1000) });
check("the escriba is heard", audio.started.length, heardSoFar + 1);

// WHOSE VOICE THIS WAS. A `said` row starts with no text — the words
// arrive later, from the transcriber — so without a name the transcript
// is a flat run of identical rows in which neither window can say who
// spoke. Each side sees the other named and itself bare.
T.onEvent("entry", { id: 9101, kind: "said", at: new Date().toISOString(),
                     text: "", seconds: 2, audio: "att_aaaaaaaaaaaaaaaa",
                     repeats: 1, by: "" });
T.onEvent("entry", { id: 9102, kind: "said", at: new Date().toISOString(),
                     text: "", seconds: 3, audio: "att_bbbbbbbbbbbbbbbb",
                     repeats: 1, by: "marta.ruiz" });
const saidRows = byId.get("scroll").walk()
  .filter((n) => n.className === "said");
const labels = saidRows.map((w) => w.children[0].textContent);
check("your own utterance is still just yours", labels.includes("tú"), true);
check("and the guest's carries their name",
      labels.includes("marta.ruiz"), true);

// WHAT THE HOST CANNOT MISS. A second brain somebody can join silently is
// a trust problem, so the band is not dismissible: it is there for as
// long as somebody else can hear the conversation.
T.S.guest = false; T.S.guests = []; T.S.inviting = false;
T.renderGuest();
const band = () => byId.get("guest-band");
const offer = () => byId.get("guest-offer");
check("nothing is said when nobody else is here", band().hidden, true);
T.S.guests = [{ email: "marta@ejemplo.es", here: false, name: null,
                open: true, motivo: "conoce el cierre", tema: "cierre" }];
T.renderGuest();
check("an invitation standing open is shown", band().hidden, false);
check("with the address as the host typed it",
      band().textContent.includes("marta@ejemplo.es"), true);

// THE THREE STATES OF AN INVITATION, because an address the host has
// typed is not yet an invitation anybody has been sent. The escriba has
// to say why that person is needed, and the host has to read it, before
// a word of it leaves this session.
T.S.guests = [{ email: "marta@ejemplo.es", here: false, name: null,
                open: false, motivo: null, tema: null }];
T.renderGuest();
check("while the escriba is still writing, the host is told so",
      band().textContent.includes("está escribiendo"), true);
check("and there is nothing to send yet",
      band().walk().some((n) => n._text === "enviar la invitación"), false);

T.S.guests = [{ email: "marta@ejemplo.es", here: false, name: null,
                open: false, tema: "el cierre contable",
                motivo: "llevó el cierre del año pasado" }];
T.renderGuest();
check("the justification is shown to the host first",
      band().textContent.includes("llevó el cierre del año pasado") &&
      band().textContent.includes("el cierre contable"), true);
check("said to be exactly what the guest will read",
      band().textContent.includes("antes de decidir"), true);
check("and that nobody outside has seen it",
      band().textContent.includes("sin enviar"), true);
check("now it can be sent",
      band().walk().some((n) => n._text === "enviar la invitación"), true);
// ONCE SENT, there is nothing left to read or to send: what the guest is
// deciding on is fixed.
T.S.guests = [{ email: "marta@ejemplo.es", here: false, name: null,
                open: true, tema: "el cierre contable",
                motivo: "llevó el cierre del año pasado" }];
T.renderGuest();
check("a sent invitation offers no second send",
      band().walk().some((n) => n._text === "enviar la invitación"), false);
check("and stops calling itself unsent",
      band().textContent.includes("sin enviar"), false);

// TWO SIDES, BECAUSE AN INTERVIEW HAS TWO. A single line saying
// "somebody is here" cannot be acted on when there are two of them, and
// the one who has said their piece leaves while the other is still
// answering -- so each gets its own way out.
T.S.guests = [{ email: "marta@ejemplo.es", here: true, name: "marta.ruiz",
                open: true, motivo: "m", tema: "t" },
              { email: "carlos@ejemplo.es", here: true, name: "carlos.vega",
                open: true, motivo: "m", tema: "t" }];
T.renderGuest();
check("both are named", band().textContent.includes("marta.ruiz") &&
      band().textContent.includes("carlos.vega"), true);
check("and each has its own way out",
      band().walk().filter((n) => n.className === "invite").length, 2);
// THE CONSEQUENCES THE HOST AGREED TO, said while they are true rather
// than once at the moment of inviting.
check("that they can hear the escriba",
      band().textContent.includes("oye al escriba"), true);
check("and that the wiki is being read with the host's permissions",
      band().textContent.includes("con tus permisos"), true);
// AND THE BILL, because an interview is exactly the long many-turn
// session the ceiling was sized against, and the ladder finalises the
// HOST's session at 80 %.
// THE DAEMON'S OWN SHAPE, which `renderEngines` already reads: a first
// attempt at this invented `spend.usd` and would have rendered nothing
// at all, forever, with both halves looking correct on their own.
T.S.consumo = { window: null,
                spend: { budget: { used: { usd: 4.5 }, limits: { usd: 10 } } } };
T.renderGuest();
check("the host sees what the room is spending",
      band().textContent.includes("4.50") &&
      band().textContent.includes("10.00"), true);
T.S.consumo = { window: null, spend: null };
T.renderGuest();
check("and a reading we do not have is not shown as zero",
      band().textContent.includes("0.00"), false);

// A REFUSAL THE HOST CAN READ. `invite()` used to `return` on a refused
// press, so the address that was turned down sat in the field looking
// exactly like one that had been accepted. The server now refuses an
// address no account has -- and a refusal nobody can see would be no
// refusal at all, which is the silence this whole change is about.
T.S.guests = []; T.S.inviting = true; T.S.inviteError = null;
T.renderGuest();
check("the field alone says nothing about a refusal",
      band().textContent.includes(ES["guest.unknown"]), false);
// `ES[...]` and not a literal: the catalogue is what the page draws, so
// a leg written against a copy of it would pass while the key was gone.
T.S.inviteError = ES["guest.unknown"];
T.renderGuest();
check("an address nobody has is said so, beside the field",
      band().textContent.includes(ES["guest.unknown"]), true);
// TWO REFUSALS, NEVER ONE: "nobody has that address" sends the host to
// fix a typo, "I could not ask" sends them to press again in a moment.
T.S.inviteError = ES["guest.uncheckable"];
T.renderGuest();
check("and a realm that could not be asked says that instead",
      band().textContent.includes(ES["guest.uncheckable"]) &&
      !band().textContent.includes(ES["guest.unknown"]), true);
T.S.inviting = false; T.S.inviteError = null;

// WHO HOLDS THE TURN. The lock has always existed server-side; what is
// new is that three people can race for it, and a press that silently
// does nothing is the worst thing this page could do with one.
T.S.guest = false; T.S.who = "dani"; T.S.floor = null;
// The sandbox is an insecure origin, so `micReason` is set and the button
// is disabled for that reason throughout. Lifted for these four legs,
// which are about the floor and nothing else.
const noMic = T.S.micReason;
T.S.micReason = null;
T.pttFace();
const pttEl = () => byId.get("ptt");
check("with the floor free, the button works", pttEl().disabled, false);
T.S.floor = "marta.ruiz";
T.pttFace();
check("while somebody else talks it does not", pttEl().disabled, true);
check("and it says who", byId.get("ptt-title").textContent.includes("marta.ruiz"),
      true);
T.S.floor = "";           // the host has it
T.pttFace();
check("the host holding it does not lock the host out", pttEl().disabled, false);
T.S.floor = null;
T.S.micReason = noMic;
T.renderAll();

// THE GUEST'S OWN REMINDER of where they are, that they are seeing this
// conversation from the moment they walked in, and who else is in it.
T.S.guests = []; T.S.guest = true; T.S.host = "dani"; T.S.who = "marta.ruiz";
T.S.present = ["marta.ruiz", "carlos.vega"];
T.renderGuest();
check("the guest is told whose session this is",
      band().textContent.includes("dani"), true);
check("and that there is a before they cannot see",
      band().textContent.includes("antes de entrar"), true);
check("the invite button is not theirs to press", byId.get("guest-btn").hidden, true);
check("and they are told who else is listening",
      band().textContent.includes("carlos.vega"), true);
check("without being told about themselves",
      band().textContent.split("marta.ruiz").length - 1, 0);

// An offer is shown INSTEAD of opening their own session: `/invitacion`
// resolved no person, so nothing has been started for them yet.
T.S.guest = false; T.S.host = ""; T.S.present = [];
T.S.invited = [{ room: "r1", host: "dani" }];
T.renderGuest();
check("somebody invited somewhere is told so", offer().hidden, false);
check("and by whom", offer().textContent.includes("dani"), true);
T.S.invited = [];
T.renderGuest();
check("and nothing is offered to somebody invited nowhere",
      offer().hidden, true);
T.S.guest = false;

// AN INVITATION THAT ARRIVES WHILE THE PAGE IS ALREADY OPEN. `/invitacion`
// is asked once, at boot, so a page that booted at 18:06 was never told
// about an invitation sent at 18:14 and the invited person sat in their
// own escriba while the host waited. The stream they already have is
// what carries it now.
T.onEvent("invitacion", { rooms: [{ room: "r9", host: "dani",
                                    tema: "el expediente",
                                    motivo: "conoce el expediente" }] });
check("an invitation sent while the page was open is shown at once",
      offer().hidden, false);
check("with what they are wanted for, before they decide",
      offer().textContent.includes("el expediente"), true);
// AND IT LEAVES BY THE PATH IT ARRIVED: the frame carries the whole list,
// so a withdrawal is the same frame with nothing in it. An offer left on
// screen after the host changed their mind is a button that 403s.
T.onEvent("invitacion", { rooms: [] });
check("and a withdrawn one goes off the screen the same way",
      offer().hidden, true);

// THE WAY BACK FROM A CEILING. A budget ending is the one the server
// deliberately does not repair, so the alert is the only place that
// offers another session -- and without the offer the limit would be
// enforced by making the escriba unusable rather than by asking.
const slot = () => byId.get("alert-slot");
const again = () => slot().walk().some((n) => n._text === ES["alert.budget.again"]);
T.S.guest = false;
T.onEvent("alert", { kind: "budget", at: "18:14" });
check("a ceiling offers another session", again(), true);
check("and says when it was reached",
      slot().textContent.includes("18:14"), true);
T.onEvent("alert", { kind: "session", detail: "stopped" });
check("an ordinary ending offers nothing to press", again(), false);
// NOT TO A GUEST: they are subscribed to the HOST's hub, so the host's
// ceiling reaches their page too, and a button there would press against
// their own session about somebody else's limit.
T.S.guest = true;
T.onEvent("alert", { kind: "budget", at: "18:14" });
check("and a guest is not offered somebody else's ceiling", again(), false);
T.S.guest = false;
T.S.alert = null;

// STARTING FROM SCRATCH, AND WHO MAY. The counts come from the server
// before the confirmation is shown, and the recordings are named apart
// from the rest: a memory can be told again, a conversation cannot.
const scratch = () => byId.get("scratch");
T.S.guest = false; T.S.scratch = null;
T.renderScratch();
check("the way to start over is offered",
      scratch().walk().some((n) => n._text === ES["scratch.button"]), true);
// NOT A GUEST'S WORKSPACE TO RESET. They are in somebody else's session;
// the button would reset their own, which is not what the press means.
T.S.guest = true;
T.renderScratch();
check("but never to a guest", scratch().textContent, "");
T.S.guest = false;

// THE COUNTS FIRST: "start from scratch" and "move aside the nineteen
// things you told it over three weeks" are the same press.
T.S.scratch = { curated: 19, raw: 2, references: 66, recordings: 140, days: 3 };
T.renderScratch();
check("the confirmation says how many memories go", 
      scratch().textContent.includes("19"), true);
check("and how many recordings", scratch().textContent.includes("140"), true);
// THE ONE THING NOTHING CAN RECONSTRUCT, said apart from the rest.
check("and that the recordings are the part that cannot be re-had",
      scratch().textContent.includes(ES["scratch.irreplaceable"]), true);
check("and that nothing is actually deleted",
      scratch().textContent.includes(ES["scratch.kept"]), true);
// A TYPED WORD, not a second click: two clicks in the same place is
// something a thumb does.
// The stub element keeps whatever the render assigns, so the field is
// found by the property the page actually sets on it.
check("and asks for a word rather than another click",
      scratch().walk().some((n) => n.placeholder === ES["scratch.type"]), true);
T.S.scratch = null;
T.renderScratch();

// ------------------------------------------- where the answer came from
// The escriba may not read a URL aloud — a dictated address helps nobody
// who is listening — so this row is the ONLY way the person gets one.
// Asked for the exact URL of a page it had just opened, it answered that
// there was not one; it had six, in that page's own Sources section.
const GITLAB = "https://gitlab.averroes.lda/lda/x/-/tree/2f78f59af93b/y";
const anotado = (id, links) => {
  T.onEvent("entry", { id, kind: "anotado", at: new Date().toISOString(),
                       text: "anotado algo", repeats: 1, links });
  const el = T.rows.get(id);
  const found = { anchors: [], nodes: [] };
  (function walk(n) {
    for (const c of n.children || []) {
      if (c.tagName === "A") found.anchors.push(c);
      if ((c.className || "").includes("nodo")) found.nodes.push(c);
      walk(c);
    }
  })(el.body);
  return found;
};

const link = anotado(910001, [{ nombre: "el repo", url: GITLAB }]);
check("a source with a URL is a link", link.anchors.length, 1);
check("pointing where it said", link.anchors[0].attrs.href, GITLAB);
check("named as it would be said aloud", link.anchors[0].textContent, "el repo");
// A new tab, and no window.opener for a page off the corporate network.
check("opening elsewhere", link.anchors[0].attrs.target, "_blank");
check("without handing it this page", link.anchors[0].attrs.rel, "noopener noreferrer");
// The full address is not shown inline — it is a commit URL and would
// take four lines — but it must be recoverable without clicking.
check("the address is on the title", link.anchors[0].attrs.title, GITLAB);

// A WIKI PAGE HAS NO ADDRESS. Its identity is the node id, so it is
// shown to be read and copied; anything that looked like a link and went
// nowhere would be worse than plain text.
const node = anotado(910002, [{ nombre: "la página", nodo: "mainframe-connection-pool" }]);
check("a wiki page is not rendered as a link", node.anchors.length, 0);
check("its id is shown instead", node.nodes.length, 1);
check("verbatim", node.nodes[0].textContent, "mainframe-connection-pool");

// The same page, as a source of an answer: a link where the deployment
// says one can be built, the bare id where it cannot.
T.S.wikiPage = "https://kb.example/#/knowledge/{id}";
const linked = anotado(910004, [{ nombre: "la página", nodo: "mainframe-connection-pool" }]);
check("a wiki source becomes a link when there is one to make",
      linked.anchors.length, 1);
check("to the page the pattern names", linked.anchors[0].attrs.href,
      "https://kb.example/#/knowledge/mainframe-connection-pool");
T.S.wikiPage = null;

const none = anotado(910003, []);
check("a turn that named no source adds nothing", none.anchors.length, 0);
check("and no empty box", none.nodes.length, 0);

// ------------------------------------------------------- the sequence
//
// NOTHING ELSE EXECUTES THIS CODE.  `tests_sequence.py` guards the span
// builder and that the page calls the right route and reads the right
// fields; the DRAWING -- a row per span, a bar placed in percents, the
// indent that carries the nesting -- had no leg at all, which is how a
// view that throws on its first real answer ships looking finished.
//
// FED A FIXTURE AND NOT THE PAGE'S OWN MOCK: a drawing asserted against
// the constant it draws from proves only that the constant exists.
const seqBody = new El("div");
T.paintSequence(seqBody, {
  wall: "2026-10-18T22:31:04.100", total: 10000, spans: [
    { label: "turno 1", depth: 0, t0: 0, t1: 10000,
      detail: "22 413 tok · 0,19 $", sub: false },
    { label: "decide", depth: 1, t0: 2500, t1: 7500, detail: "images 32",
      sub: false },
    { label: "documentalista", depth: 1, t0: 8000, t1: 10000, detail: "",
      sub: true },
  ],
});

const seqRows = seqBody.children.filter((c) => c.className === "line");
check("one row per span", seqRows.length, 3);
const when = seqBody.children[0];
check("the header carries the wall clock",
      when.children[0].textContent, "2026-10-18T22:31:04.100");
check("and the total, in the catalogue's own sentence",
      when.children[1].textContent, "10,0 s en total");
// THE INDENT IS THE NESTING, and it is the server's `depth` rather than
// anything this page worked out for itself.
check("depth becomes the indent",
      seqRows.map((r) => r.children[0].style.paddingLeft),
      ["0px", "14px", "14px"]);
// THE BAR IS PLACED IN PERCENTS of the whole span, so the drawing
// cannot disagree with the duration printed beside it.
const seqBar = (r) => r.children[1].children[0];
check("a bar starts where its span does and is as long as it lasted",
      [seqBar(seqRows[1]).style.left, seqBar(seqRows[1]).style.width],
      ["25%", "50%"]);
check("and a subagent is drawn as one",
      [seqBar(seqRows[0]).className, seqBar(seqRows[2]).className],
      ["fill", "fill sub"]);
check("each row prints what it lasted",
      seqRows.map((r) => r.children[2].textContent),
      ["10,0 s", "5,0 s", "2,0 s"]);
// AND THE PAGE AGREES WITH THE TERMINAL on the unit, which is the whole
// reason both carry the same three-line rule: a fast call is fast, not
// zero.
const fastBody = new El("div");
T.paintSequence(fastBody, { wall: "x", total: 1000, spans: [
  { label: "enter_tier → escribano", depth: 0, t0: 0, t1: 31, detail: "",
    sub: false }] });
check("a sub-second span is drawn in milliseconds",
      fastBody.children.filter((c) => c.className === "line")[0]
        .children[2].textContent, "31 ms");
check("and what the server said about it",
      seqRows[1].children[3].textContent, "images 32");
// WHAT IS CUT IS STILL READABLE: the detail column ellipsises, so the
// whole of it is on the element too.
check("a truncated detail keeps its full text",
      seqRows[1].children[3].title, "images 32");
// AND THE SPEND SENTENCE IS ALWAYS THERE, because the number above it
// needs one.
check("the footnote about spend is drawn",
      seqBody.children.filter((c) => c.className === "foot").length, 1);

// THE DRAWING IS LIVE, so the signals it listens to have to be the ones
// that arrive. `state` is published on session open, session reopen and
// a locale change -- NEVER when a turn ends -- so hooking it alone left
// the sheet showing whatever was true when it was opened, with a button
// beside it to say so.
const onEventSrc = PAGE_HTML.slice(PAGE_HTML.indexOf("function onEvent"));
const branch = (kind) => {
  const at = onEventSrc.indexOf(`kind === "${kind}"`);
  return onEventSrc.slice(at, at + 400);
};
check("a transcript row redraws the sequence",
      branch("entry").includes("drawSequence"), true);
check("and so does a change of status",
      branch("status").includes("drawSequence"), true);
// AND A TICKER, because a turn that commissions a document spends ten
// seconds or more inside `decide` calls with no frame crossing the wire.
check("the sheet ticks while it is open",
      /seqTimer = setInterval\(/.test(PAGE_HTML), true);
check("and stops when it closes",
      /closeSequence\(\) \{[^}]*clearInterval\(seqTimer\)/s.test(PAGE_HTML),
      true);
check("so nothing offers to refresh what is not stale",
      PAGE_HTML.includes("seq.refresh"), false);

// NO CLASS INSIDE THE PANEL MAY ALSO BE STYLED UNSCOPED, and this is
// the general form of a defect that shipped: `.seq .bar` inherited the
// transcript's playback pill, whose `padding:0 12px` and border set a
// 26px floor under `*{box-sizing:border-box}` -- so EVERY span rendered
// at the same width whatever its duration, and a waterfall whose bars
// are not proportional is not a waterfall. `.row` was quieter: an
// inherited `border-bottom` under every line. Neither raised anything.
{
  const css = PAGE_HTML.slice(PAGE_HTML.indexOf("<style>"),
                              PAGE_HTML.indexOf("</style>"));
  const bare = new Set([...css.matchAll(/(?:^|\n|,)\s*\.([a-z][a-z0-9-]*)\s*(?=[{,:])/g)]
    .map((m) => m[1]));
  const mine = new Set([...css.matchAll(/\.(?:seq|dock) \.([a-z][a-z0-9-]*)/g)]
    .map((m) => m[1]));
  const clash = [...mine].filter((c) => bare.has(c) && c !== "seq").sort();
  check("no class in the sequence is also styled unscoped", clash, []);
}

// THE LEFT EDGE IS A HANDLE, and a handle only a mouse can reach is one
// some people do not have. The drag itself needs a browser; what can be
// asserted here is that the control is a real button carrying what a
// screen reader needs, and that losing the panel by dragging a little
// too far cannot happen.
const grip = T.seqGrip(new El("article"));
check("the handle is a real button", grip.tagName, "BUTTON");
check("that says what it is",
      [grip.attrs.role, grip.attrs["aria-orientation"]],
      ["separator", "vertical"]);
check("and what it does, from the catalogue",
      grip.attrs["aria-label"], "Ancho del panel: arrastra, o usa las flechas");
// THE PANEL IS A COLUMN OF THE PAGE, not a drawer over it: a scrim made
// the one thing it is for -- watching a turn happen -- impossible to do
// while taking that turn, because the tray was behind it.
check("the sequence is docked, not modal",
      /openSequence[\s\S]{0,900}\$\("main"\)\.append\(dock\)/.test(PAGE_HTML),
      true);
check("and it does not put a scrim over the conversation",
      /openSequence[\s\S]{0,900}scrim/.test(PAGE_HTML), false);
check("the button says whether the panel is showing",
      /seq-btn"\)\.setAttribute\("aria-pressed", "true"\)/.test(PAGE_HTML),
      true);
// AND THE BODY SCROLLS, NOT THE DOCK: an absolutely-placed handle in a
// scrolling box slides out of reach as you read down it.
check("the dock does not scroll itself",
      /\.dock\{[^}]*overflow:hidden/.test(PAGE_HTML), true);
// THE HANDLE MOVES THE TRACK, not the element: the dock is a grid
// column, so setting its own width would leave the column where it was
// and the panel would simply overflow it.
check("the handle moves the grid track",
      /setProperty\("--seq-w"/.test(PAGE_HTML), true);
// AND A WIDTH KEPT FROM A WIDE SCREEN MUST NOT EAT A NARROW ONE.
check("the track is capped in CSS, not only in the handler",
      /min\(var\(--seq-w,0px\),60vw\)/.test(PAGE_HTML), true);

const seqEmpty = new El("div");
T.paintSequence(seqEmpty, { wall: "x", total: 0, spans: [] });
check("a conversation with no turns says so, and draws no rows",
      [seqEmpty.children.filter((c) => c.className === "note").length,
       seqEmpty.children.filter((c) => c.className === "line").length], [1, 0]);

const seqFailed = new El("div");
T.paintSequence(seqFailed, null);
check("and an answer that never came says that instead",
      seqFailed.children[0].textContent, "No se pudo leer la secuencia.");

// ------------------------------------------------------- the backchannel
//
// The escriba acknowledging a demonstration while it is still recording.
// The server says WHICH clip; these legs are about what the page does
// with that, because both failures are silent: a leaked element every
// twenty seconds, or a reconnect replaying a word the person already
// heard.
{
  const plays = [];
  let made = 0;
  const RealAudio = sandbox.Audio;
  sandbox.Audio = function () {
    made += 1;
    return {
      preload: "", set src(v) { plays.push(v); }, play: () => Promise.resolve(),
    };
  };
  // ONE ELEMENT, REUSED.  A demonstration is minutes long with a
  // boundary every twenty seconds.
  T.sayBack({ clip: "/voz/es/back.ack1.ogg" });
  T.BACK.last = 0;                       // a boundary later, not a replay
  T.sayBack({ clip: "/voz/es/back.ack3.ogg" });
  check("one audio element serves every acknowledgement", made, 1);
  check("and each clip is played by name", plays,
        ["/voz/es/back.ack1.ogg", "/voz/es/back.ack3.ogg"]);

  // A GUEST HEARS THE SAME CLIP FROM THE SAME PATH, and this is the leg
  // that would have caught the bug. The page used to build the URL with
  // `api()`, which prefixes `/invitado` and appends the room to every
  // path it is handed; the twelve clips have no guest mirror, so a
  // guest's browser asked for `/invitado/voz/es/back.ack3.ogg` and got a
  // 404 that `play()`'s catch dropped in silence. MEASURED against the
  // real router on 2026-10-20: the host's path matched, the guest's
  // matched nothing. The server names the finished path now, so the two
  // browsers ask for one thing.
  const wasRoom = T.S.room;
  T.S.room = "a1b2c3d4";
  T.BACK.last = 0;
  T.sayBack({ clip: "/voz/es/back.ack1.ogg" });
  check("a guest asks for the clip where the host does", plays[plays.length - 1],
        "/voz/es/back.ack1.ogg");
  T.S.room = wasRoom;

  // A SECOND FRAME IN THE SAME SECOND IS A RECONNECT, not the escriba
  // speaking twice: the server thins boundaries to one per fifteen
  // seconds, so anything closer came from the page's own stream.
  const before = plays.length;
  T.sayBack({ clip: "/voz/es/back.ack2.ogg" });
  check("a frame replayed within the second is ignored", plays.length, before);

  // A FRAME WITH NO CLIP DOES NOTHING, which is what an older server
  // publishing a shape this page does not know looks like.
  T.sayBack({});
  T.sayBack(null);
  check("a frame with no clip is dropped", plays.length, before);
  sandbox.Audio = RealAudio;
}

// ------------------------------------------------- the demo button's reason
//
// Screen capture is DESKTOP-ONLY on the web: neither Chrome on Android
// nor Safari on iOS exposes `getDisplayMedia`, because the platform does
// not hand a page the screen at all.  So on a phone the demonstration
// cannot work however the page is written, and the button has to say so
// before it is pressed -- which is `pttFace`'s rule for the microphone.
{
  const btn = sandbox.document.getElementById("demo-btn");
  const note = sandbox.document.getElementById("demo-note");
  const real = sandbox.navigator.mediaDevices;
  // THE SANDBOX IS AN INSECURE ORIGIN, which is why `micReason` is set
  // throughout this suite -- and the context is the FIRST thing
  // `demoUnavailable` asks, so these legs have to say which one they
  // are in rather than inherit it.
  const secure = sandbox.isSecureContext;
  sandbox.isSecureContext = true;

  // A DESKTOP BROWSER: nothing is said and the button works.
  sandbox.navigator.mediaDevices = { getUserMedia: () => {},
                                     getDisplayMedia: () => {} };
  check("where the screen can be shared there is no reason",
        T.demoUnavailable(), null);

  // A PHONE: `getDisplayMedia` is simply absent.
  sandbox.navigator.mediaDevices = { getUserMedia: () => {} };
  check("a browser with no getDisplayMedia has one",
        T.demoUnavailable(), ES["demo.unsupported"]);
  T.S.demo.reason = T.demoUnavailable();
  T.demoFace();
  check("and the button is disabled rather than left to fail",
        btn && btn.disabled, true);
  check("with the reason where the invitation used to be",
        note && note.textContent, ES["demo.unsupported"]);
  check("marked as a fault", note && note.className, "demo-note bad");

  // THE CONTEXT IS ASKED FIRST, because a page on plain http has no
  // media APIs at all and "this browser cannot" would blame the browser
  // for the deployment.
  sandbox.isSecureContext = false;
  check("an insecure origin names itself, not the browser",
        T.demoUnavailable(), ES["demo.insecure"]);
  sandbox.isSecureContext = true;

  sandbox.navigator.mediaDevices = real;
  sandbox.isSecureContext = secure;
  T.S.demo.reason = null;
  T.demoFace();
  check("and the invitation comes back when it can be honoured",
        note && note.textContent, ES["demo.hint"]);
}

// ----------------------------------------------- the document reader
//
// HOW WIDE AND HOW BIG, REMEMBERED PER READER.  A document of
// screenshots is read at a different size from one of prose, and
// somebody who sets it once should not set it again on the next one.
{
  T.openReader({ path: "docs/demo/index.md", at: "19 oct 16:47" });
  const sheet = byId.get("reader-slot").walk()
    .find((n) => n.className === "sheet");
  check("the reader opens a sheet", !!sheet, true);
  check("with an edge to drag",
        !!(sheet && sheet.walk().find((n) => n.className === "grip")), true);
  const zoom = sheet && sheet.walk().find((n) => n.className === "zoom");
  check("and a zoom control", !!zoom, true);
  check("starting at the size it was left at",
        zoom && zoom.textContent.includes("100%"), true);

  // THROUGH A LADDER, so a zoom cannot drift to 103 % and cannot run
  // away: the ends hold.
  const [less, , more] = zoom.children;
  more.onclick(); more.onclick();
  check("stepping up moves through the ladder",
        Math.round(T.READ.zoom * 100), Math.round(T.ZOOMS[4] * 100));
  check("and is remembered for the next document",
        sandbox.localStorage.getItem("escriba-doc-zoom"), String(T.ZOOMS[4]));
  for (let i = 0; i < 20; i++) more.onclick();
  check("the top of the ladder holds", T.READ.zoom, T.ZOOMS[T.ZOOMS.length - 1]);
  for (let i = 0; i < 20; i++) less.onclick();
  check("and so does the bottom", T.READ.zoom, T.ZOOMS[0]);
  T.closeReader();
}

// ------------------------------------------------------ a download row
//
// THE ESCRIBA OFFERS A FILE OF THE PERSON'S OWN and the row is where it
// stays.  A document is written once and mentioned once; a sentence
// saying "ya está listo" scrolls away, and the path and the button have
// nowhere else durable to live.
T.onEvent("entry", { id: 500078, kind: "descarga",
                     at: new Date().toISOString(), repeats: 1,
                     text: "la documentación de la demostración",
                     path: "docs/dimensiones/index.md", size: 3223 });
{
  const row = byId.get("scroll").walk()
    .find((n) => n.dataset && String(n.dataset.id) === "500078");
  const text = row ? row.textContent : "";
  check("a download row is named for her",
        text.includes("la documentación de la demostración"), true);
  check("and says how big it is before she takes it",
        text.includes("3.1 MB") || text.includes("kB"), true);
  const link = row && row.walk().find((n) => n.tagName === "A");
  check("the link points at the serving route",
        !!(link && String(link.attrs.href || link.href)
                     .includes("/descarga?path=")), true);
  // `download` ASKS THE BROWSER TO SAVE rather than navigate: a
  // markdown file would otherwise render in a tab and a recording would
  // start playing, neither of which is what was offered.
  check("and asks the browser to save rather than open",
        !!(link && "download" in link.attrs), true);
}

// ------------------------------------------------------- a diagram row
//
// THE SOURCE IS WHAT TRAVELS.  `dibujar` sends Mermaid text and the
// PAGE draws it: nothing is rendered server-side, so the record holds
// something that journals, diffs and re-renders, and no renderer has to
// exist on the host.
//
// WHAT THIS CAN ASSERT, and it says so rather than pretending: the stub
// has no dynamic `import`, so mermaid never loads here and the picture
// is never made.  That is exactly the state worth pinning — the row
// must show the escriba's own source rather than an empty frame, which
// would read as "it drew nothing".
T.onEvent("entry", { id: 500077, kind: "diagrama",
                     at: new Date().toISOString(), repeats: 1,
                     text: "las cuatro dimensiones",
                     mermaid: "flowchart TD\n  A[bundle]-->B[fuentes]" });
{
  const row = byId.get("scroll").walk()
    .find((n) => n.dataset && String(n.dataset.id) === "500077");
  const text = row ? row.textContent : "";
  check("a diagram row is titled by what it is of",
        text.includes("las cuatro dimensiones"), true);
  // IT SAYS IT IS DRAWING, with the spinner the transcript uses while
  // it waits for words.  MEASURED on a live page 2026-10-19 15:17: the
  // first cut put the raw Mermaid in the slot until the picture
  // arrived, and the owner read it mid-load without knowing a drawing
  // was coming.  The source is the raw material of an answer, not one.
  check("a diagram row says it is drawing",
        text.includes(ES["diagram.drawing"]), true);
  check("with the same spinner the transcript waits with",
        !!(row && row.walk().find((n) => n.className === "spin")), true);
  check("in a slot the drawing can replace",
        !!(row && row.walk().find((n) => n.className === "draw")), true);
  // THE SOURCE IS THE FAILURE STATE NOW, and only that: with a reason
  // beside it, so "the drawing did not happen" cannot be read as "it
  // chose to answer in text".  Not reachable from this stub -- it has
  // no dynamic `import`, so the module never loads and never fails
  // here either -- which is said rather than faked.
  check("and the source is not shown as though it were the answer",
        text.includes("A[bundle]-->B[fuentes]"), false);
}

// ------------------------------------------- the newlines somebody wrote
//
// A GUARD ON A RULE NO DOM STUB CAN EXERCISE, and it says so rather than
// pretending to be a render test: the stub has no stylesheet, so the only
// thing assertable here is that the declaration is present.
//
// The reply arrives with real newlines in it -- MEASURED 2026-10-19,
// "...segun esas normas.\n\nPor cierto, ..." in the provider's own
// transcript -- and `el()` puts them in the DOM with `textContent`, which
// keeps them.  HTML then collapses every one into a single space on the
// way to the screen, so a reply written in three paragraphs reached the
// person as one long line and the breaks were invisible.
{
  const styles = PAGE_HTML.replace(/\s+/g, "");
  for (const sel of [".spokep{", ".heard{"]) {
    const at = styles.indexOf(sel);
    const rule = at < 0 ? "" : styles.slice(at, styles.indexOf("}", at));
    check(`${sel} keeps the line breaks it was given`,
          rule.includes("white-space:pre-line"), true);
  }
}

console.log(fail ? `${fail} failure(s)` : "web page renders OK");
process.exit(fail ? 1 : 0);
