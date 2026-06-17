import random
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


def compare_script_object(script1, script2, expected_diff):
    for k in set(script1.info.model_dump().keys()):
        if k in expected_diff:
            assert getattr(script1.info, k, None) != getattr(script2.info, k, None)
        else:
            assert getattr(script1.info, k, None) == getattr(script2.info, k, None)

    if "audit" not in expected_diff:
        assert script1.audit.content == script2.audit.content
    else:
        assert script1.audit.content != script2.audit.content

    if script1.remediation is None and script2.remediation is None:
        assert "remediation" not in expected_diff
    elif script1.remediation is None or script2.remediation is None:
        assert "remediation" in expected_diff
    elif script1.remediation.content == script2.remediation.content:
        assert "remediation" not in expected_diff
    elif script1.remediation.content != script2.remediation.content:
        assert "remediation" in expected_diff
    else:
        raise pytest.fail("Remediation script comparison got unexpected input.")


def test_help():
    result = runner.invoke(app, ["script", "push", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} script push" in result.stdout
    assert "Push local custom script changes to Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_dry_run(scripts_lrc):
    local, _, _ = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "push", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created script:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated script:", result.stdout)) == 1
    assert len(re.findall(r"Would have deleted script:", result.stdout)) == 0
    assert "Would have deleted script:" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no scripts have changed
    assert Repository.load_path(model=CustomScript) == local


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_clean(scripts_lrc):
    local, _, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "push", "--all", "--clean"])
    assert result.exit_code == 0

    # Check output
    assert "Pushing 3 changes to Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 1
    assert len(re.findall(r"deleted in Iru", result.stdout)) == 1
    assert "Push operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+1\s+0", result.stdout)
    assert re.search(r"Deleted\s+1\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Remote only updates\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    repo = Repository.load_path(model=CustomScript)

    # Check that the created script's id has been updated along with dependent fields
    old_created_script = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_script = repo[str(old_created_script.info_path)]
    assert old_created_script.id not in repo
    assert new_created_script.id in repo
    compare_script_object(
        old_created_script,
        new_created_script,
        {"id", "created_at", "updated_at", "sync_hash", "mdm_identifier"},
    )

    # Check that the updated script has not changed.
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_script_object(local[updated_id], repo[updated_id], {"updated_at", "sync_hash"})

    # Check that the deleted script is still not in the repo
    deleted_id = changes[ChangeType.CREATE_REMOTE][0][1].id
    assert deleted_id not in repo


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_force(scripts_lrc):
    local, _, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "push", "--all", "--force"])
    assert result.exit_code == 0

    # Check output
    assert "Pushing 4 changes to Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 3
    assert len(re.findall(r"deleted in Iru", result.stdout)) == 0
    assert "Push operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+3\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Remote only item\s+1", result.stdout)
    assert "Updated on remote only" not in result.stdout

    repo = Repository.load_path(model=CustomScript)

    # Check that the created script's id has been updated along with dependent fields
    old_created_script = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_script = repo[str(old_created_script.info_path)]
    assert old_created_script.id not in repo
    assert new_created_script.id in repo
    compare_script_object(
        old_created_script,
        new_created_script,
        {"id", "created_at", "updated_at", "sync_hash", "script", "mdm_identifier"},
    )

    # Check that the updated script has not changed.
    for change_type in (ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT):
        updated_id = changes[change_type][0][0].id
        compare_script_object(local[updated_id], repo[updated_id], {"updated_at", "sync_hash", "script"})

    # check that the update_remote script has been reverted to the local state
    updated_id = changes[ChangeType.UPDATE_REMOTE][0][0].id
    compare_script_object(local[updated_id], repo[updated_id], {"updated_at"})


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_by_id_and_path(scripts_lrc):
    local, _, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    cmd_args = ["script", "push", "--force", "--id", changes[ChangeType.CREATE_REMOTE][0][1].id]
    for idx, change_type in enumerate(changes):
        if change_type == ChangeType.CREATE_REMOTE:
            continue
        if idx % 2 == 0:
            cmd_args.append("--path")
            cmd_args.append(random.choice(changes[change_type])[0].info_path.parent)
        else:
            cmd_args.append("--id")
            cmd_args.append(random.choice(changes[change_type])[0].id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Pushing 4 changes to Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 3
    assert len(re.findall(r"deleted in Iru", result.stdout)) == 0
    assert "Push operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+3\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Remote only item\s+1", result.stdout)
    assert "Updated on remote only" not in result.stdout

    repo = Repository.load_path(model=CustomScript)

    # Check that the created script's id has been updated along with dependent fields
    old_created_script = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_script = repo[str(old_created_script.info_path)]
    assert old_created_script.id not in repo
    assert new_created_script.id in repo
    compare_script_object(
        old_created_script,
        new_created_script,
        {"id", "created_at", "updated_at", "sync_hash", "mdm_identifier"},
    )

    # Check that the updated script has not changed.
    for change_type in (ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT):
        updated_id = changes[change_type][0][0].id
        compare_script_object(local[updated_id], repo[updated_id], {"updated_at", "sync_hash"})

    # check that the update_remote script has been reverted to the local state
    updated_id = changes[ChangeType.UPDATE_REMOTE][0][0].id
    compare_script_object(local[updated_id], repo[updated_id], {"updated_at"})


@pytest.mark.usefixtures("patch_scripts_endpoints", "scripts_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["script", "push", "--force", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd", "scripts_lrc")
def test_invalid_path():
    missing_path = Path("scripts/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["script", "push", "--force", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)
