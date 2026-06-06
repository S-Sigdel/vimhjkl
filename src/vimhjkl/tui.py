"""Terminal rendering for vimhjkl.

Deliberately NOT a persistent full-screen application: a long-lived
prompt_toolkit Application owns the tty and corrupts the terminal when vim is
launched as a child.  Instead we render with plain ANSI and read single keys
via termios, and use prompt_toolkit only for the one-shot main menu (it fully
exits its alt-screen before we ever hand the tty to vim).
"""

from __future__ import annotations

import contextlib
import re
import sys
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def c(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{RESET}"


def clear() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def rule(width: int = 72) -> str:
    return c("─" * width, GREY)


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_len(s: str) -> int:
    """Length of ``s`` as drawn — ANSI colour codes don't take cells."""
    return len(_ANSI_RE.sub("", s))


def pad_visible(s: str, width: int) -> str:
    """Right-pad a (possibly coloured) string to ``width`` visible columns."""
    return s + " " * max(0, width - visible_len(s))


def truncate_visible(s: str, width: int) -> str:
    """Cut a (possibly coloured) string to at most ``width`` visible columns,
    keeping ANSI codes intact and resetting colour at the end.  Guarantees the
    result never wraps a terminal line — which is what keeps a two-column frame
    from spilling onto extra rows and 'jumping' on every redraw."""
    if width <= 0:
        return ""
    if visible_len(s) <= width:
        return s
    out, vis, i, had_color = [], 0, 0, False
    while i < len(s) and vis < width:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            had_color = True
            i = m.end()
            continue
        out.append(s[i])
        vis += 1
        i += 1
    res = "".join(out)
    return res + RESET if had_color else res


def wrap_plain(text: str, width: int) -> list[str]:
    """Greedy word-wrap of plain text to ``width`` columns (no ANSI inside)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


def banner(title: str, subtitle: str = "") -> None:
    print()
    print(c("  ⚔  " + title, BOLD + CYAN))
    if subtitle:
        print(c("     " + subtitle, GREY))
    print(rule())


def progress_bar(frac: float, width: int = 20, color: str = GREEN) -> str:
    """A filled/empty block bar, e.g. ▓▓▓▓░░░░░░."""
    frac = max(0.0, min(1.0, frac))
    fill = int(round(frac * width))
    return c("▓" * fill, color) + c("░" * (width - fill), GREY)


# ---------------------------------------------------------------------------
# Buffer rendering — show a "before"/"after" buffer in a framed box.
# ---------------------------------------------------------------------------

# Black text on a yellow background — the "land here" target cell.
HILITE = "\033[30;43m"


def _highlight_cell(text: str, col0: int) -> str:
    """Wrap the single character at 0-based ``col0`` in the target highlight.

    ``text`` must already be padded to its display width (so ``col0`` is always
    in range — a column past end-of-line lands on a padded space).
    """
    if not _supports_color() or col0 < 0 or col0 >= len(text):
        return text
    return text[:col0] + HILITE + text[col0] + RESET + text[col0 + 1:]


def render_buffer(lines: list[str], label: str = "", color: str = BLUE,
                  cursor: Optional[list[int]] = None,
                  target: Optional[list[int]] = None) -> None:
    width = max([len(l) for l in lines] + [len(label), 20])
    width = min(width, 76)
    if label:
        print(c(f"  {label}", color + BOLD))
    top = "  ┌" + "─" * (width + 2) + "┐"
    print(c(top, GREY))
    for i, line in enumerate(lines, start=1):
        shown = line if len(line) <= width else line[: width - 1] + "…"
        cell = shown.ljust(width)
        # Highlight the target cell (1-based [line, col]) so "land on the
        # highlighted target" is literally true.
        if target and target[0] == i:
            cell = _highlight_cell(cell, target[1] - 1)
        marker = ""
        if cursor and cursor[0] == i:
            marker = c(" ◀ start" if target else " ◀ cursor", YELLOW)
        print(c("  │ ", GREY) + cell + c(" │", GREY) + marker)
    bot = "  └" + "─" * (width + 2) + "┘"
    print(c(bot, GREY))


# ---------------------------------------------------------------------------
# Single-key input (no Enter needed) for review-mode self-rating.
# ---------------------------------------------------------------------------

def read_key() -> str:
    """Read a single keypress.  Returns the character (space -> ' ')."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line[:1] if line else "q"
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover (non-unix)
        return sys.stdin.readline()[:1] or "q"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        with _suspend_guard(fd, old):
            ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == "\x03":  # Ctrl-C
        raise KeyboardInterrupt
    return ch


def pause(msg: str = "press any key to continue…") -> None:
    print(c("  " + msg, GREY))
    read_key()


def install_signal_guard() -> None:
    """Restore the tty to its cooked startup state if the process is killed by
    SIGTERM/SIGHUP — those terminate without running the try/finally restores in
    read_key/menu/pager, which would otherwise leave the terminal raw and corrupt
    the user's shell (sudo echoing until ``stty sane``).  Re-raises the default
    action so the process still dies.  Installed once, globally, for the whole run.

    SIGTSTP (Ctrl-Z) is deliberately NOT handled here: it's handled only inside
    the raw regions that own the tty (see ``_suspend_guard``), so it never
    interferes with a child like vim that manages its own job control."""
    if not sys.stdin.isatty():
        return
    try:
        import os
        import signal
        import termios
    except ImportError:  # pragma: no cover (non-unix)
        return
    fd = sys.stdin.fileno()
    cooked = termios.tcgetattr(fd)

    def _die(signum, _frame):
        termios.tcsetattr(fd, termios.TCSADRAIN, cooked)
        signal.signal(signum, signal.SIG_DFL)  # re-raise with default action
        os.kill(os.getpid(), signum)

    for _sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(_sig, _die)


@contextlib.contextmanager
def _suspend_guard(fd: int, cooked):
    """Context manager: while a raw region holds the tty, make Ctrl-Z (SIGTSTP)
    drop back to the ``cooked`` mode *before* the process stops — so the shell it
    hands control to is sane — and re-enter raw on resume.  Scoped to the ``with``
    body; the previous SIGTSTP disposition is restored on exit, so outside these
    regions (e.g. while vim runs) Ctrl-Z keeps its default behaviour."""
    try:
        import os
        import signal
        import termios
        import tty
    except ImportError:  # pragma: no cover (non-unix)
        yield
        return

    def _suspend(signum, _frame):
        termios.tcsetattr(fd, termios.TCSADRAIN, cooked)  # sane shell while stopped
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)                      # actually stop here
        # --- resumes here on SIGCONT (e.g. `fg`) ---
        signal.signal(signum, _suspend)                   # re-arm
        tty.setraw(fd)                                    # back into raw

    prev = signal.signal(signal.SIGTSTP, _suspend)
    try:
        yield
    finally:
        signal.signal(signal.SIGTSTP, prev)


