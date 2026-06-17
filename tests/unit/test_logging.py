import logging
from logging.handlers import RotatingFileHandler

import platformdirs
import typer
from typer.main import get_command

from iructl._cli import main
from iructl._constants import APP_BRANDING, APP_NAME


def _bare_context() -> typer.Context:
    """A minimal context for invoking the root callback directly, with no parsed parameters."""
    app = typer.Typer(add_completion=False)

    @app.command()
    def _noop() -> None: ...

    return get_command(app).make_context("main", [])


def test_logging_setup(monkeypatch, caplog):
    """Ensure that basicconfig is called with the correct parameters after main is executed."""

    def mock_basicconfig(**kwargs):
        assert kwargs["level"] == logging.INFO
        assert kwargs["format"] == "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        assert len(kwargs["handlers"]) == 1
        assert isinstance(kwargs["handlers"][0], RotatingFileHandler)
        assert kwargs["handlers"][0].baseFilename == str(
            platformdirs.user_log_path(appname=APP_NAME) / f"{APP_NAME}.log"
        )
        assert kwargs["handlers"][0].maxBytes == 1024 * 5000
        assert kwargs["handlers"][0].backupCount == 3

    monkeypatch.setattr(logging, "basicConfig", mock_basicconfig)
    with caplog.at_level(logging.DEBUG):
        main(_bare_context())
    assert f"--- Starting {APP_BRANDING} ---" in caplog.text
