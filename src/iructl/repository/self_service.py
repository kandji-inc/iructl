import contextlib
import functools
from typing import Any, Protocol
from uuid import UUID

from iructl._constants import DEFAULT_SCRIPT_CATEGORY
from iructl.api import ApiConfig, SelfServiceCategoriesResource


class _SelfServiceInfo(Protocol):
    """The self-service fields an info model must expose to build a self-service payload."""

    show_in_self_service: bool
    self_service_category_id: str | None
    self_service_recommended: bool | None


@functools.cache
def get_category_id(config: ApiConfig, name: str | None = None) -> str:
    """Get the ID for the named category (or default) in Self Service, if it exists."""
    if name is not None:
        with contextlib.suppress(ValueError):
            UUID(name, version=4)
            return name  # name is already a valid UUID

    with SelfServiceCategoriesResource(config) as ss:
        categories = ss.list()

    name = DEFAULT_SCRIPT_CATEGORY if name is None else name
    try:
        return next(c.id for c in categories if c.name.lower() == name.lower())
    except StopIteration:
        raise ValueError(f"Unable to get ID for category '{name}'. Ensure the category exists.")


def self_service_payload(config: ApiConfig, info: _SelfServiceInfo) -> dict[str, Any]:
    """Build the self-service keys for a create/update payload.

    Empty when the member is not shown in Self Service; otherwise resolves the category to an
    ID and includes the recommended flag (defaulting to False).
    """
    if not info.show_in_self_service:
        return {}
    return {
        "self_service_category_id": get_category_id(config=config, name=info.self_service_category_id),
        "self_service_recommended": info.self_service_recommended or False,
    }
