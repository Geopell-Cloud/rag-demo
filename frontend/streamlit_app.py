"""Streamlit frontend for the RAG chat app.

Calls the Function App's POST /api/chat endpoint server-side, so the
function key never reaches the browser.

To run locally:
    pip install -r requirements.txt
    export API_BASE="https://<function-app>.azurewebsites.net/api"
    export FUNCTION_KEY="<function key>"
    streamlit run streamlit_app.py
"""
import os

import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE", "").rstrip("/")
FUNCTION_KEY = os.environ.get("FUNCTION_KEY", "")

EXAMPLE_QUESTIONS = [
    "What is the Geopell Cloud DevOps, Cloud & AI Engineering Training?",
    "What is RAG?",
    "Please provide information on the duration and schedule of Geopell Cloud DevOps, Cloud & AI Training.",
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
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if not API_BASE or not FUNCTION_KEY:
    st.error("API_BASE and FUNCTION_KEY environment variables must be set.")
    st.stop()

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
            timeout=200,  # agent runs make several model/search calls
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
                st.markdown(f"{n}. `{step['tool']}({args})` → {step['result']}")
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

# A prompt comes from the chat box or from an example-question button
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
