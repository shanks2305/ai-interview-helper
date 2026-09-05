const statusEl = document.getElementById("status");
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const endSessionBtn = document.getElementById("end-session");
const introEl = document.getElementById("intro");
const roomEl = document.getElementById("room");
const doneEl = document.getElementById("done");
const errorEl = document.getElementById("error");
const recDotEl = document.getElementById("rec-dot");
const micLabelEl = document.getElementById("mic-label");
const timerEl = document.getElementById("timer");
const captureCardEl = document.getElementById("capture-card");
const captureModeEl = document.getElementById("capture-mode");
const micDeviceEl = document.getElementById("mic-device");
const micDeviceFieldEl = document.getElementById("mic-device-field");
const meetingSourceEl = document.getElementById("meeting-source");
const meetingSourceFieldEl = document.getElementById("meeting-source-field");
const audioHintEl = document.getElementById("audio-hint");
const levelHintEl = document.getElementById("level-hint");
const gainEl = document.getElementById("input-gain");
const gainValueEl = document.getElementById("gain-value");
const testInputBtn = document.getElementById("test-input");

const CAPTURE_PREFS_KEY = "ai-interview-capture";
const LOOPBACK_PATTERN =
  /blackhole|loopback|soundflower|vb-audio|cable|stereo mix|what u hear|multi-output|aggregate/i;
const MEETING_SETUP_HINT =
  "Could not capture meeting audio. On macOS, allow Screen Recording, or install BlackHole and pick it under Meeting source. On Windows, try Stereo Mix or VB-Audio.";

const LEVEL_HINT_IDLE = "Test the input before you listen so Whisper sees a clean signal.";
const GAIN_MIN = 0.4;
const GAIN_MAX = 3;

const meters = {
  mic: {
    row: document.getElementById("level-mic-row"),
    fill: document.getElementById("level-mic-fill"),
    status: document.getElementById("level-mic-status"),
    analyser: null,
    displayed: 0,
  },
  meeting: {
    row: document.getElementById("level-meeting-row"),
    fill: document.getElementById("level-meeting-fill"),
    status: document.getElementById("level-meeting-status"),
    analyser: null,
    displayed: 0,
  },
};

let ownedStreams = [];
let captureGraphKey = "";
let gainNodes = [];
let graphNodes = [];
let keepAliveGain = null;
let monitorEl = null;
let previewing = false;

const apiBase = window.interviewApp?.apiBase ?? "";
const wsUrl = window.interviewApp?.wsUrl ?? "ws://127.0.0.1:8000/ws/interview";
const liveUrl = `${apiBase || "http://127.0.0.1:8000"}/live/`;

let socket = null;
let mediaStream = null;
let recorder = null;
let audioContext = null;
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

function loadCapturePrefs() {
  try {
    const stored = JSON.parse(localStorage.getItem(CAPTURE_PREFS_KEY) || "{}");
    return stored && typeof stored === "object" ? stored : {};
  } catch {
    return {};
  }
}

function saveCapturePrefs() {
  localStorage.setItem(
    CAPTURE_PREFS_KEY,
    JSON.stringify({
      mode: captureModeEl?.value || "both",
      micDeviceId: micDeviceEl?.value || "",
      meetingSource: meetingSourceEl?.value || "auto",
      gain: currentGain(),
    }),
  );
}

function captureMode() {
  const mode = captureModeEl?.value;
  return mode === "mic" || mode === "meeting" || mode === "both" ? mode : "both";
}

function wantsMic() {
  const mode = captureMode();
  return mode === "mic" || mode === "both";
}

function wantsMeeting() {
  const mode = captureMode();
  return mode === "meeting" || mode === "both";
}

function meetingSource() {
  const value = meetingSourceEl?.value;
  return value && typeof value === "string" ? value : "auto";
}

function currentGain() {
  const raw = Number(gainEl?.value);
  if (!Number.isFinite(raw)) {
    return 1;
  }
  return Math.min(GAIN_MAX, Math.max(GAIN_MIN, raw));
}

