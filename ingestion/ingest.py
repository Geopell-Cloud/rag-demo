"""Indexes and ingests documents in storage account.

Uses the same index_document() code as Azure portal upload, so re-running this
replaces each file's chunks instead of adding duplicates.

Run after create_index.py:
    python ingest.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from azure.storage.blob import BlobServiceClient

import ingestion_lib as lib

STORAGE_CONNECTION_STRING = os.environ["AZURE_STORAGE_CONNECTION_STRING"]


def main() -> int:
    container = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING).get_container_client(lib.STORAGE_CONTAINER)
    indexed = unchanged = skipped = failed = 0

    for blob in container.list_blobs():
        if not blob.name.lower().endswith(lib.ALLOWED_EXTENSIONS):
            print(f"Skipping {blob.name} (unsupported type)")
            skipped += 1
            continue
        print(f"Indexing {blob.name} ...")
        try:
            result = lib.index_blob(blob.name)
            if result["status"] == "unchanged":
                print("  unchanged, skipped")
                unchanged += 1
            else:
                print(f"  {result['chunks']} chunks (replaced {result['replaced_chunks']})")
                indexed += 1
        except Exception as exc:
            print(f"::error::Failed to index {blob.name}: {exc}")
            failed += 1

    print(f"Done: {indexed} indexed, {unchanged} unchanged, {skipped} unsupported, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
