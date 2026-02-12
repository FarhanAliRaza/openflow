# OpenWhispr

Voice typing and transcription powered by [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B). Hold a key combo, speak, release — text appears at your cursor.

## Features

- **Push-to-talk voice typing** — hold Win/Cmd+X (configurable), speak, release to transcribe and auto-type
- **Fast inference** — runs Qwen3-ASR-0.6B in bfloat16 on CUDA (CPU fallback available)
- **Cross-platform** — auto-detects Linux (evdev + ydotool), macOS (pynput), Windows (pynput)
- **No key leaking** — trigger key never reaches the focused app (evdev proxy on Linux, suppress mode on macOS/Windows)

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
pip install numpy soundfile sounddevice scipy pyperclip qwen-asr transformers tokenizers pynput evdev torch
```

### 3. Download model weights

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B
```

This downloads ~1.8 GB into `Qwen3-ASR-0.6B/` inside the project directory.

**Custom location:** If you store the weights elsewhere, pass the path directly:

```bash
uv run python src/voice_type --model /path/to/Qwen3-ASR-0.6B
```

### 4. Start voice typing

```bash
uv run python src/voice_type
```

Hold **Win/Cmd+X** to record, release to transcribe. Text is pasted into whatever window is focused.

**Linux setup (one-time):**

```bash
sudo apt install ydotool
sudo systemctl enable --now ydotool
sudo usermod -aG input $USER  # log out & back in
```

## Usage

The `src/voice_type/` package auto-detects your platform and uses the appropriate backend (evdev on Linux, pynput on macOS/Windows).

```bash
# Default shortcut: Win/Cmd+X
uv run python src/voice_type

# Custom shortcut (e.g. Ctrl+V)
uv run python src/voice_type --key v --modifier ctrl

# List audio input devices
uv run python src/voice_type --list-devices

# Select a specific device
uv run python src/voice_type --device 8

# Use weights from a custom path
uv run python src/voice_type --model /path/to/Qwen3-ASR-0.6B
```

**Platform notes:**
- **macOS**: Grant Accessibility permissions to your terminal (System Settings > Privacy & Security > Accessibility)
- **Windows**: No special setup needed
- **Linux (X11/Wayland)**: Requires ydotool for text injection (see Linux setup above)

## Project Structure

```
openflow/
├── src/
│   └── voice_type/
│       ├── __main__.py        # Entry point
│       ├── cli.py             # Argparse + main()
│       ├── config.py          # Constants, paths, device config
│       ├── notify.py          # Desktop notifications
│       ├── text_inject.py     # ydotool / clipboard text injection
│       ├── audio.py           # Recording + resampling
│       ├── asr.py             # ASR model wrapper
│       ├── key_monitor.py     # Evdev proxy + pynput suppress monitors
│       ├── daemon.py          # VoiceTypeDaemon
│       └── devices.py         # Audio device selection
├── Qwen3-ASR-0.6B/            # Model weights (not in git)
├── justfile                    # Command runner recipes
└── pyproject.toml
```

## Using the justfile

If you have [just](https://github.com/casey/just) installed:

```bash
just install          # uv sync
just download-model   # Download Qwen3-ASR-0.6B weights
just setup-linux      # Install ydotool + input group
just start            # Start voice typing
just devices          # List audio input devices
```

## Requirements

- Python 3.10–3.12
- NVIDIA GPU with CUDA (recommended) or CPU
- Works on Linux, macOS, and Windows
