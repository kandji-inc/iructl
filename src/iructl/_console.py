import functools
import logging
import sys
from enum import StrEnum
from io import StringIO

from pygments.lexers import get_lexer_by_name, guess_lexer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

__all__ = ["OutputConsole"]

type MessageType = str | Table | Syntax | Panel

theme = Theme({"warning": "yellow", "error": "red", "success": "green"})

stdout = Console(theme=theme, highlight=False)
stderr = Console(theme=theme, highlight=False, stderr=True)

epilog_text = "Made with :heart: by [yellow]Iru[/yellow]"


class SyntaxType(StrEnum):
    JSON = "json"
    YAML = "yaml"
    XML = "xml"


class OutputFormat(StrEnum):
    TABLE = "table"
    PLIST = "plist"
    JSON = "json"
    YAML = "yaml"

    def to_syntax(self) -> SyntaxType | None:
        """Convert the output format to a syntax type."""
        match self:
            case OutputFormat.JSON:
                return SyntaxType.JSON
            case OutputFormat.YAML:
                return SyntaxType.YAML
            case OutputFormat.PLIST:
                return SyntaxType.XML
            case OutputFormat.TABLE:
                return None


def render_plain_text(message, new_line_start: bool = False) -> str:
    """Render a message to plain text without markup."""
    if isinstance(message, str) and "[" not in message:
        # If the message is a string and does not contain any markup, return it as is
        return ("\n" if new_line_start else "") + message

    # Otherwise, use the rich console to render the message to plain text
    capture_file = StringIO()
    Console(file=capture_file, force_terminal=False, color_system=None, highlight=False, theme=theme).print(
        message,
        end="",
        soft_wrap=True,  # disables inserting new lines to match the console width
        new_line_start=new_line_start,
    )
    return capture_file.getvalue()


class OutputConsole:
    """A console wrapper that can print to stdout and stderr and log messages to a logger."""

    def __init__(self, logger: logging.Logger | None = None):
        """Initialize a new OutputConsole object."""
        # Use module level console objects
        self._stdout = stdout
        self._stderr = stderr

        # default to root logger if none provided
        self._logger = logging.getLogger() if logger is None else logger

    @property
    def stdout(self) -> Console:
        """Get the stdout console."""
        return self._stdout

    @property
    def stderr(self) -> Console:
        """Get the stderr console."""
        return self._stderr

    @property
    def width(self) -> int:
        """Get the width of the console."""
        return self._stdout.width

    @functools.cached_property
    def logs_to_std(self):
        """Lazily check if the logger has a stream handler and cache the result"""

        def _logs_to_std(current: logging.Logger) -> bool:
            """Check if the logger or its parents have a stream handler."""
            if any(
                isinstance(handler, logging.StreamHandler) and handler.stream in {sys.stderr, sys.stdout}
                for handler in current.handlers
            ):
                return True
            if current.propagate and current.parent is not None:
                return _logs_to_std(current.parent)
            return False

        return _logs_to_std(self._logger)

    def log(self, level: int, message: str):
        """Log a message to the logger."""
        self._logger.log(level, message)

    def debug(self, message: str):
        """Log a debug message to the logger."""
        self.log(logging.DEBUG, message)

    def info(self, message: str):
        """Log an info message to the logger."""
        self.log(logging.INFO, message)

    def warning(self, message: str):
        """Log a warning message to the logger."""
        self.log(logging.WARNING, message)

    def error(self, message: str):
        """Log an error message to the logger."""
        self.log(logging.ERROR, message)

    def critical(self, message: str):
        """Log a critical message to the logger."""
        self.log(logging.CRITICAL, message)

    def print(
        self,
        message: MessageType,
        *,
        style: str = "none",
        level: int = logging.INFO,
        stderr: bool = False,
        soft_wrap: bool | None = None,
        new_line_start: bool = False,
    ):
        """Print a message to the console and log it to the logger."""

        # Log the sanitized message to the log handler
        plain_message = render_plain_text(message, new_line_start=isinstance(message, Syntax | Table))
        self.log(level, plain_message)

        # Skip printing to the console if there is already a stream handler to avoid duplicate output
        if not self.logs_to_std:
            stream: Console = getattr(self, "stderr" if stderr else "stdout")
            if stream.is_terminal:
                # If the console is a terminal, print the styled message
                styled_message = Text.from_markup(message, style=style) if isinstance(message, str) else message
                stream.print(styled_message, new_line_start=new_line_start, soft_wrap=soft_wrap)
            else:
                # If the console is not a terminal, print the plain message. It is already fully
                # rendered text, so disable markup parsing to avoid re-interpreting literal brackets.
                stream.print(plain_message, new_line_start=False, overflow="ignore", soft_wrap=True, markup=False)

    def print_success(
        self, message: MessageType, style: str = "success", level: int = logging.INFO, stderr: bool = False
    ):
        """Print a success message to the console and log it to the logger."""
        self.print(message, style=style, level=level, stderr=stderr)

    def print_warning(
        self, message: MessageType, style: str = "warning", level: int = logging.WARNING, stderr: bool = True
    ):
        """Print a warning message to the console and log it to the logger."""
        self.print(message, style=style, level=level, stderr=stderr)

    def print_error(self, message: MessageType, style: str = "error", level: int = logging.ERROR, stderr: bool = True):
        """Print an error message to the console and log it to the logger."""
        self.print(message, style=style, level=level, stderr=stderr)

    def print_with_leading_blank(self, message: MessageType, **kwargs):
        """Print a blank line followed by the message, to visually separate it from preceding output."""
        self.print("")
        self.print(message, **kwargs)

    def print_syntax(self, content: str, syntax: SyntaxType | None = None, level: int = logging.INFO):
        """Print a syntax-highlighted message to the console and log it to the logger."""
        if self._stdout.is_terminal:
            lexer = guess_lexer(content) if syntax is None else get_lexer_by_name(syntax)
            rendered_syntax = Syntax(content, lexer, background_color="default")
        else:
            rendered_syntax = content
        self.print(rendered_syntax, level=level, soft_wrap=True)
