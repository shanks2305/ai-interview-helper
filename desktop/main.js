const { app, BrowserWindow, desktopCapturer, globalShortcut, ipcMain, session, shell, systemPreferences } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const { createTray, destroyTray, setTrayListening } = require("./tray");

app.commandLine.appendSwitch(
  "enable-features",
  [
    "MacLoopbackAudioForScreenShare",
    "MacCatapSystemAudioLoopbackCapture",
    "MacSckSystemAudioLoopbackOverride",
    "PulseaudioLoopbackForScreenShare",
  ].join(","),
);

const API_BASE = (process.env.AI_INTERVIEW_API || "http://127.0.0.1:8000").replace(/\/$/, "");
const apiUrl = new URL(API_BASE);
const API_HOST = apiUrl.hostname;
const API_PORT = apiUrl.port || (apiUrl.protocol === "https:" ? "443" : "80");
const HEALTH_URL = `${API_BASE}/health`;
// CLI (`uv run ai-interview`) starts the API and sets AI_INTERVIEW_SPAWN_API=0.
// Standalone `npm start` in desktop/ may start the API as a fallback.
const PROJECT_ROOT = path.join(__dirname, "..");
const LISTEN_SHORTCUT = "CommandOrControl+Shift+L";
const END_SESSION_SHORTCUT = "CommandOrControl+Shift+E";
const NEW_SESSION_SHORTCUT = "CommandOrControl+Shift+N";

let backendProcess = null;
let startedBackend = false;
let mainWindow = null;
let isQuitting = false;
const pendingIpc = new Set();

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

function spawnApiAllowed() {
  const value = String(process.env.AI_INTERVIEW_SPAWN_API ?? "1").toLowerCase();
  return value !== "0" && value !== "false" && value !== "no";
}

function startBackend() {
  const python = pythonExecutable();
  backendProcess = spawn(
    python,
    ["-m", "ai_interview", "--api-only", "--host", API_HOST, "--port", String(API_PORT)],
    {
      cwd: PROJECT_ROOT,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PYTHONUNBUFFERED: "1", AI_INTERVIEW_HOST: API_HOST },
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
    console.log(`Using interview API at ${API_BASE}`);
    return;
  }
  if (!spawnApiAllowed()) {
    throw new Error(
      `Interview API is not reachable at ${API_BASE}. Start it with: uv run ai-interview --api-only`,
    );
  }
  console.log(`Starting interview API at ${API_BASE}`);
  startBackend();
  await waitForHealth();
}

function screenAccessStatus() {
  if (process.platform !== "darwin" || !systemPreferences.getMediaAccessStatus) {
    return "unknown";
  }
  return systemPreferences.getMediaAccessStatus("screen");
}

function openScreenRecordingSettings() {
  if (process.platform !== "darwin") {
    return;
  }
  shell
    .openExternal(
      "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    )
    .catch((error) => {
      console.error("Could not open Screen Recording settings:", error);
    });
}

async function getScreenSources() {
  return desktopCapturer.getSources({
    types: ["screen"],
    thumbnailSize: { width: 0, height: 0 },
  });
}

function allowMedia() {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(true);
  });
  ses.setPermissionCheckHandler(() => true);
  ses.setDisplayMediaRequestHandler(async (request, callback) => {
    try {
      const status = screenAccessStatus();
      if (status === "denied") {
        console.error(
          "Screen Recording is denied. Enable it for Electron (or Terminal/Cursor if launched from there), then fully quit and relaunch.",
        );
        openScreenRecordingSettings();
        callback({});
        return;
      }
      const sources = await getScreenSources();
      const video = sources[0];
      if (!video) {
        console.error("No screen sources available for display media.");
        if (status !== "granted") {
          openScreenRecordingSettings();
        }
        callback({});
        return;
      }
      callback({
        video,
        audio: request.audioRequested ? "loopback" : undefined,
      });
    } catch (error) {
      console.error("Display media handler failed:", error?.message || error);
      if (screenAccessStatus() !== "granted") {
        openScreenRecordingSettings();
      }
      // Electron rejects getDisplayMedia when video was requested but omitted;
      // still call callback so the renderer gets a clean NotAllowedError.
      try {
        callback({});
      } catch (callbackError) {
        console.error("Failed to deny display media request:", callbackError);
      }
    }
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

function sendRenderer(channel) {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.webContents.isLoading()) {
    pendingIpc.add(channel);
    if (!mainWindow || mainWindow.isDestroyed()) {
      createWindow({ show: false });
    }
    return;
  }
  mainWindow.webContents.send(channel);
}

function sendToggleListen() {
  sendRenderer("interview:toggle-listen");
}

function sendEndSession() {
  sendRenderer("interview:end-session");
}

function sendNewSession() {
  sendRenderer("interview:new-session");
}

function createWindow({ show = true } = {}) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    return mainWindow;
  }

  const window = new BrowserWindow({
    width: 440,
    height: 760,
    minWidth: 400,
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
      autoplayPolicy: "no-user-gesture-required",
    },
  });

  window.loadURL(API_BASE);

  window.webContents.on("did-finish-load", () => {
    for (const channel of pendingIpc) {
      window.webContents.send(channel);
    }
    pendingIpc.clear();
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
    onEndSession: sendEndSession,
    onNewSession: sendNewSession,
    onQuit: () => {
      isQuitting = true;
      app.quit();
    },
  };
}

function registerGlobalShortcut(accelerator, action) {
  const registered = globalShortcut.register(accelerator, () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      createWindow({ show: false });
    }
    action();
  });
  if (!registered) {
    console.error(`Could not register global shortcut ${accelerator}`);
  }
}

function registerSessionShortcuts() {
  registerGlobalShortcut(LISTEN_SHORTCUT, sendToggleListen);
  registerGlobalShortcut(END_SESSION_SHORTCUT, sendEndSession);
  registerGlobalShortcut(NEW_SESSION_SHORTCUT, sendNewSession);
}

ipcMain.on("interview:listening", (_event, isListening) => {
  setTrayListening(isListening, trayHandlers());
});

ipcMain.handle("interview:open-live", () => {
  const token = (process.env.AI_INTERVIEW_TOKEN || "").trim();
  const live = new URL(`${API_BASE}/live/`);
  if (token) {
    live.searchParams.set("token", token);
  }
  return shell.openExternal(live.toString());
});

ipcMain.handle("interview:desktop-source", async () => {
  try {
    const sources = await getScreenSources();
    const source = sources[0];
    return source ? { id: source.id, name: source.name } : null;
  } catch (error) {
    console.error("Desktop source lookup failed:", error?.message || error);
    if (screenAccessStatus() !== "granted") {
      openScreenRecordingSettings();
    }
    return null;
  }
});

ipcMain.handle("interview:media-status", () => {
  if (process.platform !== "darwin" || !systemPreferences.getMediaAccessStatus) {
    return { microphone: "unknown", screen: "unknown" };
  }
  return {
    microphone: systemPreferences.getMediaAccessStatus("microphone"),
    screen: systemPreferences.getMediaAccessStatus("screen"),
  };
});

ipcMain.handle("interview:open-screen-settings", () => {
  openScreenRecordingSettings();
});

async function bootstrap() {
  await ensureBackend();
  allowMedia();
  await ensureMicrophoneAccess();
  createTray(trayHandlers());
  createWindow();
  registerSessionShortcuts();
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
