import pytest
import requests

_APP_ID = "test-app-id-12345"


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Replace apps.sleep with a no-op recorder; return the list of requested backoff delays."""
    delays: list[float] = []
    monkeypatch.setattr("iructl.api.apps.sleep", delays.append)
    return delays


@pytest.fixture
def stub_patch(monkeypatch):
    """Install a fake ApiClient.patch that returns/raises the given outcomes in order.

    The final outcome is reused once exhausted, so a single 503 models a server that
    never finishes processing.
    """

    def _install(outcomes):
        remaining = list(outcomes)

        def fake_patch(self, path, data=None, json=None, files=None):
            outcome = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr("iructl.api.client.ApiClient.patch", fake_patch)

    return _install


@pytest.fixture
def make_http_error(response_factory):
    """Build a realistic requests.HTTPError for a status, as raise_for_status would raise it."""

    def _make(status: int, *, reason: str, body: bytes = b"") -> requests.HTTPError:
        response = response_factory(status, body)
        response.reason = reason
        response.url = f"https://tenant.api.iru.com/api/v1/library/custom-apps/{_APP_ID}"
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            return error
        raise AssertionError(f"status {status} did not raise an HTTPError")

    return _make


def test_transient_503_is_retried_until_success(
    custom_apps_resource, stub_patch, recorded_sleeps, make_http_error, response_factory, valid_app_json
):
    # Generic 503 body the old code would not have retried.
    error_503 = make_http_error(503, reason="Service Unavailable", body=b"Service Unavailable")
    stub_patch([error_503, error_503, response_factory(200, valid_app_json(id="recovered-app"))])

    result = custom_apps_resource.update(id=_APP_ID, name="Renamed App")

    assert result.id == "recovered-app"  # success response after retries
    assert len(recorded_sleeps) == 2  # two retries


def test_503_raises_when_retry_budget_exhausted(custom_apps_resource, stub_patch, recorded_sleeps, make_http_error):
    stub_patch([make_http_error(503, reason="Service Unavailable", body=b"Service Unavailable")])

    with pytest.raises(requests.HTTPError, match="503 Server Error"):
        custom_apps_resource.update(id=_APP_ID, name="Renamed App")

    assert max(recorded_sleeps) <= 30  # backoff cap
    assert sum(recorded_sleeps) >= 300  # full retry budget


def test_non_503_error_is_raised_immediately_without_retry(
    custom_apps_resource, stub_patch, recorded_sleeps, make_http_error
):
    stub_patch([make_http_error(400, reason="Bad Request", body=b"bad payload")])

    with pytest.raises(requests.HTTPError, match="400 Client Error"):
        custom_apps_resource.update(id=_APP_ID, name="Renamed App")

    assert recorded_sleeps == []  # non-503 is fatal, no retry
