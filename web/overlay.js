const statusEl = document.getElementById("status");
const questionEl = document.getElementById("question");
const pointsEl = document.getElementById("points");
const ACCESS_TOKEN = new URLSearchParams(location.search).get("token") || "";
const wsUrl = (() => {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/ws/live", `${protocol}//${location.host}`);
  if (ACCESS_TOKEN) {
    url.searchParams.set("token", ACCESS_TOKEN);
  }
  return url.toString();
})();

let socket = null;
let reconnectTimer = 0;
let lastQuestion = "";

function setStatus(state, label) {
  statusEl.dataset.state = state;
  statusEl.textContent = label;
}

function renderPoints(question, points, drafting) {
  if (question) {
    lastQuestion = question;
  }
  const text = (question || lastQuestion || "").trim();
  questionEl.textContent = text || "Waiting for a question";
  questionEl.classList.toggle("is-empty", !text);
  const bullets = points?.bullets || [];
  if (!bullets.length) {
    pointsEl.classList.add("is-empty");
    pointsEl.textContent = drafting ? "Drafting…" : "Listen, then glance here.";
    return;
  }
  pointsEl.classList.remove("is-empty");
  pointsEl.replaceChildren();
  const list = document.createElement("ul");
  for (const item of bullets) {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  }
  pointsEl.append(list);
  if (points.example) {
    const box = document.createElement("div");
    box.className = "example";
    const label = document.createElement("span");
    label.textContent = "Example";
    const body = document.createElement("p");
    body.textContent = points.example;
    box.append(label, body);
    pointsEl.append(box);
  }
}

function extractPoints(text) {
  const cleaned = String(text || "")
    .replace(/```[\s\S]*?```/g, "\n")
    .trim();
  if (!cleaned) {
    return null;
  }
  const bullets = cleaned
    .split(/\n+/)
    .map((line) => line.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "").replace(/[*_`#]/g, "").trim())
    .filter((line) => line.length > 12)
    .slice(0, 5);
  return bullets.length ? { bullets, example: "" } : null;
}

function apply(event) {
  const type = event.type;
  if (type === "snapshot" || type === "qa" || type === "answer") {
    const question = event.question || lastQuestion;
    const answer = event.answer || event.text || "";
    const points = event.talking_points || extractPoints(answer);
    renderPoints(question, points, false);
    setStatus("ok", event.listening ? "Listening" : "Ready");
    return;
  }
  if (type === "partial_question" || type === "question") {
    renderPoints(event.text || "", null, type === "question");
    setStatus("checking", type === "question" ? "Drafting" : "Hearing");
    return;
  }
  if (type === "answer_delta") {
    renderPoints(event.text || lastQuestion, event.talking_points || extractPoints(event.text), true);
    setStatus("checking", "Drafting");
    return;
  }
  if (type === "status") {
    if (event.state === "listening") {
      setStatus("ok", "Listening");
    } else if (event.state === "thinking") {
      setStatus("checking", "Drafting");
    } else if (event.state === "idle") {
      setStatus("checking", "Waiting");
    }
    return;
  }
  if (type === "error") {
    setStatus("down", "Error");
  }
  if (type === "session_start") {
    lastQuestion = "";
    renderPoints("", null, false);
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
      apply(JSON.parse(message.data));
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
  socket.addEventListener("error", () => socket?.close());
}

connect();
