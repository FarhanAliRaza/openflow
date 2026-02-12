import signal
import subprocess
import threading
import time

from voice_type.asr import ASRModel
from voice_type.audio import VoiceRecorder
from voice_type.config import IS_LINUX, MODEL_PATH, TARGET_SR
from voice_type.key_monitor import make_key_monitor
from voice_type.notify import notify
from voice_type.text_inject import type_text


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class VoiceTypeDaemon:
    def __init__(self, device=None, key="x", modifier="cmd",
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

        if IS_LINUX:
            self._check_ydotool()

        self.running = True
        self.key_monitor = make_key_monitor(
            on_press=self._start_recording,
            on_release=self._stop_and_transcribe,
            key_char=self.key,
            modifier=self.modifier,
        )
        self.key_monitor.start()

        mod_label = {"cmd": "Win/Cmd", "win": "Win/Cmd", "super": "Win/Cmd",
                     "ctrl": "Ctrl", "alt": "Alt",
                     "shift": "Shift"}.get(self.modifier.lower(), self.modifier)
        inject_mode = "ydotool" if IS_LINUX else "clipboard paste"
        notify("Voice Type",
               f"Push-to-talk: hold {mod_label}+{self.key.upper()}",
               timeout=4000)
        print(f"Text injection: {inject_mode}")
        print("Press Ctrl+C to stop.")

        signal.signal(signal.SIGINT, lambda *_: self._signal_stop())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: self._signal_stop())

        try:
            self._stop_event.wait()
        finally:
            self._cleanup()

    def _check_ydotool(self):
        try:
            subprocess.run(
                ["ydotool", "type", "--", ""],
                timeout=3,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("WARNING: ydotool not available.")
            print("  Install: sudo apt install ydotool")
            print("  Enable:  sudo systemctl enable --now ydotool")
            print("  Fallback: text will be copied to clipboard.\n")

    def _start_recording(self):
        self.recording = True
        self.recorder.start()
        notify("Recording...", "Release to stop", timeout=30000)
        print(f"[{time.strftime('%H:%M:%S')}] Recording started")

    def _stop_and_transcribe(self):
        self.recording = False
        self.processing = True
        notify("Transcribing...", "", timeout=5000)
        print(f"[{time.strftime('%H:%M:%S')}] Stopped, transcribing...")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self):
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
                status = "typed" if ok else "failed"
                notify("Voice Type",
                       f"{text[:100]}  [{elapsed:.0f}ms]", timeout=4000)
                print(f"[{time.strftime('%H:%M:%S')}] {status}: "
                      f"\"{text}\" ({duration:.1f}s, {elapsed:.0f}ms, {lang})")
            else:
                notify("Voice Type", "No speech detected", timeout=2000)
                print(f"[{time.strftime('%H:%M:%S')}] No speech ({duration:.1f}s)")

        except Exception as e:
            notify("Voice Type Error", str(e))
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
        notify("Voice Type", "Stopped", timeout=2000)
        print("Shutdown.")
