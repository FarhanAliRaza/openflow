#!/usr/bin/env python3
"""
System-wide voice typing daemon using Qwen3-ASR (push-to-talk mode).

Records audio while a keyboard shortcut is held, transcribes on release,
and types the result directly into whatever window is currently focused.

Usage:
  # Start push-to-talk (hold Win+X to record, release to transcribe):
  python tools/voice_type.py

  # Custom key combo:
  python tools/voice_type.py --key KEY_V --modifier KEY_LEFTCTRL

  # List audio devices:
  python tools/voice_type.py --list-devices

Setup (one-time):
  1. Install ydotool (for auto-typing into focused window):
       sudo apt install ydotool
       sudo systemctl enable --now ydotool
       sudo usermod -aG input $USER   # then log out & back in

  2. Start the daemon:
       python tools/voice_type.py

Flow:
  Hold Win+X -> "Recording..." -> speak -> release -> text appears at cursor

Dependencies: pip install qwen-asr sounddevice numpy scipy torch evdev
System:       ydotool (recommended), wl-copy, notify-send
"""

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "voice-type"
CONFIG_FILE = CONFIG_DIR / "config.json"
TARGET_SR = 16000

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
MODEL_PATH = str(PROJECT_DIR / "Qwen3-ASR-0.6B")


def load_saved_device():
    """Load previously selected device from config."""
    import json
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return cfg.get("device_index"), cfg.get("device_name", "")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None, ""


