"""Document indexing shared by the upload endpoints, the queue worker, and ingest.py.

Flow for a portal upload (large files never pass through an HTTP request):
  1. /api/upload-url  -> returns a short-lived SAS link to write one blob
  2. browser/Streamlit PUTs the file straight to Blob Storage
  3. /api/index       -> puts the blob name on the 'index-jobs' queue
  4. queue trigger    -> process_queued_blob() indexes it with the Function's longer
                         timeout and records progress in the blob's metadata
  5. /api/status      -> the UI polls this until the file is indexed or failed

Removing a document (delete_source) deletes its chunks from the index and the
blob from the container.

index_document() is the single indexing path. It replaces a file's chunks,
never duplicates them: chunk IDs come from the file name and chunk number,
and any older chunks for that file are deleted first.
"""
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
from azure.storage.queue import QueueClient

from agent import openai_client, search_client

STORAGE_CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
STORAGE_CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER", "documents")
DOCINTEL_ENDPOINT = os.environ["AZURE_DOCUMENTINTELLIGENCE_ENDPOINT"]
DOCINTEL_KEY = os.environ["AZURE_DOCUMENTINTELLIGENCE_KEY"]
EMBEDDING_DEPLOYMENT_NAME = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
INDEX_QUEUE_NAME = "index-jobs"  # must match the queue_trigger in function_app.py
MAX_ATTEMPTS = 3

ALLOWED_EXTENSIONS = (".txt", ".pdf", ".docx")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_LINK_MINUTES = 60
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBED_BATCH = 64
INDEX_BATCH = 100
CONTENT_HASH_FIELD = "contentHash"  # SHA-256 of the file bytes, stored on every chunk

_blob_service = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
_docintel = DocumentIntelligenceClient(endpoint=DOCINTEL_ENDPOINT, credential=AzureKeyCredential(DOCINTEL_KEY))


class UploadError(ValueError):
    """Problem with the request or the file (wrong type, too big, empty, unreadable)."""


def check_extension(name: str) -> str:
    if not name or not name.lower().endswith(ALLOWED_EXTENSIONS):
        raise UploadError(f"Only {', '.join(ALLOWED_EXTENSIONS)} files are supported")
    return name


def create_upload_url(filename: str) -> dict:
    name = check_extension(os.path.basename((filename or "").strip()))
    sas = generate_blob_sas(
        account_name=_blob_service.account_name,
        container_name=STORAGE_CONTAINER,
        blob_name=name,
        account_key=_blob_service.credential.account_key,
        permission=BlobSasPermissions(create=True, write=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=UPLOAD_LINK_MINUTES),
    )
    url = _blob_service.get_blob_client(STORAGE_CONTAINER, name).url
    return {"blob": name, "upload_url": f"{url}?{sas}"}


def enqueue_index(blob_name: str) -> str:
    name = check_extension(blob_name)
    try:
        _blob_service.get_blob_client(STORAGE_CONTAINER, name).set_blob_metadata({"index_status": "queued"})
    except ResourceNotFoundError:
        raise UploadError(f"{name} was not found in the container; upload it first") from None
    queue = QueueClient.from_connection_string(STORAGE_CONNECTION_STRING, INDEX_QUEUE_NAME)
    try:
        queue.create_queue()
    except ResourceExistsError:
        pass
    queue.send_message(name)
    return name


def extract_text(name: str, data: bytes) -> str:
    if name.lower().endswith(".txt"):
        return data.decode("utf-8", errors="replace")
    poller = _docintel.begin_analyze_document("prebuilt-read", body=data, content_type="application/octet-stream")
    return poller.result().content or ""


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        piece = text[start:start + CHUNK_SIZE].strip()
        if piece:
            chunks.append(piece)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _safe_key(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:60]
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def _embed_many(texts: list[str]) -> list[list[float]]:
    vectors = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = openai_client.embeddings.create(model=EMBEDDING_DEPLOYMENT_NAME, input=texts[i:i + EMBED_BATCH])
        vectors.extend(item.embedding for item in sorted(resp.data, key=lambda d: d.index))
    return vectors


def _remove_existing_chunks(name: str) -> int:
    escaped = name.replace("'", "''")
    removed = 0
    while True:
        stale = [{"id": r["id"]} for r in search_client.search(
            search_text="*", filter=f"source eq '{escaped}'", select=["id"], top=1000)]
        if not stale:
            return removed
        for i in range(0, len(stale), INDEX_BATCH):
            search_client.delete_documents(stale[i:i + INDEX_BATCH])
        removed += len(stale)


def _indexed_hash(name: str) -> str | None:
    """Content hash stored on this file's chunks, or None if the file isn't indexed."""
    escaped = name.replace("'", "''")
    for hit in search_client.search(search_text="*", filter=f"source eq '{escaped}'",
                                    select=[CONTENT_HASH_FIELD], top=1):
        return hit.get(CONTENT_HASH_FIELD)
    return None


