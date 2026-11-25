import json
import logging

import azure.functions as func

from agent import run_agent

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


def _json(payload, status=200):
    return func.HttpResponse(json.dumps(payload, ensure_ascii=False), status_code=status, mimetype="application/json")


def _body(req):
    try:
        return req.get_json() or {}
    except ValueError:
        return None


@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    body = _body(req)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)

    question = str(body.get("question", "")).strip()
    history = body.get("history", [])
    if not question:
        return _json({"error": "'question' is required"}, 400)

    try:
        answer, sources, steps = run_agent(question, history)
        return _json({"answer": answer, "sources": sources, "steps": steps})
    except Exception as exc:
        logging.exception("chat endpoint failed")
        return _json({"error": str(exc)}, 500)


@app.route(route="upload-url", methods=["POST"])
def upload_url(req: func.HttpRequest) -> func.HttpResponse:
    from ingestion_lib import UploadError, create_upload_url

    body = _body(req)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    try:
        return _json(create_upload_url(body.get("filename", "")))
    except UploadError as exc:
        return _json({"error": str(exc)}, 400)


@app.route(route="index", methods=["POST"])
def index(req: func.HttpRequest) -> func.HttpResponse:
    from ingestion_lib import UploadError, enqueue_index

    body = _body(req)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    try:
        return _json({"queued": enqueue_index(body.get("blob", ""))}, 202)
    except UploadError as exc:
        return _json({"error": str(exc)}, 400)


@app.queue_trigger(arg_name="msg", queue_name="index-jobs", connection="AZURE_STORAGE_CONNECTION_STRING")
def index_worker(msg: func.QueueMessage) -> None:
    from ingestion_lib import process_queued_blob

    name = msg.get_body().decode("utf-8")

    result = process_queued_blob(name, msg.dequeue_count)
    logging.info("index job for %s: %s", name, result)


@app.route(route="sources", methods=["GET", "DELETE"])
def sources(req: func.HttpRequest) -> func.HttpResponse:
    """GET lists indexed sources. DELETE ?name=<file> removes the chunks and the blob."""
    if req.method == "DELETE":
        try:
            from ingestion_lib import UploadError, delete_source

            return _json(delete_source(req.params.get("name", "")))
        except UploadError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception as exc:
            logging.exception("delete failed")
            return _json({"error": f"Could not remove the document: {exc}"}, 500)

    try:
        from ingestion_lib import list_all_sources

        return _json({"sources": list_all_sources()})
    except Exception as exc:
        logging.exception("sources failed")
        return _json({"error": str(exc)}, 500)


@app.route(route="status", methods=["GET"])
def status(req: func.HttpRequest) -> func.HttpResponse:
    """Indexing progress for an uploaded file: ?blob=<file name>"""
    blob = req.params.get("blob", "").strip()
    if not blob:
        return _json({"error": "'blob' is required"}, 400)
    try:
        from ingestion_lib import get_status

        return _json(get_status(blob))
    except Exception as exc:
        logging.exception("status failed")
        return _json({"error": str(exc)}, 500)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"status": "ok"})
