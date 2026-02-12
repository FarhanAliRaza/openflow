import threading

from voice_type.config import IS_LINUX


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
