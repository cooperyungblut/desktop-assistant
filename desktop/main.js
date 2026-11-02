// Desktop shell (Milestone 4 spike): Juniper in a frameless, transparent,
// always-on-top window on the desktop. The Python app still does all the work;
// this window just loads its page in the avatar-only layout.
//
//   npm start            (also starts the Python app if it isn't running)
//   npm start -- --no-app   (window only; you start the app yourself)
//
// Clicks pass through the window to whatever is behind it, except on her
// buttons. Move mode (hotkey or tray icon) lets you drag her around. The
// terminal lists which hotkeys she got.

const fs = require("node:fs");
const { spawn, spawnSync } = require("node:child_process");
const path = require("node:path");
const {
  app, BrowserWindow, Menu, Tray, globalShortcut, ipcMain, nativeImage, screen, session, shell,
} = require("electron");
const {
  SIZES, appUrl, sameOrigin, defaultBounds, onScreen, candidateKeys, pickHotkey,
} = require("./shell.js");

// The avatar is WebGL, so the window must render on the GPU. Electron does by
// default (unlike a browser with hardware acceleration turned off); never call
// app.disableHardwareAcceleration(). On laptops, prefer the discrete GPU.
app.commandLine.appendSwitch("force_high_performance_gpu");

const URL_TO_LOAD = appUrl(process.argv, process.env);
const SETTINGS_FILE = () => path.join(app.getPath("userData"), "settings.json");

let win = null;
let tray = null;
let moving = false;
let chatOpen = false;
let waitingLogged = false;
let backend = null; // the Python app, when this shell started it
const CHAT_WIDTH = 380; // the chat panel opens to her left
let settings = { size: "medium", bounds: null };

// ---- settings --------------------------------------------------------------

function loadSettings() {
  try {
    settings = { ...settings, ...JSON.parse(fs.readFileSync(SETTINGS_FILE(), "utf8")) };
  } catch {
    // first run, or an unreadable file: use the defaults
  }
  if (!SIZES[settings.size]) settings.size = "medium";
}

function saveSettings() {
  if (win) {
    // Remember where she sits, not the chat panel's extra width.
    const b = win.getBounds();
    settings.bounds = chatOpen ? { ...b, x: b.x + CHAT_WIDTH, width: b.width - CHAT_WIDTH } : b;
  }
  try {
    fs.writeFileSync(SETTINGS_FILE(), JSON.stringify(settings, null, 2));
  } catch (err) {
    console.warn("Couldn't save window position:", err.message);
  }
}

function startBounds() {
  const areas = screen.getAllDisplays().map((d) => d.workArea);
  const saved = settings.bounds;
  if (saved && onScreen(saved, areas)) return saved;
  return defaultBounds(screen.getPrimaryDisplay().workArea, SIZES[settings.size]);
}

// ---- window ----------------------------------------------------------------

function createWindow() {
  win = new BrowserWindow({
    ...startBounds(),
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: false, // transparent windows can't be edge-resized on Windows anyway
    alwaysOnTop: true,
    skipTaskbar: true, // she lives in the tray instead
    show: false,
    title: "Juniper",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      // Read a brief from the global hotkey without clicking the page first.
      autoplayPolicy: "no-user-gesture-required",
    },
  });
  win.setAlwaysOnTop(true, "floating");
  setClickThrough(true);

  win.once("ready-to-show", () => win.showInactive());
  win.webContents.once("did-finish-load", logGpu); // the GPU process is up by then
  win.webContents.on("did-finish-load", sendAvatarWidth);
  win.on("moved", saveSettings);
  win.on("closed", () => { win = null; });

  // Links in a brief open in the normal browser, never inside this window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!sameOrigin(url, URL_TO_LOAD)) e.preventDefault();
  });

  // The Python app may not be running yet: show a note and keep retrying.
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return; // -3: aborted by a newer load
    if (!waitingLogged) console.log(`Waiting for the app at ${URL_TO_LOAD} (${desc})…`);
    waitingLogged = true;
    win.loadURL(waitingPage());
    setTimeout(() => win?.loadURL(URL_TO_LOAD), 3000);
  });
  win.loadURL(URL_TO_LOAD);
}

