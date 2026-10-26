"""Streamlit frontend for the RAG chat app.

Calls the Function App's POST /api/chat endpoint server-side, so the
function key never reaches the browser.

To run locally:
    pip install -r requirements.txt
    export API_BASE="https://<function-app>.azurewebsites.net/api"
    export FUNCTION_KEY="<function key>"
    streamlit run streamlit_app.py
"""
import base64
import os

import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE", "").rstrip("/")
FUNCTION_KEY = os.environ.get("FUNCTION_KEY", "")

EXAMPLE_QUESTIONS = [
    "What is the Geopell Cloud DevOps, Cloud & AI Engineering Training?",
    "What is RAG?",
    "Please provide information on the duration and schedule for this DevOps Training.",
    "Do you need previous experience to participate in DevOps Training?",
    "What are some of the things I can learn from the training?",
    "How do I register for the training?",
    "Before joining DevOps Training, what are the requirements?",
    "Describe the DevOps lifecycle.",
    "What is DevOps?",
    "What are some common use cases for RAG?",
]

st.set_page_config(page_title="RAG Demo", page_icon="💬", layout="centered")

st.markdown(
    """
    <style>
      .app-header {
        background: #123f58; color: white; padding: 15px 20px; height: 70px;
        border-radius: 6px; font-size: 1.5rem; font-weight: 600;
      }
      .app-header .dot {
        display: inline-block; width: 10px; height: 10px; margin-right: 10px;
        border-radius: 50%; background: #4cd964;
      }

      /* ---- Example-question buttons only (widget keys: example_0, example_1, ...) ---- */

      /* Make the two columns in each example row stretch to the same height */
      div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-example_"]) {
        align-items: stretch !important;
      }
      div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-example_"]) > div[data-testid="stColumn"],
      div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-example_"]) > div[data-testid="column"] {
        display: flex; flex-direction: column;
      }
      div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-example_"]) > div > div[data-testid="stVerticalBlock"] {
        flex: 1 1 auto;
      }

      /* Pass the full height down to the button itself */
      div[class*="st-key-example_"],
      div[class*="st-key-example_"] .stButton {
        height: 100%;
      }
      div[class*="st-key-example_"] button {
        height: 100%; min-height: 3rem;
      }

      /* Wrap long labels instead of clipping or overflowing */
      div[class*="st-key-example_"] button,
      div[class*="st-key-example_"] button p {
        white-space: normal; word-break: break-word; overflow-wrap: anywhere;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if not API_BASE or not FUNCTION_KEY:
    st.error("API_BASE and FUNCTION_KEY environment variables must be set.")
    st.stop()

# ---------------- Sidebar: upload and index documents ----------------
ALLOWED_TYPES = ["txt", "pdf", "docx"]
MAX_UPLOAD_MB = 200


def _json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def upload_document(name: str, data: bytes) -> dict:
    """Get a write link, PUT the file straight to Blob Storage, then queue indexing."""
    try:
        resp = requests.post(f"{API_BASE}/upload-url", params={"code": FUNCTION_KEY},
                             json={"filename": name}, timeout=60)
    except requests.RequestException as exc:
        return {"error": f"Could not reach the backend: {exc}"}
    link = _json_or_none(resp) or {}
    if resp.status_code != 200:
        return {"error": link.get("error", f"Backend returned HTTP {resp.status_code}")}

    try:
        put = requests.put(
            link["upload_url"],
            data=data,
            headers={"x-ms-blob-type": "BlockBlob", "Content-Type": "application/octet-stream"},
            timeout=900,
        )
        put.raise_for_status()
    except requests.RequestException as exc:
        return {"error": f"Upload to storage failed: {exc}"}

    try:
        resp = requests.post(f"{API_BASE}/index", params={"code": FUNCTION_KEY},
                             json={"blob": link["blob"]}, timeout=60)
    except requests.RequestException as exc:
        return {"error": f"Could not start indexing: {exc}"}
    if resp.status_code != 202:
        return {"error": (_json_or_none(resp) or {}).get("error", f"Backend returned HTTP {resp.status_code}")}
    return {"blob": link["blob"]}


@st.cache_data(ttl=20, show_spinner=False)
def list_indexed_sources(api_base: str, key: str) -> list[dict] | None:
    try:
        resp = requests.get(f"{api_base}/sources", params={"code": key}, timeout=30)
        return resp.json().get("sources", [])
    except (requests.RequestException, ValueError):
        return None


with st.sidebar:
    st.header("Upload documents")
    st.caption(f"PDF, DOCX, or TXT, up to {MAX_UPLOAD_MB} MB. Small files are indexed quickly; large files take longer to index.")
    uploaded = st.file_uploader("Choose a file", type=ALLOWED_TYPES, label_visibility="collapsed", key="uploader")

    if st.button("Upload and Index Document", use_container_width=True, disabled=uploaded is None):
        data = uploaded.getvalue()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            st.error(f"{uploaded.name} is larger than {MAX_UPLOAD_MB} MB.")
        else:
            with st.spinner(f"Uploading {uploaded.name}..."):
                result = upload_document(uploaded.name, data)
            if "error" in result:
                st.error(result["error"])
            else:
                st.success(f"Uploaded {result['blob']}. Indexing has started.")

    st.subheader("Indexed Documents")
    if st.button("Refresh list", use_container_width=True):
        list_indexed_sources.clear()
    sources_list = list_indexed_sources(API_BASE, FUNCTION_KEY)
    if sources_list is None:
        st.caption("Could not load the document list.")
    elif not sources_list:
        st.caption("No documents indexed yet.")
    else:
        for src in sources_list:
            st.markdown(f"- `{src['source']}` ({src['chunks']} chunks)")

if "messages" not in st.session_state:
    st.session_state.messages = []  # [{"role", "content", "sources", "steps"}]
if "pending" not in st.session_state:
    st.session_state.pending = None


def ask(question: str) -> tuple[str, list[str], list[dict]]:
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
    try:
        resp = requests.post(
            f"{API_BASE}/chat",
            params={"code": FUNCTION_KEY},
            json={"question": question, "history": history},
            timeout=240,  # Function App requests are cut off at ~230s
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return f"Request failed: {exc}", [], []
    if "error" in data:
        return f"Error: {data['error']}", [], []
    return data["answer"], data.get("sources", []), data.get("steps", [])


def show_details(sources: list[str], steps: list[dict]) -> None:
    """Render the agent's work (tool calls) and the sources it used."""
    if steps:
        with st.expander(f"Agent steps ({len(steps)})"):
            for n, step in enumerate(steps, 1):
                args = ", ".join(f"{k}={v!r}" for k, v in step["args"].items())
                took = f" ({step['seconds']}s)" if "seconds" in step else ""
                st.markdown(f"{n}. `{step['tool']}({args})` → {step['result']}{took}")
    if sources:
        with st.expander("Sources"):
            for src in sources:
                st.markdown(f"- `{src}`")


# Header with clear-chat button
head, clear = st.columns([5, 1])
head.markdown('<div class="app-header"><span class="dot"></span>RAG Demo Chat App</div>', unsafe_allow_html=True)
if clear.button("Clear chat", use_container_width=True):
    st.session_state.messages = []
    st.session_state.pending = None
    st.rerun()

# Prompt comes from chat box or from example-question button
prompt = st.chat_input("Type a new question...")
if st.session_state.pending:
    prompt = st.session_state.pending
    st.session_state.pending = None

# Empty state with example questions
if not st.session_state.messages and not prompt:
    st.markdown("<h3>Ask a question or try one of these examples</h3>", unsafe_allow_html=True)
    st.caption("An agent searches your documents, decides what to look up and how often, and answers with citations.")
    for row_start in range(0, len(EXAMPLE_QUESTIONS), 2):
        cols = st.columns(2)
        for offset, col in enumerate(cols):
            i = row_start + offset
            if i < len(EXAMPLE_QUESTIONS):
                if col.button(EXAMPLE_QUESTIONS[i], key=f"example_{i}", use_container_width=True):
                    st.session_state.pending = EXAMPLE_QUESTIONS[i]
                    st.rerun()

# Existing conversation
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.write(m["content"])
        show_details(m.get("sources", []), m.get("steps", []))

# New turn
if prompt:
    with st.chat_message("user"):
        st.write(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Agent is planning, retrieving, evaluating, and refining..."):
            answer, sources, steps = ask(prompt)
        st.write(answer)
        show_details(sources, steps)
    st.session_state.messages.append({"role": "user", "content": prompt, "sources": [], "steps": []})
    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources, "steps": steps})
