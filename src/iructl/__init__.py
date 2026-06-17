from ._cli import app
from ._constants import APP_NAME

__all__ = ["app", "main"]


def main() -> None:
    app(prog_name=APP_NAME)
