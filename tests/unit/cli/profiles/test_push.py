import logging
import plistlib
from collections import Counter, OrderedDict
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from iructl._cli.common import ActionResponse, ActionType
from iructl._cli.utility import do_pushes, prepare_push_actions, update_local_member
from iructl._diff import ChangeType
from iructl.api import CustomProfilePayload
from iructl.repository import CustomProfile


class TestDoPush:
    """Tests for the ``do_push`` function."""


@pytest.fixture
def patch_profiles_endpoints(monkeypatch):
    """Patch the profiles endpoints for testing."""

    def fake_create_profile(
        self, name, file, active, runs_on_mac, runs_on_iphone, runs_on_ipad, runs_on_tv, runs_on_vision
    ):
        random_id = str(uuid4())
        with file.open("rb") as f:
            plist = plistlib.load(f, dict_type=OrderedDict)
            plist["PayloadUUID"] = random_id
            plist["PayloadDisplayName"] = name
            plist["PayloadIdentifier"] = f"com.kandji.profile.custom.{random_id}"
        profile_content = plistlib.dumps(plist, fmt=plistlib.PlistFormat.FMT_XML, sort_keys=False).decode("utf-8")
        assert "\t" in profile_content, "Profile content should be formatted with tabs"
        create_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = {
            "id": random_id,
            "name": name,
            "active": active,
            "profile": profile_content,
            "mdm_identifier": f"com.kandji.profile.custom.{random_id}",
            "runs_on_mac": runs_on_mac if runs_on_mac is not None else False,
            "runs_on_iphone": runs_on_iphone if runs_on_iphone is not None else False,
            "runs_on_ipad": runs_on_ipad if runs_on_ipad is not None else False,
            "runs_on_tv": runs_on_tv if runs_on_tv is not None else False,
            "runs_on_vision": runs_on_vision if runs_on_vision is not None else False,
            "created_at": create_time,
            "updated_at": create_time,
        }

        return CustomProfilePayload.model_validate(payload)

    def fake_update_profile(
        self, id, name, file, active, runs_on_mac, runs_on_iphone, runs_on_ipad, runs_on_tv, runs_on_vision
    ):
        with file.open("rb") as f:
            plist = plistlib.load(f, dict_type=OrderedDict)
            plist["PayloadUUID"] = id
            plist["PayloadDisplayName"] = name
            plist["PayloadIdentifier"] = f"com.kandji.profile.custom.{id}"
        profile_content = plistlib.dumps(plist, fmt=plistlib.PlistFormat.FMT_XML, sort_keys=False).decode("utf-8")
        assert "\t" in profile_content, "Profile content should be formatted with tabs"
        create_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        update_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = {
            "id": id,
            "name": name,
            "active": active,
            "profile": profile_content,
            "mdm_identifier": f"com.kandji.profile.custom.{id}",
            "runs_on_mac": runs_on_mac if runs_on_mac is not None else False,
            "runs_on_iphone": runs_on_iphone if runs_on_iphone is not None else False,
            "runs_on_ipad": runs_on_ipad if runs_on_ipad is not None else False,
            "runs_on_tv": runs_on_tv if runs_on_tv is not None else False,
            "runs_on_vision": runs_on_vision if runs_on_vision is not None else False,
            "created_at": create_time,
            "updated_at": update_time,
        }
        return CustomProfilePayload.model_validate(payload)

    def fake_delete_profile(self, id):
        return

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.create", fake_create_profile)
    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.update", fake_update_profile)
    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.delete", fake_delete_profile)


@pytest.mark.parametrize(
    ("force_push", "allow_delete"),
    [
        pytest.param(False, False, id="no_force_no_delete"),
        pytest.param(True, False, id="force_no_delete"),
        pytest.param(False, True, id="no_force_delete"),
        pytest.param(True, True, id="force_delete"),
    ],
)
def test_prepare_profile_actions(profiles_lrc, force_push, allow_delete):
    _, _, changes = profiles_lrc
    actions = prepare_push_actions(changes, force_push=force_push, allow_delete=allow_delete)

    # Check that all actions have a profile
    assert all(action.member is not None for action in actions)

    # Check expected action types
    expected_actions = {
        ActionType.CREATE,
        ActionType.UPDATE,
    }
    if not force_push or not allow_delete:
        expected_actions.add(ActionType.SKIP)
    if allow_delete:
        expected_actions.add(ActionType.DELETE)
    action_set = {action.action for action in actions}
    assert action_set == expected_actions

    # Check expected number of actions including skips
    assert len(actions) == len([c for c in changes if c is not ChangeType.NONE])

    # Check expected number of each action type excluding skips
    actions_without_skips = [a for a in actions if a.action != ActionType.SKIP]
    included_change_types = {ChangeType.CREATE_LOCAL, ChangeType.UPDATE_LOCAL}
    if allow_delete:
        included_change_types.add(ChangeType.CREATE_REMOTE)
    if force_push:
        included_change_types.add(ChangeType.UPDATE_REMOTE)
        included_change_types.add(ChangeType.CONFLICT)
    assert len(actions_without_skips) == sum(len(p) for c, p in changes.items() if c in included_change_types)


@pytest.mark.usefixtures("patch_profiles_endpoints")
def test_do_push(caplog, config, profiles_lrc, prepared_push_actions):
    """Test the ``do_push`` function."""
    caplog.set_level(logging.DEBUG)
    local, _, _ = profiles_lrc

    push_results = do_pushes(config=config, local_repo=local, actions=prepared_push_actions)

    assert len(push_results.success) == len(prepared_push_actions)
    assert len(push_results.failure) == 0  # No failures expected for test
    prepared_actions_counter = Counter(a.action for a in prepared_push_actions)
    push_results_counter = Counter(a.action for a in push_results.success)
    assert prepared_actions_counter == push_results_counter

    for result in push_results.success:
        assert isinstance(result, ActionResponse)
        match result.action:
            case ActionType.CREATE:
                assert result.member is not None
                # only create actions should have an updated id,
                # so this is an effective method for checking
                # which api method was called.
                assert result.member.id != result.id
                assert (
                    f"{result.member.name} ({result.id}) created in Iru successfully with new Iru ID: {result.member.id}"
                    in caplog.text
                )
            case ActionType.UPDATE:
                assert result.member is not None
                assert result.member.id == result.id
                assert f"{result.member.name} ({result.id}) updated in Iru successfully" in caplog.text
            case ActionType.DELETE:
                assert result.member is None
                deleted_profile = next(p for p in prepared_push_actions if p.member.id == result.id).member
                assert f"{deleted_profile.name} ({result.id}) deleted in Iru successfully" in caplog.text


def test_update_local_profile(profiles_repo_obj, profile_sync_results):
    results_to_update = [p for p in profile_sync_results.success if p.action is not ActionType.DELETE]

    for result in results_to_update:
        update_local_member(local_repo=profiles_repo_obj, result=result)

    for result in results_to_update:
        if result.id != result.member.id:  # ActionType.CREATE
            # Check that the profile id was updated in the repo
            assert result.id not in profiles_repo_obj
            assert result.member.id in profiles_repo_obj

        local_profile = profiles_repo_obj[result.member.id]

        # Check that the result profile was merged into the local repo
        assert local_profile.diff_hash == result.member.diff_hash

        loaded_profile = CustomProfile.from_path(local_profile.profile_path)

        # Check that the profile sync hash was update
        assert local_profile == loaded_profile
        assert loaded_profile.sync_hash is not None
        assert loaded_profile.sync_hash == loaded_profile.diff_hash
