import hashlib
import plistlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._utils import content_suffixed_filename
from iructl.api.apps import CustomAppsResource
from iructl.exceptions import PayloadTransferError
from iructl.repository import CustomApp, InstallEnforcement, InstallType, Repository
from tests.fixtures.apps import app_to_response

runner = CliRunner()

INSTALLER = b"installer-bytes-content"
INSTALLER_SHA = hashlib.sha256(INSTALLER).hexdigest()
INSTALLER_NAME = content_suffixed_filename("installer.pkg", INSTALLER_SHA)


def _make_local_app(repo: Path, factory, *, name: str = "My App") -> CustomApp:
    """Write a local custom-app member (no scripts) under <repo>/apps."""
    member = factory(
        name=name,
        file_name=INSTALLER_NAME,
        file_sha256=INSTALLER_SHA,
        install_type=InstallType.PACKAGE,
        install_enforcement=InstallEnforcement.INSTALL_ONCE,
        has_audit=False,
        has_preinstall=False,
        has_postinstall=False,
    )
    member.ensure_paths(repo / "apps")
    member.write()
    return member


def _place_installer(repo: Path, content: bytes = INSTALLER, name: str = INSTALLER_NAME) -> Path:
    payloads = repo / "payloads"
    payloads.mkdir(exist_ok=True)
    target = payloads / name
    target.write_bytes(content)
    return target


@pytest.fixture
def unchanged_app(iructl_repo_cd, custom_app_factory, apps_remote) -> CustomApp:
    """A local app whose metadata matches Iru (ChangeType.NONE), with no local installer yet."""
    member = _make_local_app(iructl_repo_cd, custom_app_factory)
    member.sync_hash = member.diff_hash
    member.write()
    apps_remote[member.id] = CustomApp.from_api_payload(app_to_response(member))
    (iructl_repo_cd / "payloads").mkdir(exist_ok=True)
    return member


@pytest.fixture
def stub_installer_download(monkeypatch):
    """Patch the installer download to write INSTALLER bytes to its destination."""

    def _download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
        dest.write_bytes(INSTALLER)

    monkeypatch.setattr("iructl.api.client.S3Client.download_file", _download)


