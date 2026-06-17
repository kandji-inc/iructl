import hashlib
import io
import json
import logging
import os
import re
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Self
from urllib.parse import urljoin, urlparse

import requests
from pydantic import BaseModel, ConfigDict, Field, field_validator

from iructl._console import OutputConsole
from iructl._constants import SOURCE
from iructl.exceptions import ApiClientError, PayloadIntegrityError, PayloadTransferError

console = OutputConsole(logging.getLogger(__name__))

_CHUNK_SIZE = 1024 * 1024
_REQUEST_TIMEOUT = (10, 300)  # (connect timeout, read timeout)


class ApiConfig(BaseModel):
    """A Container for API configuration values.

    Attributes:
        url (str): API base URL for the tenant (Kandji: https://<subdomain>.api.kandji.io or .api.eu.kandji.io;
            Iru: https://<subdomain>.api.iru.com). Must use the https:// schema.
        api_token (str): API authentication token for the tenant.

    """

    model_config = ConfigDict(frozen=True)

    url: str = Field(alias="tenant_url")
    api_token: str = Field(repr=False)

    def __hash__(self) -> int:
        return hash(tuple(self.__dict__.items()))

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, v) -> str:
        """Ensure the url is using https and matches a supported Kandji or Iru API host."""
        # Ensure the URL is a string with a https schema

        if isinstance(v, str) and (parsed_url := urlparse(v)).scheme in ("", "http"):
            if parsed_url.netloc == "":
                # handle misclassified netloc: https://docs.python.org/3/library/urllib.parse.html#urllib.parse.urlparse
                v = parsed_url._replace(scheme="https", netloc=parsed_url.path, path="").geturl()
            else:
                v = parsed_url._replace(scheme="https").geturl()

        v = v.rstrip("/")  # Normalize without trailing slash

        if not re.fullmatch(r"https://[A-Za-z0-9-]+\.api((\.eu)?\.kandji\.io|\.iru\.com)", v):
            raise ValueError(
                "The Tenant URL must be a valid Kandji or Iru API URL. "
                "Use https://<tenant>.api.kandji.io, https://<tenant>.api.eu.kandji.io, "
                "or https://<tenant>.api.iru.com."
            )
        return v

    @field_validator("api_token", mode="after")
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Ensure the api_token is a uuid4 string."""
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", v):
            raise ValueError(
                "The API token must be a valid UUID4 string. "
                "Please ensure the token is in the format 12345678-1234-5678-1234-123456789012."
            )
        return v


class HttpClient:
    """A requests.Session wrapper with consistent request/response logging and no authentication.

    The transport for any HTTP the app makes.

    Methods:
        request: Make a generic (buffered) HTTP request
        close: Close the internal session object.
    """

    def __init__(self) -> None:
        self._session = requests.Session()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @property
    def session(self) -> requests.Session:
        """Get the session object for the client.

        Returns:
            requests.Session: The internal session object

        Raises:
            ApiClientError: Raised when the session is not open.

        """

        if self._session is None:
            raise ApiClientError("No open session available.")

        return self._session

    def close(self) -> None:
        """Close the internal session object."""
        if self._session is not None:
            self.session.close()
            self._session = None

    def request(
        self,
        method: str,
        url: str,
        *args,
        extra_params: dict[str, str] | None = None,
        anticipated_error: Callable[[requests.Response], bool] | None = None,
        **kwargs,
    ) -> requests.Response:
        """Make a generic HTTP request, logging the request and (buffered) response.

        Args:
            anticipated_error (Callable[[requests.Response], bool] | None): A predicate
                the caller classifies itself (e.g. a duplicate-assignment 400). When it
                returns True for the error response, the HTTPError is logged at debug
                rather than error. The error is raised either way.

        Returns:
            requests.Response: The response object from the request

        Raises:
            requests.ConnectionError: Raised when the API connection fails
            requests.HTTPError: Raised when the HTTP request returns an unsuccessful status code

        """

        try:
            console.debug(f"Making {method} request to {url}")

            if extra_params:
                kwargs["params"] = kwargs.get("params", {}) | extra_params

            response = self.session.request(method, url, *args, **kwargs)

            console.debug(f"Response status code: {response.status_code}")

            try:
                headers = "\n" + json.dumps(dict(response.headers), indent=2)
            except json.JSONDecodeError:
                headers = response.headers
            console.debug(f"Response headers: {headers}")

            try:
                content = "\n" + json.dumps(response.json(), indent=2)
            except json.JSONDecodeError:
                content = response.text
            console.debug(f"Response content: {content}")

            response.raise_for_status()
        except requests.ConnectionError as error:
            console.error(f"Connection error occurred: {error}")
            raise
        except requests.HTTPError as error:
            if anticipated_error is not None and anticipated_error(error.response):
                console.debug(f"Anticipated HTTP {error.response.status_code} response: {error.response.text}")
            else:
                console.error(f"HTTP error occurred: {error.response.status_code}")
                console.error(f"Response content: {error.response.text}")
            raise

        return response


class ApiClient(HttpClient):
    """An HttpClient that authenticates against the Iru API and resolves resource paths.

    Adds the bearer token and Accept header to every request, tags each with the source query
    parameter, and converts relative resource paths to fully qualified tenant URLs.

    Methods:
        get: Make a GET HTTP request
        patch: Make a PATCH HTTP request
        post: Make a POST HTTP request
        delete: Make a DELETE HTTP request
    """

    def __init__(self, config: ApiConfig) -> None:
        super().__init__()
        self._config = config
        self._update_header()

    def _update_header(self):
        """Update the session headers with the API token."""
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self._config.api_token}",
                "Accept": "application/json",
            }
        )

    def _make_url(self, path: str):
        """Convert a relative path to a fully qualified URL."""
        return urljoin(self._config.url, path)

    def request(
        self,
        method: str,
        url: str,
        *args,
        extra_params: dict[str, str] | None = None,
        anticipated_error: Callable[[requests.Response], bool] | None = None,
        **kwargs,
    ) -> requests.Response:
        """Make a request to the Iru API, tagging it with the source query parameter."""
        params = {"source": SOURCE, **(extra_params or {})}
        return super().request(method, url, *args, extra_params=params, anticipated_error=anticipated_error, **kwargs)

    def get(self, path: str) -> requests.Response:
        """Make a GET HTTP request to the resolved API endpoint at path."""
        return self.request("GET", self._make_url(path))

    def patch(
        self,
        path: str,
        data: dict | None = None,
        json: dict | None = None,
        files: list[tuple[str, tuple[str, io.BufferedReader, str]]] | None = None,
    ) -> requests.Response:
        """Make a PATCH HTTP request to the resolved API endpoint at path."""
        return self.request("PATCH", self._make_url(path), data=data, json=json, files=files)

    def post(
        self,
        path: str,
        data: dict | None = None,
        json: dict | None = None,
        files: list[tuple[str, tuple[str, io.BufferedReader, str]]] | None = None,
        anticipated_error: Callable[[requests.Response], bool] | None = None,
    ) -> requests.Response:
        """Make a POST HTTP request to the resolved API endpoint at path."""
        return self.request(
            "POST", self._make_url(path), data=data, json=json, files=files, anticipated_error=anticipated_error
        )

    def delete(self, path: str) -> requests.Response:
        """Make a DELETE HTTP request to the resolved API endpoint at path."""
        return self.request("DELETE", self._make_url(path))


class StreamingS3Upload:
    """A read()-able multipart/form-data body for an S3 presigned POST.

    Serves the form fields, then the file in chunks (calling on_progress as each is read),
    then the closing boundary. read() lets requests stream it via blocksize reads so progress
    fires during the send; __len__ and content_type supply the exact Content-Length S3 needs.
    """

    def __init__(
        self,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
        file_size: int,
        content_type: str,
        *,
        on_progress: Callable[[int], None] = lambda _: None,
    ) -> None:
        self._file_path = file_path
        self._file_size = file_size
        self._on_progress = on_progress
        self.boundary = secrets.token_hex(16)
        self._prefix = self._build_prefix(fields, file_field, file_path.name, content_type)
        self._suffix = f"\r\n--{self.boundary}--\r\n".encode()
        self._chunks = self._iter_chunks()
        self._buffer = b""

    def _build_prefix(self, fields: dict[str, str], file_field: str, file_name: str, content_type: str) -> bytes:
        """Build the body bytes that precede the file content: the form fields then the file header."""
        parts: list[str] = []
        for name, value in fields.items():
            parts.append(f"--{self.boundary}\r\n")
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
            parts.append(f"{value}\r\n")
        parts.append(f"--{self.boundary}\r\n")
        parts.append(f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n')
        parts.append(f"Content-Type: {content_type}\r\n\r\n")
        return "".join(parts).encode()

    @property
    def content_type(self) -> str:
        """The multipart Content-Type header value, including the generated boundary."""
        return f"multipart/form-data; boundary={self.boundary}"

    def __len__(self) -> int:
        """The exact body length, so requests can set Content-Length (S3 needs a known length)."""
        return len(self._prefix) + self._file_size + len(self._suffix)

    def _iter_chunks(self) -> Iterator[bytes]:
        """Yield the body in order: prefix, then the file in chunks, then the closing boundary."""
        yield self._prefix
        with self._file_path.open("rb") as binary:
            for chunk in iter(lambda: binary.read(_CHUNK_SIZE), b""):
                self._on_progress(len(chunk))
                yield chunk
        yield self._suffix

    def read(self, amt: int | None = -1) -> bytes:
        """Return up to amt bytes of the body, pulling more from the chunk stream as needed."""
        if amt is None or amt < 0:
            data = self._buffer + b"".join(self._chunks)
            self._buffer = b""
            return data
        while len(self._buffer) < amt:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                break
        data, self._buffer = self._buffer[:amt], self._buffer[amt:]
        return data

    def __iter__(self) -> Iterator[bytes]:
        # Fallback for transports that iterate instead of calling read(); serves buffered bytes first.
        if self._buffer:
            yield self._buffer
            self._buffer = b""
        yield from self._chunks


class S3Client(HttpClient):
    """An unauthenticated HttpClient for streaming installer binaries to and from S3 presigned URLs.

    A bare transport (no Iru auth headers, none to clear) that reuses the client's request/response
    logging. Presigned URLs carry their own credentials in the URL or POST policy.
    """

    def upload_file(
        self,
        url: str,
        fields: dict[str, str],
        file_path: Path,
        *,
        file_size: int,
        on_progress: Callable[[int], None] = lambda _: None,
    ) -> requests.Response:
        """Stream file_path to the S3 presigned POST at url, reporting each chunk's bytes via on_progress.

        The body is a lazily-generated multipart payload, so requests streams it from disk rather
        than buffering it in memory.
        """
        body = StreamingS3Upload(
            fields, "file", file_path, file_size, "application/octet-stream", on_progress=on_progress
        )
        return self.request(
            "POST", url, data=body, headers={"Content-Type": body.content_type}, timeout=_REQUEST_TIMEOUT
        )

    def download_file(
        self,
        url: str,
        dest: Path,
        *,
        expected_sha: str,
        file_size: int | None = None,
        on_progress: Callable[[int], None] = lambda _: None,
    ) -> None:
        """Stream url to dest, verifying sha256 before an atomic rename.

        on_progress is called with each chunk's byte count as the download streams; file_size is
        accepted for API symmetry and is otherwise unused here.

        Raises:
            PayloadTransferError: The download request failed or the bytes failed sha256
                verification (PayloadIntegrityError, a subclass, for the latter).
        """
        del file_size  # the bar's total is owned by the caller via on_progress
        digest = hashlib.sha256()
        tmp_path = dest.with_name(f".{dest.name}.{secrets.token_hex(8)}.part")
        try:
            console.debug(f"Downloading {url} to {dest}")
            with self.session.get(url, stream=True, timeout=_REQUEST_TIMEOUT) as response:
                console.debug(f"Response status code: {response.status_code}")
                response.raise_for_status()
                with tmp_path.open("wb") as tmp_file:
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        tmp_file.write(chunk)
                        digest.update(chunk)
                        on_progress(len(chunk))

            if digest.hexdigest() != expected_sha:
                raise PayloadIntegrityError(f"Downloaded file failed sha256 verification (expected {expected_sha}).")
            os.replace(tmp_path, dest)
        except requests.RequestException as error:
            tmp_path.unlink(missing_ok=True)
            raise PayloadTransferError(f"Failed to download file from S3: {error}") from error
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
