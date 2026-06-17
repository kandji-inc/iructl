import plistlib
from io import BufferedReader
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from iructl.api import CustomProfilePayload


def test_successful_patch(monkeypatch, response_factory, custom_profiles_resource, tmp_path):
    profile_path = tmp_path / "updated_profile.mobileconfig"
    profile_path.write_text("updated mobileconfig data")

    def mock_patch_request(self, url: str, data: dict[str, Any], files: list[tuple[str, tuple[str, BufferedReader]]]):
        file_obj = files[0][1][1]
        json_data = {
            "id": url.rsplit("/", maxsplit=1)[-1],  # Extract the profile id from the URL
            "name": data["name"],
            "active": data["active"],
            "profile": file_obj.read().decode(),
            "mdm_identifier": f"com.kandji.profile.custom.{url.rsplit('/', maxsplit=1)[-1]}",
            "runs_on_mac": True,
            "runs_on_iphone": False,
            "runs_on_ipad": False,
            "runs_on_tv": False,
            "runs_on_vision": False,
            "created_at": "2024-01-01T12:00:00Z",
            "updated_at": "2024-01-01T12:00:00Z",
        }
        if not file_obj.closed:
            file_obj.close()
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.patch", mock_patch_request)
    response = custom_profiles_resource.update("existing-profile-id", "Updated Profile Name", profile_path)
    assert isinstance(response, CustomProfilePayload)
    assert response.id == "existing-profile-id"
    assert response.name == "Updated Profile Name"
    assert response.profile == "updated mobileconfig data"


def test_no_file_response(custom_profiles_resource):
    with pytest.raises(FileNotFoundError, match="does not exist or is not readable"):
        custom_profiles_resource.update("existing-profile-id", "Updated Profile Name", Path("/path/to/nowhere"))


def test_json_response_error(monkeypatch, response_factory, custom_profiles_resource, tmp_path):
    def mock_patch_request(self, url, data=None, files=None):
        return response_factory(200, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.patch", mock_patch_request)
    profile_path = tmp_path / "updated_profile.mobileconfig"
    profile_path.write_text("updated mobileconfig data")
    with pytest.raises(ValidationError):
        custom_profiles_resource.update("existing-profile-id", "Updated Profile Name", profile_path)


def test_successful_patch_without_file(monkeypatch, response_factory, custom_profiles_resource):
    def mock_patch_request(self, url: str, data: dict[str, Any], files: list[tuple[str, tuple[str, BufferedReader]]]):
        json_data = {
            "id": url.rsplit("/", maxsplit=1)[-1],  # Extract the profile id from the URL
            "name": data["name"] if "name" in data else "Existing Profile Name",
            "active": data["active"] if "active" in data else False,
            "profile": "<updated_xml_payload>",
            "mdm_identifier": f"com.kandji.profile.custom.{data.get('name', 'Existing Profile')}",
            "runs_on_mac": True,
            "runs_on_iphone": False,
            "runs_on_ipad": False,
            "runs_on_tv": False,
            "runs_on_vision": False,
            "created_at": "2024-01-01T12:00:00Z",
            "updated_at": "2024-01-02T12:00:00Z",
        }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.patch", mock_patch_request)
    response = custom_profiles_resource.update(id="existing-profile-id", name="Updated Profile Name")
    assert isinstance(response, CustomProfilePayload)
    assert response.id == "existing-profile-id"
    assert response.name == "Updated Profile Name"


@pytest.mark.allow_http
def test_successful_patch_live(setup_live_profiles_create_and_delete, resources, custom_profiles_resource):
    profile_id = setup_live_profiles_create_and_delete
    profile_path = resources / "profile_02.mobileconfig"
    profile_name = "Test Profile Updated"
    response = custom_profiles_resource.update(profile_id, name=profile_name, file=profile_path)
    assert isinstance(response, CustomProfilePayload)
    assert response.id == profile_id
    assert response.name == profile_name
    profile_payload = plistlib.loads(response.profile.encode())
    profile = plistlib.loads(profile_path.read_bytes())
    assert profile_payload["PayloadDisplayName"] == profile_name
    assert profile_payload["PayloadType"] == profile["PayloadType"]
    assert profile_payload["PayloadContent"] == profile["PayloadContent"]
    assert profile_payload["PayloadIdentifier"].startswith("com.kandji.profile.custom.")
