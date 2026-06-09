import shutil
import signal
import threading
import time

from voice_type.asr import ASRModel, looks_like_context_echo
from voice_type.audio import VoiceRecorder
from voice_type.cleanup import strip_fillers
from voice_type.config import IS_LINUX, MODEL_PATH, TARGET_SR
from voice_type.context import active_claude_md
from voice_type.key_monitor import make_key_monitor
from voice_type.notify import notify
from voice_type.refine import claude_available, get_vocab, refine_text
from voice_type.text_inject import ensure_ydotoold, stop_ydotoold, type_text


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class VoiceTypeDaemon:
    def __init__(self, device=None, key="x", modifier="cmd",
                 model_path=MODEL_PATH, refine=False, refine_model="haiku",
                 debug=False):
        self.asr = ASRModel(model_path)
        self.recorder = VoiceRecorder(device=device)
        self.recording = False
        self.processing = False
        self.running = False
        self.key = key
        self.modifier = modifier
        self.key_monitor = None
        self._stop_event = threading.Event()
        self._ydotool_ready = False  # set in _check_ydotool (Linux only)

        # Context from the focused app's folder, captured at record start.
        # Its CLAUDE.md feeds two things: ASR vocabulary biasing (always, even
        # offline) and the claude CLI rewrite (when claude is installed).
        self.refine_enabled = refine and claude_available()
        self.refine_model = refine_model
        self.debug = debug
        self._claude_md = None      # CLAUDE.md path for the focused folder
        self._asr_vocab = ""        # technical terms extracted from it
        self._ctx_thread = None     # background lookup of the above

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
        if IS_LINUX:
            inject_mode = ("ydotool keystrokes" if self._ydotool_ready
                           else "clipboard paste (ydotoold missing — "
                                "install it for direct typing)")
        else:
            inject_mode = "clipboard paste"
        print(f"Text injection: {inject_mode}")
        if self.refine_enabled:
            print(f"Refinement: claude CLI ({self.refine_model}) — ON, "
                  "applied when the focused app's folder has a CLAUDE.md "
                  "(adds ~5-7s/utterance)")
        else:
            print("Refinement: off (ASR vocab biasing still active; "
                  "pass --refine to enable claude polish)")
        print("Press Ctrl+C to stop.")

        signal.signal(signal.SIGINT, lambda *_: self._signal_stop())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: self._signal_stop())

        try:
            self._stop_event.wait()
        finally:
            self._cleanup()

    def _check_ydotool(self):
        if shutil.which("ydotool") is None:
            print("WARNING: ydotool not available.")
            print("  Install: sudo apt install ydotool ydotoold")
            print("  Fallback: text will be copied to clipboard.\n")
            return
        # Bring up the persistent uinput backend so keystrokes don't drop
        # characters (see text_inject.ensure_ydotoold).
        self._ydotool_ready = ensure_ydotoold()

    def _start_recording(self):
        self.recording = True
        self.recorder.start()
        notify("Recording...", "Release to stop", timeout=30000)
        print(f"[{time.strftime('%H:%M:%S')}] Recording started")

        # Resolve the focused app's CLAUDE.md now (it has focus while the
        # hotkey is held) and do it off-thread so it overlaps with speaking.
        self._claude_md = None
        self._asr_vocab = ""
        self._ctx_thread = threading.Thread(
            target=self._capture_context, daemon=True)
        self._ctx_thread.start()

    def _capture_context(self):
        try:
            self._claude_md = active_claude_md(debug=self.debug)
            self._asr_vocab = (get_vocab(self._claude_md, model=self.refine_model,
                                         debug=self.debug)
                               if self._claude_md else "")
        except Exception as e:
            if self.debug:
                print(f"[debug] context capture failed: {e!r}")
            self._claude_md = None
            self._asr_vocab = ""

    def _stop_and_transcribe(self):
        self.recording = False
        self.processing = True
        print(f"[{time.strftime('%H:%M:%S')}] Stopped, transcribing...")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self):
        try:
            audio = self.recorder.stop()
            duration = len(audio) / TARGET_SR

            if len(audio) < TARGET_SR * 0.3:
                print(f"[{time.strftime('%H:%M:%S')}] Too short: {duration:.1f}s")
                return

            # The folder lookup was kicked off at record start; make sure it
            # finished so we can bias ASR toward the project's vocabulary.
            if self._ctx_thread:
                self._ctx_thread.join(timeout=1.5)
            if self.debug and self._asr_vocab:
                print(f"[debug] ASR vocab ({len(self._asr_vocab)} chars): "
                      f"{self._asr_vocab[:200]}...")

            t0 = time.perf_counter()
            text, lang = self.asr.transcribe(audio, TARGET_SR,
                                             context=self._asr_vocab)
            elapsed = (time.perf_counter() - t0) * 1000

            # Silent/garbled audio makes Qwen3-ASR echo the vocab context back
            # as the "transcription". Drop it rather than typing the term list.
            if self._asr_vocab and looks_like_context_echo(text, self._asr_vocab):
                print(f"[{time.strftime('%H:%M:%S')}] Discarded context echo "
                      f"({duration:.1f}s) — likely silent or unintelligible audio")
                return

            text = strip_fillers(text)  # always-on, fast filler removal
            if text.strip():
                final = self._maybe_refine(text)
                ok = type_text(final)
                status = "typed" if ok else "failed"
                print(f"[{time.strftime('%H:%M:%S')}] {status}: "
                      f"\"{final}\" ({duration:.1f}s, {elapsed:.0f}ms, {lang})")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] No speech ({duration:.1f}s)")

        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.processing = False

    def _maybe_refine(self, text):
        """Rewrite `text` via claude when the focused folder has a CLAUDE.md.

        Falls back to the raw transcription if refinement is off, no CLAUDE.md
        was found, or the CLI call fails.
        """
        if not self.refine_enabled:
            return text
        if not self._claude_md:
            if self.debug:
                print("[debug] no CLAUDE.md for focused window — "
                      "typing raw transcription")
            return text

        t0 = time.perf_counter()
        refined = refine_text(text, self._asr_vocab, model=self.refine_model,
                              debug=self.debug)
        elapsed = (time.perf_counter() - t0) * 1000
        if refined != text:
            print(f"[{time.strftime('%H:%M:%S')}] refined via "
                  f"{self._claude_md} ({elapsed:.0f}ms)")
        elif self.debug:
            print(f"[debug] refinement returned unchanged text ({elapsed:.0f}ms)")
        return refined

    def _signal_stop(self):
        self.running = False
        self._stop_event.set()

    def _cleanup(self):
        if self.key_monitor:
            self.key_monitor.stop()
        if IS_LINUX:
            stop_ydotoold()
        print("Shutdown.")
