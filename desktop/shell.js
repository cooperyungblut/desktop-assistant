// Small pure helpers for main.js, kept apart so `node --test` can check them
// without starting Electron.

const DEFAULT_URL = "http://127.0.0.1:8765/";

// Window sizes offered in the tray menu (CSS pixels). Transparent windows on
// Windows can't be resized by dragging an edge, so the menu picks one.
const SIZES = {
  small: { width: 300, height: 420 },
  medium: { width: 400, height: 560 },
  large: { width: 520, height: 720 },
};

// The page the window loads: the app's own page in its avatar-only layout.
// `--url=...` on the command line (or SECRETARY_URL) points it elsewhere.
function appUrl(argv = [], env = {}) {
  const arg = argv.find((a) => a.startsWith("--url="));
  const url = new URL(arg ? arg.slice("--url=".length) : env.SECRETARY_URL || DEFAULT_URL);
  url.searchParams.set("desktop", "1");
  return url.toString();
}

// Only the app's own page may use the microphone (and only it is loaded).
function sameOrigin(a, b) {
  try {
    return new URL(a).origin === new URL(b).origin;
  } catch {
    return false;
  }
}

// Default spot: bottom-right corner of the work area (above the taskbar).
function defaultBounds(workArea, size, margin = 16) {
  return {
    x: workArea.x + workArea.width - size.width - margin,
    y: workArea.y + workArea.height - size.height,
    width: size.width,
    height: size.height,
  };
}

// A saved position can end up off-screen (a monitor unplugged, resolution
// changed). Keep at least `keep` pixels of the window on some display, or
// fall back to the default spot on the primary one.
function onScreen(bounds, workAreas, keep = 80) {
  return workAreas.some((a) =>
    bounds.x + bounds.width - keep >= a.x &&
    bounds.x + keep <= a.x + a.width &&
    bounds.y + keep <= a.y + a.height &&
    bounds.y >= a.y - keep);
}

// Hotkeys to try for one action: the user's own choice (a string or a list
// from settings.json) first, then the defaults, without repeats.
function candidateKeys(custom, defaults) {
  const mine = [].concat(custom ?? []).filter((k) => typeof k === "string" && k.trim());
  return [...new Set([...mine, ...defaults])];
}

// Global hotkeys are first come, first served on Windows: registering one that
// another app holds fails. Take the first one that works.
function pickHotkey(keys, tryRegister) {
  const taken = [];
  for (const key of keys) {
    let ok = false;
    try {
      ok = tryRegister(key);
    } catch {
      ok = false; // an invalid accelerator string in settings.json
    }
    if (ok) return { key, taken };
    taken.push(key);
  }
  return { key: null, taken };
}

// The window always has room for the chat panel on both sides of her, empty
// and click-through while the panel is closed. Resizing a window on Windows
// shows the old frame at the new position for a moment, so neither opening
// the panel nor switching its side may resize or move the window. Settings
// store her part only.
function withChatRoom(avatar, chatWidth) {
  return { ...avatar, x: avatar.x - chatWidth, width: avatar.width + 2 * chatWidth };
}
function avatarPart(window, chatWidth) {
  return { ...window, x: window.x + chatWidth, width: window.width - 2 * chatWidth };
}

// Which side of her the chat panel shows on: the chosen side when it fits on
// her screen, else the other side when that fits, else the chosen one anyway.
function chatSideFor(avatar, area, preferred, chatWidth) {
  const fits = {
    left: avatar.x - chatWidth >= area.x,
    right: avatar.x + avatar.width + chatWidth <= area.x + area.width,
  };
  const other = preferred === "right" ? "left" : "right";
  return fits[preferred] || !fits[other] ? preferred : other;
}

// Slide a window back inside a screen's work area (it keeps its size).
function fitInside(b, area) {
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
  return {
    ...b,
    x: clamp(b.x, area.x, area.x + area.width - b.width),
    y: clamp(b.y, area.y, area.y + area.height - b.height),
  };
}

module.exports = {
  DEFAULT_URL, SIZES, appUrl, sameOrigin, defaultBounds, onScreen, candidateKeys, pickHotkey,
  withChatRoom, avatarPart, chatSideFor, fitInside,
};
