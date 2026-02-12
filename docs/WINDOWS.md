# Windows Setup Guide

Transcription on Windows using the CLI and Gradio web UI.

> **Note:** Push-to-talk voice typing (`tools/voice_type.py`) is Linux-only — it relies on evdev for keyboard monitoring and ydotool for text injection. On Windows, use the Gradio web UI for microphone-based transcription, or the CLI for file transcription.

## Prerequisites

- Python 3.10–3.12
- NVIDIA GPU with CUDA drivers (recommended) or CPU
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## 1. Install Python Dependencies

Using uv (recommended):

```powershell
cd openwhispr
uv sync
```

Or with pip (skip the Linux-only `evdev` package):

```powershell
pip install numpy soundfile sounddevice scipy pyperclip qwen-asr transformers tokenizers torch
```

## 2. Download Model Weights

### Hugging Face CLI (recommended)

```powershell
# Install the CLI
pip install huggingface-hub

# Download Qwen3-ASR-0.6B (1.8 GB)
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B
```

### Python API (alternative)

```python
from huggingface_hub import snapshot_download
snapshot_download("Qwen/Qwen3-ASR-0.6B", local_dir="Qwen3-ASR-0.6B")
```

### Verify

After downloading, confirm the weights are in place:

```powershell
dir Qwen3-ASR-0.6B\model.safetensors
```

If you store the weights in a different location, pass the path with `--model`:

```powershell
uv run python tools/voice_type.py --model C:\path\to\Qwen3-ASR-0.6B
```

Or create a directory junction:

```powershell
mklink /J Qwen3-ASR-0.6B C:\path\to\your\Qwen3-ASR-0.6B
```

## 3. CLI Transcription

Transcribe audio files from the command line. This auto-downloads the larger 1.7B model on first run:

```powershell
uv run python scripts/transcribe.py recording.wav
```

Output:

```
Loading Qwen3-ASR-1.7B...
Model loaded in 4.2s

Ever tried ever failed no matter try again fail again fail better

[10.0s audio -> 523ms, lang=English]
```

## 4. Gradio Web UI

Browser-based transcription with microphone recording and file upload.

### Install Gradio

```powershell
uv sync --extra gradio
```

Or:

```powershell
pip install gradio
```

### Start the UI

```powershell
uv run python scripts/transcribe_gradio.py
```

Opens at http://127.0.0.1:7860. Two tabs:

- **Microphone** — click Record, speak, click Transcribe
- **Upload** — drag and drop an audio file

For remote access or if microphone doesn't work (requires HTTPS for browser mic access):

```powershell
uv run python scripts/transcribe_gradio.py --share
```

This creates a public `*.gradio.live` URL with HTTPS.

## Troubleshooting

### CUDA not detected

Verify PyTorch sees your GPU:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
```

If `False`, install the CUDA version of PyTorch:

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

### Model download fails or is slow

If `huggingface-cli` is slow, use a mirror or download the safetensors file directly:

```powershell
huggingface-cli download Qwen/Qwen3-ASR-0.6B --local-dir Qwen3-ASR-0.6B --resume-download
```

The `--resume-download` flag lets you retry interrupted downloads.

### Microphone not working in Gradio

Browser microphone access requires a secure context (HTTPS or localhost). Make sure you're accessing:

- `http://127.0.0.1:7860` (localhost is allowed), or
- Use `--share` for a public HTTPS URL

### Import errors for `evdev`

The `evdev` package is Linux-only. If pip tries to install it on Windows and fails, install without it:

```powershell
pip install numpy soundfile sounddevice scipy pyperclip qwen-asr transformers tokenizers torch
```

The transcription scripts (`scripts/transcribe.py`, `scripts/transcribe_gradio.py`) do not use evdev.
