from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from iructl._cli.common import GlobalState
from iructl._cli.member import MemberCliDescriptor, register_lifecycle_commands
from iructl._cli.member.commands import _git_callback
from iructl._cli.profile import descriptor as profile_descriptor
from iructl._cli.script import descriptor as script_descriptor
from iructl.repository import CustomProfile, CustomScript, RepositoryDirectory

runner = CliRunner()


def _ctx(*, git: bool) -> typer.Context:
    """A stand-in context exposing only the GlobalState the callback reads."""
    return SimpleNamespace(obj=GlobalState(git=git))  # type: ignore[return-value]


LIFECYCLE_COMMANDS = ("pull", "push", "sync", "delete", "list")


@pytest.mark.parametrize(
    ("descriptor", "member_type", "member_name", "directory"),
    [
        pytest.param(script_descriptor, CustomScript, "custom script", RepositoryDirectory.SCRIPTS, id="script"),
        pytest.param(profile_descriptor, CustomProfile, "custom profile", RepositoryDirectory.PROFILES, id="profile"),
    ],
)
def test_descriptor_derives_metadata_from_member_type(
    descriptor: MemberCliDescriptor, member_type, member_name, directory
):
    assert descriptor.member_type is member_type
    assert descriptor.member_name == member_name
    assert descriptor.repo_directory is directory


@pytest.mark.parametrize("descriptor", [script_descriptor, profile_descriptor])
@pytest.mark.parametrize("command", LIFECYCLE_COMMANDS)
def test_factory_registers_lifecycle_command_with_per_noun_help(descriptor: MemberCliDescriptor, command: str):
    app = register_lifecycle_commands(typer.Typer(rich_markup_mode="rich"), descriptor)

    # The root callback seeds ctx.obj in the real CLI; seed it here for the isolated group app.
    result = runner.invoke(app, [command, "--help"], obj=GlobalState())

    assert result.exit_code == 0
    # Per-noun option help is preserved via the handler's dynamic annotations.
    if command in ("pull", "push", "sync", "delete"):
        assert f"Include {descriptor.noun_singular}(s) at path." in result.output


def test_git_callback_runs_locate_git(monkeypatch):
    """The shared callback locates git before any member command runs."""
    called = False

    def mock_locate_git():
        nonlocal called
        called = True
        return "/usr/bin/git"

    monkeypatch.setattr("iructl._git.locate_git", mock_locate_git)
    _git_callback(_ctx(git=True))
    assert called is True


def test_git_callback_exits_when_git_missing(monkeypatch):
    """The shared callback exits with code 1 when git is unavailable."""

    def mock_locate_git():
        raise FileNotFoundError("Failed to locate the git executable.")

    monkeypatch.setattr("iructl._git.locate_git", mock_locate_git)
    with pytest.raises(typer.Exit) as ctx:
        _git_callback(_ctx(git=True))
    assert ctx.value.exit_code == 1


def test_git_callback_skips_locate_git_when_git_disabled(monkeypatch):
    """With --no-git, the callback returns without requiring the git executable."""
    called = False

    def mock_locate_git():
        nonlocal called
        called = True
        raise FileNotFoundError("Failed to locate the git executable.")

    monkeypatch.setattr("iructl._git.locate_git", mock_locate_git)
    _git_callback(_ctx(git=False))
    assert called is False
