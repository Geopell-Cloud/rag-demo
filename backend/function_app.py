import json
import logging

import azure.functions as func

from rag import get_answer

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "invalid JSON body"}), status_code=400, mimetype="application/json")

    question = (body or {}).get("question", "").strip()
    history = (body or {}).get("history", [])

    if not question:
        return func.HttpResponse(json.dumps({"error": "'question' is required"}), status_code=400, mimetype="application/json")

    try:
        answer, sources = get_answer(question, history)
        return func.HttpResponse(json.dumps({"answer": answer, "sources": sources}), mimetype="application/json")
    except Exception as exc:  # noqa: BLE001 - surface a clean 500 to the client
        logging.exception("chat endpoint failed")
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=500, mimetype="application/json")


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok"}), mimetype="application/json")
