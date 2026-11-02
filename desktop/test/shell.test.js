const test = require("node:test");
const assert = require("node:assert/strict");
const { appUrl, sameOrigin, defaultBounds, onScreen } = require("../shell.js");
const { candidateKeys, pickHotkey } = require("../shell.js");

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
