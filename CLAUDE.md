# OpenFlow — Open Source Voice Typing

An open-source alternative to [Wispr Flow](https://wisprflow.ai) — push-to-talk voice typing that works in every app, powered entirely by local/open-source models.

## Project Vision

Replace your keyboard with your voice. Hold a hotkey, speak naturally, release — polished text appears at your cursor. No cloud APIs, no subscriptions, fully offline, fully open source.

## Current State

We have a working push-to-talk transcription system:

- **Platforms**: Linux (X11/Wayland via EvDev + ydotool), Windows (Pynput), macOS (Pynput)
- **ASR Model**: Qwen3-ASR-0.6B (local, offline, CUDA/CPU)
- **Architecture**: Event-driven daemon — KeyMonitor → VoiceRecorder → ASR → TextInjection
- **Key Features Built**: Hotkey combo detection with no key leaking, audio recording + resampling, local transcription, cross-platform text injection, device persistence, desktop notifications

## What We're Building (Wispr Flow Feature Parity)

### Phase 1 — AI Text Refinement (Core Differentiator)

Raw transcription is not enough. Wispr Flow's killer feature is that it cleans up speech into polished writing. We need the same, using local LLMs.

- [ ] **Filler word removal** — Strip "um", "uh", "like", "you know", repetitions, false starts
- [ ] **Auto-punctuation** — Detect sentence boundaries from pauses and intonation, insert proper punctuation
- [ ] **Backtracking/corrections** — Handle mid-sentence corrections ("let's meet at 2... actually 3" → "let's meet at 3")
- [ ] **Grammar and spelling cleanup** — Fix transcription artifacts, proper capitalization, basic grammar
- [ ] **Numbered list formatting** — Detect when user is listing items, format as numbered/bulleted list
- [ ] **LLM integration** — Use a local LLM (e.g., Qwen3, Llama, Phi) to post-process raw transcription into clean text. This is the pipeline: `raw_audio → ASR → raw_text → LLM_cleanup → polished_text → inject`

### Phase 2 — Context Awareness & Tone

- [ ] **Active window detection** — Detect which app has focus (email client, chat, code editor, document)
- [ ] **Tone adaptation** — Adjust formality based on context: formal for email/docs, casual for chat/messaging, technical for code editors
- [ ] **Context-aware spelling** — Use surrounding text / app context to correctly spell names, technical terms
- [ ] **Append vs replace modes** — Option to append to existing text or replace selection

### Phase 3 — Personalization

- [ ] **Personal dictionary** — User-defined vocabulary for names, jargon, acronyms, brand terms. Stored in `~/.config/openflow/dictionary.json`
- [ ] **Auto-learning dictionary** — Learn corrections the user makes frequently and apply them automatically
- [ ] **Voice shortcuts / snippets** — User-defined trigger phrases that expand to full formatted text (e.g., say "my address" → full mailing address)
- [ ] **Tone presets** — User-configurable tone profiles (formal, casual, technical, enthusiastic)

### Phase 4 — Voice Commands (Command Mode)

- [ ] **Command mode toggle** — A way to switch from dictation to command mode (e.g., different hotkey, or keyword like "command:")
- [ ] **Text transformation commands** — "Make this more formal", "Turn this into bullet points", "Summarize this", "Make it shorter"
- [ ] **Editing commands** — "Delete last sentence", "Undo", "Select all", "Replace X with Y"
- [ ] **App control commands** — Basic commands like "new line", "new paragraph", "tab"

### Phase 5 — Developer Features

- [ ] **Code-aware dictation** — Handle camelCase, snake_case, PascalCase conversions when in code editors
- [ ] **Syntax preservation** — Don't auto-correct code syntax, variable names, CLI commands
- [ ] **File tagging** — Recognize filenames spoken aloud in editors like Cursor/VS Code
- [ ] **Technical jargon recognition** — Built-in dictionary for common dev tools/frameworks (Kubernetes, Supabase, PostgreSQL, etc.)

### Phase 6 — Multilingual & Accessibility

- [ ] **Language auto-detection** — Detect spoken language automatically and transcribe accordingly (Qwen3-ASR already supports 52+ languages)
- [ ] **Mixed-language support** — Handle code-switching (e.g., English + Spanish in same sentence)
- [ ] **Whisper mode** — Optimize recognition for quiet/whispered speech
- [ ] **Accessibility features** — Support for users with mobility challenges, RSI, dyslexia

### Phase 7 — UI & System Tray

- [ ] **System tray application** — Background daemon with tray icon showing status (idle, recording, processing)
- [ ] **Settings GUI** — Configure hotkeys, devices, model, tone, dictionary from a GUI
- [ ] **Visual feedback** — Overlay or indicator showing recording state, transcription progress
- [ ] **Usage statistics** — Track words dictated, time saved, most used apps

### Phase 8 — Performance & Streaming

- [ ] **Streaming transcription** — Start transcribing while still speaking (don't wait for release)
- [ ] **Model optimization** — Quantized models (GGUF/ONNX) for faster inference on CPU
- [ ] **Warm model** — Keep model loaded in memory between recordings (already partially done)
- [ ] **Reduced latency pipeline** — Overlap audio processing with inference

## Tech Stack

| Component | Current | Target |
|-----------|---------|--------|
| ASR | Qwen3-ASR-0.6B | Qwen3-ASR (or faster alternatives as they emerge) |
| Text Cleanup LLM | None | Local LLM via llama.cpp, Ollama, or transformers |
| Keyboard | EvDev (Linux), Pynput (Win/Mac) | Same |
| Audio | sounddevice + scipy | Same |
| Text Injection | ydotool (Linux), clipboard (Win/Mac) | Same, improve ydotool reliability |
| Config | JSON files | JSON/TOML in `~/.config/openflow/` |
| UI | CLI only | System tray (Qt/GTK or Tauri) |
| Package Manager | uv | uv |
| Python | 3.10-3.12 | 3.10-3.12 |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        OpenFlow Daemon                        │
│                                                              │
│  ┌────────────┐  ┌────────────┐  ┌─────────┐  ┌──────────┐ │
│  │ KeyMonitor │→ │ Recorder   │→ │ ASR     │→ │ LLM      │ │
│  │ (hotkey)   │  │ (audio)    │  │ (STT)   │  │ (cleanup) │ │
│  └────────────┘  └────────────┘  └─────────┘  └────┬─────┘ │
│                                                     │        │
│  ┌────────────┐  ┌────────────┐  ┌─────────────────▼──────┐ │
│  │ Context    │→ │ Dictionary │→ │ Text Injector          │ │
│  │ (app info) │  │ (personal) │  │ (type into active app) │ │
│  └────────────┘  └────────────┘  └────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

## Source Structure

```
src/voice_type/
├── __main__.py          # Entry point
├── cli.py               # Argument parser & main()
├── config.py            # Constants, paths, platform detection
├── daemon.py            # VoiceTypeDaemon — core orchestrator
├── key_monitor.py       # Keyboard event handling (EvDev/Pynput)
├── audio.py             # Audio recording & resampling
├── asr.py               # ASR model wrapper (Qwen3-ASR)
├── text_inject.py       # Platform-specific text injection
├── devices.py           # Audio device enumeration/selection
└── notify.py            # Desktop notifications
```

## Development Commands

```bash
# Install dependencies
uv sync

# Run the app
uv run python src/voice_type

# Run with options
uv run python src/voice_type --key x --modifier cmd --list-devices

# Linux setup
sudo apt install ydotool ydotoold   # ydotoold is a SEPARATE package (the daemon)
sudo usermod -aG input $USER         # then log out/in so the group applies
# OpenFlow launches ydotoold itself as your user — no systemd service needed.
# The daemon gives ydotool a persistent uinput device; without it, fast
# keystrokes drop characters (notably spaces between words).
```

## Key Principles

1. **Offline first** — Everything runs locally. No cloud APIs, no telemetry, no data leaves the machine.
2. **Open source models only** — Qwen, Llama, Phi, Whisper — no proprietary model dependencies.
3. **Cross-platform** — Linux, Windows, macOS. First-class Linux support (most voice tools ignore Linux).
4. **Low latency** — Sub-second from release to text appearing. Keep models warm, optimize the pipeline.
5. **Simple codebase** — Clean Python, minimal abstractions, easy to contribute to.
6. **No key leaking** — The trigger hotkey must never reach the OS/application. This is solved and must stay solved.
