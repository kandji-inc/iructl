import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.repository import CustomScript, Repository
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["script", "delete", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} script delete" in result.stdout
    assert "Delete scripts from your local repository or Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_dry_run(scripts_lrc):
    local, _, _ = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "delete", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have deleted script.*in Iru:", result.stdout)) == 9
    assert len(re.findall(r"Would have deleted script.*locally:", result.stdout)) == 9
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no scripts have changed
    assert Repository.load_path(model=CustomScript) == local


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all(scripts_lrc):
    local, _, _ = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "delete", "--all", "--force"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 10 scripts..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 9
    assert len(re.findall(r"in Iru", result.stdout)) == 9
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Both\s+8\s+0", result.stdout)
    assert re.search(r"Local\s+1\s+0", result.stdout)
    assert re.search(r"Remote\s+1\s+0", result.stdout)

    # Check no scripts have changed
    assert len(Repository.load_path(model=CustomScript)) == 0


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_by_id_and_path(scripts_lrc):
    pre_local, remote, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == pre_local

    cmd_args = [
        "script",
        "delete",
        "--force",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].info_path.parent),
    ]
    script_ids = list(set(pre_local.keys()) & set(remote.keys()))[:4]
    for idx, script_id in enumerate(script_ids):
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(str(pre_local[script_id].info_path.parent))
        else:
            cmd_args.append("--id")
            cmd_args.append(script_id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 6 scripts..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 5
    assert len(re.findall(r"in Iru", result.stdout)) == 5
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Both\s+4\s+0", result.stdout)
    assert re.search(r"Local\s+1\s+0", result.stdout)
    assert re.search(r"Remote\s+1\s+0", result.stdout)

    post_local = Repository.load_path(model=CustomScript)
    deleted_set = {changes[ChangeType.CREATE_REMOTE][0][1].id, changes[ChangeType.CREATE_LOCAL][0][0].id, *script_ids}
    all_id_set = set(pre_local.keys()) | set(remote.keys())
    for script_id in all_id_set:
        if script_id in deleted_set:
            assert script_id not in post_local
        else:
            assert script_id in post_local


@pytest.mark.usefixtures("iructl_repo_cd")
def test_local_only(scripts_lrc):
    local, _, _ = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "delete", "--all", "--force", "--local"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 9 scripts..." in result.stdout
    assert len(re.findall(r"local repo", result.stdout)) == 9
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Local\s+9\s+0", result.stdout)

    # Check no scripts have changed
    assert len(Repository.load_path(model=CustomScript)) == 0


@pytest.mark.usefixtures("iructl_repo_cd")
def test_remote_only(scripts_lrc, patch_scripts_endpoints):
    local, _, _ = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "delete", "--all", "--force", "--remote"])
    assert result.exit_code == 0

    # Check that api was called to delete scripts
    assert patch_scripts_endpoints["delete"] == 9

    # Check output
    assert "Running in dry-run mode" not in result.stdout
    assert "Deleting 9 scripts..." in result.stdout
    assert len(re.findall(r"in Iru", result.stdout)) == 9
    assert "local repo" not in result.stdout
    assert "Dry run complete. No changes were made." not in result.stdout
    assert "Delete operation complete!" in result.stdout

    # Check the summary table
    assert re.search(r"Remote\s+9\s+0", result.stdout)

    # Check no local scripts have changed
    assert Repository.load_path(model=CustomScript) == local


@pytest.mark.usefixtures("patch_scripts_endpoints", "scripts_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["script", "delete", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd", "scripts_lrc")
def test_invalid_path():
    missing_path = Path("scripts/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["script", "delete", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)
