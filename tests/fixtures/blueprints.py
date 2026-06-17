import functools

import pytest
import requests

from iructl._cli import utility
from iructl._constants import APP_NAME
from iructl.api import BlueprintPayload
from iructl.repository import BlueprintAssignment

# Tests assert on the leading 8 chars; Rich truncates full UUIDs in output.
BLUEPRINT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BLUEPRINT_A_NAME = "Production Macs"
MISSING_BLUEPRINT = "dddddddd-dddd-dddd-dddd-dddddddddddd"
NODE_A = "11111111-1111-1111-1111-111111111111"

# The 400 body is a JSON string (bytes keep the quotes); the 404 body is an object the CLI unwraps.
DUPLICATE_BODY = b'"Assignment node can only have one of this Library Item type."'
BLUEPRINT_NOT_FOUND_BODY = b'{"detail": "Not found."}'


@pytest.fixture
def make_http_error(response_factory):
    """Return a factory building a ``requests.HTTPError`` from a status code and body."""

    def _make(status_code: int, body: bytes) -> requests.HTTPError:
        return requests.HTTPError(response=response_factory(status_code, body))

    return _make


@pytest.fixture
def patch_blueprints_list(monkeypatch: pytest.MonkeyPatch):
    """Stub the cached tenant blueprint list used for name<->id resolution.

    The installer takes a ``{name: blueprint_id}`` map; installed empty by default so UUID-only
    tests resolve without a network call and reverse-name lookups return ``None``. Patches the
    cached ``_blueprints`` directly rather than ``BlueprintsResource.list`` to bypass its cache.
    """

    def install(blueprints: dict[str, str] | None = None) -> None:
        payloads = [
            BlueprintPayload(id=blueprint_id, name=name, type="classic")
            for name, blueprint_id in (blueprints or {}).items()
        ]
        monkeypatch.setattr("iructl.repository.blueprints._blueprints", lambda _config: payloads)

    install()
    return install


@pytest.fixture
def patch_blueprints_assign(monkeypatch: pytest.MonkeyPatch, patch_blueprints_list):
    """Install a fake ``BlueprintsResource.assign`` and return its call log.

    The installer takes a ``{blueprint_id: behavior}`` map; behavior is ``None`` for success
    or a ``BaseException`` to raise.
    """
    calls: list[dict] = []

    def install(behaviors: dict | None = None) -> list[dict]:
        resolved = behaviors or {}

        def fake_assign(self, blueprint, *, library_item_id, node=None):
            calls.append({"blueprint": blueprint, "library_item_id": library_item_id, "node": node})
            outcome = resolved.get(blueprint)
            if isinstance(outcome, BaseException):
                raise outcome
            return [library_item_id]

        monkeypatch.setattr("iructl.api.blueprints.BlueprintsResource.assign", fake_assign)
        return calls

    return install


@pytest.fixture
def report_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Redirect ``save_report`` to ``tmp_path`` and return that path.

    Its default ``report_path`` is bound at import, so patch the call-site reference.
    """
    path = tmp_path / f"{APP_NAME}_report.json"
    monkeypatch.setattr(
        "iructl._cli.member.defaults.save_report",
        functools.partial(utility.save_report, report_path=path),
    )
    return path


@pytest.fixture
def declare_blueprints():
    def _declare(member, assignments: list[BlueprintAssignment]):
        member.info.ensure_blueprints = assignments
        member.write()
        return member

    return _declare
