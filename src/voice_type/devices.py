from voice_type.config import CONFIG_FILE, load_saved_device, save_device


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
