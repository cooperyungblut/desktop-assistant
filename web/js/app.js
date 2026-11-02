// Wires the page together: WebSocket events in, user actions out.
import { marked } from "marked";
import DOMPurify from "dompurify";
import { Avatar } from "./avatar.js";
import { SpeechPlayer } from "./audio.js";
import { PushToTalk, blobToBase64 } from "./mic.js";

const $ = (id) => document.getElementById(id);
const els = {
  ready: $("ready"), readyLabel: $("ready-label"), caption: $("caption"),
  briefTitle: $("brief-title"), briefBody: $("brief-body"), history: $("history"),
  messages: $("messages"), form: $("ask-form"), input: $("ask-input"),
  askClaude: $("ask-claude"), stop: $("stop"), persona: $("persona-name"), mic: $("mic"),
  note: $("avatar-note"),
};

// ---- avatar + speech ---------------------------------------------------------

// The desktop window (desktop/, loads ?desktop) shows only the avatar and
// caps idle drawing to save GPU; ?fps=N overrides the cap (0 = no cap).
const params = new URLSearchParams(location.search);
const DESKTOP = params.has("desktop");
const idleFps = params.has("fps") ? Number(params.get("fps")) || 0 : DESKTOP ? 30 : 0;

const avatar = new Avatar($("avatar"), { idleFps });
avatar.load("models/avatar.vrm").catch((err) => {
  console.warn(err);
  els.note.hidden = false;
  els.note.textContent =
    "No avatar yet: save a .vrm file as web/models/avatar.vrm and reload (see web/models/README.md).";
});

// The face holds its mood for the whole reply, including the gaps while the
// next sentence is still being made, and settles back to neutral a moment
// after the last sentence (or right after an interruption).
const RELAX_AFTER_MS = 1500;
let relaxTimer = null;
let morePending = false; // the server hasn't said "speech_done" for this reply yet
function holdMood() { clearTimeout(relaxTimer); relaxTimer = null; }
function relaxSoon(ms = RELAX_AFTER_MS) {
  holdMood();
  relaxTimer = setTimeout(() => avatar.setExpression("neutral"), ms);
}

const player = new SpeechPlayer({
  onLevel: (v) => { avatar.mouth = v; },
  onSentence: (text) => { holdMood(); avatar.talking = true; els.caption.textContent = text; },
  onIdle: () => {
    els.caption.textContent = "";
    avatar.mouth = 0;
    if (morePending) return; // just a gap between sentences: keep the talking face
    avatar.talking = false;
    relaxSoon();
  },
});

// ---- connection ----------------------------------------------------------------

let ws;
let readyId = null;
let newestId = null; // newest brief, read or not
const replies = new Map(); // reply id -> chat bubble

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => setDot("dot-link", true);
  ws.onclose = () => { setDot("dot-link", false); setTimeout(connect, 2000); };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
}
const send = (msg) => ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify(msg));

