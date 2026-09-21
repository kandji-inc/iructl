import hashlib
import re
import shutil
from pathlib import Path

import pytest
from rich.syntax import Syntax

from iructl._console import OutputFormat, SyntaxType
from iructl._utils import content_suffixed_filename
from iructl.api import CustomAppPayload, InstallEnforcement, InstallType
from iructl.exceptions import (
    DuplicateAppError,
    DuplicateInfoFileError,
    InvalidAppError,
    MissingAppInstallerError,
    MissingInfoFileError,
)
from iructl.repository import (
    ACCEPTED_INFO_EXTENSIONS,
    AppFile,
    AppInfoFile,
    BlueprintAssignment,
    CustomApp,
    InfoFormat,
    Repository,
    Script,
)
from iructl.repository.custom_app import DownloadResult

SCRIPT_ATTRIBUTES = ("audit", "preinstall", "postinstall")

_INSTALLER = b"installer"
_INSTALLER_SHA = hashlib.sha256(_INSTALLER).hexdigest()


@pytest.fixture
def app_directory_info_file(app_directory) -> Path:
    """Return the path to a valid info file in the app directory."""
    return next(app_directory.glob("info.*"))


@pytest.fixture
def app_directory_with_extra_info_file(app_directory, app_directory_info_file) -> Path:
    """Return an app directory with a second info file."""
    suffix = (ACCEPTED_INFO_EXTENSIONS - {app_directory_info_file.suffix}).pop()
    shutil.copy(app_directory_info_file, app_directory_info_file.with_name(f"info{suffix}"))
    return app_directory


@pytest.fixture
def app_directory_without_info_file(app_directory, app_directory_info_file) -> Path:
    """Return an app directory without an info file."""
    app_directory_info_file.unlink()
    return app_directory


@pytest.fixture
def app_directory_with_extra_audit_script(app_directory) -> Path:
    """Return an app directory with a duplicate audit script file."""
    audit_path = next(app_directory.glob("audit*"))
    shutil.copy(audit_path, audit_path.with_name("audit2.zsh"))
    return app_directory


def _payload_data(info, **overrides) -> dict:
    """Build a CustomAppPayload dict from an AppInfoFile plus server-only fields."""
    data = info.model_dump(mode="json", exclude={"sync_hash", "file"}) | {
        "sha256": info.file.sha256,
        "file_key": f"tenants/1/library/custom_apps/{info.file.name}",
        "file_url": "https://example.com/file",
        "file_size": 1234,
        "file_updated": info.updated_at or info.created_at,
        "audit_script": "",
        "preinstall_script": "",
        "postinstall_script": "",
    }
    return data | overrides


def _remote_member(custom_app_factory, sha256, file_name="installer.pkg") -> CustomApp:
    """Build a CustomApp as it would arrive from the API (carrying file_url/file_size)."""
    info = custom_app_factory(has_audit=False, file_name=file_name, file_sha256=sha256).info
    return CustomApp.from_api_payload(CustomAppPayload.model_validate(_payload_data(info)))


