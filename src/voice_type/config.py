import platform
from pathlib import Path

TARGET_SR = 16000
SYSTEM = platform.system()  # 'Linux', 'Darwin', 'Windows'
IS_LINUX = SYSTEM == "Linux"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
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
