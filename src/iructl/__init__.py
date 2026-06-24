import logging
import sys

from rich.markup import escape

from ._cli import app
from ._console import OutputConsole
from ._constants import APP_NAME

__all__ = ["app", "main"]

logger = logging.getLogger(__name__)
console = OutputConsole(logger)


def main() -> None:
    try:
        app(prog_name=APP_NAME)
    except Exception as error:
        detail = escape(str(error)) or type(error).__name__
        message = f"An unexpected error occurred: {detail}"
        logger.exception(message)
        console.print_error(message)
        sys.exit(1)
