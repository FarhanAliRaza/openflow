import os
import socket
import subprocess
import time

from voice_type.config import IS_LINUX, SYSTEM
from voice_type.notify import notify

# Inter-keystroke delay for `ydotool type`. ydotool's own default is 12ms;
# anything much lower (the old code used 2ms) outruns the compositor and drops
# characters — spaces between words most of all.
KEY_DELAY_MS = "12"

# Where ydotoold puts its control socket depends on the version: 1.x defaults
# to $XDG_RUNTIME_DIR/.ydotool_socket and honours the YDOTOOL_SOCKET env var and
# a -p PATH flag; 0.1.8 (current Ubuntu) hardcodes /tmp/.ydotool_socket, ignores
# the env var, and treats -p as socket *permissions*, not a path. So we don't
# dictate a path — we launch the daemon bare and discover whichever socket it
# actually creates.
YDOTOOL_SOCKETS = [
    os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), ".ydotool_socket"),
    "/tmp/.ydotool_socket",
]

_ydotoold_proc = None

# Whether `ydotool type`/`key` can reach a daemon. Without one, keystroke
# injection silently drops characters, so we route to the clipboard instead.
_ydotoold_ready = False


# ---------------------------------------------------------------------------
# ydotoold — persistent uinput backend
#
# ydotool needs a running ydotoold daemon. Without one, every `ydotool type`
# call creates a throwaway uinput device the compositor hasn't registered yet,
# so the opening keystrokes get dropped intermittently. We run ydotoold once
# and keep it alive so the virtual keyboard persists across utterances. No sudo
# needed at runtime: /dev/uinput is group-rw for `input`, which the user is in.
# ---------------------------------------------------------------------------

def ensure_ydotoold():
    """Make sure a ydotoold socket is available, starting the daemon if not.

    Returns True if `ydotool type` can talk to a daemon, False otherwise (caller
    should expect the clipboard fallback)."""
    global _ydotoold_proc, _ydotoold_ready
    if not IS_LINUX:
        return False

    # A daemon may already be up (systemd, a previous run): adopt it rather than
    # spawn a duplicate. We probe with a real connection, not os.path.exists,
    # because 0.1.8 leaves its socket file behind on exit — the file lingering
    # doesn't mean anything is listening.
    if _adopt_live_socket():
        return True

    # No live daemon. Drop any stale socket a dead one left, or ydotoold's
    # bind() fails with EADDRINUSE.
    _clear_stale_sockets()

    try:
        _ydotoold_proc = subprocess.Popen(
            ["ydotoold"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("WARNING: ydotoold not installed — falling back to clipboard "
              "paste (keystroke typing drops characters without the daemon).")
        print("  Install: sudo apt install ydotoold")
        return False

    for _ in range(30):
        if _adopt_live_socket():
            return True
        time.sleep(0.1)
    print("WARNING: ydotoold did not start within 3s.")
    return False


def _adopt_live_socket():
    """Point the client at a ydotoold socket that actually accepts connections.

    ydotoold listens on a SOCK_STREAM unix socket, so a connect probe tells a
    live daemon apart from the stale socket file 0.1.8 leaves on exit (and from
    a same-named zombie). Sets YDOTOOL_SOCKET for the 1.x client; 0.1.8 ignores
    it but defaults to the same /tmp path. Returns True on success."""
    global _ydotoold_ready
    for path in YDOTOOL_SOCKETS:
        if not os.path.exists(path):
            continue
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            probe.connect(path)
        except OSError:
            continue
        finally:
            probe.close()
        os.environ["YDOTOOL_SOCKET"] = path
        _ydotoold_ready = True
        return True
    return False


def _clear_stale_sockets():
    for path in YDOTOOL_SOCKETS:
        try:
            os.unlink(path)
        except OSError:
            pass


def stop_ydotoold():
    global _ydotoold_proc
    if _ydotoold_proc and _ydotoold_proc.poll() is None:
        _ydotoold_proc.terminate()
        try:
            _ydotoold_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _ydotoold_proc.kill()
    _ydotoold_proc = None


# ---------------------------------------------------------------------------
# Text injection (platform-adaptive)
# ---------------------------------------------------------------------------

def type_text_linux(text):
    """Linux: ydotool for real keystrokes, clipboard fallback.

    Keystroke typing needs a running ydotoold (see ensure_ydotoold); without
    it `ydotool type` returns success while silently dropping characters, so we
    skip straight to the clipboard rather than emit garbled text."""
    if not text.strip():
        return False

    if _ydotoold_ready:
        try:
            r = subprocess.run(
                ["ydotool", "type", "--key-delay", KEY_DELAY_MS, "--", text],
                timeout=15, capture_output=True,
            )
            if r.returncode == 0:
                return True
            print(f"  ydotool type failed (rc={r.returncode}): "
                  f"{r.stderr.decode().strip()}")
        except FileNotFoundError:
            print("  ydotool not found")
        except subprocess.TimeoutExpired:
            print("  ydotool type timed out")

    return _clipboard_fallback(text)


def _clipboard_fallback(text):
    """Copy to the clipboard and tell the user to paste manually."""
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
