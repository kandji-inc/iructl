import pytest
from pydantic import ValidationError

from iructl.api import BlueprintPayload

PAGE_ONE = {
    "count": 3,
    "next": "/api/v1/blueprints?limit=2&offset=2",
    "previous": None,
    "results": [
        {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Production Macs", "type": "classic"},
        {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "name": "Staging", "type": "maps"},
    ],
}
PAGE_TWO = {
    "count": 3,
    "next": None,
    "previous": "/api/v1/blueprints?limit=2",
    "results": [
        {"id": "cccccccc-cccc-cccc-cccc-cccccccccccc", "name": "Testing", "type": "classic"},
    ],
}


def test_list_returns_single_page_results(patch_api_get, response_factory, blueprints_resource):
    paths = patch_api_get(response_factory(200, PAGE_TWO))

    result = blueprints_resource.list()

    assert paths == ["/api/v1/blueprints"]
    assert result.count == 3
    assert result.results == [
        BlueprintPayload(id="cccccccc-cccc-cccc-cccc-cccccccccccc", name="Testing", type="classic")
    ]


def test_list_follows_pagination_until_next_is_none(patch_api_get, response_factory, blueprints_resource):
    paths = patch_api_get(response_factory(200, PAGE_ONE), response_factory(200, PAGE_TWO))

    result = blueprints_resource.list()

    assert paths == ["/api/v1/blueprints", "/api/v1/blueprints?limit=2&offset=2"]
    assert [blueprint.name for blueprint in result.results] == ["Production Macs", "Staging", "Testing"]


def _single_page(blueprint: dict) -> dict:
    return {"count": 1, "next": None, "previous": None, "results": [blueprint]}


def test_list_ignores_unknown_payload_fields(patch_api_get, response_factory, blueprints_resource):
    blueprint = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "name": "Production Macs",
        "type": "classic",
        "color": "blue",
        "computers_count": 7,
    }
    patch_api_get(response_factory(200, _single_page(blueprint)))

    result = blueprints_resource.list()

    assert result.results == [
        BlueprintPayload(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", name="Production Macs", type="classic")
    ]


def test_list_normalizes_ids_to_lowercase(patch_api_get, response_factory, blueprints_resource):
    blueprint = {"id": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "name": "Production Macs", "type": "classic"}
    patch_api_get(response_factory(200, _single_page(blueprint)))

    result = blueprints_resource.list()

    assert result.results[0].id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_list_when_response_malformed_raises_validation_error(patch_api_get, response_factory, blueprints_resource):
    patch_api_get(response_factory(200, {"count": 1, "next": None, "previous": None, "results": "nope"}))

    with pytest.raises(ValidationError, match=r"results"):
        blueprints_resource.list()
