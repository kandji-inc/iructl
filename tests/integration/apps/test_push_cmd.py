import plistlib
import random
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.exceptions import PayloadTransferError
from iructl.repository import CustomApp, Repository
from tests.fixtures.apps import INSTALLER, INSTALLER_NAME, compare_app_object, make_local_app, place_installer
from tests.output import normalize_output

runner = CliRunner()


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppPush:
    def test_create_uploads_installer(self, iructl_repo_cd, custom_app_factory, patch_apps_endpoints, apps_remote):
        make_local_app(iructl_repo_cd, custom_app_factory)
        place_installer(iructl_repo_cd)

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert patch_apps_endpoints["create"] == 1
        assert len(apps_remote) == 1

    def test_does_not_create_payloads_gitignore(self, iructl_repo_cd, custom_app_factory):
        # Push uploads existing installers; it never downloads, so it leaves the payload dir alone.
        make_local_app(iructl_repo_cd, custom_app_factory)
        place_installer(iructl_repo_cd)

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert not (iructl_repo_cd / "payloads" / ".gitignore").exists()


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppInstallerSyncWarnings:
    """Push/sync warn when an app's installer reference is out of sync with the payload dir (SYS-2886)."""

    @pytest.mark.parametrize("command", ["push", "sync"])
    def test_warns_on_mismatched_clean_installer(self, command, iructl_repo_cd, unchanged_app, patch_apps_endpoints):
        place_installer(iructl_repo_cd, b"a-new-installer", "new.pkg")
        info_data = plistlib.loads(unchanged_app.info_path.read_bytes())
        info_data["file"]["name"] = "new.pkg"
        unchanged_app.info_path.write_bytes(plistlib.dumps(info_data))

        result = runner.invoke(app, ["app", command, "--all"])

        assert result.exit_code == 0, result.output
        assert "doesn't match the recorded sha256" in result.output
        assert f"app set-file {unchanged_app.id}" in result.output
        assert patch_apps_endpoints["update"] == 0  # warned, but nothing was silently pushed

    @pytest.mark.parametrize(
        "placed",
        [
            # Gitignored payloads are routinely absent (fresh clone, metadata-only push).
            pytest.param(None, id="payload-absent"),
            pytest.param((INSTALLER, INSTALLER_NAME), id="suffixed-name"),
            pytest.param((b"replaced-in-place-bytes", INSTALLER_NAME), id="suffixed-name-trusted-content"),
            pytest.param((INSTALLER, "installer.pkg"), id="legacy-clean-name"),
            pytest.param((INSTALLER, "installer_deadbeef.pkg"), id="stray-suffixed-tag"),
        ],
    )
    @pytest.mark.usefixtures("unchanged_app")
    def test_no_warning(self, iructl_repo_cd, patch_apps_endpoints, placed):
        if placed is not None:
            place_installer(iructl_repo_cd, *placed)

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert "set-file" not in result.output
        assert patch_apps_endpoints["update"] == 0