class TestCustomApp:
    @pytest.mark.parametrize("attribute", SCRIPT_ATTRIBUTES)
    def test_path_property_raises_when_script_missing(self, custom_app_factory, attribute):
        """Reading a script path property raises when the script is absent."""
        app = custom_app_factory(has_audit=False, has_preinstall=False, has_postinstall=False)
        with pytest.raises(ValueError, match=f"{attribute}_path property must be set before reading"):
            getattr(app, f"{attribute}_path")

    @pytest.mark.parametrize("attribute", SCRIPT_ATTRIBUTES)
    def test_path_property_raises_when_path_unset(self, custom_app_factory, attribute):
        """Reading a script path property raises when the script exists but its path is unset."""
        app = custom_app_factory(has_audit=True, has_preinstall=True, has_postinstall=True)
        with pytest.raises(ValueError, match=f"{attribute}_path property must be set before reading"):
            getattr(app, f"{attribute}_path")

    def test_ensure_paths_without_paths(self, custom_app_obj, apps_repo):
        """ensure_paths places the member under apps/<name>/ with default child filenames."""
        member_dir = apps_repo / custom_app_obj.name
        assert not custom_app_obj.has_paths
        custom_app_obj.ensure_paths(apps_repo)
        assert custom_app_obj.has_paths
        assert custom_app_obj.info_path == member_dir / f"info.{custom_app_obj.info.format}"
        for attribute in SCRIPT_ATTRIBUTES:
            script = getattr(custom_app_obj, attribute)
            if script is not None:
                assert script.path == member_dir / f"{attribute}.zsh"

    def test_ensure_paths_with_existing_parent(self, custom_app_obj, apps_repo):
        """ensure_paths increments the directory name when it already exists."""
        (apps_repo / custom_app_obj.name).mkdir()
        custom_app_obj.ensure_paths(apps_repo)
        assert (
            custom_app_obj.info_path == apps_repo / f"{custom_app_obj.name} (1)" / f"info.{custom_app_obj.info.format}"
        )

    def test_ensure_paths_is_idempotent(self, custom_app_obj, apps_repo):
        """Calling ensure_paths a second time leaves the existing paths unchanged."""
        custom_app_obj.ensure_paths(apps_repo)
        first_info_path = custom_app_obj.info_path

        custom_app_obj.ensure_paths(apps_repo)
        assert custom_app_obj.info_path == first_info_path

    def test_write_to_path(self, custom_app_obj, apps_repo):
        """Writing creates the info file and each present script file."""
        custom_app_obj.ensure_paths(apps_repo)
        assert not custom_app_obj.info_path.exists()

        custom_app_obj.write()

        assert custom_app_obj.info_path.exists()
        for attribute in SCRIPT_ATTRIBUTES:
            script = getattr(custom_app_obj, attribute)
            if script is not None:
                assert script.path.exists()

    def test_write_removes_stale_script(self, custom_app_factory, apps_repo):
        """Writing an app without a script removes a stale file with that prefix."""
        app = custom_app_factory(has_audit=False, has_preinstall=False, has_postinstall=False)
        app.ensure_paths(apps_repo)
        app.write()
        stale = app.info_path.parent / "preinstall.zsh"
        stale.write_text("#!/bin/zsh\n")

        app.write()
        assert not stale.exists()

    def test_write_rejects_bad_script_prefix(self, custom_app_factory, apps_repo):
        """Writing raises when a script filename does not start with its expected prefix."""
        app = custom_app_factory(has_audit=False, has_preinstall=True, has_postinstall=False)
        app.ensure_paths(apps_repo)
        app.preinstall.path = app.info_path.parent / "not_preinstall.zsh"
        with pytest.raises(InvalidAppError, match="preinstall"):
            app.write()

    def test_write_raises_when_paths_unset(self, custom_app_factory):
        """Writing raises before touching disk when the path properties have not been set."""
        app = custom_app_factory(has_audit=False, has_preinstall=False, has_postinstall=False)
        assert not app.has_paths
        with pytest.raises(ValueError, match="must be set before writing"):
            app.write()

    def test_write_rejects_scripts_in_different_directories(self, custom_app_factory, apps_repo):
        """Writing raises when child files do not all live in the same directory."""
        app = custom_app_factory(has_audit=False, has_preinstall=True, has_postinstall=False)
        app.ensure_paths(apps_repo)
        app.preinstall.path = app.info_path.parent / "elsewhere" / "preinstall.zsh"
        with pytest.raises(InvalidAppError, match="within the same directory"):
            app.write()

    @pytest.mark.parametrize(
        "path_fixture",
        [
            pytest.param("app_directory_info_file", id="info-file-path"),
            pytest.param("app_directory", id="directory-path"),
        ],
    )
    def test_load_from_path(self, request, path_fixture):
        """Loading from a valid info file path or app directory yields the named app."""
        path = request.getfixturevalue(path_fixture)
        assert CustomApp.from_path(path).name == "Test App"

    @pytest.mark.parametrize(
        ("setup_fixture", "expected_exception", "match"),
        [
            pytest.param(
                "app_directory_without_info_file",
                MissingInfoFileError,
                "Unable to locate info file",
                id="missing-info-file",
            ),
            pytest.param(
                "app_directory_with_extra_info_file",
                DuplicateInfoFileError,
                "Multiple info files",
                id="extra-info-file",
            ),
            pytest.param(
                "app_directory_with_extra_audit_script",
                DuplicateAppError,
                "Multiple audit files",
                id="duplicate-script",
            ),
        ],
    )
    def test_load_rejects_invalid_directory(self, request, app_directory, setup_fixture, expected_exception, match):
        """Loading an app directory with a structural problem raises the matching error."""
        request.getfixturevalue(setup_fixture)
        with pytest.raises(expected_exception, match=match):
            CustomApp.from_path(app_directory)

    def test_load_audit_without_continuous_enforcement_raises(self, app_directory_factory, tmp_path):
        """A member-level validation failure surfaces as InvalidAppError, not a raw ValidationError.

        The CLI loader only catches InvalidRepositoryMemberError, so an unwrapped ValidationError
        would escape as an unhandled traceback instead of a clean error.
        """
        written = app_directory_factory(
            tmp_path / "app",
            has_audit=False,
            has_preinstall=False,
            has_postinstall=False,
            install_enforcement=InstallEnforcement.INSTALL_ONCE,
        )
        (written["info"].parent / "audit.zsh").write_text("#!/bin/zsh\necho audit\n")
        with pytest.raises(InvalidAppError, match="continuously_enforce"):
            CustomApp.from_path(written["info"].parent)

    def test_round_trip_through_disk(self, custom_app_obj, apps_repo):
        """Writing then loading reproduces an equivalent member."""
        custom_app_obj.ensure_paths(apps_repo)
        custom_app_obj.write()

        loaded = CustomApp.from_path(custom_app_obj.info_path)
        assert loaded.diff_hash == custom_app_obj.diff_hash

    def test_audit_requires_continuous_enforcement(self, app_info_data_factory):
        """A non-empty audit script requires install_enforcement == continuously_enforce."""
        info_data = app_info_data_factory(install_enforcement=InstallEnforcement.INSTALL_ONCE)
        info = AppInfoFile.model_validate(info_data)
        with pytest.raises(ValueError, match="continuously_enforce"):
            CustomApp(info=info, audit=Script(content="#!/bin/zsh\necho audit\n"))

    def test_from_api_payload(self, app_info_file_obj):
        """from_api_payload derives the local name (clean base + content-hash suffix) and the sha256."""
        payload = CustomAppPayload.model_validate(
            _payload_data(app_info_file_obj, audit_script="", preinstall_script="echo pre", postinstall_script="")
        )
        app = CustomApp.from_api_payload(payload)
        assert app.info.file.name == content_suffixed_filename(app_info_file_obj.file.payload_name, payload.sha256)
        assert app.info.file.payload_name == app_info_file_obj.file.payload_name  # clean base recovered
        assert app.info.file.sha256 == payload.sha256
        assert app.audit is None
        assert app.preinstall is not None
        assert app.preinstall.content == "echo pre"
        assert app.postinstall is None

    def test_from_api_payload_strips_upload_token(self, app_info_file_obj):
        """from_api_payload strips the server's per-upload token before applying the local suffix."""
        payload = CustomAppPayload.model_validate(
            _payload_data(app_info_file_obj, file_key="tenants/1/library/custom_apps/Installer_ab12cd34.pkg")
        )
        app = CustomApp.from_api_payload(payload)
        assert app.info.file.payload_name == "Installer.pkg"  # server token removed
        assert app.info.file.name == content_suffixed_filename("Installer.pkg", payload.sha256)

    def test_from_api_payload_audit(self, app_info_data_factory):
        """An audit script from the API is mapped when continuously enforced."""
        info_data = app_info_data_factory(install_enforcement=InstallEnforcement.CONTINUOUSLY_ENFORCE)
        payload = CustomAppPayload.model_validate(
            _payload_data(AppInfoFile.model_validate(info_data), audit_script="echo audit")
        )
        app = CustomApp.from_api_payload(payload)
        assert app.audit is not None
        assert app.audit.content == "echo audit"

    @pytest.mark.parametrize("install_type", [InstallType.PACKAGE, InstallType.IMAGE])
    def test_from_api_payload_blank_unzip_location(self, app_info_data_factory, install_type):
        """A non-zip app whose payload carries the API's "" unzip_location loads with None."""
        info = AppInfoFile.model_validate(
            app_info_data_factory(install_type=install_type, install_enforcement=InstallEnforcement.INSTALL_ONCE)
        )
        payload = CustomAppPayload.model_validate(_payload_data(info, unzip_location=""))
        app = CustomApp.from_api_payload(payload)
        assert app.info.unzip_location is None

    @pytest.mark.parametrize("install_type", [InstallType.PACKAGE, InstallType.IMAGE])
    def test_from_api_payload_drops_stale_unzip_location(self, app_info_data_factory, install_type):
        """A non-zip app whose payload carries a stale, non-empty unzip_location loads with None."""
        info = AppInfoFile.model_validate(
            app_info_data_factory(install_type=install_type, install_enforcement=InstallEnforcement.INSTALL_ONCE)
        )
        payload = CustomAppPayload.model_validate(_payload_data(info, unzip_location="/var/tmp"))
        assert payload.unzip_location == "/var/tmp"  # bad shape still accepted at the payload layer
        app = CustomApp.from_api_payload(payload)  # previously raised ValidationError
        assert app.info.unzip_location is None

    @pytest.mark.parametrize(
        "install_enforcement",
        [
            pytest.param(InstallEnforcement.INSTALL_ONCE, id="install-once"),
            pytest.param(InstallEnforcement.NO_ENFORCEMENT, id="no-enforcement"),
        ],
    )
    def test_from_api_payload_drops_stale_audit_script(self, app_info_data_factory, install_enforcement):
        """A non-enforced app whose payload carries a stale audit script loads without one."""
        info = AppInfoFile.model_validate(app_info_data_factory(install_enforcement=install_enforcement))
        payload = CustomAppPayload.model_validate(_payload_data(info, audit_script="echo audit"))
        assert payload.audit_script == "echo audit"  # bad shape still accepted at the payload layer
        app = CustomApp.from_api_payload(payload)  # previously raised ValidationError
        assert app.audit is None
        assert app.info.install_enforcement == install_enforcement

    def test_diff_hash_stability(self, custom_app_obj):
        """diff_hash is stable to no-op edits and sensitive to meaningful changes."""
        original = custom_app_obj.diff_hash

        original_name = custom_app_obj.name
        custom_app_obj.info.name = "Renamed App"
        assert custom_app_obj.diff_hash != original
        custom_app_obj.info.name = original_name
        assert custom_app_obj.diff_hash == original

    def test_diff_hash_tracks_binary_and_scripts(self, custom_app_factory):
        """The member diff_hash reflects file.sha256 and script content changes.

        file.name is part of AppInfoFile.diff_hash by design (see
        test_info.py::test_diff_hash_changes_when_file_name_changes); the member hash inherits
        that from its info child, so it is exercised at the info level rather than here.
        """
        app = custom_app_factory(has_audit=False, has_preinstall=True, has_postinstall=False)
        original = app.diff_hash

        app.info.file.sha256 = "f" * 64
        assert app.diff_hash != original

        changed_binary = app.diff_hash
        app.preinstall.content = "#!/bin/zsh\necho changed\n"
        assert app.diff_hash != changed_binary

    def test_diff_hash_stable_across_formats(self, custom_app_obj, apps_repo):
        """diff_hash is unchanged when the info file format changes."""
        custom_app_obj.ensure_paths(apps_repo)
        custom_app_obj.write()
        plist_hash = CustomApp.from_path(custom_app_obj.info_path).diff_hash

        # Re-write the same data as JSON and reload.
        custom_app_obj.info_path.unlink()
        custom_app_obj.info.format = InfoFormat.JSON
        custom_app_obj.info.path = custom_app_obj.info.path.with_name("info.json")
        custom_app_obj.info.write()
        json_hash = CustomApp.from_path(custom_app_obj.info.path).diff_hash

        assert plist_hash == json_hash