function waitingPage() {
  const html = `<!doctype html><meta charset="utf-8"><body style="margin:0;height:100vh;
    display:grid;place-items:end center;background:transparent;font:14px system-ui,sans-serif">
    <p style="margin:24px;padding:10px 14px;border-radius:12px;background:#1e1b27e6;color:#ece9f5">
    Waiting for the app at ${URL_TO_LOAD.replace(/[?].*/, "")}…<br>
    ${backend ? "It's starting (the first start takes a moment)." : "Start it with <code>uv run desk-secretary --no-browser</code>."}</p>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

// While clicks pass through, Electron still forwards mouse moves to the page,
// so the page can say when the pointer is over one of her buttons.
function setClickThrough(on) {
  if (!win) return;
  if (on && !moving) win.setIgnoreMouseEvents(true, { forward: true });
  else win.setIgnoreMouseEvents(false);
}

function setMoving(on) {
  moving = on;
  if (!win) return;
  setClickThrough(!on);
  win.webContents.send("shell:moving", on);
  if (on) win.focus();
  else saveSettings();
  buildTrayMenu();
}

function setSize(name) {
  settings.size = name;
  if (win) {
    const b = win.getBounds();
    const s = SIZES[name];
    const width = s.width + (chatOpen ? CHAT_WIDTH : 0);
    // Grow or shrink around the bottom-right corner, where she usually sits.
    sendAvatarWidth(s.width);
    win.setBounds({ x: b.x + b.width - width, y: b.y + b.height - s.height, width, height: s.height });
  }
  saveSettings();
  buildTrayMenu();
}

// Her part of the window. The page pins her stage to this width at the right
// edge, so the window can grow to the left without moving her.
function sendAvatarWidth(width = win && win.getBounds().width - (chatOpen ? CHAT_WIDTH : 0)) {
  if (!win) return;
  win.webContents.send("shell:avatar-width", width);
}

// The chat panel (brief + messages + text box) widens the window to the left.
function setChat(on) {
  if (!win || on === chatOpen) return;
  sendAvatarWidth(); // pin her before the window changes size
  const b = win.getBounds();
  const dx = on ? CHAT_WIDTH : -CHAT_WIDTH;
  chatOpen = on;
  win.setBounds({ ...b, x: b.x - dx, width: b.width + dx });
  win.webContents.send("shell:chat", on);
  if (on) {
    if (!win.isVisible()) win.showInactive();
    win.focus(); // so you can type straight away
  }
  buildTrayMenu();
}

function toggleVisible() {
  if (!win) return;
  if (win.isVisible()) win.hide();
  else win.showInactive();
  buildTrayMenu();
}

// Commands for the page (see onCommand in web/js/app.js).
const command = (name) => win?.webContents.send("shell:command", name);

// ---- tray + hotkeys --------------------------------------------------------

// Each action tries its hotkeys in order and keeps the first one no other app
// has taken. "hotkeys" in settings.json can put your own first, e.g.
//   "hotkeys": { "talk": "Control+Alt+T" }
const HOTKEYS = {
  talk: { label: "Talk (toggle the mic)", run: () => command("talk"),
    keys: ["Control+Alt+Space", "Control+Alt+J", "Control+Alt+Shift+Space"] },
  brief: { label: "Read the brief / stop", run: () => command("brief"),
    keys: ["Control+Alt+B", "Control+Alt+K", "Control+Alt+Shift+B"] },
  move: { label: "Move mode on/off", run: () => setMoving(!moving),
    keys: ["Control+Alt+M", "Control+Alt+U", "Control+Alt+Shift+M"] },
  hide: { label: "Hide / show", run: () => toggleVisible(),
    keys: ["Control+Alt+H", "Control+Alt+Shift+H"] },
  chat: { label: "Chat panel open/close", run: () => setChat(!chatOpen),
    keys: ["Control+Alt+C", "Control+Alt+I", "Control+Alt+Shift+C"] },
};
const activeKeys = {}; // action -> the hotkey that registered

function registerHotkeys() {
  for (const [action, hk] of Object.entries(HOTKEYS)) {
    const keys = candidateKeys(settings.hotkeys?.[action], hk.keys);
    const { key, taken } = pickHotkey(keys, (k) => globalShortcut.register(k, hk.run));
    activeKeys[action] = key;
    if (taken.length) console.warn(`Hotkey ${taken.join(", ")} is taken by another app.`);
    if (key) console.log(`${hk.label}: ${key}`);
    else console.warn(`${hk.label}: no free hotkey; use the tray menu.`);
  }
}

// Shows the hotkey next to a tray item without registering it twice.
const keyFor = (action) => (activeKeys[action]
  ? { accelerator: activeKeys[action], registerAccelerator: false } : {});

function buildTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Talk", ...keyFor("talk"), click: () => command("talk") },
    { label: "Read the brief (new, else latest) / stop", ...keyFor("brief"), click: () => command("brief") },
    { label: "Chat", type: "checkbox", checked: chatOpen, ...keyFor("chat"), click: () => setChat(!chatOpen) },
    { type: "separator" },
    { label: "Move mode", type: "checkbox", checked: moving, ...keyFor("move"), click: () => setMoving(!moving) },
    {
      label: "Size",
      submenu: Object.keys(SIZES).map((name) => ({
        label: name[0].toUpperCase() + name.slice(1), type: "radio",
        checked: settings.size === name, click: () => setSize(name),
      })),
    },
    { label: win?.isVisible() ? "Hide" : "Show", ...keyFor("hide"), click: toggleVisible },
    { type: "separator" },
    { label: "Open full page in browser (chat, brief)", click: () => shell.openExternal(URL_TO_LOAD.replace(/[?].*/, "")) },
    { label: "Reload", click: () => win?.reload() },
    { label: "Developer tools", click: () => win?.webContents.openDevTools({ mode: "detach" }) },
    { label: "GPU report", click: openGpuReport },
    { label: "Settings file (custom hotkeys)", click: () => { saveSettings(); shell.openPath(SETTINGS_FILE()); } },
    { type: "separator" },
    { label: "Quit", click: () => app.quit() },
  ]));
}

function openGpuReport() {
  const w = new BrowserWindow({ width: 900, height: 700, title: "GPU report" });
  w.loadURL("chrome://gpu");
}

// A purple dot, so the tray needs no image file.
function trayIcon() {
  const size = 32;
  const px = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - 15.5, y - 15.5);
      const a = Math.max(0, Math.min(1, 14 - d)); // soft edge
      px.set([0xf6 * a, 0x5c * a, 0x8a * a, 255 * a].map(Math.round), (y * size + x) * 4); // BGRA
    }
  }
  return nativeImage.createFromBitmap(px, { width: size, height: size });
}

// ---- GPU check -------------------------------------------------------------

// Printed once at startup so it's easy to confirm she renders on the GPU:
// "webgl enabled, compositing enabled" is what we want. "disabled_software"
// or "unavailable_software" means she's being drawn on the CPU.
async function logGpu() {
  const s = app.getGPUFeatureStatus();
  console.log(`GPU: webgl ${s.webgl}, compositing ${s.gpu_compositing}, rasterization ${s.rasterization}`);
  try {
    const info = await app.getGPUInfo("basic");
    const active = info.gpuDevice?.find((d) => d.active) ?? info.gpuDevice?.[0];
    if (active) console.log(`GPU device: vendor 0x${active.vendorId.toString(16)}, device 0x${active.deviceId.toString(16)}`);
  } catch {
    // not available on every platform; the feature status above is enough
  }
}

// ---- the Python app ----------------------------------------------------------

async function appRunning() {
  try {
    const res = await fetch(new URL("/", URL_TO_LOAD), { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

// One launch starts everything: if the app isn't already running, start it
// from the repo folder and stop it again on Quit. Its log shows in this terminal.
async function startBackend() {
  if (process.argv.includes("--no-app") || (await appRunning())) return;
  const root = path.resolve(__dirname, "..");
  console.log("Starting the app: uv run desk-secretary --no-browser");
  const child = spawn("uv", ["run", "desk-secretary", "--no-browser"], {
    cwd: root, stdio: "inherit", windowsHide: true,
  });
  backend = child;
  child.on("error", (err) => {
    console.warn(`Couldn't start the app (${err.message}). Start it yourself: uv run desk-secretary --no-browser`);
    if (backend === child) backend = null;
  });
  child.on("exit", (code) => {
    if (backend === child) console.log(`The app stopped (exit code ${code}).`);
    if (backend === child) backend = null;
  });
}