function applyGain() {
  const gain = currentGain();
  if (gainValueEl) {
    gainValueEl.textContent = `${gain.toFixed(1)}×`;
  }
  for (const node of gainNodes) {
    node.gain.value = gain;
  }
}

function disconnectGraph() {
  for (const node of graphNodes) {
    try {
      node.disconnect();
    } catch {
      // Node was already disconnected or closed with the context.
    }
  }
  graphNodes = [];
  gainNodes = [];
}

function stopMonitor() {
  if (!monitorEl) {
    return;
  }
  monitorEl.pause();
  monitorEl.srcObject = null;
  monitorEl = null;
}

function monitorMix(stream) {
  stopMonitor();
  monitorEl = new Audio();
  monitorEl.srcObject = stream;
  monitorEl.muted = true;
  monitorEl.volume = 0;
  monitorEl.play().catch(() => {});
}

function ensureAudioContext() {
  if (!audioContext || audioContext.state === "closed") {
    audioContext = new AudioContext();
    keepAliveGain = audioContext.createGain();
    keepAliveGain.gain.value = 0;
    keepAliveGain.connect(audioContext.destination);
  }
  if (audioContext.state === "suspended") {
    return audioContext.resume().catch(() => {});
  }
  return Promise.resolve();
}

function captureLiveLabel() {
  const mode = captureMode();
  if (mode === "meeting") {
    return "Meeting audio · capturing question";
  }
  if (mode === "both") {
    return "Mic + meeting · capturing question";
  }
  return "Mic live · capturing question";
}

function captureIdleLabel() {
  return "Audio idle";
}

function isLoopbackDevice(device) {
  return LOOPBACK_PATTERN.test(device.label || "");
}

function stopOwnedStreams() {
  for (const stream of ownedStreams) {
    stream.getTracks().forEach((track) => track.stop());
  }
  ownedStreams = [];
}

function rememberStream(stream) {
  if (stream) {
    ownedStreams.push(stream);
  }
  return stream;
}

function forgetStream(stream) {
  if (!stream) {
    return;
  }
  stream.getTracks().forEach((track) => track.stop());
  ownedStreams = ownedStreams.filter((owned) => owned !== stream);
}

function dropVideoTracks(stream) {
  stream.getVideoTracks().forEach((track) => track.stop());
  return new MediaStream(stream.getAudioTracks());
}

function micConstraints() {
  const deviceId = micDeviceEl?.value;
  const audio = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
  };
  if (deviceId) {
    audio.deviceId = { exact: deviceId };
  }
  return { audio };
}

function meetingConstraints() {
  return {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
  };
}

async function listAudioInputs() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((device) => device.kind === "audioinput");
}

async function populateMicDevices() {
  if (!micDeviceEl) {
    return;
  }
  const selected = micDeviceEl.value || loadCapturePrefs().micDeviceId || "";
  const inputs = await listAudioInputs();
  micDeviceEl.replaceChildren();
  const defaultOption = document.createElement("option");
  defaultOption.value = "";
  defaultOption.textContent = "System default";
  micDeviceEl.append(defaultOption);
  for (const device of inputs) {
    if (!device.deviceId) {
      continue;
    }
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label
      ? `${device.label}${isLoopbackDevice(device) ? " (loopback)" : ""}`
      : `Microphone ${micDeviceEl.options.length}`;
    micDeviceEl.append(option);
  }
  if (selected && [...micDeviceEl.options].some((option) => option.value === selected)) {
    micDeviceEl.value = selected;
  }
}

function addMeetingOption(value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  meetingSourceEl.append(option);
}

async function populateMeetingSources() {
  if (!meetingSourceEl) {
    return;
  }
  const selected = meetingSourceEl.value || loadCapturePrefs().meetingSource || "auto";
  meetingSourceEl.replaceChildren();
  addMeetingOption("auto", "Auto (system, then loopback)");
  addMeetingOption("system", "System / tab audio");
  const inputs = await listAudioInputs();
  for (const device of inputs) {
    if (!device.deviceId || !isLoopbackDevice(device)) {
      continue;
    }
    addMeetingOption(`device:${device.deviceId}`, device.label || "Loopback device");
  }
  if (selected && [...meetingSourceEl.options].some((option) => option.value === selected)) {
    meetingSourceEl.value = selected;
  } else {
    meetingSourceEl.value = "auto";
  }
}

