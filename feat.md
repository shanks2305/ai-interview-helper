# AI Interview — improvements and features

Local interview copilot: Electron captures the mic, FastAPI transcribes (Whisper or cloud STT), an LLM drafts answers, and `/live/` is the second-screen Q&A view.

## Improvements

### 1. Give the model interview memory
`copilot_turn` currently ignores prior questions (`already_handled` is discarded). Each turn is answered in isolation. Interviewers follow up. Pass a short rolling transcript of Q&A (last 5–8 turns) into the prompt so answers stay consistent.

### 2. Capture the interviewer, not just the candidate
The desktop app uses `getUserMedia` on the microphone only. With headphones / Zoom / Meet, the interviewer’s voice often never hits the mic. Add system / loopback audio (BlackHole, Stereo Mix, Electron `desktopCapturer` + tab audio, or a virtual cable).

### 3. Stop requiring Listen → Pause for every question
Wire up VAD (unused knobs already exist: `TRANSCRIBE_INTERVAL_SECONDS`, `UTTERANCE_STABLE_TICKS`). Flow: listen continuously → detect end of utterance → transcribe → draft, with a mute / “don’t answer this” hotkey.

### 4. Render typed answers as markdown
Spoken prompt forbids markdown; typed prompt asks for fenced Python. The live page uses `textContent`, so code dumps as a wall of text. Render markdown + syntax highlight on `/live/` (keep spoken answers plain).

### 5. Dedup and “not a question” are too brittle
Exact folded-string match misses follow-ups and false-starts. Use a cheap classifier: skip greetings, “um”, overlapping speech; treat follow-ups as new turns. Make skip/retry a first-class event.

### 6. Persist sessions
`InterviewSession` lives in memory. SQLite (or JSONL) for sessions, export Markdown/PDF, reopen last session on launch.

### 7. Second-screen polish
Keep-awake, larger type, “copy talking points”, hide latency stats during the interview, show them on recap. Stream partial transcripts (`partial_question` is handled in the UI but never emitted).

### 8. Engineering hygiene
- Tests around pipeline, STT reuse (`REUSE_EXTRA_BYTES`), and hub snapshot
- Dual API spawn (CLI and Electron `main.js`) is easy to confuse
- `requires-python = ">=3.14"` will freeze a lot of machines
- Local Whisper is CPU `int8` only; Metal/CUDA would cut STT latency
- Keep bind to `127.0.0.1` by default; if binding `0.0.0.0` for phone-on-LAN, add a simple token

## Features

### High impact
- Role / company / resume context (paste JD + resume once)
- Answer modes: 15s spoken, 45s spoken, STAR, system-design outline, glanceable bullets
- Talking-points view: 3–5 bullets + one example
- Follow-up pack: 2 likely follow-ups with one-liners after each answer
- Hotkeys: listen/pause, skip clip, regenerate shorter, regenerate with code, copy current answer
- Mic vs meeting audio picker and input-level calibration

### Interview-specific intelligence
- Question type router: behavioral vs coding vs system design vs trivia
- Whiteboard / coding assist: language selector, complexity, “explain as I type”
- Company / interviewer notes
- Red-flag detector: trick question / they want tradeoffs not a tutorial

### After the call
- Session library with search
- Self-review: slow spots, weak topics, suggested drills
- Flashcards from missed or slow questions
- Sanitized recap export (no raw audio)

### Nice-to-have
- Overlay / always-on-top compact HUD
- Screenshot / OCR a problem from a shared screen
- Multi-language STT (today `language="en"` is hardcoded)
- Offline-first: Ollama + local Whisper as a one-click profile

## Suggested ship order

1. Meeting/system audio + partial transcripts
2. Conversation context + JD/resume
3. Markdown live answers + talking-points mode
4. Persist + export sessions
5. VAD auto-turns + skip/regenerate hotkeys