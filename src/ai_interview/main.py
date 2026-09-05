import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .hub import hub
from .paths import renderer_dir, web_dir
from .pipeline import InterviewPipeline
from .settings import LLM_MODEL, LLM_PROVIDER, STT_MODEL, STT_PROVIDER, api_ready

RENDERER_DIR = renderer_dir()
WEB_DIR = web_dir()
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="ai-interview")
NO_STORE = {"Cache-Control": "no-store"}


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
    return hub.session or {"active": False, "turns": [], "stats": {"questions": 0}}


class AskBody(BaseModel):
    text: str = Field(min_length=1)


@app.post("/api/ask")
async def api_ask(body: AskBody) -> dict[str, str]:
    error = await hub.submit_ask(body.text)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": "queued"}


@app.websocket("/ws/live")
async def live_socket(websocket: WebSocket) -> None:
    await websocket.accept()
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
            if isinstance(event, dict) and event.get("type") == "ask":
                error = await hub.submit_ask(str(event.get("text") or ""))
                if error:
                    await websocket.send_json({"type": "error", "message": error})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(websocket)


@app.websocket("/ws/interview")
async def interview_socket(websocket: WebSocket) -> None:
    """UI streams mic audio. Server transcribes, validates questions, and drafts answers."""
    await websocket.accept()
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
    await emit({"type": "status", "state": "idle", "detail": "Mic idle"})
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
                elif event_type == "ask":
                    logger.info("event ask chars=%s", len(str(event.get("text") or "")))
                    await pipeline.ask_typed(str(event.get("text") or ""))
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

if RENDERER_DIR.exists():
    app.mount("/", StaticFiles(directory=RENDERER_DIR, html=True), name="ui")
