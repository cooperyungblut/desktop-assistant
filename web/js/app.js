// Wires the page together: WebSocket events in, user actions out.
import { marked } from "marked";
import DOMPurify from "dompurify";
import { Avatar } from "./avatar.js";
import { SpeechPlayer } from "./audio.js";

const $ = (id) => document.getElementById(id);
const els = {
  ready: $("ready"), readyLabel: $("ready-label"), caption: $("caption"),
  briefTitle: $("brief-title"), briefBody: $("brief-body"), history: $("history"),
  messages: $("messages"), form: $("ask-form"), input: $("ask-input"),
  askClaude: $("ask-claude"), stop: $("stop"), persona: $("persona-name"),
  note: $("avatar-note"),
};

// ---- avatar + speech ---------------------------------------------------------

const avatar = new Avatar($("avatar"));
avatar.load("models/avatar.vrm").catch((err) => {
  console.warn(err);
  els.note.hidden = false;
  els.note.textContent =
    "No avatar yet: save a .vrm file as web/models/avatar.vrm and reload (see web/models/README.md).";
});

const player = new SpeechPlayer({
  onLevel: (v) => { avatar.mouth = v; },
  onSentence: (text) => { els.caption.textContent = text; },
  onIdle: () => { els.caption.textContent = ""; avatar.mouth = 0; },
});

// ---- connection ----------------------------------------------------------------

let ws;
let readyId = null;
let replyEl = null;

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
  },
  briefs(e) {
    readyId = e.ready;
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
  reply_start() { replyEl = addMessage("her", "…"); replyEl.dataset.text = ""; },
  reply_token(e) {
    if (!replyEl) return;
    replyEl.dataset.text += e.text;
    replyEl.textContent = replyEl.dataset.text;
    scrollChat();
  },
  reply_end(e) { if (replyEl) replyEl.textContent = e.text; replyEl = null; },
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

function stopTalking() { player.stop(); send({ type: "stop" }); }

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

// Space starts the waiting brief (or stops speech); Esc stops. Ignored while typing.
document.addEventListener("keydown", (e) => {
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

// ---- helpers -------------------------------------------------------------------

function addMessage(kind, text) {
  const li = document.createElement("li");
  li.className = kind;
  li.textContent = text;
  els.messages.append(li);
  scrollChat();
  return li;
}
function scrollChat() { els.messages.scrollTop = els.messages.scrollHeight; }
function setDot(id, on) { $(id).classList.toggle("on", !!on); $(id).classList.toggle("off", !on); }
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

// Handy from the browser console while debugging: secretary.avatar.setExpression("happy")
window.secretary = { avatar, player };

connect();
