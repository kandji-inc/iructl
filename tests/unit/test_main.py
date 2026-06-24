import logging

import pytest

import iructl


class TestMainUnexpectedErrors:
    @pytest.mark.parametrize(
        ("error", "expected_detail"),
        [
            (RuntimeError("kaboom"), "kaboom"),
            (RuntimeError(), "RuntimeError"),
        ],
        ids=["with_message", "empty_message_falls_back_to_type"],
    )
    def test_unhandled_error_exits_one_and_logs_full_exception(self, monkeypatch, caplog, error, expected_detail):
        """An unhandled error is a clean message + non-zero exit, with the full exception logged (even outside debug); an empty message falls back to the exception type name."""

        def _raise(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(iructl, "app", _raise)

        with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc:
            iructl.main()

        assert exc.value.code == 1
        assert f"An unexpected error occurred: {expected_detail}" in caplog.text
        assert any(record.exc_info for record in caplog.records)

    @pytest.mark.parametrize(
        "control_flow_exit",
        [SystemExit(0), KeyboardInterrupt()],
        ids=["system_exit", "keyboard_interrupt"],
    )
    def test_control_flow_exit_passes_through_unchanged(self, monkeypatch, control_flow_exit):
        """Control-flow exits (SystemExit from help/--version/validation, KeyboardInterrupt) propagate unchanged - not swallowed or remapped to exit code 1."""

        def _raise(*_args, **_kwargs):
            raise control_flow_exit

        monkeypatch.setattr(iructl, "app", _raise)

        with pytest.raises((SystemExit, KeyboardInterrupt)) as exc:
            iructl.main()

        assert exc.value is control_flow_exit

    @pytest.mark.parametrize(
        "error_message",
        ["[/usr/bin]", "[/]"],
        ids=["path_like_closing_tag", "bare_close_all"],
    )
    def test_markup_in_error_message_renders_cleanly_on_tty(self, monkeypatch, tmp_path, error_message):
        """An error message containing rich-markup brackets is escaped, so the default
        interactive path (file logging + TTY stderr) prints a clean message and exits 1
        instead of raising MarkupError from Text.from_markup."""
        from rich.console import Console

        from iructl._console import OutputConsole, theme

        def _raise(*_args, **_kwargs):
            raise RuntimeError(error_message)

        monkeypatch.setattr(iructl, "app", _raise)

        logger = logging.getLogger("test_markup_main")
        logger.handlers.clear()
        logger.propagate = False
        logger.addHandler(logging.FileHandler(tmp_path / "iructl.log"))
        console = OutputConsole(logger)
        console._stderr = Console(theme=theme, highlight=False, stderr=True, force_terminal=True)
        monkeypatch.setattr(iructl, "console", console)

        with pytest.raises(SystemExit) as exc:
            iructl.main()

        assert exc.value.code == 1
