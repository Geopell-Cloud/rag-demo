"""Creates or updates the Azure AI Search index used for RAG.

Run once before ingesting documents:
    python create_index.py
"""
import os

from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
INDEX_NAME = os.environ.get("AZURE_SEARCH_INDEX", "rag-index")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))  # text-embedding-3-small

credential = DefaultAzureCredential()
index_client = SearchIndexClient(endpoint=ENDPOINT, credential=credential)

fields = [
    SimpleField(name="id", type=SearchFieldDataType.String, key=True),
    SearchableField(name="content", type=SearchFieldDataType.String),
    SimpleField(name="source", type=SearchFieldDataType.String, filterable=True, facetable=True),
    SearchField(
        name="content_vector",
        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
        searchable=True,
        vector_search_dimensions=EMBEDDING_DIMENSIONS,
        vector_search_profile_name="default-profile",
    ),
]

vector_search = VectorSearch(
    algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
    profiles=[VectorSearchProfile(name="default-profile", algorithm_configuration_name="default-hnsw")],
)

index = SearchIndex(name=INDEX_NAME, fields=fields, vector_search=vector_search)

if __name__ == "__main__":
    index_client.create_or_update_index(index)
    print(f"Index '{INDEX_NAME}' created/updated on {ENDPOINT}")
