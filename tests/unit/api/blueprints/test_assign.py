import pytest
import requests
from pydantic import ValidationError

from iructl.api import is_duplicate_assignment


@pytest.mark.parametrize(
    ("kwargs", "expected_json"),
    [
        pytest.param(
            {"node": "node-uuid"},
            {"library_item_id": "li-uuid", "assignment_node_id": "node-uuid"},
            id="with_node",
        ),
        pytest.param(
            {},
            {"library_item_id": "li-uuid"},
            id="without_node",
        ),
        pytest.param(
            {"node": ""},
            {"library_item_id": "li-uuid"},
            id="empty_node",
        ),
    ],
)
def test_assign_sends_expected_body(patch_api_post, response_factory, blueprints_resource, kwargs, expected_json):
    captured = patch_api_post(response_factory(200, ["li-1"]))

    blueprints_resource.assign("bp-uuid", library_item_id="li-uuid", **kwargs)

    assert captured["path"] == "/api/v1/blueprints/bp-uuid/assign-library-item"
    assert captured["json"] == expected_json
    assert captured["data"] is None
    assert captured["files"] is None
    # The duplicate-assignment 400 is classified by the caller, so it is passed as anticipated.
    assert captured["anticipated_error"] is is_duplicate_assignment


def test_assign_returns_parsed_library_item_ids(patch_api_post, response_factory, blueprints_resource):
    patch_api_post(response_factory(200, ["li-1", "li-2"]))

    response = blueprints_resource.assign("bp-uuid", library_item_id="li-uuid")

    assert response == ["li-1", "li-2"]


def test_assign_when_non_2xx_response_raises_http_error(monkeypatch, response_factory, blueprints_resource):
    def mock_session_request(self, method, url, *args, **kwargs):
        return response_factory(400, b"Assignment node not found")

    monkeypatch.setattr("requests.sessions.Session.request", mock_session_request)
    with pytest.raises(requests.HTTPError, match=r"400") as exc:
        blueprints_resource.assign("bp-uuid", library_item_id="li-uuid", node="node-uuid")
    assert exc.value.response.text == "Assignment node not found"


def test_assign_when_response_is_malformed_raises_validation_error(
    patch_api_post, response_factory, blueprints_resource
):
    patch_api_post(response_factory(200, {"library_items": ["li-1"]}))

    with pytest.raises(ValidationError, match=r"list"):
        blueprints_resource.assign("bp-uuid", library_item_id="li-uuid")


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        pytest.param(400, b'"Library Item already exists in Assignment Node"', True, id="already-assigned"),
        pytest.param(400, b'"Assignment node can only have one of this Library Item type."', True, id="node-exclusive"),
        pytest.param(400, b"Assignment node not found", False, id="other-400"),
        pytest.param(409, b'"Library Item already exists in Assignment Node"', False, id="marker-wrong-status"),
        pytest.param(404, b'{"details": "blueprint not found"}', False, id="404"),
    ],
)
def test_is_duplicate_assignment(response_factory, status_code, body, expected):
    assert is_duplicate_assignment(response_factory(status_code, body)) is expected
