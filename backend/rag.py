import os
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI

AZURE_SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
AZURE_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "rag-index")
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_CHAT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.1")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))

# Single managed-identity credential, reused for both Search and OpenAI.
credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX,
    credential=credential,
)

openai_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    azure_ad_token_provider=token_provider,
    api_version=AZURE_OPENAI_API_VERSION,
)

SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question using ONLY the "
    "numbered sources below. If the sources don't contain the answer, say you "
    "don't know. After each fact, cite the source like [source_name]."
)


def embed(text: str) -> list[float]:
    resp = openai_client.embeddings.create(model=AZURE_OPENAI_EMBEDDING_DEPLOYMENT, input=text)
    return resp.data[0].embedding


def retrieve(question: str, top: int = TOP_K) -> list[dict]:
    """Hybrid search: keyword + vector, over the ingested chunks."""
    vector_query = VectorizedQuery(vector=embed(question), k_nearest_neighbors=top, fields="content_vector")
    results = search_client.search(
        search_text=question,
        vector_queries=[vector_query],
        select=["id", "content", "source"],
        top=top,
    )
    return [{"id": r["id"], "content": r["content"], "source": r["source"]} for r in results]


def get_answer(question: str, history: list[dict] | None = None) -> tuple[str, list[str]]:
    docs = retrieve(question)
    context = "\n\n".join(f"[{d['source']}]\n{d['content']}" for d in docs) or "(no matching sources found)"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend((history or [])[-6:])  # keep prior turns short
    messages.append({"role": "user", "content": f"Sources:\n{context}\n\nQuestion: {question}"})

    resp = openai_client.chat.completions.create(
        model=AZURE_OPENAI_CHAT_DEPLOYMENT,
        messages=messages,
        temperature=0.3,
    )
    answer = resp.choices[0].message.content
    sources = sorted({d["source"] for d in docs})
    return answer, sources
