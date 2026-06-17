import pytest
from pydantic import ValidationError

from iructl.api import PayloadList


def test_successful_list(monkeypatch, response_factory, custom_profiles_resource):
    def mock_get_request(self, url):
        json_data = {
            "count": 2,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": "a403f056-b1ec-4053-a79d-72ad2fe92485",
                    "name": "odt-sso-ext-test",
                    "active": False,
                    "profile": "<xml payload>",
                    "mdm_identifier": "com.kandji.profile.custom.a403f056-b1ec-4053-a79d-72ad2fe92485",
                    "runs_on_mac": True,
                    "runs_on_iphone": False,
                    "runs_on_ipad": False,
                    "runs_on_tv": False,
                    "runs_on_vision": False,
                    "created_at": "2023-03-10T19:27:58.677287Z",
                    "updated_at": "2023-03-10T20:06:28.622392Z",
                },
                {
                    "id": "fdf05e37-f157-4cbf-9eab-2e2ef43938ab",
                    "name": "Custom Profile New Name",
                    "active": False,
                    "profile": "<xml payload>",
                    "mdm_identifier": "com.kandji.profile.custom.fdf05e37-f157-4cbf-9eab-2e2ef43938ab",
                    "runs_on_mac": True,
                    "runs_on_iphone": False,
                    "runs_on_ipad": False,
                    "runs_on_tv": False,
                    "runs_on_vision": False,
                    "created_at": "2024-01-28T12:12:40.716224Z",
                    "updated_at": "2024-01-28T12:19:47.897192Z",
                },
            ],
        }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    response = custom_profiles_resource.list()
    assert isinstance(response, PayloadList)
    assert response.results[0].id == "a403f056-b1ec-4053-a79d-72ad2fe92485"


def test_successful_list_with_pagination(monkeypatch, response_factory, custom_profiles_resource):
    # Mock the first page
    def mock_get_request(self, url):
        if "offset=2" in url:  # If the URL includes offset=300, return mocked second page data
            json_data = {
                "count": 4,
                "next": None,  # No more pages
                "previous": "https://danielchapa.api.iru.com/api/v1/library/custom-profiles?limit=2",
                "results": [
                    {
                        "id": "a403f056-b1ec-4053-a79d-72ad2fe92485",
                        "name": "odt-sso-ext-test",
                        "active": False,
                        "profile": "<xml payload>",
                        "mdm_identifier": "com.kandji.profile.custom.a403f056-b1ec-4053-a79d-72ad2fe92485",
                        "runs_on_mac": True,
                        "runs_on_iphone": False,
                        "runs_on_ipad": False,
                        "runs_on_tv": False,
                        "runs_on_vision": False,
                        "created_at": "2023-03-10T19:27:58.677287Z",
                        "updated_at": "2023-03-10T20:06:28.622392Z",
                    },
                    {
                        "id": "fdf05e37-f157-4cbf-9eab-2e2ef43938ab",
                        "name": "Custom Profile New Name",
                        "active": False,
                        "profile": "<xml payload>",
                        "mdm_identifier": "com.kandji.profile.custom.fdf05e37-f157-4cbf-9eab-2e2ef43938ab",
                        "runs_on_mac": True,
                        "runs_on_iphone": False,
                        "runs_on_ipad": False,
                        "runs_on_tv": False,
                        "runs_on_vision": False,
                        "created_at": "2024-01-28T12:12:40.716224Z",
                        "updated_at": "2024-01-28T12:19:47.897192Z",
                    },
                ],
            }
        else:  # Mocked first page response
            json_data = {
                "count": 4,
                "next": "https://danielchapa.api.iru.com/api/v1/library/custom-profiles?limit=2&offset=2",
                "previous": None,
                "results": [
                    {
                        "id": "a403f056-b1ec-4053-a79d-72ad2fe92485",
                        "name": "odt-sso-ext-test",
                        "active": False,
                        "profile": "<xml payload>",
                        "mdm_identifier": "com.kandji.profile.custom.a403f056-b1ec-4053-a79d-72ad2fe92485",
                        "runs_on_mac": True,
                        "runs_on_iphone": False,
                        "runs_on_ipad": False,
                        "runs_on_tv": False,
                        "runs_on_vision": False,
                        "created_at": "2023-03-10T19:27:58.677287Z",
                        "updated_at": "2023-03-10T20:06:28.622392Z",
                    },
                    {
                        "id": "fdf05e37-f157-4cbf-9eab-2e2ef43938ab",
                        "name": "Custom Profile New Name",
                        "active": False,
                        "profile": "<xml payload>",
                        "mdm_identifier": "com.kandji.profile.custom.fdf05e37-f157-4cbf-9eab-2e2ef43938ab",
                        "runs_on_mac": True,
                        "runs_on_iphone": False,
                        "runs_on_ipad": False,
                        "runs_on_tv": False,
                        "runs_on_vision": False,
                        "created_at": "2024-01-28T12:12:40.716224Z",
                        "updated_at": "2024-01-28T12:19:47.897192Z",
                    },
                ],
            }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    response = custom_profiles_resource.list()
    assert isinstance(response, PayloadList)
    # Assert that all results from both pages are included
    assert response.count == 4  # 2 profiles on each page
    assert len(response.results) == response.count
    assert response.next is None
    assert response.previous is None


def test_list_json_response_error(monkeypatch, response_factory, custom_profiles_resource):
    def mock_get_request(self, url):
        return response_factory(200, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    with pytest.raises(ValidationError):
        custom_profiles_resource.list()


@pytest.mark.allow_http
def test_successful_list_live(setup_live_profiles_create_and_delete, custom_profiles_resource):
    profile_id = setup_live_profiles_create_and_delete
    response = custom_profiles_resource.list()
    assert isinstance(response, PayloadList)
    assert response.count > 0
    assert len(response.results) == response.count
    assert any(profile.id == profile_id for profile in response.results)