def _read_raw_key(fd: int) -> str:
    """Read one keypress, decoding arrow keys.  Assumes the tty is ALREADY in raw
    mode (the menu loop holds it) — does no termios save/restore of its own.

    Returns a semantic token for special keys — ``UP`` / ``DOWN`` / ``LEFT`` /
    ``RIGHT`` / ``ENTER`` / ``ESC`` — or the literal character otherwise.
    """
    import os
    import select

    # os.read (unbuffered) — NOT sys.stdin.read, whose buffering would swallow
    # the rest of an escape sequence and make the select() peek below miss it.
    b = os.read(fd, 1)
    if not b:
        return "ESC"
    if b == b"\x03":  # Ctrl-C
        raise KeyboardInterrupt
    if b in (b"\r", b"\n"):
        return "ENTER"
    if b == b"\x1b":
        # ESC alone, or the start of an arrow-key escape sequence.  Peek with a
        # short timeout to tell them apart.
        ready, _, _ = select.select([fd], [], [], 0.03)
        if not ready:
            return "ESC"
        if os.read(fd, 1) == b"[":
            code = os.read(fd, 1)
            return {b"A": "UP", b"B": "DOWN", b"C": "RIGHT", b"D": "LEFT"}.get(code, "ESC")
        return "ESC"
    return b.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def _opt_parts(opt) -> tuple[str, str, str]:
    """Normalise an option to ``(key, title, description)``.

    Accepts ``(key, title)`` or ``(key, title, description)``.
    """
    if len(opt) >= 3:
        return opt[0], opt[1], opt[2]
    return opt[0], opt[1], ""


def rnu_gutter(i: int, idx: int, selected: bool) -> str:
    """A vim ``relativenumber`` gutter cell: the current line shows its absolute
    number (1-based), every other line its distance from the cursor — so you can
    read a number off any row and jump there with ``{count}j`` / ``{count}k``."""
    n = (i + 1) if selected else abs(i - idx)
    return c(f"{n:>2} ", (CYAN + BOLD) if selected else GREY)


