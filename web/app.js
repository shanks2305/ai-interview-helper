const statusEl = document.getElementById("status");
const subtitleEl = document.getElementById("subtitle");
const questionEl = document.getElementById("question");
const answerEl = document.getElementById("answer");
const logEl = document.getElementById("log");
const historyEl = document.getElementById("history");
const liveStageEl = document.getElementById("live-stage");
const dashboardEl = document.getElementById("dashboard");
const recapBodyEl = document.getElementById("recap-body");
const recapSummaryEl = document.getElementById("recap-summary");
const dashChartEl = document.getElementById("dash-chart");

const askCardEl = document.getElementById("ask-card");
const askFormEl = document.getElementById("ask-form");
const askInputEl = document.getElementById("ask-input");
const askSubmitEl = document.getElementById("ask-submit");
const askErrorEl = document.getElementById("ask-error");
const askShortcutEl = document.getElementById("ask-shortcut");
const copyAnswerBtn = document.getElementById("copy-answer");

const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/live`;
const isMac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
const PLACEHOLDER_QUESTION = "No question yet";
const PLACEHOLDER_ANSWER =
  "Start listening in the desktop app, or paste a question that was not asked out loud.";

let socket = null;
let reconnectTimer = 0;
let latestSession = null;
let currentView = "idle";
let current = { question: "", answer: "" };

function setStatus(state, label) {
  statusEl.dataset.state = state;
  statusEl.textContent = label;
}

function setView(view) {
  currentView = view;
  document.body.dataset.view = view;
  const ended = view === "ended";
  liveStageEl.hidden = false;
  dashboardEl.hidden = !ended;
  subtitleEl.textContent = ended ? "Session recap" : "Live copilot";
  if (ended && latestSession) {
    requestAnimationFrame(() => {
      renderChart(dashChartEl, latestSession.turns ?? [], { height: 240, emptyEl: null });
    });
    dashboardEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function showHistory() {
  if (historyEl) {
    historyEl.hidden = logEl.children.length === 0;
  }
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

function formatElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = String(Math.floor(total / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function prependPair(question, answer) {
  const item = document.createElement("li");
  item.className = "pair";
  const q = document.createElement("p");
  q.className = "pair-q";
  q.textContent = question;
  const a = document.createElement("p");
  a.className = "pair-a";
  a.textContent = answer;
  item.append(q, a);
  item.addEventListener("click", () => {
    item.classList.toggle("is-open");
  });
  logEl.prepend(item);
  showHistory();
}

function archiveCurrent() {
  if (current.question && current.answer) {
    prependPair(current.question, current.answer);
  }
  current = { question: "", answer: "" };
}

function updateCopyButton() {
  if (!copyAnswerBtn) {
    return;
  }
  const ready = Boolean(current.answer) && current.answer !== "Drafting…";
  copyAnswerBtn.hidden = !ready;
  copyAnswerBtn.textContent = "Copy answer";
}

function setQuestionText(text) {
  const value = text || "";
  questionEl.classList.toggle("is-placeholder", !value);
  questionEl.textContent = value || PLACEHOLDER_QUESTION;
}

function setAnswerText(text) {
  const value = text || "";
  answerEl.classList.toggle("is-placeholder", !value);
  answerEl.textContent = value || PLACEHOLDER_ANSWER;
  updateCopyButton();
}

function setCurrent(question, answer) {
  current = { question: question || "", answer: answer || "" };
  setQuestionText(current.question);
  setAnswerText(current.answer);
}

function renderChart(canvas, turns, { height = 220, emptyEl } = {}) {
  const ctx = canvas?.getContext("2d");
  if (!ctx) {
    return;
  }
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 640;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!turns.length) {
    if (emptyEl) {
      emptyEl.hidden = false;
    }
    return;
  }
  if (emptyEl) {
    emptyEl.hidden = true;
  }

  const pad = { top: 16, right: 12, bottom: 28, left: 36 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxMs = Math.max(
    1000,
    ...turns.map((turn) => Math.max(turn.listen_ms || 0, turn.total_ms || 0)),
  );
  const groupW = plotW / turns.length;

  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.fillStyle = "#8b93a7";
  ctx.font = "11px sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(formatMs(maxMs * (1 - i / 4)), 4, y + 4);
  }

  turns.forEach((turn, index) => {
    const x = pad.left + index * groupW;
    const barW = Math.min(22, groupW * 0.28);
    const gap = 6;
    const qH = ((turn.listen_ms || 0) / maxMs) * plotH;
    const aH = ((turn.total_ms || 0) / maxMs) * plotH;
    const qX = x + groupW / 2 - barW - gap / 2;
    const aX = x + groupW / 2 + gap / 2;
    ctx.fillStyle = "#7c9cff";
    ctx.fillRect(qX, pad.top + plotH - qH, barW, Math.max(2, qH));
    ctx.fillStyle = "#6ee7c5";
    ctx.fillRect(aX, pad.top + plotH - aH, barW, Math.max(2, aH));
    ctx.fillStyle = "#8b93a7";
    ctx.textAlign = "center";
    ctx.fillText(`Q${turn.index || index + 1}`, x + groupW / 2, height - 8);
    ctx.textAlign = "start";
  });
}

function renderRecap(session) {
  const turns = session?.turns ?? [];
  const stats = session?.stats ?? {};
  recapBodyEl.replaceChildren();
  for (const turn of turns) {
    const row = document.createElement("tr");
    const cells = [
      String(turn.index ?? ""),
      turn.question || "",
      formatMs(turn.listen_ms),
      formatMs(turn.stt_ms),
      formatMs(turn.llm_ms),
      formatMs(turn.llm_first_ms),
      formatMs(turn.total_ms),
    ];
    cells.forEach((value, index) => {
      const cell = document.createElement(index === 0 ? "th" : "td");
      cell.textContent = value;
      row.append(cell);
    });
    recapBodyEl.append(row);
  }
  recapSummaryEl.textContent = turns.length
    ? `${stats.questions || 0} questions in ${formatMs(stats.duration_ms)}. ` +
      `Average listen ${formatMs(stats.avg_listen_ms)}, average answer ${formatMs(stats.avg_answer_ms)} ` +
      `(fastest ${formatMs(stats.fastest_answer_ms)}, slowest ${formatMs(stats.slowest_answer_ms)}).`
    : "No questions were recorded in this session.";

  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = value;
    }
  };
  set("dash-count", String(stats.questions ?? turns.length ?? 0));
  set("dash-duration", stats.duration_ms ? formatElapsed(stats.duration_ms) : "—");
  set("dash-avg-q", stats.questions ? formatMs(stats.avg_listen_ms) : "—");
  set("dash-avg-a", stats.questions ? formatMs(stats.avg_answer_ms) : "—");
  set("dash-avg-stt", stats.questions ? formatMs(stats.avg_stt_ms) : "—");
  set("dash-first", stats.questions ? formatMs(stats.avg_llm_first_ms) : "—");
  set("dash-fast", stats.questions ? formatMs(stats.fastest_answer_ms) : "—");
  set("dash-slow", stats.questions ? formatMs(stats.slowest_answer_ms) : "—");
}

function applySession(session, { ended = false } = {}) {
  latestSession = session || null;
  if (!session) {
    setView("idle");
    return;
  }

  if (session.active) {
    setView("live");
    return;
  }

  if (ended || session.active === false) {
    renderRecap(session);
    setView("ended");
  }
}

function applySnapshot(event) {
  const pairs = event.pairs ?? [];
  const liveQuestion = event.question || "";
  const liveAnswer = event.answer || "";
  logEl.replaceChildren();

  const older = pairs.filter((pair) => {
    return pair.question !== liveQuestion || pair.answer !== liveAnswer;
  });
  for (const pair of [...older].reverse()) {
    prependPair(pair.question, pair.answer);
  }
  setCurrent(liveQuestion, liveAnswer);
  showHistory();
  applySession(event.session);
  setStatus(event.listening ? "ok" : "checking", event.listening ? "Listening" : "Waiting");
}

function handleEvent(event) {
  const type = event.type;
  if (type === "snapshot") {
    applySnapshot(event);
    return;
  }
  if (type === "session_start") {
    logEl.replaceChildren();
    showHistory();
    setCurrent("", "");
    applySession(event.session);
    setStatus("ok", "Session started");
    return;
  }
  if (type === "turn_stats") {
    latestSession = event.session || latestSession;
    return;
  }
  if (type === "session_summary") {
    applySession(event.session, { ended: true });
    setStatus("ok", "Dashboard ready");
    return;
  }
  if (type === "status") {
    const state = event.state;
    if (state === "idle") {
      setStatus("checking", "Waiting");
    } else if (state === "thinking") {
      setStatus("checking", "Drafting");
    } else if (state === "ready") {
      setStatus("ok", "Answer ready");
    } else {
      setStatus("ok", "Listening");
    }
    return;
  }
  if (type === "partial_question") {
    if (!current.question || current.answer) {
      archiveCurrent();
    }
    current.question = event.text || "…";
    current.answer = "";
    setQuestionText(current.question);
    setAnswerText("");
    return;
  }
  if (type === "question") {
    archiveCurrent();
    current.question = event.text || "";
    current.answer = event.valid === false ? "Skipped — not a question." : "";
    setQuestionText(current.question);
    setAnswerText(current.answer || (event.valid === false ? "" : "Drafting…"));
    if (event.valid === false) {
      prependPair(current.question, current.answer);
      current = { question: "", answer: "" };
      updateCopyButton();
    }
    return;
  }
  if (type === "answer_delta") {
    current.answer = event.text || "";
    setAnswerText(current.answer);
    setStatus("checking", "Drafting");
    return;
  }
  if (type === "answer") {
    current.answer = event.text || "";
    setAnswerText(current.answer);
    setStatus("ok", "Answer ready");
    return;
  }
  if (type === "qa") {
    if (event.valid === false) {
      setStatus("ok", "Skipped");
      return;
    }
    current.question = event.question || current.question;
    current.answer = event.answer || "";
    setQuestionText(current.question);
    setAnswerText(current.answer);
    setStatus("ok", "Answer ready");
    return;
  }
  if (type === "error") {
    setAnswerText(event.message || "The interview stream reported an error.");
    setStatus("down", "Error");
  }
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  socket = new WebSocket(wsUrl);
  socket.addEventListener("open", () => setStatus("ok", "Connected"));
  socket.addEventListener("message", (message) => {
    try {
      handleEvent(JSON.parse(message.data));
    } catch {
      setStatus("down", "Bad event");
    }
  });
  socket.addEventListener("close", () => {
    socket = null;
    setStatus("down", "Reconnecting");
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connect, 1500);
  });
  socket.addEventListener("error", () => {
    socket?.close();
  });
}

connect();

function setAskError(message) {
  if (!askErrorEl) {
    return;
  }
  askErrorEl.hidden = !message;
  askErrorEl.textContent = message || "";
}

async function submitTypedQuestion() {
  const text = askInputEl?.value.trim() || "";
  if (!text) {
    setAskError("Paste a question first.");
    return;
  }
  setAskError("");
  if (askSubmitEl) {
    askSubmitEl.disabled = true;
  }
  try {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "ask", text }));
    } else {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Could not send the question.");
      }
    }
    askInputEl.value = "";
    if (askCardEl) {
      askCardEl.open = false;
    }
    setView("live");
    setCurrent(text, "Drafting…");
    setStatus("checking", "Drafting");
  } catch (error) {
    setAskError(error instanceof Error ? error.message : "Could not send the question.");
  } finally {
    if (askSubmitEl) {
      askSubmitEl.disabled = false;
    }
  }
}

askFormEl?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitTypedQuestion();
});

askInputEl?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    submitTypedQuestion();
  }
});

if (askShortcutEl) {
  askShortcutEl.textContent = isMac ? "⌘ Enter to send" : "Ctrl + Enter to send";
}

askCardEl?.addEventListener("toggle", () => {
  if (askCardEl.open) {
    askInputEl?.focus();
  }
});

copyAnswerBtn?.addEventListener("click", async () => {
  if (!current.answer) {
    return;
  }
  try {
    await navigator.clipboard.writeText(current.answer);
    copyAnswerBtn.textContent = "Copied";
    window.setTimeout(() => {
      copyAnswerBtn.textContent = "Copy answer";
    }, 1200);
  } catch {
    copyAnswerBtn.textContent = "Copy failed";
  }
});
window.addEventListener("resize", () => {
  if (currentView === "ended" && latestSession) {
    renderChart(dashChartEl, latestSession.turns ?? [], { height: 240, emptyEl: null });
  }
});

if (window.interviewApp) {
  document.body.classList.add("in-electron");
}
