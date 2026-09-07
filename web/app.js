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
const dashTitleEl = document.getElementById("dash-title");
const exportActionsEl = document.getElementById("export-actions");
const exportMdEl = document.getElementById("export-md");
const exportPdfEl = document.getElementById("export-pdf");
const libraryEl = document.getElementById("library");
const libraryListEl = document.getElementById("library-list");
const libraryEmptyEl = document.getElementById("library-empty");
const librarySearchEl = document.getElementById("library-search");
const libraryCountEl = document.getElementById("library-count");
const libraryBtn = document.getElementById("library-btn");

const askCardEl = document.getElementById("ask-card");
const askFormEl = document.getElementById("ask-form");
const askInputEl = document.getElementById("ask-input");
const askSubmitEl = document.getElementById("ask-submit");
const askErrorEl = document.getElementById("ask-error");
const askShortcutEl = document.getElementById("ask-shortcut");
const copyAnswerBtn = document.getElementById("copy-answer");
const answerTagEl = document.getElementById("answer-tag");
const modeBarEl = document.getElementById("mode-bar");
const modeToolbarEl = document.getElementById("mode-toolbar");
const viewBarEl = document.getElementById("view-bar");
const contextCardEl = document.getElementById("context-card");
const contextFormEl = document.getElementById("context-form");
const contextRoleEl = document.getElementById("context-role");
const contextCompanyEl = document.getElementById("context-company");
const contextJdEl = document.getElementById("context-jd");
const contextResumeEl = document.getElementById("context-resume");
const contextHintEl = document.getElementById("context-hint");
const contextSaveEl = document.getElementById("context-save");
const contextClearEl = document.getElementById("context-clear");
const contextErrorEl = document.getElementById("context-error");
const newSessionBtn = document.getElementById("new-session");

const ACCESS_TOKEN = new URLSearchParams(location.search).get("token") || "";
const wsUrl = (() => {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/ws/live", `${protocol}//${location.host}`);
  if (ACCESS_TOKEN) {
    url.searchParams.set("token", ACCESS_TOKEN);
  }
  return url.toString();
})();
const isMac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
const PLACEHOLDER_QUESTION = "No question yet";
const PLACEHOLDER_ANSWER =
  "Start listening in the desktop app, or paste a question that was not asked out loud.";
const SKIPPED_ANSWER = "Skipped — not a question.";
const SKIP_LABELS = {
  filler: "Skipped — filler.",
  greeting: "Skipped — not a question.",
  incomplete: "Skipped — false start.",
  overlap: "Skipped — overlapping speech.",
  duplicate: "Already answered.",
  not_a_question: "Skipped — not a question.",
};

let socket = null;
let reconnectTimer = 0;
let latestSession = null;
let currentView = "idle";
let current = { question: "", answer: "", source: "", answer_mode: "", talking_points: null };
let libraryItems = [];
let libraryQuery = "";
let libraryTimer = 0;
let previousView = "idle";
let viewingSessionId = null;
let libraryLoadId = 0;
let libraryOpenId = 0;
let wakeLock = null;
let wakeLockGen = 0;
const COPY_LABEL = "Copy talking points";
const CONTEXT_STORAGE_KEY = "ai-interview-context";
const MODE_STORAGE_KEY = "ai-interview-answer-mode";
const VIEW_STORAGE_KEY = "ai-interview-answer-view";
const CONTEXT_HINT = "Paste the JD and resume once — used for every answer";
const DEFAULT_ANSWER_MODE = "spoken_45";
const DEFAULT_ANSWER_MODES = [
  { id: "spoken_15", label: "15s", title: "15s spoken", markdown: false },
  { id: "spoken_45", label: "45s", title: "45s spoken", markdown: false },
  { id: "star", label: "STAR", title: "STAR", markdown: true },
  { id: "system_design", label: "Design", title: "System-design outline", markdown: true },
  { id: "bullets", label: "Bullets", title: "Glanceable bullets", markdown: true },
];
let contextDirty = false;
let contextSeeded = false;
let answerModes = DEFAULT_ANSWER_MODES;
let currentAnswerMode = DEFAULT_ANSWER_MODE;
let modeSeeded = false;
let answerLayout = "points";

function emptyContext() {
  return { role: "", company: "", job_description: "", resume: "" };
}

function hasContext(payload) {
  const ctx = payload || {};
  return Boolean(
    (ctx.role || "").trim() ||
      (ctx.company || "").trim() ||
      (ctx.job_description || "").trim() ||
      (ctx.resume || "").trim()
  );
}

function contextLabel(payload) {
  const role = (payload?.role || "").trim();
  const company = (payload?.company || "").trim();
  if (role && company) {
    return `${role} · ${company}`;
  }
  if (role || company) {
    return role || company;
  }
  if (hasContext(payload)) {
    return "JD and resume saved";
  }
  return "";
}

function contextFromForm() {
  return {
    role: contextRoleEl?.value.trim() || "",
    company: contextCompanyEl?.value.trim() || "",
    job_description: contextJdEl?.value.trim() || "",
    resume: contextResumeEl?.value.trim() || "",
  };
}

function loadContextDraft() {
  try {
    const raw = localStorage.getItem(CONTEXT_STORAGE_KEY);
    if (!raw) {
      return emptyContext();
    }
    const parsed = JSON.parse(raw);
    return {
      role: parsed.role || "",
      company: parsed.company || "",
      job_description: parsed.job_description || "",
      resume: parsed.resume || "",
    };
  } catch {
    return emptyContext();
  }
}

