import hashlib
import logging
import threading
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Self
from urllib.parse import urlparse

import pytest
import requests
from pydantic import ValidationError

from iructl._constants import APP_NAME, SOURCE, _detect_source
from iructl.api import ApiClient, ApiConfig, S3Client
from iructl.api.client import StreamingS3Upload
from iructl.exceptions import ApiClientError, PayloadIntegrityError, PayloadTransferError


@pytest.fixture
def fake_client(config) -> Generator[ApiClient]:
    client = ApiClient(config=config)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def patch_requests(monkeypatch, response_factory):
    request_calls: list[tuple[tuple, dict]] = []

    def mock_request(self, *args, **kwargs):
        request_calls.append((args, kwargs))
        return response_factory(status_code=200, content=b"")

    monkeypatch.setattr("requests.sessions.Session.request", mock_request)
    return request_calls


class TestApiConfig:
    def test_valid_config(self):
        valid_configs = [
            "https://xxxxxxxx.api.kandji.io",
            "https://xxxxxxxx.api.kandji.io/",
            "https://xxxxxxxx.api.eu.kandji.io",
            "http://xxxxxxxx.api.kandji.io",
            "http://xxxxxxxx.api.kandji.io/",
            "http://xxxxxxxx.api.eu.kandji.io",
            "xxxxxxxx.api.kandji.io",
            "xxxxxxxx.api.kandji.io/",
            "xxxxxxxx.api.eu.kandji.io",
            "https://xxxxxxxx.api.iru.com",
            "http://xxxxxxxx.api.iru.com",
            "xxxxxxxx.api.iru.com",
        ]
        for tenant_url in valid_configs:
            config = ApiConfig(tenant_url=tenant_url, api_token="00000000-0000-0000-0000-000000000000")
            assert urlparse(config.url).scheme == "https"
            assert config.api_token == "00000000-0000-0000-0000-000000000000"

    def test_invalid_url(self):
        invalid_urls = [
            "ftp://xxxxxxxx.api.kandji.io",  # Invalid scheme
            "https://xxxxxxxx.api.kandji.io:8080",  # Port numbers are not allowed
            "http://xxxxxxxx.kandji.io",  # Invalid netloc
            "https://invalid-url.com",  # Invalid netloc
            "https://xxxxxxxx.api.eu.iru.com",  # No EU subdomain on iru
            "https://xxxxxxxx.iru.com",  # Missing api. subdomain
            "https://xxxxxxxx.api.iru.net",  # Unsupported TLD
        ]
        for url in invalid_urls:
            with pytest.raises(ValidationError) as exc:
                ApiConfig(tenant_url=url, api_token="00000000-0000-0000-0000-000000000000")
            assert "The Tenant URL must be a valid Kandji or Iru API URL" in exc.value.errors()[0]["msg"]

    def test_invalid_token(self):
        invalid_tokens = [
            "",  # empty string
            "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",  # Invalid character
            "abcd",  # Too short
            "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",  # Not a UUID4
        ]
        for token in invalid_tokens:
            with pytest.raises(ValidationError) as exc:
                ApiConfig(tenant_url="xxxxxxxx.api.kandji.io", api_token=token)
            assert "The API token must be a valid UUID4 string." in exc.value.errors()[0]["msg"]


class TestApiClient:
    def test_no_session(self, fake_client):
        fake_client.close()
        with pytest.raises(ApiClientError):
            fake_client.session

    def test_source_param(self, fake_client, patch_requests):
        """Test that the source parameter is added to all requests."""
        fake_client.get("/get")
        fake_client.patch("/patch")
        fake_client.post("/post")
        fake_client.delete("/delete")

        for call in patch_requests:
            assert call[1]["params"]["source"] == SOURCE

        # Ensure that other parameters are not overwritten
        fake_client.request("GET", "https://example.com", params={"page": 3, "source": r"¯\_(ツ)_/¯"})
        assert patch_requests[-1][1]["params"] == {"page": 3, "source": SOURCE}

    def test_anticipated_error_logged_at_debug(self, fake_client, monkeypatch, response_factory, caplog):
        """An HTTPError the caller anticipates is logged at debug, not error, and still raised."""

        def mock_request(self, *args, **kwargs):
            return response_factory(status_code=400, content=b"already exists")

        monkeypatch.setattr("requests.sessions.Session.request", mock_request)
        with caplog.at_level("DEBUG", logger="iructl.api.client"):
            with pytest.raises(requests.HTTPError):
                fake_client.post("/assign", anticipated_error=lambda response: response.status_code == 400)

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("Anticipated HTTP 400" in r.message and r.levelno == logging.DEBUG for r in caplog.records)

    def test_unanticipated_error_logged_at_error(self, fake_client, monkeypatch, response_factory, caplog):
        """An HTTPError the predicate rejects is logged at error and raised."""

        def mock_request(self, *args, **kwargs):
            return response_factory(status_code=400, content=b"genuine failure")

        monkeypatch.setattr("requests.sessions.Session.request", mock_request)
        with caplog.at_level("DEBUG", logger="iructl.api.client"):
            with pytest.raises(requests.HTTPError):
                fake_client.post("/assign", anticipated_error=lambda _response: False)

        error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert "HTTP error occurred: 400" in error_messages
        assert "Response content: genuine failure" in error_messages


