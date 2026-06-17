import hashlib
import json
import plistlib
from abc import ABC
from collections import OrderedDict
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Self, override
from xml.parsers import expat

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from ruamel.yaml import YAMLError

from iructl._constants import DEFAULT_APP_CATEGORY, DEFAULT_SCRIPT_CATEGORY
from iructl._utils import content_suffixed_filename, yaml
from iructl.api import ExecutionFrequency, InstallEnforcement, InstallType
from iructl.exceptions import InvalidInfoFileError

INFO_FORMAT_HASH_KEYS = ("id", "name", "active")
PROFILE_RUNS_ON_PARAMS = (
    "runs_on_mac",
    "runs_on_iphone",
    "runs_on_ipad",
    "runs_on_tv",
    "runs_on_vision",
    "runs_on_android",
    "runs_on_windows",
)
PROFILE_INFO_HASH_KEYS = (*INFO_FORMAT_HASH_KEYS, *PROFILE_RUNS_ON_PARAMS)
SCRIPT_INFO_HASH_KEYS = (
    *INFO_FORMAT_HASH_KEYS,
    "execution_frequency",
    "restart",
    "show_in_self_service",
    "self_service_category_id",
    "self_service_recommended",
)
APP_INFO_HASH_KEYS = (
    *INFO_FORMAT_HASH_KEYS,
    "install_type",
    "install_enforcement",
    "restart",
    "unzip_location",
    "show_in_self_service",
    "self_service_category_id",
    "self_service_recommended",
    "file.sha256",
)

type NormalizedUuid = Annotated[str, AfterValidator(str.lower)]


class InfoFormat(StrEnum):
    PLIST = "plist"
    JSON = "json"
    YAML = "yaml"


SUFFIX_MAP = OrderedDict(
    [
        (".plist", InfoFormat.PLIST),
        (".json", InfoFormat.JSON),
        (".yaml", InfoFormat.YAML),
        (".yml", InfoFormat.YAML),
    ]
)
ACCEPTED_INFO_EXTENSIONS = set(SUFFIX_MAP.keys())
_INFO_FORMAT = f"info.<{'|'.join(SUFFIX_MAP.keys())}>"


