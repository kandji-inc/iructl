import hashlib
import io
import json
import plistlib
from abc import ABC
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Self, override
from uuid import uuid4
from xml.parsers import expat

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iructl._console import OutputFormat
from iructl._utils import yaml
from iructl.exceptions import InvalidProfileError


class File(BaseModel, ABC):
    """An abstract data model for representing a generic file."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    default_suffix: ClassVar[str] = ""

    content: str
    path: Path | None = Field(exclude=True, default=None)

    @classmethod
    def default_glob(cls, attribute: str) -> str:
        """On-disk match for a child of this type, named after its attribute."""
        return f"{attribute}*"

    @field_validator("path", mode="after")
    @classmethod
    def ensure_absolute_paths(cls, v: Path | None) -> Path | None:
        """Ensure that the path property is an absolute paths."""
        if isinstance(v, Path):
            v = v.resolve()
        return v

    @property
    def diff_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls(content=path.read_text(encoding="utf-8"), path=path)

    @classmethod
    def from_api_content(cls, content: str) -> str:
        """Adjust content pulled from the API before construction. Override to normalize a stored format."""
        return content

    def write(self):
        if self.path is None:
            raise ValueError("Cannot write without a path set.")
        self.path.write_text(self.content, encoding="utf-8")

    def format_plain_text(self, format: OutputFormat) -> str:  # noqa: ARG002
        """Format the content as plain text."""
        return self.content


class Mobileconfig(File):
    """A data model for representing a mobileconfig file."""

    default_suffix: ClassVar[str] = ".mobileconfig"

    @classmethod
    @override
    def default_glob(cls, attribute: str) -> str:
        """Match any file with the mobileconfig extension, regardless of name."""
        return f"*{cls.default_suffix}"

    @field_validator("path", mode="after")
    @classmethod
    def ensure_valid_extension(cls, v: Path | None) -> Path | None:
        """Ensure that the path property has a valid extension."""
        if v is not None and v.suffix != ".mobileconfig":
            raise ValueError("Invalid mobileconfig file extension: Expected .mobileconfig")
        return v

    @property
    def data(self) -> dict[str, Any]:
        return self._data(self.content)

    @staticmethod
    @lru_cache
    def _data(content: str | bytes) -> dict[str, Any]:
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            return plistlib.loads(content, dict_type=OrderedDict)
        except (plistlib.InvalidFileException, expat.ExpatError) as error:
            raise ValueError("The mobileconfig content is not a valid plist") from error

    @classmethod
    def default_content(cls, _id: str | None = None, name: str = "New Profile") -> str:
        """Get the default content for a profile."""
        if _id is None:
            _id = str(uuid4())

        return (
            plistlib.dumps(
                {
                    "PayloadDisplayName": name,
                    "PayloadIdentifier": f"com.kandji.profile.custom.{_id}",
                    "PayloadType": "Configuration",
                    "PayloadUUID": _id,
                    "PayloadVersion": 1,
                    "PayloadContent": [{}],
                }
            )
            .decode("utf-8")
            .expandtabs(4)
        )

    @override
    @classmethod
    def load(cls, path: Path) -> Self:
        profile_bytes = path.read_bytes()
        try:
            profile_data = cls._data(profile_bytes)
        except ValueError as error:
            raise InvalidProfileError(
                f"The mobileconfig at {path} is in an invalid format. Check the file and try again."
            ) from error
        if profile_bytes[:8] == b"bplist00":
            profile_content = plistlib.dumps(profile_data, fmt=plistlib.FMT_XML).decode("utf-8")
        else:
            profile_content = profile_bytes.decode("utf-8")
        return cls(content=profile_content, path=path)

    @override
    def format_plain_text(self, format: OutputFormat) -> str:
        format_dict = plistlib.loads(self.content.encode("utf-8"))
        match format:
            case OutputFormat.PLIST | OutputFormat.TABLE:
                return plistlib.dumps(format_dict, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")
            case OutputFormat.JSON:
                return json.dumps(format_dict, indent=2)
            case OutputFormat.YAML:
                output_str = io.StringIO()
                yaml.dump(format_dict, output_str)
                return output_str.getvalue()


class Script(File):
    """A data model for representing a script file."""

    default_suffix: ClassVar[str] = ".zsh"
    default_content: ClassVar[str] = """#!/bin/zsh -f
# https://docs.iru.com/en/endpoint/library/library-items-profiles/custom-scripts-overview#custom-scripts-overview

echo "Hello, World!"
exit 0
"""

    @property
    @override
    def diff_hash(self) -> str:
        # Iru strips leading/trailing whitespace server-side, regardless of what is sent.
        return hashlib.sha256(self.content.strip().encode("utf-8")).hexdigest()

    @classmethod
    @override
    def from_api_content(cls, content: str) -> str:
        """Normalize to a single trailing newline, so scripts pulled from Iru have a consistent local format."""
        stripped = content.strip()
        return f"{stripped}\n" if stripped else stripped

    @override
    def write(self):
        if self.path is None:
            raise ValueError("Cannot write without a path set.")
        self.path.write_text(self.content, encoding="utf-8")

        # Make the script executable
        self.path.chmod(self.path.stat().st_mode | 0o111)
