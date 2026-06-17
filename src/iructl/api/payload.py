import re
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

type NormalizedUuid = Annotated[str, AfterValidator(str.lower)]
type ApiPayload = CustomProfilePayload | CustomScriptPayload | CustomAppPayload

_UPLOAD_TOKEN_RE = re.compile(r"_[0-9a-fA-F]+(\.[^.]+)$")


class _PayloadBase(BaseModel):
    """Common Pydantic config for payload-related models."""

    model_config = ConfigDict(extra="ignore")


class CustomProfilePayload(_PayloadBase):
    """Payload model for custom profiles API endpoints."""

    @field_validator("profile", mode="after")
    @classmethod
    def tabs_to_spaces(cls, v: str) -> str:
        return v.expandtabs(tabsize=4)

    id: NormalizedUuid
    name: str
    active: bool
    profile: str
    mdm_identifier: str
    created_at: str
    updated_at: str
    runs_on_mac: bool = False
    runs_on_iphone: bool = False
    runs_on_ipad: bool = False
    runs_on_tv: bool = False
    runs_on_vision: bool = False
    runs_on_android: bool = False
    runs_on_windows: bool = False


class CustomScriptPayload(_PayloadBase):
    """Payload model for custom script API endpoints."""

    id: NormalizedUuid
    name: str
    active: bool
    execution_frequency: str
    restart: bool
    script: str
    remediation_script: str
    created_at: str
    updated_at: str
    show_in_self_service: bool | None = False
    self_service_category_id: str | None = None
    self_service_recommended: bool | None = None


class CustomAppPayload(_PayloadBase):
    """Payload model for custom app library API endpoints."""

    id: NormalizedUuid
    name: str
    sha256: str
    file_key: str
    file_url: str
    file_size: int
    file_updated: str
    install_type: str = Field(description="Installation type", pattern="^(package|zip|image)$")
    install_enforcement: str = Field(
        description="Install enforcement type", pattern="^(install_once|continuously_enforce|no_enforcement)$"
    )
    unzip_location: str | None = None
    restart: bool
    audit_script: str
    preinstall_script: str
    postinstall_script: str
    active: bool
    created_at: str
    updated_at: str
    show_in_self_service: bool | None = False
    self_service_category_id: str | None = None
    self_service_recommended: bool | None = None

    @field_validator("unzip_location", mode="after")
    @classmethod
    def blank_unzip_location_to_none(cls, v: str | None) -> str | None:
        """The API returns "" for non-zip apps; normalize the sentinel to None."""
        return v or None

    @property
    def file_name(self) -> str:
        """The installer file name with the upload endpoint's per-upload token removed."""
        return _UPLOAD_TOKEN_RE.sub(r"\1", Path(self.file_key).name)


class CustomAppUploadPayload(_PayloadBase):
    """Payload model for app binary upload results"""

    name: str
    expires: str
    post_url: str
    post_data: dict[str, str]
    file_key: str


class SelfServiceCategoryPayload(_PayloadBase):
    """Payload model for self-service categories API endpoints."""

    id: str
    name: str


class BlueprintPayload(_PayloadBase):
    """Payload model for the blueprints list API endpoint."""

    id: NormalizedUuid
    name: str
    type: str


type ListablePayload = ApiPayload | BlueprintPayload


class PayloadList[PayloadType: ListablePayload](BaseModel):
    """Payload model for the syncable list endpoints."""

    count: int = 0
    next: str | None = None
    previous: str | None = None
    results: list[PayloadType] = []
