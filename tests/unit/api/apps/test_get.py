import pytest
from pydantic import ValidationError

from iructl.api import CustomAppPayload


def test_successful_get(monkeypatch, response_factory, custom_apps_resource):
    def mock_get_request(self, app_id):
        json_data = {
            "id": app_id.split("/")[-1],
            "name": "ms_company_portal",
            "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/CompanyPortal_5.2310.5-Installer_0f04055b.pkg",
            "install_type": "package",
            "install_enforcement": "install_once",
            "audit_script": "",
            "unzip_location": "",
            "active": True,
            "restart": False,
            "preinstall_script": "",
            "postinstall_script": "",
            "file_url": "shortlived S3 URL",
            "file_size": 42375997,
            "file_updated": "2024-01-20T20:07:48Z",
            "created_at": "2024-01-20T20:07:53.612150Z",
            "updated_at": "2024-02-26T22:31:53.632505Z",
            "show_in_self_service": False,
            "sha256": "random-sha-sum",
        }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    app_id = "random-id-i-created-on-the-fly"
    response = custom_apps_resource.get(app_id)
    assert isinstance(response, CustomAppPayload)
    assert response.id == app_id


def test_json_response_error(monkeypatch, response_factory, custom_apps_resource):
    def mock_get_request(self, app_id):
        return response_factory(200, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    with pytest.raises(ValidationError):
        custom_apps_resource.get("random-id")


@pytest.mark.allow_http
def test_successful_get_live(setup_live_apps_create_and_delete, custom_apps_resource):
    app_id = setup_live_apps_create_and_delete
    response = custom_apps_resource.get(app_id)
    assert isinstance(response, CustomAppPayload)
    assert response.id == app_id
