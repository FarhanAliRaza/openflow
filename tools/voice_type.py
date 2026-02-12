#!/usr/bin/env python3
"""
Voice typing using Qwen3-ASR (push-to-talk) — works on Linux, macOS, Windows.

Auto-detects the platform:
  Linux:   evdev for keyboard monitoring, ydotool for text injection
  macOS:   pynput for keyboard + clipboard paste (Cmd+V)
  Windows: pynput for keyboard + clipboard paste (Ctrl+V)

Usage:
  python tools/voice_type_cross.py
  python tools/voice_type_cross.py --key x --modifier cmd
  python tools/voice_type_cross.py --list-devices

Platform setup:
  Linux:   sudo apt install ydotool && sudo systemctl enable --now ydotool
           sudo usermod -aG input $USER  (then log out & back in)
  macOS:   Grant Accessibility permissions to your terminal
           (System Settings > Privacy & Security > Accessibility)
  Windows: No special setup needed.

Flow:
  Hold Win/Cmd+X -> speak -> release -> text appears at cursor
"""

import argparse
import platform
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TARGET_SR = 16000
SYSTEM = platform.system()  # 'Linux', 'Darwin', 'Windows'
IS_LINUX = SYSTEM == "Linux"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
MODEL_PATH = str(PROJECT_DIR / "Qwen3-ASR-0.6B")