class TestAppsRepository:
    def test_load_path_discovers_apps(self, apps_repo):
        """Repository.load_path(model=CustomApp) discovers apps/*/info.*."""
        repo = Repository.load_path(model=CustomApp, path=apps_repo)
        assert len(repo) == 10
        assert all(isinstance(member, CustomApp) for member in repo.values())


class TestResolveBinary:
    def test_returns_path_when_sha_matches(self, custom_app_factory, tmp_path):
        """_resolve_binary returns the binary path when its sha256 matches the info file."""
        content = b"installer-bytes"
        sha256 = hashlib.sha256(content).hexdigest()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)
        binary_path = tmp_path / app.info.file.name
        binary_path.write_bytes(content)

        assert app._resolve_binary(tmp_path) == binary_path

    def test_raises_when_binary_missing(self, custom_app_factory, tmp_path):
        """_resolve_binary raises when the binary is absent from the payload dir."""
        app = custom_app_factory(has_audit=False, file_name="installer.pkg")
        with pytest.raises(MissingAppInstallerError, match="Unable to locate"):
            app._resolve_binary(tmp_path)

    def test_raises_when_sha_mismatches(self, custom_app_factory, tmp_path):
        """_resolve_binary raises when the binary's sha256 does not match the info file."""
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256="a" * 64)
        (tmp_path / app.info.file.name).write_bytes(b"different-bytes")
        with pytest.raises(InvalidAppError, match="does not match"):
            app._resolve_binary(tmp_path)

    def test_migrates_legacy_unsuffixed_binary(self, custom_app_factory, tmp_path):
        """A pre-suffix installer left under its clean name is renamed to the suffixed name in place."""
        content = b"installer-bytes"
        sha256 = hashlib.sha256(content).hexdigest()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)
        legacy = tmp_path / app.info.file.payload_name
        legacy.write_bytes(content)

        resolved = app._resolve_binary(tmp_path)

        assert resolved == tmp_path / app.info.file.name  # canonical suffixed path
        assert resolved.read_bytes() == content
        assert not legacy.exists()  # migrated, no orphan left behind

    def test_keeps_legacy_binary_when_sha_mismatches(self, custom_app_factory, tmp_path):
        """A legacy installer that fails verification is left under its clean name."""
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256="a" * 64)
        legacy = tmp_path / app.info.file.payload_name
        legacy.write_bytes(b"different-bytes")

        with pytest.raises(InvalidAppError, match=re.escape(str(legacy))):
            app._resolve_binary(tmp_path)

        assert legacy.is_file()  # not renamed to a suffixed name its content does not match
        assert not (tmp_path / app.info.file.name).exists()

    def test_push_remote_create_requires_payload_dir(self, custom_app_factory, config):
        """push_remote(create=True) raises before any API call when no payload dir is given."""
        app = custom_app_factory(has_audit=False)
        with pytest.raises(InvalidAppError, match="payload directory"):
            app.push_remote(config=config, create=True, payload_dir=None)