const handlers = {
  status(e) {
    els.persona.textContent = e.persona;
    document.title = `${e.persona} · Desk Secretary`;
    setDot("dot-model", e.model);
    setDot("dot-tts", e.tts);
    setDot("dot-stt", e.stt);
    els.mic.disabled = !e.stt;
  },
  briefs(e) {
    readyId = e.ready;
    newestId = e.briefs[0]?.id ?? null;
    els.ready.hidden = !readyId;
    if (readyId) {
      const b = e.briefs.find((x) => x.id === readyId);
      els.readyLabel.textContent = `${b.sample ? "Sample " : ""}${cap(b.type)} brief ready`;
    }
    els.history.replaceChildren(new Option("History", ""),
      ...e.briefs.map((b) => new Option(`${b.date} ${b.type}${b.sample ? " (sample)" : ""}${b.handled ? "" : " (new)"}`, b.id)));
  },
  readout(e) {
    const b = e.brief;
    els.briefTitle.textContent = `${cap(b.type)} brief · ${b.date}`;
    if (b.sample) {
      const tag = document.createElement("span");
      tag.className = "sample-tag";
      tag.textContent = "SAMPLE";
      tag.title = "Made-up data for testing";
      els.briefTitle.append(" ", tag);
    }
    // Brief content includes email text, so treat it as untrusted HTML.
    els.briefBody.innerHTML = DOMPurify.sanitize(marked.parse(b.display));
    addMessage("note", `Reading the ${b.type} brief`);
  },
  say(e) { player.enqueue(e.text, e.audio); },
  expression(e) {
    holdMood();
    morePending = true;
    avatar.setFaces(e.faces, e.talk_faces);
  },
  speech_done() {
    morePending = false;
    if (!player.busy) { avatar.talking = false; relaxSoon(); }
  },
  // Replies carry an id, so a reply that was interrupted can still be closed out
  // even if a newer one has already started.
  reply_start(e) { replies.set(e.id, addMessage("her", "…")); },
  reply_token(e) {
    const el = replies.get(e.id);
    if (!el) return;
    el.dataset.text = (el.dataset.text || "") + e.text;
    el.textContent = el.dataset.text;
    scrollChat();
  },
  reply_end(e) {
    const el = replies.get(e.id);
    if (el) el.textContent = e.text;
    replies.delete(e.id);
  },
  transcript(e) {
    const el = pendingVoice ?? (e.text ? addMessage("you", "") : null);
    pendingVoice = null;
    if (!el) return;
    if (e.text) {
      el.className = "you";
      el.textContent = e.text;
      if (DESKTOP) flash(`You: ${e.text}`);
    } else {
      el.className = "note";
      el.textContent = "Didn't catch that. Hold V a little longer and try again?";
      if (DESKTOP) flash("Didn't catch that. Try again?");
    }
  },
  escalated(e) { addMessage("note", `Saved for Claude (${e.id}): “${e.question}”`); },
  error(e) { addMessage("note", `⚠ ${e.message}`); },
};
function handle(e) { handlers[e.type]?.(e); }

// ---- user actions --------------------------------------------------------------

function startBrief(id = readyId) {
  player.unlock();
  player.stop();
  send({ type: "start_brief", id });
}

function ask(text) {
  text = text.trim();
  if (!text) return;
  player.unlock();
  player.stop();
  addMessage("you", text);
  send({ type: "ask", text });
  els.input.value = "";
}

function stopTalking() {
  morePending = false;
  player.stop(); // its onIdle relaxes the face
  send({ type: "stop" });
}

// ---- push-to-talk ------------------------------------------------------------

let pendingVoice = null; // chat bubble waiting for its transcript

const ptt = new PushToTalk({
  onWaiting() { els.caption.textContent = "Starting microphone…"; },
  onStart(mode) {
    player.unlock();
    stopTalking(); // talking over her interrupts her, like a real conversation
    els.mic.classList.add("live");
    els.mic.setAttribute("aria-pressed", "true");
    els.caption.textContent = mode === "toggle"
      ? "Listening… (click the mic or pause to finish)"
      : "Listening…";
  },
  onStop() {
    els.mic.classList.remove("live");
    els.mic.setAttribute("aria-pressed", "false");
    if (els.caption.textContent.startsWith("Listening…")
        || els.caption.textContent === "Starting microphone…") {
      els.caption.textContent = "";
    }
  },
  async onClip(blob) {
    pendingVoice = addMessage("you pending", "🎙 …");
    send({ type: "voice", audio: await blobToBase64(blob) });
  },
  onError(message) { addMessage("note", `⚠ ${message}`); },
});

// The mic button is a toggle: click to talk, click again (or just stop talking
// for a few seconds) to send. Holding V still works as push-to-talk.
els.mic.addEventListener("click", () => {
  if (!els.mic.disabled) ptt.toggle();
});
// Switching windows ends a held V (its keyup would never arrive). A toggled
// recording keeps going and ends on silence.
window.addEventListener("blur", () => { if (ptt.mode === "hold") ptt.release(); });

