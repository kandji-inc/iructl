import pytest
from pydantic import ValidationError

from iructl.api import CustomAppPayload, InstallEnforcement, InstallType


def test_successful_create_path(monkeypatch, response_factory, custom_apps_resource, tmp_path, valid_app_json):
    def mock_upload_file(self, file_path, file_size, *, name=None, on_progress=None):
        file_key = "companies/companies/d934a231-e183-4951-b0a0-763e20572c1d/library/custom_apps/test_ae245110.pkg"
        return file_key

    def mock_post_request(self, path, data):
        return response_factory(201, valid_app_json())

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource._upload_file", mock_upload_file)
    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)

    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    response = custom_apps_resource.create(
        name="Custom Apps Test",
        file=temp_file,
        install_type=InstallType.PACKAGE,
        install_enforcement=InstallEnforcement.INSTALL_ONCE,
        audit_script="",
        preinstall_script="",
        postinstall_script="",
        restart=False,
        active=True,
        show_in_self_service=True,
        self_service_category_id="e6f6d5b4-0659-4b37-872c-5471115d453b",
        self_service_recommended=True,
        unzip_location="",
    )

    assert isinstance(response, CustomAppPayload)
    assert response.id == "test-app-id-12345"
    assert response.name == "Custom Apps Test"


def test_create_streams_and_pulses_before_metadata(
    monkeypatch, response_factory, custom_apps_resource, tmp_path, recording_reporter, valid_app_json
):
    """create opens the stream, uploads, pulses the bar, then makes the metadata POST -- in order."""

    def mock_upload_file(self, file_path, file_size, *, name=None, on_progress=lambda _: None):
        recording_reporter.events.append("upload")
        return "companies/companies/d934a231-e183-4951-b0a0-763e20572c1d/library/custom_apps/test_ae245110.pkg"

    def mock_post_request(self, path, data):
        recording_reporter.events.append("metadata")
        return response_factory(201, valid_app_json())

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource._upload_file", mock_upload_file)
    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)

    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    custom_apps_resource.create(
        name="Custom Apps Test",
        file=temp_file,
        install_type=InstallType.PACKAGE,
        install_enforcement=InstallEnforcement.INSTALL_ONCE,
        audit_script="",
        preinstall_script="",
        postinstall_script="",
        restart=False,
        active=True,
        reporter=recording_reporter,
    )

    assert recording_reporter.events == ["stream", "upload", "pulse", "metadata"]


def test_json_response_error(monkeypatch, response_factory, custom_apps_resource, tmp_path):
    def mock_upload_file(self, file_path, file_size, *, name=None, on_progress=None):
        file_key = "companies/companies/d934a231-e183-4951-b0a0-763e20572c1d/library/custom_apps/test_ae245110.pkg"
        return file_key

    def mock_post_request(self, path, data):
        return response_factory(201, b"not a json response")

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource._upload_file", mock_upload_file)
    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)

    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    with pytest.raises(ValidationError):
        custom_apps_resource.create(
            name="Custom Apps Test",
            file=temp_file,
            install_type=InstallType.PACKAGE,
            install_enforcement=InstallEnforcement.INSTALL_ONCE,
            audit_script="",
            preinstall_script="",
            postinstall_script="",
            restart=False,
            active=True,
        )


def test_create_with_missing_unzip_location(custom_apps_resource, tmp_path):
    temp_file = tmp_path / "test_app.zip"
    temp_file.write_text("This is a test app file content.")

    with pytest.raises(ValueError, match="unzip_location must be provided when install_type is 'zip'"):
        custom_apps_resource.create(
            name="Missing Unzip Location Test",
            file=temp_file,
            install_type=InstallType.ZIP,  # Valid install type
            install_enforcement=InstallEnforcement.INSTALL_ONCE,
            audit_script="",
            preinstall_script="",
            postinstall_script="",
            restart=False,
            active=True,
        )


def test_create_with_invalid_audit_script(custom_apps_resource, tmp_path):
    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    with pytest.raises(
        ValueError, match="audit_script can only be used with install_enforcement 'continuously_enforce'"
    ):
        custom_apps_resource.create(
            name="Invalid Audit Script Test",
            file=temp_file,
            install_type=InstallType.PACKAGE,
            install_enforcement=InstallEnforcement.INSTALL_ONCE,  # Invalid enforcement for audit script
            audit_script="#!/bin/bash\necho 'Audit script'",
            preinstall_script="",
            postinstall_script="",
            restart=False,
            active=True,
        )


def test_create_with_show_in_self_service_missing_category_id(custom_apps_resource, tmp_path):
    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    with pytest.raises(ValueError, match="self_service_category_id is required if show_in_self_service is True"):
        custom_apps_resource.create(
            name="Missing Category ID Test",
            file=temp_file,
            install_type=InstallType.PACKAGE,
            install_enforcement=InstallEnforcement.INSTALL_ONCE,
            audit_script="",
            preinstall_script="",
            postinstall_script="",
            restart=False,
            active=True,
            show_in_self_service=True,  # Missing self_service_category_id
        )


def test_create_with_no_enforcement_requires_self_service(custom_apps_resource, tmp_path):
    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    with pytest.raises(
        ValueError,
        match='"show_in_self_service" and "self_service_category_id" are required if install_enforcement is NO_ENFORCEMENT',
    ):
        custom_apps_resource.create(
            name="No Enforcement Without Self Service Test",
            file=temp_file,
            install_type=InstallType.PACKAGE,
            install_enforcement=InstallEnforcement.NO_ENFORCEMENT,
            audit_script="",
            preinstall_script="",
            postinstall_script="",
            restart=False,
            active=True,
            show_in_self_service=False,
        )


@pytest.mark.allow_http
def test_successful_create_live(custom_apps_resource, register_app_cleanup, tmp_path):
    temp_file = tmp_path / "live_test_app.zip"
    temp_file.write_text("This is a live test app file content.")
    response = custom_apps_resource.create(
        name="Live Test App",
        file=temp_file,
        install_type=InstallType.ZIP,
        install_enforcement=InstallEnforcement.CONTINUOUSLY_ENFORCE,
        audit_script="#!/bin/bash\necho 'Audit script'",
        preinstall_script="#!/bin/bash\necho 'Pre-install script'",
        postinstall_script="#!/bin/bash\necho 'Post-install script'",
        restart=False,
        active=True,
        show_in_self_service=False,
        unzip_location="/var/tmp",
    )
    register_app_cleanup(response.id)
    assert response.id is not None
    assert response.name == "Live Test App"
    assert response.install_type == "zip"
    assert response.install_enforcement == "continuously_enforce"
    assert response.audit_script == "#!/bin/bash\necho 'Audit script'"
    assert response.preinstall_script == "#!/bin/bash\necho 'Pre-install script'"
    assert response.postinstall_script == "#!/bin/bash\necho 'Post-install script'"
    assert response.restart is False
    assert response.active is True
