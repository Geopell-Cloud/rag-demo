"""Ingests documents from Blob Storage into the Search index.

Reads every blob in AZURE_STORAGE_CONTAINER, extracts text with Document
Intelligence's prebuilt-read model, chunks it, embeds each chunk with the
Azure OpenAI embedding deployment, and uploads it to Azure AI Search.

Run after create_index.py:
    python ingest.py
"""
import os
import uuid

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.storage.blob import BlobServiceClient
from openai import AzureOpenAI

STORAGE_ACCOUNT_URL = os.environ["AZURE_STORAGE_ACCOUNT_URL"]  # e.g. https://<acct>.blob.core.windows.net
CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER", "rag-container")
DOCINTEL_ENDPOINT = os.environ["AZURE_DOCUMENTINTELLIGENCE_ENDPOINT"]
SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX", "rag-index")
OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))       # characters
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "200"))  # characters
UPLOAD_BATCH_SIZE = 50

credential = DefaultAzureCredential()
token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)
docintel_client = DocumentIntelligenceClient(endpoint=DOCINTEL_ENDPOINT, credential=credential)
search_client = SearchClient(endpoint=SEARCH_ENDPOINT, index_name=SEARCH_INDEX, credential=credential)
openai_client = AzureOpenAI(azure_endpoint=OPENAI_ENDPOINT, azure_ad_token_provider=token_provider, api_version=OPENAI_API_VERSION)


def extract_text(blob_bytes: bytes) -> str:
    poller = docintel_client.begin_analyze_document(
        "prebuilt-read", body=blob_bytes, content_type="application/octet-stream"
    )
    return poller.result().content


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks


def embed(text: str) -> list[float]:
    resp = openai_client.embeddings.create(model=EMBEDDING_DEPLOYMENT, input=text)
    return resp.data[0].embedding


def main() -> None:
    container = blob_service.get_container_client(CONTAINER_NAME)
    batch: list[dict] = []
    total_chunks = 0

    for blob in container.list_blobs():
        print(f"Processing {blob.name} ...")
        blob_bytes = container.download_blob(blob.name).readall()
        text = extract_text(blob_bytes)

        for chunk in chunk_text(text):
            batch.append(
                {
                    "id": str(uuid.uuid4()),
                    "content": chunk,
                    "source": blob.name,
                    "content_vector": embed(chunk),
                }
            )
            total_chunks += 1
            if len(batch) >= UPLOAD_BATCH_SIZE:
                search_client.upload_documents(batch)
                batch = []

    if batch:
        search_client.upload_documents(batch)

    print(f"Ingestion complete: {total_chunks} chunks indexed into '{SEARCH_INDEX}'.")


if __name__ == "__main__":
    main()
