import logging
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from time import sleep

import requests

from iructl._console import OutputConsole
from iructl._progress import NULL_REPORTER, StreamReporter
from iructl.api.client import S3Client
from iructl.exceptions import PayloadTransferError

from .payload import CustomAppPayload, CustomAppUploadPayload, PayloadList
from .resource_base import ResourceBase

console = OutputConsole(logging.getLogger(__name__))

# A large installer can return 503 while finalizing server-side
_RETRY_BACKOFF_CAP = 30  # max seconds per backoff
_RETRY_MAX_WAIT = 300  # total retry budget in seconds (~5 min)

SHOW_IN_SELF_SERVICE_EXAMPLE = """Example:
  "show_in_self_service": true
  "self_service_category_id": "ae492437-c35f-46a3-bd0b-21188a69dfb1"
"""


def _resolve_installer(file: Path) -> tuple[Path, int]:
    """Resolve an installer file argument to its on-disk path and size."""
    if not isinstance(file, Path):
        raise ValueError("Invalid file type provided. Must be a Path object.")
    if not file.is_file():
        raise FileNotFoundError(f"The file {file} does not exist or is not readable")
    return file, file.stat().st_size


class InstallType(StrEnum):
    """An enumeration of possible installation types for a custom app."""

    PACKAGE = "package"
    ZIP = "zip"
    IMAGE = "image"


class InstallEnforcement(StrEnum):
    """An enumeration of possible installation enforcement types for a custom app."""

    INSTALL_ONCE = "install_once"
    CONTINUOUSLY_ENFORCE = "continuously_enforce"
    NO_ENFORCEMENT = "no_enforcement"


