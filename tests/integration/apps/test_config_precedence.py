"""End-to-end precedence for the layered `payload_dir` config, exercised through `app set-file`.

The resolution ladder (highest wins) is: CLI flag > env var > repo `.iructl` > user config >
built-in `<repo>/payloads`. `set-file` resolves the payload dir and imports the `--file` installer
into it, so each test pins one rung and asserts the binary landed in the resolved directory. No API
mocking is needed (unlike push/pull, which talk to Iru).
"""

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._constants import PAYLOAD_DIR_ENV, ROOT_MARKER
from iructl._utils import content_suffixed_filename
from iructl.repository import CustomApp, InstallEnforcement, InstallType

runner = CliRunner()

_INSTALLER_BYTES = b"a-different-installer"
# set-file imports under the content-suffixed name (`new_<sha8>.pkg`), not the source name.
_LANDED_NAME = content_suffixed_filename("new.pkg", hashlib.sha256(_INSTALLER_BYTES).hexdigest())


def _make_local_app(repo: Path, factory, *, name: str = "My App") -> CustomApp:
    """Write a local custom-app member (no scripts) under <repo>/apps."""
    member = factory(
        name=name,
        file_name="installer.pkg",
        file_sha256=hashlib.sha256(b"installer").hexdigest(),
        install_type=InstallType.PACKAGE,
        install_enforcement=InstallEnforcement.INSTALL_ONCE,
        has_audit=False,
        has_preinstall=False,
        has_postinstall=False,
    )
    member.ensure_paths(repo / "apps")
    member.write()
    return member


@pytest.mark.parametrize(
    ("set_layers", "winner"),
    [
        pytest.param((), "default", id="default-is-repo-payloads"),
        pytest.param(("user",), "user", id="user-config"),
        pytest.param(("user", "repo"), "repo", id="repo-over-user"),
        pytest.param(("repo", "env"), "env", id="env-over-repo"),
        pytest.param(("env", "flag"), "flag", id="flag-over-env"),
    ],
)
def test_payload_dir_precedence(
    iructl_repo_cd: Path, custom_app_factory, monkeypatch, tmp_path, set_layers: tuple[str, ...], winner: str
):
    member = _make_local_app(iructl_repo_cd, custom_app_factory)
    source = tmp_path / "new.pkg"
    source.write_bytes(_INSTALLER_BYTES)

    layer_dirs = {name: tmp_path / f"{name}-payloads" for name in ("user", "repo", "env", "flag")}

    if "user" in set_layers:
        user_file = tmp_path / "user.yaml"
        user_file.write_text(f"payload_dir: {layer_dirs['user']}\n")
        monkeypatch.setattr("iructl._config.USER_CONFIG_FILE", user_file)
    if "repo" in set_layers:
        (iructl_repo_cd / ROOT_MARKER).write_text(f"payload_dir: {layer_dirs['repo']}\n")
    if "env" in set_layers:
        monkeypatch.setenv(PAYLOAD_DIR_ENV, str(layer_dirs["env"]))
    flag_args = ("--payload-dir", str(layer_dirs["flag"])) if "flag" in set_layers else ()

    expected_dir = (iructl_repo_cd / "payloads") if winner == "default" else layer_dirs[winner]

    result = runner.invoke(
        app, ["app", "set-file", str(member.info_path.parent), "--file", str(source), "--move", *flag_args]
    )

    assert result.exit_code == 0, result.output
    assert (expected_dir / _LANDED_NAME).read_bytes() == _INSTALLER_BYTES


def test_relative_payload_dir_anchors_to_repo_root_not_cwd(iructl_repo: Path, custom_app_factory, tmp_path):
    # cwd is tmp_path (autouse tmp_path_cd) and the repo is a child of it, so a relative payload dir
    # anchored to the repo root lands somewhere different than CWD-relative resolution would.
    member = _make_local_app(iructl_repo, custom_app_factory)
    source = tmp_path / "new.pkg"
    source.write_bytes(_INSTALLER_BYTES)

    result = runner.invoke(
        app,
        ["--repo", str(iructl_repo), "app", "set-file", str(member.info_path.parent),
         "--file", str(source), "--move", "--payload-dir", "staging"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert (iructl_repo / "staging" / _LANDED_NAME).read_bytes() == _INSTALLER_BYTES
    assert not (tmp_path / "staging").exists()  # not CWD-relative