class _InfoBase(BaseModel):
    """Common Pydantic config for info-file-related models."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class BlueprintAssignment(_InfoBase):
    """A single (blueprint, node) assignment pair for a Library Item.

    The blueprint may be declared as a UUID or a blueprint name (resolved at push
    time); the node, when present, must be a UUID.
    """

    blueprint: str
    node: NormalizedUuid | None = None


class InfoFile(_InfoBase, ABC):
    """An abstract data model for representing a generic info file."""

    HASH_KEYS: ClassVar[tuple[str, ...]] = ()

    # Fields that exist only on disk, absent from the remote API contract.
    LOCAL_ONLY: ClassVar[frozenset[str]] = frozenset({"sync_hash", "ensure_blueprints"})

    id: NormalizedUuid
    name: str
    active: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    sync_hash: str | None = None
    ensure_blueprints: list[BlueprintAssignment] | None = None
    format: InfoFormat = Field(exclude=True, default=InfoFormat.PLIST)
    path: Path | None = Field(exclude=True, default=None)

    @field_validator("ensure_blueprints", mode="before")
    @classmethod
    def coerce_blueprint_assignments(cls, value: Any) -> Any:
        """Coerce bare-UUID string entries to BlueprintAssignment dicts."""
        if isinstance(value, list):
            return [{"blueprint": entry} if isinstance(entry, str) else entry for entry in value]
        return value

    @field_validator("path", mode="after")
    @classmethod
    def ensure_absolute_paths(cls, v: Path | None) -> Path | None:
        """Ensure that the path property is an absolute paths."""
        if isinstance(v, Path):
            v = v.resolve()
        return v

    @field_validator("path", mode="after")
    @classmethod
    def ensure_valid_file_name(cls, v: Path | None) -> Path | None:
        """Ensure that the path property is a valid file name."""
        if isinstance(v, Path):
            if v.stem != "info" or v.suffix not in ACCEPTED_INFO_EXTENSIONS:
                raise ValueError(f"Invalid info file name. Expected format: {_INFO_FORMAT}")
        return v

    @classmethod
    def load(cls, path: Path) -> Self:
        match path.suffix:
            case ".plist":
                with path.open("rb") as file:
                    try:  # Load plist data
                        info_data = plistlib.load(file)
                    except (plistlib.InvalidFileException, expat.ExpatError) as error:
                        raise InvalidInfoFileError(
                            f"Profile info at {path} is not a valid plist file.\n{error}"
                        ) from error
            case ".json":
                with path.open("r", encoding="utf-8") as file:
                    try:
                        info_data = json.load(file)
                    except json.JSONDecodeError as error:
                        raise InvalidInfoFileError(
                            f"Profile info at {path} is not a valid json file.\n{error}"
                        ) from error
            case ".yml" | ".yaml":
                try:
                    info_data = yaml.load(path)
                except YAMLError as error:
                    raise InvalidInfoFileError(f"Profile info at {path} is not a valid yaml file.\n{error}") from error
            case _:
                raise InvalidInfoFileError(
                    f"Profile info file at {path} does not have a valid suffix. ({_INFO_FORMAT})"
                )

        info_data["path"] = path
        info_data["format"] = SUFFIX_MAP[path.suffix]
        try:
            return cls.model_validate(info_data)
        except ValidationError as error:
            raise InvalidInfoFileError(f"Profile info at {path} is not a valid info file.\n{error}") from error

    def write(self) -> None:
        if self.path is None:
            raise ValueError("The info file has no path set.")

        # Drop unset/None defaults; keep local-only fields.
        info_data = self.model_dump(mode="json", exclude_unset=True, exclude_none=True, exclude={"path"})

        # Create missing directories if they do not exist
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Write info to file
        if self.path.suffix == ".plist":
            with self.path.open("wb") as file:
                # plistlib dump's indent type is not configurable. As a workaround, we dump to a string and use expandtabs.
                plist_str = plistlib.dumps(info_data, sort_keys=False)
                file.write(plist_str.expandtabs(4))
        elif self.path.suffix == ".json":
            with self.path.open("w", encoding="utf-8") as file:
                json.dump(info_data, file, indent=2)
        elif self.path.suffix in (".yml", ".yaml"):
            with self.path.open("w", encoding="utf-8") as file:
                yaml.dump(info_data, file)

    def _hash_value(self, key: str) -> Any:
        """Resolve a (possibly dotted) hash key to a scalar for diff_hash."""
        value: Any = self
        for attr in key.split("."):
            value = getattr(value, attr)
        return value

    @property
    def diff_hash(self) -> str:
        return hashlib.sha256("".join(str(self._hash_value(key)) for key in self.HASH_KEYS).encode("utf-8")).hexdigest()


class ProfileInfoFile(InfoFile):
    """A data model for representing a profile info file."""

    HASH_KEYS: ClassVar = PROFILE_INFO_HASH_KEYS

    mdm_identifier: str = ""
    runs_on_mac: bool = False
    runs_on_iphone: bool = False
    runs_on_ipad: bool = False
    runs_on_tv: bool = False
    runs_on_vision: bool = False
    runs_on_android: bool = False
    runs_on_windows: bool = False

    @model_validator(mode="after")
    def ensure_mdm_identifier(self) -> Self:
        """Ensure the MDM identifier is set."""
        if self.mdm_identifier == "":
            self.mdm_identifier = f"com.kandji.profile.custom.{self.id}"
        return self

    @model_validator(mode="after")
    def at_least_one_runs_on(self) -> Self:
        """Ensure at least one runs_on_* is True."""

        if not set(PROFILE_RUNS_ON_PARAMS).intersection(self.model_fields_set):
            for param in PROFILE_RUNS_ON_PARAMS:
                setattr(self, param, True)

        if not any(getattr(self, param) for param in PROFILE_RUNS_ON_PARAMS):
            raise ValueError("At least one runs_on_* property must be True.")
        return self

    @override
    def _hash_value(self, key: str) -> Any:
        value = super()._hash_value(key)
        if key in PROFILE_RUNS_ON_PARAMS and value is None:
            return False
        return value


class ScriptInfoFile(InfoFile):
    """A data model for representing a script info file."""

    HASH_KEYS: ClassVar = SCRIPT_INFO_HASH_KEYS

    execution_frequency: ExecutionFrequency = ExecutionFrequency.ONCE
    restart: bool = False
    show_in_self_service: bool = False
    self_service_category_id: str | None = None
    self_service_recommended: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def update_self_service_options(cls, values: Any) -> Any:
        """Set default show in self service options."""
        if isinstance(values, dict) and (
            values.get("show_in_self_service") is True
            or values.get("self_service_recommended") is True
            or values.get("self_service_category_id") is not None
            or values.get("execution_frequency") == ExecutionFrequency.NO_ENFORCEMENT
        ):
            # Set default values for missing self service options if any are provided
            values["show_in_self_service"] = True
            values["self_service_category_id"] = values.get("self_service_category_id") or DEFAULT_SCRIPT_CATEGORY
            values["self_service_recommended"] = values.get("self_service_recommended") or False
        return values


class AppFile(_InfoBase):
    """A reference to a custom app's installer file (binary lives outside the repo)."""

    name: str
    sha256: str

    @model_validator(mode="before")
    @classmethod
    def _suffix_name(cls, data: Any) -> Any:
        """Normalize name to carry the content-hash suffix."""
        if isinstance(data, dict) and "name" in data and "sha256" in data:
            return {**data, "name": content_suffixed_filename(data["name"], data["sha256"])}
        return data

    @property
    def payload_name(self) -> str:
        """The installer name with the local content-hash suffix removed."""
        path = Path(self.name)
        suffix = f"_{self.sha256[:8]}"
        return f"{path.stem[: -len(suffix)]}{path.suffix}" if path.stem.endswith(suffix) else self.name


