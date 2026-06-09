import json
import os
import re
import subprocess
from pathlib import Path

from voice_type.config import IS_LINUX, SYSTEM


# ---------------------------------------------------------------------------
# Active-window context
#
# Find the CLAUDE.md that governs the folder of whatever app currently has
# focus — i.e. where the dictated text is about to be typed. Everything here
# is best-effort: any failure returns None so the caller simply skips
# refinement.
# ---------------------------------------------------------------------------

CLAUDE_MD_NAMES = ("CLAUDE.md", "CLAUDE.MD", "claude.md")
_MAX_WALK_UP = 30  # parent directories to climb before giving up


def _gnome_shell_focused_window():
    """(pid, title) of the focused window via the Window Calls GNOME extension.

    Wayland's core protocol forbids reading which window is focused, so GNOME
    exposes it only through a sanctioned extension
    ('window-calls@domandoman.xyz'). Returns (None, "") when the extension is
    absent or nothing is focused.
    """
    try:
        out = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
             "--object-path", "/org/gnome/Shell/Extensions/Windows",
             "--method", "org.gnome.Shell.Extensions.Windows.List"],
            capture_output=True, timeout=2, text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, ""
    if out.returncode != 0:
        return None, ""
    # gdbus wraps the JSON payload in a GVariant tuple: ('[{...}]',)
    raw = out.stdout
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return None, ""
    try:
        windows = json.loads(raw[start:end + 1].replace("\\'", "'"))
    except (ValueError, TypeError):
        return None, ""
    for w in windows:
        if w.get("focus") and w.get("pid"):
            return int(w["pid"]), str(w.get("title") or "")
    return None, ""


def _focused_window():
    """(pid, title) of the focused window, or (None, "") if undetectable.

    The title matters because one process often owns several windows in
    different folders (VS Code, a terminal server). Those windows share a PID,
    so the PID alone can't say which one is focused — but the title names the
    project, so we carry it through to disambiguate.
    """
    try:
        if SYSTEM == "Windows":
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None, ""
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return (pid.value or None), buf.value

        if IS_LINUX:
            # GNOME (X11 or Wayland) via the Window Calls extension first; then
            # xdotool for other X11 window managers.
            pid, title = _gnome_shell_focused_window()
            if pid:
                return pid, title
            out = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowpid", "getwindowname"],
                capture_output=True, timeout=2, text=True,
            )
            if out.returncode != 0:
                return None, ""
            lines = out.stdout.splitlines()
            pid = lines[0].strip() if lines else ""
            title = lines[1] if len(lines) > 1 else ""
            return (int(pid) if pid.isdigit() else None), title

        if SYSTEM == "Darwin":
            out = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get unix id of '
                 'first process whose frontmost is true'],
                capture_output=True, timeout=2, text=True,
            )
            pid = out.stdout.strip()
            return (int(pid) if out.returncode == 0 and pid.isdigit()
                    else None), ""
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None, ""


def _candidate_dirs(pid):
    """Working directories for the process and its descendants.

    Terminals own the focused window but run the real shell as a child, so we
    collect the cwd of the process and every descendant, deepest path first so
    the most specific directory wins.
    """
    try:
        import psutil
    except ImportError:
        return []

    try:
        proc = psutil.Process(pid)
        procs = [proc] + proc.children(recursive=True)
    except (psutil.Error, OSError):
        return []

    dirs = []
    seen = set()
    for p in procs:
        try:
            cwd = p.cwd()
        except (psutil.Error, OSError):
            continue
        if cwd and cwd not in seen:
            seen.add(cwd)
            dirs.append(Path(cwd))

    dirs.sort(key=lambda d: len(d.parts), reverse=True)
    return dirs


def _title_names_dir(dirname, title):
    """True if `dirname` appears as a whole token in the window title.

    Bounded so 'reflex' matches "reflex - VS Code" but not "reflexive"; the
    folder's own punctuation (e.g. the '-' in 'bmv-2') stays part of the token.
    """
    name, title = dirname.lower(), title.lower()
    if not name or not title:
        return False
    i = title.find(name)
    while i != -1:
        before = title[i - 1] if i else " "
        after = title[i + len(name)] if i + len(name) < len(title) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = title.find(name, i + 1)
    return False


def _rank_dirs_by_title(dirs, title):
    """Reorder candidate dirs so the one the window title names comes first.

    When several windows share a PID (VS Code, a terminal server), _candidate_dirs
    returns every folder they touch and can't tell which window is focused. The
    title can: "bmv-2 - Visual Studio Code" names the project. Among ties — and
    when the title matches nothing — the deepest path still wins, as before.
    """
    if not title:
        return dirs
    return sorted(
        dirs,
        key=lambda d: (_title_names_dir(d.name, title), len(d.parts), len(d.name)),
        reverse=True,
    )


