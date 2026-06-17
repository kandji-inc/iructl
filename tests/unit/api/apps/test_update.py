import pytest
from pydantic import ValidationError

from iructl.api import CustomAppPayload, InstallEnforcement, InstallType


@pytest.fixture
def stub_upload(monkeypatch):
    """Stub `_upload_file` to return a fixed S3 key so tests skip the upload path entirely."""

    def _stub(self, file_path, file_size, *, name=None, on_progress=None):
        return "companies/companies/d934a231-e183-4951-b0a0-763e20572c1d/library/custom_apps/test_ae245110.pkg"

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource._upload_file", _stub)


@pytest.fixture
def stub_patch_ok(monkeypatch, response_factory, valid_app_json):
    """Stub `ApiClient.patch` with a 201 + valid payload; returns a dict capturing the call's data."""
    captured: dict = {}

    def _stub(self, path, data):
        captured["path"] = path
        captured["data"] = data
        return response_factory(201, valid_app_json(name="Custom Apps Test Updated"))

    monkeypatch.setattr("iructl.api.client.ApiClient.patch", _stub)
    return captured


@pytest.mark.usefixtures("stub_upload", "stub_patch_ok")
def test_update_returns_payload_when_file_provided(custom_apps_resource, tmp_path):
    temp_file = tmp_path / "test_app_update.pkg"
    temp_file.write_text("This is an updated test app file content.")

    response = custom_apps_resource.update(id="test-app-id-12345", name="Custom Apps Test Updated", file=temp_file)

    assert response.id == "test-app-id-12345"
    assert response.name == "Custom Apps Test Updated"


@pytest.mark.usefixtures("stub_patch_ok")
def test_update_returns_payload_when_no_file_provided(custom_apps_resource):
    response = custom_apps_resource.update(id="test-app-id-12345", name="Custom Apps Test Updated")

    assert response.id == "test-app-id-12345"
    assert response.name == "Custom Apps Test Updated"


def test_update_streams_and_pulses_before_patch(
    monkeypatch, response_factory, custom_apps_resource, tmp_path, recording_reporter, valid_app_json
):
    # A file means an upload: update streams it under the reporter, pulses the bar before the PATCH.
    temp_file = tmp_path / "test_app_update.pkg"
    temp_file.write_text("updated content")

    def mock_upload(self, file_path, file_size, *, name=None, on_progress=lambda _: None):
        recording_reporter.events.append("upload")
        return "companies/companies/d934a231-e183-4951-b0a0-763e20572c1d/library/custom_apps/test_ae245110.pkg"

    def mock_patch(self, path, data):
        recording_reporter.events.append("metadata")
        return response_factory(201, valid_app_json())

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource._upload_file", mock_upload)
    monkeypatch.setattr("iructl.api.client.ApiClient.patch", mock_patch)

    custom_apps_resource.update(id="test-app-id-12345", file=temp_file, reporter=recording_reporter)

    assert recording_reporter.events == ["stream", "upload", "pulse", "metadata"]


@pytest.mark.usefixtures("stub_patch_ok")
def test_update_skips_upload_without_file(monkeypatch, custom_apps_resource):
    # A metadata-only update uploads nothing, so _upload_file is never called.
    uploads: list[object] = []

    def mock_upload(self, file_path, file_size, *, name=None, on_progress=None):
        uploads.append(file_path)
        return "unused"

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource._upload_file", mock_upload)

    custom_apps_resource.update(id="test-app-id-12345", name="Renamed")

    assert uploads == []


@pytest.mark.usefixtures("stub_upload")
def test_update_raises_validation_error_when_response_is_not_json(
    monkeypatch, response_factory, custom_apps_resource, tmp_path
):
    def mock_patch_request(self, path, data):
        return response_factory(201, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.patch", mock_patch_request)

    temp_file = tmp_path / "test_app.pkg"
    temp_file.write_text("This is a test app file content.")

    with pytest.raises(ValidationError, match=r"Invalid JSON"):
        custom_apps_resource.update(id="test-app-id-12345", name="Custom Apps Test Updated", file=temp_file)


@pytest.mark.parametrize(
    ("update_kwargs", "match"),
    [
        pytest.param(
            {"install_type": InstallType.ZIP},
            "unzip_location must be provided when install_type is 'zip'",
            id="zip_without_unzip_location",
        ),
        pytest.param(
            {
                "install_enforcement": InstallEnforcement.INSTALL_ONCE,
                "audit_script": "#!/bin/bash\necho 'Audit script'",
            },
            "audit_script can only be used with install_enforcement 'continuously_enforce'",
            id="audit_script_without_continuously_enforce",
        ),
        pytest.param(
            {"show_in_self_service": True},
            "self_service_category_id is required if show_in_self_service is True",
            id="show_in_self_service_without_category_id",
        ),
        pytest.param(
            {
                "install_enforcement": InstallEnforcement.NO_ENFORCEMENT,
                "show_in_self_service": False,
            },
            '"show_in_self_service" and "self_service_category_id" are required if install_enforcement is NO_ENFORCEMENT',
            id="no_enforcement_with_self_service_false",
        ),
        pytest.param(
            {"install_enforcement": InstallEnforcement.NO_ENFORCEMENT},
            '"show_in_self_service" and "self_service_category_id" are required if install_enforcement is NO_ENFORCEMENT',
            id="no_enforcement_without_self_service",
        ),
    ],
)
def test_update_raises_value_error_for_invalid_kwargs(custom_apps_resource, update_kwargs, match):
    with pytest.raises(ValueError, match=match):
        custom_apps_resource.update(id="test-app-id-12345", **update_kwargs)


def test_update_partial_omits_self_service_keys_from_payload(stub_patch_ok, custom_apps_resource):
    custom_apps_resource.update(id="test-app-id-12345", name="Renamed Only")

    assert "show_in_self_service" not in stub_patch_ok["data"]
    assert "self_service_category_id" not in stub_patch_ok["data"]
    assert "self_service_recommended" not in stub_patch_ok["data"]


@pytest.mark.parametrize(
    ("update_kwargs", "expected_payload"),
    [
        pytest.param(
            {"name": "Renamed Only"},
            {"name": "Renamed Only"},
            id="name_only",
        ),
        pytest.param(
            {"active": False},
            {"active": False},
            id="active_only",
        ),
        pytest.param(
            {"name": "Renamed", "active": True, "restart": False},
            {"name": "Renamed", "active": True, "restart": False},
            id="name_active_restart",
        ),
    ],
)
def test_update_payload_contains_only_explicit_fields(
    stub_patch_ok, custom_apps_resource, update_kwargs, expected_payload
):
    """A partial update sends only the keys the caller explicitly set; unset defaults must not leak."""
    custom_apps_resource.update(id="test-app-id-12345", **update_kwargs)

    assert stub_patch_ok["data"] == expected_payload


@pytest.mark.allow_http
def test_successful_update_live(setup_live_apps_create_and_delete, custom_apps_resource):
    app_id = setup_live_apps_create_and_delete
    response = custom_apps_resource.update(id=app_id, name="Updated Live App Name")
    assert isinstance(response, CustomAppPayload)
    assert response.id == app_id
    assert response.name == "Updated Live App Name"
