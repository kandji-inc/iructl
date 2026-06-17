import subprocess

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._cli.payloads import _GITIGNORE_CONTENTS
from iructl._constants import APP_BRANDING, APP_NAME, PAYLOAD_DIR_ENV, ROOT_MARKER
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["new", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} new" in result.stdout
    assert "Create a new repository" in result.stdout
    assert "A Path to the directory where the new repository" in result.stdout


def test_new(tmp_path):
    repo = tmp_path / "test_repo"
    result = runner.invoke(app, ["new", str(repo)])
    assert result.exit_code == 0
    assert f"Created a new {APP_BRANDING} repository at" in result.stdout
    assert (repo / "README.md").is_file()
    assert (repo / ".gitignore").is_file()
    assert (repo / "profiles").is_dir()
    assert (repo / "scripts").is_dir()
    assert (repo / "apps").is_dir()
    assert (repo / "payloads").is_dir()
    assert (repo / "payloads" / ".gitignore").read_text() == _GITIGNORE_CONTENTS
    assert (repo / ".git").is_dir()


def test_new_relocated_payload_dir_not_scaffolded(tmp_path, monkeypatch):
    external = tmp_path / "external_payloads"
    monkeypatch.setenv(PAYLOAD_DIR_ENV, str(external))
    repo = tmp_path / "test_repo"
    result = runner.invoke(app, ["new", str(repo)])
    assert result.exit_code == 0
    # The payload dir is relocated outside the repo, so new must not scaffold one inside it
    # and must not create the external location either.
    assert not (repo / "payloads").exists()
    assert not external.exists()


def test_new_scaffolds_user_config_payload_dir(tmp_path, monkeypatch):
    user_config = tmp_path / "user.yaml"
    user_config.write_text("payload_dir: vendor\n")
    monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", user_config)
    repo = tmp_path / "test_repo"

    result = runner.invoke(app, ["new", str(repo)])

    assert result.exit_code == 0, result.output
    # The relative user-config payload dir anchors to the new repo root and is scaffolded there.
    assert (repo / "vendor" / ".gitignore").read_text() == _GITIGNORE_CONTENTS
    assert not (repo / "payloads").exists()


def test_new_env_payload_dir_overrides_user_config(tmp_path, monkeypatch):
    user_config = tmp_path / "user.yaml"
    user_config.write_text("payload_dir: vendor\n")
    monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", user_config)
    external = tmp_path / "external_payloads"
    monkeypatch.setenv(PAYLOAD_DIR_ENV, str(external))
    repo = tmp_path / "test_repo"

    result = runner.invoke(app, ["new", str(repo)])

    assert result.exit_code == 0, result.output
    # Env wins over user config; the external dir is outside the repo, so nothing is scaffolded.
    assert not (repo / "vendor").exists()
    assert not (repo / "payloads").exists()
    assert not external.exists()


def test_new_existing(tmp_path):
    repo = tmp_path / "test_repo"
    repo.mkdir()
    result = runner.invoke(app, ["new", str(repo)])
    assert result.exit_code == 2
    assert "already exists." in result.stderr


@pytest.mark.parametrize("sibling_marker", [False, True], ids=["empty-git-repo", "with-sibling-iructl-repo"])
def test_new_inside_git_repo_allowed(git_repo, sibling_marker):
    # Multiple iructl repos may live in one git repo: a marker in a sibling subtree must not block.
    if sibling_marker:
        (git_repo / "iru_a").mkdir()
        (git_repo / "iru_a" / ROOT_MARKER).touch()
    repo = git_repo / "iru_b"
    result = runner.invoke(app, ["new", str(repo)])
    assert result.exit_code == 0
    assert f"Created a new {APP_BRANDING} repository at" in result.stdout
    assert (repo / ROOT_MARKER).is_file()
    assert not (repo / ".git").is_dir()


@pytest.mark.parametrize("with_git", [False, True], ids=["marker-only", "marker-and-git"])
def test_new_inside_iructl_repo_rejected(tmp_path, with_git):
    # Nesting inside an iructl repo is rejected whether or not the parent is a git working tree.
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ROOT_MARKER).touch()
    if with_git:
        subprocess.run(["git", "init", str(parent)], check=True, capture_output=True)
    result = runner.invoke(app, ["new", str(parent / "child")])
    assert result.exit_code == 2
    assert f"is already a {APP_BRANDING} repository" in normalize_output(result.stderr)
    assert not (parent / "child").exists()
