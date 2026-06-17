from collections.abc import Iterable
from typing import Any, ClassVar, override

from rich.console import RenderableType
from rich.syntax import Syntax

from iructl._constants import PROFILES_DIR
from iructl.api import ApiConfig, CustomProfilePayload, CustomProfilesResource
from iructl.exceptions import (
    DuplicateProfileError,
    InvalidProfileError,
    MissingProfileError,
)

from .content import Mobileconfig
from .info import PROFILE_RUNS_ON_PARAMS, ProfileInfoFile
from .member_base import ContentChild, MemberBase, MemberConfig, child_path


class CustomProfile(MemberBase[ProfileInfoFile, CustomProfilePayload]):
    """A data model for representing a custom profile."""

    directory_name: ClassVar[str] = PROFILES_DIR

    _config: ClassVar = MemberConfig(
        member_name="custom profile",
        resource_cls=CustomProfilesResource,
        content_specs=(
            ContentChild(
                attribute="profile",
                payload_field="profile",
                content_cls=Mobileconfig,
                required=True,
                default_content=lambda info, parent: Mobileconfig.default_content(_id=info.id, name=parent.stem),
            ),
        ),
        invalid_error=InvalidProfileError,
        duplicate_error=DuplicateProfileError,
        missing_error=MissingProfileError,
    )

    info: ProfileInfoFile
    profile: Mobileconfig

    profile_path = child_path("profile")

    @override
    @classmethod
    def _info_from_payload(cls, payload: CustomProfilePayload) -> ProfileInfoFile:
        """Patch legacy all-false runs_on payloads to all-true before building the info model.

        All runs_on parameters being false was possible in older versions of Iru before the
        runs_on parameter was required.
        """
        if all(getattr(payload, param) is False for param in PROFILE_RUNS_ON_PARAMS):
            payload = payload.model_copy()
            for param in PROFILE_RUNS_ON_PARAMS:
                setattr(payload, param, True)
        return super()._info_from_payload(payload)

    def _format_runs_on(self) -> str:
        """Format a profile's runs_on attributes into a human-readable string."""

        all_platforms = ["Mac", "iPhone", "iPad", "TV", "Vision"]

        runs_on_list = [
            platform for platform in all_platforms if getattr(self.info, f"runs_on_{platform.lower()}", False)
        ]

        runs_on_length = len(runs_on_list)

        if runs_on_length == 0:
            runs_on_list = all_platforms.copy()

        if runs_on_length == 1:
            return runs_on_list[0]
        elif runs_on_length == 2:
            return f"{runs_on_list[0]} and {runs_on_list[1]}"
        else:
            return f"{', '.join(runs_on_list[:-1])}, and {runs_on_list[-1]}"

    @override
    def _update_payload(self, config: ApiConfig) -> dict[str, Any]:
        """Build the create/update keyword arguments for the custom profile resource."""
        return {
            "name": self.name,
            "file": self.profile_path,
            "active": self.info.active,
            "runs_on_mac": self.info.runs_on_mac,
            "runs_on_iphone": self.info.runs_on_iphone,
            "runs_on_ipad": self.info.runs_on_ipad,
            "runs_on_tv": self.info.runs_on_tv,
            "runs_on_vision": self.info.runs_on_vision,
        }

    @override
    def _detail_rows(self) -> Iterable[tuple[str, RenderableType]]:
        return [
            ("MDM Identifier", self.info.mdm_identifier),
            ("Active", str(self.info.active)),
            ("Runs On", self._format_runs_on()),
        ]

    @override
    def _content_rows(self) -> Iterable[tuple[str, RenderableType]]:
        return [("Profile", Syntax(self.profile.content, "xml", background_color="default"))]
