#!/usr/bin/env python3
"""
File-based audio transcription using Qwen3-ASR-0.6B.

Usage:
    python transcribe.py audio.mp3
    python transcribe.py audio.mp3 -o transcript.txt
    python transcribe.py audio.mp3 --language English
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample

TARGET_SR = 16000
PROJECT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = str(PROJECT_DIR / "Qwen3-ASR-0.6B")


def load_model(model_path):
    import torch
    from qwen_asr import Qwen3ASRModel

    print(f"Loading Qwen3-ASR from {model_path}...", file=sys.stderr)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    model = Qwen3ASRModel.from_pretrained(
        model_path, dtype=torch.bfloat16,
        device_map=device, max_new_tokens=4096,
    )
    print(f"Model loaded on {device}.", file=sys.stderr)
    return model


def load_audio(path):
    audio, sr = sf.read(path, dtype="float32")
    # Convert stereo to mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    # Resample to 16kHz
    if sr != TARGET_SR:
        n = int(len(audio) * TARGET_SR / sr)
        audio = resample(audio, n).astype(np.float32)
        sr = TARGET_SR
    return audio, sr


def transcribe_chunks(model, audio, sr, language, chunk_seconds=30):
    """Transcribe audio in chunks to handle long files."""
    chunk_size = chunk_seconds * sr
    total_samples = len(audio)
    chunks = []

    for start in range(0, total_samples, chunk_size):
        end = min(start + chunk_size, total_samples)
        chunk = audio[start:end]
        # Skip very short chunks (< 0.3s)
        if len(chunk) < sr * 0.3:
            continue
        chunks.append((start, chunk))

    results = []
    for i, (start, chunk) in enumerate(chunks):
        t0 = time.time()
        out = model.transcribe(audio=(chunk, sr), language=language)
        elapsed = time.time() - t0
        if out:
            text = out[0].text
            lang = out[0].language
            timestamp = start / sr
            print(
                f"  Chunk {i + 1}/{len(chunks)} "
                f"[{timestamp:.1f}s - {(start + len(chunk)) / sr:.1f}s] "
                f"({elapsed:.1f}s) lang={lang}",
                file=sys.stderr,
            )
            results.append(text)
        else:
            print(f"  Chunk {i + 1}/{len(chunks)} — no speech detected", file=sys.stderr)

    return " ".join(results)


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio files using Qwen3-ASR-0.6B")
    parser.add_argument("file", help="Audio file to transcribe (mp3, wav, flac, etc.)")
    parser.add_argument("-o", "--output", help="Write transcript to file instead of stdout")
    parser.add_argument("--model", default=MODEL_PATH, help="Path to Qwen3-ASR model")
    parser.add_argument("--language", default="English", help="Language hint (default: English)")
    parser.add_argument("--chunk", type=int, default=30, help="Chunk length in seconds (default: 30)")
    args = parser.parse_args()

    audio_path = Path(args.file)
    if not audio_path.exists():
        print(f"Error: {audio_path} not found", file=sys.stderr)
        sys.exit(1)

    model = load_model(args.model)

    print(f"Loading audio: {audio_path}", file=sys.stderr)
    audio, sr = load_audio(audio_path)
    duration = len(audio) / sr
    print(f"Duration: {duration:.1f}s ({duration / 60:.1f} min)", file=sys.stderr)

    print("Transcribing...", file=sys.stderr)
    t0 = time.time()
    transcript = transcribe_chunks(model, audio, sr, args.language, args.chunk)
    total = time.time() - t0
    print(f"Done in {total:.1f}s (RTF: {total / duration:.2f}x)", file=sys.stderr)

    if args.output:
        Path(args.output).write_text(transcript)
        print(f"Saved to {args.output}", file=sys.stderr)
    else:
        print(transcript)


if __name__ == "__main__":
    main()