async function findLoopbackDevice() {
  const inputs = await listAudioInputs();
  return inputs.find((device) => device.deviceId && isLoopbackDevice(device)) ?? null;
}

async function captureMicrophone() {
  try {
    return rememberStream(await navigator.mediaDevices.getUserMedia(micConstraints()));
  } catch {
    throw new Error("Microphone permission was denied.");
  }
}

async function captureDisplayLoopback() {
  if (!navigator.mediaDevices.getDisplayMedia) {
    return null;
  }
  try {
    const display = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: true,
    });
    rememberStream(display);
    const audio = dropVideoTracks(display);
    if (!audio.getAudioTracks().length) {
      forgetStream(display);
      return null;
    }
    return audio;
  } catch (error) {
    if (error?.name === "NotAllowedError") {
      throw new Error(
        "Meeting audio was blocked. Allow screen / system audio capture and try again.",
      );
    }
    return null;
  }
}

async function captureChromeDesktopLoopback() {
  const source = await window.interviewApp?.getDesktopSource?.();
  if (!source?.id) {
    return null;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        mandatory: {
          chromeMediaSource: "desktop",
          chromeMediaSourceId: source.id,
        },
      },
      video: {
        mandatory: {
          chromeMediaSource: "desktop",
          chromeMediaSourceId: source.id,
          maxWidth: 2,
          maxHeight: 2,
        },
      },
    });
    rememberStream(stream);
    const audio = dropVideoTracks(stream);
    if (!audio.getAudioTracks().length) {
      forgetStream(stream);
      return null;
    }
    return audio;
  } catch {
    return null;
  }
}

async function captureLoopbackDevice(deviceId = "") {
  const device = deviceId
    ? { deviceId }
    : await findLoopbackDevice();
  if (!device?.deviceId) {
    return null;
  }
  return rememberStream(
    await navigator.mediaDevices.getUserMedia({
      audio: {
        ...meetingConstraints(),
        deviceId: { exact: device.deviceId },
        channelCount: 1,
      },
    }),
  );
}

async function captureMeetingAudio() {
  const source = meetingSource();
  if (source.startsWith("device:")) {
    const stream = await captureLoopbackDevice(source.slice("device:".length));
    if (!stream) {
      throw new Error("Could not open the selected loopback device.");
    }
    return stream;
  }
  if (source === "auto" || source === "system") {
    const display = await captureDisplayLoopback();
    if (display) {
      return display;
    }
    const chromeDesktop = await captureChromeDesktopLoopback();
    if (chromeDesktop) {
      return chromeDesktop;
    }
    if (source === "system") {
      throw new Error(MEETING_SETUP_HINT);
    }
  }
  const virtual = await captureLoopbackDevice();
  if (virtual) {
    return virtual;
  }
  throw new Error(MEETING_SETUP_HINT);
}

function resetMeters() {
  for (const meter of Object.values(meters)) {
    meter.analyser = null;
    meter.displayed = 0;
    if (meter.fill) {
      meter.fill.style.width = "3%";
    }
    if (meter.row) {
      meter.row.dataset.band = "silent";
    }
    if (meter.status) {
      meter.status.textContent = "Silent";
    }
  }
  if (levelHintEl && !previewing && !capturing) {
    levelHintEl.textContent = LEVEL_HINT_IDLE;
  }
}

function syncLevelRows() {
  if (meters.mic.row) {
    meters.mic.row.hidden = !wantsMic();
  }
  if (meters.meeting.row) {
    meters.meeting.row.hidden = !wantsMeeting();
  }
}