class TestDetectSource:
    """The source tag gains a -ci suffix whenever the CI env var is present."""

    def test_ci_set(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        assert _detect_source() == f"{APP_NAME}-ci"

    def test_ci_present_but_empty(self, monkeypatch):
        # "Present" means the key exists, regardless of value.
        monkeypatch.setenv("CI", "")
        assert _detect_source() == f"{APP_NAME}-ci"

    def test_ci_unset(self, monkeypatch):
        monkeypatch.delenv("CI", raising=False)
        assert _detect_source() == APP_NAME


def _make_upload(file_path, on_progress=lambda _: None) -> StreamingS3Upload:
    fields = {"key": "uploads/app.pkg", "policy": "base64policy", "x-amz-signature": "deadbeef"}
    return StreamingS3Upload(
        fields, "file", file_path, file_path.stat().st_size, "application/octet-stream", on_progress=on_progress
    )


def _drain_via_read(upload: StreamingS3Upload, blocksize: int = 8192) -> bytes:
    """Consume the body the way requests/http.client does: repeated read(blocksize) until empty."""
    parts = []
    while chunk := upload.read(blocksize):
        parts.append(chunk)
    return b"".join(parts)


class TestStreamingS3Upload:
    """StreamingS3Upload serves a multipart body via read(), sized for an exact Content-Length."""

    SIZES = [
        pytest.param(0, id="empty"),
        pytest.param(64, id="sub-chunk"),
        pytest.param(1024 * 1024, id="one-chunk"),
        pytest.param(1024 * 1024 + 17, id="multi-chunk"),
    ]

    @pytest.mark.parametrize("size", SIZES)
    def test_len_matches_body_read_in_blocks(self, tmp_path, size):
        file_path = tmp_path / "app.pkg"
        file_path.write_bytes(b"x" * size)

        upload = _make_upload(file_path)

        assert len(upload) == len(_drain_via_read(upload))

    @pytest.mark.parametrize("size", SIZES)
    def test_progress_totals_file_size(self, tmp_path, size):
        file_path = tmp_path / "app.pkg"
        file_path.write_bytes(b"x" * size)

        advanced: list[int] = []
        _drain_via_read(_make_upload(file_path, on_progress=advanced.append))

        assert sum(advanced) == size

    def test_progress_fires_across_multiple_reads(self, tmp_path):
        # A multi-chunk file must report progress more than once rather than all at the end --
        # this is what keeps the bar moving during the send.
        file_path = tmp_path / "app.pkg"
        file_path.write_bytes(b"x" * (3 * 1024 * 1024))

        advanced: list[int] = []
        _drain_via_read(_make_upload(file_path, on_progress=advanced.append))

        assert len(advanced) >= 2

    def test_file_part_is_emitted_last(self, tmp_path):
        file_path = tmp_path / "app.pkg"
        file_path.write_bytes(b"PKGDATA")

        body = _drain_via_read(_make_upload(file_path))

        # Every form field header precedes the file part header, which precedes the file bytes.
        assert body.index(b'name="x-amz-signature"') < body.index(b'name="file"; filename="app.pkg"')
        assert body.index(b'name="file"') < body.index(b"PKGDATA")

    def test_content_type_carries_boundary(self, tmp_path):
        file_path = tmp_path / "app.pkg"
        file_path.write_bytes(b"data")

        upload = _make_upload(file_path)

        assert upload.content_type == f"multipart/form-data; boundary={upload.boundary}"


class _FakeStreamResponse:
    """Minimal streaming requests.Response stand-in usable as a context manager."""

    def __init__(self, chunks: list[bytes], *, raise_after: Exception | None = None) -> None:
        self._chunks = chunks
        self._raise_after = raise_after
        self.status_code = 200

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int | None = None) -> Iterator[bytes]:  # noqa: ARG002
        yield from self._chunks
        if self._raise_after is not None:
            raise self._raise_after