def test_help():
    result = runner.invoke(app, ["app", "push", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} app push" in result.stdout
    assert "Push local custom app changes to Iru" in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_dry_run(apps_lrc):
    local, _, _ = apps_lrc

    result = runner.invoke(app, ["app", "push", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created app:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated app:", result.stdout)) == 1
    assert "Would have deleted app:" not in result.stdout
    assert len(re.findall(r"Would upload installer:", result.stdout)) == 1
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no apps have changed
    assert Repository.load_path(model=CustomApp) == local


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_clean(apps_lrc):
    local, _, changes = apps_lrc

    result = runner.invoke(app, ["app", "push", "--all", "--clean"])
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
    assert "Installer Transfer Summary" in result.stdout
    assert re.search(r"Uploaded\s+1", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Remote only updates\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    repo = Repository.load_path(model=CustomApp)

    # Check that the created app's id has been updated along with dependent fields
    old_created_app = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_app = repo[str(old_created_app.info_path)]
    assert old_created_app.id not in repo
    assert new_created_app.id in repo
    compare_app_object(old_created_app, new_created_app, {"id", "created_at", "updated_at", "sync_hash"})

    # Check that the updated app has not changed beyond the expected sync fields.
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_app_object(local[updated_id], repo[updated_id], {"updated_at", "sync_hash"})

    # Check that the deleted (remote-only) app is still not in the repo
    deleted_id = changes[ChangeType.CREATE_REMOTE][0][1].id
    assert deleted_id not in repo


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_force(apps_lrc):
    local, _, changes = apps_lrc

    result = runner.invoke(app, ["app", "push", "--all", "--force"])
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
    assert "Installer Transfer Summary" in result.stdout
    assert re.search(r"Uploaded\s+1", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Remote only item\s+1", result.stdout)

    repo = Repository.load_path(model=CustomApp)

    # Check that the created app's id has been updated along with dependent fields
    old_created_app = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_app = repo[str(old_created_app.info_path)]
    assert old_created_app.id not in repo
    assert new_created_app.id in repo
    compare_app_object(old_created_app, new_created_app, {"id", "created_at", "updated_at", "sync_hash"})

    # Check that the locally-updated apps have not changed beyond the expected sync fields.
    for change_type in (ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT):
        updated_id = changes[change_type][0][0].id
        compare_app_object(local[updated_id], repo[updated_id], {"updated_at", "sync_hash"})

    # Check that the remote-only update has been reverted to the local state
    updated_id = changes[ChangeType.UPDATE_REMOTE][0][0].id
    compare_app_object(local[updated_id], repo[updated_id], {"updated_at"})


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_by_id_and_path(apps_lrc):
    _, _, changes = apps_lrc

    cmd_args = ["app", "push", "--force", "--id", changes[ChangeType.CREATE_REMOTE][0][1].id]
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
    assert re.search(r"Uploaded\s+1", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Remote only item\s+1", result.stdout)


@pytest.mark.usefixtures("patch_apps_endpoints", "apps_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["app", "push", "--force", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_invalid_path(apps_lrc):
    missing_path = Path("apps/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["app", "push", "--force", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppPushPayloadDir:
    """Push reads each installer from the resolved payload directory."""

    def test_honors_payload_dir_option(self, iructl_repo_cd, custom_app_factory, patch_apps_endpoints, tmp_path):
        make_local_app(iructl_repo_cd, custom_app_factory)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "installer.pkg").write_bytes(INSTALLER)

        result = runner.invoke(app, ["app", "push", "--all", "--payload-dir", str(elsewhere)])

        assert result.exit_code == 0, result.output
        assert patch_apps_endpoints["create"] == 1

    def test_honors_payload_dir_env(
        self, iructl_repo_cd, custom_app_factory, patch_apps_endpoints, tmp_path, monkeypatch
    ):
        make_local_app(iructl_repo_cd, custom_app_factory)
        elsewhere = tmp_path / "env_payloads"
        elsewhere.mkdir()
        (elsewhere / "installer.pkg").write_bytes(INSTALLER)
        monkeypatch.setenv("IRUCTL_PAYLOAD_DIR", str(elsewhere))

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert patch_apps_endpoints["create"] == 1


def test_push_leaves_tracked_installer_uncommitted(iructl_repo_cd, custom_app_factory, patch_apps_endpoints):
    # A binary that git already tracks has its working-tree change kept out of the push commits.
    repo = iructl_repo_cd
    place_installer(repo, content=b"old-version")
    subprocess.run(["git", "-C", str(repo), "add", "-f", f"payloads/{INSTALLER_NAME}"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "track installer"], check=True, capture_output=True)
    # Overwrite with the bytes the app references, leaving an uncommitted working-tree change.
    (repo / "payloads" / INSTALLER_NAME).write_bytes(INSTALLER)
    make_local_app(repo, custom_app_factory)

    result = runner.invoke(app, ["app", "push", "--all"])

    assert result.exit_code == 0, result.output
    assert patch_apps_endpoints["create"] == 1

    # The committed installer keeps its old bytes; the new bytes stay on disk, uncommitted.
    head = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:payloads/{INSTALLER_NAME}"], check=True, capture_output=True
    )
    assert head.stdout == b"old-version"
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", f"payloads/{INSTALLER_NAME}"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip().startswith("M")
    assert (repo / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER


@pytest.mark.usefixtures("patch_apps_endpoints")
def test_upload_failure_reports_failure(iructl_repo_cd, custom_app_factory, monkeypatch):
    make_local_app(iructl_repo_cd, custom_app_factory)
    place_installer(iructl_repo_cd)

    def fail_create(self, **kwargs):
        raise PayloadTransferError("S3 upload failed")

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource.create", fail_create)

    result = runner.invoke(app, ["app", "push", "--all"])

    # A failed upload is a failed create: exit 1, surfaced in the action failure column,
    # and absent from the success-only Installer Transfer Summary.
    assert result.exit_code == 1, result.output
    assert re.search(r"Created\s+0\s+0\s+1", result.stdout)
    assert "Installer Transfer Summary" not in result.stdout
