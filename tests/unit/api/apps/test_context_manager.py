import pytest

from iructl.api import CustomAppsResource
from iructl.exceptions import ApiClientError


def test_client_exists_in_context_manager(config):
    custom_apps_resource = CustomAppsResource(config)

    assert custom_apps_resource._client is None
    with pytest.raises(ApiClientError) as exc_info:
        custom_apps_resource.client
    assert str(exc_info.value) == "No open client available."

    with custom_apps_resource as apps:
        assert apps.client is not None

    assert custom_apps_resource._client is None
    with pytest.raises(ApiClientError) as exc_info:
        custom_apps_resource.client
    assert str(exc_info.value) == "No open client available."
