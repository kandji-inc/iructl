from collections.abc import Generator

import pytest

from iructl.api import SelfServiceCategoriesResource, SelfServiceCategoryPayload


@pytest.fixture
def ss_categories_resource(config) -> Generator[SelfServiceCategoriesResource]:
    with SelfServiceCategoriesResource(config) as resource:
        yield resource


@pytest.fixture
def patched_ss_categories_endpoints(monkeypatch, config, response_factory) -> Generator[SelfServiceCategoriesResource]:
    remote_data = [
        {"id": "19837db4-21a2-4b17-b9b5-ebb69678110b", "name": "Apps"},
        {"id": "dfc22bac-192a-4a67-846d-b2a689a95c10", "name": "Productivity"},
        {"id": "32442c29-688e-4eb3-beb6-5a1394234c75", "name": "Utilities"},
        {"id": "348a25e9-49e8-4bf4-8eb9-e9d18de641cf", "name": "Security"},
    ]

    called_counter = {"list": 0}

    def fake_list(self, path) -> list[dict[str, str]]:
        called_counter["list"] += 1

        return response_factory(200, remote_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", fake_list)

    with SelfServiceCategoriesResource(config) as resource:
        setattr(resource, "called_counter", called_counter)
        yield resource


@pytest.mark.allow_http
def test_list_live(ss_categories_resource):
    categories = ss_categories_resource.list()
    assert len(categories) > 0
    assert all(isinstance(category, SelfServiceCategoryPayload) for category in categories)


def test_list(patched_ss_categories_endpoints):
    categories = patched_ss_categories_endpoints.list()
    assert len(categories) == 4
    assert all(isinstance(category, SelfServiceCategoryPayload) for category in categories)
    assert patched_ss_categories_endpoints.called_counter["list"] == 1
