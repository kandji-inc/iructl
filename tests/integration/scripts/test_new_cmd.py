import json
import plistlib

from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_BRANDING, APP_NAME
from iructl.repository import Script

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["script", "new", "--help"])

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that the help message contains the expected content
    assert f"Usage: {APP_NAME} script new [OPTIONS]" in result.stdout.replace("\n", "")


def test_prompt_for_name(iructl_repo_cd):
    result = runner.invoke(app, ["script", "new"], input="Test Script\n")

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert "New script created at" in result.stdout.replace("\n", "")

    # Check that the script directory was created
    script_dir = "scripts/Test Script"
    assert script_dir in result.stdout.replace("\n", "")


def test_empty_script(iructl_repo_cd):
    result = runner.invoke(app, ["script", "new", "--name", "New Script"])
    script_dir = iructl_repo_cd / "scripts/New Script"

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"New script created at {script_dir}" in result.stdout.replace("\n", "")

    # Check that the script directory was created
    assert script_dir.is_dir()

    # Check that the info file was created and is a valid plist
    info_file = script_dir / "info.plist"
    assert info_file.is_file()
    with info_file.open("rb") as file:
        info_data = plistlib.load(file)

    assert info_data["name"] == "New Script"
    assert info_data["active"] is False
    assert info_data["execution_frequency"] == "once"
    assert info_data["restart"] is False

    # Check that the audit script was created and is a valid plist
    audit_script = script_dir / "audit.zsh"
    assert audit_script.is_file()

    # Check that no remediation script was created
    remediation_script = script_dir / "remediation"
    assert not remediation_script.is_file()


def test_empty_script_with_options(iructl_repo):
    result = runner.invoke(
        app,
        [
            "script",
            "new",
            "--name",
            "Test Script",
            "--active",
            "--execution-frequency",
            "every_15_min",
            "--restart",
            "--include-remediation",
            "--output",
            str(iructl_repo / "scripts/subdirectory"),
            "--info-format",
            "json",
        ],
    )
    scripts_dir = iructl_repo / "scripts/subdirectory/Test Script"

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"New script created at {scripts_dir}" in result.stdout.replace("\n", "")

    # Check that the script directory was created
    assert scripts_dir.is_dir()

    # Check that the info file was created and is a valid json
    info_file = scripts_dir / "info.json"
    assert info_file.is_file()
    with info_file.open("rb") as file:
        info_data = json.load(file)

    assert info_data["name"] == "Test Script"
    assert info_data["active"] is True
    assert info_data["execution_frequency"] == "every_15_min"
    assert info_data["restart"] is True

    # Check that the audit script was created and is a valid plist
    audit_script = scripts_dir / "audit.zsh"
    assert audit_script.is_file()

    # Check that no remediation script was created
    remediation_script = scripts_dir / "remediation.zsh"
    assert remediation_script.is_file()


def test_empty_script_with_invalid_output_path(tmp_path):
    result = runner.invoke(app, ["script", "new", "--name", "New Script", "--output", str(tmp_path)])

    # Check that the command failed
    assert result.exit_code == 2

    # Check that the error message contains the expected content
    assert "must be located inside a scripts directory of" in result.stderr
    assert f"a valid {APP_BRANDING} repository" in result.stderr


def test_import_script(script_file, iructl_repo_cd):
    script_content = script_file.read_text()
    result = runner.invoke(app, ["script", "new", "--name", "New Script", "--import-audit", str(script_file), "--move"])
    script_dir = iructl_repo_cd / "scripts/New Script"  # Default path and name

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert "Script imported at" in result.stdout

    # Check that the script directory was created and the script file was moved
    assert script_dir.is_dir()
    assert not script_file.exists()

    # Check that the info file was created and is a valid plist
    info_file = script_dir / "info.plist"
    assert info_file.is_file()

    # Check that the mobileconfig file was created and is a valid plist
    audit_file = script_dir / "audit.sh"
    assert audit_file.is_file()
    assert script_content == audit_file.read_text()


