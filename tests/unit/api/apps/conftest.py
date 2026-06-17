import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path
from time import sleep
from urllib.parse import urljoin

import pytest
import requests

from iructl.api import ApiConfig, CustomAppsResource, SelfServiceCategoriesResource


def upload_app(config: ApiConfig, app_name: str) -> tuple[str, dict[str, str], str]:
    """Get upload information for a dummy app in an Iru tenant."""
    headers = {"Authorization": f"Bearer {config.api_token}"}
    resource_url = urljoin(config.url, "api/v1/library/custom-apps/upload")
    response = requests.post(
        resource_url,
        headers=headers,
        data={"name": app_name},
    )
    response.raise_for_status()
    file_key = response.json()["file_key"]
    post_data = response.json()["post_data"]
    post_url = response.json()["post_url"]
    return file_key, post_data, post_url


def upload_app_to_s3(post_url: str, post_data: dict[str, str], app_name: str, tmp_path: Path) -> bool:
    """Upload a dummy app to S3."""
    app_file = tmp_path / app_name
    app_file.write_text("dummy app data")
    with app_file.open("rb") as fh:
        response = requests.post(
            url=post_url,
            data=post_data,
            files=[("file", (app_name, fh))],
        )
    response.raise_for_status()
    return True


def create_app(
    config: ApiConfig,
    file_key: str,
    *,
    show_in_self_service: bool = False,
    self_service_category_id: str | None = None,
) -> str:
    """Create a single app in an Iru tenant and return the app ID.

    When ``show_in_self_service=True``, ``self_service_category_id`` must be provided
    or the API will 400 (matches the validation in ``CustomAppsResource.create``/``update``).
    """
    resource_url = urljoin(config.url, "api/v1/library/custom-apps")
    headers = {"Authorization": f"Bearer {config.api_token}"}
    payload = {
        "name": "Test App",
        "file_key": file_key,
        "install_type": "zip",
        "install_enforcement": "continuously_enforce",
        "audit_script": "#!/bin/bash\necho 'Audit script'",
        "preinstall_script": "#!/bin/bash\necho 'Pre-install script'",
        "postinstall_script": "#!/bin/bash\necho 'Post-install script'",
        "restart": False,
        "active": True,
        "show_in_self_service": show_in_self_service,
        "unzip_location": "/var/tmp",
    }
    if self_service_category_id is not None:
        payload["self_service_category_id"] = self_service_category_id
    max_attempts = 5
    for attempt in range(max_attempts):
        sleep(5)  # need to wait for the s3 upload to process
        response = requests.post(resource_url, headers=headers, json=payload)
        if response.status_code == 201:
            response.raise_for_status()
            return response.json()["id"]

        print(f"Attempt {attempt + 1}/{max_attempts}: Status code {response.status_code}")
        if attempt == max_attempts - 1:  # Last attempt
            response.raise_for_status()

    # This should never be reached due to raise_for_status(), but satisfies type checker
    raise RuntimeError("Failed to create app after all attempts")


def delete_app_factory(config: ApiConfig, app_id: str) -> Callable:
    """Return a function which deletes a specific app in an Iru tenant.

    The function can be used directly or passed to addfinallizer() in order to setup
    cleanup steps after the test.
    """

    def delete_app():
        resource_url = urljoin(config.url, f"api/v1/library/custom-apps/{app_id}")
        headers = {"Authorization": f"Bearer {config.api_token}"}
        response = requests.delete(resource_url, headers=headers)
        if not response.ok and response.status_code != 404:
            response.raise_for_status()

    return delete_app


def create_without_upload_and_delete_factory(config: ApiConfig, file_key: str) -> Callable:
    """Return a function which creates an app without uploading it and deletes it after the test."""

    def create_and_delete():
        app_id = create_app(config, file_key)
        delete_app_factory(config, app_id)()
        return app_id

    return create_and_delete


@pytest.fixture
def setup_live_apps_create_only(config: ApiConfig, tmp_path: Path) -> Iterator[str]:
    """Create an app in an Iru tenant; teardown attempts delete unconditionally.

    The teardown delete is intentional even though the canonical caller
    (test_delete_successful_live) deletes the app itself: delete_app_factory
    swallows 404, so the teardown is a no-op on success and a safety net if
    the test fails before deletion.
    """
    file_key, post_data, post_url = upload_app(config, "test_app.pkg")
    upload_app_to_s3(post_url, post_data, "test_app.pkg", tmp_path)
    app_id = create_app(config, file_key)
    yield app_id
    delete_app_factory(config, app_id)()


