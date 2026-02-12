# OpenWhispr command runner
# Install just: https://github.com/casey/just

default_key := "KEY_X"
default_modifier := "KEY_LEFTMETA"
model := "Qwen3-ASR-0.6B"

# List available recipes
default:
    @just --list

# ─── Setup ────────────────────────────────────────────────────────────────────

# Install Python dependencies
install:
    uv sync

# Download Qwen3-ASR-0.6B model weights from Hugging Face
download-model:
    huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B

# Install system dependencies (ydotool + input group)
setup-linux:
    sudo apt install -y ydotool
    sudo systemctl enable --now ydotool
    sudo usermod -aG input $USER
    @echo ""
    @echo "Done. Log out and back in for the input group to take effect."

# Full setup: install deps + download model + system setup
setup: install download-model setup-linux

# ─── Voice Typing ─────────────────────────────────────────────────────────────

# Start push-to-talk voice typing (default: Win+X)
start:
    uv run python tools/voice_type.py --model {{ model }}

# Start push-to-talk with a custom key combo
start-custom key=default_key modifier=default_modifier:
    uv run python tools/voice_type.py --key {{ key }} --modifier {{ modifier }} --model {{ model }}

# Start toggle-mode daemon
start-toggle:
    uv run python tools/voice_type_toggle.py --model {{ model }}

# Send toggle command to running daemon
toggle:
    uv run python tools/voice_type_toggle.py --toggle

# Query toggle daemon status
status:
    uv run python tools/voice_type_toggle.py --status

# Stop toggle daemon
stop:
    uv run python tools/voice_type_toggle.py --stop

# ─── Audio Devices ────────────────────────────────────────────────────────────

# List available audio input devices
devices:
    uv run python tools/voice_type.py --list-devices

# Start with a specific audio device index
start-device device_id:
    uv run python tools/voice_type.py --device {{ device_id }} --model {{ model }}