function saveContextDraft(payload) {
  try {
    localStorage.setItem(CONTEXT_STORAGE_KEY, JSON.stringify(payload || contextFromForm()));
  } catch {
    // Private mode / quota.
  }
}

function fillContextForm(payload, { markSaved = false } = {}) {
  const ctx = payload || emptyContext();
  if (contextRoleEl) contextRoleEl.value = ctx.role || "";
  if (contextCompanyEl) contextCompanyEl.value = ctx.company || "";
  if (contextJdEl) contextJdEl.value = ctx.job_description || "";
  if (contextResumeEl) contextResumeEl.value = ctx.resume || "";
  if (markSaved) {
    contextDirty = false;
  }
  updateContextHint(ctx);
}

function updateContextHint(payload) {
  if (!contextHintEl) {
    return;
  }
  const ctx = payload || contextFromForm();
  const label = contextLabel(ctx);
  contextHintEl.textContent = label || CONTEXT_HINT;
}

function setContextError(message) {
  if (!contextErrorEl) {
    return;
  }
  contextErrorEl.hidden = !message;
  contextErrorEl.textContent = message || "";
}

function contextFromSession(session, fallback) {
  if (session?.context && typeof session.context === "object") {
    return {
      role: session.context.role || "",
      company: session.context.company || "",
      job_description: session.context.job_description || "",
      resume: session.context.resume || "",
    };
  }
  return fallback || emptyContext();
}

function applyContext(payload, { force = false, markSaved = false } = {}) {
  if (contextDirty && !force) {
    updateContextHint(contextFromForm());
    return;
  }
  fillContextForm(payload, { markSaved });
}

function normalizeAnswerMode(value) {
  const key = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[-\s]/g, "_");
  if (answerModes.some((mode) => mode.id === key)) {
    return key;
  }
  const aliases = {
    "15": "spoken_15",
    "15s": "spoken_15",
    "45": "spoken_45",
    "45s": "spoken_45",
    spoken: "spoken_45",
    design: "system_design",
    outline: "system_design",
    glanceable: "bullets",
    bullet: "bullets",
    behavioral: "star",
  };
  return aliases[key] || DEFAULT_ANSWER_MODE;
}

function modeSpec(id) {
  const value = normalizeAnswerMode(id);
  return (
    answerModes.find((mode) => mode.id === value) ||
    answerModes.find((mode) => mode.id === DEFAULT_ANSWER_MODE) ||
    answerModes[0]
  );
}

function modeUsesMarkdown(id) {
  return Boolean(modeSpec(id)?.markdown);
}

function loadStoredAnswerMode() {
  try {
    return normalizeAnswerMode(localStorage.getItem(MODE_STORAGE_KEY) || DEFAULT_ANSWER_MODE);
  } catch {
    return DEFAULT_ANSWER_MODE;
  }
}

function saveAnswerMode(mode) {
  try {
    localStorage.setItem(MODE_STORAGE_KEY, normalizeAnswerMode(mode));
  } catch {
    // Private mode / quota.
  }
}

function updateAnswerTag() {
  if (!answerTagEl) {
    return;
  }
  const spec = modeSpec(current.answer_mode || currentAnswerMode);
  if (answerLayout === "points" && canShowPoints(current.answer)) {
    answerTagEl.textContent = "Talking points";
    return;
  }
  answerTagEl.textContent = `Answer · ${spec.label}`;
}

function applyAnswerMode(mode) {
  currentAnswerMode = normalizeAnswerMode(mode);
  saveAnswerMode(currentAnswerMode);
  if (modeBarEl) {
    for (const btn of modeBarEl.querySelectorAll(".mode-btn")) {
      btn.setAttribute("aria-checked", btn.dataset.mode === currentAnswerMode ? "true" : "false");
    }
  }
  updateAnswerTag();
}

function renderModeBar() {
  if (!modeBarEl) {
    return;
  }
  modeBarEl.replaceChildren();
  for (const mode of answerModes) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mode-btn";
    btn.dataset.mode = mode.id;
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", mode.id === currentAnswerMode ? "true" : "false");
    btn.title = mode.title || mode.label;
    btn.textContent = mode.label;
    btn.addEventListener("click", () => {
      submitAnswerMode(mode.id);
    });
    modeBarEl.append(btn);
  }
}

async function submitAnswerMode(mode, { quiet = false } = {}) {
  applyAnswerMode(mode);
  try {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "set_answer_mode", mode: currentAnswerMode }));
      return;
    }
    const response = await fetch(withToken("/api/session/answer-mode"), {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mode: currentAnswerMode }),
    });
    if (!response.ok && !quiet) {
      throw new Error("Could not save answer mode.");
    }
  } catch {
    if (!quiet) {
      setStatus("down", "Could not save answer mode");
    }
  }
}

function seedAnswerMode(event) {
  if (Array.isArray(event.answer_modes) && event.answer_modes.length) {
    answerModes = event.answer_modes;
    renderModeBar();
  }
  const server = normalizeAnswerMode(event.answer_mode || event.session?.answer_mode);
  const local = loadStoredAnswerMode();
  if (server && server !== DEFAULT_ANSWER_MODE) {
    applyAnswerMode(server);
    modeSeeded = true;
    return;
  }
  if (!modeSeeded && local !== server) {
    applyAnswerMode(local);
    modeSeeded = true;
    submitAnswerMode(local, { quiet: true });
    return;
  }
  applyAnswerMode(server || local);
  modeSeeded = true;
}

