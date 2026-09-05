# AI Interview

Local interview copilot: desktop microphone capture, Whisper STT, LLM answers, live Q&A in the browser.

## Run

```bash
uv sync
uv run ai-interview
```

That starts the API on `http://127.0.0.1:8000` and the Electron mic window. Open live Q&A from the desktop button, or go to `http://127.0.0.1:8000/live/`.

API only (no desktop):

```bash
uv run ai-interview --api-only
```

Copy `.env.example` to `.env` and set your LLM / STT providers before the first run.

## Docker

The image runs the API only (no Electron mic window). Live Q&A is at `http://localhost:8000/live/`.

```bash
cp .env.example .env   # then set keys
docker compose up --build
```

Or without Compose:

```bash
docker build -t ai-interview .
docker run --rm -p 8000:8000 --env-file .env ai-interview
```

Use `STT_PROVIDER=local` in `.env` to transcribe with Whisper inside the container. Models are cached in a named volume. The desktop mic app still runs on the host (`uv run ai-interview`) and talks to this API.
