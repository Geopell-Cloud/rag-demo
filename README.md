# RAG Demo Chat App — Azure OpenAI + Azure AI Search

This app demonstrates RAG (Retrieval Augmented Generation) using a Function App and uploaded documents. 

It uses Azure OpenAI Service to access GPT models, Azure AI Search for data indexing and retrieval the app's backend is written in Python.

## Sample Questions for RAG Demo Chat App

You can ask the RAG chat app questions such as:

* What is the Geopell Cloud DevOps, Cloud & AI Engineering Training?
* What is RAG?
* Please provide information on the duration and schedule for this DevOps Training.
* Do you need previous experience to participate in DevOps Training?
* What are some of the things I can learn from the training?
* How do I register for the training?
* Before joining DevOps Training, what are the requirements?

## Project Structure (for demo app)

```
rag-demo/
├── backend/                         # Azure Functions (Python) API and queue worker
│   ├── agent.py                     # Tool-calling agent: search_documents and list_sources
│   ├── function_app.py              # HTTP routes and queue trigger
│   ├── ingestion_lib.py             # Upload links, indexing, status, listing, and deletion
│   ├── host.json
│   ├── requirements-backend.txt
├── frontend/                        # Streamlit web app (App Service)
│   ├── streamlit_app.py             # Chat UI and upload document sidebar
│   ├── requirements.txt
├── ingestion/                       # Scripts run by GitHub Actions ingest job
│   ├── create_index.py              # Creates or updates search index schema
│   ├── ingest.py                    # Indexes every supported file in storage container
│   └── requirements-ingest.txt
├── data/                            # Source documents
└── .github/workflows/
    └── ingest-and-deploy.yml        # GitHub Actions Pipeline: ingest, deploy API, deploy UI
```

## What each file does

- **`agent.py`** runs the chat. It calls the chat model with two tools, `search_documents` (hybrid vector and keyword search) and `list_sources`, and repeats until it has enough evidence or reaches the step limit, then returns an answer with its sources and the steps taken.
- **`function_app.py`** exposes the Azure Functions routes: `POST /api/chat`, `POST /api/upload-url`, `POST /api/index`, `GET /api/status`, `GET` and `DELETE /api/sources`, and `GET /api/health`. It also holds the queue trigger `index_worker`, which indexes uploaded files in the background.
- **`ingestion_lib.py`** holds all indexing logic: issuing upload links, queueing jobs, extracting text (plain read for `.txt`, Document Intelligence for `.pdf` and `.docx`), chunking, embedding, skipping unchanged files, replacing chunks, recording status in blob metadata, listing sources, and deleting a document.
- **`streamlit_app.py`** is the user interface. The main area is the chat with example questions, an expandable "Agent steps" view, and citations. The sidebar uploads files, shows indexing progress, lists indexed documents, and removes them after confirmation.
- **`create_index.py`** creates or updates the search index schema, including the `contentHash` field (helps prevent embedding and re-indexing the same document file source).
- **`ingest.py`** indexes every supported blob in the storage container by calling the same `index_blob` code the portal uses. It skips unchanged files and reports indexed, unchanged, unsupported, and failed files.

## How it works

### Chat

1. The user asks a question in the Streamlit app.
2. Streamlit sends the question to `POST /api/chat`.
3. The agent asks the chat model what to do. The model can answer directly, or call `search_documents` and `list_sources`.
4. Each tool call runs against Azure AI Search. The model reads the results and either calls more tools or writes the answer, citing the files used.
5. The response returns `answer`, `sources`, and `steps`. The UI shows the answer, the cited sources, and the steps with their timings.

### Uploading a document

1. The user picks a `.txt`, `.pdf`, or `.docx` file of up to 200 MB and clicks **Upload and Index Document**.
2. Streamlit calls `POST /api/upload-url`. The Function App returns a SAS link that allows writing that one blob.
3. Streamlit uploads the file directly to the storage account container with a `PUT`.
4. Streamlit calls `POST /api/index`. The Function App checks that the blob exists, sets its status to `queued`, and puts its name on the `index-jobs` queue.
5. The queue worker (`index_worker`) runs `process_queued_blob`. It sets the status to `indexing`, then indexes the blob.
6. Streamlit polls `GET /api/status` every 3 seconds until the status is `indexed`, `unchanged`, `failed`, or `missing`, and shows the result. If the wait passes 5 minutes, it says indexing continues in the background, and the **Refresh list** button checks again.