function buildCaptureGraph(labeledStreams) {
  if (!audioContext || audioContext.state === "closed") {
    throw new Error("Audio context is not ready.");
  }
  disconnectGraph();
  const destination = audioContext.createMediaStreamDestination();
  const gain = currentGain();
  for (const { label, stream } of labeledStreams) {
    if (!stream?.getAudioTracks().length) {
      continue;
    }
    const source = audioContext.createMediaStreamSource(stream);
    const node = audioContext.createGain();
    node.gain.value = gain;
    const tap = audioContext.createAnalyser();
    tap.fftSize = 2048;
    tap.smoothingTimeConstant = 0.35;
    source.connect(node);
    node.connect(destination);
    node.connect(tap);
    tap.connect(keepAliveGain);
    graphNodes.push(source, node, tap);
    gainNodes.push(node);
    if (meters[label]) {
      meters[label].analyser = tap;
    }
  }
  pumpLevel();
  return destination.stream;
}

function currentCaptureKey() {
  return `${captureMode()}|${micDeviceEl?.value || ""}|${meetingSource()}`;
}

function releaseCaptureGraph({ closeContext = true } = {}) {
  cancelAnimationFrame(levelRaf);
  levelRaf = 0;
  disconnectGraph();
  stopMonitor();
  stopOwnedStreams();
  mediaStream = null;
  captureGraphKey = "";
  resetMeters();
  if (closeContext) {
    keepAliveGain = null;
    audioContext?.close().catch(() => {});
    audioContext = null;
  }
}

function setCaptureControlsEnabled(enabled) {
  if (captureModeEl) {
    captureModeEl.disabled = !enabled;
  }
  if (micDeviceEl) {
    micDeviceEl.disabled = !enabled;
  }
  if (meetingSourceEl) {
    meetingSourceEl.disabled = !enabled;
  }
}

