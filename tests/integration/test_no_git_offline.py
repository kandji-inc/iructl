"""`--no-git` must invoke zero git subprocesses, even where git is unavailable.

Each test makes ``git.locate_git`` raise, so any attempt to shell out to git fails loudly; the
commands are expected to succeed regardless.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import ROOT_MARKER
from iructl.repository import RepositoryDirectory

runner = CliRunner()


@pytest.fixture(autouse=True)
def git_unavailable(monkeypatch):
    """Make any git invocation fail, so a passing test proves git was never called."""

    def no_git():
        raise FileNotFoundError("Failed to locate the git executable.")

    monkeypatch.setattr("iructl._git.locate_git", no_git)


def test_list_runs_in_marker_only_repo_without_git(tmp_path: Path):
    # A .iructl marker with no git repository at all.
    repo = tmp_path / "repo"
    (repo / RepositoryDirectory.PROFILES).mkdir(parents=True)
    (repo / ROOT_MARKER).touch()

    result = runner.invoke(app, ["--no-git", "--repo", str(repo), "profile", "list", "--local"])

    assert result.exit_code == 0, result.output


def test_new_scaffolds_without_creating_a_git_repo(tmp_path: Path):
    repo = tmp_path / "fresh"

    result = runner.invoke(app, ["--no-git", "new", str(repo)])

    assert result.exit_code == 0, result.output
    assert (repo / ROOT_MARKER).is_file()
    assert (repo / RepositoryDirectory.PROFILES).is_dir()
    assert (repo / RepositoryDirectory.SCRIPTS).is_dir()
    assert (repo / RepositoryDirectory.APPS).is_dir()
    assert not (repo / ".git").exists()
    assert not (repo / ".gitignore").exists()
    assert not (repo / "payloads" / ".gitignore").exists()
