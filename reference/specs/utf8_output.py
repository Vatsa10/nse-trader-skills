# --- utf8 output: keep byte-identical in every script (tests/test_repo.py checks) ---
# The tools print the rupee sign. On Windows, piped output (Claude Code reads it through a pipe) defaults to
# cp1252, which has no rupee sign, and the first print of one raised UnicodeEncodeError. Switch a stream that
# cannot encode it to UTF-8; a stream that already can (UTF-8 consoles, Linux, macOS) is left alone.
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    try:
        "₹".encode(_stream.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
# --- end utf8 output ---
