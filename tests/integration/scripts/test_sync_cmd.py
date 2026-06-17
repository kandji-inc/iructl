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
    result = runner.invoke(app, ["script", "sync", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} script sync" in result.stdout
    assert "Sync custom scripts with Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_dry_run(scripts_lrc):
    local, _, _ = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == local

    result = runner.invoke(app, ["script", "sync", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created script.*in Iru:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated script.*in Iru:", result.stdout)) == 1
    assert len(re.findall(r"Would have created script.*locally:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated script.*locally:", result.stdout)) == 1
    assert "Would have deleted script:" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no scripts have changed
    assert Repository.load_path(model=CustomScript) == local


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_force_push(scripts_lrc):
    pre_local, _, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == pre_local

    result = runner.invoke(app, ["script", "sync", "--all", "--force-mode", "push"])
    assert result.exit_code == 0

    # Check output
    assert "Syncing 5 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 2
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 1
    assert len(re.findall(r"deleted.*successfully", result.stdout)) == 0
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+2\s+0", result.stdout)) == 1  # in push table
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 1  # in pull table
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert "Deleted" not in result.stdout

    post_local = Repository.load_path(model=CustomScript)

    # Check that the created script's id has been updated along with dependent fields
    old_created_script = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_script = post_local[str(old_created_script.info_path)]
    assert old_created_script.id not in post_local
    assert new_created_script.id in post_local
    compare_script_object(
        old_created_script,
        new_created_script,
        {"id", "created_at", "updated_at", "sync_hash"},
    )

    # Check that the pushed scripts have only changed in expected ways. The changes in this case are
    # because the PayloadDisplayName is set to match the info file name field
    for change_type in {ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT}:
        updated_id = changes[change_type][0][0].id
        compare_script_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash", "script"})

    # Check that the pulled scripts match the remote
    for change_type in {ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE}:
        remote_script = changes[change_type][0][1]
        post_local_script = post_local[remote_script.id]
        compare_script_object(
            remote_script,
            post_local_script,
            {"sync_hash", "audit_path", "remediation_path", "info_path"},
        )


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_all_force_pull(scripts_lrc):
    pre_local, _, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == pre_local

    result = runner.invoke(app, ["script", "sync", "--all", "--force-mode", "pull"])
    assert result.exit_code == 0

    # Check output
    assert "Syncing 5 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 1
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 2
    assert len(re.findall(r"deleted.*successfully", result.stdout)) == 0
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+2\s+0", result.stdout)) == 1  # in pull table
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 1  # in push table
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert "Deleted" not in result.stdout

    post_local = Repository.load_path(model=CustomScript)

    # Check that the created script's id has been updated along with dependent fields
    old_created_script = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_script = post_local[str(old_created_script.info_path)]
    assert old_created_script.id not in post_local
    assert new_created_script.id in post_local
    compare_script_object(
        old_created_script,
        new_created_script,
        {"id", "created_at", "updated_at", "sync_hash"},
    )

    # Check that the pushed scripts have only changed in expected ways. The changes in this case are
    # because the PayloadDisplayName is set to match the info file name field
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_script_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash", "script"})

    # Check that the pulled scripts match the remote
    for change_type in {ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT}:
        remote_script = changes[change_type][0][1]
        post_local_script = post_local[remote_script.id]
        compare_script_object(
            remote_script,
            post_local_script,
            {"sync_hash", "audit_path", "remediation_path", "info_path"},
        )


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd")
def test_by_id_and_path(scripts_lrc):
    pre_local, _, changes = scripts_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomScript) == pre_local

    cmd_args = [
        "script",
        "sync",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].info_path.parent),
    ]
    for idx, change_type in enumerate(changes):
        if change_type in {ChangeType.CREATE_REMOTE, ChangeType.CREATE_LOCAL}:
            continue
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(random.choice(changes[change_type])[0].info_path.parent)
        else:
            cmd_args.append("--id")
            cmd_args.append(random.choice(changes[change_type])[0].id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Syncing 4 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 1
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 1
    assert len(re.findall(r"deleted in Iru", result.stdout)) == 0
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 2
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    post_local = Repository.load_path(model=CustomScript)

    # Check that the created script's id has been updated along with dependent fields
    old_created_script = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_script = post_local[str(old_created_script.info_path)]
    assert old_created_script.id not in post_local
    assert new_created_script.id in post_local
    compare_script_object(
        old_created_script,
        new_created_script,
        {"id", "created_at", "updated_at", "sync_hash"},
    )

    # Check that the pushed scripts have only changed in expected ways. The changes in this case are
    # because the PayloadDisplayName is set to match the info file name field
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_script_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash", "script"})

    # Check that the pulled scripts match the remote
    for change_type in {ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE}:
        remote_script = changes[change_type][0][1]
        post_local_script = post_local[remote_script.id]
        compare_script_object(
            remote_script,
            post_local_script,
            {"sync_hash", "audit_path", "remediation_path", "info_path"},
        )

    # Check that the conflicting script has not changed.
    conflicting_id = changes[ChangeType.CONFLICT][0][0].id
    assert pre_local[conflicting_id] == post_local[conflicting_id]


@pytest.mark.usefixtures("patch_scripts_endpoints", "scripts_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["script", "sync", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_scripts_endpoints", "iructl_repo_cd", "scripts_lrc")
def test_invalid_path():
    missing_path = Path("scripts/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["script", "sync", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)
