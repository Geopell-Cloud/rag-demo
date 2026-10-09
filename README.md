# RAG Demo Chat App — Azure OpenAI + Azure AI Search

This app demonstrates RAG (Retrieval Augmented Generation) using a Function App and uploaded documents. 

It uses Azure OpenAI Service to access GPT models, Azure AI Search for data indexing and retrieval the app's backend is written in Python.

## Project Structure (for demo app)

```
backend/
  function_app.py                     # Azure Function (Python v2 model) exposes POST /api/chat
  host.json
  rag.py
  requirements-backend.txt
data/                                 # Sample documents
  DevOps_Overview.pdf
  Geopell-Cloud-Knowledge-Base.txt
  RAG_Overview.pdf
frontend/
  app.js
  index.html
  style.css
ingestion/
  create_index.py                     # Builds search index
  ingest.py                           # Loads documents
  requirements-ingest.txt
```

## How it works

1. **Ingestion:**
   `create_index.py` creates a hybrid (keyword + vector) index in Azure AI
   Search. `ingest.py` reads every blob in storage container, extracts
   text with Document Intelligence's `prebuilt-read` model, chunks it,
   embeds each chunk with Azure OpenAI embedding deployment, and
   uploads the chunks to the index.

2. **Chat (the Function App):**
   `POST /api/chat` embeds the user's question, does a hybrid search against the
   index, stuffs the top results into a prompt, and calls Azure OpenAI
   chat deployment. Returns `{ answer, sources }`.

3. **Frontend:**
   `index.html` is a static page with no dependencies — open it directly or
   host it from the Storage Account's static website. It just calls the
   Function App.

All Azure auth uses `DefaultAzureCredential` and `AzureKeyCredential`.

## GitHub Actions: read Terraform outputs + automated ingest + automated deploy + setup frontend

`.github/workflows/ingest-and-deploy.yml` runs the entire ingest-and-deploy
cycle when documents are pushed into the `data/` folder at the repo root,
or the backend changes:

1. **`get-endpoints`** — Captures the endpoints and resource names from Terraform resource outputs to use throughout workflow
2. **`ingest`** — uploads everything from `data/` to Storage Account's container, then runs `create_index.py` and `ingest.py`
   against it.
3. **`deploy-function-app`** — packages `backend/` (installs
   dependencies into `.python_packages/lib/site-packages`) and deploys it with
   `Azure/functions-action`, then sets the required app settings.
4. **`deploy-frontend`** — deploys and sets up the frontend, including fetching the function app key, enabling hosting on storage account, allowing website URL via CORS, and copying frontend files to storage account.

### 1. Put documents in `data/`

```
repo/
  data/
    document-a.pdf
    document-b.docx
  backend/
  ingestion/
  frontend/
  .github/workflows/ingest-and-deploy.yml
```

Any file type Document Intelligence's `prebuilt-read` model supports (PDF, DOCX, images, etc.) works here.
This app also supports .txt and .md files.

### 2. Set up OIDC federated auth for GitHub Actions

Create an app registration with a federated credential. Use `repo:<org>/<repo>:environment:<env>` while using a GitHub Environment.

### 3. Grant the app's identity the required roles

The identity behind `AZURE_CLIENT_ID` needs:

| Resource               | Role                                |
|-------------------------|---------------------------------------|
| Storage Account         | `Storage Blob Data Contributor`      |
| Azure AI Search         | `Search Index Data Contributor` and `Search Service Contributor` |
| Document Intelligence   | `Cognitive Services User`            |
| Azure OpenAI            | `Cognitive Services OpenAI User`     |
| Function App (or its resource group) | `Contributor` (for `functions-action` and `az functionapp config appsettings set`) |

### 4. Add repository secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Example value |
|---|---|
| `AZURE_CLIENT_ID` | app registration's client ID |
| `AZURE_TENANT_ID` | tenant ID |
| `AZURE_SUBSCRIPTION_ID` | subscription ID |

### 5. Run the workflow

## Notes

- Uses single chat endpoint
- Uses hybrid (vector + keyword)
  search
- Chunking is simple character-based splitting
