import re
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_BRANDING

runner = CliRunner()


def _collapse_whitespace(text: str) -> str:
    """Flatten rich-box line wrapping so substring matches span wrapped words.

    Strips the vertical box-drawing char along with whitespace, since rich wraps
    long messages across lines and the border ends up between words.
    """
    return re.sub(r"[\s│]+", " ", text)


@pytest.mark.usefixtures("tmp_path_cd")
def test_no_git_repo():
    profile_id = str(uuid4())
    results = {}
    results["list"] = runner.invoke(app, ["profile", "list"])
    results["pull"] = runner.invoke(app, ["profile", "pull", "--id", profile_id])
    results["push"] = runner.invoke(app, ["profile", "push", "--id", profile_id])
    results["sync"] = runner.invoke(app, ["profile", "sync", "--id", profile_id])
    results["delete"] = runner.invoke(app, ["profile", "delete", "--id", profile_id])

    for result in results.values():
        assert result.exit_code == 2
        stderr = _collapse_whitespace(result.stderr)
        assert "Invalid value" in stderr
        assert f"is not a valid {APP_BRANDING}" in stderr


def test_git_repo_no_marker(git_repo):
    profile_id = str(uuid4())
    results = {}
    results["list"] = runner.invoke(app, ["profile", "list", "--repo", git_repo])
    results["pull"] = runner.invoke(app, ["profile", "pull", "--repo", git_repo, "--id", profile_id])
    results["push"] = runner.invoke(app, ["profile", "push", "--repo", git_repo, "--id", profile_id])
    results["sync"] = runner.invoke(app, ["profile", "sync", "--repo", git_repo, "--id", profile_id])
    results["delete"] = runner.invoke(app, ["profile", "delete", "--repo", git_repo, "--id", profile_id])

    for result in results.values():
        assert result.exit_code == 2
        stderr = _collapse_whitespace(result.stderr)
        assert "Invalid value" in stderr
        assert f"is not a valid {APP_BRANDING}" in stderr
