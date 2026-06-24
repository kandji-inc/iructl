import pytest
from typer.testing import CliRunner

from iructl._cli import app
from iructl.repository import CustomApp
from tests.fixtures.apps import INSTALLER, INSTALLER_NAME, app_to_response, make_local_app, place_installer

runner = CliRunner()


@pytest.fixture
def registered_app(iructl_repo_cd, custom_app_factory, apps_remote) -> CustomApp:
    """A local app with a matching copy on the remote, addressable by the download command."""
    member = make_local_app(iructl_repo_cd, custom_app_factory)
    apps_remote[member.id] = CustomApp.from_api_payload(app_to_response(member))
    return member


@pytest.mark.usefixtures("iructl_repo_cd", "patch_apps_endpoints")
class TestAppDownload:
    def test_downloads_single_app(self, iructl_repo_cd, registered_app, monkeypatch):
        (iructl_repo_cd / "payloads").mkdir(exist_ok=True)

        def fake_download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
            dest.write_bytes(INSTALLER)

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fake_download)

        result = runner.invoke(app, ["app", "download", registered_app.id])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    @pytest.mark.usefixtures("stub_installer_download")
    def test_force_overwrites_differing_installer(self, iructl_repo_cd, registered_app):
        place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        result = runner.invoke(app, ["app", "download", registered_app.id, "--force"])

        assert result.exit_code == 0, result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    @pytest.mark.usefixtures("stub_installer_download")
    def test_honors_payload_dir(self, iructl_repo_cd, registered_app, tmp_path):
        target = tmp_path / "elsewhere"

        result = runner.invoke(app, ["app", "download", registered_app.id, "--payload-dir", str(target)])

        assert result.exit_code == 0, result.output
        assert (target / INSTALLER_NAME).read_bytes() == INSTALLER
        assert not (iructl_repo_cd / "payloads" / INSTALLER_NAME).exists()

    def test_up_to_date_is_a_noop(self, iructl_repo_cd, registered_app, monkeypatch):
        place_installer(iructl_repo_cd)  # payloads/<INSTALLER_NAME> already matches the remote sha

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run when the installer is already up to date")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        result = runner.invoke(app, ["app", "download", registered_app.id])

        assert result.exit_code == 0, result.output
        assert "already present and up to date" in result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == INSTALLER

    def test_mismatch_without_force_exits_nonzero(self, iructl_repo_cd, registered_app, monkeypatch):
        place_installer(iructl_repo_cd, content=b"stale-local-bytes")

        def fail_download(*args, **kwargs):
            pytest.fail("download should not run on a mismatch without --force")

        monkeypatch.setattr("iructl.api.client.S3Client.download_file", fail_download)

        result = runner.invoke(app, ["app", "download", registered_app.id])

        assert result.exit_code == 1, result.output
        assert "--force" in result.output
        assert (iructl_repo_cd / "payloads" / INSTALLER_NAME).read_bytes() == b"stale-local-bytes"