### Indexing pipeline (`index_document`)

1. Reject files that are empty, larger than 200 MB, or of an unsupported type.
2. Compute the SHA-256 of the bytes and compare it with the `contentHash` stored on the file's chunks. If they match, stop with status `unchanged`. No embedding calls or index writes happen.
3. Delete any existing chunks for that file name, so changed files replace their chunks rather than adding duplicates.
4. Extract text. `.txt` is decoded as UTF-8. `.pdf` and `.docx` go through Document Intelligence's `prebuilt-read` model. A file with no text fails with a clear message.
5. Split the text into 1,000-character chunks with a 200-character overlap.
6. Embed the chunks in batches of 64 with the embedding deployment.
7. Write each chunk to the search index. Chunk IDs come from the file name and chunk number, and every chunk stores the file's `contentHash`.

Each file's status is written to its blob metadata (`index_status`, `index_chunks`, `index_error`). Problems with the file itself, such as no readable text, are final and marked `failed`. Other errors (such as a service outage) raise, so the queue retries. After 3 attempts the message moves to the `index-jobs-poison` queue.

### Listing and removing documents

- **List:** `GET /api/sources` returns every source in the search index with its chunk count, sorted by name. The sidebar uses this list, so what it shows is what the chat can search.
- **Remove:** Clicking **Remove** and then **Confirm** calls `DELETE /api/sources?name=<file>`. It deletes the file's chunks from the search index and its blob from the container.

### Ingest job (`ingest.py`)

`ingest.py` runs in the pipeline and lists every supported blob in the container, calls `index_blob` for each, and prints a summary. Unchanged files are skipped.

## GitHub Actions: read Terraform outputs + automated ingest + automated deploy backend and frontend

`.github/workflows/ingest-and-deploy.yml` runs manually.

| Job | Depends on | What it does |
|---|---|---|
| `get-endpoints` | — | Logs in with OIDC and runs `terraform init` and `terraform output` to read the endpoints and resource names. |
| `ingest` | `get-endpoints` | Fetches the AI Search, OpenAI, Document Intelligence, and storage account keys. Uploads `data/` to the storage container, runs `create_index.py`, then runs `ingest.py`. |
| `deploy-function-app` | `get-endpoints`, `ingest` | Fetches the AI Search and OpenAI keys and the storage account and Document Intelligence secrets. Packages `backend/` with its dependencies, deploys it to the Function App, and sets its app settings. |
| `deploy-frontend` | `get-endpoints`, `deploy-function-app` | Fetches the host function key. Sets `API_BASE` and `FUNCTION_KEY` on the web app, then deploys `frontend/` to the Streamlit App Service. |

Only three secrets are stored in GitHub: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. Everything else is read from Terraform state or fetched from Azure during the run, and secrets are masked in the logs.

The Terraform outputs the workflow expects are `resource_group_name`, `foundry_resource_group_name`, `search_endpoint`, `openai_endpoint`, `docintel_endpoint`, `storage_account_name`, `function_app_name`, and `web_app_name`.

## Azure resources

| Resource | Used for |
|---|---|
| Storage account | Source documents in the `rag-container` container, the `index-jobs` and `index-jobs-poison` queues, and Function App storage |
| Azure AI Search | Hybrid vector and keyword index (`rag-index`) with the `contentHash` field |
| Azure OpenAI / Microsoft Foundry | Chat deployment (`gpt-5-nano`) and embedding deployment (`text-embedding-3-small`) |
| Document Intelligence | Text extraction for PDF and DOCX files |
| Function App (Linux) | API, agent, and queue worker. *Always On* should be enabled so the queue worker runs |
| Web App (App Service, Linux) | Streamlit frontend |

### Identity Permissions

| Resource | Role |
|---|---|
| Storage account | `Storage Blob Data Contributor` |
| Azure AI Search | `Search Index Data Contributor` and `Search Service Contributor` |
| Document Intelligence | `Cognitive Services User` |
| Azure OpenAI | `Cognitive Services OpenAI User` |

## Running locally

1. Create `backend/local.settings.json` from the template, with real values from the deployed resources.
2. Create the index: from `ingestion/`, activate its venv, load the settings, and run `python create_index.py`.
3. Start the backend: from `backend/`, run `func start`. Keep this window open, because it runs the queue worker.
4. Start the frontend: from `frontend/`, set `API_BASE=http://localhost:7071/api` and `FUNCTION_KEY`, then run `streamlit run streamlit_app.py`.
