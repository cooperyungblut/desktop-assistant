const test = require("node:test");
const assert = require("node:assert/strict");
const { appUrl, sameOrigin, defaultBounds, onScreen } = require("../shell.js");
const {
  candidateKeys, pickHotkey, withChatRoom, avatarPart, chatSideFor, fitInside,
} = require("../shell.js");

test("loads the app's page in the desktop layout", () => {
  assert.equal(appUrl(), "http://127.0.0.1:8765/?desktop=1");
  assert.equal(appUrl(["--url=http://localhost:9000/"]), "http://localhost:9000/?desktop=1");
  assert.equal(appUrl([], { SECRETARY_URL: "http://127.0.0.1:8800" }), "http://127.0.0.1:8800/?desktop=1");
});

test("only the app's own origin counts", () => {
  assert.ok(sameOrigin("http://127.0.0.1:8765/x", "http://127.0.0.1:8765/?desktop=1"));
  assert.ok(!sameOrigin("http://127.0.0.1:9999/", "http://127.0.0.1:8765/"));
  assert.ok(!sameOrigin("https://evil.example/", "http://127.0.0.1:8765/"));
  assert.ok(!sameOrigin("not a url", "http://127.0.0.1:8765/"));
});

test("starts in the bottom-right corner above the taskbar", () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  assert.deepEqual(defaultBounds(area, { width: 400, height: 560 }),
    { x: 1504, y: 480, width: 400, height: 560 });
});

test("a position on an unplugged monitor is rejected", () => {
  const areas = [{ x: 0, y: 0, width: 1920, height: 1040 }];
  assert.ok(onScreen({ x: 1500, y: 480, width: 400, height: 560 }, areas));
  assert.ok(!onScreen({ x: 2200, y: 300, width: 400, height: 560 }, areas)); // second monitor gone
  assert.ok(!onScreen({ x: 100, y: 1100, width: 400, height: 560 }, areas));
});

test("a taken hotkey falls back to the next one", () => {
  const free = new Set(["Control+Alt+J"]);
  const r = pickHotkey(["Control+Alt+Space", "Control+Alt+J", "Control+Alt+X"], (k) => free.has(k));
  assert.deepEqual(r, { key: "Control+Alt+J", taken: ["Control+Alt+Space"] });
  assert.deepEqual(pickHotkey(["A"], () => false), { key: null, taken: ["A"] });
  assert.deepEqual(pickHotkey(["Bad", "B"], (k) => { if (k === "Bad") throw new Error(); return true; }),
    { key: "B", taken: ["Bad"] });
});

test("your own hotkey from settings goes first", () => {
  assert.deepEqual(candidateKeys("Control+Alt+T", ["X", "Y"]), ["Control+Alt+T", "X", "Y"]);
  assert.deepEqual(candidateKeys(["Y", ""], ["X", "Y"]), ["Y", "X"]);
  assert.deepEqual(candidateKeys(undefined, ["X"]), ["X"]);
});

test("the chat room is added on both sides of her and taken off again", () => {
  const avatar = { x: 1000, y: 480, width: 400, height: 560 };
  const win = withChatRoom(avatar, 380);
  assert.deepEqual(win, { x: 620, y: 480, width: 1160, height: 560 });
  assert.deepEqual(avatarPart(win, 380), avatar);
});

test("the chat shows on the chosen side unless she is at that screen edge", () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  const at = (x) => ({ x, y: 480, width: 400, height: 560 });
  assert.equal(chatSideFor(at(1000), area, "left", 380), "left");
  assert.equal(chatSideFor(at(1000), area, "right", 380), "right");
  assert.equal(chatSideFor(at(100), area, "left", 380), "right"); // no room to her left
  assert.equal(chatSideFor(at(1504), area, "right", 380), "left"); // no room to her right
  assert.equal(chatSideFor(at(380), area, "left", 380), "left"); // just fits
  // A screen too narrow for either side: keep the chosen one.
  const narrow = { x: 0, y: 0, width: 800, height: 1040 };
  assert.equal(chatSideFor(at(200), narrow, "right", 380), "right");
  // A second monitor to the right of the first.
  assert.equal(chatSideFor(at(1950), { ...area, x: 1920 }, "left", 380), "right");
});

test("a window past the screen edge slides back inside", () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  assert.deepEqual(fitInside({ x: 1504, y: 480, width: 780, height: 560 }, area),
    { x: 1140, y: 480, width: 780, height: 560 });
  assert.deepEqual(fitInside({ x: -50, y: 600, width: 780, height: 560 }, area),
    { x: 0, y: 480, width: 780, height: 560 });
  const inside = { x: 100, y: 100, width: 780, height: 560 };
  assert.deepEqual(fitInside(inside, area), inside);
});

test("hotkeys read the way her hints show them", () => {
  const { keyLabel } = require("../shell.js");
  assert.equal(keyLabel("Control+Alt+J"), "Ctrl+Alt+J");
  assert.equal(keyLabel("CommandOrControl+Shift+space"), "Ctrl+Shift+Space");
  assert.equal(keyLabel("Alt+F9"), "Alt+F9");
  assert.equal(keyLabel("Control+Alt+k"), "Ctrl+Alt+K");
  assert.equal(keyLabel(null), null);
});
