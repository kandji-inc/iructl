import random
from collections.abc import Generator
from urllib.parse import parse_qs, urljoin, urlparse

import pytest
from requests import Response, Session
from requests.exceptions import HTTPError

from iructl.api import CustomScriptPayload, CustomScriptsResource, SelfServiceCategoriesResource
from tests.fixtures.scripts import script_info_data_factory


@pytest.fixture
def scripts_resource(config) -> Generator[CustomScriptsResource]:
    with CustomScriptsResource(config) as resource:
        yield resource


@pytest.fixture
def remote_scripts(request, script_info_data_factory) -> dict[str, CustomScriptPayload]:
    marker = request.node.get_closest_marker("script_count")
    script_count = marker.args[0] if marker else 5

    remote_data: dict[str, CustomScriptPayload] = {}
    for _ in range(script_count):
        script_data = script_info_data_factory()
        script_data["script"] = "#!/bin/sh\necho 'Hello, World!'"
        script_data["remediation_script"] = "#!/bin/sh\necho 'Goodbye, World!'" if random.choice([True, False]) else ""
        remote_data[script_data["id"]] = CustomScriptPayload.model_validate(script_data)
    return remote_data


@pytest.fixture
def patched_scripts_resource(
    monkeypatch, config, response_factory, remote_scripts, script_info_data_factory
) -> Generator[CustomScriptsResource]:
    called_counter = {"list": 0, "get": 0, "create": 0, "update": 0, "delete": 0}

    default_list_limit = 5

    def fake_get(self, path) -> Response:
        parsed_path = urlparse(path)
        if parsed_path.path == CustomScriptsResource._path:
            # do list
            called_counter["list"] += 1
            script_list = [script.model_dump(mode="json", exclude_none=True) for script in remote_scripts.values()]

            query = parse_qs(parsed_path.query)
            limit = int(query.get("limit", [default_list_limit])[0])
            offset = int(query.get("offset", [0])[0])
            limited_script_list = script_list[offset : offset + limit]

            response = {
                "count": len(script_list),
                "previous": None
                if offset == 0
                else f"/api/v1/library/custom-scripts?limit={limit}&offset={offset - limit}",
                "next": None
                if offset + limit >= len(script_list)
                else f"/api/v1/library/custom-scripts?limit={limit}&offset={offset + limit}",
                "results": limited_script_list,
            }
            return response_factory(200, response)
        else:
            # do get
            called_counter["get"] += 1
            script_id = path.split("/")[-1]
            if script_id not in remote_scripts:
                raise HTTPError(
                    f"404 Client Error: Not Found for url: {path}",
                    response=response_factory(404, {"detail": "No CustomScript matches the given query."}),
                )
            return response_factory(200, remote_scripts[script_id].model_dump(mode="json", exclude_none=True))

    def fake_post(self, path, json) -> Response:
        called_counter["create"] += 1
        script_data = script_info_data_factory(
            **{k: v for k, v in json.items() if k not in {"script", "remediation_script"}}
        ) | {"script": json["script"], "remediation_script": json.get("remediation_script", "")}
        script = CustomScriptPayload.model_validate(script_data)
        remote_scripts[script.id] = script
        return response_factory(201, script.model_dump(mode="json", exclude_none=True))

    def fake_patch(self, path, json) -> Response:
        called_counter["update"] += 1
        script_id = path.split("/")[-1]
        if script_id not in remote_scripts:
            raise HTTPError(
                f"404 Client Error: Not Found for url: {path}",
                response=response_factory(404, {"detail": "No CustomScript matches the given query."}),
            )
        for key, value in json.items():
            setattr(remote_scripts[script_id], key, value)
        return response_factory(200, remote_scripts[script_id].model_dump(mode="json", exclude_none=True))

    def fake_delete(self, path) -> Response:
        called_counter["delete"] += 1
        script_id = path.split("/")[-1]
        if script_id not in remote_scripts:
            raise HTTPError(
                f"404 Client Error: Not Found for url: {path}",
                response=response_factory(404, {"detail": "No CustomScript matches the given query."}),
            )
        del remote_scripts[script_id]
        return response_factory(204, {})

    monkeypatch.setattr("iructl.api.client.ApiClient.get", fake_get)
    monkeypatch.setattr("iructl.api.client.ApiClient.post", fake_post)
    monkeypatch.setattr("iructl.api.client.ApiClient.patch", fake_patch)
    monkeypatch.setattr("iructl.api.client.ApiClient.delete", fake_delete)

    with CustomScriptsResource(config) as resource:
        setattr(resource, "called_counter", called_counter)
        yield resource


@pytest.fixture
def ensure_script_resources(request, config, script_info_data_factory):
    marker = request.node.get_closest_marker("script_count")
    script_count = marker.args[0] if marker else 5

    created_script_ids = []

    with Session() as session:
        session.headers.update({"Authorization": f"Bearer {config.api_token}", "Accept": "application/json"})

        response = session.get(str(config.url) + SelfServiceCategoriesResource._path)
        response.raise_for_status()
        categories = response.json()

        for _ in range(script_count):
            script_data = script_info_data_factory()

            # Patch with real category id
            if "self_service_category_id" in script_data:
                script_data["self_service_category_id"] = random.choice(categories)["id"]

            script_data = {k: v for k, v in script_data.items() if v is not None}
            script_data["script"] = "#!/bin/sh\necho 'Hello, World!'"
            if random.choice([True, False]):
                script_data["remediation_script"] = "#!/bin/sh\necho 'Goodbye, World!'"
            response = session.post(str(config.url) + CustomScriptsResource._path, json=script_data)
            if response.status_code != 201:
                raise ValueError(f"Failed to create script: {response.text}")
            created_script_ids.append(response.json()["id"])

        yield created_script_ids

        failed = []
        for script_id in created_script_ids:
            response = session.delete(urljoin(str(config.url), f"{CustomScriptsResource._path}/{script_id}"))
            if response.status_code != 204:
                failed.append(script_id)
        if failed:
            raise ValueError(f"Failed to delete scripts: {failed}")