async function refreshAudioHint() {
  if (!audioHintEl) {
    return;
  }
  const loopback = await findLoopbackDevice().catch(() => null);
  const status = await window.interviewApp?.mediaStatus?.().catch(() => null);
  const parts = [];
  if (wantsMeeting()) {
    parts.push("Meeting audio hears Zoom/Meet through headphones or speakers.");
    if (status?.screen === "denied") {
      parts.push(
        "macOS Screen Recording is denied — enable Electron (and Terminal/Cursor if you launched from there), then fully quit and relaunch.",
      );
      window.interviewApp?.openScreenSettings?.();
    } else if (status?.screen === "not-determined") {
      parts.push("macOS will ask for Screen Recording the first time you capture meeting audio.");
    }
    if (loopback) {
      parts.push(`Loopback device available: ${loopback.label}. Pick it under Meeting source if system audio is silent.`);
    }
  } else {
    parts.push("Microphone only. Switch to meeting audio if the interviewer is in Zoom/Meet with headphones.");
  }
  audioHintEl.textContent = parts.join(" ");
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

function showPanel(name) {
  introEl.hidden = name !== "idle";
  roomEl.hidden = name !== "room";
  if (doneEl) {
    doneEl.hidden = name !== "done";
  }
  if (captureCardEl) {
    captureCardEl.hidden = name === "done";
  }
}

function handleServerEvent(event) {
  const type = event.type;
  if (type === "status") {
    const state = event.state ?? "idle";
    if (state === "listening") {
      setStatus("ok", "Listening");
      setLive(true, event.detail || captureLiveLabel());
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
      setLive(false, event.detail || "Audio idle");
    }
    return;
  }

  if (type === "session_start") {
    showPanel("room");
    return;
  }

  if (type === "session_summary") {
    showPanel("done");
    setStatus("ok", "Session ended");
    return;
  }

  if (type === "turn_stats") {
    return;
  }

  if (type === "error") {
    showError(event.message || "The interview stream reported an error.");
    setStatus("down", "Stream error");
  }
}

function readLevel(analyser) {
  const samples = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(samples);
  let sum = 0;
  let peak = 0;
  for (const sample of samples) {
    sum += sample * sample;
    peak = Math.max(peak, Math.abs(sample));
  }
  return { rms: Math.sqrt(sum / samples.length), peak };
}

function levelBand({ rms, peak }) {
  if (peak >= 0.98 || rms >= 0.35) {
    return "clip";
  }
  if (rms < 0.008) {
    return "silent";
  }
  if (rms < 0.04) {
    return "quiet";
  }
  if (rms < 0.22) {
    return "good";
  }
  return "loud";
}

function levelLabel(band) {
  if (band === "quiet") {
    return "Too quiet";
  }
  if (band === "loud") {
    return "Hot";
  }
  if (band === "clip") {
    return "Clipping";
  }
  if (band === "good") {
    return "Good";
  }
  return "Silent";
}

function weakBand(band) {
  return band === "silent" || band === "quiet";
}

function updateLevelHint() {
  if (!levelHintEl) {
    return;
  }
  const live = capturing || previewing || Boolean(mediaStream);
  if (!live) {
    levelHintEl.textContent = LEVEL_HINT_IDLE;
    return;
  }
  const micBand = meters.mic.row?.hidden ? null : meters.mic.row?.dataset.band;
  const meetingBand = meters.meeting.row?.hidden ? null : meters.meeting.row?.dataset.band;
  const bands = [micBand, meetingBand].filter(Boolean);
  if (bands.includes("clip")) {
    levelHintEl.textContent = "Lower gain — clipping will distort the transcript.";
    return;
  }
  if (micBand && meetingBand && weakBand(meetingBand) && !weakBand(micBand)) {
    levelHintEl.textContent =
      "Mic is live, meeting is silent. Grant system audio or pick a loopback device.";
    return;
  }
  if (micBand && meetingBand && weakBand(micBand) && !weakBand(meetingBand)) {
    levelHintEl.textContent =
      "Meeting is live, mic is silent. Check the microphone or switch to meeting only.";
    return;
  }
  if (bands.includes("quiet") && !bands.includes("good") && !bands.includes("loud")) {
    levelHintEl.textContent = "Too quiet for Whisper. Speak, play the meeting, or raise gain.";
    return;
  }
  if (bands.length && bands.every((band) => band === "silent")) {
    levelHintEl.textContent = wantsMeeting()
      ? "No signal yet. Play the meeting or pick a loopback device."
      : "No signal yet. Speak into the microphone.";
    return;
  }
  if (bands.includes("loud")) {
    levelHintEl.textContent = "Hot but usable. Lower gain if it starts clipping.";
    return;
  }
  levelHintEl.textContent = "Good level. Listen when you are ready.";
}

function paintMeter(meter, reading) {
  const live = Boolean(meter.analyser);
  const rms = live ? reading.rms : 0;
  meter.displayed = meter.displayed * 0.55 + rms * 0.45;
  const db = 20 * Math.log10(Math.max(meter.displayed, 1e-6));
  const width = live ? Math.min(100, Math.max(3, ((db + 48) / 48) * 100)) : 3;
  if (meter.fill) {
    meter.fill.style.width = `${width}%`;
  }
  const band = live ? levelBand({ rms: meter.displayed, peak: reading.peak }) : "silent";
  if (meter.row) {
    meter.row.dataset.band = band;
  }
  if (meter.status) {
    meter.status.textContent = live ? levelLabel(band) : "Silent";
  }
  return band;
}

function pumpLevel() {
  for (const meter of Object.values(meters)) {
    if (!meter.analyser) {
      paintMeter(meter, { rms: 0, peak: 0 });
      continue;
    }
    paintMeter(meter, readLevel(meter.analyser));
  }
  updateLevelHint();
  if (Object.values(meters).some((meter) => meter.analyser)) {
    levelRaf = requestAnimationFrame(pumpLevel);
  }
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
    showPanel("room");
    startBtn.disabled = true;
    return true;
  }

  showError("");
  try {
    socket = await openSocket();
  } catch {
    reportListening(false);
    showError(`Could not connect to ${wsUrl}.`);
    setStatus("down", "WebSocket down");
    startBtn.disabled = false;
    return false;
  }

  bindSocket(socket);
  sessionActive = true;
  showPanel("room");
  startBtn.disabled = true;
  return true;
}