els.ready.addEventListener("click", () => startBrief());
els.history.addEventListener("change", () => {
  if (els.history.value) startBrief(els.history.value);
  els.history.value = "";
});
els.form.addEventListener("submit", (e) => { e.preventDefault(); ask(els.input.value); });
els.askClaude.addEventListener("click", () => {
  if (els.input.value.trim()) ask(`/claude ${els.input.value}`);
  else els.input.focus();
});
els.stop.addEventListener("click", stopTalking);

// Space starts the waiting brief (or stops speech); Esc stops; hold V to talk.
// All ignored while typing in the question box.
document.addEventListener("keydown", (e) => {
  if (e.code === "KeyV" && e.target !== els.input && !els.mic.disabled) {
    e.preventDefault();
    if (!e.repeat) ptt.press();
    return;
  }
  if (e.target === els.input) {
    if (e.key === "Escape") stopTalking();
    return;
  }
  if (e.code === "Space") {
    e.preventDefault();
    if (player.busy) stopTalking();
    else if (readyId) startBrief();
    else els.input.focus();
  } else if (e.key === "Escape") {
    stopTalking();
  }
});

document.addEventListener("keyup", (e) => {
  if (e.code === "KeyV" && ptt.mode === "hold") ptt.release();
});

// ---- desktop window ----------------------------------------------------------

// window.desktopShell comes from desktop/preload.js; it's missing in a browser.
const shellApi = window.desktopShell;
if (shellApi) {
  // Clicks pass through the window except over something clickable. Electron
  // forwards mouse moves even while ignoring clicks, so this keeps up.
  let interactive = false;
  const setInteractive = (on) => {
    if (on !== interactive) shellApi.setInteractive((interactive = on));
  };
  document.addEventListener("mousemove", (e) =>
    setInteractive(!!e.target.closest?.("button:not([hidden]), select, input, [data-hit]")));
  document.addEventListener("mouseleave", () => setInteractive(false));

  shellApi.onMoving((on) => document.documentElement.classList.toggle("moving", on));
  $("move-done").addEventListener("click", () => shellApi.setMoving(false));

  // Global hotkeys and the tray menu (desktop/main.js).
  shellApi.onCommand((name) => {
    if (name === "talk" && !els.mic.disabled) ptt.toggle();
    else if (name === "brief") {
      // The waiting brief, or else the newest one again (the desktop window
      // has no history list).
      if (player.busy) stopTalking();
      else if (readyId || newestId) startBrief(readyId || newestId);
      else flash("No briefs yet.");
    }
  });
}

// With no chat panel on the desktop, notes and what you said flash in the caption.
let flashTimer = null;
function flash(text, ms = 4000) {
  if (player.busy) return;
  els.caption.textContent = text;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    if (els.caption.textContent === text) els.caption.textContent = "";
  }, ms);
}

// ---- helpers -------------------------------------------------------------------

function addMessage(kind, text) {
  const li = document.createElement("li");
  li.className = kind;
  li.textContent = text;
  els.messages.append(li);
  if (DESKTOP && kind === "note") flash(text);
  scrollChat();
  return li;
}
function scrollChat() { els.messages.scrollTop = els.messages.scrollHeight; }
function setDot(id, on) { $(id).classList.toggle("on", !!on); $(id).classList.toggle("off", !on); }
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

// Handy from the browser console while debugging:
//   secretary.avatar.setExpression("happy", 0.6, 0.3)   (weight, then weight while talking)
//   secretary.avatar.setFaces({ relaxed: 0.5, happy: 0.15 })   (a mix; half while talking)
//   secretary.avatar.setExpression("neutral")   back to a resting face
//   secretary.avatar.expressionNames()
window.secretary = { avatar, player, ptt };

connect();
