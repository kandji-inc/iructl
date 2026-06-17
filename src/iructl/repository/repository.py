from collections.abc import Iterable, Iterator, MutableMapping
from enum import StrEnum
from pathlib import Path
from typing import Self, override
from uuid import UUID

from iructl._constants import APPS_DIR, PROFILES_DIR, SCRIPTS_DIR
from iructl._utils import locate_repo_root
from iructl.exceptions import InvalidRepositoryError

from .info import ACCEPTED_INFO_EXTENSIONS
from .member_base import MemberBase


class RepositoryDirectory(StrEnum):
    """Enum of subdirectories directories in a repository."""

    PROFILES = PROFILES_DIR
    SCRIPTS = SCRIPTS_DIR
    APPS = APPS_DIR


class Repository[MemberType: MemberBase](MutableMapping[str, MemberType]):
    """A mutable mapping of ID to RepositoryMember objects.

    Supports get operations by ID or path and set and delete operations by ID only. For set, the ID
    must match the script object's ID. Otherwise a ValueError is raised.

    In addition to the standard constructor, a Repository objects can be loaded from a directory.

    Methods:
        load_path: Load scripts from a directory of mobileconfig files.
    """

    def __init__(self, members: Iterable[MemberType] = iter(()), root: Path | None = None) -> None:
        """Initialize the Repository object."""
        self._id_dict: dict[str, MemberType] = {}
        self._path_dict: dict[Path, str] = {}
        self._member_type: type[MemberType] | None = None
        for member in members:
            self.__setitem__(member.id, member)
        self._root_path = root

    @property
    def root(self) -> Path | None:
        return self._root_path

    @property
    def member_type(self) -> type[MemberType]:
        if self._member_type is None:
            raise ValueError("Repository member type not set")
        return self._member_type

    @override
    def __getitem__(self, key: str | UUID | Path) -> MemberType:
        if str(key).lower() in self._id_dict:
            return self._id_dict[str(key).lower()]

        path_key = key.resolve() if isinstance(key, Path) else Path(str(key)).resolve()
        if path_key.exists():
            if path_key.is_file():
                path_key = path_key.parent
            if path_key in self._path_dict:
                return self._id_dict[self._path_dict[path_key]]
        raise KeyError(f"Member {key} not found")

    @override
    def __setitem__(self, key: str, value: MemberType) -> None:
        if key != value.id:
            raise ValueError("Key must match member ID")
        if self._member_type is None:
            self._member_type = type(value)
        self._id_dict[key] = value
        if value.has_paths:
            self._path_dict[value.info_path.resolve().parent] = value.id

    @override
    def __delitem__(self, key: str) -> None:
        if key in self._id_dict:
            del self._id_dict[key]
            self._path_dict = {k: v for k, v in self._path_dict.items() if v != key}
        else:
            raise KeyError(f"Repository member with ID={key} was not found")

    @override
    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str | UUID | Path):
            return False
        try:
            self[key]
            return True
        except KeyError:
            return False

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._id_dict)

    @override
    def __len__(self) -> int:
        return len(self._id_dict)

    @classmethod
    def load_path(cls, model: type[MemberType], path: Path = Path(".")) -> Self:
        """Load objects from an Iru Control repository."""

        locate_repo_root(cd_path=path)

        members: list[MemberType] = []
        ids: set[str] = set()
        for member_path in (p for p in path.glob("**/info.*") if p.suffix in ACCEPTED_INFO_EXTENSIONS):
            member = model.from_path(member_path)
            if member.id in ids:
                # Two scripts in the same repository cannot have the same ID
                raise InvalidRepositoryError(f"Duplicate member ID ({member.id}) found at {member_path.parent}")
            ids.add(member.id)
            members.append(member)

        repo = cls(members, root=path)
        return repo