def _transport_returning(monkeypatch, chunks: list[bytes], *, raise_after: Exception | None = None) -> S3Client:
    """An S3Client whose session streams the given chunks for any GET, optionally failing mid-stream."""
    transport = S3Client()
    response = _FakeStreamResponse(chunks, raise_after=raise_after)
    monkeypatch.setattr(transport.session, "get", lambda *_args, **_kwargs: response)
    return transport


class TestDownloadFile:
    """S3Client.download_file streams a URL to disk, verifying sha256 before the atomic rename."""

    def test_writes_when_sha_matches(self, tmp_path, monkeypatch):
        content = b"installer-bytes-0123456789"
        transport = _transport_returning(monkeypatch, [content[:5], content[5:]])

        dest = tmp_path / "app.pkg"
        transport.download_file("https://example.com/file", dest, expected_sha=hashlib.sha256(content).hexdigest())

        assert dest.read_bytes() == content

    def test_on_progress_deltas_sum_to_content_length(self, tmp_path, monkeypatch):
        chunks = [b"abc", b"defgh", b"ij"]
        content = b"".join(chunks)
        transport = _transport_returning(monkeypatch, chunks)

        advanced: list[int] = []
        transport.download_file(
            "https://example.com/file",
            tmp_path / "app.pkg",
            expected_sha=hashlib.sha256(content).hexdigest(),
            on_progress=advanced.append,
        )

        assert sum(advanced) == len(content)
        assert advanced == [len(chunk) for chunk in chunks]

    def test_rejects_and_cleans_up_on_sha_mismatch(self, tmp_path, monkeypatch):
        transport = _transport_returning(monkeypatch, [b"unexpected"])

        dest = tmp_path / "app.pkg"
        with pytest.raises(PayloadIntegrityError, match="sha256"):
            transport.download_file("https://example.com/file", dest, expected_sha="a" * 64)

        assert not dest.exists()
        assert not list(tmp_path.glob(".app.pkg*.part"))  # temp file removed

    def test_wraps_transport_error_and_cleans_up(self, tmp_path, monkeypatch):
        transport = _transport_returning(
            monkeypatch, [b"partial"], raise_after=requests.ConnectionError("connection reset")
        )

        dest = tmp_path / "app.pkg"
        with pytest.raises(PayloadTransferError, match="Failed to download file from S3"):
            transport.download_file("https://example.com/file", dest, expected_sha="a" * 64)

        assert not dest.exists()
        assert not list(tmp_path.glob(".app.pkg*.part"))  # partial temp file removed

    def test_concurrent_downloads_to_same_dest_do_not_race(self, tmp_path, monkeypatch):
        """Two installers that resolve to one local filename download at once without clobbering each other.

        Regression for the temp-file race: a temp path shared by both downloads let the first thread's
        rename pull the file out from under the second, which then failed its own rename with ENOENT.
        """
        content = b"shared-installer-bytes"
        expected_sha = hashlib.sha256(content).hexdigest()
        dest = tmp_path / "app.pkg"

        # Hold both downloads at the end of their write phase, then release together so they reach the
        # rename step concurrently -- the exact interleaving the bug needed to surface.
        at_rename = threading.Barrier(2, timeout=5)

        class _BarrierStream(_FakeStreamResponse):
            def iter_content(self, chunk_size: int | None = None) -> Iterator[bytes]:
                yield from super().iter_content(chunk_size)
                at_rename.wait()

        # Build (and stub) both transports up front on this thread; the worker threads only download.
        transports = []
        for _ in range(2):
            transport = S3Client()
            monkeypatch.setattr(transport.session, "get", lambda *_args, **_kwargs: _BarrierStream([content]))
            transports.append(transport)

        def download_once(transport: S3Client) -> None:
            transport.download_file("https://example.com/file", dest, expected_sha=expected_sha)

        with ThreadPoolExecutor(max_workers=2) as executor:
            for future in [executor.submit(download_once, transport) for transport in transports]:
                future.result()  # re-raises ENOENT (or any failure) from the worker thread

        assert dest.read_bytes() == content
        assert not list(tmp_path.glob(".app.pkg*.part"))  # no temp files left behind
