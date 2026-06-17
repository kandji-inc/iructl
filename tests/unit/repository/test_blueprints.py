import pytest
import requests

from iructl.api import ApiConfig, BlueprintPayload, PayloadList
from iructl.repository.blueprints import get_blueprint_id, get_blueprint_name

BLUEPRINT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BLUEPRINT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture
def patch_blueprints_list(monkeypatch):
    """Patch BlueprintsResource.list with canned name -> UUID data; the returned dict counts list calls."""

    def _patch(blueprints: dict[str, str]) -> dict[str, int]:
        calls = {"count": 0}

        def fake_list(self):
            calls["count"] += 1
            return PayloadList[BlueprintPayload](
                count=len(blueprints),
                results=[
                    BlueprintPayload(id=blueprint_id, name=name, type="classic")
                    for name, blueprint_id in blueprints.items()
                ],
            )

        monkeypatch.setattr("iructl.api.blueprints.BlueprintsResource.list", fake_list)
        return calls

    return _patch


def test_get_blueprint_id_passes_uuid_through_without_api_call(config, patch_blueprints_list):
    calls = patch_blueprints_list({})

    assert get_blueprint_id(config, BLUEPRINT_A.upper()) == BLUEPRINT_A
    assert calls["count"] == 0


def test_get_blueprint_id_resolves_name_case_insensitively(config, patch_blueprints_list):
    patch_blueprints_list({"Production Macs": BLUEPRINT_A})

    assert get_blueprint_id(config, "pRoDuCtIoN mAcS") == BLUEPRINT_A


def test_get_blueprint_id_when_name_unknown_raises_value_error(config, patch_blueprints_list):
    patch_blueprints_list({"Production Macs": BLUEPRINT_A})

    with pytest.raises(ValueError, match=r"Unable to get ID for blueprint 'Missing'\. Ensure the blueprint exists\."):
        get_blueprint_id(config, "Missing")


def test_get_blueprint_id_fetches_blueprint_map_once_per_config(config, patch_blueprints_list):
    calls = patch_blueprints_list({"Production Macs": BLUEPRINT_A, "Staging": BLUEPRINT_B})

    assert get_blueprint_id(config, "Production Macs") == BLUEPRINT_A
    assert get_blueprint_id(config, "Staging") == BLUEPRINT_B
    assert calls["count"] == 1


def test_get_blueprint_id_does_not_share_cache_across_configs(config, patch_blueprints_list):
    calls = patch_blueprints_list({"Production Macs": BLUEPRINT_A})
    other_config = ApiConfig(tenant_url="https://other.api.iru.com", api_token="11111111-1111-1111-1111-111111111111")

    assert get_blueprint_id(config, "Production Macs") == BLUEPRINT_A
    assert get_blueprint_id(other_config, "Production Macs") == BLUEPRINT_A
    assert calls["count"] == 2


def test_get_blueprint_name_resolves_uuid_to_name(config, patch_blueprints_list):
    patch_blueprints_list({"Production Macs": BLUEPRINT_A})

    assert get_blueprint_name(config, BLUEPRINT_A.upper()) == "Production Macs"


def test_get_blueprint_name_when_uuid_unknown_returns_none(config, patch_blueprints_list):
    patch_blueprints_list({"Production Macs": BLUEPRINT_A})

    assert get_blueprint_name(config, BLUEPRINT_B) is None


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(requests.ConnectionError("connection refused"), id="connection-error"),
        pytest.param(requests.ReadTimeout("read timed out"), id="read-timeout"),
        pytest.param(requests.HTTPError("503 Server Error"), id="http-error"),
    ],
)
def test_get_blueprint_name_when_list_fails_returns_none(config, monkeypatch, error):
    def fail_list(self):
        raise error

    monkeypatch.setattr("iructl.api.blueprints.BlueprintsResource.list", fail_list)

    assert get_blueprint_name(config, BLUEPRINT_A) is None


def test_resolver_directions_share_one_fetch(config, patch_blueprints_list):
    calls = patch_blueprints_list({"Production Macs": BLUEPRINT_A})

    assert get_blueprint_id(config, "Production Macs") == BLUEPRINT_A
    assert get_blueprint_name(config, BLUEPRINT_A) == "Production Macs"
    assert calls["count"] == 1
