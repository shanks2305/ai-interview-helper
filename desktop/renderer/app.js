const statusEl = document.getElementById("status");
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const endSessionBtn = document.getElementById("end-session");
const introEl = document.getElementById("intro");
const roomEl = document.getElementById("room");
const errorEl = document.getElementById("error");
const recDotEl = document.getElementById("rec-dot");
const micLabelEl = document.getElementById("mic-label");
const timerEl = document.getElementById("timer");
const levelFillEl = document.getElementById("level-fill");
const sessionStatsEl = document.getElementById("session-stats");
const recapEl = document.getElementById("recap");
const recapBodyEl = document.getElementById("recap-body");
const recapSummaryEl = document.getElementById("recap-summary");
const recapStatsEl = document.getElementById("recap-stats");

const apiBase = window.interviewApp?.apiBase ?? "";
const wsUrl = window.interviewApp?.wsUrl ?? "ws://127.0.0.1:8000/ws/interview";
const liveUrl = `${apiBase || "http://127.0.0.1:8000"}/live/`;

let socket = null;
let mediaStream = null;
let recorder = null;
let audioContext = null;
let analyser = null;
let levelRaf = 0;
let startedAt = 0;
let timerId = 0;
let sessionActive = false;
let capturing = false;
let listenBusy = false;
let audioQueue = Promise.resolve();

function reportListening(isListening) {
  window.interviewApp?.setListening?.(isListening);
}

function setStatus(state, label) {
  statusEl.dataset.state = state;
  statusEl.textContent = label;
}

function showError(message) {
  errorEl.hidden = !message;
  errorEl.textContent = message ?? "";
}

function pickMimeType() {
  const types = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];
  return types.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

