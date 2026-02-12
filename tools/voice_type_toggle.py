#!/usr/bin/env python3
"""
System-wide voice typing daemon using Qwen3-ASR (toggle mode).

Runs a socket-based daemon that toggles recording on/off via commands.
Bind a keyboard shortcut (e.g. in GNOME Settings) to send the --toggle command.

Usage:
  # Start the toggle daemon:
  python tools/voice_type_toggle.py

  # Toggle recording (bind this to a keyboard shortcut):
  python tools/voice_type_toggle.py --toggle

  # Query status:
  python tools/voice_type_toggle.py --status

  # Stop the daemon:
  python tools/voice_type_toggle.py --stop

Setup (one-time):
  1. Install ydotool (for auto-typing into focused window):
       sudo apt install ydotool
       sudo systemctl enable --now ydotool
       sudo usermod -aG input $USER   # then log out & back in

  2. Bind a keyboard shortcut in GNOME Settings -> Keyboard -> Custom Shortcuts:
       Name:     Voice Type
       Command:  /full/path/to/python3 /full/path/to/tools/voice_type_toggle.py --toggle
       Shortcut: Super+V

  3. Start the daemon:
       python tools/voice_type_toggle.py

Flow:
  Press shortcut -> "Recording..." -> speak -> press again -> text appears at cursor

Dependencies: pip install qwen-asr sounddevice numpy scipy torch
System:       ydotool (recommended), wl-copy, notify-send
"""

import argparse
import os
import signal
import socket
import sys
from pathlib import Path

from voice_type import (
    ASRModel,
    VoiceRecorder,
    list_devices,
    load_saved_device,
    notify,
    resample_audio,
    save_device,
    select_device,
    type_text,
    CONFIG_FILE,
    MODEL_PATH,
    TARGET_SR,
)

SOCKET_PATH = "/tmp/voice_type_global.sock"
PID_PATH = "/tmp/voice_type_global.pid"


# ---------------------------------------------------------------------------
# Toggle daemon
# ---------------------------------------------------------------------------

class VoiceTypeToggleDaemon:
    def __init__(self, device=None, model_path=MODEL_PATH):
        self.asr = ASRModel(model_path)
        self.recorder = VoiceRecorder(device=device)
        self.recording = False
        self.processing = False
        self.running = False

    def start(self):
        # Clean stale socket
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

        self.asr.load()
        ydotool_ok = self._check_ydotool()

        # PID file
        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))

        # Unix socket
        self.running = True
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o777)
        sock.listen(5)
        sock.settimeout(1.0)

        mode = "ydotool (auto-type)" if ydotool_ok else "clipboard (Ctrl+V)"

        print(f"\nBind your shortcut to:")
        print(f"  python3 {Path(__file__).resolve()} --toggle")
        print()

        notify("Voice Type", f"Toggle mode ready — {mode}", timeout=4000)
        print(f"Listening on {SOCKET_PATH}")
        print(f"Text injection: {mode}")

        signal.signal(signal.SIGTERM, lambda *_: self._signal_stop())
        signal.signal(signal.SIGINT, lambda *_: self._signal_stop())

        try:
            while self.running:
                try:
                    conn, _ = sock.accept()
                    data = conn.recv(64).decode().strip()
                    if data == "toggle":
                        self.handle_toggle()
                        conn.sendall(b"ok\n")
                    elif data == "status":
                        state = ("recording" if self.recording
                                 else "processing" if self.processing
                                 else "idle")
                        conn.sendall(f"{state}\n".encode())
                    elif data == "stop":
                        conn.sendall(b"stopping\n")
                        self.running = False
                    conn.close()
                except socket.timeout:
                    continue
                except OSError:
                    if self.running:
                        raise
        finally:
            self._cleanup(sock)

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

    def handle_toggle(self):
        if self.processing:
            return
        if not self.recording:
            self._start_recording()
        else:
            self._stop_and_transcribe()

    def _start_recording(self):
        import time
        self.recording = True
        self.recorder.start()
        notify("Recording...", "Press shortcut again to stop", timeout=30000)
        print(f"[{time.strftime('%H:%M:%S')}] Recording started")

    def _stop_and_transcribe(self):
        import time
        import threading
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

    def _cleanup(self, sock):
        sock.close()
        for p in (SOCKET_PATH, PID_PATH):
            if os.path.exists(p):
                os.unlink(p)
        notify("Voice Type", "Daemon stopped", timeout=2000)
        print("Shutdown.")


# ---------------------------------------------------------------------------
# Client commands (--toggle, --stop, --status)
# ---------------------------------------------------------------------------

def send_command(cmd):
    """Send a command to the running daemon via Unix socket."""
    if not os.path.exists(SOCKET_PATH):
        print("Daemon not running. Start with: python tools/voice_type_toggle.py")
        sys.exit(1)
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(SOCKET_PATH)
        sock.sendall(cmd.encode() + b"\n")
        resp = sock.recv(64).decode().strip()
        sock.close()
        return resp
    except ConnectionRefusedError:
        os.unlink(SOCKET_PATH)
        print("Daemon not running (stale socket removed).")
        sys.exit(1)
    except socket.timeout:
        print("Daemon not responding.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="System-wide voice typing — toggle mode (Qwen3-ASR)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--toggle", action="store_true",
                        help="Toggle recording on/off")
    parser.add_argument("--stop", action="store_true",
                        help="Stop the running daemon")
    parser.add_argument("--status", action="store_true",
                        help="Query daemon status")
    parser.add_argument("--list-devices", action="store_true",
                        help="List audio input devices and exit")
    parser.add_argument("--device", type=int, default=None,
                        help="Audio input device index (see --list-devices)")
    parser.add_argument("--model", type=str, default=MODEL_PATH,
                        help=f"Path to Qwen3-ASR model directory (default: {MODEL_PATH})")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return
    elif args.toggle:
        send_command("toggle")
    elif args.stop:
        send_command("stop")
        print("Daemon stopping.")
    elif args.status:
        resp = send_command("status")
        print(f"Daemon status: {resp}")
    else:
        # Check if already running
        if os.path.exists(SOCKET_PATH):
            try:
                resp = send_command("status")
                print(f"Daemon already running (status: {resp}).")
                print("Use --stop to stop it first.")
                sys.exit(1)
            except SystemExit:
                pass  # stale socket, cleaned up by send_command

        device = args.device
        if device is None:
            device = select_device()

        daemon = VoiceTypeToggleDaemon(device=device, model_path=args.model)
        daemon.start()


if __name__ == "__main__":
    main()