def _nav_footer(count: str, extra: str = "") -> str:
    """Shared footer describing the vim-style navigation, with a pending count."""
    base = c("  j/k move · 3j/5k jump · gg/G top/bottom · Enter choose · q quit"
             + extra, GREY)
    return base + (c(f"   {count}", YELLOW + BOLD) if count else "")


def _menu_frame(title: str, subtitle: str, header_lines: Optional[list[str]],
                options: list[tuple], idx: int, count: str = "") -> str:
    """Build one full menu screen as a string with raw-safe ``\\r\\n`` endings."""
    rows: list[str] = ["", c("  ⚔  " + title, BOLD + CYAN)]
    if subtitle:
        rows.append(c("     " + subtitle, GREY))
    rows.append(rule())
    if header_lines:
        rows += ["  " + line for line in header_lines]
        rows.append(rule())
    rows.append("")
    for i, opt in enumerate(options):
        _key, otitle, desc = _opt_parts(opt)
        selected = i == idx
        pointer = c("▸ ", CYAN + BOLD) if selected else "  "
        line = "  " + rnu_gutter(i, idx, selected) + pointer \
            + c(otitle, CYAN + BOLD if selected else RESET)
        if desc:
            line += c("  — " + desc, CYAN if selected else GREY)
        rows.append(line)
    rows.append("")
    rows.append(_nav_footer(count))
    return "\033[2J\033[H" + "\r\n".join(rows) + "\r\n"


def _count_move(idx: int, n: int, key: str, count: str) -> int:
    """Apply a vim motion to a list cursor.  ``j``/``k`` move by ``count`` (default
    1) — wrapping on a bare step, clamping when a count is given; ``gg``/``G`` go to
    the ends (``{count}G`` to an absolute line).  Returns the new index."""
    step = int(count) if count.isdigit() else 1
    if key in ("DOWN", "j"):
        return min(n - 1, idx + step) if count else (idx + 1) % n
    if key in ("UP", "k"):
        return max(0, idx - step) if count else (idx - 1) % n
    if key == "G":
        return min(n, int(count)) - 1 if count.isdigit() else n - 1
    return idx


def menu(title: str, options: list[tuple], subtitle: str = "",
         header_lines: Optional[list[str]] = None) -> Optional[str]:
    """A keyboard-driven, dojo-styled menu.

    ``options`` is a list of ``(key, title[, description])``.  Returns the chosen
    option key, or ``None`` if cancelled.  Drawn in the app's own ANSI style (no
    prompt_toolkit dialog) and navigated with ↑/↓ or j/k; Enter chooses, q/Esc
    cancels, and number keys jump straight to an option.
    """
    if not sys.stdin.isatty():
        return _menu_fallback(title, options)
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover (non-unix)
        return _menu_fallback(title, options)

    n = len(options)
    idx = 0
    count = ""          # pending vim count (e.g. "3" before j)
    g_pending = False   # first 'g' of a 'gg' pressed
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    # Raw mode is held across the WHOLE loop (not per-keypress) so the terminal
    # never flaps back to cooked/echo between renders; restored on exit.
    try:
        tty.setraw(fd)
        with _suspend_guard(fd, old):
            while True:
                sys.stdout.write(_menu_frame(title, subtitle, header_lines,
                                             options, idx, count))
                sys.stdout.flush()
                k = _read_raw_key(fd)
                if k.isdigit():
                    count += k          # build a count; consumed by the next motion
                    g_pending = False
                    continue
                if k == "g":            # gg -> top
                    if g_pending:
                        idx, g_pending = 0, False
                    else:
                        g_pending = True
                        continue
                elif k in ("UP", "k", "DOWN", "j", "G"):
                    idx = _count_move(idx, n, k, count)
                elif k in ("ENTER", " "):
                    return _opt_parts(options[idx])[0]
                elif k in ("ESC", "q"):
                    return None
                count, g_pending = "", False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        clear()


def _menu_fallback(title: str, options: list[tuple]) -> Optional[str]:
    print()
    print(c("  " + title, BOLD + CYAN))
    for i, opt in enumerate(options, start=1):
        _key, otitle, desc = _opt_parts(opt)
        tail = c("  — " + desc, GREY) if desc else ""
        print(f"   {c(str(i), YELLOW)}. {otitle}{tail}")
    print()
    try:
        raw = input("  choose a number (or q to quit): ").strip()
    except EOFError:
        return None
    if raw.lower() in ("q", "quit", ""):
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return _opt_parts(options[int(raw) - 1])[0]
    return None


def prompt_line(msg: str) -> str:
    try:
        return input(c("  " + msg, CYAN))
    except (EOFError, KeyboardInterrupt):
        return ""