def find_claude_md(start_dir):
    """Walk up from start_dir looking for a CLAUDE.md. Returns Path or None."""
    try:
        d = Path(start_dir).resolve()
    except OSError:
        return None
    for _ in range(_MAX_WALK_UP):
        for name in CLAUDE_MD_NAMES:
            candidate = d / name
            if candidate.is_file():
                return candidate
        if d.parent == d:
            break
        d = d.parent
    return None


def active_claude_md(debug=False):
    """CLAUDE.md governing the focused window's directory, or None.

    Best-effort and platform-dependent (X11/Windows/macOS); returns None on
    Wayland or whenever the directory can't be resolved.
    """
    pid, title = _focused_window()
    if not pid:
        if debug:
            hint = ""
            if IS_LINUX:
                if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                    hint = (" (enable the 'window-calls' GNOME extension, "
                            "then log out and back in)")
                else:
                    hint = " (install xdotool for X11 window detection)"
            print(f"[debug] could not detect the focused window's pid{hint}")
        return None
    dirs = _rank_dirs_by_title(_candidate_dirs(pid), title)
    if debug:
        print(f"[debug] focused pid {pid} title={title!r}; candidate dirs: "
              f"{[str(d) for d in dirs] or 'none'}")
    for d in dirs:
        found = find_claude_md(d)
        if found:
            if debug:
                print(f"[debug] CLAUDE.md found: {found}")
            return found
    if debug:
        print("[debug] no CLAUDE.md found above any candidate dir")
    return None


# ---------------------------------------------------------------------------
# ASR vocabulary
#
# Pull the technical terms out of a CLAUDE.md so they can be fed to Qwen3-ASR
# as context — biasing recognition toward jargon, tool, and command names
# (e.g. "ydotool") that generic speech-to-text mangles into common words.
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+-]*")
_INLINE_CODE = re.compile(r"`([^`\n]{1,60})`")
_FENCED_CODE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
# "Techy"-shaped: internal caps, 2+ caps, a digit, or joining punctuation —
# e.g. Qwen3-ASR, EvDev, X11, wl-copy, notify-send, snake_case.
_TECHY = re.compile(r"[a-z][A-Z]|[A-Z]{2,}|[0-9]|[._/+-]")
_PATHY = re.compile(r"[/\\]|\.(py|json|md|txt|toml|cfg|sh)$")

# Common English plus doc/source-comment words that otherwise masquerade as
# "code tokens". Dropped so real jargon (ydotool, evdev, pynput) ranks first.
_STOPWORDS = frozenset("""
the a an and or but if then else to of in on for with from by as at is are was
were be been being it its this that these those you your we our they their them
do does did done can could will would should may might must not no nor yes so
than too very just also into onto out up down over under again more most less
all any some each every both few many much which what when where who whom why
how here there now only own same such once new old via per
app info type active main core point entry parser argument arguments constant
constants path paths platform detection setup wrapper handling enumeration
selection persistence notification notifications option options install enable
run running runs use used using uses file files list mode line lines code
example examples note notes current target between across default defaults
press hold speak release naturally appears polished cursor word words text
into start stop value values number numbers item items name names version
""".split())


def asr_vocab_from(claude_md_path, max_terms=60, max_chars=900):
    """A comma-separated vocabulary string extracted from a CLAUDE.md.

    Ranks terms by how likely they are to be *spoken and mis-transcribed*:
    bare tool/command names found in code (ydotool, pynput) first, then
    techy-shaped names (Qwen3-ASR, X11), with file paths last and English /
    comment noise dropped. Returns "" if the file can't be read.
    """
    try:
        text = Path(claude_md_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    code_tokens = set(_TOKEN.findall(
        "\n".join(_INLINE_CODE.findall(text) + _FENCED_CODE.findall(text))))

    seen, scored = set(), []

    def consider(tok):
        tok = tok.strip(" `.,:;()[]{}\"'")
        low = tok.lower()
        if not (2 <= len(tok) <= 40) or tok.isdigit() or low in _STOPWORDS \
                or low in seen:
            return
        if _PATHY.search(tok):
            score = 1                                   # paths: rarely dictated
        elif _TECHY.search(tok):
            score = 3                                   # Qwen3-ASR, X11, EvDev
        elif tok in code_tokens and tok.isalpha() and tok.islower() \
                and len(tok) >= 4:
            score = 4                                   # bare command/tool name
        else:
            return                                      # plain prose word: drop
        seen.add(low)
        scored.append((score, tok))

    for tok in _TOKEN.findall(text):
        consider(tok)

    scored.sort(key=lambda s: -s[0])  # stable: keeps document order within a tier
    terms = [t for _, t in scored]
    return ", ".join(terms[:max_terms])[:max_chars]