class TestPushRemotePayload:
    """push_remote forwards each script's content and the resolved binary to the resource."""

    def test_strips_trailing_newlines_before_upload(self, custom_app_factory, config):
        app = custom_app_factory(has_audit=True, has_preinstall=True, has_postinstall=True)
        app.audit.content = "#!/bin/zsh\necho audit\r\n\n"
        app.preinstall.content = "#!/bin/zsh\necho preinstall\n\r"
        app.postinstall.content = "#!/bin/zsh\necho postinstall\n"
        original_hash = app.diff_hash

        payload = app._update_payload(config)

        assert payload["audit_script"] == "#!/bin/zsh\necho audit"
        assert payload["preinstall_script"] == "#!/bin/zsh\necho preinstall"
        assert payload["postinstall_script"] == "#!/bin/zsh\necho postinstall"
        app.audit.content = payload["audit_script"]
        app.preinstall.content = payload["preinstall_script"]
        app.postinstall.content = payload["postinstall_script"]
        assert app.diff_hash == original_hash

    def test_create_sends_scripts_and_resolved_binary(self, custom_app_factory, config, monkeypatch, tmp_path):
        """create passes the three script contents and the resolved binary path as file."""
        content = b"installer-bytes"
        sha256 = hashlib.sha256(content).hexdigest()
        app = custom_app_factory(
            has_audit=True,
            has_preinstall=True,
            has_postinstall=True,
            file_name="installer.pkg",
            file_sha256=sha256,
        )
        binary_path = tmp_path / app.info.file.name
        binary_path.write_bytes(content)

        captured_kwargs = {}

        def fake_create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.create", fake_create)

        app.push_remote(config, create=True, payload_dir=tmp_path)

        assert captured_kwargs["audit_script"] == app.audit.content.rstrip("\r\n")
        assert captured_kwargs["preinstall_script"] == app.preinstall.content.rstrip("\r\n")
        assert captured_kwargs["postinstall_script"] == app.postinstall.content.rstrip("\r\n")
        assert captured_kwargs["file"] == binary_path

    def test_update_without_payload_dir_omits_file(self, custom_app_factory, config, monkeypatch):
        """update without a payload dir sends absent scripts as "" and no file reference."""
        app = custom_app_factory(has_audit=False, has_preinstall=False, has_postinstall=False)

        captured_kwargs = {}

        def fake_update(self, **kwargs):
            captured_kwargs.update(kwargs)
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.update", fake_update)

        app.push_remote(config, create=False)

        assert captured_kwargs["audit_script"] == ""
        assert captured_kwargs["preinstall_script"] == ""
        assert captured_kwargs["postinstall_script"] == ""
        assert "file" not in captured_kwargs