# ---------------------------------------------------------------------------
# Live view — a generic raw-mode render/key loop for interactive screens
# (settings toggles, etc.).  tui owns raw mode + key decoding + signal safety;
# the caller owns all state, supplying a frame renderer and a key handler.
# ---------------------------------------------------------------------------


def live_view(render: Callable[[], str],
              on_key: Callable[[str], Optional[str]]) -> None:
    """Drive an interactive screen until the key handler asks to stop.

    ``render()`` returns a full frame string (use ``\\r\\n`` line endings and a
    leading ``\\033[2J\\033[H`` clear — see ``_menu_frame``).  ``on_key(key)`` is
    called with a decoded key token (``UP``/``DOWN``/``LEFT``/``RIGHT``/``ENTER``/
    ``ESC`` or a literal char) and returns ``"exit"`` to end the loop.  Raw mode is
    held across the whole loop and restored on exit, exactly like ``menu``.
    """
    if not sys.stdin.isatty():
        # No tty: render once, no interaction.
        sys.stdout.write(render().replace("\r\n", "\n"))
        return
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover (non-unix)
        sys.stdout.write(render().replace("\r\n", "\n"))
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        with _suspend_guard(fd, old):
            while True:
                sys.stdout.write(render())
                sys.stdout.flush()
                if on_key(_read_raw_key(fd)) == "exit":
                    return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        clear()


# ---------------------------------------------------------------------------
# Pager — a scrollable read-only view (the curriculum screen)
# ---------------------------------------------------------------------------

def _pager_frame(title: str, subtitle: str, header_lines: Optional[list[str]],
                 lines: list[str], top: int, view_h: int, footer: str,
                 count: str = "") -> str:
    rows: list[str] = ["", c("  ⚔  " + title, BOLD + CYAN)]
    if subtitle:
        rows.append(c("     " + subtitle, GREY))
    rows.append(rule())
    if header_lines:
        rows += ["  " + line for line in header_lines]
        rows.append(rule())
    window = lines[top:top + view_h]
    rows += window
    rows += [""] * max(0, view_h - len(window))   # keep the box a stable height
    rows.append(rule())
    shown_to = top + len(window)
    pos = f"{(top + 1) if lines else 0}-{shown_to}/{len(lines)}"
    tail = c(f"    [{pos}]", GREY) + (c(f"  {count}", YELLOW + BOLD) if count else "")
    rows.append(c(f"  {footer}", GREY) + tail)
    return "\033[2J\033[H" + "\r\n".join(rows) + "\r\n"


def pager(title: str, lines: list[str], *, subtitle: str = "",
          header_lines: Optional[list[str]] = None,
          footer: str = "j/k or ↑/↓ scroll · g/G top/bottom · Esc/q back") -> None:
    """Scroll a list of (already-rendered) lines.  Esc or q returns."""
    if not sys.stdin.isatty():
        print()
        print(c("  ⚔  " + title, BOLD + CYAN))
        for line in (header_lines or []):
            print("  " + line)
        for line in lines:
            print(line)
        return
    try:
        import shutil
        import termios
        import tty
    except ImportError:  # pragma: no cover (non-unix)
        for line in lines:
            print(line)
        return

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    top = 0
    count = ""          # pending vim count for count-scrolling / {count}G
    try:
        tty.setraw(fd)
        with _suspend_guard(fd, old):
            while True:
                size = shutil.get_terminal_size((80, 24))
                chrome = 5 + (len(header_lines) + 1 if header_lines else 0)
                view_h = max(3, size.lines - chrome)
                max_top = max(0, len(lines) - view_h)
                top = min(top, max_top)
                sys.stdout.write(_pager_frame(title, subtitle, header_lines, lines,
                                              top, view_h, footer, count))
                sys.stdout.flush()
                k = _read_raw_key(fd)
                if k.isdigit():
                    count += k                       # build a count for j/k/G
                    continue
                step = int(count) if count else 1
                if k in ("DOWN", "j"):
                    top = min(max_top, top + step)
                elif k in ("UP", "k"):
                    top = max(0, top - step)
                elif k == " ":
                    top = min(max_top, top + view_h)
                elif k in ("b", "B"):
                    top = max(0, top - view_h)
                elif k == "g":
                    top = 0
                elif k == "G":                       # {count}G -> jump to that line
                    top = min(max_top, int(count) - 1) if count else max_top
                elif k in ("ESC", "q", "Q"):
                    return
                count = ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        clear()
