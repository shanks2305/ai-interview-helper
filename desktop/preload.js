const { contextBridge, ipcRenderer } = require("electron");

const apiBase = (process.env.AI_INTERVIEW_API || "http://127.0.0.1:8000").replace(/\/$/, "");
const token = (process.env.AI_INTERVIEW_TOKEN || "").trim();
const wsBase = apiBase.replace(/^https:/i, "wss:").replace(/^http:/i, "ws:");
const ws = new URL("/ws/interview", wsBase);
if (token) {
  ws.searchParams.set("token", token);
}

contextBridge.exposeInMainWorld("interviewApp", {
  apiBase,
  wsUrl: ws.toString(),
  token,
  listenShortcut: "CommandOrControl+Shift+L",
  endSessionShortcut: "CommandOrControl+Shift+E",
  overlayShortcut: "CommandOrControl+Shift+O",
  onToggleListen(callback) {
    const listener = () => {
      callback();
    };
    ipcRenderer.on("interview:toggle-listen", listener);
    return () => ipcRenderer.removeListener("interview:toggle-listen", listener);
  },
  onEndSession(callback) {
    const listener = () => {
      callback();
    };
    ipcRenderer.on("interview:end-session", listener);
    return () => ipcRenderer.removeListener("interview:end-session", listener);
  },
  onNewSession(callback) {
    const listener = () => {
      callback();
    };
    ipcRenderer.on("interview:new-session", listener);
    return () => ipcRenderer.removeListener("interview:new-session", listener);
  },
  setListening(isListening) {
    ipcRenderer.send("interview:listening", Boolean(isListening));
  },
  openLive() {
    return ipcRenderer.invoke("interview:open-live");
  },
  toggleOverlay() {
    return ipcRenderer.invoke("interview:toggle-overlay");
  },
  getDesktopSource() {
    return ipcRenderer.invoke("interview:desktop-source");
  },
  mediaStatus() {
    return ipcRenderer.invoke("interview:media-status");
  },
  openScreenSettings() {
    return ipcRenderer.invoke("interview:open-screen-settings");
  },
});
