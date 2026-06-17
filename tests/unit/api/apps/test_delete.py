import pytest
from requests.exceptions import HTTPError


def test_delete_successful(monkeypatch, response_factory, custom_apps_resource):
    def mock_delete_request(self, url):
        assert url.endswith("existing-app-id")
        return response_factory(204, {})

    monkeypatch.setattr("iructl.api.client.ApiClient.delete", mock_delete_request)
    custom_apps_resource.delete("existing-app-id")


@pytest.mark.allow_http
def test_delete_successful_live(setup_live_apps_create_only, custom_apps_resource):
    app_id = setup_live_apps_create_only

    # Ensure the app exists in Iru
    assert custom_apps_resource.get(app_id).id == app_id

    # Delete the app, no error is a good thing
    custom_apps_resource.delete(app_id)

    # Double-check that the app is gone
    with pytest.raises(HTTPError) as ctx:
        custom_apps_resource.get(app_id)
    assert ctx.value.response.status_code == 404
