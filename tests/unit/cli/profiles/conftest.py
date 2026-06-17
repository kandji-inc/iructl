import random
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iructl._cli.common import (
    ActionResponse,
    ActionType,
    OperationType,
    PreparedAction,
    ResultType,
    SyncResults,
)
from iructl._diff import ChangeType
from tests.fixtures.profiles import (
    mobileconfig_content_factory,
    mobileconfig_data_factory,
    patch_profiles_endpoints,
    profile_directory_factory,
    profile_info_content_factory,
    profile_info_data_factory,
    profiles_lrc,
    profiles_repo,
    profiles_repo_obj,
    profiles_response,
)


@pytest.fixture(params=(True, False), ids=("allow_delete", "no_allow_delete"))
def prepared_push_actions(request, profiles_lrc) -> list[PreparedAction]:
    """Prepare a test set of api actions for the of profiles."""
    local_repo, _, _ = profiles_lrc

    allowed_types = [ActionType.CREATE, ActionType.UPDATE]
    if request.param:
        allowed_types.append(ActionType.DELETE)

    prepared_actions: list[PreparedAction] = []
    profiles = list(local_repo.values())  # copy to list to allow modifying the dict
    for profile in profiles:
        profile.sync_hash = profile.diff_hash
        action_type = random.choice(allowed_types)
        match action_type:
            case ActionType.CREATE:
                change_type = ChangeType.CREATE_LOCAL
            case ActionType.UPDATE:
                change_type = ChangeType.UPDATE_LOCAL
            case ActionType.DELETE:
                change_type = random.choice([ChangeType.CREATE_REMOTE])
            case ActionType.SKIP:
                if request.param:
                    change_type = random.choice([ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT])
                else:
                    change_type = random.choice(
                        [ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT]
                    )
        prepared_actions.append(
            PreparedAction(
                action=action_type,
                operation=OperationType.SKIP if action_type is ActionType.SKIP else OperationType.PUSH,
                change=change_type,
                member=profile,
            )
        )
        if action_type is ActionType.DELETE:
            # Simulate Delete by removing the profile from the repo
            profile.profile_path.unlink()
            profile.info_path.unlink()
            profile.profile_path.parent.rmdir()
            del local_repo[profile.id]

    return prepared_actions


@pytest.fixture(params=(0, 1, 2, 5), ids=("no_failures", "one_failure", "multiple_failures", "all_failures"))
def profile_sync_results(request, prepared_push_actions) -> SyncResults:
    """Prepare a set of profile action results with a variable number of failures."""
    action_responses = []
    for idx, action in enumerate(prepared_push_actions):
        response_profile = action.member.model_copy(deep=True)
        if action.action is ActionType.CREATE:
            new_id = str(uuid4())
            response_profile.info.id = new_id
            response_profile.info.mdm_identifier = f"com.kandji.profile.custom.{new_id}"
        if action.action in {ActionType.CREATE, ActionType.UPDATE}:
            change_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            response_profile.info.created_at = change_time
            response_profile.info.updated_at = change_time
        action_responses.append(
            ActionResponse(
                id=action.member.id,
                action=action.action,
                operation=action.operation,
                result=ResultType.FAILURE if idx < request.param else ResultType.SUCCESS,
                member=None if action.action is ActionType.DELETE else response_profile,
            )
        )

    sync_results = SyncResults(
        success=[r for r in action_responses if r.result is ResultType.SUCCESS],
        failure=[r for r in action_responses if r.result is ResultType.FAILURE],
    )
    return sync_results