function setStatus(state, label) {
  statusEl.dataset.state = state;
  statusEl.textContent = label;
}

function setView(view) {
  if (view !== "library") {
    previousView = view;
  }
  currentView = view;
  document.body.dataset.view = view;
  const ended = view === "ended";
  const library = view === "library";
  liveStageEl.hidden = library;
  dashboardEl.hidden = !ended;
  if (modeToolbarEl) {
    modeToolbarEl.hidden = library;
  } else if (modeBarEl) {
    modeBarEl.hidden = library;
  }
  if (libraryEl) {
    libraryEl.hidden = !library;
  }
  if (libraryBtn) {
    libraryBtn.setAttribute("aria-pressed", library ? "true" : "false");
    libraryBtn.textContent = library ? "Back" : "Library";
  }
  subtitleEl.textContent = library ? "Session library" : ended ? "Session recap" : "Live copilot";
  syncWakeLock();
  syncLibraryUrl();
  if (library) {
    loadLibrary();
    return;
  }
  if (ended && latestSession) {
    requestAnimationFrame(() => {
      renderChart(dashChartEl, latestSession.turns ?? [], { height: 240, emptyEl: null });
    });
    dashboardEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function toggleLibrary() {
  if (currentView === "library") {
    const fallback = latestSession?.active ? "live" : latestSession ? "ended" : "idle";
    setView(previousView === "library" ? fallback : previousView);
    return;
  }
  previousView = currentView;
  setView("library");
  librarySearchEl?.focus();
}

function syncLibraryUrl() {
  const url = new URL(location.href);
  if (libraryQuery) {
    url.searchParams.set("q", libraryQuery);
  } else {
    url.searchParams.delete("q");
  }
  if (viewingSessionId && currentView !== "live" && currentView !== "library") {
    url.searchParams.set("session", viewingSessionId);
  } else {
    url.searchParams.delete("session");
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (`${location.pathname}${location.search}${location.hash}` !== next) {
    history.replaceState(null, "", next);
  }
}

async function requestWakeLock() {
  if (!("wakeLock" in navigator) || document.visibilityState !== "visible") {
    return;
  }
  if (currentView === "ended" || currentView === "library") {
    return;
  }
  const gen = ++wakeLockGen;
  try {
    const lock = await navigator.wakeLock.request("screen");
    if (gen !== wakeLockGen || currentView === "ended" || currentView === "library") {
      lock.release().catch(() => {});
      return;
    }
    wakeLock = lock;
    wakeLock.addEventListener("release", () => {
      if (wakeLock === lock) {
        wakeLock = null;
      }
    });
  } catch {
    // Unsupported in this context (common on iOS Safari tabs).
  }
}

function releaseWakeLock() {
  wakeLockGen += 1;
  if (!wakeLock) {
    return;
  }
  wakeLock.release().catch(() => {});
  wakeLock = null;
}

function syncWakeLock() {
  if (currentView === "ended" || currentView === "library" || document.visibilityState !== "visible") {
    releaseWakeLock();
    return;
  }
  requestWakeLock();
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

function formatWhen(iso) {
  if (!iso) {
    return "";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function withToken(url) {
  if (!ACCESS_TOKEN) {
    return url;
  }
  const next = new URL(url, location.origin);
  next.searchParams.set("token", ACCESS_TOKEN);
  return `${next.pathname}${next.search}`;
}

function authHeaders(extra = {}) {
  if (!ACCESS_TOKEN) {
    return extra;
  }
  return { ...extra, "X-Interview-Token": ACCESS_TOKEN };
}

function bindExport(session) {
  const id = session?.id;
  if (!exportActionsEl || !exportMdEl || !exportPdfEl) {
    return;
  }
  if (!id) {
    exportActionsEl.hidden = true;
    return;
  }
  exportActionsEl.hidden = false;
  exportMdEl.href = withToken(`/api/sessions/${id}/export.md`);
  exportMdEl.setAttribute("download", "");
  exportPdfEl.href = withToken(`/api/sessions/${id}/print?autoprint=1`);
}

function prependPair(question, answer, source, mode) {
  const item = document.createElement("li");
  item.className = "pair";
  const q = document.createElement("p");
  q.className = "pair-q";
  q.textContent = question;
  const a = document.createElement("div");
  a.className = "pair-a";
  setRichText(a, answer, source, mode);
  item.append(q, a);
  item.tabIndex = 0;
  item.setAttribute("role", "button");
  item.setAttribute("aria-expanded", "false");
  item.addEventListener("click", () => {
    const open = item.classList.toggle("is-open");
    item.setAttribute("aria-expanded", open ? "true" : "false");
  });
  item.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    item.click();
  });
  logEl.prepend(item);
  showHistory();
}

function archiveCurrent() {
  if (current.question && current.answer && !isSkipAnswer(current.answer) && current.answer !== PLACEHOLDER_ANSWER) {
    prependPair(current.question, current.answer, current.source, current.answer_mode);
  }
  current = { question: "", answer: "", source: "", answer_mode: currentAnswerMode, talking_points: null };
}

function isSkipAnswer(text) {
  const value = text || "";
  return (
    value === "Drafting…" ||
    value.startsWith("Skipped") ||
    value.startsWith("Already answered")
  );
}

function readyStatusLabel(detail) {
  const text = (detail || "").toLowerCase();
  if (text.includes("already answered")) {
    return "Already answered";
  }
  if (
    text.includes("skip") ||
    text.includes("false start") ||
    text.includes("not a question") ||
    text.includes("filler") ||
    text.includes("overlap")
  ) {
    return "Skipped";
  }
  if (text.includes("no speech")) {
    return "No speech";
  }
  return "Answer ready";
}

function talkingPoints(text, payload) {
  const points = normalizeTalkingPoints(payload) || extractTalkingPoints(text);
  return formatTalkingPoints(points);
}

function normalizeTalkingPoints(payload) {
  const bullets = (payload?.bullets || [])
    .map((item) => String(item || "").trim())
    .filter(Boolean)
    .slice(0, 5);
  if (!bullets.length) {
    return null;
  }
  return { bullets, example: String(payload.example || "").trim() };
}

function formatTalkingPoints(points) {
  if (!points?.bullets?.length) {
    return "";
  }
  const lines = points.bullets.map((item) => `• ${item}`);
  if (points.example) {
    lines.push("", `Example: ${points.example}`);
  }
  return lines.join("\n");
}

function extractTalkingPoints(text) {
  const cleaned = String(text || "")
    .replace(/```[\s\S]*?```/g, "\n")
    .trim();
  if (!cleaned) {
    return null;
  }
  const items = fromStar(cleaned) || fromBullets(cleaned) || fromSentences(cleaned);
  if (!items.length) {
    return null;
  }
  const example = pickExample(items);
  const others = items.map((item) => item.text).filter((item) => item !== example);
  if (example && others.length >= 3) {
    return { bullets: others.slice(0, 5), example };
  }
  return { bullets: items.map((item) => item.text).slice(0, 5), example: "" };
}

function cleanPoint(text, label) {
  let value = String(text || "")
    .replace(/[*_`#]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[-—:]\s*/, "");
  if (label && value && !new RegExp(`^${label}\\b`, "i").test(value)) {
    value = `${label}: ${value}`;
  }
  if (value.length > 220) {
    value = `${value.slice(0, 219).trim()}…`;
  }
  return value;
}

function fromStar(text) {
  const found = [];
  for (const raw of text.split(/\n+/)) {
    const match = raw
      .replace(/^#{1,6}\s+/, "")
      .trim()
      .match(/^\*{0,2}(Situation|Task|Action|Result|Goal|Requirements|Design|Flow|Scale|Trade-?offs?)\*{0,2}\s*[:—-]\s*(.+)$/i);
    if (!match) {
      continue;
    }
    const label = canonicalLabel(match[1]);
    const value = cleanPoint(match[2], label);
    if (value) {
      found.push({ text: value, label });
    }
  }
  return found.length >= 2 ? found : null;
}

function fromBullets(text) {
  const found = [];
  for (const raw of text.split(/\n+/)) {
    const line = raw.replace(/^#{1,6}\s+/, "").trim();
    const bold = line.match(/^(?:(?:[-*+]|\d+[.)])\s+)?\*\*(.+?)\*\*\s*[—:-]\s*(.+)$/);
    if (bold) {
      const label = canonicalLabel(bold[1]);
      const value = cleanPoint(bold[2], label);
      if (value) {
        found.push({ text: value, label });
      }
      continue;
    }
    const match = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.+)$/);
    if (!match) {
      continue;
    }
    const value = cleanPoint(match[1], "");
    if (value) {
      found.push({ text: value, label: "" });
    }
  }
  return found.length >= 2 ? found : null;
}

function fromSentences(text) {
  const collapsed = text.replace(/^#{1,6}\s+/gm, "").replace(/\s+/g, " ").trim();
  const parts = collapsed
    .split(/(?<=[.!?])\s+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 12);
  const sentences = parts.length ? parts : collapsed ? [collapsed] : [];
  return sentences.slice(0, 8).map((item) => ({ text: cleanPoint(item, ""), label: "" })).filter((item) => item.text);
}

function canonicalLabel(value) {
  const key = String(value || "").toLowerCase().replace(/[^a-z]+/g, "");
  const names = {
    situation: "Situation",
    task: "Task",
    action: "Action",
    result: "Result",
    goal: "Goal",
    requirements: "Requirements",
    design: "Design",
    flow: "Flow",
    scale: "Scale",
    tradeoff: "Trade-off",
    tradeoffs: "Trade-offs",
  };
  return names[key] || String(value || "").trim();
}

function pickExample(items) {
  const hint =
    /\b(for example|e\.g\.|such as|say we|say you|in production|production|postgres|postgresql|redis|kafka|stripe|dynamo|s3|webhook|checkout|inventory|mutex|semaphore|queue|\bapi\b|\bdb\b)\b/i;
  let best = { score: 0, text: "" };
  items.forEach((item, index) => {
    let score = 0;
    if (hint.test(item.text)) {
      score += 3;
    }
    if (/^(action|design|flow)$/i.test(item.label)) {
      score += 4;
    }
    if (index > 0 && /\b[A-Z][a-zA-Z]+\b/.test(item.text)) {
      score += 1;
    }
    if (item.text.length > 90) {
      score += 1;
    }
    if (index === 0) {
      score -= 1;
    }
    if (score > best.score) {
      best = { score, text: item.text };
    }
  });
  return best.score > 0 ? best.text : "";
}

function loadAnswerLayout() {
  try {
    const stored = localStorage.getItem(VIEW_STORAGE_KEY);
    return stored === "full" ? "full" : "points";
  } catch {
    return "points";
  }
}

function saveAnswerLayout(layout) {
  answerLayout = layout === "full" ? "full" : "points";
  try {
    localStorage.setItem(VIEW_STORAGE_KEY, answerLayout);
  } catch {
    // Private mode / quota.
  }
  if (viewBarEl) {
    for (const btn of viewBarEl.querySelectorAll(".view-btn")) {
      btn.setAttribute("aria-checked", btn.dataset.layout === answerLayout ? "true" : "false");
    }
  }
  document.body.dataset.layout = answerLayout;
  updateAnswerTag();
  if (current.answer) {
    setAnswerText(current.answer);
  }
}

function canShowPoints(text) {
  return Boolean(text) && text !== "Drafting…" && text !== PLACEHOLDER_ANSWER && !isSkipAnswer(text);
}

function renderTalkingPoints(el, points) {
  el.classList.remove("is-markdown", "is-placeholder");
  el.classList.add("is-points");
  el.replaceChildren();
  const list = document.createElement("ul");
  list.className = "points-list";
  for (const item of points.bullets) {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  }
  el.append(list);
  if (points.example) {
    const box = document.createElement("div");
    box.className = "points-example";
    const label = document.createElement("span");
    label.className = "points-example-label";
    label.textContent = "Example";
    const body = document.createElement("p");
    body.className = "points-example-text";
    body.textContent = points.example;
    box.append(label, body);
    el.append(box);
  }
}

function updateCopyButton() {
  if (!copyAnswerBtn) {
    return;
  }
  const ready = canShowPoints(current.answer);
  copyAnswerBtn.hidden = !ready;
  copyAnswerBtn.textContent = COPY_LABEL;
  if (viewBarEl) {
    viewBarEl.hidden = !ready;
  }
}

function shouldRenderMarkdown(source, text, mode) {
  if (!text || text === "Drafting…" || text === PLACEHOLDER_ANSWER || isSkipAnswer(text)) {
    return false;
  }
  if (typeof window.renderMarkdown !== "function") {
    return false;
  }
  if (modeUsesMarkdown(mode || current.answer_mode || currentAnswerMode)) {
    return true;
  }
  return source === "typed";
}

function setRichText(el, text, source, mode) {
  const markdown = shouldRenderMarkdown(source, text, mode);
  el.classList.toggle("is-markdown", markdown);
  if (markdown) {
    window.renderMarkdown(el, text);
    return;
  }
  el.textContent = text;
}

function setQuestionText(text, { partial = false } = {}) {
  const value = text || "";
  questionEl.classList.toggle("is-placeholder", !value);
  questionEl.classList.toggle("is-partial", Boolean(partial) && Boolean(value));
  questionEl.textContent = value || PLACEHOLDER_QUESTION;
}

function setAnswerText(text) {
  const value = text || "";
  const skipped = Boolean(value) && isSkipAnswer(value) && value !== "Drafting…";
  answerEl.classList.toggle("is-placeholder", !value);
  answerEl.classList.toggle("is-skip", skipped);
  answerEl.classList.toggle("is-drafting", value === "Drafting…");
  if (!value) {
    answerEl.classList.remove("is-markdown", "is-points");
    answerEl.textContent = PLACEHOLDER_ANSWER;
    updateCopyButton();
    updateAnswerTag();
    return;
  }
  const points =
    normalizeTalkingPoints(current.talking_points) ||
    (canShowPoints(value) ? extractTalkingPoints(value) : null);
  if (answerLayout === "points" && canShowPoints(value) && points?.bullets?.length) {
    current.talking_points = points;
    renderTalkingPoints(answerEl, points);
    updateCopyButton();
    updateAnswerTag();
    return;
  }
  answerEl.classList.remove("is-points");
  setRichText(answerEl, value, current.source, current.answer_mode || currentAnswerMode);
  updateCopyButton();
  updateAnswerTag();
}

function setCurrent(question, answer, source, mode, talkingPointsPayload) {
  current = {
    question: question || "",
    answer: answer || "",
    source: source ?? current.source ?? "",
    answer_mode: normalizeAnswerMode(mode || current.answer_mode || currentAnswerMode),
    talking_points: normalizeTalkingPoints(talkingPointsPayload),
  };
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
  if (dashTitleEl) {
    dashTitleEl.textContent = session?.title || "How this session went";
  }
  bindExport(session);

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
  if (session?.id) {
    viewingSessionId = session.id;
  }
  if (!session) {
    viewingSessionId = null;
    bindExport(null);
    setView("idle");
    return;
  }

  if (session.active) {
    bindExport(null);
    setView("live");
    return;
  }

  if (ended || session.active === false) {
    renderRecap(session);
    setView("ended");
  }
}

function showStoredSession(session) {
  const turns = session?.turns ?? [];
  logEl.replaceChildren();
  const last = turns[turns.length - 1];
  const older = last ? turns.slice(0, -1) : turns;
  for (const turn of older) {
    prependPair(turn.question, turn.answer, turn.source, turn.answer_mode);
  }
  setCurrent(last?.question || "", last?.answer || "", last?.source || "", last?.answer_mode);
  showHistory();
  applySession(session, { ended: session && session.active === false });
  applyContext(contextFromSession(session), { markSaved: Boolean(session?.active) });
  applyAnswerMode(session?.answer_mode || last?.answer_mode || currentAnswerMode);
  renderLibrary();
}

function renderLibrary() {
  if (!libraryListEl || !libraryEmptyEl) {
    return;
  }
  libraryListEl.replaceChildren();
  libraryEmptyEl.hidden = libraryItems.length > 0;
  libraryEmptyEl.textContent = libraryQuery
    ? "No sessions match that search."
    : "No saved sessions yet. End an interview to file it here.";
  if (libraryCountEl) {
    if (!libraryItems.length) {
      libraryCountEl.textContent = "";
    } else if (libraryQuery) {
      libraryCountEl.textContent = libraryItems.length === 1 ? "1 match" : `${libraryItems.length} matches`;
    } else {
      libraryCountEl.textContent = libraryItems.length === 1 ? "1 session" : `${libraryItems.length} sessions`;
    }
  }
  const currentId = viewingSessionId || latestSession?.id;
  const matchLabel = {
    question: "Matched question",
    answer: "Matched answer",
    context: "Matched company",
  };
  for (const item of libraryItems) {
    const wrap = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-item";
    if (item.id === currentId) {
      button.classList.add("is-current");
    }
    const title = document.createElement("span");
    title.className = "library-title";
    title.textContent = item.title || "Empty session";
    const meta = document.createElement("span");
    meta.className = "library-meta";
    const when = formatWhen(item.ended_at || item.updated_at || item.started_at);
    const count = item.questions === 1 ? "1 question" : `${item.questions || 0} questions`;
    const duration = item.duration_ms ? formatElapsed(item.duration_ms) : "";
    const roleCompany = [item.role, item.company].filter(Boolean).join(" · ");
    const matched = libraryQuery ? matchLabel[item.match] || "" : "";
    meta.textContent = [count, duration, when, roleCompany, item.active ? "In progress" : matched]
      .filter(Boolean)
      .join(" · ");
    button.append(title, meta);
    if (item.preview) {
      const preview = document.createElement("span");
      preview.className = "library-preview";
      preview.textContent = item.preview;
      button.append(preview);
    }
    button.addEventListener("click", () => {
      openLibrarySession(item.id);
    });
    wrap.append(button);
    libraryListEl.append(wrap);
  }
}

async function loadLibrary(query = libraryQuery) {
  libraryQuery = query;
  const requestId = ++libraryLoadId;
  try {
    const params = new URLSearchParams();
    if (query) {
      params.set("q", query);
    }
    const response = await fetch(withToken(`/api/sessions${params.size ? `?${params}` : ""}`), {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error("Could not load sessions.");
    }
    const payload = await response.json();
    if (requestId !== libraryLoadId) {
      return;
    }
    libraryItems = payload.sessions || [];
  } catch {
    if (requestId !== libraryLoadId) {
      return;
    }
    libraryItems = [];
  }
  renderLibrary();
  syncLibraryUrl();
}

async function openLibrarySession(sessionId) {
  viewingSessionId = sessionId;
  const requestId = ++libraryOpenId;
  try {
    const response = await fetch(withToken(`/api/sessions/${sessionId}`), {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error("Could not open session.");
    }
    const session = await response.json();
    if (requestId !== libraryOpenId || viewingSessionId !== sessionId) {
      return;
    }
    showStoredSession(session);
    if (session.active) {
      setStatus("ok", "Session in progress");
    } else {
      setStatus("ok", "Saved recap");
    }
  } catch {
    if (requestId !== libraryOpenId || viewingSessionId !== sessionId) {
      return;
    }
    setStatus("down", "Could not open session");
  }
}

function applySnapshot(event) {
  const incoming = event.session;
  const liveTakeover = Boolean(event.listening);
  const keepStoredView =
    !liveTakeover &&
    (currentView === "library" ||
      (Boolean(viewingSessionId) && (!incoming?.id || incoming.id !== viewingSessionId)));
  if (keepStoredView) {
    seedContext(event);
    seedAnswerMode(event);
    setStatus(event.listening ? "ok" : "checking", event.listening ? "Listening" : "Waiting");
    loadLibrary();
    return;
  }
  const pairs = event.pairs ?? [];
  const liveQuestion = event.question || "";
  const liveAnswer = event.answer || "";
  logEl.replaceChildren();

  const older = pairs.filter((pair) => {
    return pair.question !== liveQuestion || pair.answer !== liveAnswer;
  });
  for (const pair of [...older].reverse()) {
    prependPair(pair.question, pair.answer, pair.source, pair.answer_mode);
  }
  setCurrent(liveQuestion, liveAnswer, event.source || "", event.answer_mode, event.talking_points);
  showHistory();
  applySession(event.session);
  seedContext(event);
  seedAnswerMode(event);
  setStatus(event.listening ? "ok" : "checking", event.listening ? "Listening" : "Waiting");
  loadLibrary();
}

function handleEvent(event) {
  const type = event.type;
  if (currentView === "library" && ["partial_question", "question", "answer_delta", "answer", "qa"].includes(type)) {
    setView("live");
  }
  if (type === "snapshot") {
    applySnapshot(event);
    return;
  }
  if (type === "session_start") {
    logEl.replaceChildren();
    showHistory();
    setCurrent("", "", "", event.session?.answer_mode);
    applySession(event.session);
    applyContext(contextFromSession(event.session, event.context), { markSaved: true });
    applyAnswerMode(event.session?.answer_mode || event.answer_mode || currentAnswerMode);
    setStatus("ok", "Session started");
    loadLibrary();
    return;
  }
  if (type === "session_resume") {
    showStoredSession(event.session);
    applyContext(contextFromSession(event.session, event.context), { markSaved: true });
    applyAnswerMode(event.session?.answer_mode || event.answer_mode || currentAnswerMode);
    setStatus("ok", "Session resumed");
    return;
  }
  if (type === "context_updated") {
    const ctx = event.context || contextFromSession(event.session);
    applyContext(ctx, { force: true, markSaved: true });
    saveContextDraft(ctx);
    if (event.session) {
      latestSession = event.session;
    }
    setContextError("");
    return;
  }
  if (type === "answer_mode_updated") {
    applyAnswerMode(event.answer_mode || event.session?.answer_mode);
    if (event.session) {
      latestSession = event.session;
    }
    return;
  }
  if (type === "turn_stats") {
    latestSession = event.session || latestSession;
    if (currentView === "ended") {
      renderRecap(latestSession);
    } else {
      bindExport(null);
    }
    renderLibrary();
    return;
  }
  if (type === "session_summary") {
    applySession(event.session, { ended: true });
    setStatus("ok", "Dashboard ready");
    loadLibrary();
    return;
  }
  if (type === "status") {
    const state = event.state;
    if (state === "idle") {
      setStatus("checking", "Waiting");
    } else if (state === "thinking") {
      const detail = event.detail || "";
      setStatus("checking", /transcrib/i.test(detail) ? "Hearing question" : "Drafting");
    } else if (state === "ready") {
      setStatus("ok", readyStatusLabel(event.detail));
    } else {
      setStatus("ok", "Listening");
    }
    return;
  }
  if (type === "skip") {
    const label = event.detail || SKIP_LABELS[event.reason] || SKIPPED_ANSWER;
    setStatus(event.reason === "duplicate" ? "ok" : "checking", event.reason === "duplicate" ? "Already answered" : "Skipped");
    if (!current.answer || current.answer === "Drafting…" || isSkipAnswer(current.answer)) {
      current.question = event.text || current.question;
      current.answer = label;
      current.source = event.source || current.source || "spoken";
      current.answer_mode = normalizeAnswerMode(event.answer_mode || current.answer_mode || currentAnswerMode);
      current.talking_points = null;
      setQuestionText(current.question);
      setAnswerText(current.answer);
      updateAnswerTag();
    }
    return;
  }
  if (type === "partial_question") {
    if (!current.question || current.answer) {
      archiveCurrent();
    }
    current.question = event.text || "…";
    current.answer = "";
    current.source = event.source || "spoken";
    current.answer_mode = normalizeAnswerMode(event.answer_mode || currentAnswerMode);
    current.talking_points = null;
    setQuestionText(current.question, { partial: true });
    setAnswerText("");
    setStatus("checking", "Hearing question");
    return;
  }
  if (type === "question") {
    archiveCurrent();
    current.question = event.text || "";
    current.answer = event.valid === false ? SKIPPED_ANSWER : "";
    current.source = event.source || "spoken";
    current.answer_mode = normalizeAnswerMode(event.answer_mode || currentAnswerMode);
    current.talking_points = null;
    setQuestionText(current.question);
    setAnswerText(current.answer || (event.valid === false ? "" : "Drafting…"));
    updateAnswerTag();
    if (event.valid === false) {
      prependPair(current.question, current.answer, current.source, current.answer_mode);
      setCurrent("", "", "", currentAnswerMode);
      setQuestionText("");
      setAnswerText("");
      updateCopyButton();
      updateAnswerTag();
    }
    return;
  }
  if (type === "answer_delta") {
    current.answer = event.text || "";
    if (event.source) {
      current.source = event.source;
    }
    if (event.answer_mode) {
      current.answer_mode = normalizeAnswerMode(event.answer_mode);
    }
    current.talking_points = normalizeTalkingPoints(event.talking_points);
    setAnswerText(current.answer);
    setStatus("checking", "Drafting");
    return;
  }
  if (type === "answer") {
    current.answer = event.text || "";
    if (event.source) {
      current.source = event.source;
    }
    if (event.answer_mode) {
      current.answer_mode = normalizeAnswerMode(event.answer_mode);
    }
    current.talking_points = normalizeTalkingPoints(event.talking_points);
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
    if (event.source) {
      current.source = event.source;
    }
    if (event.answer_mode) {
      current.answer_mode = normalizeAnswerMode(event.answer_mode);
    }
    current.talking_points = normalizeTalkingPoints(event.talking_points);
    setQuestionText(current.question);
    setAnswerText(current.answer);
    setStatus("ok", "Answer ready");
    return;
  }
  if (type === "error") {
    const message = event.message || "The interview stream reported an error.";
    setStatus("down", "Error");
    if (!current.answer || current.answer === "Drafting…" || isSkipAnswer(current.answer)) {
      setAnswerText(message);
    }
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
  socket.addEventListener("close", (event) => {
    socket = null;
    if (event.code === 4401) {
      setStatus("down", ACCESS_TOKEN ? "Invalid access token" : "Need ?token= in the live URL");
      return;
    }
    setStatus("down", "Reconnecting");
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connect, 1500);
  });
  socket.addEventListener("error", () => {
    socket?.close();
  });
}

function seedContext(event) {
  const server = event.context || contextFromSession(event.session);
  if (hasContext(server)) {
    applyContext(server, { markSaved: true });
    contextSeeded = true;
    return;
  }
  if (contextDirty) {
    updateContextHint(contextFromForm());
    return;
  }
  const draft = hasContext(contextFromForm()) ? contextFromForm() : loadContextDraft();
  if (!hasContext(draft)) {
    if (!contextSeeded) {
      applyContext(emptyContext());
    }
    contextSeeded = true;
    return;
  }
  if (!contextSeeded) {
    fillContextForm(draft);
  }
  contextSeeded = true;
  submitContext({ quiet: true });
}

async function submitContext({ quiet = false } = {}) {
  const payload = contextFromForm();
  saveContextDraft(payload);
  setContextError("");
  if (contextSaveEl) {
    contextSaveEl.disabled = true;
  }
  try {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "set_context", ...payload }));
    } else {
      const response = await fetch(withToken("/api/session/context"), {
        method: "PUT",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Could not save context.");
      }
      const body = await response.json().catch(() => ({}));
      applyContext(body.context || payload, { force: true, markSaved: true });
    }
    contextDirty = false;
    updateContextHint(payload);
    if (contextCardEl && !quiet) {
      contextCardEl.open = false;
    }
  } catch (error) {
    if (!quiet) {
      setContextError(error instanceof Error ? error.message : "Could not save context.");
    }
  } finally {
    if (contextSaveEl) {
      contextSaveEl.disabled = false;
    }
  }
}

async function clearContext() {
  fillContextForm(emptyContext(), { markSaved: true });
  saveContextDraft(emptyContext());
  await submitContext({ quiet: true });
  setContextError("");
}

async function startNewSession() {
  if (newSessionBtn) {
    newSessionBtn.disabled = true;
  }
  try {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "new_session" }));
      return;
    }
    const response = await fetch(withToken("/api/session/new"), {
      method: "POST",
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error("Could not start a new session.");
    }
    const payload = await response.json();
    if (payload.session) {
      handleEvent({ type: "session_start", session: payload.session });
    }
  } catch {
    setStatus("down", "Could not start session");
  } finally {
    if (newSessionBtn) {
      newSessionBtn.disabled = false;
    }
  }
}

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
        headers: authHeaders({ "Content-Type": "application/json" }),
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
    setCurrent(text, "Drafting…", "typed", currentAnswerMode);
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
  askShortcutEl.replaceChildren();
  const first = document.createElement("kbd");
  first.textContent = isMac ? "⌘" : "Ctrl";
  const second = document.createElement("kbd");
  second.textContent = "Enter";
  askShortcutEl.append(first, second, document.createTextNode(" to send"));
}

askCardEl?.addEventListener("toggle", () => {
  if (askCardEl.open) {
    askInputEl?.focus();
  }
});

contextFormEl?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitContext();
});

contextClearEl?.addEventListener("click", () => {
  clearContext();
});

contextFormEl?.addEventListener("input", () => {
  contextDirty = true;
  saveContextDraft();
  updateContextHint(contextFromForm());
});

contextCardEl?.addEventListener("toggle", () => {
  if (contextCardEl.open) {
    contextRoleEl?.focus();
  }
});

newSessionBtn?.addEventListener("click", () => {
  startNewSession();
});

libraryBtn?.addEventListener("click", () => {
  toggleLibrary();
});

copyAnswerBtn?.addEventListener("click", async () => {
  const points = talkingPoints(current.answer, current.talking_points);
  if (!points) {
    return;
  }
  try {
    await navigator.clipboard.writeText(points);
    copyAnswerBtn.textContent = "Copied";
    window.setTimeout(() => {
      copyAnswerBtn.textContent = COPY_LABEL;
    }, 1200);
  } catch {
    copyAnswerBtn.textContent = "Copy failed";
  }
});

viewBarEl?.addEventListener("click", (event) => {
  const btn = event.target.closest(".view-btn");
  if (!btn?.dataset.layout) {
    return;
  }
  saveAnswerLayout(btn.dataset.layout);
});
document.addEventListener("visibilitychange", syncWakeLock);
window.addEventListener("resize", () => {
  if (currentView === "ended" && latestSession) {
    renderChart(dashChartEl, latestSession.turns ?? [], { height: 240, emptyEl: null });
  }
});

librarySearchEl?.addEventListener("input", () => {
  window.clearTimeout(libraryTimer);
  libraryTimer = window.setTimeout(() => {
    loadLibrary(librarySearchEl.value.trim());
  }, 200);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) {
    return;
  }
  const target = event.target;
  const tag = target && target.tagName ? target.tagName : "";
  if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) {
    return;
  }
  if (currentView === "live") {
    return;
  }
  event.preventDefault();
  if (currentView !== "library") {
    previousView = currentView;
    setView("library");
  }
  librarySearchEl?.focus();
  librarySearchEl?.select();
});

if (window.interviewApp) {
  document.body.classList.add("in-electron");
}

renderModeBar();
applyAnswerMode(loadStoredAnswerMode());
saveAnswerLayout(loadAnswerLayout());
const bootParams = new URLSearchParams(location.search);
const bootQuery = (bootParams.get("q") || "").trim();
const bootSession = (bootParams.get("session") || "").trim();
if (librarySearchEl && bootQuery) {
  librarySearchEl.value = bootQuery;
}
loadLibrary(bootQuery);
if (bootSession) {
  viewingSessionId = bootSession;
  openLibrarySession(bootSession);
} else if (bootQuery) {
  setView("library");
}
connect();
syncWakeLock();
