import random
import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl._utils import content_suffixed_filename
from iructl.api.apps import CustomAppsResource
from iructl.exceptions import PayloadTransferError
from iructl.repository import CustomApp, Repository
from tests.fixtures.apps import (
    INSTALLER,
    INSTALLER_NAME,
    INSTALLER_SHA,
    app_to_response,
    compare_app_object,
    make_local_app,
    place_installer,
)
from tests.output import normalize_output

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["app", "pull", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} app pull" in result.stdout
    assert "Pull remote custom app changes from Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_dry_run(apps_lrc):
    local, _, _ = apps_lrc

    result = runner.invoke(app, ["app", "pull", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created app:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated app:", result.stdout)) == 1
    assert "Would have deleted app:" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no apps have changed
    assert Repository.load_path(model=CustomApp) == local


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_clean(apps_lrc):
    _, _, changes = apps_lrc

    result = runner.invoke(app, ["app", "pull", "--all", "--clean"])
    assert result.exit_code == 0

    # Check output
    assert "Pulling 3 changes from Iru..." in result.stdout
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 1
    assert len(re.findall(r"deleted in local repo", result.stdout)) == 1
    assert "Pull operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+1\s+0", result.stdout)
    assert re.search(r"Deleted\s+1\s+0", result.stdout)
    # No --download, so no installer movement and no transfer summary.
    assert "Installer Transfer Summary" not in result.stdout
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Local only updates\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    repo = Repository.load_path(model=CustomApp)

    # Check that the created app matches the remote
    remote_app_from_changes = changes[ChangeType.CREATE_REMOTE][0][1]
    new_created_local_app = repo[remote_app_from_changes.id]
    compare_app_object(remote_app_from_changes, new_created_local_app, {"sync_hash"})

    # Check that the updated app matches the remote
    remote_app = changes[ChangeType.UPDATE_REMOTE][0][1]
    compare_app_object(remote_app, repo[remote_app.id], {"sync_hash"})

    # Check that the deleted (local-only) app is no longer in the repo
    deleted_id = changes[ChangeType.CREATE_LOCAL][0][0].id
    assert deleted_id not in repo


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_all_force(apps_lrc):
    _, _, changes = apps_lrc

    result = runner.invoke(app, ["app", "pull", "--all", "--force"])
    assert result.exit_code == 0

    # Check output
    assert "Pulling 4 changes from Iru..." in result.stdout
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 3
    assert len(re.findall(r"deleted in local repo", result.stdout)) == 0
    assert "Pull operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+3\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Local only item\s+1", result.stdout)

    repo = Repository.load_path(model=CustomApp)

    # Check that the created app matches the remote
    remote_app_from_changes = changes[ChangeType.CREATE_REMOTE][0][1]
    new_created_local_app = repo[remote_app_from_changes.id]
    compare_app_object(remote_app_from_changes, new_created_local_app, {"sync_hash"})

    # Check that the remote-updated and conflicting apps match the remote
    for change_type in (ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT):
        remote_app = changes[change_type][0][1]
        compare_app_object(remote_app, repo[remote_app.id], {"sync_hash"})

    # Check that the locally-updated app has been reverted to the remote state
    remote_app = changes[ChangeType.UPDATE_LOCAL][0][1]
    compare_app_object(remote_app, repo[remote_app.id], {"sync_hash"})


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_by_id_and_path(apps_lrc):
    _, _, changes = apps_lrc

    cmd_args = [
        "app",
        "pull",
        "--force",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].info_path.parent),
    ]
    for idx, change_type in enumerate(
        {k for k in changes if k not in {ChangeType.CREATE_REMOTE, ChangeType.CREATE_LOCAL}}
    ):
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(str(random.choice(changes[change_type])[0].info_path.parent))
        else:
            cmd_args.append("--id")
            cmd_args.append(random.choice(changes[change_type])[0].id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Pulling 4 changes from Iru..." in result.stdout
    assert len(re.findall(r"created in local repo", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 3
    assert len(re.findall(r"deleted in local repo", result.stdout)) == 0
    assert "Pull operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+3\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Local only item\s+1", result.stdout)

    repo = Repository.load_path(model=CustomApp)

    # Check that the updated apps match the remote
    for change_type in (ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT, ChangeType.UPDATE_REMOTE):
        remote_app = changes[change_type][0][1]
        compare_app_object(remote_app, repo[remote_app.id], {"sync_hash"})


@pytest.mark.usefixtures("patch_apps_endpoints", "apps_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["app", "pull", "--force", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_invalid_path(apps_lrc):
    missing_path = Path("apps/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["app", "pull", "--force", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppPullDownloadFile:
    @pytest.mark.usefixtures("unchanged_app", "stub_installer_download")
    def test_downloads_missing_installer(self, iructl_repo_cd):
        result = runner.invoke(app, ["app", "pull", "--all", "--download"])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    @pytest.mark.usefixtures("stub_installer_download")
    def test_create_logs_local_repo_and_download(self, iructl_repo_cd, custom_app_factory, apps_remote):
        remote = custom_app_factory(file_name="installer.pkg", file_sha256=INSTALLER_SHA, has_audit=False)
        apps_remote[remote.id] = remote

        result = runner.invoke(app, ["app", "pull", "--all", "--download"])

        assert result.exit_code == 0, result.output
        assert f"{remote.name} ({remote.id}) created in local repo successfully" in result.output
        assert f"{INSTALLER_NAME} downloaded to" in result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    def test_pull_writes_new_app_in_configured_format(self, custom_app_factory, apps_remote):
        # A new app pulled from Iru honors --info-format for its info file.
        remote = custom_app_factory(file_name="installer.pkg", file_sha256=INSTALLER_SHA, has_audit=False)
        apps_remote[remote.id] = remote

        result = runner.invoke(app, ["app", "pull", "--id", str(remote.id), "--info-format", "yaml"])

        assert result.exit_code == 0, result.output
        assert Repository.load_path(model=CustomApp)[remote.id].info.path.name == "info.yaml"

    def test_pull_skips_invalid_remote_app(self, monkeypatch, custom_app_factory, apps_remote):
        # One unconvertible remote app is warned about and reported; the rest still pull.
        valid = custom_app_factory(file_name="installer.pkg", file_sha256=INSTALLER_SHA, has_audit=False)
        apps_remote[valid.id] = valid
        # A zip app without an unzip_location fails AppInfoFile validation on conversion.
        bad = custom_app_factory(name="Bad App", file_name="bad.pkg", has_audit=False)
        bad_payload = app_to_response(bad).model_copy(update={"install_type": "zip"})

        fixture_list = CustomAppsResource.list  # the endpoint fake from patch_apps_endpoints

        def fake_list(self):
            payload_list = fixture_list(self)
            payload_list.results.append(bad_payload)
            payload_list.count += 1
            return payload_list

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.list", fake_list)

        result = runner.invoke(app, ["app", "pull", "--all"])

        assert result.exit_code == 1, result.output
        assert f"Skipping invalid remote member '{bad.name}' ({bad.id})" in result.output
        assert "Invalid remote item" in result.output
        local_repo = Repository.load_path(model=CustomApp)
        assert valid.id in local_repo
        assert bad.id not in local_repo

    @pytest.mark.usefixtures("unchanged_app")
    def test_download_failure_flips_exit_code(self, monkeypatch):
        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            raise PayloadTransferError("network down")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        result = runner.invoke(app, ["app", "pull", "--all", "--download"])

        assert result.exit_code == 1, result.output

    @pytest.mark.usefixtures("unchanged_app", "stub_installer_download")
    def test_force_overwrites_differing_installer(self, iructl_repo_cd):
        place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        result = runner.invoke(app, ["app", "pull", "--all", "--download", "--force"])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    @pytest.mark.usefixtures("unchanged_app")
    def test_differing_installer_left_without_force(self, iructl_repo_cd, monkeypatch):
        place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run on a mismatch without --force")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        result = runner.invoke(app, ["app", "pull", "--all", "--download"])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"
        assert "--force" in result.output


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppDryRunPreview:
    """Dry runs preview the installer transfers that would happen, making no changes."""

    @pytest.mark.usefixtures("unchanged_app")
    def test_pull_download_previews_missing_installer(self, iructl_repo_cd):
        result = runner.invoke(app, ["app", "pull", "--all", "--download", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would have downloaded app" in result.output
        assert "already up to date" not in result.output
        assert not (iructl_repo_cd / "payloads" / INSTALLER_NAME).exists()  # dry run made no changes

    @pytest.mark.parametrize("command", ["pull", "sync"])
    def test_download_previews_mismatch_as_skip(self, command, iructl_repo_cd, unchanged_app):
        # The dry-run must agree with the real run: a differing installer is skipped, not downloaded.
        place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        result = runner.invoke(app, ["app", command, "--all", "--download", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Installer differs, would skip." in result.output
        assert f"app download {unchanged_app.id} --force" in result.output
        assert "Would have downloaded" not in result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"

    def test_push_previews_installer_upload(self, iructl_repo_cd, custom_app_factory):
        make_local_app(iructl_repo_cd, custom_app_factory)

        result = runner.invoke(app, ["app", "push", "--all", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would upload installer" in result.output

    @pytest.mark.parametrize("command", ["pull", "sync"])
    def test_create_previews_ride_along_installer(self, command, iructl_repo_cd, custom_app_factory, apps_remote):
        # A new app previews its metadata create and the installer download that rides along with it.
        remote = custom_app_factory(file_name="installer.pkg", file_sha256=INSTALLER_SHA, has_audit=False)
        apps_remote[remote.id] = remote

        result = runner.invoke(app, ["app", command, "--all", "--download", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would have created app" in result.output
        assert "Would download installer" in result.output
        assert not (iructl_repo_cd / "payloads" / INSTALLER_NAME).exists()  # dry run made no changes


@pytest.mark.usefixtures("patch_apps_endpoints", "unchanged_app", "stub_installer_download")
def test_download_creates_payloads_gitignore(iructl_repo_cd):
    result = runner.invoke(app, ["app", "pull", "--all", "--download"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo_cd / "payloads" / ".gitignore").read_text() == "*\n"


@pytest.mark.usefixtures("patch_apps_endpoints", "stub_installer_download")
def test_fresh_repo_multi_app_download(iructl_repo_cd, custom_app_factory, apps_remote):
    # A clean checkout pulling several apps with --download lands every installer with the right sha.
    names = ["AppOne.pkg", "AppTwo.pkg", "AppThree.pkg"]
    for name in names:
        member = custom_app_factory(file_name=name, file_sha256=INSTALLER_SHA, has_audit=False)
        apps_remote[member.id] = member

    result = runner.invoke(app, ["app", "pull", "--all", "--download"])

    assert result.exit_code == 0, result.output
    local = Repository.load_path(model=CustomApp)
    assert len(local) == len(names)
    for name in names:
        suffixed = content_suffixed_filename(name, INSTALLER_SHA)
        assert (iructl_repo_cd / "payloads" / suffixed).read_bytes() == INSTALLER
    for member in local.values():
        assert member.info.file.sha256 == INSTALLER_SHA
