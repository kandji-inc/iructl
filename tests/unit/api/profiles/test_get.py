import pytest
from pydantic import ValidationError

from iructl.api import CustomProfilePayload


def test_successful_get(monkeypatch, response_factory, custom_profiles_resource):
    def mock_get_request(self, profile_id):
        json_data = {
            "id": profile_id.split("/")[-1],
            "name": "odt-sso-ext-test",
            "active": False,
            "profile": "<xml_payload>",
            "mdm_identifier": "com.kandji.profile.custom.a403f056-b1ec-4053-a79d-72ad2fe92485",
            "runs_on_mac": True,
            "runs_on_iphone": False,
            "runs_on_ipad": False,
            "runs_on_tv": False,
            "runs_on_vision": False,
            "created_at": "2023-03-10T19:27:58.677287Z",
            "updated_at": "2023-03-10T20:06:28.622392Z",
        }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    profile_id = "random-id-i-created-on-the-fly"
    response = custom_profiles_resource.get(profile_id)
    assert isinstance(response, CustomProfilePayload)
    assert response.id == profile_id


def test_json_response_error(monkeypatch, response_factory, custom_profiles_resource):
    def mock_get_request(self, profile_id):
        return response_factory(200, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    with pytest.raises(ValidationError):
        custom_profiles_resource.get("random-id")


@pytest.mark.allow_http
def test_successful_get_live(setup_live_profiles_create_and_delete, custom_profiles_resource):
    profile_id = setup_live_profiles_create_and_delete
    response = custom_profiles_resource.get(profile_id)
    assert isinstance(response, CustomProfilePayload)
    assert response.id == profile_id