CONFIG_DIR = Path.home() / ".config" / "voice-type"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def load_saved_device():
    import json
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        return cfg.get("device_index"), cfg.get("device_name", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return None, ""


def save_device(device_index, device_name=""):
    import json
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump({"device_index": device_index, "device_name": device_name}, f)


# ---------------------------------------------------------------------------
# Notifications (platform-adaptive)
# ---------------------------------------------------------------------------

def notify(title, body="", timeout=3000):
    """Desktop notification (best-effort, never blocks)."""
    try:
        if SYSTEM == "Linux":
            subprocess.Popen(
                ["notify-send", "--expire-time", str(timeout),
                 "--app-name", "Voice Type", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif SYSTEM == "Darwin":
            t = title.replace('"', '\\"')
            b = body.replace('"', '\\"')
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "{b}" with title "{t}"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Text injection (platform-adaptive)
# ---------------------------------------------------------------------------

def type_text_linux(text):
    """Linux: ydotool for real keystrokes, clipboard fallback."""
    if not text.strip():
        return False
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

    # Fallback: clipboard
    try:
        subprocess.run(["wl-copy", "--", text], check=True, timeout=5)
        notify("Voice Type",
               "Copied to clipboard — paste with Ctrl+V",
               timeout=5000)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return False


def type_text_pynput(text):
    """macOS/Windows: clipboard + paste shortcut."""
    if not text.strip():
        return False
    import pyperclip
    from pynput.keyboard import Controller, Key

    pyperclip.copy(text)
    time.sleep(0.05)

    kb = Controller()
    paste_mod = Key.cmd if SYSTEM == "Darwin" else Key.ctrl
    with kb.pressed(paste_mod):
        kb.press("v")
        kb.release("v")
    return True


def type_text(text):
    if IS_LINUX:
        return type_text_linux(text)
    return type_text_pynput(text)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def resample_audio(audio, orig_sr, target_sr):
    import numpy as np
    from scipy import signal as scipy_signal
    if orig_sr == target_sr:
        return audio
    n = int(len(audio) * target_sr / orig_sr)
    return scipy_signal.resample(audio, n).astype(np.float32)


class VoiceRecorder:
    def __init__(self, device=None):
        import sounddevice as sd
        import queue
        self.device = device
        dev_info = (sd.query_devices(device) if device is not None
                    else sd.query_devices(sd.default.device[0]))
        self.native_sr = int(dev_info["default_samplerate"])
        self.recording = False
        self.audio_queue = queue.Queue()
        self.stream = None

    def start(self):
        import sounddevice as sd
        if self.recording:
            return
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except Exception:
                break
        self.recording = True
        self.stream = sd.InputStream(
            samplerate=self.native_sr, channels=1, dtype="float32",
            device=self.device, callback=self._callback, blocksize=1024,
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
            except Exception:
                break
        if chunks:
            audio = np.concatenate(chunks).flatten()
            return resample_audio(audio, self.native_sr, TARGET_SR)
        return np.array([], dtype=np.float32)


# ---------------------------------------------------------------------------
# ASR Model
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
            self.model_path, dtype=torch.bfloat16,
            device_map=device, max_new_tokens=4096,
        )
        print(f"Model loaded on {device}.")

    def transcribe(self, audio, sr):
        if self.model is None:
            raise RuntimeError("Model not loaded")
        results = self.model.transcribe(audio=(audio, sr), language="English")
        if not results:
            return "", ""
        return results[0].text, results[0].language


# ---------------------------------------------------------------------------
# Key monitor — Linux (evdev)
# ---------------------------------------------------------------------------

# Mapping from friendly names to evdev key names
_EVDEV_KEY_MAP = {
    "cmd": "KEY_LEFTMETA", "win": "KEY_LEFTMETA", "super": "KEY_LEFTMETA",
    "ctrl": "KEY_LEFTCTRL", "alt": "KEY_LEFTALT", "shift": "KEY_LEFTSHIFT",
}

_EVDEV_MODIFIER_VARIANTS = {
    "KEY_LEFTMETA":  ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "KEY_LEFTCTRL":  ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "KEY_LEFTALT":   ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "KEY_LEFTSHIFT": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
}

class EvdevKeyMonitor:
    """Push-to-talk via evdev (Linux).

    Grabs keyboards at startup and proxies all events through a UInput
    virtual device.  The trigger key is consumed (not forwarded) while
    the modifier is held, so it never reaches the display server.
    All other keys pass through transparently — no stale state issues.
    Kernel auto-releases grabs + closes UInput if the process dies.
    """

    def __init__(self, on_press, on_release, key_char="x", modifier="cmd"):
        import evdev
        self.on_press = on_press
        self.on_release = on_release

        # Resolve modifier
        evdev_mod = _EVDEV_KEY_MAP.get(modifier.lower(), modifier.upper())
        if not evdev_mod.startswith("KEY_"):
            evdev_mod = "KEY_" + evdev_mod
        self.mod_codes = set()
        for name in _EVDEV_MODIFIER_VARIANTS.get(evdev_mod, (evdev_mod,)):
            self.mod_codes.add(getattr(evdev.ecodes, name))

        # Resolve key
        evdev_key = f"KEY_{key_char.upper()}"
        self.key_code = getattr(evdev.ecodes, evdev_key)

        self.mod_held = False
        self.key_active = False
        self.running = False
        self._uinput = None
        self._grabbed_fds = set()
        self._pending_mod_event = None

    def find_keyboards(self):
        import evdev
        keyboards = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            caps = dev.capabilities(verbose=False)
            if 1 in caps and self.key_code in caps[1]:
                keyboards.append(dev)
        return keyboards

    def start(self):
        import evdev
        self.running = True
        keyboards = self.find_keyboards()
        if not keyboards:
            print("WARNING: No keyboard devices found.")
            print("  Make sure your user is in the 'input' group:")
            print("    sudo usermod -aG input $USER")
            return

        grab_devices = [kb for kb in keyboards
                        if "keyboard" in kb.name.lower()]
        names = [kb.name for kb in keyboards]
        print(f"Push-to-talk: monitoring {len(keyboards)} device(s): {names}")

        if grab_devices:
            grab_names = [kb.name for kb in grab_devices]
            print(f"  Proxied keyboards: {grab_names}")
            self._uinput = evdev.UInput.from_device(
                *grab_devices, name="voice-type-proxy")
            for dev in grab_devices:
                dev.grab()
            self._grabbed_fds = {dev.fd for dev in grab_devices}

        thread = threading.Thread(target=self._monitor_loop, args=(keyboards,),
                                  daemon=True)
        thread.start()

    def _monitor_loop(self, keyboards):
        import select
        import evdev
        fds = {kb.fd: kb for kb in keyboards}
        while self.running:
            r, _, _ = select.select(fds.keys(), [], [], 1.0)
            for fd in r:
                dev = fds[fd]
                is_proxied = fd in self._grabbed_fds
                try:
                    for event in dev.read():
                        action = None
                        if event.type == evdev.ecodes.EV_KEY:
                            action = self._handle_key(event.code, event.value)

                        if is_proxied and self._uinput:
                            if action == "consume":
                                # Combo fired — discard buffered modifier
                                self._pending_mod_event = None
                            elif action == "buffer":
                                # Hold modifier, don't forward yet
                                self._pending_mod_event = event
                            else:
                                # Forward — flush pending modifier first
                                # if a real key arrives (not SYN etc.)
                                if (self._pending_mod_event is not None
                                        and event.type == evdev.ecodes.EV_KEY):
                                    self._uinput.write_event(
                                        self._pending_mod_event)
                                    self._pending_mod_event = None
                                self._uinput.write_event(event)
                except OSError:
                    pass

    def _handle_key(self, code, value):
        """Returns 'buffer', 'consume', or None (forward)."""
        if code in self.mod_codes:
            if value == 1:
                self.mod_held = True
                return "buffer"
            elif value == 0:
                self.mod_held = False
                if self.key_active:
                    self.key_active = False
                    self.on_release()
                    return "consume"  # mod UP during combo — consume
                return None  # normal mod UP — forward
            return None  # repeat
        elif code == self.key_code:
            if value == 1 and self.mod_held and not self.key_active:
                self.key_active = True
                self.on_press()
                return "consume"
            elif self.key_active:
                if value == 0:
                    self.key_active = False
                    self.on_release()
                return "consume"  # repeat or release during combo
        return None  # forward everything else

    def stop(self):
        self.running = False
        if self._uinput:
            try:
                self._uinput.close()
            except Exception:
                pass
            self._uinput = None


# ---------------------------------------------------------------------------
# Key monitor — macOS / Windows (pynput)
# ---------------------------------------------------------------------------

_PYNPUT_MODIFIER_ALIASES = {
    "cmd": "cmd", "win": "cmd", "super": "cmd",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift",
}


class PynputKeyMonitor:
    """Push-to-talk via pynput (macOS/Windows).

    Uses suppress=True so all keyboard events are consumed by pynput.
    Non-combo events are re-emitted via Controller.  The modifier press
    is buffered until we know whether the trigger key follows, preventing
    the OS from seeing a lone modifier tap during the combo.
    """

    def __init__(self, on_press, on_release, key_char="x", modifier="cmd"):
        from pynput import keyboard
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self.target_char = key_char.lower()
        target_kc = keyboard.KeyCode.from_char(self.target_char)
        self.target_vk = target_kc.vk if hasattr(target_kc, "vk") else None
        self.modifier_name = _PYNPUT_MODIFIER_ALIASES.get(
            modifier.lower(), modifier.lower())
        self.mod_held = False
        self.key_active = False
        self.listener = None
        self._controller = keyboard.Controller()
        self._pending_mod_key = None

    def _is_modifier(self, key):
        from pynput.keyboard import Key
        groups = {
            "cmd":   {Key.cmd, Key.cmd_l, Key.cmd_r},
            "ctrl":  {Key.ctrl, Key.ctrl_l, Key.ctrl_r},
            "alt":   {Key.alt, Key.alt_l, Key.alt_r},
            "shift": {Key.shift, Key.shift_l, Key.shift_r},
        }
        return key in groups.get(self.modifier_name, set())

    def _is_target(self, key):
        from pynput.keyboard import KeyCode
        if not isinstance(key, KeyCode):
            return False
        if key.char is not None:
            return key.char.lower() == self.target_char
        if (self.target_vk is not None
                and hasattr(key, "vk") and key.vk is not None):
            return key.vk == self.target_vk
        return False

    def _flush_pending(self):
        """Forward buffered modifier press to the OS."""
        if self._pending_mod_key is not None:
            self._controller.press(self._pending_mod_key)
            self._pending_mod_key = None

    def start(self):
        from pynput import keyboard
        self.listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=True,
        )
        self.listener.daemon = True
        self.listener.start()

        mod_label = {"cmd": "Win/Cmd", "ctrl": "Ctrl", "alt": "Alt",
                     "shift": "Shift"}.get(self.modifier_name, self.modifier_name)
        print(f"Push-to-talk: hold {mod_label}+{self.target_char.upper()}")

    def _on_press(self, key):
        if self._is_modifier(key):
            self.mod_held = True
            self._pending_mod_key = key
        elif self._is_target(key) and self.mod_held and not self.key_active:
            # Combo — consume both (discard buffered modifier)
            self._pending_mod_key = None
            self.key_active = True
            self.on_press_cb()
        else:
            # Not our combo — flush modifier and forward this key
            self._flush_pending()
            self._controller.press(key)

    def _on_release(self, key):
        if self._is_modifier(key):
            self.mod_held = False
            if self.key_active:
                # Combo ends — consume modifier release
                self.key_active = False
                self._pending_mod_key = None
                self.on_release_cb()
            else:
                # Normal modifier release — flush press + forward release
                self._flush_pending()
                self._controller.release(key)
        elif self._is_target(key) and self.key_active:
            # Combo ends via trigger key release — consume
            self.key_active = False
            self.on_release_cb()
        else:
            self._controller.release(key)

    def stop(self):
        if self.listener:
            self.listener.stop()


def make_key_monitor(on_press, on_release, key_char, modifier):
    """Create the right KeyMonitor for the current platform."""
    if IS_LINUX:
        return EvdevKeyMonitor(on_press, on_release, key_char, modifier)
    return PynputKeyMonitor(on_press, on_release, key_char, modifier)


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


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------

def list_devices():
    import sounddevice as sd
    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    print("\nAudio input devices:")
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            marker = " *" if i == default_idx else "  "
            sr = int(dev["default_samplerate"])
            print(f"{marker} [{i}] {dev['name']}  ({sr} Hz)")
    print("\n  * = default")
    print("\nUse --device <number> to select.\n")


def select_device():
    import sounddevice as sd
    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    input_devs = [(i, dev) for i, dev in enumerate(devices)
                  if dev["max_input_channels"] > 0]

    saved_idx, saved_name = load_saved_device()
    saved_valid = False
    if saved_idx is not None:
        try:
            dev = sd.query_devices(saved_idx)
            if dev["max_input_channels"] > 0 and dev["name"] == saved_name:
                saved_valid = True
        except Exception:
            pass

    # If saved device is still valid, use it silently
    if saved_valid:
        return saved_idx

    if len(input_devs) <= 1:
        return None

    print("\nAudio input devices:")
    for i, dev in input_devs:
        marker = " (default)" if i == default_idx else ""
        sr = int(dev["default_samplerate"])
        print(f"  [{i}] {dev['name']}  {sr} Hz{marker}")

    try:
        choice = input("Select device (Enter for default): ").strip()
        idx = None if choice == "" else int(choice)
    except (ValueError, EOFError):
        idx = None

    if idx is not None:
        try:
            name = sd.query_devices(idx)["name"]
        except Exception:
            name = ""
        save_device(idx, name)
        print(f"  Saved device [{idx}] to {CONFIG_FILE}")

    return idx


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Voice typing — push-to-talk (Qwen3-ASR). "
                    "Auto-detects platform: evdev on Linux, pynput elsewhere.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--list-devices", action="store_true",
                        help="List audio input devices and exit")
    parser.add_argument("--device", type=int, default=None,
                        help="Audio input device index")
    parser.add_argument("--key", type=str, default="x",
                        help="Key for push-to-talk (default: x)")
    parser.add_argument("--modifier", type=str, default="cmd",
                        help="Modifier: cmd/win/super, ctrl, alt, shift "
                             "(default: cmd = Win/Cmd)")
    parser.add_argument("--model", type=str, default=MODEL_PATH,
                        help=f"Path to Qwen3-ASR model (default: {MODEL_PATH})")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    backend = "evdev" if IS_LINUX else "pynput"
    print(f"Platform: {SYSTEM} (keyboard backend: {backend})\n")

    if SYSTEM == "Darwin":
        print("macOS: Ensure your terminal has Accessibility permissions")
        print("  (System Settings > Privacy & Security > Accessibility)\n")

    device = args.device if args.device is not None else select_device()

    daemon = VoiceTypeDaemon(
        device=device,
        key=args.key,
        modifier=args.modifier,
        model_path=args.model,
    )
    daemon.start()


if __name__ == "__main__":
    main()
