const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("interviewApp", {
  apiBase: "http://127.0.0.1:8000",
  wsUrl: "ws://127.0.0.1:8000/ws/interview",
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
});
