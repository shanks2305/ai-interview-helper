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
  onToggleListen(callback) {
    const listener = () => {
      callback();
    };
    ipcRenderer.on("interview:toggle-listen", listener);
    return () => ipcRenderer.removeListener("interview:toggle-listen", listener);
  },
  setListening(isListening) {
    ipcRenderer.send("interview:listening", Boolean(isListening));
  },
  openLive() {
    return ipcRenderer.invoke("interview:open-live");
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
