"""Agentic RAG: an LLM-driven tool-calling loop over Azure AI Search.

Instead of one fixed retrieve-then-answer pass, the model decides what to do:
it can list the available documents, run several searches with different
queries, narrow a search to one document, and only answers once it judges it
has enough evidence (or hits the step limit).
"""
import json
import os

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI

AZURE_SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
AZURE_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "rag-index")
AZURE_SEARCH_API_KEY = os.environ["AZURE_SEARCH_API_KEY"]
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_API_KEY = os.environ["AZURE_OPENAI_API_KEY"]
AZURE_OPENAI_CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.4-mini")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "5"))  # model calls that may use tools
MAX_CHUNK_CHARS = 1500  # keep tool results small so the loop stays cheap

search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX,
    credential=AzureKeyCredential(AZURE_SEARCH_API_KEY),
)

openai_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)

SYSTEM_PROMPT = """You are a research agent that answers questions using ONLY a private \
document knowledge base, which you reach through tools.

How to work:
1. Decide what you need. For greetings or small talk, answer directly without tools.
2. If the question is about what documents exist, call list_sources.
3. Otherwise call search_documents. Break multi-part or comparative questions into \
separate searches, rephrase and retry if results are weak, and use the `source` \
filter to dig deeper into one document once you know which one matters.
4. Stop searching as soon as you have enough evidence. Do not repeat identical searches.
5. Answer only from what the tools returned. If the documents do not contain the \
answer, say so plainly and mention what you searched for. Never use outside knowledge \
as if it came from the documents.
6. Cite the file name after each fact, like [policy.pdf]."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Hybrid (keyword + semantic) search over the document chunks. Returns the "
                "most relevant passages with their source file names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for. Use specific terms."},
                    "source": {
                        "type": "string",
                        "description": "Optional exact file name (from list_sources) to restrict the search to.",
                    },
                    "top": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Passages to return (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_sources",
            "description": "List the document file names in the knowledge base with their chunk counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def embed(text: str) -> list[float]:
    resp = openai_client.embeddings.create(model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT, input=text)
    return resp.data[0].embedding


def search_documents(query: str, source: str | None = None, top: int | None = None) -> list[dict]:
    top = max(1, min(int(top or TOP_K), 10))
    extra = {}
    if source:
        escaped = source.replace("'", "''")  # OData string-literal escaping
        extra["filter"] = f"source eq '{escaped}'"
    results = search_client.search(
        search_text=query,
        vector_queries=[VectorizedQuery(vector=embed(query), k_nearest_neighbors=top, fields="content_vector")],
        select=["id", "content", "source"],
        top=top,
        **extra,
    )
    return [{"source": r["source"], "content": r["content"][:MAX_CHUNK_CHARS]} for r in results]


def list_sources() -> list[dict]:
    results = search_client.search(search_text="*", facets=["source,count:100"], top=0)
    facets = results.get_facets() or {}
    return [{"source": f["value"], "chunks": f["count"]} for f in facets.get("source", [])]


def run_tool(name: str, raw_args: str, retrieved: dict) -> tuple[object, str, dict]:
    """Execute one tool call. Never raises: errors go back to the model as the result."""
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {"error": "arguments were not valid JSON"}, "invalid arguments", {}
    try:
        if name == "search_documents":
            hits = search_documents(**args)
            for h in hits:
                retrieved.setdefault(h["source"], None)
            return hits, f"{len(hits)} passage(s) from {len({h['source'] for h in hits})} document(s)", args
        if name == "list_sources":
            found = list_sources()
            return found, f"{len(found)} document(s)", args
        return {"error": f"unknown tool '{name}'"}, "unknown tool", args
    except Exception as exc:  # noqa: BLE001 - let the agent see and recover from tool failures
        return {"error": str(exc)}, f"failed: {exc}", args


def run_agent(question: str, history: list[dict] | None = None) -> tuple[str, list[str], list[dict]]:
    """Returns (answer, cited sources, steps taken)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend((history or [])[-6:])
    messages.append({"role": "user", "content": question})

    steps: list[dict] = []
    retrieved: dict[str, None] = {}  # ordered, de-duplicated source names
    answer = None

    for _ in range(MAX_STEPS):
        resp = openai_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.2,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            answer = msg.content or ""
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments},
                    }
                    for c in msg.tool_calls
                ],
            }
        )
        for call in msg.tool_calls:
            result, summary, args = run_tool(call.function.name, call.function.arguments, retrieved)
            steps.append({"tool": call.function.name, "args": args, "result": summary})
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)}
            )

    if answer is None:  # step budget used up: force a final answer without tools
        messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the search limit. Answer now using only what you retrieved, "
                    "and say clearly what is still missing."
                ),
            }
        )
        resp = openai_client.chat.completions.create(
            model=AZURE_OPENAI_CHAT_DEPLOYMENT, messages=messages, temperature=0.2
        )
        answer = resp.choices[0].message.content or ""

    all_sources = list(retrieved)
    cited = [s for s in all_sources if s in answer]
    return answer, cited or all_sources, steps