def index_document(name: str, data: bytes) -> dict:
    """Index one file's bytes under its blob name. Skips files whose content is already indexed."""
    check_extension(name)
    if not data:
        raise UploadError(f"{name} is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(f"{name} is larger than 200 MB")

    digest = hashlib.sha256(data).hexdigest()
    if _indexed_hash(name) == digest:
        return {"source": name, "status": "unchanged", "chunks": 0, "replaced_chunks": 0}

    replaced = _remove_existing_chunks(name)

    try:
        text = extract_text(name, data)
    except Exception as exc:
        raise UploadError(f"Could not read text from {name}: {exc}") from exc

    chunks = chunk_text(text)
    if not chunks:
        raise UploadError(f"No readable text found in {name}")

    vectors = _embed_many(chunks)
    base = _safe_key(name)
    docs = [
        {"id": f"{base}-{i}", "content": c, "source": name, CONTENT_HASH_FIELD: digest, "content_vector": v}
        for i, (c, v) in enumerate(zip(chunks, vectors))
    ]
    for i in range(0, len(docs), INDEX_BATCH):
        results = search_client.upload_documents(docs[i:i + INDEX_BATCH])
        failed = [r for r in results if not r.succeeded]
        if failed:
            raise UploadError(f"Indexing failed for {len(failed)} chunk(s) of {name}")

    return {"source": name, "status": "indexed", "chunks": len(docs), "replaced_chunks": replaced}


def index_blob(blob_name: str) -> dict:
    """Download a blob from the container and index it. Used by the queue worker and ingest.py."""
    check_extension(blob_name)
    client = _blob_service.get_blob_client(STORAGE_CONTAINER, blob_name)
    size = client.get_blob_properties().size
    if size > MAX_UPLOAD_BYTES:
        raise UploadError(f"{blob_name} is larger than 200 MB")
    return index_document(blob_name, client.download_blob().readall())


# ---------------------------------------------------------------------------
# Progress tracking, listing, and removal
# ---------------------------------------------------------------------------

def _ascii(value, limit: int = 200) -> str:
    """Blob metadata values must be plain ASCII on one line."""
    return re.sub(r"[^\x20-\x7E]", " ", str(value)).strip()[:limit] or "-"


def _set_status(blob_name: str, status: str, **extra) -> None:
    metadata = {"index_status": status}
    metadata.update({f"index_{key}": _ascii(value) for key, value in extra.items()})
    try:
        _blob_service.get_blob_client(STORAGE_CONTAINER, blob_name).set_blob_metadata(metadata)
    except ResourceNotFoundError:
        pass


def process_queued_blob(blob_name: str, attempt: int) -> dict:
    """Queue worker entry point: index one blob and record its status.

    Problems with the file itself (unreadable, no text) are final, so they are
    recorded as 'failed' without retrying. Anything else is re-raised so the
    queue retries; after MAX_ATTEMPTS the message moves to the poison queue.
    """
    blob = _blob_service.get_blob_client(STORAGE_CONTAINER, blob_name)
    if not blob.exists():
        logging.warning("%s no longer exists; skipping", blob_name)
        return {"source": blob_name, "status": "missing", "chunks": 0}

    _set_status(blob_name, "indexing")
    try:
        result = index_blob(blob_name)
    except UploadError as exc:
        _set_status(blob_name, "failed", error=exc)
        return {"source": blob_name, "status": "failed", "error": str(exc)}
    except Exception as exc:
        _set_status(blob_name, "failed" if attempt >= MAX_ATTEMPTS else "retrying", error=exc)
        raise
    _set_status(blob_name, result["status"], chunks=result["chunks"])
    return result


def get_status(blob_name: str) -> dict:
    """Indexing progress for one uploaded file, read from its blob metadata."""
    try:
        props = _blob_service.get_blob_client(STORAGE_CONTAINER, blob_name).get_blob_properties()
    except ResourceNotFoundError:
        return {"status": "missing", "chunks": None, "error": None}
    meta = props.metadata or {}
    chunks = meta.get("index_chunks", "")
    return {
        "status": meta.get("index_status", "unknown"),
        "chunks": int(chunks) if chunks.isdigit() else None,
        "error": meta.get("index_error"),
    }


def list_all_sources() -> list[dict]:
    """Every source in the search index with its chunk count (up to 1000 files)."""
    results = search_client.search(search_text="*", facets=["source,count:1000"], top=0)
    facets = results.get_facets() or {}
    sources = [{"source": f["value"], "chunks": f["count"]} for f in facets.get("source", [])]
    return sorted(sources, key=lambda item: item["source"].lower())


def delete_source(name: str) -> dict:
    """Remove a document everywhere: its chunks from the index, then its blob.

    Safe to repeat: if one half already happened, the other still completes.
    """
    name = (name or "").strip()
    if not name:
        raise UploadError("A source name is required")
    removed = _remove_existing_chunks(name)
    blob_deleted = True
    try:
        _blob_service.get_blob_client(STORAGE_CONTAINER, name).delete_blob(delete_snapshots="include")
    except ResourceNotFoundError:
        blob_deleted = False
    return {"source": name, "chunks_removed": removed, "blob_deleted": blob_deleted}
