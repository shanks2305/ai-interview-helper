import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import Request

from .auth import CLOSE_UNAUTHORIZED, http_path_requires_token, is_authorized, provided_token, websocket_authorized
from .hub import hub
from .modes import DEFAULT_ANSWER_MODE, answer_mode_catalog
from .paths import renderer_dir, web_dir
from .pipeline import InterviewPipeline
from .session import normalize_context, session_markdown, session_print_html
from .settings import LLM_MODEL, LLM_PROVIDER, STT_MODEL, STT_PROVIDER, api_ready
from .store import get_store

RENDERER_DIR = renderer_dir()
WEB_DIR = web_dir()
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store = get_store()
    store.close_stale()
    hub.restore(store.latest())
    yield


app = FastAPI(title="ai-interview", lifespan=lifespan)
NO_STORE = {"Cache-Control": "no-store"}


@app.middleware("http")
async def lan_token_middleware(request: Request, call_next):
    if http_path_requires_token(request.url.path):
        client = request.client.host if request.client else ""
        token = provided_token(query=request.query_params, headers=request.headers)
        if not is_authorized(client_host=client, provided=token):
            return JSONResponse({"detail": "Missing or invalid access token"}, status_code=401)
    return await call_next(request)


async def _accept_socket(websocket: WebSocket) -> bool:
    await websocket.accept()
    if websocket_authorized(websocket):
        return True
    await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Missing or invalid access token")
    return False


def _status_payload(status: str) -> dict[str, str]:
    return {
        "status": status,
        "llm": "ready" if api_ready() else "missing_key",
        "llm_provider": LLM_PROVIDER,
        "llm_model": LLM_MODEL,
        "stt_provider": STT_PROVIDER,
        "stt_model": STT_MODEL,
    }


def _web_file(name: str, media_type: str) -> FileResponse:
    return FileResponse(WEB_DIR / name, media_type=media_type, headers=NO_STORE)


@app.get("/health")
def health() -> dict[str, str]:
    return _status_payload("healthy")


@app.get("/api/status")
def api_status() -> dict[str, str]:
    return _status_payload("ok")


@app.get("/api/session")
def api_session() -> dict[str, Any]:
    if hub.session:
        return hub.session
    latest = get_store().latest()
    if latest is not None:
        return latest.snapshot()
    return {"active": False, "turns": [], "stats": {"questions": 0}, "context": dict(hub.context)}


@app.get("/api/sessions")
def api_sessions(q: str = Query(default="")) -> dict[str, Any]:
    return {"sessions": get_store().list_summaries(query=q)}


def _stored_session(session_id: str):
    session = get_store().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/api/sessions/{session_id}")
def api_session_detail(session_id: str) -> dict[str, Any]:
    return _stored_session(session_id).snapshot()