class TestPushRemoteUploadDecision:
    """push_remote uploads on create and only on an update when the binary changed."""

    def test_create_marks_uploaded(self, custom_app_factory, config, monkeypatch, tmp_path):
        """create always uploads, so the outcome is flagged uploaded."""
        content = b"installer-bytes"
        sha256 = hashlib.sha256(content).hexdigest()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)
        (tmp_path / app.info.file.name).write_bytes(content)

        def fake_create(self, **kwargs):
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.create", fake_create)

        outcome = app.push_remote(config, create=True, payload_dir=tmp_path)
        assert outcome.uploaded is True

    def test_uploads_under_suffix_stripped_name(self, custom_app_factory, config, monkeypatch, tmp_path):
        """The installer streams from its suffixed on-disk file but is registered under the clean name."""
        content = b"installer-bytes"
        sha256 = hashlib.sha256(content).hexdigest()
        name = f"installer_{sha256[:8]}.pkg"
        app = custom_app_factory(has_audit=False, file_name=name, file_sha256=sha256)
        (tmp_path / name).write_bytes(content)

        captured = {}

        def fake_create(self, **kwargs):
            captured.update(kwargs)
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.create", fake_create)

        app.push_remote(config, create=True, payload_dir=tmp_path)
        assert captured["file"] == tmp_path / name  # bytes come from the suffixed on-disk file
        assert captured["file_name"] == "installer.pkg"  # but it is registered under the clean name

    def test_update_skips_upload_when_sha_matches(self, custom_app_factory, config, monkeypatch, tmp_path):
        """A matching counterpart sha is a metadata-only PATCH; no binary needs to be on disk."""
        sha256 = "c" * 64
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)
        other = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)

        captured = {}

        def fake_update(self, **kwargs):
            captured.update(kwargs)
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.update", fake_update)

        outcome = app.push_remote(config, create=False, payload_dir=tmp_path, other=other)
        assert outcome.uploaded is False
        assert "file" not in captured  # binary never resolved (absent from tmp_path) and not uploaded

    def test_update_uploads_when_sha_differs(self, custom_app_factory, config, monkeypatch, tmp_path):
        """A differing counterpart sha resolves and uploads the binary."""
        content = b"new-installer"
        sha256 = hashlib.sha256(content).hexdigest()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)
        other = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256="b" * 64)
        (tmp_path / app.info.file.name).write_bytes(content)

        captured = {}

        def fake_update(self, **kwargs):
            captured.update(kwargs)
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.update", fake_update)

        outcome = app.push_remote(config, create=False, payload_dir=tmp_path, other=other)
        assert outcome.uploaded is True
        assert captured["file"] == tmp_path / app.info.file.name

    def test_update_fetches_remote_sha_when_other_missing(self, custom_app_factory, config, monkeypatch, tmp_path):
        """With no counterpart, the remote sha is fetched once to decide whether to upload."""
        content = b"installer"
        sha256 = hashlib.sha256(content).hexdigest()
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=sha256)
        (tmp_path / app.info.file.name).write_bytes(content)

        def fake_get(self, id):
            return CustomAppPayload.model_validate(_payload_data(app.info, sha256="d" * 64))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.get", fake_get)
        captured = {}

        def fake_update(self, **kwargs):
            captured.update(kwargs)
            return CustomAppPayload.model_validate(_payload_data(app.info))

        monkeypatch.setattr("iructl.api.apps.CustomAppsResource.update", fake_update)

        outcome = app.push_remote(config, create=False, payload_dir=tmp_path, other=None)
        assert outcome.uploaded is True
        assert captured["file"] == tmp_path / app.info.file.name


