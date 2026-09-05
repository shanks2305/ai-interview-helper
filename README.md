# AI Interview

**Version 1.0.** Local interview copilot: desktop capture of the interviewer (meeting/system audio) and optional microphone, Whisper STT, LLM answers, live Q&A in the browser.

Requires **Python 3.11+**.

## Run

```bash
uv sync
uv run ai-interview
```

That starts the API on `http://127.0.0.1:8000`, then opens the Electron capture window. The CLI owns the API process; Electron does not start a second one.

Default source is **mic + meeting audio** so Zoom/Meet through headphones is transcribed, not only your laptop mic. In the capture window, pick **Mic + meeting**, **Meeting only**, or **Microphone only**, then **Test input** and watch the meters. Speak or play a few seconds of the call: the bar should sit on **Good**. Raise **Input gain** if it stays **Too quiet**; lower it if it hits **Clipping**. Open live Q&A from the desktop button, or go to `http://127.0.0.1:8000/live/`. On that page, open **Interview context** and paste the job description and resume once; later answers stay grounded in that role. Use the **15s / 45s / STAR / Design / Bullets** control to pick how the next answer is drafted. The live card defaults to a **talking-points** layout (3–5 bullets plus one example); switch to **Full** when you need the complete draft. **New session** archives the current interview and starts a fresh one without dropping that context or answer mode.

API only (no desktop), for example when the live page is on another device:

```bash
uv run ai-interview --api-only
```

Desktop only, if the API is already running:

```bash
uv run ai-interview --desktop-only
```

`npm start` inside `desktop/` is a fallback that may start the API itself. Prefer `uv run ai-interview`.

Copy `.env.example` to `.env` and set your LLM / STT providers before the first run.

Sessions are stored in `data/sessions.db`. Opening `/live/` restores the last recap. **Library** searches past questions, answers, and companies (`/` focuses search). Export Markdown or use **Save PDF** (browser print) from a recap.

### Phone on LAN

Keep the default bind (`127.0.0.1`) unless you need the live page on a phone. Binding all interfaces mints (or uses) `AI_INTERVIEW_TOKEN`. Loopback clients stay open; other clients must pass the token.

```bash
uv run ai-interview --api-only --host 0.0.0.0
```

The process prints a live URL like `http://192.168.x.x:8000/live/?token=…`. You can also set `AI_INTERVIEW_TOKEN` in `.env`.

### Local Whisper

`STT_PROVIDER=local` uses faster-whisper. Device is `auto`: CUDA (`float16`) if a GPU is visible, Apple Silicon Metal via `mlx-whisper` when that package is installed, otherwise CPU `int8`. Override with `STT_DEVICE` (`cpu`, `cuda`, `mlx`) and `STT_COMPUTE_TYPE`.

```bash
pip install mlx-whisper   # Apple Silicon GPU
```

### Meeting audio on macOS

Electron captures system loopback (macOS 13+). The first listen may prompt for **Screen Recording**. If you launched from Terminal or Cursor, grant Screen Recording to that parent app and restart. If loopback is silent, install [BlackHole](https://existential.audio/blackhole/) (or similar), route meeting audio into it, and pick that device under **Meeting source**.

On Windows, loopback usually works without a driver; Stereo Mix or VB-Audio Cable is a fallback. In a browser (API-only), use **Meeting audio** and share the Meet/Zoom tab with audio.

## Docker

The image runs the API only (no Electron mic window). Live Q&A is at `http://localhost:8000/live/`. Set `AI_INTERVIEW_TOKEN` in `.env` so a host or phone browser can call `/api` and `/ws`.

```bash
cp .env.example .env   # then set keys and AI_INTERVIEW_TOKEN
docker compose up --build
```

Or without Compose:

```bash
docker build -t ai-interview .
docker run --rm -p 8000:8000 --env-file .env ai-interview
```

Use `STT_PROVIDER=local` in `.env` to transcribe with Whisper inside the container. Models are cached in a named volume. Interview sessions persist in the `interview-data` volume. The desktop mic app still runs on the host (`uv run ai-interview --desktop-only`) and talks to this API.
