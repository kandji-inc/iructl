import json
import plistlib
import shutil

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_BRANDING, APP_NAME, INFO_FORMAT_ENV
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["profile", "new", "--help"])

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that the help message contains the expected content
    assert f"Usage: {APP_NAME} profile new [OPTIONS]" in result.stdout.replace("\n", "")


def test_empty_profile(iructl_repo_cd):
    result = runner.invoke(app, ["profile", "new", "--name", "New Profile"])
    profile_dir = iructl_repo_cd / "profiles/New Profile"

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"New profile created at {profile_dir}" in result.stdout.replace("\n", "")

    # Check that the profile directory was created
    assert profile_dir.is_dir()

    # Check that the info file was created and is a valid plist
    info_file = profile_dir / "info.plist"
    assert info_file.is_file()
    with info_file.open("rb") as file:
        info_data = plistlib.load(file)

    # Check that the mobileconfig file was created and is a valid plist
    mobileconfig_file = profile_dir / "profile.mobileconfig"
    assert mobileconfig_file.is_file()
    assert not mobileconfig_file.read_bytes().startswith(b"bplist00")
    with mobileconfig_file.open("rb") as file:
        mobileconfig_data = plistlib.load(file)

    # Check that the info file and mobileconfig file have the expected data
    assert mobileconfig_data["PayloadDisplayName"] == info_data["name"] == "New Profile"
    assert mobileconfig_data["PayloadUUID"].upper() == info_data["id"].upper()
    assert mobileconfig_data["PayloadIdentifier"] == info_data["mdm_identifier"]
    assert info_data["runs_on_mac"] is True
    assert info_data["runs_on_iphone"] is True
    assert info_data["runs_on_ipad"] is True
    assert info_data["runs_on_tv"] is True
    assert info_data["runs_on_vision"] is True
    assert info_data["active"] is False


def test_empty_profile_with_options(iructl_repo):
    result = runner.invoke(
        app,
        [
            "profile",
            "new",
            "--name",
            "Test Profile",
            "--runs-on",
            "mac",
            "--runs-on",
            "iphone",
            "--runs-on",
            "ipad",
            "--active",
            "--output",
            str(iructl_repo / "profiles/subdirectory"),
            "--info-format",
            "json",
        ],
    )
    profile_dir = iructl_repo / "profiles/subdirectory/Test Profile"

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"New profile created at {profile_dir}" in result.stdout.replace("\n", "")

    # Check that the profile directory was created
    assert profile_dir.is_dir()

    # Check that the info file was created and is a valid json
    info_file = profile_dir / "info.json"
    assert info_file.is_file()
    with info_file.open("rb") as file:
        info_data = json.load(file)

    # Check that the mobileconfig file was created and is a valid plist
    mobileconfig_file = profile_dir / "profile.mobileconfig"
    assert mobileconfig_file.is_file()
    assert not mobileconfig_file.read_bytes().startswith(b"bplist00")
    with mobileconfig_file.open("rb") as file:
        mobileconfig_data = plistlib.load(file)

    # Check that the info file and mobileconfig file have the expected data
    assert mobileconfig_data["PayloadDisplayName"] == info_data["name"] == "Test Profile"
    assert mobileconfig_data["PayloadUUID"].upper() == info_data["id"].upper()
    assert mobileconfig_data["PayloadIdentifier"] == info_data["mdm_identifier"]
    assert info_data["runs_on_mac"] is True
    assert info_data["runs_on_iphone"] is True
    assert info_data["runs_on_ipad"] is True
    assert "runs_on_tv" not in info_data
    assert "runs_on_vision" not in info_data
    assert info_data["active"] is True


def test_empty_profile_with_invalid_output_path(tmp_path):
    result = runner.invoke(app, ["profile", "new", "--name", "New Profile", "--output", str(tmp_path)])

    # Check that the command failed
    assert result.exit_code == 2

    # Check that the error message contains the expected content
    assert "must be located inside a profiles directory" in result.stderr
    assert f"of a valid {APP_BRANDING} repository" in result.stderr


def test_import_profile(mobileconfig_data, mobileconfig_file, iructl_repo_cd):
    result = runner.invoke(app, ["profile", "new", "--import", str(mobileconfig_file), "--move"])
    profile_dir = iructl_repo_cd / "profiles" / mobileconfig_data["name"]

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"Profile imported at {profile_dir}" in result.stdout.replace("\n", "")

    # Check that the profile directory was created and the profile file was moved
    assert profile_dir.is_dir()
    assert not mobileconfig_file.exists()

    # Check that the info file was created and is a valid plist
    info_file = profile_dir / "info.plist"
    assert info_file.is_file()
    with info_file.open("rb") as file:
        info_data = plistlib.load(file)

    # Check that the mobileconfig file was created and is a valid plist
    mobileconfig_matches = list(profile_dir.glob("*.mobileconfig"))
    assert len(mobileconfig_matches) == 1
    mobileconfig_file = mobileconfig_matches[0]
    with mobileconfig_file.open("rb") as file:
        mobileconfig_file_data = plistlib.load(file)

    # Check that the info file and mobileconfig file have the expected data
    assert mobileconfig_file_data["PayloadDisplayName"] == info_data["name"] == mobileconfig_data["name"]
    assert mobileconfig_file_data["PayloadUUID"].upper() != info_data["id"].upper()

    assert mobileconfig_file_data["PayloadIdentifier"] != info_data["mdm_identifier"]
    assert info_data["runs_on_mac"] is True
    assert info_data["runs_on_iphone"] is True
    assert info_data["runs_on_ipad"] is True
    assert info_data["runs_on_tv"] is True
    assert info_data["runs_on_vision"] is True
    assert info_data["active"] is False


