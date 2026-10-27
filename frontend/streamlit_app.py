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
import hashlib
import os
import re
import time

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
      [data-testid="stMarkdownContainer"] h3, [data-testid="stCaptionContainer"] {
        text-align: center;
      }
      [data-testid="stColumn"] p {
        word-break: break-word;
        white-space: normal;
      }
      [data-testid="stColumn"] button {
        height: 70px;

      /* Sidebar: let long file names wrap instead of overflowing */
      [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
        overflow-wrap: anywhere; word-break: break-word;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if not API_BASE or not FUNCTION_KEY:
    st.error("API_BASE and FUNCTION_KEY environment variables must be set.")
    st.stop()

# ---------------- Sidebar: upload, list, and remove documents ----------------
ALLOWED_TYPES = ["txt", "pdf", "docx"]
MAX_UPLOAD_MB = 200
INDEX_WAIT_SECONDS = 300   # how long the UI waits for indexing before saying it continues in the background
POLL_SECONDS = 3


def _json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def _md_escape(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+!|~<>])", r"\\\1", text)


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


def wait_for_indexing(blob: str) -> dict:
    """Poll the backend until the file is indexed, fails, or the wait runs out."""
    deadline = time.time() + INDEX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            resp = requests.get(f"{API_BASE}/status", params={"code": FUNCTION_KEY, "blob": blob}, timeout=30)
            info = _json_or_none(resp) or {}
        except requests.RequestException:
            info = {}
        if info.get("status") in ("indexed", "unchanged", "failed", "missing"):
            return info
        time.sleep(POLL_SECONDS)
    return {"status": "timeout"}


def remove_document(name: str) -> dict:
    """Delete the document's chunks from the index and its blob from storage."""
    try:
        resp = requests.delete(f"{API_BASE}/sources", params={"code": FUNCTION_KEY, "name": name}, timeout=120)
    except requests.RequestException as exc:
        return {"error": f"Could not reach the backend: {exc}"}
    body = _json_or_none(resp) or {}
    if resp.status_code != 200:
        return {"error": body.get("error", f"Backend returned HTTP {resp.status_code}")}
    return body


@st.cache_data(ttl=20, show_spinner=False)
def list_indexed_sources(api_base: str, key: str) -> list[dict] | None:
    """Returns (sources, error). Errors are shown in the sidebar instead of hidden."""
    try:
        resp = requests.get(f"{api_base}/sources", params={"code": key}, timeout=30)
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"
    body = _json_or_none(resp)
    if body is None:
        return None, f"HTTP {resp.status_code}, not JSON: {resp.text[:150]}"
    if resp.status_code != 200 or "error" in body:
        return None, f"HTTP {resp.status_code}: {body.get('error', 'unexpected response')}"
    return body.get("sources", []), None


def describe_indexing(name: str, info: dict) -> tuple[str, str]:
    """(streamlit message kind, text) for the result of an upload."""
    state = info.get("status")
    if state == "indexed":
        chunks = info.get("chunks")
        detail = f" ({chunks} chunks)" if chunks else ""
        return "success", f"{name} is indexed{detail}. You can ask about it now."
    if state == "unchanged":
        return "info", f"{name} was already indexed with identical content, so nothing changed."
    if state == "failed":
        return "error", f"Indexing {name} failed: {info.get('error') or 'unknown error'}"
    if state == "missing":
        return "error", f"{name} disappeared from storage before it could be indexed."
    return "info", (f"{name} is uploaded and still being indexed in the background. "
                    "Large files take longer. Click Refresh list to check.")


with st.sidebar:
    st.header("Upload documents")
    st.caption(f"PDF, DOCX, or TXT, up to {MAX_UPLOAD_MB} MB. Small files are ready within seconds; large files take longer to index.")

    flash = st.session_state.pop("flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])

    uploaded = st.file_uploader("Choose a file", type=ALLOWED_TYPES, label_visibility="collapsed", key="uploader")

    if st.button("Upload and Index Document", use_container_width=True, disabled=uploaded is None):
        data = uploaded.getvalue()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            st.error(f"{uploaded.name} is larger than {MAX_UPLOAD_MB} MB.")
        else:
            with st.status(f"Processing {uploaded.name}", expanded=True) as progress:
                st.write("Uploading to storage...")
                result = upload_document(uploaded.name, data)
                if "error" in result:
                    outcome = ("error", result["error"])
                    progress.update(label="Upload failed", state="error")
                else:
                    st.write("Extracting text, chunking, embedding, and indexing...")
                    outcome = describe_indexing(uploaded.name, wait_for_indexing(result["blob"]))
                    progress.update(label="Finished", state="complete")
            list_indexed_sources.clear()
            st.session_state.flash = outcome
            st.rerun()

    st.subheader("Indexed Documents")
    if st.button("Refresh list", use_container_width=True):
        list_indexed_sources.clear()

    sources_list, sources_error = list_indexed_sources(API_BASE, FUNCTION_KEY)
    if sources_error:
        st.caption(f"Could not load the document list. {sources_error}")
    elif not sources_list:
        st.caption("No documents indexed yet.")
    else:
        pending = st.session_state.get("confirm_delete")
        for src in sources_list:
            name = src["source"]
            uid = hashlib.md5(name.encode("utf-8")).hexdigest()[:10]
            left, right = st.columns([4, 1])
            left.markdown(f"**{_md_escape(name)}**")
            if right.button("**X**", key=f"rm_{uid}", use_container_width=True):
                st.session_state.confirm_delete = name
                pending = name
            if pending == name:
                st.warning("Remove this document from the index and delete its file from storage?")
                yes, no = st.columns(2)
                if yes.button("Confirm", key=f"yes_{uid}", type="primary", use_container_width=True):
                    with st.spinner(f"Removing {name}..."):
                        removed = remove_document(name)
                    list_indexed_sources.clear()
                    st.session_state.confirm_delete = None
                    if "error" in removed:
                        st.session_state.flash = ("error", removed["error"])
                    else:
                        gone = "its file was deleted from storage" if removed.get("blob_deleted") else "its file was already gone from storage"
                        st.session_state.flash = (
                            "success", f"Removed {name}: {removed['chunks_removed']} chunks deleted from the index, and {gone}.")
                    st.rerun()
                if no.button("Cancel", key=f"no_{uid}", use_container_width=True):
                    st.session_state.confirm_delete = None
                    st.rerun()

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