class TestFromApiPayloadFileMetadata:
    """from_api_payload caches the remote-only file_url/file_size for the download pass."""

    def test_caches_file_url_and_size(self, custom_app_factory):
        info = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256="a" * 64).info
        payload = CustomAppPayload.model_validate(_payload_data(info))
        member = CustomApp.from_api_payload(payload)
        assert member.file_url == payload.file_url
        assert member.file_size == payload.file_size

    def test_disk_loaded_member_has_no_file_metadata(self, custom_app_factory):
        app = custom_app_factory(has_audit=False)
        assert app.file_url is None
        assert app.file_size is None


class TestDownloadBinary:
    """download_binary ensures the installer is present, sha-checked against the remote."""

    def test_downloads_when_absent(self, custom_app_factory, monkeypatch, tmp_path):
        content = b"installer"
        member = _remote_member(custom_app_factory, hashlib.sha256(content).hexdigest())

        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            dest.write_bytes(content)

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        assert member.download_binary(tmp_path) == DownloadResult.DOWNLOADED
        assert (tmp_path / member.info.file.name).read_bytes() == content

    def test_up_to_date_when_present_and_matching(self, custom_app_factory, monkeypatch, tmp_path):
        content = b"installer"
        member = _remote_member(custom_app_factory, hashlib.sha256(content).hexdigest())
        (tmp_path / member.info.file.name).write_bytes(content)

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run when the file is up to date")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        assert member.download_binary(tmp_path) == DownloadResult.UP_TO_DATE

    def test_mismatch_skipped_without_force(self, custom_app_factory, monkeypatch, tmp_path):
        member = _remote_member(custom_app_factory, "a" * 64)
        (tmp_path / member.info.file.name).write_bytes(b"different")

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run on a mismatch without force")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        assert member.download_binary(tmp_path) == DownloadResult.MISMATCH_SKIPPED

    def test_force_overwrites_mismatch(self, custom_app_factory, monkeypatch, tmp_path):
        content = b"installer"
        member = _remote_member(custom_app_factory, hashlib.sha256(content).hexdigest())
        (tmp_path / member.info.file.name).write_bytes(b"different")

        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            dest.write_bytes(content)

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        assert member.download_binary(tmp_path, force=True) == DownloadResult.DOWNLOADED
        assert (tmp_path / member.info.file.name).read_bytes() == content

    def test_raises_without_remote_url(self, custom_app_factory, tmp_path):
        app = custom_app_factory(has_audit=False)  # disk-loaded member has no remote file_url
        with pytest.raises(InvalidAppError, match="download URL"):
            app.download_binary(tmp_path)

    def test_migrates_legacy_binary_without_download(self, custom_app_factory, monkeypatch, tmp_path):
        member = _remote_member(custom_app_factory, _INSTALLER_SHA)
        legacy = tmp_path / member.info.file.payload_name
        legacy.write_bytes(_INSTALLER)

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run when the legacy installer matches")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        assert member.download_binary(tmp_path) == DownloadResult.MIGRATED
        assert (tmp_path / member.info.file.name).read_bytes() == _INSTALLER
        assert not legacy.exists()

    @pytest.mark.parametrize(
        ("replaced_sha", "legacy_kept"),
        [
            pytest.param(hashlib.sha256(b"old-installer").hexdigest(), False, id="tracked-removed"),
            pytest.param("b" * 64, True, id="unrecognized-kept"),
            pytest.param(None, True, id="no-replaced-sha-kept"),
        ],
    )
    def test_force_download_legacy_cleanup(self, custom_app_factory, monkeypatch, tmp_path, replaced_sha, legacy_kept):
        """A forced download deletes the stale legacy file only when it matches the previously tracked sha."""
        member = _remote_member(custom_app_factory, _INSTALLER_SHA)
        legacy = tmp_path / member.info.file.payload_name
        legacy.write_bytes(b"old-installer")

        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            dest.write_bytes(_INSTALLER)

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        result = member.download_binary(tmp_path, force=True, replaced_sha=replaced_sha)

        assert result == DownloadResult.DOWNLOADED
        assert (tmp_path / member.info.file.name).read_bytes() == _INSTALLER
        assert legacy.exists() is legacy_kept


