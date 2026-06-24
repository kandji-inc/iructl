import random
import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.repository import CustomApp, Repository
from tests.fixtures.apps import INSTALLER, INSTALLER_NAME, compare_app_object, place_installer
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["app", "sync", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} app sync" in result.stdout
    assert "Sync custom apps with Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_dry_run(apps_lrc):
    local, _, _ = apps_lrc

    result = runner.invoke(app, ["app", "sync", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created app.*in Iru:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated app.*in Iru:", result.stdout)) == 1
    assert len(re.findall(r"Would have created app.*locally:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated app.*locally:", result.stdout)) == 1
    assert "Would have deleted app:" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no apps have changed
    assert Repository.load_path(model=CustomApp) == local


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_force_push(apps_lrc):
    pre_local, _, changes = apps_lrc

    result = runner.invoke(app, ["app", "sync", "--all", "--force-mode", "push"])
    assert result.exit_code == 0

    # Check output
    assert "Syncing 5 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 2
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 1
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+2\s+0", result.stdout)) == 1  # in push table
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 1  # in pull table
    assert "Installer Transfer Summary" in result.stdout
    assert re.search(r"Uploaded\s+1", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)

    post_local = Repository.load_path(model=CustomApp)

    # Check that the created app's id has been updated along with dependent fields
    old_created_app = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_app = post_local[str(old_created_app.info_path)]
    assert old_created_app.id not in post_local
    assert new_created_app.id in post_local
    compare_app_object(old_created_app, new_created_app, {"id", "created_at", "updated_at", "sync_hash"})

    # Check that the pushed apps have changed only in the expected sync fields
    for change_type in (ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT):
        updated_id = changes[change_type][0][0].id
        compare_app_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash"})

    # Check that the pulled apps match the remote
    for change_type in (ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE):
        remote_app = changes[change_type][0][1]
        compare_app_object(remote_app, post_local[remote_app.id], {"sync_hash"})


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_force_pull(apps_lrc):
    pre_local, _, changes = apps_lrc

    result = runner.invoke(app, ["app", "sync", "--all", "--force-mode", "pull"])
    assert result.exit_code == 0

    # Check output
    assert "Syncing 5 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 1
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 2
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+2\s+0", result.stdout)) == 1  # in pull table
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 1  # in push table
    assert "Installer Transfer Summary" in result.stdout
    assert re.search(r"Uploaded\s+1", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)

    post_local = Repository.load_path(model=CustomApp)

    # Check that the created app's id has been updated along with dependent fields
    old_created_app = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_app = post_local[str(old_created_app.info_path)]
    assert old_created_app.id not in post_local
    assert new_created_app.id in post_local
    compare_app_object(old_created_app, new_created_app, {"id", "created_at", "updated_at", "sync_hash"})

    # Check that the pushed app has changed only in the expected sync fields
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_app_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash"})

    # Check that the pulled apps match the remote
    for change_type in (ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT):
        remote_app = changes[change_type][0][1]
        compare_app_object(remote_app, post_local[remote_app.id], {"sync_hash"})


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_by_id_and_path(apps_lrc):
    pre_local, _, changes = apps_lrc

    cmd_args = [
        "app",
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
    assert "Installer Transfer Summary" in result.stdout
    assert re.search(r"Uploaded\s+1", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    post_local = Repository.load_path(model=CustomApp)

    # Check that the created app's id has been updated along with dependent fields
    old_created_app = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_app = post_local[str(old_created_app.info_path)]
    assert old_created_app.id not in post_local
    assert new_created_app.id in post_local
    compare_app_object(old_created_app, new_created_app, {"id", "created_at", "updated_at", "sync_hash"})

    # Check that the pushed app has changed only in the expected sync fields
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_app_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash"})

    # Check that the pulled apps match the remote
    for change_type in (ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE):
        remote_app = changes[change_type][0][1]
        compare_app_object(remote_app, post_local[remote_app.id], {"sync_hash"})

    # Check that the conflicting app (skip mode) has not changed.
    conflicting_id = changes[ChangeType.CONFLICT][0][0].id
    assert pre_local[conflicting_id] == post_local[conflicting_id]


@pytest.mark.usefixtures("patch_apps_endpoints", "apps_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["app", "sync", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_invalid_path(apps_lrc):
    missing_path = Path("apps/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["app", "sync", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)


@pytest.mark.usefixtures("patch_apps_endpoints", "unchanged_app", "stub_installer_download")
def test_sync_force_mode_pull_overwrites_differing_installer(iructl_repo_cd):
    place_installer(iructl_repo_cd, content=b"stale-local-bytes")

    result = runner.invoke(app, ["app", "sync", "--all", "--download", "--force-mode", "pull"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER


@pytest.mark.usefixtures("patch_apps_endpoints")
def test_sync_download_mismatch_hint_points_to_app_download(iructl_repo_cd, unchanged_app, monkeypatch):
    # A mismatch is fixed by the dedicated single-app download command, the same for pull and sync.
    place_installer(iructl_repo_cd, content=b"stale-local-bytes")

    def fail_download(*args, **kwargs):
        pytest.fail("download should not run on a mismatch without --force")

    monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

    result = runner.invoke(app, ["app", "sync", "--all", "--download"])

    assert result.exit_code == 0, result.output
    assert f"app download {unchanged_app.id} --force" in result.output
    assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"


@pytest.mark.usefixtures("patch_apps_endpoints", "unchanged_app", "stub_installer_download")
def test_download_creates_payloads_gitignore(iructl_repo_cd):
    result = runner.invoke(app, ["app", "sync", "--all", "--download"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo_cd / "payloads" / ".gitignore").read_text() == "*\n"