@pytest.fixture
def setup_live_apps_create_and_delete(config: ApiConfig, tmp_path: Path) -> Iterator[str]:
    """Create an app in an Iru tenant and delete it after the test."""
    file_key, post_data, post_url = upload_app(config, "test_app.pkg")
    upload_app_to_s3(post_url, post_data, "test_app.pkg", tmp_path)
    app_id = create_app(config, file_key)
    yield app_id
    delete_app_factory(config, app_id)()


@pytest.fixture
def setup_live_apps_create_and_delete_with_self_service(config: ApiConfig, tmp_path: Path) -> Iterator[str]:
    """Create an app with show_in_self_service=True and delete after the test.

    Skips if the tenant has no self-service categories — there is no way to satisfy
    show_in_self_service=True without a category id.
    """
    with SelfServiceCategoriesResource(config) as categories:
        category_list = categories.list()
    if not category_list:
        pytest.skip("tenant has no self-service categories; cannot exercise show_in_self_service=True")

    file_key, post_data, post_url = upload_app(config, "test_app.pkg")
    upload_app_to_s3(post_url, post_data, "test_app.pkg", tmp_path)
    app_id = create_app(
        config,
        file_key,
        show_in_self_service=True,
        self_service_category_id=category_list[0].id,
    )
    yield app_id
    delete_app_factory(config, app_id)()


@pytest.fixture
def live_app_upload(config: ApiConfig, tmp_path: Path) -> Callable[[str], str]:
    """Return a callable that uploads `file_name` (written into the fixture's tmp_path) to S3 and returns the file_key."""

    def _upload(file_name: str) -> str:
        file_key, post_data, post_url = upload_app(config, file_name)
        upload_app_to_s3(post_url, post_data, file_name, tmp_path)
        return file_key

    return _upload


@pytest.fixture
def custom_apps_resource(config: ApiConfig) -> Iterator[CustomAppsResource]:
    """Return an open CustomAppsResource object."""
    with CustomAppsResource(config) as apps:
        yield apps


@pytest.fixture
def register_app_cleanup(config: ApiConfig, request) -> Callable[[str], None]:
    """Return a callable that schedules deletion of an app id at test teardown (swallows 404)."""

    def _register(app_id: str) -> None:
        request.addfinalizer(delete_app_factory(config, app_id))

    return _register


@pytest.fixture
def valid_app_json() -> Callable[..., dict]:
    """Factory for a valid CustomAppPayload-shaped JSON dict for mocking create/update responses."""

    def _make(**overrides) -> dict:
        data = {
            "id": "test-app-id-12345",
            "name": "Custom Apps Test",
            "file_key": "companies/companies/d934a231-e183-4951-b0a0-763e20572c1d/library/custom_apps/test_18cf0dfc.pkg",
            "install_type": "package",
            "install_enforcement": "install_once",
            "audit_script": "",
            "unzip_location": "",
            "active": True,
            "restart": False,
            "preinstall_script": "",
            "postinstall_script": "",
            "file_url": "(temporary download link from S3)",
            "sha256": "30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58",  # pragma: allowlist secret
            "file_size": 1048576,
            "file_updated": "2023-10-05T21:25:19Z",
            "created_at": "2023-10-13T17:25:45.868709Z",
            "updated_at": "2023-10-13T17:25:45.868789Z",
            "show_in_self_service": True,
            "self_service_category_id": "e6f6d5b4-0659-4b37-872c-5471115d453b",
            "self_service_recommended": True,
        }
        data.update(overrides)
        return data

    return _make


class _RecordingHandle:
    """A TransferHandle that records when pulse fires; advance is a no-op."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def advance(self, advanced: int) -> None:
        del advanced

    def pulse(self, description: str) -> None:
        del description
        self._events.append("pulse")


class RecordingReporter:
    """A StreamReporter that records stream-open / pulse ordering relative to the upload + metadata call."""

    def __init__(self) -> None:
        self.events: list[str] = []

    @contextlib.contextmanager
    def stream(self, description: str, total: int | None):
        del description, total
        self.events.append("stream")
        yield _RecordingHandle(self.events)


@pytest.fixture
def recording_reporter() -> RecordingReporter:
    """A StreamReporter whose `events` list captures the stream/pulse ordering for transfer assertions."""
    return RecordingReporter()