class TestPlanDownload:
    """plan_download classifies what download_binary would do, without downloading."""

    @pytest.mark.parametrize(
        ("on_disk", "sha256", "force", "expected"),
        [
            pytest.param(None, "a" * 64, False, DownloadResult.DOWNLOADED, id="absent"),
            pytest.param(_INSTALLER, _INSTALLER_SHA, False, DownloadResult.UP_TO_DATE, id="present-matching"),
            pytest.param(b"different", "a" * 64, False, DownloadResult.MISMATCH_SKIPPED, id="differing-no-force"),
            pytest.param(b"different", "a" * 64, True, DownloadResult.DOWNLOADED, id="differing-force"),
        ],
    )
    def test_plan_download(self, custom_app_factory, tmp_path, on_disk, sha256, force, expected):
        member = _remote_member(custom_app_factory, sha256)
        if on_disk is not None:
            (tmp_path / member.info.file.name).write_bytes(on_disk)
        assert member.plan_download(tmp_path, force=force) == expected

    @pytest.mark.parametrize(
        ("content", "force", "expected"),
        [
            pytest.param(_INSTALLER, False, DownloadResult.MIGRATED, id="legacy-matching"),
            pytest.param(b"different", False, DownloadResult.MISMATCH_SKIPPED, id="legacy-differing-no-force"),
            pytest.param(b"different", True, DownloadResult.DOWNLOADED, id="legacy-differing-force"),
        ],
    )
    def test_plan_download_legacy_named_binary(self, custom_app_factory, tmp_path, content, force, expected):
        member = _remote_member(custom_app_factory, _INSTALLER_SHA)
        (tmp_path / member.info.file.payload_name).write_bytes(content)
        assert member.plan_download(tmp_path, force=force) == expected

    def test_plan_download_prefers_suffixed_target_over_legacy(self, custom_app_factory, tmp_path):
        member = _remote_member(custom_app_factory, _INSTALLER_SHA)
        (tmp_path / member.info.file.name).write_bytes(_INSTALLER)
        (tmp_path / member.info.file.payload_name).write_bytes(b"stale")
        assert member.plan_download(tmp_path) == DownloadResult.UP_TO_DATE


class TestContentSuffixedFilename:
    """content_suffixed_filename and AppFile.payload_name are an idempotent add/strip pair."""

    def test_appends_content_hash_suffix(self):
        assert content_suffixed_filename("MyApp.pkg", "a" * 64) == "MyApp_aaaaaaaa.pkg"

    def test_is_idempotent(self):
        once = content_suffixed_filename("MyApp.pkg", "a" * 64)
        assert content_suffixed_filename(once, "a" * 64) == once

    def test_appfile_suffixes_name_on_construction(self):
        assert AppFile(name="MyApp.pkg", sha256="a" * 64).name == "MyApp_aaaaaaaa.pkg"

    def test_payload_name_strips_the_matching_suffix(self):
        assert AppFile(name="MyApp_aaaaaaaa.pkg", sha256="a" * 64).payload_name == "MyApp.pkg"

    def test_payload_name_leaves_an_unsuffixed_name(self):
        assert AppFile(name="MyApp.pkg", sha256="a" * 64).payload_name == "MyApp.pkg"

    def test_payload_name_keeps_a_non_matching_trailing_hex(self):
        # A trailing hex run that is not this file's content tag is part of the name, not stripped.
        assert AppFile(name="MyApp_deadbeef.pkg", sha256="a" * 64).payload_name == "MyApp_deadbeef.pkg"

    def test_validator_does_not_mutate_caller_dict(self):
        data = {"name": "MyApp.pkg", "sha256": "a" * 64}
        AppFile.model_validate(data)
        assert data["name"] == "MyApp.pkg"  # the caller's dict is left untouched


class TestWouldUpload:
    """would_upload mirrors push_remote's update decision: upload unless the counterpart sha matches."""

    @pytest.mark.parametrize(
        ("other_sha", "expected"),
        [
            pytest.param("c" * 64, False, id="matching-counterpart"),
            pytest.param("d" * 64, True, id="differing-counterpart"),
            pytest.param(None, True, id="missing-counterpart"),
        ],
    )
    def test_would_upload(self, custom_app_factory, other_sha, expected):
        app = custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256="c" * 64)
        other = (
            None
            if other_sha is None
            else custom_app_factory(has_audit=False, file_name="installer.pkg", file_sha256=other_sha)
        )
        assert app.would_upload(other) is expected


class TestCustomAppRendering:
    @pytest.mark.parametrize(
        "preview_mode",
        [pytest.param(False, id="normal"), pytest.param(True, id="preview")],
    )
    @pytest.mark.parametrize(
        "output_format",
        [pytest.param(fmt, id=fmt.value) for fmt in OutputFormat],
    )
    def test_format_plain_text_renders(self, custom_app_obj, output_format, preview_mode):
        """Every output format renders the app name in both normal and preview mode."""
        rendered = custom_app_obj.format_plain_text(output_format, preview_mode=preview_mode)
        assert custom_app_obj.name in rendered

    @pytest.mark.parametrize("preview_mode", [False, True])
    def test_syntax_dict_ensure_blueprints_gated_by_preview(self, custom_app_obj, preview_mode):
        """ensure_blueprints is local-only metadata, surfaced only under preview_mode."""
        rendered = custom_app_obj.prepare_syntax_dict(preview_mode=preview_mode)
        assert ("ensure_blueprints" in rendered) == preview_mode

    def test_syntax_dict_nests_file_reference(self, custom_app_obj):
        """The installer reference renders as a nested file block, mirroring the info schema."""
        assert custom_app_obj.prepare_syntax_dict()["file"] == {
            "name": custom_app_obj.info.file.name,
            "sha256": custom_app_obj.info.file.sha256,
        }

    def test_syntax_dict_inlines_script_bodies(self, custom_app_factory):
        """Script children render inline under *_script keys; an absent child is None."""
        app = custom_app_factory(has_audit=True, has_preinstall=True, has_postinstall=False)
        rendered = app.prepare_syntax_dict()
        assert rendered["audit_script"] == app.audit.content
        assert rendered["preinstall_script"] == app.preinstall.content
        assert rendered["postinstall_script"] is None
        # The raw child attribute names never leak into the rendered dict.
        assert {"audit", "preinstall", "postinstall"}.isdisjoint(rendered)
        # None is dropped under XML, which plistlib cannot represent.
        assert "postinstall_script" not in app.prepare_syntax_dict(syntax=SyntaxType.XML)

    def test_syntax_dict_excludes_sync_hash(self, custom_app_obj):
        """sync_hash is local-only and never serialized for remote comparison."""
        assert "sync_hash" not in custom_app_obj.prepare_syntax_dict()

    def test_syntax_dict_includes_app_metadata(self, custom_app_obj):
        """Full app metadata is carried so app list --format json is complete."""
        rendered = custom_app_obj.prepare_syntax_dict()
        info = custom_app_obj.info
        assert rendered["id"] == info.id
        assert rendered["name"] == info.name
        assert rendered["install_type"] == info.install_type
        assert rendered["install_enforcement"] == info.install_enforcement
        assert rendered["restart"] == info.restart
        assert rendered["active"] == info.active

    def test_plist_preview_strips_nested_none(self, custom_app_factory):
        """ensure_blueprints with a None node must not break plist preview rendering."""
        app = custom_app_factory(has_audit=False)
        app.info.ensure_blueprints = [BlueprintAssignment(blueprint="engineering")]
        assert app.name in app.format_plain_text(OutputFormat.PLIST, preview_mode=True)


class TestAppTable:
    """Characterizes format_table: rows, order, values, content, conditional unzip, title."""

    def test_detail_rows(self, custom_app_obj, table_rows):
        rows = table_rows(custom_app_obj.format_table())
        # custom_app_obj is never a zip install, so no Unzip Location row is present.
        assert [label for label, _ in rows] == [
            "ID",
            "Name",
            "Active",
            "Install Type",
            "Install Enforcement",
            "Restart",
            "File Name",
            "File SHA256",
            "Show in Self Service",
            "Self Service Category ID",
            "Self Service Recommended",
            "Created At",
            "Updated At",
            "Audit Script",
            "Preinstall Script",
            "Postinstall Script",
        ]
        info = custom_app_obj.info
        values = dict(rows)
        assert values["ID"] == custom_app_obj.id
        assert values["Name"] == custom_app_obj.name
        assert values["Active"] == str(info.active)
        assert values["Install Type"] == str(info.install_type)
        assert values["Install Enforcement"] == str(info.install_enforcement)
        assert values["Restart"] == str(info.restart)
        assert values["File Name"] == info.file.name
        assert values["File SHA256"] == info.file.sha256
        assert values["Show in Self Service"] == str(info.show_in_self_service)
        assert values["Self Service Category ID"] == str(info.self_service_category_id or "")
        assert values["Self Service Recommended"] == str(
            "" if info.self_service_recommended is None else info.self_service_recommended
        )
        assert values["Created At"] == info.created_at
        assert values["Updated At"] == (info.updated_at if info.updated_at is not None else info.created_at)

    def test_content_rows_render_every_script_slot(self, custom_app_obj, table_rows):
        rows = dict(table_rows(custom_app_obj.format_table()))
        for attribute, label in (
            ("audit", "Audit Script"),
            ("preinstall", "Preinstall Script"),
            ("postinstall", "Postinstall Script"),
        ):
            cell = rows[label]
            script = getattr(custom_app_obj, attribute)
            assert isinstance(cell, Syntax)
            assert cell.code == (script.content if script is not None else "")

    def test_unzip_row_present_for_zip(self, custom_app_factory, table_rows):
        app = custom_app_factory(install_type=InstallType.ZIP, unzip_location="/Applications")
        assert dict(table_rows(app.format_table()))["Unzip Location"] == "/Applications"

    def test_unzip_row_absent_for_non_zip(self, custom_app_factory, table_rows):
        app = custom_app_factory(install_type=InstallType.PACKAGE)
        labels = [label for label, _ in table_rows(app.format_table())]
        assert "Unzip Location" not in labels

    def test_updated_at_falls_back_to_created_at(self, custom_app_obj, table_rows):
        custom_app_obj.info.updated_at = None
        assert dict(table_rows(custom_app_obj.format_table()))["Updated At"] == custom_app_obj.info.created_at

    @pytest.mark.parametrize("preview_mode", [False, True])
    def test_ensure_blueprints_row_gated_by_preview(self, custom_app_obj, table_rows, preview_mode):
        labels = [label for label, _ in table_rows(custom_app_obj.format_table(preview_mode=preview_mode))]
        assert ("Ensure Blueprints" in labels) == preview_mode
        if preview_mode:
            assert labels[-1] == "Ensure Blueprints"

    def test_title_remote_without_paths(self, custom_app_obj):
        assert custom_app_obj.format_table().title == "Custom App Details (Remote)"
