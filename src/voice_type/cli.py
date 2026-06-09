"""
Voice typing using Qwen3-ASR (push-to-talk) — works on Linux, macOS, Windows.

Auto-detects the platform:
  Linux:   evdev for keyboard monitoring, ydotool for text injection
  macOS:   pynput for keyboard + clipboard paste (Cmd+V)
  Windows: pynput for keyboard + clipboard paste (Ctrl+V)

Usage:
  python src/voice_type
  python src/voice_type --key x --modifier cmd
  python src/voice_type --list-devices

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

from voice_type.config import IS_LINUX, MODEL_PATH, SYSTEM
from voice_type.daemon import VoiceTypeDaemon
from voice_type.devices import list_devices, select_device


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
    parser.add_argument("--refine", action="store_true",
                        help="Polish transcription with the local claude CLI "
                             "(off by default; adds ~5-7s/utterance, needs "
                             "claude installed). ASR vocab biasing stays on "
                             "regardless.")
    parser.add_argument("--refine-model", type=str, default="haiku",
                        help="Model for the claude refinement layer "
                             "(default: haiku)")
    parser.add_argument("--debug", action="store_true",
                        help="Print refinement details: detected CLAUDE.md, "
                             "the prompt sent to claude, and before/after text")
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
        refine=args.refine,
        refine_model=args.refine_model,
        debug=args.debug,
    )
    daemon.start()