class CustomAppsResource(ResourceBase):
    """An API client wrapper for interacting with the Custom Apps endpoint.

    Attributes:
        client (ApiClient): An ApiClient object with an open Session

    Methods:
        list: Retrieve a list of all custom apps
        get: Retrieve a single custom app by id
        create: Create a new custom app
        update: Update an existing custom app by id
        delete: Delete an existing custom app by id

    """

    _path = "/api/v1/library/custom-apps"

    def _upload_file(
        self,
        file_path: Path,
        file_size: int,
        *,
        name: str | None = None,
        on_progress: Callable[[int], None] = lambda _: None,
    ) -> str:
        """Upload a file to S3, reporting each chunk's bytes via on_progress; returns the S3 file key.

        name overrides the filename registered with the endpoint (defaults to file_path.name).

        Raises:
            FileNotFoundError: Raised when the file does not exist or is not readable
            ValueError: Raised when invalid file type is provided
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the presign request returns an unsuccessful status code
            PayloadTransferError: Raised when the S3 upload fails or is rejected
            ValidationError: Raised when the response does not match the expected schema

        """
        # Get upload details from API
        payload = {"name": name or file_path.name}
        response = self.client.post(f"{self._path}/upload", data=payload)
        upload_response = CustomAppUploadPayload.model_validate_json(response.content)

        try:
            with S3Client() as transport:
                response = transport.upload_file(
                    upload_response.post_url,
                    upload_response.post_data,
                    file_path,
                    file_size=file_size,
                    on_progress=on_progress,
                )
        except requests.RequestException as error:
            raise PayloadTransferError(f"Failed to upload file to S3: {error}") from error
        if response.status_code != 204:
            raise PayloadTransferError(f"Failed to upload file to S3: {response.text}")

        return upload_response.file_key

    def _send_with_retry(self, send: Callable[[], requests.Response], *, action: str) -> CustomAppPayload:
        """Send a create/update request, retrying transient 503s while Iru finalizes the upload."""
        attempt = 0
        waited = 0.0
        while True:
            try:
                response = send()
                return CustomAppPayload.model_validate_json(response.content)
            except requests.HTTPError as e:
                if e.response.status_code == 503 and waited < _RETRY_MAX_WAIT:
                    delay = min(2**attempt, _RETRY_BACKOFF_CAP)
                    console.info(
                        f"Upload still being processed (503), retrying in {delay}s "
                        f"(waited {int(waited)}s/{_RETRY_MAX_WAIT}s)..."
                    )
                    console.debug(f"503 response body: {e.response.text}")
                    sleep(delay)
                    waited += delay
                    attempt += 1
                    continue
                console.error(f"Failed to {action} custom app: {e.response.text}")
                raise

    def list(self) -> PayloadList[CustomAppPayload]:
        """Retrieve a list of all custom apps.

        Returns:
            PayloadList: An object containing all combined results

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        all_results = PayloadList[CustomAppPayload]()
        next_page = self._path
        while next_page:
            response = self.client.get(next_page)

            # Parse bytes content to CustomAppPayloadList or raise ValidationError
            app_list = PayloadList[CustomAppPayload].model_validate_json(response.content)

            all_results.count = app_list.count
            all_results.results.extend(app_list.results)

            next_page = app_list.next

        return all_results

    def get(self, id: str) -> CustomAppPayload:
        """Retrieve details about a custom app.

        Args:
            id (str): The library item id of the app to retrieve

        Returns:
            CustomAppPayload: A parsed object from the response

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """
        response = self.client.get(f"{self._path}/{id}")
        return CustomAppPayload.model_validate_json(response.content)

    def create(
        self,
        name: str,
        file: Path,
        install_type: InstallType,
        install_enforcement: InstallEnforcement,
        audit_script: str,
        preinstall_script: str,
        postinstall_script: str,
        restart: bool,
        active: bool,
        show_in_self_service: bool | None = False,
        self_service_category_id: str | None = None,
        self_service_recommended: bool | None = None,
        unzip_location: str | None = None,
        file_name: str | None = None,
        reporter: StreamReporter = NULL_REPORTER,
    ) -> CustomAppPayload:
        """Create a new custom app in Iru.

        Args:
            name (str): The name for the new app
            file (Path): The app file to upload
            install_type (InstallType): The installation type
            install_enforcement (InstallEnforcement): The enforcement type for installation
            audit_script (str): Script to audit app installation (only with 'continuously_enforce')
            preinstall_script (str): Script to run before installation
            postinstall_script (str): Script to run after installation
            restart (bool): Whether to restart after installation
            active (bool): Whether the app is active
            show_in_self_service (bool, optional): Whether to show in self service
            self_service_category_id (str, optional): Category ID for self service
            self_service_recommended (bool, optional): Whether recommended in self service
            unzip_location (str, optional): Location to unzip (required for 'zip' install_type)
            file_name (str, optional): Override the uploaded filename
            reporter (StreamReporter, optional): Progress reporter for the upload stream

        Returns:
            CustomAppPayload: A parsed object from the response

        Raises:
            ValueError: Raised when invalid parameters are passed
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """
        if audit_script and install_enforcement != InstallEnforcement.CONTINUOUSLY_ENFORCE:
            raise ValueError("audit_script can only be used with install_enforcement 'continuously_enforce'")
        if install_type == InstallType.ZIP and unzip_location is None:
            raise ValueError("unzip_location must be provided when install_type is 'zip'")

        if install_enforcement == InstallEnforcement.NO_ENFORCEMENT and not show_in_self_service:
            raise ValueError(
                '"show_in_self_service" and "self_service_category_id" are required if install_enforcement is '
                f"NO_ENFORCEMENT. You can add the required keys to your app's info file.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
            )

        if show_in_self_service:
            if self_service_category_id is None:
                raise ValueError(
                    f"self_service_category_id is required if show_in_self_service is True.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
                )

        # Pulse the bar through the slow server-side create so it keeps reading as active.
        file_path, file_size = _resolve_installer(file)
        with reporter.stream(f"Uploading {file_path.name}", file_size) as transfer:
            file_key = self._upload_file(file_path, file_size, name=file_name, on_progress=transfer.advance)
            transfer.pulse(f"Finalizing {file_path.name}")

            payload = {
                "name": name,
                "file_key": file_key,
                "install_type": str(install_type),
                "install_enforcement": str(install_enforcement),
                "audit_script": audit_script,
                "preinstall_script": preinstall_script,
                "postinstall_script": postinstall_script,
                "restart": restart,
                "active": active,
                "show_in_self_service": show_in_self_service,
                "self_service_category_id": self_service_category_id,
                "self_service_recommended": self_service_recommended,
                "unzip_location": unzip_location,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            return self._send_with_retry(lambda: self.client.post(self._path, data=payload), action="create")

    def update(
        self,
        id: str,
        name: str | None = None,
        file: Path | None = None,
        install_type: InstallType | None = None,
        install_enforcement: InstallEnforcement | None = None,
        audit_script: str | None = None,
        preinstall_script: str | None = None,
        postinstall_script: str | None = None,
        restart: bool | None = None,
        active: bool | None = None,
        show_in_self_service: bool | None = None,
        self_service_category_id: str | None = None,
        self_service_recommended: bool = False,
        unzip_location: str | None = None,
        file_name: str | None = None,
        reporter: StreamReporter = NULL_REPORTER,
    ) -> CustomAppPayload:
        """Update an existing custom app in Iru.

        Args:
            id (str): The library item id of the app to update
            name (str, optional): The name for the app
            file_key (str, optional): The S3 file key for the uploaded app file
            install_type (InstallType, optional): The installation type
            install_enforcement (InstallEnforcement, optional): The enforcement type for installation
            audit_script (str, optional): Script to audit app installation (only with 'continuously_enforce')
            preinstall_script (str, optional): Script to run before installation
            postinstall_script (str, optional): Script to run after installation
            restart (bool, optional): Whether to restart after installation
            active (bool, optional): Whether the app is active
            show_in_self_service (bool, optional): Whether to show in self service
            self_service_category_id (str, optional): Category ID for self service
            self_service_recommended (bool, optional): Whether recommended in self service
            unzip_location (str, optional): Location to unzip (required for 'zip' install_type)
            file_name (str, optional): Override the uploaded filename
            reporter (StreamReporter, optional): Progress reporter for the upload stream

        Returns:
            CustomAppPayload: A parsed object from the response

        Raises:
            ValueError: Raised when invalid parameters are passed
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        if audit_script and install_enforcement != InstallEnforcement.CONTINUOUSLY_ENFORCE:
            raise ValueError("audit_script can only be used with install_enforcement 'continuously_enforce'")
        if install_type == InstallType.ZIP and unzip_location is None:
            raise ValueError("unzip_location must be provided when install_type is 'zip'")

        if install_enforcement == InstallEnforcement.NO_ENFORCEMENT and not show_in_self_service:
            raise ValueError(
                '"show_in_self_service" and "self_service_category_id" are required if install_enforcement is '
                f"NO_ENFORCEMENT. You can add the required keys to your app's info file.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
            )

        if show_in_self_service:
            if self_service_category_id is None:
                raise ValueError(
                    f"self_service_category_id is required if show_in_self_service is True.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
                )

        payload = {
            "name": name,
            "install_type": None if install_type is None else str(install_type),
            "install_enforcement": None if install_enforcement is None else str(install_enforcement),
            "audit_script": audit_script,
            "preinstall_script": preinstall_script,
            "postinstall_script": postinstall_script,
            "restart": restart,
            "active": active,
            "show_in_self_service": show_in_self_service,
            "unzip_location": unzip_location,
        }
        if show_in_self_service:
            payload["self_service_category_id"] = self_service_category_id
            payload["self_service_recommended"] = self_service_recommended

        def patch() -> CustomAppPayload:
            cleaned = {k: v for k, v in payload.items() if v is not None}
            return self._send_with_retry(lambda: self.client.patch(f"{self._path}/{id}", data=cleaned), action="update")

        if file:
            # Pulse the bar through the slow server-side update so it keeps reading as active.
            file_path, file_size = _resolve_installer(file)
            with reporter.stream(f"Uploading {file_path.name}", file_size) as transfer:
                payload["file_key"] = self._upload_file(
                    file_path, file_size, name=file_name, on_progress=transfer.advance
                )
                transfer.pulse(f"Finalizing {file_path.name}")
                return patch()
        return patch()

    def delete(self, id: str) -> None:
        """Delete an existing custom app in Iru.

        Args:
            id (str): The library item id of the app to delete

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails

        """

        self.client.delete(f"{self._path}/{id}")