async function ensureCapture() {
  if (mediaStream && captureGraphKey === currentCaptureKey()) {
    await ensureAudioContext();
    return true;
  }
  await ensureAudioContext();
  releaseCaptureGraph({ closeContext: false });
  try {
    const labeled = [];
    if (wantsMic()) {
      labeled.push({ label: "mic", stream: await captureMicrophone() });
    }
    if (wantsMeeting()) {
      labeled.push({ label: "meeting", stream: await captureMeetingAudio() });
    }
    const live = labeled.filter((item) => item.stream?.getAudioTracks().length);
    if (!live.length) {
      throw new Error("No audio tracks were captured.");
    }
    mediaStream = buildCaptureGraph(live);
    monitorMix(mediaStream);
    captureGraphKey = currentCaptureKey();
    await populateMicDevices();
    await populateMeetingSources();
    await refreshAudioHint();
    updateCalibrateUi();
    return true;
  } catch (error) {
    releaseCaptureGraph();
    reportListening(false);
    showError(error?.message || "Could not start audio capture.");
    setStatus("down", "Audio blocked");
    startBtn.disabled = false;
    updateCalibrateUi();
    return false;
  }
}

async function warmMicMeter() {
  if (!wantsMic() || capturing || previewing || listenBusy || mediaStream) {
    return;
  }
  try {
    await ensureAudioContext();
    const stream = await captureMicrophone();
    mediaStream = buildCaptureGraph([{ label: "mic", stream }]);
    monitorMix(mediaStream);
    captureGraphKey = `mic-warm|${micDeviceEl?.value || ""}`;
    updateCalibrateUi();
  } catch {
    releaseCaptureGraph({ closeContext: false });
  }
}

async function ensureSession() {
  if (!(await ensureSocket())) {
    return false;
  }
  return ensureCapture();
}

function updateCalibrateUi() {
  syncLevelRows();
  applyGain();
  if (!testInputBtn) {
    return;
  }
  if (capturing || (sessionActive && mediaStream)) {
    testInputBtn.hidden = true;
    testInputBtn.disabled = true;
    return;
  }
  testInputBtn.hidden = false;
  testInputBtn.disabled = listenBusy;
  testInputBtn.textContent = previewing ? "Stop test" : "Test input";
}

async function startPreview() {
  if (capturing || listenBusy || previewing) {
    return;
  }
  void ensureAudioContext();
  listenBusy = true;
  updateCalibrateUi();
  try {
    showError("");
    await ensureAudioContext();
    const ready = await ensureCapture();
    if (!ready) {
      return;
    }
    previewing = true;
    setLive(false, "Testing input · not sending to STT");
    setStatus("ok", "Testing input");
  } finally {
    listenBusy = false;
    updateCalibrateUi();
  }
}

async function stopPreview() {
  if (capturing || listenBusy || !previewing) {
    return;
  }
  previewing = false;
  if (!sessionActive) {
    releaseCaptureGraph({ closeContext: false });
    setLive(false, captureIdleLabel());
    warmMicMeter().catch(() => {});
  }
  updateCalibrateUi();
}

async function togglePreview() {
  if (previewing) {
    await stopPreview();
    return;
  }
  await startPreview();
}