@app.get("/api/sessions/{session_id}/export.md")
def api_session_markdown(session_id: str) -> PlainTextResponse:
    session = _stored_session(session_id)
    filename = f"{session.export_stem()}.md"
    return PlainTextResponse(
        session_markdown(session),
        media_type="text/markdown; charset=utf-8",
        headers={
            **NO_STORE,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/api/sessions/{session_id}/print")
def api_session_print(session_id: str, autoprint: bool = False) -> HTMLResponse:
    session = _stored_session(session_id)
    return HTMLResponse(
        session_print_html(session, autoprint=autoprint),
        headers=NO_STORE,
    )


@app.delete("/api/sessions/{session_id}")
async def api_session_delete(session_id: str) -> dict[str, Any]:
    deleted = await hub.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": "deleted", "id": session_id, "session": hub.session}


class AskBody(BaseModel):
    text: str = Field(min_length=1)


class ContextBody(BaseModel):
    role: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    job_description: str = Field(default="", max_length=50_000)
    resume: str = Field(default="", max_length=50_000)


class AnswerModeBody(BaseModel):
    mode: str = Field(default=DEFAULT_ANSWER_MODE, max_length=40)


class RedraftBody(BaseModel):
    instruction: str = Field(default="again", max_length=40)
    mode: str = Field(default="", max_length=40)
    question: str = Field(default="", max_length=20_000)


class ReviseBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


@app.post("/api/ask")
async def api_ask(body: AskBody) -> dict[str, str]:
    error = await hub.submit_ask(body.text)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": "queued"}


@app.put("/api/session/context")
async def api_session_context(body: ContextBody) -> dict[str, Any]:
    await hub.submit_context(body.model_dump())
    return {"ok": "saved", "context": dict(hub.context)}


@app.put("/api/session/answer-mode")
async def api_session_answer_mode(body: AnswerModeBody) -> dict[str, Any]:
    mode = await hub.submit_answer_mode(body.mode)
    return {
        "ok": "saved",
        "answer_mode": mode,
        "answer_modes": answer_mode_catalog(),
    }


@app.post("/api/ask/redraft")
async def api_ask_redraft(body: RedraftBody) -> dict[str, str]:
    error = await hub.submit_redraft(
        instruction=body.instruction,
        mode=body.mode or None,
        question=body.question or None,
    )
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": "queued"}


@app.post("/api/ask/revise")
async def api_ask_revise(body: ReviseBody) -> dict[str, str]:
    error = await hub.submit_revise(body.text)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": "queued"}


@app.post("/api/session/new")
async def api_session_new() -> dict[str, Any]:
    await hub.start_new_session()
    return {"ok": "started", "session": hub.session}


@app.post("/api/session/end")
async def api_session_end() -> dict[str, Any]:
    await hub.end_session()
    return {"ok": "ended", "session": hub.session}


@app.websocket("/ws/live")
async def live_socket(websocket: WebSocket) -> None:
    if not await _accept_socket(websocket):
        return
    await hub.subscribe(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "ask":
                error = await hub.submit_ask(str(event.get("text") or ""))
                if error:
                    await websocket.send_json({"type": "error", "message": error})
            elif event_type == "redraft":
                error = await hub.submit_redraft(
                    instruction=str(event.get("instruction") or ""),
                    mode=str(event.get("mode") or event.get("answer_mode") or "") or None,
                    question=str(event.get("text") or event.get("question") or "") or None,
                )
                if error:
                    await websocket.send_json({"type": "error", "message": error})
            elif event_type == "revise_question":
                error = await hub.submit_revise(str(event.get("text") or event.get("question") or ""))
                if error:
                    await websocket.send_json({"type": "error", "message": error})
            elif event_type == "set_context":
                await hub.submit_context(normalize_context(event))
            elif event_type == "set_answer_mode":
                await hub.submit_answer_mode(str(event.get("mode") or event.get("answer_mode") or ""))
            elif event_type == "new_session":
                await hub.start_new_session()
            elif event_type == "end_session":
                await hub.end_session()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(websocket)


@app.websocket("/ws/interview")
async def interview_socket(websocket: WebSocket) -> None:
    """UI streams interview audio. Server transcribes, validates questions, and drafts answers."""
    if not await _accept_socket(websocket):
        return
    send_lock = asyncio.Lock()

    async def emit(event: dict[str, Any]) -> None:
        try:
            async with send_lock:
                await websocket.send_json(event)
        except Exception:
            logger.debug("desktop socket closed; still publishing to live viewers")
        await hub.publish(event)

    pipeline = InterviewPipeline(emit)
    hub.attach_pipeline(pipeline)
    await emit({"type": "status", "state": "idle", "detail": "Audio idle"})
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text:
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("invalid json from desktop: %s", text)
                    continue
                if not isinstance(event, dict):
                    logger.info("desktop value: %s", event)
                    continue
                event_type = event.get("type")
                if event_type == "start":
                    logger.info("event start mimeType=%s", event.get("mimeType"))
                    await pipeline.start(event.get("mimeType"))
                elif event_type == "prime":
                    logger.info("event prime")
                    await pipeline.prime()
                elif event_type == "stop":
                    logger.info("event stop")
                    await pipeline.stop()
                elif event_type == "end_session":
                    logger.info("event end_session")
                    await pipeline.end_session()
                elif event_type == "new_session":
                    logger.info("event new_session")
                    await pipeline.start_new_session()
                elif event_type == "ask":
                    logger.info("event ask chars=%s", len(str(event.get("text") or "")))
                    await pipeline.ask_typed(str(event.get("text") or ""))
                elif event_type == "redraft":
                    await pipeline.redraft(
                        instruction=str(event.get("instruction") or ""),
                        mode=str(event.get("mode") or event.get("answer_mode") or "") or None,
                        question=str(event.get("text") or event.get("question") or "") or None,
                    )
                elif event_type == "revise_question":
                    await pipeline.revise_question(str(event.get("text") or event.get("question") or ""))
                elif event_type == "set_context":
                    await pipeline.set_context(normalize_context(event))
                elif event_type == "set_answer_mode":
                    await pipeline.set_answer_mode(
                        str(event.get("mode") or event.get("answer_mode") or "")
                    )
                else:
                    logger.info("event %s %s", event_type, event)
                continue
            payload = message.get("bytes")
            if payload:
                pipeline.add_audio(payload)
    except WebSocketDisconnect:
        pass
    finally:
        hub.detach_pipeline(pipeline)
        await pipeline.close()
        await hub.mark_idle()


@app.get("/live", include_in_schema=False)
@app.get("/live/", include_in_schema=False)
def live_page() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers=NO_STORE)


@app.get("/live/styles.css", include_in_schema=False)
def live_css() -> FileResponse:
    return _web_file("styles.css", "text/css")


@app.get("/live/app.js", include_in_schema=False)
def live_js() -> FileResponse:
    return _web_file("app.js", "text/javascript")


@app.get("/live/markdown.js", include_in_schema=False)
def live_markdown() -> FileResponse:
    return _web_file("markdown.js", "text/javascript")


@app.get("/live/overlay", include_in_schema=False)
@app.get("/live/overlay/", include_in_schema=False)
def overlay_page() -> FileResponse:
    return FileResponse(WEB_DIR / "overlay.html", headers=NO_STORE)


@app.get("/live/overlay.css", include_in_schema=False)
def overlay_css() -> FileResponse:
    return _web_file("overlay.css", "text/css")


@app.get("/live/overlay.js", include_in_schema=False)
def overlay_js() -> FileResponse:
    return _web_file("overlay.js", "text/javascript")

if RENDERER_DIR.exists():
    app.mount("/", StaticFiles(directory=RENDERER_DIR, html=True), name="ui")
