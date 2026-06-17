import pytest

from iructl._cli.common import (
    ActionResponse,
    ActionType,
    BlueprintActionOutcome,
    OperationType,
    ResultType,
    SyncResults,
)
from tests.fixtures.apps import app_info_data_factory, custom_app_factory

# Re-exported so test modules in this package can request the custom-app fixtures.
__all__ = ["app_info_data_factory", "custom_app_factory"]

_BLUEPRINT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_NODE_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def make_partial_sync_results():
    """Build a SyncResults holding one partial item with a single failed blueprint outcome."""

    def _build(*, node_id: str | None = _NODE_A, blueprint_name: str | None = None) -> SyncResults:
        outcome = BlueprintActionOutcome(
            blueprint_id=_BLUEPRINT_A,
            node_id=node_id,
            status="failed",
            status_code=404,
            error_message="nope",
            blueprint_name=blueprint_name,
        )
        partial = ActionResponse(
            id="li",
            action=ActionType.SKIP,
            operation=OperationType.PUSH,
            result=ResultType.PARTIAL,
            member=None,
            blueprint_outcomes=[outcome],
        )
        return SyncResults(partial=[partial])

    return _build