function formatElapsed(ms) {
  const total = Math.floor(ms / 1000);
  const minutes = String(Math.floor(total / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function setLive(live, label) {
  recDotEl.dataset.live = live ? "true" : "false";
  micLabelEl.textContent = label;
}

function setRoomControls() {
  if (!stopBtn) {
    return;
  }
  stopBtn.textContent = capturing ? "Pause" : "Listen";
}

function openLivePage() {
  if (window.interviewApp?.openLive) {
    window.interviewApp.openLive();
    return;
  }
  window.open(liveUrl, "_blank", "noopener,noreferrer");
}

function formatMs(ms) {
  if (!ms && ms !== 0) {
    return "—";
  }
  if (ms < 1000) {
    return `${Math.round(ms)}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

function applySession(session) {
  if (!sessionStatsEl) {
    return;
  }
  const stats = session?.stats ?? {};
  const turns = session?.turns ?? [];
  if (sessionStatsEl) {
    sessionStatsEl.hidden = true;
  }

  if (session?.active === false) {
    roomEl.hidden = true;
    recapEl.hidden = false;
    recapSummaryEl.textContent = turns.length
      ? `${stats.questions} questions in ${formatMs(stats.duration_ms)}. ` +
        `Average listen ${formatMs(stats.avg_listen_ms)}, average answer ${formatMs(stats.avg_answer_ms)}.`
      : "No questions were recorded in this session.";
    if (recapStatsEl) {
      recapStatsEl.hidden = !turns.length;
      recapStatsEl.replaceChildren();
      if (turns.length) {
        for (const [value, label] of [
          [String(stats.questions), "Questions"],
          [formatElapsed(stats.duration_ms), "Duration"],
          [formatMs(stats.avg_listen_ms), "Avg listen"],
          [formatMs(stats.avg_answer_ms), "Avg answer"],
        ]) {
          const card = document.createElement("article");
          card.className = "stat";
          const valueEl = document.createElement("p");
          valueEl.className = "stat-value";
          valueEl.textContent = value;
          const labelEl = document.createElement("p");
          labelEl.className = "stat-label";
          labelEl.textContent = label;
          card.append(valueEl, labelEl);
          recapStatsEl.append(card);
        }
      }
    }
    recapBodyEl.replaceChildren();
    for (const turn of turns) {
      const row = document.createElement("tr");
      for (const value of [
        String(turn.index ?? ""),
        turn.question || "",
        formatMs(turn.listen_ms),
        formatMs(turn.total_ms),
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      recapBodyEl.append(row);
    }
  }
}

function handleServerEvent(event) {
  const type = event.type;
  if (type === "status") {
    const state = event.state ?? "idle";
    if (state === "listening") {
      setStatus("ok", "Listening");
      setLive(true, event.detail || "Mic live · capturing question");
    } else if (state === "thinking") {
      setStatus("checking", "Drafting on live page");
      if (event.detail) {
        micLabelEl.textContent = event.detail;
      }
    } else if (state === "ready") {
      setStatus("ok", "Paused");
      setLive(false, event.detail || "Paused · listen for the next question");
    } else {
      setStatus("ok", "Paused");
      setLive(false, event.detail || "Mic idle");
    }
    return;
  }

  if (type === "session_start") {
    recapEl.hidden = true;
    recapBodyEl.replaceChildren();
    if (recapStatsEl) {
      recapStatsEl.hidden = true;
      recapStatsEl.replaceChildren();
    }
    introEl.hidden = true;
    roomEl.hidden = false;
    applySession(event.session);
    return;
  }

  if (type === "turn_stats" || type === "session_summary") {
    applySession(event.session);
    if (type === "session_summary") {
      setStatus("ok", "Session recap ready");
    }
    return;
  }

  if (type === "error") {
    showError(event.message || "The interview stream reported an error.");
    setStatus("down", "Stream error");
  }
}

function pumpLevel() {
  if (!analyser) {
    return;
  }
  const samples = new Uint8Array(analyser.fftSize);
  analyser.getByteTimeDomainData(samples);
  let sum = 0;
  for (const sample of samples) {
    const centered = (sample - 128) / 128;
    sum += centered * centered;
  }
  const rms = Math.sqrt(sum / samples.length);
  const width = capturing ? Math.min(100, Math.max(4, rms * 280)) : 4;
  levelFillEl.style.width = `${width}%`;
  levelRaf = requestAnimationFrame(pumpLevel);
}

function startTimer() {
  startedAt = Date.now();
  timerEl.textContent = "00:00";
  timerId = window.setInterval(() => {
    timerEl.textContent = formatElapsed(Date.now() - startedAt);
  }, 250);
}

function stopTimer() {
  window.clearInterval(timerId);
  timerId = 0;
}

function sendJson(payload) {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

function sendAudioChunk(blob) {
  if (!blob.size || socket?.readyState !== WebSocket.OPEN) {
    return audioQueue;
  }
  audioQueue = audioQueue.then(async () => {
    const buffer = await blob.arrayBuffer();
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(buffer);
    }
  }).catch(() => {
    showError("Could not send an audio chunk.");
  });
  return audioQueue;
}

function openSocket() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    const fail = (message) => {
      ws.close();
      reject(new Error(message));
    };
    const timeout = window.setTimeout(() => fail("WebSocket timed out."), 8000);
    ws.addEventListener("open", () => {
      window.clearTimeout(timeout);
      resolve(ws);
    });
    ws.addEventListener("error", () => {
      window.clearTimeout(timeout);
      fail("Could not open the interview WebSocket.");
    });
  });
}

function bindSocket(ws) {
  ws.addEventListener("message", (event) => {
    if (typeof event.data !== "string") {
      return;
    }
    try {
      handleServerEvent(JSON.parse(event.data));
    } catch {
      showError("Received a malformed server event.");
    }
  });
  ws.addEventListener("close", () => {
    if (sessionActive) {
      showError("The interview stream closed.");
      setStatus("down", "WebSocket closed");
      teardown(false).catch(() => {});
    }
  });
}

function startRecorder() {
  const mimeType = pickMimeType();
  recorder = mimeType
    ? new MediaRecorder(mediaStream, { mimeType })
    : new MediaRecorder(mediaStream);

  recorder.addEventListener("dataavailable", (event) => {
    sendAudioChunk(event.data);
  });

  recorder.start(250);
  sendJson({ type: "start", mimeType: recorder.mimeType });
}

async function ensureSocket() {
  if (socket?.readyState === WebSocket.OPEN) {
    sessionActive = true;
    introEl.hidden = true;
    recapEl.hidden = true;
    roomEl.hidden = false;
    startBtn.disabled = true;
    return true;
  }

  showError("");
  try {
    socket = await openSocket();
  } catch {
    reportListening(false);
    showError("Could not connect to ws://127.0.0.1:8000/ws/interview.");
    setStatus("down", "WebSocket down");
    startBtn.disabled = false;
    return false;
  }

  bindSocket(socket);
  sessionActive = true;
  introEl.hidden = true;
  recapEl.hidden = true;
  roomEl.hidden = false;
  startBtn.disabled = true;
  return true;
}

async function ensureMic() {
  if (mediaStream) {
    return true;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        channelCount: 1,
      },
    });
  } catch {
    reportListening(false);
    showError("Microphone permission was denied.");
    setStatus("down", "Mic blocked");
    startBtn.disabled = false;
    return false;
  }

  audioContext = new AudioContext();
  const source = audioContext.createMediaStreamSource(mediaStream);
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(analyser);
  pumpLevel();
  return true;
}

async function ensureSession() {
  if (!(await ensureSocket())) {
    return false;
  }
  return ensureMic();
}

async function submitTypedQuestion(inputEl) {
  const text = inputEl?.value.trim() || "";
  if (!text) {
    showError("Paste a question first.");
    return;
  }
  const ready = await ensureSocket();
  if (!ready) {
    return;
  }
  showError("");
  sendJson({ type: "ask", text });
  inputEl.value = "";
  setStatus("checking", "Drafting typed question");
}

async function startCapture() {
  if (capturing || listenBusy) {
    return;
  }
  listenBusy = true;
  startBtn.disabled = true;
  try {
    const ready = await ensureSession();
    if (!ready) {
      return;
    }
    startRecorder();
    capturing = true;
    recapEl.hidden = true;
    introEl.hidden = true;
    roomEl.hidden = false;
    setRoomControls();
    setLive(true, "Mic live · capturing question");
    setStatus("ok", "Listening");
    startTimer();
    reportListening(true);
  } finally {
    listenBusy = false;
  }
}

function stopRecorder(activeRecorder) {
  return new Promise((resolve) => {
    if (!activeRecorder || activeRecorder.state === "inactive") {
      resolve();
      return;
    }
    activeRecorder.addEventListener("stop", () => resolve(), { once: true });
    activeRecorder.stop();
  });
}

async function pauseCapture() {
  if (!capturing || listenBusy) {
    return;
  }
  listenBusy = true;
  const activeRecorder = recorder;
  recorder = null;
  capturing = false;
  setRoomControls();
  stopTimer();
  reportListening(false);
  sendJson({ type: "prime" });

  try {
    await stopRecorder(activeRecorder);
    await audioQueue;
    sendJson({ type: "stop" });
    setLive(false, "Paused · drafting on live page");
    setStatus("checking", "Drafting on live page");
  } finally {
    listenBusy = false;
  }
}

async function endSession() {
  if (listenBusy) {
    return;
  }
  if (capturing) {
    await pauseCapture();
  }
  sendJson({ type: "end_session" });
  setLive(false, "Session ended");
  setStatus("ok", "Session recap ready");
}

async function teardown(notifyServer) {
  const activeRecorder = recorder;
  recorder = null;
  capturing = false;
  sessionActive = false;
  listenBusy = true;
  setRoomControls();
  reportListening(false);

  await stopRecorder(activeRecorder);
  await audioQueue;
  if (notifyServer) {
    sendJson({ type: "stop" });
  }

  mediaStream?.getTracks().forEach((track) => track.stop());
  mediaStream = null;
  cancelAnimationFrame(levelRaf);
  levelRaf = 0;
  analyser = null;
  audioContext?.close().catch(() => {});
  audioContext = null;
  levelFillEl.style.width = "4%";
  stopTimer();

  if (socket) {
    const current = socket;
    socket = null;
    if (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING) {
      current.close();
    }
  }

  setLive(false, "Mic idle");
  roomEl.hidden = true;
  introEl.hidden = false;
  startBtn.disabled = statusEl.dataset.state === "down";
  listenBusy = false;
}

async function toggleCapture() {
  if (listenBusy) {
    return;
  }
  if (capturing) {
    await pauseCapture();
    return;
  }
  await startCapture();
}

async function pingHealth() {
  const response = await fetch(`${apiBase}/health`);
  if (!response.ok) {
    throw new Error("Health check failed");
  }
  return response.json();
}

async function connect() {
  if (sessionActive) {
    return;
  }
  try {
    await pingHealth();
    setStatus("ok", "API healthy");
    startBtn.disabled = false;
    showError("");
  } catch {
    setStatus("down", "API unavailable");
    startBtn.disabled = true;
    showError("Could not reach the local FastAPI backend on port 8000.");
  }
}

function shortcutLabel(accelerator) {
  const isMac = navigator.platform.toLowerCase().includes("mac");
  if (accelerator === "CommandOrControl+Shift+L") {
    return isMac ? "⌘⇧L" : "Ctrl+Shift+L";
  }
  return accelerator;
}

startBtn.addEventListener("click", () => {
  startCapture().catch(() => {
    startBtn.disabled = false;
    reportListening(false);
    showError("Could not start listening.");
  });
});
stopBtn.addEventListener("click", () => {
  toggleCapture().catch(() => {
    showError("Could not toggle listen / pause.");
  });
});
endSessionBtn?.addEventListener("click", () => {
  endSession().catch(() => {
    showError("Could not end the session.");
  });
});

for (const button of document.querySelectorAll("#open-live, #open-live-room, #open-live-recap")) {
  button.addEventListener("click", openLivePage);
}

document.getElementById("new-session")?.addEventListener("click", () => {
  startCapture().catch(() => {
    showError("Could not start listening.");
  });
});

function bindAskForm(form, input) {
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    submitTypedQuestion(input).catch(() => {
      showError("Could not send the question.");
    });
  });
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submitTypedQuestion(input).catch(() => {
        showError("Could not send the question.");
      });
    }
  });
}

bindAskForm(document.getElementById("ask-form"), document.getElementById("ask-input"));
bindAskForm(document.getElementById("intro-ask-form"), document.getElementById("intro-ask-input"));

const shortcutEl = document.getElementById("listen-shortcut");
if (shortcutEl && window.interviewApp?.listenShortcut) {
  shortcutEl.textContent = shortcutLabel(window.interviewApp.listenShortcut);
}

window.interviewApp?.onToggleListen?.(() => {
  toggleCapture().catch(() => {
    startBtn.disabled = false;
    reportListening(false);
    showError("Could not toggle listening from the global shortcut.");
  });
});

reportListening(false);
connect();
setInterval(connect, 8000);
