from collections.abc import Callable, Iterator
from typing import Any

import pytest
import requests

from iructl.api import ApiConfig, BlueprintsResource


@pytest.fixture
def blueprints_resource(config: ApiConfig) -> Iterator[BlueprintsResource]:
    """Return an open BlueprintsResource object."""
    with BlueprintsResource(config) as blueprints:
        yield blueprints


@pytest.fixture
def patch_api_get(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Patch ApiClient.get to serve queued responses in order; the returned list records requested paths."""

    def _patch(*responses: requests.Response) -> list[str]:
        queue = list(responses)
        paths: list[str] = []

        def mock_get(self, path):
            paths.append(path)
            return queue.pop(0)

        monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get)
        return paths

    return _patch


@pytest.fixture
def patch_api_post(monkeypatch: pytest.MonkeyPatch) -> Callable[[requests.Response], dict[str, Any]]:
    """Patch ApiClient.post; the returned dict captures path, json, data, files, and anticipated_error on call."""

    def _patch(response: requests.Response) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def mock_post(self, path, data=None, json=None, files=None, anticipated_error=None):
            captured["path"] = path
            captured["json"] = json
            captured["data"] = data
            captured["files"] = files
            captured["anticipated_error"] = anticipated_error
            return response

        monkeypatch.setattr("iructl.api.client.ApiClient.post", mock_post)
        return captured

    return _patch
