import subprocess
import time

from voice_type.config import IS_LINUX, SYSTEM
from voice_type.notify import notify


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
