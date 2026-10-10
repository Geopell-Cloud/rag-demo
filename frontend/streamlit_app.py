"""Streamlit frontend for the RAG chat app.

Calls the Function App's POST /api/chat endpoint server-side, so the
function key never reaches the browser.

To run locally:
    pip install -r requirements-frontend.txt
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
    "What is RAG?",
    "List some common RAG architectures.",
    "Provide reasons why someone would use RAG.",
    "What are some common use cases for RAG?",
    "What is DevOps?",
    "Describe the DevOps lifecycle.",
]

st.set_page_config(page_title="RAG Demo", page_icon="💬", layout="centered")

st.markdown(
    """
    <style>
      .app-header {
        background: #123f58; color: white; padding: 12px 20px;
        border-radius: 6px; font-size: 1.1rem; font-weight: 600;
      }
      .app-header .dot {
        display: inline-block; width: 10px; height: 10px; margin-right: 10px;
        border-radius: 50%; background: #4cd964;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

if not API_BASE or not FUNCTION_KEY:
    st.error("API_BASE and FUNCTION_KEY environment variables must be set.")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []  # [{"role", "content", "sources"}]
if "pending" not in st.session_state:
    st.session_state.pending = None


def ask(question: str) -> tuple[str, list[str]]:
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
    try:
        resp = requests.post(
            f"{API_BASE}/chat",
            params={"code": FUNCTION_KEY},
            json={"question": question, "history": history},
            timeout=120,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return f"Request failed: {exc}", []
    if "error" in data:
        return f"Error: {data['error']}", []
    return data["answer"], data.get("sources", [])


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
    st.markdown("### Ask a question or try one of these examples")
    st.caption("Answers are from the documents that are indexed from a data source, with citations.")
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        if cols[i % 2].button(q, key=f"ex{i}", use_container_width=True):
            st.session_state.pending = q
            st.rerun()

# Existing conversation
for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.write(m["content"])
        if m.get("sources"):
            with st.expander("Sources"):
                for s in m["sources"]:
                    st.markdown(f"- `{s}`")

# New turn
if prompt:
    with st.chat_message("user"):
        st.write(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            answer, sources = ask(prompt)
        st.write(answer)
        if sources:
            with st.expander("Sources"):
                for s in sources:
                    st.markdown(f"- `{s}`")
    st.session_state.messages.append({"role": "user", "content": prompt, "sources": []})
    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": sources})
