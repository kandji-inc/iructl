import contextlib
import functools
from uuid import UUID

import requests
from pydantic import ValidationError

from iructl.api import ApiConfig, BlueprintPayload, BlueprintsResource


@functools.cache
def _blueprints(config: ApiConfig) -> list[BlueprintPayload]:
    """Fetch the tenant's blueprints, once per config."""
    with BlueprintsResource(config) as blueprints:
        return blueprints.list().results


def get_blueprint_id(config: ApiConfig, name: str) -> str:
    """Get the ID for the named blueprint, if it exists."""
    with contextlib.suppress(ValueError):
        UUID(name)
        return name.lower()  # name is already a valid UUID
    try:
        return next(b.id for b in _blueprints(config) if b.name.lower() == name.lower())
    except StopIteration:
        raise ValueError(f"Unable to get ID for blueprint '{name}'. Ensure the blueprint exists.")


def get_blueprint_name(config: ApiConfig, blueprint_id: str) -> str | None:
    """Get the name for the blueprint UUID, for display purposes.

    Best-effort: returns None when the ID is unknown to the tenant or the blueprint
    list cannot be fetched, so callers can fall back to displaying the UUID.
    """
    with contextlib.suppress(requests.RequestException, ValidationError):
        return next((b.name for b in _blueprints(config) if b.id == blueprint_id.lower()), None)
    return None