function stopBackend() {
  const child = backend;
  backend = null;
  if (!child?.pid) return;
  // On Windows "uv run" starts Python as its own child: end the whole tree.
  if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  else child.kill();
}

// ---- start -----------------------------------------------------------------

if (!app.requestSingleInstanceLock()) {
  app.quit(); // she's already on the desktop
} else {
  app.on("second-instance", () => win?.showInactive());

  app.whenReady().then(() => {
    loadSettings();

    // Microphone for push-to-talk: allowed for the app's own page, nothing else.
    const allowed = (wc, permission, origin) =>
      permission === "media" && sameOrigin(origin || wc?.getURL(), URL_TO_LOAD);
    session.defaultSession.setPermissionRequestHandler((wc, permission, cb, details) =>
      cb(allowed(wc, permission, details.requestingUrl)));
    session.defaultSession.setPermissionCheckHandler((wc, permission, origin) =>
      allowed(wc, permission, origin));

    ipcMain.on("shell:interactive", (_e, on) => setClickThrough(!on));
    ipcMain.on("shell:moving", (_e, on) => setMoving(!!on));
    ipcMain.on("shell:chat", (_e, on) => setChat(!!on));

    startBackend().finally(createWindow);
    tray = new Tray(trayIcon());
    tray.setToolTip("Juniper");
    tray.on("click", toggleVisible);
    buildTrayMenu();
    registerHotkeys();
  });

  app.on("will-quit", () => {
    globalShortcut.unregisterAll();
    stopBackend();
  });
  app.on("window-all-closed", () => app.quit());
}
