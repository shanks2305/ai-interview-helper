const { app, BrowserWindow, globalShortcut, ipcMain, session, shell, systemPreferences } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const { createTray, destroyTray, setTrayListening } = require("./tray");

const API_BASE = (process.env.AI_INTERVIEW_API || "http://127.0.0.1:8000").replace(/\/$/, "");
const apiUrl = new URL(API_BASE);
const API_HOST = apiUrl.hostname;
const API_PORT = apiUrl.port || (apiUrl.protocol === "https:" ? "443" : "80");
const HEALTH_URL = `${API_BASE}/health`;
const PROJECT_ROOT = path.join(__dirname, "..");
const LISTEN_SHORTCUT = "CommandOrControl+Shift+L";

let backendProcess = null;
let startedBackend = false;
let mainWindow = null;
let isQuitting = false;
let pendingToggleListen = false;

function pythonExecutable() {
  const venvPython =
    process.platform === "win32"
      ? path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
      : path.join(PROJECT_ROOT, ".venv", "bin", "python");
  return fs.existsSync(venvPython) ? venvPython : "python3";
}

async function isHealthy() {
  try {
    const response = await fetch(HEALTH_URL);
    return response.ok;
  } catch {
    return false;
  }
}

function startBackend() {
  const python = pythonExecutable();
  backendProcess = spawn(
    python,
    ["-m", "ai_interview", "--api-only", "--host", API_HOST, "--port", String(API_PORT)],
    {
      cwd: PROJECT_ROOT,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    },
  );
  startedBackend = true;

  backendProcess.stdout?.on("data", (chunk) => {
    process.stdout.write(`[api] ${chunk}`);
  });
  backendProcess.stderr?.on("data", (chunk) => {
    process.stderr.write(`[api] ${chunk}`);
  });
  backendProcess.on("exit", (code) => {
    backendProcess = null;
    if (code && code !== 0) {
      console.error(`API process exited with code ${code}`);
    }
  });
}

async function waitForHealth(timeoutMs = 20000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await isHealthy()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("The interview API did not become healthy in time.");
}

async function ensureBackend() {
  if (await isHealthy()) {
    return;
  }
  startBackend();
  await waitForHealth();
}

function allowMicrophone() {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(true);
  });
}

async function ensureMicrophoneAccess() {
  if (process.platform !== "darwin" || !systemPreferences.askForMediaAccess) {
    return;
  }
  try {
    await systemPreferences.askForMediaAccess("microphone");
  } catch (error) {
    console.error("Microphone permission prompt failed:", error);
  }
}

function sendToggleListen() {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isLoading()) {
    pendingToggleListen = true;
    return;
  }
  mainWindow.webContents.send("interview:toggle-listen");
}

function createWindow({ show = true } = {}) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    return mainWindow;
  }

  const window = new BrowserWindow({
    width: 1120,
    height: 760,
    minWidth: 880,
    minHeight: 600,
    show,
    backgroundColor: "#07080c",
    title: "AI Interview",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: { x: 16, y: 18 },
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  window.loadURL(API_BASE);

  window.webContents.on("did-finish-load", () => {
    if (pendingToggleListen) {
      pendingToggleListen = false;
      window.webContents.send("interview:toggle-listen");
    }
  });

  window.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      window.hide();
    }
  });

  window.on("closed", () => {
    if (mainWindow === window) {
      mainWindow = null;
    }
  });

  mainWindow = window;
  return window;
}

function showWindow() {
  const window = createWindow();
  if (window.isMinimized()) {
    window.restore();
  }
  window.show();
  window.focus();
}

function trayHandlers() {
  return {
    onShow: showWindow,
    onToggle: sendToggleListen,
    onQuit: () => {
      isQuitting = true;
      app.quit();
    },
  };
}

function registerListenShortcut() {
  const registered = globalShortcut.register(LISTEN_SHORTCUT, () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      createWindow({ show: false });
    }
    sendToggleListen();
  });
  if (!registered) {
    console.error(`Could not register global shortcut ${LISTEN_SHORTCUT}`);
  }
}

ipcMain.on("interview:listening", (_event, isListening) => {
  setTrayListening(isListening, trayHandlers());
});

ipcMain.handle("interview:open-live", () => {
  return shell.openExternal(`${API_BASE}/live/`);
});

async function bootstrap() {
  await ensureBackend();
  allowMicrophone();
  await ensureMicrophoneAccess();
  createTray(trayHandlers());
  createWindow();
  registerListenShortcut();
}

app.whenReady().then(() => {
  bootstrap().catch((error) => {
    console.error(error);
    app.quit();
  });

  app.on("activate", () => {
    showWindow();
  });
});

app.on("window-all-closed", () => {
  // Stay in the tray so the global shortcut and status icon keep working.
});

app.on("before-quit", () => {
  isQuitting = true;
  globalShortcut.unregisterAll();
  destroyTray();
  if (startedBackend && backendProcess && !backendProcess.killed) {
    backendProcess.kill();
  }
});
