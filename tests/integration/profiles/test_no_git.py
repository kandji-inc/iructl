"""`--no-git` / `git_enabled: false` gate the commits that wrap a push."""

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import ROOT_MARKER

runner = CliRunner()


@pytest.fixture
def commit_spy(monkeypatch):
    """Replace git.commit_all_changes with a call recorder (consumed via git.commit_all_changes)."""
    calls = []

    def fake_commit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("iructl._git.commit_all_changes", fake_commit)
    return calls


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc")
@pytest.mark.parametrize(
    ("cli_args", "repo_config", "expect_enabled"),
    [
        pytest.param([], None, True, id="commits-by-default"),
        pytest.param(["--no-git"], None, False, id="no-git-flag-skips-commits"),
        pytest.param([], "git_enabled: false\n", False, id="git-enabled-false-config-skips-commits"),
    ],
)
def test_git_setting_gates_commits(iructl_repo_cd, commit_spy, cli_args, repo_config, expect_enabled):
    if repo_config is not None:
        (iructl_repo_cd / ROOT_MARKER).write_text(repo_config)

    result = runner.invoke(app, [*cli_args, "profile", "push", "--all", "--force"])

    assert result.exit_code == 0
    assert commit_spy
    assert all(call["enabled"] == expect_enabled for call in commit_spy)