async function startCapture() {
  if (capturing || listenBusy) {
    return;
  }
  void ensureAudioContext();
  listenBusy = true;
  startBtn.disabled = true;
  updateCalibrateUi();
  try {
    await ensureAudioContext();
    const ready = await ensureSession();
    if (!ready) {
      return;
    }
    startRecorder();
    capturing = true;
    previewing = false;
    setCaptureControlsEnabled(false);
    showPanel("room");
    setRoomControls();
    setLive(true, captureLiveLabel());
    setStatus("ok", "Listening");
    startTimer();
    reportListening(true);
  } finally {
    listenBusy = false;
    updateCalibrateUi();
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
  setCaptureControlsEnabled(true);
  setRoomControls();
  stopTimer();
  reportListening(false);
  sendJson({ type: "prime" });
  updateCalibrateUi();

  try {
    await stopRecorder(activeRecorder);
    await audioQueue;
    sendJson({ type: "stop" });
    setLive(false, "Paused · drafting on live page");
    setStatus("checking", "Drafting on live page");
  } finally {
    listenBusy = false;
    updateCalibrateUi();
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
  setStatus("ok", "Session ended");
}

async function teardown(notifyServer) {
  const activeRecorder = recorder;
  recorder = null;
  capturing = false;
  previewing = false;
  sessionActive = false;
  listenBusy = true;
  setRoomControls();
  reportListening(false);

  await stopRecorder(activeRecorder);
  await audioQueue;
  if (notifyServer) {
    sendJson({ type: "stop" });
  }

  releaseCaptureGraph();
  stopTimer();
  setCaptureControlsEnabled(true);

  if (socket) {
    const current = socket;
    socket = null;
    if (current.readyState === WebSocket.OPEN || current.readyState === WebSocket.CONNECTING) {
      current.close();
    }
  }

  setLive(false, captureIdleLabel());
  showPanel("idle");
  startBtn.disabled = statusEl.dataset.state === "down";
  listenBusy = false;
  updateCalibrateUi();
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
  if (sessionActive || previewing) {
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

for (const button of document.querySelectorAll("#open-live, #open-live-room, #open-live-done")) {
  button.addEventListener("click", openLivePage);
}

document.getElementById("new-session")?.addEventListener("click", () => {
  startCapture().catch(() => {
    showError("Could not start listening.");
  });
});

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

function syncCaptureFields() {
  if (micDeviceFieldEl) {
    micDeviceFieldEl.hidden = !wantsMic();
  }
  if (meetingSourceFieldEl) {
    meetingSourceFieldEl.hidden = !wantsMeeting();
  }
  syncLevelRows();
}

function onCapturePrefChange() {
  saveCapturePrefs();
  syncCaptureFields();
  refreshAudioHint().catch(() => {});
  const shouldResumePreview = previewing && !capturing;
  if (!capturing && !listenBusy) {
    previewing = false;
    releaseCaptureGraph({ closeContext: false });
    updateCalibrateUi();
    if (shouldResumePreview) {
      startPreview().catch(() => {
        showError("Could not restart the input test.");
      });
    } else {
      warmMicMeter().catch(() => {});
    }
  }
}

const savedPrefs = loadCapturePrefs();
if (captureModeEl && (savedPrefs.mode === "mic" || savedPrefs.mode === "meeting" || savedPrefs.mode === "both")) {
  captureModeEl.value = savedPrefs.mode;
}
if (gainEl && typeof savedPrefs.gain === "number") {
  gainEl.value = String(savedPrefs.gain);
}
applyGain();
syncCaptureFields();
updateCalibrateUi();

captureModeEl?.addEventListener("change", onCapturePrefChange);
micDeviceEl?.addEventListener("change", onCapturePrefChange);
meetingSourceEl?.addEventListener("change", onCapturePrefChange);
gainEl?.addEventListener("input", () => {
  applyGain();
  saveCapturePrefs();
});
testInputBtn?.addEventListener("click", () => {
  togglePreview().catch(() => {
    showError("Could not test the audio input.");
  });
});
navigator.mediaDevices?.addEventListener?.("devicechange", () => {
  Promise.all([populateMicDevices(), populateMeetingSources()])
    .then(refreshAudioHint)
    .catch(() => {});
});
Promise.all([populateMicDevices(), populateMeetingSources()])
  .then(() => {
    if (savedPrefs.micDeviceId && micDeviceEl) {
      micDeviceEl.value = savedPrefs.micDeviceId;
    }
    if (savedPrefs.meetingSource && meetingSourceEl) {
      const exists = [...meetingSourceEl.options].some(
        (option) => option.value === savedPrefs.meetingSource,
      );
      if (exists) {
        meetingSourceEl.value = savedPrefs.meetingSource;
      }
    }
    return refreshAudioHint();
  })
  .then(() => warmMicMeter())
  .catch(() => {});

document.addEventListener("click", () => {
  ensureAudioContext();
});

reportListening(false);
connect();
setInterval(connect, 8000);
