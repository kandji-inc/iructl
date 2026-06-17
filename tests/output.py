"""Helpers for asserting against CLI output rendered by Rich/Typer."""

import re

# Matches ANSI SGR/control sequences (color, bold, cursor moves).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Matches the Unicode "Box Drawing" block used for Rich panel/table borders.
_BOX_RE = re.compile(r"[─-╿]")


def normalize_output(text: str) -> str:
    """Reduce styled, panel-wrapped CLI output to its plain text content.

    Rich wraps and styles output based on terminal width and color settings, which
    vary across machines and CI. Stripping ANSI codes and panel borders, then
    collapsing whitespace, lets content assertions check for a substring without
    depending on layout (e.g. a message split across wrapped panel lines).
    """
    text = _ANSI_RE.sub("", text)
    text = _BOX_RE.sub(" ", text)
    return " ".join(text.split())
