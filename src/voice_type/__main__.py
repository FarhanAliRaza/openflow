import sys
from pathlib import Path

# Ensure the parent directory (src/) is on sys.path so that
# "voice_type" is importable as a package.
_tools_dir = str(Path(__file__).resolve().parent.parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

from voice_type.cli import main

main()
