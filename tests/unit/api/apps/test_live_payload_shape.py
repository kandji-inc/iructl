import base64
import contextlib
import json
import warnings
from datetime import UTC, datetime
from time import sleep

import pytest
import requests

from iructl.api import CustomAppPayload, SelfServiceCategoriesResource
from iructl.api.apps import _RETRY_BACKOFF_CAP, _RETRY_MAX_WAIT

pytestmark = pytest.mark.allow_http


def _request_with_retry(client, method: str, path: str, **kwargs) -> requests.Response:
    """Issue a request, retrying transient 503s like CustomAppsResource._send_with_retry.

    Any 503 is retried with capped exponential backoff up to _RETRY_MAX_WAIT; non-503
    HTTPErrors bubble up so callers can assert on 400s.
    """
    attempt = 0
    waited = 0.0
    while True:
        try:
            return client.request(method, client._make_url(path), **kwargs)
        except requests.HTTPError as e:
            if e.response.status_code == 503 and waited < _RETRY_MAX_WAIT:
                delay = min(2**attempt, _RETRY_BACKOFF_CAP)
                sleep(delay)
                waited += delay
                attempt += 1
                continue
            raise


def _base_create_payload(*, file_key: str, name: str, **overrides) -> dict:
    """Default CREATE body that satisfies every companion-field rule."""
    payload = {
        "name": name,
        "file_key": file_key,
        "install_type": "package",
        "install_enforcement": "install_once",
        "restart": False,
        "active": False,
        "show_in_self_service": False,
        "preinstall_script": "",
        "postinstall_script": "",
    }
    payload.update(overrides)
    return payload


class TestResponseShape:
    """Pin the JSON shape of GET / LIST / PATCH responses; warn on key drift vs ``CustomAppPayload``."""

    # Server only returns these when show_in_self_service is true; otherwise absence is expected.
    _SELF_SERVICE_ONLY_FIELDS = frozenset({"self_service_category_id", "self_service_recommended"})

    def _warn_on_key_drift(self, raw: dict, op: str) -> None:
        """Surface any divergence between raw JSON keys and CustomAppPayload model fields as a pytest warning."""
        model_fields = set(CustomAppPayload.model_fields)
        unmodeled = sorted(set(raw) - model_fields)
        not_returned = set(model_fields) - set(raw)
        if not raw.get("show_in_self_service"):
            not_returned -= self._SELF_SERVICE_ONLY_FIELDS
        not_returned = sorted(not_returned)
        if unmodeled:
            warnings.warn(
                f"{op}: server-side keys not modeled by CustomAppPayload: {unmodeled}",
                UserWarning,
                stacklevel=2,
            )
        if not_returned:
            warnings.warn(
                f"{op}: CustomAppPayload fields not returned by server: {not_returned}",
                UserWarning,
                stacklevel=2,
            )

    @pytest.mark.parametrize(
        ("fixture_name", "self_service_on"),
        [
            ("setup_live_apps_create_and_delete", False),
            ("setup_live_apps_create_and_delete_with_self_service", True),
        ],
        ids=["disabled", "enabled"],
    )
    def test_get_self_service_fields(self, custom_apps_resource, request, fixture_name, self_service_on):
        app_id = request.getfixturevalue(fixture_name)
        raw = custom_apps_resource.client.get(f"/api/v1/library/custom-apps/{app_id}").json()

        assert ("self_service_category_id" in raw) is self_service_on
        assert ("self_service_recommended" in raw) is self_service_on
        self._warn_on_key_drift(raw, f"GET (show_in_self_service={self_service_on})")

        parsed = custom_apps_resource.get(app_id)
        assert parsed.id == app_id
        assert parsed.self_service_category_id == raw.get("self_service_category_id")
        assert parsed.self_service_recommended == raw.get("self_service_recommended")

    @pytest.mark.usefixtures("setup_live_apps_create_and_delete")
    def test_list_fields(self, custom_apps_resource):
        raw = custom_apps_resource.client.get("/api/v1/library/custom-apps").json()
        assert len(raw["results"]) >= 1, "list returned no results — verification needs at least one app on the tenant"

        self._warn_on_key_drift(raw["results"][0], "LIST item")

        parsed_list = custom_apps_resource.list()
        assert parsed_list.count >= 1

    def test_patch_fields(self, custom_apps_resource, setup_live_apps_create_and_delete, live_app_upload):
        app_id = setup_live_apps_create_and_delete
        before = custom_apps_resource.client.get(f"/api/v1/library/custom-apps/{app_id}").json()

        file_key = live_app_upload("replacement.pkg")
        raw = _request_with_retry(
            custom_apps_resource.client,
            "PATCH",
            f"/api/v1/library/custom-apps/{app_id}",
            data={"file_key": file_key},
        ).json()

        assert raw["file_key"] != before["file_key"]
        assert raw["file_updated"] != before["file_updated"]
        self._warn_on_key_drift(raw, "PATCH with new file")

        parsed = custom_apps_resource.get(app_id)
        assert parsed.file_key == raw["file_key"]

    def test_create_fields(self, custom_apps_resource, live_app_upload):
        """Pin the CREATE response shape; warns on key drift vs CustomAppPayload."""
        file_key = live_app_upload("create_response_shape_probe.pkg")
        payload = _base_create_payload(file_key=file_key, name="create_response_shape_probe")
        response = _request_with_retry(custom_apps_resource.client, "POST", "/api/v1/library/custom-apps", json=payload)
        created = response.json()
        try:
            self._warn_on_key_drift(created, "CREATE")
        finally:
            custom_apps_resource.delete(created["id"])