def save_device(device_index, device_name=""):
    """Save selected device to config."""
    import json
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"device_index": device_index, "device_name": device_name}, f)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def notify(title, body="", urgency="normal", timeout=3000):
    """Send desktop notification (does not steal focus)."""
    import subprocess
    try:
        subprocess.Popen(
            ["notify-send", "--urgency", urgency,
             "--expire-time", str(timeout),
             "--app-name", "Voice Type",
             title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def type_text(text):
    """Type text into the currently focused window.

    Uses ydotool type to simulate real keystrokes — works in every app
    regardless of paste shortcut (Ctrl+V, Ctrl+Shift+V, etc.).
    Falls back to clipboard if ydotool is unavailable.
    Returns True if text was delivered somehow.
    """
    import subprocess
    if not text.strip():
        return False

    # Primary: ydotool type — simulates real keystrokes, works everywhere
    try:
        r = subprocess.run(
            ["ydotool", "type", "--key-delay", "2", "--", text],
            timeout=10, capture_output=True,
        )
        if r.returncode == 0:
            return True
        print(f"  ydotool type failed (rc={r.returncode}): "
              f"{r.stderr.decode().strip()}")
    except FileNotFoundError:
        print("  ydotool not found")
    except subprocess.TimeoutExpired:
        print("  ydotool type timed out")

    # Fallback: clipboard only, user pastes manually
    try:
        subprocess.run(["wl-copy", "--", text], check=True, timeout=5)
        notify("Voice Type",
               "Copied to clipboard — paste with Ctrl+V or Ctrl+Shift+V",
               timeout=5000)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    return False


def resample_audio(audio, orig_sr, target_sr):
    import numpy as np
    from scipy import signal as scipy_signal
    if orig_sr == target_sr:
        return audio
    n = int(len(audio) * target_sr / orig_sr)
    return scipy_signal.resample(audio, n).astype(np.float32)


# ---------------------------------------------------------------------------
# Audio recorder
# ---------------------------------------------------------------------------

class VoiceRecorder:
    def __init__(self, device=None):
        import sounddevice as sd
        self.device = device
        if device is not None:
            dev_info = sd.query_devices(device)
        else:
            dev_info = sd.query_devices(sd.default.device[0])
        import queue
        self.native_sr = int(dev_info["default_samplerate"])
        self.recording = False
        self.audio_queue = queue.Queue()
        self.stream = None

    def start(self):
        import queue
        import sounddevice as sd
        if self.recording:
            return
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        self.recording = True
        self.stream = sd.InputStream(
            samplerate=self.native_sr,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
            blocksize=1024,
        )
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_queue.put(indata.copy())

    def stop(self):
        import numpy as np
        if not self.recording:
            return np.array([], dtype=np.float32)
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        chunks = []
        while not self.audio_queue.empty():
            try:
                chunks.append(self.audio_queue.get_nowait())
            except queue.Empty:
                break
        if chunks:
            import numpy as np
            audio = np.concatenate(chunks).flatten()
            return resample_audio(audio, self.native_sr, TARGET_SR)
        return np.array([], dtype=np.float32)


# ---------------------------------------------------------------------------
# Qwen3-ASR model (loaded once, stays in GPU memory)
# ---------------------------------------------------------------------------

class ASRModel:
    def __init__(self, model_path):
        self.model_path = model_path
        self.model = None

    def load(self):
        import torch
        from qwen_asr import Qwen3ASRModel

        print(f"Loading Qwen3-ASR from {self.model_path}...")
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model = Qwen3ASRModel.from_pretrained(
            self.model_path,
            dtype=torch.bfloat16,
            device_map=device,
            max_new_tokens=4096,
        )
        print(f"Model loaded on {device}.")

    def transcribe(self, audio, sr):
        """Transcribe a numpy audio array. Returns (text, language)."""
        if self.model is None:
            raise RuntimeError("Model not loaded")
        results = self.model.transcribe(audio=(audio, sr), language="English")
        if not results:
            return "", ""
        return results[0].text, results[0].language


# ---------------------------------------------------------------------------
# Push-to-talk key monitor (evdev)
# ---------------------------------------------------------------------------

class KeyMonitor:
    """Monitors keyboard via evdev for push-to-talk.

    Watches for a modifier+key combo. When the key is pressed (with modifier
    held), calls on_press. When the key is released, calls on_release.
    """

    def __init__(self, on_press, on_release, key_name="KEY_X", modifier="KEY_LEFTMETA"):
        import evdev
        self.on_press = on_press
        self.on_release = on_release
        self.key_code = getattr(evdev.ecodes, key_name)
        self.mod_code = getattr(evdev.ecodes, modifier)
        self.mod_held = False
        self.key_active = False
        self.running = False

    def find_keyboards(self):
        """Find keyboard input devices."""
        import evdev
        keyboards = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            caps = dev.capabilities(verbose=False)
            # EV_KEY = 1; check device has key events and has our key
            if 1 in caps and self.key_code in caps[1]:
                keyboards.append(dev)
        return keyboards

    def start(self):
        """Start monitoring in a background thread."""
        self.running = True
        keyboards = self.find_keyboards()
        if not keyboards:
            print("WARNING: No keyboard devices found for push-to-talk.")
            print("  Make sure your user is in the 'input' group:")
            print("    sudo usermod -aG input $USER")
            return
        names = [kb.name for kb in keyboards]
        print(f"Push-to-talk: monitoring {len(keyboards)} keyboard(s): {names}")
        thread = threading.Thread(target=self._monitor_loop, args=(keyboards,),
                                  daemon=True)
        thread.start()

    def _monitor_loop(self, keyboards):
        """Read events from all keyboards using select."""
        import select
        import evdev

        fds = {kb.fd: kb for kb in keyboards}
        while self.running:
            r, _, _ = select.select(fds.keys(), [], [], 1.0)
            for fd in r:
                dev = fds[fd]
                try:
                    for event in dev.read():
                        if event.type != evdev.ecodes.EV_KEY:
                            continue
                        self._handle_key(event.code, event.value)
                except OSError:
                    pass

    def _handle_key(self, code, value):
        # value: 0=release, 1=press, 2=repeat
        if code == self.mod_code:
            self.mod_held = value != 0
            # If modifier released while recording, stop
            if value == 0 and self.key_active:
                self.key_active = False
                self.on_release()
        elif code == self.key_code:
            if value == 1 and self.mod_held and not self.key_active:
                self.key_active = True
                self.on_press()
            elif value == 0 and self.key_active:
                self.key_active = False
                self.on_release()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# Daemon (push-to-talk only)
# ---------------------------------------------------------------------------

class VoiceTypeDaemon:
    def __init__(self, device=None, key="KEY_X", modifier="KEY_LEFTMETA",
                 model_path=MODEL_PATH):
        self.asr = ASRModel(model_path)
        self.recorder = VoiceRecorder(device=device)
        self.recording = False
        self.processing = False
        self.running = False
        self.key = key
        self.modifier = modifier
        self.key_monitor = None
        self._stop_event = threading.Event()

    def start(self):
        self.asr.load()
        ydotool_ok = self._check_ydotool()

        self.running = True

        self.key_monitor = KeyMonitor(
            on_press=self._start_recording,
            on_release=self._stop_and_transcribe,
            key_name=self.key,
            modifier=self.modifier,
        )
        self.key_monitor.start()

        mod_friendly = self.modifier.replace("KEY_", "").replace("LEFT", "").replace("RIGHT", "").title()
        key_friendly = self.key.replace("KEY_", "")
        mode = "ydotool (auto-type)" if ydotool_ok else "clipboard (Ctrl+V)"

        notify("Voice Type",
               f"Push-to-talk: hold {mod_friendly}+{key_friendly}\nMode: {mode}",
               timeout=4000)
        print(f"Push-to-talk: hold {mod_friendly}+{key_friendly} to record")
        print(f"Text injection: {mode}")
        print("Press Ctrl+C to stop.")

        signal.signal(signal.SIGTERM, lambda *_: self._signal_stop())
        signal.signal(signal.SIGINT, lambda *_: self._signal_stop())

        try:
            self._stop_event.wait()
        finally:
            self._cleanup()

    def _check_ydotool(self):
        import subprocess
        try:
            subprocess.run(
                ["ydotool", "type", "--", ""],
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("WARNING: ydotool not available.")
            print("  Install: sudo apt install ydotool")
            print("  Enable:  sudo systemctl enable --now ydotool")
            print("  Add user to input group: sudo usermod -aG input $USER")
            print("  Fallback: text will be copied to clipboard.\n")
            return False

    def _start_recording(self):
        import time
        self.recording = True
        self.recorder.start()
        notify("Recording...", "Release to stop", timeout=30000)
        print(f"[{time.strftime('%H:%M:%S')}] Recording started")

    def _stop_and_transcribe(self):
        import time
        self.recording = False
        self.processing = True
        notify("Transcribing...", "", timeout=5000)
        print(f"[{time.strftime('%H:%M:%S')}] Stopped, transcribing...")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self):
        import time
        try:
            audio = self.recorder.stop()
            duration = len(audio) / TARGET_SR

            if len(audio) < TARGET_SR * 0.3:
                notify("Voice Type", f"Too short ({duration:.1f}s)", timeout=2000)
                print(f"[{time.strftime('%H:%M:%S')}] Too short: {duration:.1f}s")
                return

            t0 = time.perf_counter()
            text, lang = self.asr.transcribe(audio, TARGET_SR)
            elapsed = (time.perf_counter() - t0) * 1000

            if text.strip():
                ok = type_text(text)
                status = "typed" if ok else "clipboard"
                notify("Voice Type",
                       f"{text[:100]}  [{elapsed:.0f}ms]",
                       timeout=4000)
                print(f"[{time.strftime('%H:%M:%S')}] {status}: "
                      f"\"{text}\" ({duration:.1f}s, {elapsed:.0f}ms, {lang})")
            else:
                notify("Voice Type", "No speech detected", timeout=2000)
                print(f"[{time.strftime('%H:%M:%S')}] No speech ({duration:.1f}s)")

        except Exception as e:
            notify("Voice Type Error", str(e), urgency="critical")
            print(f"[{time.strftime('%H:%M:%S')}] Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.processing = False

    def _signal_stop(self):
        self.running = False
        self._stop_event.set()

    def _cleanup(self):
        if self.key_monitor:
            self.key_monitor.stop()
        notify("Voice Type", "Daemon stopped", timeout=2000)
        print("Shutdown.")


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------

def list_devices():
    """Print available audio input devices."""
    import sounddevice as sd
    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    print("\nAudio input devices:")
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            marker = " *" if i == default_idx else "  "
            sr = int(dev["default_samplerate"])
            print(f"{marker} [{i}] {dev['name']}  ({sr} Hz)")
    print(f"\n  * = default")
    print(f"\nUse --device <number> to select, or Enter for default.\n")


def select_device():
    """Interactive device selection at daemon startup.

    Remembers the previous choice in ~/.config/voice-type/config.json.
    """
    import sounddevice as sd
    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    input_devs = [(i, dev) for i, dev in enumerate(devices)
                  if dev["max_input_channels"] > 0]

    saved_idx, saved_name = load_saved_device()

    # Validate saved device still exists with the same name
    saved_valid = False
    if saved_idx is not None:
        try:
            dev = sd.query_devices(saved_idx)
            if dev["max_input_channels"] > 0 and dev["name"] == saved_name:
                saved_valid = True
        except Exception:
            pass

    if len(input_devs) <= 1:
        return None

    print("\nAudio input devices:")
    for i, dev in input_devs:
        markers = []
        if i == default_idx:
            markers.append("default")
        if saved_valid and i == saved_idx:
            markers.append("saved")
        tag = f" ({', '.join(markers)})" if markers else ""
        sr = int(dev["default_samplerate"])
        print(f"  [{i}] {dev['name']}  {sr} Hz{tag}")

    if saved_valid:
        prompt = f"Select device (Enter for saved [{saved_idx}]): "
    else:
        prompt = "Select device (Enter for default): "

    try:
        choice = input(prompt).strip()
        if choice == "":
            idx = saved_idx if saved_valid else None
        else:
            idx = int(choice)
    except (ValueError, EOFError):
        idx = saved_idx if saved_valid else None

    # Save the choice
    if idx is not None:
        try:
            name = sd.query_devices(idx)["name"]
        except Exception:
            name = ""
        save_device(idx, name)
        print(f"  Saved device [{idx}] to {CONFIG_FILE}")
    elif saved_valid:
        # User pressed Enter, keep using saved
        idx = saved_idx

    return idx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="System-wide voice typing — push-to-talk mode (Qwen3-ASR)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list-devices", action="store_true",
                        help="List audio input devices and exit")
    parser.add_argument("--device", type=int, default=None,
                        help="Audio input device index (see --list-devices)")
    parser.add_argument("--key", type=str, default="KEY_X",
                        help="evdev key name for push-to-talk (default: KEY_X)")
    parser.add_argument("--modifier", type=str, default="KEY_LEFTMETA",
                        help="evdev modifier key (default: KEY_LEFTMETA = Super/Win)")
    parser.add_argument("--model", type=str, default=MODEL_PATH,
                        help=f"Path to Qwen3-ASR model directory (default: {MODEL_PATH})")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    device = args.device
    if device is None:
        device = select_device()

    daemon = VoiceTypeDaemon(
        device=device,
        key=args.key,
        modifier=args.modifier,
        model_path=args.model,
    )
    daemon.start()


if __name__ == "__main__":
    main()
