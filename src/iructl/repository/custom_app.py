from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Self, override

from pydantic import PrivateAttr, model_validator
from pygments.lexers import guess_lexer
from rich.console import RenderableType
from rich.syntax import Syntax

from iructl._constants import APPS_DIR
from iructl._progress import NULL_REPORTER, StreamReporter
from iructl._utils import sha256_file
from iructl.api import (
    ApiConfig,
    CustomAppPayload,
    CustomAppsResource,
    InstallEnforcement,
    InstallType,
    S3Client,
)
from iructl.exceptions import (
    DuplicateAppError,
    InvalidAppError,
    MissingAppInstallerError,
)

from .content import Script
from .info import AppInfoFile
from .member_base import ContentChild, MemberBase, MemberConfig, PushOutcome, child_path
from .self_service import self_service_payload

# The optional script children of a custom app, named after their attribute on disk.
SCRIPT_ATTRIBUTES = ("audit", "preinstall", "postinstall")


class DownloadResult(StrEnum):
    """Outcome of ensuring an installer binary is present in the payload directory."""

    DOWNLOADED = "downloaded"
    UP_TO_DATE = "up_to_date"
    MISMATCH_SKIPPED = "mismatch_skipped"
    MIGRATED = "migrated"


class CustomApp(MemberBase[AppInfoFile, CustomAppPayload]):
    """A data model for representing a custom app.

    The installer binary is not a child of the member. Only a reference to it
    (file.name and file.sha256) is stored in the info file; the binary lives outside the
    member directory and is uploaded/downloaded by the CLI.
    """

    directory_name: ClassVar[str] = APPS_DIR

    _config: ClassVar = MemberConfig(
        member_name="custom app",
        resource_cls=CustomAppsResource,
        content_specs=tuple(
            ContentChild(
                attribute=attr,
                payload_field=f"{attr}_script",
                content_cls=Script,
            )
            for attr in SCRIPT_ATTRIBUTES
        ),
        invalid_error=InvalidAppError,
        duplicate_error=DuplicateAppError,
    )

    info: AppInfoFile
    audit: Script | None = None
    preinstall: Script | None = None
    postinstall: Script | None = None

    audit_path = child_path("audit")
    preinstall_path = child_path("preinstall")
    postinstall_path = child_path("postinstall")

    # Remote-only metadata cached from from_api_payload; not persisted, not part of diff_hash.
    _file_url: str | None = PrivateAttr(default=None)
    _file_size: int | None = PrivateAttr(default=None)

    @property
    def file_url(self) -> str | None:
        """Presigned download URL from the last API fetch (None for disk-loaded members)."""
        return self._file_url

    @property
    def file_size(self) -> int | None:
        """Installer size in bytes from the last API fetch (None for disk-loaded members)."""
        return self._file_size

    @model_validator(mode="after")
    def require_audit_enforcement(self) -> Self:
        """An audit script is only valid when the app is continuously enforced."""
        if (
            self.audit is not None
            and self.audit.content
            and self.info.install_enforcement != InstallEnforcement.CONTINUOUSLY_ENFORCE
        ):
            raise ValueError("An audit script requires install_enforcement to be 'continuously_enforce'.")
        return self

    @override
    @classmethod
    def _info_from_payload(cls, payload: CustomAppPayload) -> AppInfoFile:
        """Flatten the payload's file_* fields into the info file's nested file reference."""
        content_fields = {spec.payload_field for spec in cls._config.content_specs}
        info_data = payload.model_dump(
            exclude=content_fields | {"sha256", "file_key", "file_url", "file_size", "file_updated"}
        )
        # Drop the stale unzip_location the API can return on non-zip apps; it is only valid for zip.
        if info_data["install_type"] != InstallType.ZIP:
            info_data["unzip_location"] = None
        # payload.file_name strips the per-upload token the endpoint appends (MyApp.pkg -> MyApp_<hex>.pkg)
        info_data["file"] = {"name": payload.file_name, "sha256": payload.sha256}
        return AppInfoFile.model_validate(info_data)

    @override
    @classmethod
    def from_api_payload(cls, payload: CustomAppPayload) -> Self:
        """Build the member, caching the remote-only file_url/file_size for the download pass."""
        # Drop the stale audit script the API can return on legacy non-enforced apps.
        if payload.audit_script and payload.install_enforcement != InstallEnforcement.CONTINUOUSLY_ENFORCE:
            payload = payload.model_copy(update={"audit_script": ""})
        member = super().from_api_payload(payload)
        member._file_url = payload.file_url
        member._file_size = payload.file_size
        return member

    def _legacy_binary(self, payload_dir: Path) -> Path | None:
        """The pre-suffix clean-named installer eligible for migration, when the suffixed target is absent."""
        binary_path = payload_dir / self.info.file.name
        if binary_path.is_file():
            return None
        legacy_path = payload_dir / self.info.file.payload_name
        if legacy_path != binary_path and legacy_path.is_file():
            return legacy_path
        return None

    def _resolve_binary(self, payload_dir: Path) -> Path:
        """Resolve and verify the installer binary within payload_dir.

        Raises:
            MissingAppInstallerError: The binary is not present in the payload directory.
            InvalidAppError: The binary's sha256 does not match the info file.
        """
        binary_path = payload_dir / self.info.file.name
        found_path = binary_path if binary_path.is_file() else self._legacy_binary(payload_dir)
        if found_path is None:
            raise MissingAppInstallerError(f"Unable to locate the installer binary at {binary_path}.")

        sha256 = sha256_file(found_path)
        if sha256 != self.info.file.sha256:
            raise InvalidAppError(
                f"The sha256 of {found_path} does not match the info file ({sha256} != {self.info.file.sha256})."
            )
        if found_path != binary_path:
            found_path.rename(binary_path)
        return binary_path

    @override
    def _update_payload(self, config: ApiConfig) -> dict[str, Any]:
        """Build the create/update keyword arguments for the custom app resource (sans binary)."""
        payload: dict[str, Any] = {
            "name": self.name,
            "install_type": self.info.install_type,
            "install_enforcement": self.info.install_enforcement,
            "audit_script": self.audit.content if self.audit else "",
            "preinstall_script": self.preinstall.content if self.preinstall else "",
            "postinstall_script": self.postinstall.content if self.postinstall else "",
            "restart": self.info.restart,
            "active": self.info.active,
            "show_in_self_service": self.info.show_in_self_service,
            "unzip_location": self.info.unzip_location,
        }
        payload |= self_service_payload(config, self.info)
        return payload

    @override
    def push_remote(
        self,
        config: ApiConfig,
        *,
        create: bool,
        payload_dir: Path | None = None,
        other: "MemberBase[Any, Any] | None" = None,
        reporter: StreamReporter = NULL_REPORTER,
    ) -> PushOutcome[CustomAppPayload]:
        """Create or update this custom app in Iru, uploading the installer binary when needed.

        create requires payload_dir and always uploads. update uploads only when the local
        sha256 differs from the remote one (read from other, or fetched once when other is None);
        a matching sha is a metadata-only PATCH that needs no binary on disk. reporter opens the
        inner byte-progress bar for the upload stream when one happens.
        """
        payload = self._update_payload(config)
        uploaded = False
        if create:
            if payload_dir is None:
                raise InvalidAppError("A payload directory is required to upload the installer binary.")
            payload["file"] = self._resolve_binary(payload_dir)
            payload["file_name"] = self.info.file.payload_name
            uploaded = True
        elif payload_dir is not None:
            remote_sha = other.info.file.sha256 if isinstance(other, CustomApp) else self._fetch_remote_sha(config)
            if remote_sha != self.info.file.sha256:
                payload["file"] = self._resolve_binary(payload_dir)
                payload["file_name"] = self.info.file.payload_name
                uploaded = True

        with CustomAppsResource(config) as api:
            result = (
                api.create(**payload, reporter=reporter)
                if create
                else api.update(id=self.id, **payload, reporter=reporter)
            )
        return PushOutcome(payload=result, uploaded=uploaded)

    def _fetch_remote_sha(self, config: ApiConfig) -> str:
        """Fetch the current remote sha256 (fallback when the counterpart member is unavailable)."""
        return self.get_remote(config).sha256

    def would_upload(self, other: "MemberBase[Any, Any] | None") -> bool:
        """Whether pushing this app would upload its installer rather than only metadata."""
        remote_sha = other.info.file.sha256 if isinstance(other, CustomApp) else None
        return remote_sha != self.info.file.sha256

    def plan_download(self, payload_dir: Path, *, force: bool = False) -> DownloadResult:
        """Classify what download_binary would do, without downloading or renaming."""
        target = payload_dir / self.info.file.name
        present = target if target.is_file() else self._legacy_binary(payload_dir)
        if present is not None:
            if sha256_file(present) == self.info.file.sha256:
                return DownloadResult.UP_TO_DATE if present == target else DownloadResult.MIGRATED
            if not force:
                return DownloadResult.MISMATCH_SKIPPED
        return DownloadResult.DOWNLOADED

    def download_binary(
        self,
        payload_dir: Path,
        *,
        force: bool = False,
        replaced_sha: str | None = None,
        on_progress: Callable[[int], None] = lambda _: None,
    ) -> DownloadResult:
        """Ensure this app's installer is present at payload_dir, downloading from Iru if needed.

        A pre-suffix installer whose content matches the info file is renamed to its suffixed
        name instead of downloaded. A forced download that replaces a stale legacy installer
        removes it only when its content matches replaced_sha (the previously recorded sha).
        """
        target = payload_dir / self.info.file.name
        result = self.plan_download(payload_dir, force=force)
        if result is DownloadResult.MIGRATED:
            # plan_download verified the legacy file's sha; stamp the content-suffixed name.
            if (legacy := self._legacy_binary(payload_dir)) is not None:
                legacy.rename(target)
                return result
            # The legacy file vanished since planning; recover with a real download.
            result = DownloadResult.DOWNLOADED
        if result is not DownloadResult.DOWNLOADED:
            return result

        if self.file_url is None:
            raise InvalidAppError("No remote download URL is available for this app.")

        legacy = self._legacy_binary(payload_dir)
        payload_dir.mkdir(parents=True, exist_ok=True)
        with S3Client() as transport:
            transport.download_file(
                self.file_url,
                target,
                expected_sha=self.info.file.sha256,
                file_size=self.file_size,
                on_progress=on_progress,
            )
        if legacy is not None and replaced_sha is not None and sha256_file(legacy) == replaced_sha:
            legacy.unlink()
        return DownloadResult.DOWNLOADED

    @override
    def _detail_rows(self) -> Iterable[tuple[str, RenderableType]]:
        rows: list[tuple[str, RenderableType]] = [
            ("Active", str(self.info.active)),
            ("Install Type", str(self.info.install_type)),
            ("Install Enforcement", str(self.info.install_enforcement)),
            ("Restart", str(self.info.restart)),
        ]
        if self.info.unzip_location is not None:
            rows.append(("Unzip Location", self.info.unzip_location))
        rows += [
            ("File Name", self.info.file.name),
            ("File SHA256", self.info.file.sha256),
            ("Show in Self Service", str(self.info.show_in_self_service)),
            ("Self Service Category ID", str(self.info.self_service_category_id or "")),
            (
                "Self Service Recommended",
                str("" if self.info.self_service_recommended is None else self.info.self_service_recommended),
            ),
        ]
        return rows

    @override
    def _content_rows(self) -> Iterable[tuple[str, RenderableType]]:
        rows: list[tuple[str, RenderableType]] = []
        for attribute in SCRIPT_ATTRIBUTES:
            script: Script | None = getattr(self, attribute)
            content = script.content if script is not None else ""
            rows.append(
                (
                    f"{attribute.capitalize()} Script",
                    Syntax(content, guess_lexer(content), background_color="default"),
                )
            )
        return rows