class TestUploadEndpoint:
    def test_presigned_post_policy_contains_expiration(self, custom_apps_resource):
        raw = custom_apps_resource.client.post(
            "/api/v1/library/custom-apps/upload", data={"name": "verification_probe.pkg"}
        ).json()

        policy = json.loads(base64.b64decode(raw["post_data"]["policy"]))
        # AWS pre-signed POST policies use ISO-8601 UTC (e.g. "2024-01-01T00:00:00.000Z");
        # parse-or-raise validates the format and the comparison pins that it has not already expired.
        expiration = datetime.fromisoformat(policy["expiration"])
        assert expiration > datetime.now(UTC)


class TestCompanionFieldRules:
    """Live API enforcement of the companion-field rules."""

    def _request_no_raise(self, client, method, path, **kwargs):
        """Like _request_with_retry but returns the response even on 4xx."""
        try:
            return _request_with_retry(client, method, path, **kwargs)
        except requests.HTTPError as e:
            return e.response

    def _get_category_id(self, config):
        """Fetch a self-service category id; skip the test if the tenant has none."""
        with SelfServiceCategoriesResource(config) as categories:
            category_list = categories.list()
        if not category_list:
            pytest.skip("tenant has no self-service categories")
        return category_list[0].id

    def _run_create(self, name, overrides, outcome, error_key, custom_apps_resource, live_app_upload):
        """Run a CREATE probe and assert the binary outcome."""

        file_key = live_app_upload(f"{name}.pkg")
        payload = _base_create_payload(file_key=file_key, name=name, **overrides)
        url = "/api/v1/library/custom-apps"

        request_ctx = pytest.raises(requests.HTTPError) if outcome == "reject" else contextlib.nullcontext()
        with request_ctx as exc_info:
            response = _request_with_retry(custom_apps_resource.client, "POST", url, json=payload)

        if outcome == "reject":
            assert exc_info is not None
            assert exc_info.value.response.status_code == 400
            assert error_key in exc_info.value.response.json()
        else:
            created = response.json()
            try:
                assert "id" in created
            finally:
                custom_apps_resource.delete(created["id"])

    @pytest.mark.parametrize(
        ("overrides", "outcome", "error_key"),
        [
            pytest.param(
                {"install_enforcement": "no_enforcement"},
                "reject",
                "show_in_self_service",
                id="without_self_service",
            ),
            pytest.param(
                {"install_enforcement": "no_enforcement", "show_in_self_service": True},
                "accept",
                None,
                id="with_self_service",
            ),
        ],
    )
    def test_no_enforcement_requires_self_service(
        self, custom_apps_resource, config, live_app_upload, overrides, outcome, error_key
    ):
        if outcome == "accept":
            overrides = {**overrides, "self_service_category_id": self._get_category_id(config)}
        self._run_create("no_enforcement", overrides, outcome, error_key, custom_apps_resource, live_app_upload)

    @pytest.mark.parametrize(
        ("overrides", "outcome", "error_key"),
        [
            pytest.param(
                {"install_enforcement": "install_once", "audit_script": "#!/bin/sh\nexit 0"},
                "reject",
                "audit_script",
                id="install_once",
            ),
            pytest.param(
                {
                    "install_enforcement": "no_enforcement",
                    "audit_script": "#!/bin/sh\nexit 0",
                    "show_in_self_service": True,
                },
                "reject",
                "audit_script",
                id="no_enforcement",
            ),
            pytest.param(
                {"install_enforcement": "continuously_enforce", "audit_script": "#!/bin/sh\nexit 0"},
                "accept",
                None,
                id="continuously_enforce",
            ),
        ],
    )
    def test_audit_script_requires_continuously_enforce(
        self, custom_apps_resource, config, live_app_upload, overrides, outcome, error_key
    ):
        if overrides.get("install_enforcement") == "no_enforcement":
            # Satisfy rule 1 so only the audit_script rule fires.
            overrides = {**overrides, "self_service_category_id": self._get_category_id(config)}
        self._run_create("audit_script", overrides, outcome, error_key, custom_apps_resource, live_app_upload)

    @pytest.mark.parametrize(
        ("overrides", "outcome", "error_key"),
        [
            pytest.param(
                {"install_type": "zip"},
                "reject",
                "unzip_location",
                id="without_unzip_location",
            ),
            pytest.param(
                {"install_type": "zip", "unzip_location": "/var/tmp"},
                "accept",
                None,
                id="with_unzip_location",
            ),
        ],
    )
    def test_zip_requires_unzip_location(self, custom_apps_resource, live_app_upload, overrides, outcome, error_key):
        self._run_create("zip", overrides, outcome, error_key, custom_apps_resource, live_app_upload)

    @pytest.mark.parametrize(
        ("overrides", "outcome", "error_key"),
        [
            pytest.param(
                {"show_in_self_service": True},
                "reject",
                "self_service_category_id",
                id="without_category",
            ),
            pytest.param(
                {"show_in_self_service": True, "self_service_recommended": True},
                "accept",
                None,
                id="with_category",
            ),
        ],
    )
    def test_self_service_requires_category(
        self, custom_apps_resource, config, live_app_upload, overrides, outcome, error_key
    ):
        if outcome == "reject":
            self._run_create("self_service", overrides, outcome, error_key, custom_apps_resource, live_app_upload)
            return

        # Accept inlined to verify persisted values.
        category_id = self._get_category_id(config)
        file_key = live_app_upload("probe_self_service_accept.pkg")
        payload = _base_create_payload(
            file_key=file_key,
            name="probe_self_service_accept",
            **overrides,
            self_service_category_id=category_id,
        )
        response = _request_with_retry(custom_apps_resource.client, "POST", "/api/v1/library/custom-apps", json=payload)
        created = response.json()
        try:
            assert created["show_in_self_service"] is True
            assert created["self_service_category_id"] == category_id
            assert created["self_service_recommended"] is True
        finally:
            custom_apps_resource.delete(created["id"])

    @pytest.mark.parametrize(
        ("overrides", "outcome", "error_key"),
        [
            pytest.param(
                {"self_service_recommended": True},
                "rejected_or_stripped",
                "self_service_recommended",
                id="without_self_service",
            ),
            pytest.param(
                {"show_in_self_service": True, "self_service_recommended": True},
                "accept",
                None,
                id="with_self_service",
            ),
        ],
    )
    def test_recommended_requires_self_service(
        self, custom_apps_resource, config, live_app_upload, overrides, outcome, error_key
    ):
        if outcome == "rejected_or_stripped":
            # Server either 400s with the right key, or accepts and strips the field.
            file_key = live_app_upload("probe_recommended_reject.pkg")
            payload = _base_create_payload(file_key=file_key, name="probe_recommended_reject", **overrides)
            response = self._request_no_raise(
                custom_apps_resource.client, "POST", "/api/v1/library/custom-apps", json=payload
            )
            if response.status_code == 400:
                body = response.json()
                assert error_key in body or "show_in_self_service" in body
                return
            assert response.status_code in (200, 201)
            created = response.json()
            try:
                assert created.get("self_service_recommended") in (None, False), (
                    "server accepted self_service_recommended=True with show_in_self_service=False; "
                    "client's drop logic is no longer purely defensive"
                )
            finally:
                custom_apps_resource.delete(created["id"])
            return

        # Accept: recommended=True persists when self_service is properly enabled.
        category_id = self._get_category_id(config)
        file_key = live_app_upload("probe_recommended_accept.pkg")
        payload = _base_create_payload(
            file_key=file_key,
            name="probe_recommended_accept",
            **overrides,
            self_service_category_id=category_id,
        )
        response = _request_with_retry(custom_apps_resource.client, "POST", "/api/v1/library/custom-apps", json=payload)
        created = response.json()
        try:
            assert created["self_service_recommended"] is True
        finally:
            custom_apps_resource.delete(created["id"])
