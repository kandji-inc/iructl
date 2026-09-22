import hashlib
import io
import json
import plistlib
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, Self, cast, get_args, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError
from rich.console import RenderableType
from rich.table import Table
from ruamel.yaml.scalarstring import LiteralScalarString

from iructl._console import OutputFormat, SyntaxType, render_plain_text
from iructl._progress import NULL_REPORTER, StreamReporter
from iructl._utils import locate_repo_root, sanitize_filename, yaml
from iructl.api import ApiConfig, ApiPayload, PayloadList
from iructl.api.resource_base import ResourceBase
from iructl.exceptions import DuplicateInfoFileError, MissingInfoFileError

from .content import File
from .info import ACCEPTED_INFO_EXTENSIONS, InfoFile

if TYPE_CHECKING:
    from collections.abc import Callable

    from iructl.repository.custom_app import DownloadResult


_EMPTY_CELL = "-"


def _strip_nones(value: Any) -> Any:
    """Recursively drop None-valued entries from dicts. plistlib rejects None at any depth."""
    if isinstance(value, dict):
        return {k: _strip_nones(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_nones(item) for item in value]
    return value


@runtime_checkable
class _DiffableModel(Protocol):
    """A pydantic model with a diff_hash property (File or InfoFile)."""

    @property
    def diff_hash(self) -> str: ...

    def model_copy(self) -> Self: ...


class _MemberApiResource(ResourceBase, Protocol):
    """The subset of a resource API used for the generic member methods."""

    def list(self) -> Any: ...

    def get(self, id: str) -> Any: ...

    def delete(self, id: str) -> None: ...


def child_path(attr: str) -> property:
    """Build a property forwarding get/set to content child attr's path.

    Concrete members declare one per child, e.g. audit_path = child_path("audit").
    Reading returns the child's path and raises when it is unset; assigning sets the
    path and raises when the child itself is absent. A plain property is returned so
    pydantic's validate_assignment routing stays intact (it special-cases properties).
    """

    def fget(self: "MemberBase") -> Path:
        child = getattr(self, attr)
        if child is None or child.path is None:
            raise ValueError(f"The {attr}_path property must be set before reading.")
        return child.path

    def fset(self: "MemberBase", value: Path) -> None:
        child = getattr(self, attr)
        if child is None:
            raise ValueError(f"The {attr} must exist before its path can be set.")
        child.path = value

    return property(fget, fset)


@dataclass(frozen=True)
class ContentChild:
    """Declarative description of one of a member's content children.

    Subclasses declare one per child in MemberConfig.content_specs; the shared member methods
    use these to convert payloads and load files so subclasses describe their children instead
    of hand-writing from_api_payload/from_path.
    """

    attribute: str  # model field name, e.g. "audit"
    payload_field: str  # API payload field, e.g. "script" or "audit_script"
    content_cls: type[File]  # content model, e.g. Script or Mobileconfig
    output_key: str | None = None  # overrides the rendered syntax-dict key; see the `key` property
    required: bool = False  # required children are always present; an absent optional child is None
    default_content: "Callable[[InfoFile, Path], str] | None" = None  # generates a required child when absent
    empty_error: bool = False  # raise the member's invalid_error when the loaded content is empty

    @property
    def key(self) -> str:
        """Key for this child's content in the rendered syntax dict: the explicit output_key, else payload_field."""
        return self.output_key or self.payload_field

    @property
    def filename(self) -> str:
        """On-disk filename for placing or generating the child: attribute + the content type's suffix."""
        return f"{self.attribute}{self.content_cls.default_suffix}"

    @property
    def glob(self) -> str:
        """On-disk match for locating the child, per the content type's naming convention."""
        return self.content_cls.default_glob(self.attribute)


@dataclass
class PushOutcome[PayloadType: ApiPayload]:
    """The result of a member push: the returned API payload and whether a binary was uploaded."""

    payload: PayloadType
    uploaded: bool = False


@dataclass(frozen=True)
class MemberConfig:
    """Per-subclass configuration for a member type.

    Declared once as _config on each MemberBase subclass to centralize the API wiring,
    content-child specs, and error types that the shared member methods rely on.
    """

    member_name: str  # human-readable noun used in error messages, e.g. "custom script"
    resource_cls: type[_MemberApiResource]  # API resource for list/get/create/update/delete
    content_specs: tuple[ContentChild, ...]  # one ContentChild per content child
    invalid_error: type[Exception]  # raised for malformed members (bad paths, empty required content)
    duplicate_error: type[Exception]  # raised when more than one file matches a content child glob
    missing_error: type[Exception] = FileNotFoundError  # absent required child when generation is disabled


class MemberBase[InfoType: InfoFile, PayloadType: ApiPayload](BaseModel, ABC):
    """Generic base for repository members."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    directory_name: ClassVar[str]

    _config: ClassVar[MemberConfig]

    info: InfoType

    info_path = child_path("info")

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Guard that each content child is declared consistently."""
        super().__pydantic_init_subclass__(**kwargs)
        if not hasattr(cls, "_config"):
            return  # abstract subclass declared without a _config
        for spec in cls._config.content_specs:
            field = cls.model_fields.get(spec.attribute)
            if field is None:
                raise TypeError(f"{cls.__name__}: content spec '{spec.attribute}' has no matching model field.")
            field_types = get_args(field.annotation) or (field.annotation,)
            if spec.content_cls not in field_types:
                raise TypeError(
                    f"{cls.__name__}: field '{spec.attribute}' type does not include its content spec "
                    f"content_cls ({spec.content_cls.__name__})."
                )
            path_attr = f"{spec.attribute}_path"
            if not isinstance(getattr(cls, path_attr, None), property):
                raise TypeError(f"{cls.__name__}: missing '{path_attr}' property for content child '{spec.attribute}'.")

    @property
    def id(self) -> str:
        """Get the unique identifier."""
        return self.info.id

    @property
    def name(self) -> str:
        """Get the name."""
        return self.info.name

    @property
    def children(self) -> list[File | InfoType]:
        """Get the info object and content child objects."""
        return [c for c in (getattr(self, field) for field in type(self).model_fields.keys()) if c is not None]

    @classmethod
    def _info_model(cls) -> type[InfoType]:
        """The concrete info model for this member, read from the info field annotation."""
        return cast("type[InfoType]", cls.model_fields["info"].annotation)

    @property
    def diff_hash(self) -> str:
        """Get the hash relevant for diff operations."""
        return hashlib.sha256("".join(child.diff_hash for child in self.children).encode("utf-8")).hexdigest()

    @property
    def sync_hash(self) -> str | None:
        """Get the stored hash at last sync."""
        return self.info.sync_hash

    @sync_hash.setter
    def sync_hash(self, value: str) -> None:
        """Set the stored hash."""
        self.info.sync_hash = value

    def updated(self, other: Self) -> Self:
        """Return an updated object."""

        update_map = {}
        for field in type(self).model_fields.keys():
            self_value = getattr(self, field)
            other_value = getattr(other, field)
            if (
                field != "info"
                and isinstance(self_value, _DiffableModel)
                and isinstance(other_value, _DiffableModel)
                and self_value.diff_hash == other_value.diff_hash
            ):
                # Equivalent content according to diff_hash; keep the local copy.
                update_map[field] = self_value.model_copy()
            elif isinstance(self_value, BaseModel) and isinstance(other_value, BaseModel):
                # A remote member has no local-only fields set; exclude them so the merge
                # cannot wipe locally-held values with None.
                merged = self_value.model_copy(update=other_value.model_dump(exclude=set(InfoFile.LOCAL_ONLY)))
                update_map[field] = type(self_value).model_validate(dict(merged.__dict__))
            else:
                update_map[field] = other_value

        return type(self)(**update_map)

    @classmethod
    def synced(cls, existing: Self | None, remote: Self) -> Self:
        """Build the local member to persist after a successful sync.

        Merge the remote into the existing local member when there is one, then stamp
        sync_hash to the merged member's current diff_hash. Returns a new member; the
        passed remote is not mutated.
        """
        merged = existing.updated(remote) if existing is not None else remote.model_copy(deep=True)
        merged.sync_hash = merged.diff_hash
        return merged

    @property
    def has_paths(self) -> bool:
        """Check if the path properties are set."""
        return all(child.path is not None for child in self.children)

    def ensure_paths(self, repo_path: Path) -> None:
        """Set the path properties to valid default paths within the repository.

        Each present content child is placed under the member directory: an unset path takes
        the child's default filename, and a path in the wrong directory is re-parented.

        Raises:
            InvalidRepositoryError: repo_path is not a valid Iru repository.
        """
        if self.has_paths:
            return  # Nothing to do here

        parent = self._ensure_parent_dir(repo_path)
        for spec in self._config.content_specs:
            child = getattr(self, spec.attribute)
            if child is None:
                continue
            if child.path is None:
                child.path = parent / spec.filename
            elif child.path.parent != parent:
                child.path = parent / child.path.name

    def _ensure_parent_dir(self, repo_path: Path) -> Path:
        """Resolve the collision-free member directory and set info.path inside it.

        Returns the parent directory so callers can place their child files in it.

        Raises:
            InvalidRepositoryError: repo_path is not a valid Iru repository.
        """
        if self.info.path is not None:
            return self.info.path.parent

        repo_path = repo_path.resolve()
        root = locate_repo_root(cd_path=repo_path) / type(self).directory_name

        parent = (repo_path if repo_path.is_relative_to(root) else root) / sanitize_filename(self.info.name)

        # If output path already exists, increment the path with a number
        count = 0
        while parent.exists():
            count += 1
            parent = parent.parent / f"{sanitize_filename(self.info.name)} ({count})"

        self.info.path = parent / f"info.{self.info.format}"
        return self.info.path.parent

    def write(self, write_content: bool = True) -> None:
        """Save the instance to the file system at the path locations."""
        # Ensure valid paths are set
        if not self.has_paths:
            raise ValueError(f"All path properties must be set before writing the {self._config.member_name}.")

        # Verify that all paths are in the same directory
        all_paths = {str(c.path.parent) for c in self.children if c.path is not None}
        if len(all_paths) != 1:
            raise self._config.invalid_error(
                f"All path properties must be paths to files within the same directory ({' != '.join(all_paths)})."
            )

        if self.info_path.stem != "info" or self.info_path.suffix not in ACCEPTED_INFO_EXTENSIONS:
            raise self._config.invalid_error(
                "The info_path property must be a path to an info file. Expected format: info.<plist|json|yml|yaml>"
            )

        for spec in self._config.content_specs:
            child: File | None = getattr(self, spec.attribute)
            if child is not None and child.path is not None and not fnmatch(child.path.name, spec.glob):
                raise self._config.invalid_error(
                    f'The {spec.attribute} file name must match "{spec.glob}" (got {child.path.name}).'
                )

        # Create missing directories if they do not exist
        self.info_path.parent.mkdir(parents=True, exist_ok=True)

        # Write info to file
        self.info.write()

        # Write content to file(s), removing any stale files for absent children
        if write_content:
            for spec in self._config.content_specs:
                child: File | None = getattr(self, spec.attribute)
                if child is None:
                    for stale_path in self.info_path.parent.glob(spec.glob):
                        if stale_path.is_file():
                            stale_path.unlink()
                else:
                    child.write()

    @classmethod
    def from_api_payload(cls, payload: PayloadType) -> Self:
        """Create an instance from an API payload.

        Builds the info model (see _info_from_payload) and wraps each declared content child;
        an optional child whose payload field is empty becomes None.
        """
        info_file = cls._info_from_payload(payload)
        children: dict[str, File | None] = {}
        for spec in cls._config.content_specs:
            content = getattr(payload, spec.payload_field)
            children[spec.attribute] = (
                spec.content_cls(content=spec.content_cls.from_api_content(content))
                if spec.required or content != ""
                else None
            )
        return cls(info=info_file, **children)

    @classmethod
    def _info_from_payload(cls, payload: PayloadType) -> InfoType:
        """Build the info model from the payload, dropping the content fields.

        Override to remap payload fields that are not stored verbatim on the info model.
        """
        content_fields = {spec.payload_field for spec in cls._config.content_specs}
        return cls._info_model().model_validate(payload.model_dump(exclude=content_fields))

    @classmethod
    def from_path(cls, path: Path, generate: bool = True) -> Self:
        """Load an instance from a file path.

        The path can be an info file or a directory containing the info and content files.
        Each declared content child is located, optionally generated (when required and
        generate is True), and loaded.
        """
        parent_path = (path.parent if path.is_file() else path).resolve()
        info_file = cls._info_model().load(cls._locate_info_file(parent_path))
        children = {
            spec.attribute: cls._load_content_child(spec, parent_path, info_file, generate)
            for spec in cls._config.content_specs
        }
        try:
            return cls(info=info_file, **children)
        except ValidationError as error:
            raise cls._config.invalid_error(f"Invalid {cls._config.member_name} at {parent_path}.\n{error}") from error

    @classmethod
    def _load_content_child(
        cls, spec: ContentChild, parent_path: Path, info_file: InfoType, generate: bool
    ) -> File | None:
        """Locate, optionally generate, and load a single content child from parent_path."""
        matches = list(parent_path.glob(spec.glob))
        if len(matches) > 1:
            listing = "\n* ".join(str(p) for p in matches)
            raise cls._config.duplicate_error(f"Multiple {spec.attribute} files exist at {parent_path}:\n* {listing}")
        if not matches:
            if spec.required and generate and spec.default_content is not None:
                target = parent_path / spec.filename
                target.write_text(spec.default_content(info_file, parent_path), encoding="utf-8")
                matches = [target]
            elif spec.required:
                raise cls._config.missing_error(f"Unable to locate {spec.attribute} file at {parent_path}.")
            else:
                return None

        loaded = spec.content_cls.load(matches[0].resolve())
        if loaded.content == "":
            if spec.empty_error:
                raise cls._config.invalid_error(
                    f'The {spec.attribute} file "{matches[0].name}" is empty. Please provide valid content.'
                )
            if not spec.required:
                return None
        return loaded

    @classmethod
    def _locate_info_file(cls, parent_path: Path) -> Path:
        """Locate the single info.* file in parent_path, ensuring there are no duplicates.

        Returns the resolved path; the caller loads it with its concrete InfoFile type.
        """
        matches = [p for p in parent_path.glob("info.*") if p.suffix in ACCEPTED_INFO_EXTENSIONS]
        if len(matches) == 0:
            raise MissingInfoFileError(
                f"Unable to locate info file at {parent_path}. Expected format: info.<plist|json|yml|yaml>"
            )
        if len(matches) > 1:
            raise DuplicateInfoFileError(f"Multiple info files exist at {parent_path}.")
        return matches[0].resolve()

    @classmethod
    def list_remote(cls, config: ApiConfig) -> PayloadList[PayloadType]:
        """List objects from Iru"""
        with cls._config.resource_cls(config) as api:
            return api.list()

    @classmethod
    def get_remote_by_id(cls, config: ApiConfig, id: str) -> PayloadType:
        """Get object from Iru by ID"""
        with cls._config.resource_cls(config) as api:
            return api.get(id=id)

    def get_remote(self, config: ApiConfig) -> PayloadType:
        """Get object from Iru"""
        with type(self)._config.resource_cls(config) as api:
            return api.get(id=self.id)

    def push_remote(
        self,
        config: ApiConfig,
        *,
        create: bool,
        payload_dir: Path | None = None,
        other: "MemberBase[Any, Any] | None" = None,
        reporter: StreamReporter = NULL_REPORTER,
    ) -> PushOutcome[PayloadType]:
        """Create or update this member in Iru.

        Builds the resource keyword arguments via _update_payload (subclass-specific) and
        dispatches to the resource's create or update. payload_dir, other, and reporter
        are accepted for the CustomApp override (binary upload) and unused by the base.
        """
        del payload_dir, other, reporter  # used only by CustomApp's override
        payload = self._update_payload(config)
        with type(self)._config.resource_cls(config) as api:
            resource = cast("Any", api)
            result = resource.create(**payload) if create else resource.update(id=self.id, **payload)
        return PushOutcome(payload=result, uploaded=False)

    def plan_download(self, payload_dir: Path, *, force: bool = False) -> "DownloadResult | None":
        """Classify the installer transfer this member needs on a pull; None for members without a binary."""
        del payload_dir, force  # used only by CustomApp's override
        return None

    @abstractmethod
    def _update_payload(self, config: ApiConfig) -> dict[str, Any]:
        """Build the create/update keyword arguments sent to the resource."""

    def delete_remote(self, config: ApiConfig):
        """Delete object in Iru"""
        with type(self)._config.resource_cls(config) as api:
            api.delete(id=self.id)

    def _base_syntax_dict(self, *, preview_mode: bool = False) -> dict:
        """Dump info with the updated_at fallback applied; subclasses add content keys.

        Local-only fields are excluded from remote-comparison output.
        """
        exclude = set(InfoFile.LOCAL_ONLY)
        if preview_mode:
            exclude.discard("ensure_blueprints")
        output_dict = self.info.model_dump(mode="json", exclude=exclude)
        if output_dict.get("updated_at") is None:
            output_dict["updated_at"] = output_dict["created_at"]
        return output_dict

    @staticmethod
    def _yaml_multiline(value: Any) -> Any:
        """Wrap multiline strings so YAML renders them as literal blocks."""
        if value is not None and len(value.splitlines()) > 1:
            return LiteralScalarString(value)
        return value

    def prepare_syntax_dict(self, syntax: SyntaxType | None = None, *, preview_mode: bool = False) -> dict:
        """Render the member as a dict ready for serialization in the given syntax."""
        output_dict = self._base_syntax_dict(preview_mode=preview_mode)
        for spec in self._config.content_specs:
            child = getattr(self, spec.attribute)
            content = None if child is None else child.content
            if content is not None and syntax == SyntaxType.YAML:
                content = self._yaml_multiline(content)
            output_dict[spec.key] = content
        return _strip_nones(output_dict) if syntax == SyntaxType.XML else output_dict

    @abstractmethod
    def _detail_rows(self) -> Iterable[tuple[str, RenderableType]]:
        """Per-type info rows, in render order: everything between Name and Created At."""

    @abstractmethod
    def _content_rows(self) -> Iterable[tuple[str, RenderableType]]:
        """The content (Syntax) rows for this member, in render order."""

    def format_table(self, *, preview_mode: bool = False) -> Table:
        """Convert a member into a printable Table."""
        table = Table(
            title=f"{self._config.member_name.title()} Details ({'Local' if self.has_paths else 'Remote'})",
            show_lines=True,
            title_justify="left",
            show_header=False,
        )
        table.add_column("Field", style="bold italic")
        table.add_row("ID", self.id)
        table.add_row("Name", self.name)
        for label, value in self._detail_rows():
            table.add_row(label, value)
        table.add_row("Created At", self.info.created_at or _EMPTY_CELL)
        table.add_row("Updated At", self.info.updated_at or self.info.created_at or _EMPTY_CELL)
        for label, value in self._content_rows():
            table.add_row(label, value)
        if preview_mode:
            table.add_row("Ensure Blueprints", self._format_ensure_blueprints())
        return table

    def _format_ensure_blueprints(self) -> str:
        """Render the info.ensure_blueprints field for the detail table.

        The None vs empty-list split is intentional: None means the field is
        omitted (no ensure behaviour), while [] is an explicit empty assignment.
        """
        assignments = self.info.ensure_blueprints
        if assignments is None:
            return _EMPTY_CELL
        if not assignments:
            return "(none declared)"
        return "\n".join(a.blueprint if a.node is None else f"{a.blueprint} (node {a.node})" for a in assignments)

    def format_plain_text(self, format: OutputFormat, *, preview_mode: bool = False) -> str:
        """Format the script content as syntax-highlighted text."""
        if format == OutputFormat.TABLE:
            return render_plain_text(self.format_table(preview_mode=preview_mode))

        format_dict = self.prepare_syntax_dict(syntax=format.to_syntax(), preview_mode=preview_mode)

        match format:
            case OutputFormat.PLIST:
                return plistlib.dumps(format_dict, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")
            case OutputFormat.JSON:
                return json.dumps(format_dict, indent=2)
            case OutputFormat.YAML:
                output_str = io.StringIO()
                yaml.dump(format_dict, output_str)
                return output_str.getvalue()
