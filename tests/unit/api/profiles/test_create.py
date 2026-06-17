import plistlib
from io import BufferedReader
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from iructl.api import CustomProfilePayload

from .conftest import delete_profile_factory


def test_successful_create_path(monkeypatch, response_factory, custom_profiles_resource, tmp_path):
    profile_path = tmp_path / "test_profile.mobileconfig"
    profile_path.write_text("dummy mobileconfig data")

    def mock_post_request(self, url: str, data: dict[str, Any], files: list[tuple[str, tuple[str, BufferedReader]]]):
        file_obj = files[0][1][1]
        json_data = {
            "id": "random-id-on-the-fly",
            "name": data["name"],
            "active": data["active"],
            "profile": file_obj.read().decode(),
            "mdm_identifier": "com.kandji.profile.custom.random-id-on-the-fly",
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
        return response_factory(201, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)
    response = custom_profiles_resource.create("Test Profile", profile_path, runs_on_mac=True)
    assert isinstance(response, CustomProfilePayload)
    assert response.name == "Test Profile"
    assert response.active is False
    assert response.profile == "dummy mobileconfig data"


def test_successful_create_buffered_reader(monkeypatch, response_factory, custom_profiles_resource, tmp_path):
    profile_path = tmp_path / "test_profile.mobileconfig"
    profile_path.write_text("dummy mobileconfig data")

    def mock_post_request(self, url: str, data: dict[str, Any], files: list[tuple[str, tuple[str, BufferedReader]]]):
        file_obj = files[0][1][1]
        json_data = {
            "id": "random-id-on-the-fly",
            "name": data["name"],
            "active": data["active"],
            "profile": file_obj.read().decode(),
            "mdm_identifier": "com.kandji.profile.custom.random-id-on-the-fly",
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
        return response_factory(201, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)
    response = custom_profiles_resource.create("Test Profile", profile_path.open("rb"), runs_on_mac=True)
    assert isinstance(response, CustomProfilePayload)
    assert response.name == "Test Profile"
    assert response.active is False
    assert response.profile == "dummy mobileconfig data"


def test_no_file_error(custom_profiles_resource):
    with pytest.raises(FileNotFoundError, match="does not exist or is not readable"):
        custom_profiles_resource.create("Test Profile", Path("/path/to/nowhere"), runs_on_mac=True)


def test_directory_passed_as_file_error(custom_profiles_resource, tmp_path):
    temp_dir = tmp_path / "test_directory"
    temp_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="does not exist or is not readable"):
        custom_profiles_resource.create("Test Profile", temp_dir, runs_on_mac=True)


def test_json_response_error(monkeypatch, response_factory, custom_profiles_resource, tmp_path):
    def mock_post_request(self, url: str, data: dict[str, Any], files: list[tuple[str, tuple[str, BufferedReader]]]):
        return response_factory(201, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)
    profile_path = tmp_path / "test_profile.mobileconfig"
    profile_path.write_text("dummy mobileconfig data")
    with pytest.raises(ValidationError):
        custom_profiles_resource.create("Test Profile", profile_path, runs_on_mac=True)


def test_runs_on_mac_only(monkeypatch, response_factory, custom_profiles_resource, tmp_path):
    profile_path = tmp_path / "test_profile.mobileconfig"
    profile_path.write_text("dummy mobileconfig data")

    def mock_post_request(self, url: str, data: dict[str, Any], files: list[tuple[str, tuple[str, BufferedReader]]]):
        assert data["runs_on_mac"] is True
        assert all(key not in data for key in ("runs_on_iphone", "runs_on_ipad", "runs_on_tv", "runs_on_vision"))
        json_response = {
            "id": "random-id",
            "name": data["name"],
            "active": data["active"],
            "profile": files[0][1][1].read().decode(),
            "mdm_identifier": "com.kandji.profile.custom.random-id",
            "runs_on_mac": True,
            "runs_on_iphone": False,
            "runs_on_ipad": False,
            "runs_on_tv": False,
            "runs_on_vision": False,
            "created_at": "2024-01-01T12:00:00Z",
            "updated_at": "2024-01-01T12:00:00Z",
        }
        return response_factory(201, json_response)

    monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post_request)

    response = custom_profiles_resource.create("Test Profile", profile_path, runs_on_mac=True)
    assert isinstance(response, CustomProfilePayload)
    assert response.name == "Test Profile"


def test_no_valid_runs_on_raises_error(custom_profiles_resource, tmp_path):
    profile_path = tmp_path / "test_profile.mobileconfig"
    profile_path.write_text("dummy mobileconfig data")

    with pytest.raises(ValueError, match=r"At least one runs_on_\* argument must be True."):
        custom_profiles_resource.create(
            "Test Profile",
            profile_path,
            runs_on_mac=False,
            runs_on_iphone=False,
        )


@pytest.mark.allow_http
def test_successful_create_live(config, resources, custom_profiles_resource, request):
    profile_path = resources / "profile_01.mobileconfig"
    profile_name = "Test Profile"
    response = custom_profiles_resource.create(
        name=profile_name,
        file=profile_path,
        runs_on_mac=True,
        runs_on_iphone=True,
        runs_on_ipad=True,
        runs_on_tv=True,
    )
    request.addfinalizer(delete_profile_factory(config, response.id))
    assert response.name == profile_name
    assert response.runs_on_mac is True
    assert response.runs_on_iphone is True
    assert response.runs_on_ipad is True
    assert response.runs_on_tv is True
    assert response.runs_on_vision is False
    assert response.active is False
    profile_payload = plistlib.loads(response.profile.encode())
    profile = plistlib.loads(profile_path.read_bytes())
    assert profile_payload["PayloadDisplayName"] == profile_name
    assert profile_payload["PayloadType"] == profile["PayloadType"]
    assert profile_payload["PayloadContent"] == profile["PayloadContent"]
    assert profile_payload["PayloadIdentifier"].startswith("com.kandji.profile.custom.")
