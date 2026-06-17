from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urljoin

import pytest
import requests

from iructl.api import ApiConfig, CustomProfilesResource


def create_profile(config: ApiConfig, resources: Path, profile_name: str) -> str:
    """Create a single profile in an Iru tenant and return the profile ID."""
    resource_url = urljoin(config.url, "api/v1/library/custom-profiles")
    resource_file = resources / profile_name
    headers = {"Authorization": f"Bearer {config.api_token}"}
    data = {"name": "Test Profile", "active": "false", "runs_on_mac": "true"}
    response = requests.post(
        resource_url, headers=headers, data=data, files=[("file", (profile_name, resource_file.open("rb")))]
    )
    response.raise_for_status()
    profile_id = response.json()["id"]

    return profile_id


def delete_profile_factory(config: ApiConfig, profile_id: str) -> Callable:
    """Return a function which deletes a specific profile in an Iru tenant.

    The function can be used directly or passed to addfinallizer() in order to setup
    cleanup steps after the test.
    """

    def delete_profile():
        resource_url = urljoin(config.url, f"api/v1/library/custom-profiles/{profile_id}")
        headers = {"Authorization": f"Bearer {config.api_token}"}
        response = requests.delete(resource_url, headers=headers)
        if not response.ok and response.status_code != 404:
            response.raise_for_status()

    return delete_profile


@pytest.fixture
def setup_live_profiles_create_only(config: ApiConfig, resources: Path) -> str:
    """Create a profile in an Iru tenant and return the profile ID."""
    profile_id = create_profile(config, resources, "profile_01.mobileconfig")
    return profile_id


@pytest.fixture
def setup_live_profiles_create_and_delete(config: ApiConfig, resources: Path) -> Iterator[str]:
    """Create a profile in an Iru tenant and delete it after the test."""
    profile_id = create_profile(config, resources, "profile_01.mobileconfig")
    yield profile_id
    delete_profile_factory(config, profile_id)()


@pytest.fixture
def custom_profiles_resource(config: ApiConfig) -> Iterator[CustomProfilesResource]:
    """Return an open CustomProfilesResource object."""
    with CustomProfilesResource(config) as profiles:
        yield profiles
