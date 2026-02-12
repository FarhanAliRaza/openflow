# Linux Setup Guide

Push-to-talk voice typing on Linux (X11 and Wayland). Hold a key combo, speak, release — text appears at your cursor.

## Prerequisites

- Python 3.10–3.12
- NVIDIA GPU with CUDA drivers (recommended) or CPU
- PipeWire or PulseAudio (for audio capture)

## 1. Install Python Dependencies

```bash
cd openwhispr
uv sync
```

Or with pip:

```bash
pip install numpy soundfile sounddevice scipy pyperclip qwen-asr transformers tokenizers evdev torch
```

## 2. Download Model Weights

```bash
# Install Hugging Face CLI
pip install huggingface-hub

# Download Qwen3-ASR-0.6B (1.8 GB)
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B
```

If you store weights elsewhere, pass the path with `--model`:

```bash
uv run python tools/voice_type.py --model /path/to/Qwen3-ASR-0.6B
```

Or symlink them into the project:

```bash
ln -s /path/to/your/Qwen3-ASR-0.6B ./Qwen3-ASR-0.6B
```

## 3. Install ydotool (Auto-Typing)

ydotool simulates real keystrokes so transcribed text is typed directly into any focused window. This works on both X11 and Wayland.

```bash
# Install
sudo apt install ydotool

# Enable the daemon (required)
sudo systemctl enable --now ydotool
```

Without ydotool, OpenWhispr falls back to copying text to the clipboard via `wl-copy` — you'll need to paste manually.

## 4. Add Your User to the `input` Group

Push-to-talk reads keyboard events via evdev, which requires access to `/dev/input/` devices.

```bash
sudo usermod -aG input $USER
```

**Log out and back in** (or reboot) for the group change to take effect.

Verify it worked:

```bash
groups | grep input
```

## 5. Start Voice Typing

```bash
uv run python tools/voice_type.py
```

On first run you'll be prompted to select an audio input device. The choice is saved to `~/.config/voice-type/config.json`.

Once loaded, hold **Win+X** to record, release to transcribe. A desktop notification shows the status.

### Custom Key Combo

Any evdev key name works. Common choices:

```bash
# Win+X (default)
uv run python tools/voice_type.py --key KEY_X --modifier KEY_LEFTMETA

# Ctrl+Space
uv run python tools/voice_type.py --key KEY_SPACE --modifier KEY_LEFTCTRL

# Right Alt+V
uv run python tools/voice_type.py --key KEY_V --modifier KEY_RIGHTALT
```

Find key names with `evtest` or check the [evdev key list](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input-event-codes.h).

### Audio Device Selection

```bash
# List available input devices
uv run python tools/voice_type.py --list-devices

# Use a specific device by index
uv run python tools/voice_type.py --device 8
```

## Toggle Mode (Alternative)

If you prefer a press-once-to-start, press-again-to-stop workflow, use the toggle daemon with a GNOME keyboard shortcut.

### Start the daemon

```bash
uv run python tools/voice_type_toggle.py
```

### Bind a GNOME shortcut

Go to **Settings > Keyboard > Custom Shortcuts** and add:

| Field    | Value |
|----------|-------|
| Name     | Voice Type |
| Command  | `/full/path/to/uv run python /full/path/to/tools/voice_type_toggle.py --toggle` |
| Shortcut | Super+V (or your choice) |

### Commands

```bash
uv run python tools/voice_type_toggle.py --toggle    # Toggle recording
uv run python tools/voice_type_toggle.py --status     # Check: idle/recording/processing
uv run python tools/voice_type_toggle.py --stop       # Stop the daemon
```

## Troubleshooting

### "No keyboard devices found for push-to-talk"

Your user isn't in the `input` group, or you haven't logged out after adding it.

```bash
sudo usermod -aG input $USER
# Then log out and back in
```

### "ydotool not available"

The ydotool service isn't running.

```bash
sudo systemctl enable --now ydotool
systemctl status ydotool
```

### Audio device not detected

Check that your microphone is recognized:

```bash
uv run python tools/voice_type.py --list-devices
```

If your device doesn't appear, check PipeWire/PulseAudio is running:

```bash
pactl list sources short
```

### Recording too short

The minimum recording duration is 0.3 seconds. If you see "Too short" messages, hold the key combo a bit longer.

### Model loading fails

Verify the weights are in the right place:

```bash
ls Qwen3-ASR-0.6B/model.safetensors
```

If the file doesn't exist, re-download:

```bash
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B
```

## Running on Startup

To start voice typing automatically at login, create a systemd user service:

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/openwhispr.service << 'EOF'
[Unit]
Description=OpenWhispr Voice Typing
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=/path/to/openwhispr
ExecStart=/path/to/uv run python tools/voice_type.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user enable --now openwhispr
```

Check status with `systemctl --user status openwhispr`.
