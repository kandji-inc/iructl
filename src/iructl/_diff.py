from enum import StrEnum
from typing import TypeVar

from iructl.repository import MemberBase

__all__ = ["ChangeType", "ChangesDict", "three_way_diff"]


class ChangeType(StrEnum):
    """The type of change returned by a three-way diff comparison.

    Attributes:
        NONE: No change between the values
        CREATE_REMOTE: The remote value is new
        UPDATE_REMOTE: The remote value has changed
        CREATE_LOCAL: The local value is new
        UPDATE_LOCAL: The local value has changed
        CONFLICT: The remote and local values have conflicting changes

    """

    NONE = "no_changes"
    CREATE_REMOTE = "new_remote"
    UPDATE_REMOTE = "updated_remote"
    CREATE_LOCAL = "new_local"
    UPDATE_LOCAL = "updated_local"
    CONFLICT = "conflict"


MemberType = TypeVar("MemberType", bound=MemberBase)
ChangesDict = dict[ChangeType, list[tuple[MemberType | None, MemberType | None]]]


def three_way_diff(*, base: str | None, local: str | None, remote: str | None) -> ChangeType:
    """Determine the type of change between three diff hashes.

    The base value is used to determine which of the remote and local values have
    changed when they are not equal. If the remote and local values are equal, the
    change is considered NONE regardless of the base value. If the base value is
    None, then any difference between the remote and local values is considered a
    BOTH change.

    Args:
        base (str | None): The diff_hash value for the compare base object
        remote (str | None): The diff_hash value for the remote object
        local (str | None): The diff_hash value for the local object

    Returns:
        ChangeType: The type of change between the values

    """

    if remote == local:
        return ChangeType.NONE
    elif remote is None and local is not None:
        return ChangeType.CREATE_LOCAL
    elif local is None and remote is not None:
        return ChangeType.CREATE_REMOTE
    elif base == local and base != remote:
        return ChangeType.UPDATE_REMOTE
    elif base == remote and base != local:
        return ChangeType.UPDATE_LOCAL
    else:
        return ChangeType.CONFLICT
