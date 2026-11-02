// Bridge between the desktop shell (main.js) and the page. The page only gets
// these few calls, never Node or Electron itself.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktopShell", {
  // true while the pointer is over something clickable, so clicks land on her
  // buttons and pass through everywhere else
  setInteractive: (on) => ipcRenderer.send("shell:interactive", !!on),
  setMoving: (on) => ipcRenderer.send("shell:moving", !!on),
  setChat: (on) => ipcRenderer.send("shell:chat", !!on),
  // tray and hotkey commands: "talk", "brief"
  onCommand: (fn) => ipcRenderer.on("shell:command", (_e, name) => fn(name)),
  onMoving: (fn) => ipcRenderer.on("shell:moving", (_e, on) => fn(on)),
  // { avatarWidth, chatSide }: her part of the window and where the chat goes
  onLayout: (fn) => ipcRenderer.on("shell:layout", (_e, layout) => fn(layout)),
  onChat: (fn) => ipcRenderer.on("shell:chat", (_e, on) => fn(on)),
});