def test_import_script_copy(script_file, iructl_repo_cd):
    result = runner.invoke(app, ["script", "new", "--name", "New Script", "--import-audit", str(script_file), "--copy"])
    script_dir = iructl_repo_cd / "scripts/New Script"

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"Script imported at {script_dir}" in result.stdout.replace("\n", "")

    # Check that the script directory was created and the script file was copied instead of moved
    audit_file = script_dir / "audit.sh"
    assert audit_file.is_file()
    assert hash(audit_file.read_bytes()) == hash(audit_file.read_bytes())


def test_import_with_remediation(script_content, iructl_repo_cd):
    audit_script = iructl_repo_cd / "audit.sh"
    audit_script.write_text(script_content)

    remediation_script = iructl_repo_cd / "remediation.sh"
    remediation_script.write_text(script_content)

    result = runner.invoke(
        app,
        [
            "script",
            "new",
            "--name",
            "New Script",
            "--import-audit",
            str(audit_script),
            "--import-remediation",
            str(remediation_script),
        ],
    )
    script_dir = iructl_repo_cd / "scripts/New Script"  # Default path and name

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert "Script imported at" in result.stdout

    # Check that the script directory was created and the script file was moved
    assert script_dir.is_dir()
    assert audit_script.is_file()
    assert remediation_script.is_file()
    assert (script_dir / "audit.sh").is_file()
    assert (script_dir / "remediation.sh").is_file()

    # Check that the info file was created
    info_file = script_dir / "info.plist"
    assert info_file.is_file()


def test_import_audit_with_generated_remediation(script_file, iructl_repo_cd):
    result = runner.invoke(
        app,
        ["script", "new", "--name", "New Script", "--import-audit", str(script_file), "--include-remediation"],
    )
    script_dir = iructl_repo_cd / "scripts/New Script"  # Default path and name

    # Check that the command ran successfully
    assert result.exit_code == 0
    assert "Script imported at" in result.stdout

    # The imported audit keeps its suffix; the generated remediation uses the default suffix.
    assert (script_dir / "audit.sh").is_file()
    remediation_script = script_dir / "remediation.zsh"
    assert remediation_script.is_file()
    assert remediation_script.read_text() == Script.default_content


def test_import_script_with_name_in_repo(script_file, iructl_repo_cd):
    new_script_name = "Test Script Name"
    result = runner.invoke(
        app,
        ["script", "new", "--import-audit", str(script_file), "--name", new_script_name],
    )
    script_dir = iructl_repo_cd / "scripts" / new_script_name

    # Check that the command ran successfully
    assert result.exit_code == 0

    # Check that command output contains the expected message
    assert f"Script imported at {script_dir}" in result.stdout.replace("\n", "")

    # Check that the specified name was used in place of the original
    assert script_dir.is_dir()

    # Check that the original script has been copied
    assert script_file.exists()
    assert (script_dir / "audit.sh").is_file()


def test_import_external_script_from_outside_repo(caplog, script_file):
    result = runner.invoke(app, ["script", "new", "--name", "New Script", "--import-audit", str(script_file)])

    assert result.exit_code == 2
    assert f"is not an initialized {APP_BRANDING} repository" in caplog.text


def test_repo_option_defaults_output_dir(iructl_repo):
    # cwd is the non-repo tmp_path (autouse tmp_path_cd), so landing in the repo can only
    # come from --repo defaulting the output dir, not the current directory.
    result = runner.invoke(app, ["--repo", str(iructl_repo), "script", "new", "--name", "Repo Script"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo / "scripts" / "Repo Script").is_dir()


def test_existing_script_directory(iructl_repo_cd):
    # Create a profile directory to take the default path
    name = "New Script"
    runner.invoke(app, ["script", "new"], input=f"{name}\n")

    # Run the command again to create a new profile with an incremented name
    for count in range(1, 10):
        result = runner.invoke(app, ["script", "new"], input=f"{name}\n")
        assert result.exit_code == 0

        script_dir = iructl_repo_cd / f"scripts/{name} ({count})"

        # Check that command output contains the expected message
        assert "New script created at" in result.stdout
        assert script_dir.name in result.stdout

        # Check that the profile directory was created
        assert script_dir.is_dir()

        # Check that the audit was created with the original name
        assert (script_dir / "audit.zsh").is_file()