class AppInfoFile(InfoFile):
    """A data model for representing a custom app info file."""

    HASH_KEYS: ClassVar = APP_INFO_HASH_KEYS

    install_type: InstallType
    install_enforcement: InstallEnforcement
    restart: bool = False
    unzip_location: str | None = None
    show_in_self_service: bool = False
    self_service_category_id: str | None = None
    self_service_recommended: bool | None = None
    file: AppFile

    @model_validator(mode="before")
    @classmethod
    def update_self_service_options(cls, values: Any) -> Any:
        """Set default show in self service options.

        The fourth trigger (install_enforcement == NO_ENFORCEMENT) mirrors the live API
        rule verified by tests/unit/api/apps/test_live_payload_shape.py::
        test_no_enforcement_requires_self_service: the API returns 400 with
        "show_in_self_service: Required when not enforcing install" otherwise.
        """
        if isinstance(values, dict) and (
            values.get("show_in_self_service") is True
            or values.get("self_service_recommended") is True
            or values.get("self_service_category_id") is not None
            or values.get("install_enforcement") == InstallEnforcement.NO_ENFORCEMENT
        ):
            values["show_in_self_service"] = True
            values["self_service_category_id"] = values.get("self_service_category_id") or DEFAULT_APP_CATEGORY
            values["self_service_recommended"] = values.get("self_service_recommended") or False
        return values

    @model_validator(mode="after")
    def validate_unzip_location(self) -> Self:
        """unzip_location is required for and only valid with install_type == ZIP."""
        if self.install_type == InstallType.ZIP and self.unzip_location is None:
            raise ValueError("unzip_location is required when install_type is 'zip'.")
        if self.install_type != InstallType.ZIP and self.unzip_location is not None:
            raise ValueError("unzip_location is only valid when install_type is 'zip'.")
        return self
