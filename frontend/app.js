const API_BASE = "REPLACE_WITH_FUNCTION_APP_URL/api";
const FUNCTION_KEY = "REPLACE_WITH_FUNCTION_KEY"; // function-level auth key

const EXAMPLE_QUESTIONS = [
  "What is RAG?",
  "List some common RAG architectures.",
  "Provide reasons why someone would use RAG.",
  "What are some common use cases for RAG?",
];

const log = document.getElementById("log");
const emptyState = document.getElementById("empty-state");
const examplesEl = document.getElementById("examples");
const input = document.getElementById("question");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");
const citationsPanel = document.getElementById("citations-panel");
const closeCitations = document.getElementById("close-citations");

let history = [];

EXAMPLE_QUESTIONS.forEach((q) => {
  const card = document.createElement("div");
  card.className = "example-card";
  card.textContent = q;
  card.addEventListener("click", () => { input.value = q; sendMessage(); });
  examplesEl.appendChild(card);
});

closeCitations.addEventListener("click", () => citationsPanel.classList.remove("open"));

function showCitation(source) {
  document.getElementById("citation-source").textContent = source;
  document.getElementById("citation-text").textContent =
    "Open the source document in your data store to view full content for: " + source;
  citationsPanel.classList.add("open");
}

function addTurn(role, text, sources, isLoading) {
  emptyState.style.display = "none";
  const turn = document.createElement("div");
  turn.className = `turn ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble" + (isLoading ? " loading" : "");
  bubble.textContent = text;
  turn.appendChild(bubble);

  if (sources && sources.length) {
    const wrap = document.createElement("div");
    wrap.className = "citations";
    sources.forEach((s) => {
      const pill = document.createElement("span");
      pill.className = "citation-pill";
      pill.textContent = s;
      pill.addEventListener("click", () => showCitation(s));
      wrap.appendChild(pill);
    });
    const container = document.createElement("div");
    container.appendChild(bubble);
    container.appendChild(wrap);
    turn.innerHTML = "";
    turn.appendChild(container);
  }

  log.appendChild(turn);
  log.scrollTop = log.scrollHeight;
  return turn;
}

async function sendMessage() {
  const question = input.value.trim();
  if (!question) return;
  input.value = "";
  sendBtn.disabled = true;
  addTurn("user", question);
  const loadingTurn = addTurn("assistant", "Thinking...", null, true);

  try {
    const resp = await fetch(`${API_BASE}/chat?code=${FUNCTION_KEY}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history }),
    });
    const data = await resp.json();
    loadingTurn.remove();

    if (data.error) {
      addTurn("assistant", `Error: ${data.error}`);
    } else {
      addTurn("assistant", data.answer, data.sources);
      history.push({ role: "user", content: question });
      history.push({ role: "assistant", content: data.answer });
    }
  } catch (err) {
    loadingTurn.remove();
    addTurn("assistant", `Request failed: ${err}`);
  } finally {
    sendBtn.disabled = false;
  }
}

sendBtn.addEventListener("click", sendMessage);
input.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

clearBtn.addEventListener("click", () => {
  history = [];
  log.innerHTML = "";
  log.appendChild(emptyState);
  emptyState.style.display = "block";
  citationsPanel.classList.remove("open");
});
