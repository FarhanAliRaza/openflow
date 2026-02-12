import subprocess

from voice_type.config import SYSTEM


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