@pytest.mark.usefixtures("iructl_repo_cd")
class TestAppNew:
    def test_uses_installer_already_in_payload_dir(self, iructl_repo_cd):
        installer = _place_installer(iructl_repo_cd)
        result = runner.invoke(
            app,
            [
                "app", "new", "--name", "My App", "--file", str(installer),
                "--installer-type", "package", "--enforcement", "install_once", "--info-format", "json",
            ],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        member = CustomApp.from_path(iructl_repo_cd / "apps" / "My App")
        assert member.info.file.name == INSTALLER_NAME
        assert member.info.file.sha256 == INSTALLER_SHA

    def test_copies_installer_into_payload_dir(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "installer.pkg"
        source.write_bytes(INSTALLER)
        result = runner.invoke(
            app,
            ["app", "new", "--name", "My App", "--file", str(source), "--installer-type", "package",
             "--enforcement", "install_once", "--copy"],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER
        assert source.exists()  # copy leaves the source in place
        member = CustomApp.from_path(iructl_repo_cd / "apps" / "My App")
        assert member.info.file.name == INSTALLER_NAME
        assert member.info.file.sha256 == INSTALLER_SHA

    def test_moves_installer_into_payload_dir(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "installer.pkg"
        source.write_bytes(INSTALLER)
        result = runner.invoke(
            app,
            ["app", "new", "--name", "My App", "--file", str(source), "--installer-type", "package",
             "--enforcement", "install_once", "--move"],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER
        assert not source.exists()  # move consumes the source

    def test_name_defaults_to_installer_stem(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "Firefox.pkg"
        source.write_bytes(INSTALLER)
        result = runner.invoke(app, ["app", "new", "--file", str(source)])
        assert result.exit_code == 0, result.output
        member = CustomApp.from_path(iructl_repo_cd / "apps" / "Firefox")
        assert member.info.name == "Firefox"

    def test_autodetects_installer_type_from_extension(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "installer.dmg"
        source.write_bytes(INSTALLER)
        result = runner.invoke(app, ["app", "new", "--name", "My App", "--file", str(source)])
        assert result.exit_code == 0, result.output
        member = CustomApp.from_path(iructl_repo_cd / "apps" / "My App")
        assert member.info.install_type is InstallType.IMAGE

    def test_errors_on_unknown_extension(self, tmp_path):
        source = tmp_path / "installer.bin"
        source.write_bytes(INSTALLER)
        result = runner.invoke(app, ["app", "new", "--name", "My App", "--file", str(source)])
        assert result.exit_code != 0

    def test_errors_on_zip_without_unzip_location(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "bundle.zip"
        source.write_bytes(INSTALLER)
        result = runner.invoke(app, ["app", "new", "--name", "My App", "--file", str(source)])
        # Clean BadParameter (exit 2), not an uncaught ValidationError traceback (exit 1).
        assert result.exit_code == 2, result.output
        assert "Unzip destination is required" in result.output
        assert not (iructl_repo_cd / "apps" / "My App").exists()

    def test_errors_on_unzip_location_for_non_zip(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "installer.pkg"
        source.write_bytes(INSTALLER)
        result = runner.invoke(
            app,
            ["app", "new", "--name", "My App", "--file", str(source), "--unzip-location", "/tmp/x"],
        )
        assert result.exit_code == 2, result.output
        assert "only valid for the zip install type" in result.output
        assert not (iructl_repo_cd / "apps" / "My App").exists()

    def test_enforcement_defaults_to_no_enforcement(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "installer.pkg"
        source.write_bytes(INSTALLER)
        result = runner.invoke(app, ["app", "new", "--name", "My App", "--file", str(source)])
        assert result.exit_code == 0, result.output
        member = CustomApp.from_path(iructl_repo_cd / "apps" / "My App")
        assert member.info.install_type is InstallType.PACKAGE
        assert member.info.install_enforcement is InstallEnforcement.NO_ENFORCEMENT
        assert member.info.show_in_self_service is True

    def test_skips_import_on_matching_sha_collision(self, iructl_repo_cd, tmp_path):
        _place_installer(iructl_repo_cd)  # payloads/installer.pkg with INSTALLER bytes
        source = tmp_path / "installer.pkg"
        source.write_bytes(INSTALLER)  # same name, identical bytes
        result = runner.invoke(
            app,
            ["app", "new", "--name", "My App", "--file", str(source), "--installer-type", "package",
             "--enforcement", "install_once", "--move"],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert source.exists()  # identical file already present, so the move is skipped
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    def test_differing_content_imports_to_a_distinct_name(self, iructl_repo_cd, tmp_path):
        _place_installer(iructl_repo_cd)  # payloads/<INSTALLER_NAME> with INSTALLER bytes
        different = b"a-different-installer"
        source = tmp_path / "installer.pkg"
        source.write_bytes(different)
        result = runner.invoke(
            app,
            ["app", "new", "--name", "My App", "--file", str(source), "--installer-type", "package",
             "--enforcement", "install_once"],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        other_name = content_suffixed_filename("installer.pkg", hashlib.sha256(different).hexdigest())
        assert other_name != INSTALLER_NAME
        assert (iructl_repo_cd / "payloads" / other_name).read_bytes() == different
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER  # original untouched

    def test_errors_when_target_name_holds_mismatched_bytes(self, iructl_repo_cd, tmp_path):
        source = tmp_path / "installer.pkg"
        source.write_bytes(INSTALLER)
        _place_installer(iructl_repo_cd, b"corrupt", INSTALLER_NAME)  # mis-tagged file at the target name
        result = runner.invoke(
            app,
            ["app", "new", "--name", "My App", "--file", str(source), "--installer-type", "package",
             "--enforcement", "install_once"],
        )  # fmt: skip
        assert result.exit_code != 0
        assert not (iructl_repo_cd / "apps" / "My App").exists()  # no app created on a name collision

    def test_errors_when_installer_missing(self, iructl_repo_cd, tmp_path):
        result = runner.invoke(
            app,
            ["app", "new", "--name", "X", "--file", str(tmp_path / "missing.pkg"), "--installer-type", "package",
             "--enforcement", "install_once"],
        )  # fmt: skip
        assert result.exit_code != 0
        # A missing installer must not create a payload directory as a side effect.
        assert not (iructl_repo_cd / "payloads").exists()

    def test_errors_audit_without_continuous_enforce(self, iructl_repo_cd, tmp_path):
        installer = _place_installer(iructl_repo_cd)
        audit = tmp_path / "audit.zsh"
        audit.write_text("#!/bin/zsh\necho hi\n")
        result = runner.invoke(
            app,
            ["app", "new", "--name", "X", "--file", str(installer), "--installer-type", "package",
             "--enforcement", "install_once", "--import-audit", str(audit)],
        )  # fmt: skip
        assert result.exit_code != 0


def test_new_app_repo_option_defaults_output_dir(iructl_repo, tmp_path):
    # cwd is the non-repo tmp_path (autouse tmp_path_cd), so landing in the repo can only
    # come from --repo defaulting the output dir, not the current directory.
    installer = tmp_path / "installer.pkg"
    installer.write_bytes(INSTALLER)

    result = runner.invoke(
        app,
        ["--repo", str(iructl_repo), "app", "new", "--name", "Repo App",
         "--file", str(installer), "--installer-type", "package"],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert (iructl_repo / "apps" / "Repo App").is_dir()
    assert not (tmp_path / "apps").exists()  # not created under cwd


@pytest.mark.usefixtures("iructl_repo_cd")
class TestAppSetFile:
    def test_updates_installer_reference(self, iructl_repo_cd, custom_app_factory):
        member = _make_local_app(iructl_repo_cd, custom_app_factory)
        new_content = b"a-different-installer"
        _place_installer(iructl_repo_cd, new_content, "new.pkg")

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", "new.pkg"])

        assert result.exit_code == 0, result.output
        reloaded = CustomApp.from_path(member.info_path.parent)
        assert reloaded.info.file.name == content_suffixed_filename("new.pkg", hashlib.sha256(new_content).hexdigest())
        assert reloaded.info.file.sha256 == hashlib.sha256(new_content).hexdigest()

    def test_imports_external_installer(self, iructl_repo_cd, custom_app_factory, tmp_path):
        member = _make_local_app(iructl_repo_cd, custom_app_factory)
        source = tmp_path / "new.pkg"
        source.write_bytes(b"a-different-installer")

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", str(source)])

        assert result.exit_code == 0, result.output
        new_name = content_suffixed_filename("new.pkg", hashlib.sha256(b"a-different-installer").hexdigest())
        assert (iructl_repo_cd / "payloads" / new_name).read_bytes() == b"a-different-installer"
        assert source.exists()  # copy leaves the source in place
        reloaded = CustomApp.from_path(member.info_path.parent)
        assert reloaded.info.file.name == new_name

    def test_move_consumes_external_installer(self, iructl_repo_cd, custom_app_factory, tmp_path):
        member = _make_local_app(iructl_repo_cd, custom_app_factory)
        source = tmp_path / "new.pkg"
        source.write_bytes(b"a-different-installer")

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", str(source), "--move"])

        assert result.exit_code == 0, result.output
        new_name = content_suffixed_filename("new.pkg", hashlib.sha256(b"a-different-installer").hexdigest())
        assert (iructl_repo_cd / "payloads" / new_name).read_bytes() == b"a-different-installer"
        assert not source.exists()  # move consumes the source

    def test_errors_when_target_name_holds_mismatched_bytes(self, iructl_repo_cd, custom_app_factory, tmp_path):
        member = _make_local_app(iructl_repo_cd, custom_app_factory)
        different = b"different-bytes"
        target_name = content_suffixed_filename("new.pkg", hashlib.sha256(different).hexdigest())
        _place_installer(iructl_repo_cd, b"existing-bytes", target_name)  # mis-tagged file at the target name
        source = tmp_path / "new.pkg"
        source.write_bytes(different)

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", str(source)])

        assert result.exit_code != 0
        assert (iructl_repo_cd / "payloads" / target_name).read_bytes() == b"existing-bytes"

    def test_errors_when_file_missing_everywhere(self, iructl_repo_cd, custom_app_factory):
        member = _make_local_app(iructl_repo_cd, custom_app_factory)

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", "absent.pkg"])

        assert result.exit_code != 0


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppPush:
    def test_create_uploads_installer(self, iructl_repo_cd, custom_app_factory, patch_apps_endpoints, apps_remote):
        _make_local_app(iructl_repo_cd, custom_app_factory)
        _place_installer(iructl_repo_cd)

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert patch_apps_endpoints["create"] == 1
        assert len(apps_remote) == 1

    def test_does_not_create_payloads_gitignore(self, iructl_repo_cd, custom_app_factory):
        # Push uploads existing installers; it never downloads, so it leaves the payload dir alone.
        _make_local_app(iructl_repo_cd, custom_app_factory)
        _place_installer(iructl_repo_cd)

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert not (iructl_repo_cd / "payloads" / ".gitignore").exists()


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppInstallerSyncWarnings:
    """Push/sync warn when an app's installer reference is out of sync with the payload dir (SYS-2886)."""

    @pytest.mark.parametrize("command", ["push", "sync"])
    def test_warns_on_mismatched_clean_installer(self, command, iructl_repo_cd, unchanged_app, patch_apps_endpoints):
        # The footgun: paste a new installer under the clean base name and hand-edit file.name,
        # leaving file.sha256 stale -- the clean-named file's bytes don't match the recorded sha.
        _place_installer(iructl_repo_cd, b"a-new-installer", "new.pkg")
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
            # A stray content-suffixed file (e.g. an old installer left after a remote update) is
            # ignored: only the recorded suffixed name and the clean payload_name are checked.
            pytest.param((INSTALLER, "installer_deadbeef.pkg"), id="stray-suffixed-tag"),
        ],
    )
    @pytest.mark.usefixtures("unchanged_app")
    def test_no_warning(self, iructl_repo_cd, patch_apps_endpoints, placed):
        if placed is not None:
            _place_installer(iructl_repo_cd, *placed)

        result = runner.invoke(app, ["app", "push", "--all"])

        assert result.exit_code == 0, result.output
        assert "set-file" not in result.output
        assert patch_apps_endpoints["update"] == 0


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
        _place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        result = runner.invoke(app, ["app", "pull", "--all", "--download", "--force"])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    @pytest.mark.usefixtures("unchanged_app")
    def test_differing_installer_left_without_force(self, iructl_repo_cd, monkeypatch):
        _place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run on a mismatch without --force")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        result = runner.invoke(app, ["app", "pull", "--all", "--download"])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"
        assert "--force" in result.output


@pytest.mark.usefixtures("patch_apps_endpoints", "unchanged_app", "stub_installer_download")
def test_sync_force_mode_pull_overwrites_differing_installer(iructl_repo_cd):
    _place_installer(iructl_repo_cd, content=b"stale-local-bytes")

    result = runner.invoke(app, ["app", "sync", "--all", "--download", "--force-mode", "pull"])

    assert result.exit_code == 0, result.output
    assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER


@pytest.mark.usefixtures("patch_apps_endpoints")
def test_sync_download_mismatch_hint_points_to_app_download(iructl_repo_cd, unchanged_app, monkeypatch):
    # A mismatch is fixed by the dedicated single-app download command, the same for pull and sync.
    _place_installer(iructl_repo_cd, content=b"stale-local-bytes")

    def fail_download(*args, **kwargs):
        pytest.fail("download should not run on a mismatch without --force")

    monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

    result = runner.invoke(app, ["app", "sync", "--all", "--download"])

    assert result.exit_code == 0, result.output
    assert f"app download {unchanged_app.id} --force" in result.output
    assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"


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
        _place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        result = runner.invoke(app, ["app", command, "--all", "--download", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Installer differs, would skip." in result.output
        assert f"app download {unchanged_app.id} --force" in result.output
        assert "Would have downloaded" not in result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"

    def test_push_previews_installer_upload(self, iructl_repo_cd, custom_app_factory):
        _make_local_app(iructl_repo_cd, custom_app_factory)

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


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppDownload:
    def test_downloads_single_app(self, iructl_repo_cd, custom_app_factory, apps_remote, monkeypatch):
        member = _make_local_app(iructl_repo_cd, custom_app_factory)
        apps_remote[member.id] = CustomApp.from_api_payload(app_to_response(member))
        (iructl_repo_cd / "payloads").mkdir(exist_ok=True)

        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            dest.write_bytes(INSTALLER)

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        result = runner.invoke(app, ["app", "download", member.id])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER
