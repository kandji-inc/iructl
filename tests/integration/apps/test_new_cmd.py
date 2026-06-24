import hashlib

import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl._utils import content_suffixed_filename
from iructl.repository import CustomApp, InstallEnforcement, InstallType
from tests.fixtures.apps import (
    INSTALLER,
    INSTALLER_NAME,
    INSTALLER_SHA,
    make_local_app,
    place_installer,
)

runner = CliRunner()


@pytest.mark.usefixtures("iructl_repo_cd")
class TestAppNew:
    def test_uses_installer_already_in_payload_dir(self, iructl_repo_cd):
        installer = place_installer(iructl_repo_cd)
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
        place_installer(iructl_repo_cd)  # payloads/installer.pkg with INSTALLER bytes
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
        place_installer(iructl_repo_cd)  # payloads/<INSTALLER_NAME> with INSTALLER bytes
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
        place_installer(iructl_repo_cd, b"corrupt", INSTALLER_NAME)  # mis-tagged file at the target name
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
        installer = place_installer(iructl_repo_cd)
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
        member = make_local_app(iructl_repo_cd, custom_app_factory)
        new_content = b"a-different-installer"
        place_installer(iructl_repo_cd, new_content, "new.pkg")

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", "new.pkg"])

        assert result.exit_code == 0, result.output
        reloaded = CustomApp.from_path(member.info_path.parent)
        assert reloaded.info.file.name == content_suffixed_filename("new.pkg", hashlib.sha256(new_content).hexdigest())
        assert reloaded.info.file.sha256 == hashlib.sha256(new_content).hexdigest()

    def test_imports_external_installer(self, iructl_repo_cd, custom_app_factory, tmp_path):
        member = make_local_app(iructl_repo_cd, custom_app_factory)
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
        member = make_local_app(iructl_repo_cd, custom_app_factory)
        source = tmp_path / "new.pkg"
        source.write_bytes(b"a-different-installer")

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", str(source), "--move"])

        assert result.exit_code == 0, result.output
        new_name = content_suffixed_filename("new.pkg", hashlib.sha256(b"a-different-installer").hexdigest())
        assert (iructl_repo_cd / "payloads" / new_name).read_bytes() == b"a-different-installer"
        assert not source.exists()  # move consumes the source

    def test_errors_when_target_name_holds_mismatched_bytes(self, iructl_repo_cd, custom_app_factory, tmp_path):
        member = make_local_app(iructl_repo_cd, custom_app_factory)
        different = b"different-bytes"
        target_name = content_suffixed_filename("new.pkg", hashlib.sha256(different).hexdigest())
        place_installer(iructl_repo_cd, b"existing-bytes", target_name)  # mis-tagged file at the target name
        source = tmp_path / "new.pkg"
        source.write_bytes(different)

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", str(source)])

        assert result.exit_code != 0
        assert (iructl_repo_cd / "payloads" / target_name).read_bytes() == b"existing-bytes"

    def test_errors_when_file_missing_everywhere(self, iructl_repo_cd, custom_app_factory):
        member = make_local_app(iructl_repo_cd, custom_app_factory)

        result = runner.invoke(app, ["app", "set-file", str(member.info_path.parent), "--file", "absent.pkg"])

        assert result.exit_code != 0