def test_import_profile_copy(mobileconfig_data, mobileconfig_file, iructl_repo_cd):
    result = runner.invoke(app, ["profile", "new", "--import", str(mobileconfig_file), "--copy"])
    profile_dir = iructl_repo_cd / "profiles" / mobileconfig_data["name"]

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"Profile imported at {profile_dir}" in result.stdout.replace("\n", "")

    # Check that the profile directory was created and the profile file was copied instead of moved
    mobileconfig_matches = list(profile_dir.glob("*.mobileconfig"))
    assert len(mobileconfig_matches) == 1
    mobileconfig_file = mobileconfig_matches[0]
    assert hash(mobileconfig_file.read_bytes()) == hash(mobileconfig_file.read_bytes())


def test_import_profile_with_name_in_repo(mobileconfig_file, iructl_repo_cd):
    # Move the profile file into the repository
    mobileconfig_file = shutil.move(mobileconfig_file, iructl_repo_cd / "profiles" / mobileconfig_file.name)

    new_profile_name = "Test Profile"
    result = runner.invoke(
        app,
        ["profile", "new", "--name", "New Profile", "--import", str(mobileconfig_file), "--name", new_profile_name],
    )
    profile_dir = iructl_repo_cd / "profiles" / new_profile_name

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"Profile imported at {profile_dir}" in result.stdout.replace("\n", "")

    # Check that the specified name was used in place of the original
    assert not mobileconfig_file.with_suffix("").exists()
    assert mobileconfig_file.with_name(new_profile_name).is_dir()

    # Check that the original profile has been copied
    assert mobileconfig_file.exists()
    assert len(list(profile_dir.glob("*.mobileconfig"))) == 1


def test_import_external_profile_from_outside_repo(caplog, mobileconfig_file):
    result = runner.invoke(app, ["profile", "new", "--name", "New Profile", "--import", str(mobileconfig_file)])

    assert result.exit_code == 2
    assert f"is not an initialized {APP_BRANDING} repository" in caplog.text


def test_repo_option_defaults_output_dir(iructl_repo):
    # cwd is the non-repo tmp_path (autouse tmp_path_cd), so landing in the repo can only
    # come from --repo defaulting the output dir, not the current directory.
    result = runner.invoke(app, ["--repo", str(iructl_repo), "profile", "new", "--name", "Repo Profile"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo / "profiles" / "Repo Profile").is_dir()


def test_existing_profile_directory(iructl_repo_cd):
    # Create a profile directory to take the default path
    name = "New Profile"
    runner.invoke(app, ["profile", "new"], input=f"{name}\n")

    # Run the command again to create a new profile with an incremented name
    for count in range(1, 10):
        result = runner.invoke(app, ["profile", "new"], input=f"{name}\n")
        assert result.exit_code == 0

        profile_dir = iructl_repo_cd / f"profiles/{name} ({count})"

        # Check that command output contains the expected message
        assert "New profile created at" in result.stdout
        assert profile_dir.name in result.stdout

        # Check that the profile directory was created
        assert profile_dir.is_dir()

        # Check that the mobileconfig file was created with the original name
        assert (profile_dir / "profile.mobileconfig").is_file()


@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [
        pytest.param(None, None, "info.plist", id="default-plist"),
        pytest.param(None, "yaml", "info.yaml", id="flag"),
        pytest.param("json", None, "info.json", id="env"),
        pytest.param("yaml", "plist", "info.plist", id="flag-over-env"),
    ],
)
def test_new_writes_info_in_configured_format(iructl_repo_cd, monkeypatch, env, flag, expected):
    if env is not None:
        monkeypatch.setenv(INFO_FORMAT_ENV, env)
    args = ["profile", "new", "--name", "Fmt Profile"]
    if flag is not None:
        args += ["--info-format", flag]

    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output

    assert (iructl_repo_cd / f"profiles/Fmt Profile/{expected}").is_file()


def test_new_rejects_invalid_info_format_env(iructl_repo_cd, monkeypatch):
    monkeypatch.setenv(INFO_FORMAT_ENV, "bogus")

    result = runner.invoke(app, ["profile", "new", "--name", "Bad Profile"])
    assert result.exit_code == 2
    assert not (iructl_repo_cd / "profiles/Bad Profile").exists()


def test_new_deprecated_format_flag_warns(iructl_repo_cd):
    # The deprecated --format still creates the file but warns to use --info-format.
    result = runner.invoke(app, ["profile", "new", "--name", "Deprecated Profile", "--format", "json"])
    assert result.exit_code == 0, result.output

    assert (iructl_repo_cd / "profiles/Deprecated Profile/info.json").is_file()
    assert "deprecated" in normalize_output(result.stderr)
