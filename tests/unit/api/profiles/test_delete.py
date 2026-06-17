import pytest
from requests.exceptions import HTTPError


def test_delete_successful(monkeypatch, response_factory, custom_profiles_resource):
    def mock_delete_request(self, url):
        assert url.endswith("existing-profile-id")
        return response_factory(204, {})

    monkeypatch.setattr("iructl.api.client.ApiClient.delete", mock_delete_request)
    custom_profiles_resource.delete("existing-profile-id")


@pytest.mark.allow_http
def test_delete_successful_live(setup_live_profiles_create_only, custom_profiles_resource):
    profile_id = setup_live_profiles_create_only

    # Ensure the profile exists in Iru
    assert custom_profiles_resource.get(profile_id).id == profile_id

    # Delete the profile, no error is a good thing
    custom_profiles_resource.delete(profile_id)

    # Double-check that the profile is gone
    with pytest.raises(HTTPError) as ctx:
        custom_profiles_resource.get(profile_id)
    assert ctx.value.response.status_code == 404
