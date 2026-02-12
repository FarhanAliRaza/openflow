# OpenWhispr

Voice typing and transcription powered by [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B). Hold a key combo, speak, release — text appears at your cursor.

## Features

- **Push-to-talk voice typing** — hold Win+X (configurable), speak, release to transcribe and auto-type
- **Fast inference** — runs Qwen3-ASR-0.6B in bfloat16 on CUDA (CPU fallback available)
- **Works everywhere** — types directly into any focused window via ydotool (no focus stealing)
- **Toggle mode** — alternative press-to-start/press-to-stop via GNOME shortcut

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/FarhanAliRaza/openflow.git
cd openflow
```

### 2. Install Python dependencies

Requires Python 3.10–3.12. Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync
```

Or with pip:

```bash
pip install numpy soundfile sounddevice scipy pyperclip qwen-asr transformers tokenizers evdev torch
```

### 3. Install system dependencies (Linux)

```bash
sudo apt install ydotool
sudo systemctl enable --now ydotool
sudo usermod -aG input $USER
```

Log out and back in for the group change to take effect.

### 4. Download model weights

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B
```

This downloads ~1.8 GB into `Qwen3-ASR-0.6B/` inside the project directory.

**Custom location:** If you store the weights elsewhere, pass the path directly:

```bash
uv run python tools/voice_type.py --model /path/to/Qwen3-ASR-0.6B
```

### 5. Start voice typing

```bash
uv run python tools/voice_type.py
```

Hold **Win+X** to record, release to transcribe. Text is typed into whatever window is focused.

See the [Linux setup guide](docs/LINUX.md) for more details on system dependencies.

## Usage

### Voice Typing (Push-to-Talk)

The default mode. Hold a key combo to record, release to transcribe and auto-type.

```bash
# Default shortcut: Win+X
uv run python tools/voice_type.py

# Custom shortcut (e.g. Ctrl+V)
uv run python tools/voice_type.py --key KEY_V --modifier KEY_LEFTCTRL

# List audio input devices
uv run python tools/voice_type.py --list-devices

# Select a specific device
uv run python tools/voice_type.py --device 8

# Use weights from a custom path
uv run python tools/voice_type.py --model /path/to/Qwen3-ASR-0.6B
```

### Voice Typing (Toggle Mode)

Alternative mode using a GNOME keyboard shortcut to toggle recording on/off via a socket daemon.

```bash
# Start the daemon
uv run python tools/voice_type_toggle.py

# Toggle recording (bind this to a keyboard shortcut)
uv run python tools/voice_type_toggle.py --toggle

# Check status / stop
uv run python tools/voice_type_toggle.py --status
uv run python tools/voice_type_toggle.py --stop
```

## Platform Guides

- [Linux Setup Guide](docs/LINUX.md) — push-to-talk, ydotool, system dependencies
- [Windows Setup Guide](docs/WINDOWS.md) — transcription scripts, Gradio UI

## Project Structure

```
openflow/
├── tools/
│   ├── voice_type.py             # Push-to-talk voice typing daemon
│   └── voice_type_toggle.py      # Toggle-mode voice typing daemon
├── docs/
│   ├── LINUX.md                  # Linux setup guide
│   └── WINDOWS.md                # Windows setup guide
├── Qwen3-ASR-0.6B/              # Model weights (not in git)
├── justfile                      # Command runner recipes
└── pyproject.toml
```

## Requirements

- Python 3.10–3.12
- NVIDIA GPU with CUDA (recommended) or CPU
- Linux for voice typing (push-to-talk uses evdev + ydotool)
- Windows/macOS support is limited (see [Windows guide](docs/WINDOWS.md))
