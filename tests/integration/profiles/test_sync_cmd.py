import random
import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_NAME, INFO_FORMAT_ENV
from iructl._diff import ChangeType
from iructl.repository import BlueprintAssignment, CustomProfile, Repository
from tests.output import normalize_output

runner = CliRunner()


def compare_profile_object(profile1, profile2, expected_diff):
    for k in set(profile1.info.model_dump().keys()):
        if k in expected_diff:
            assert getattr(profile1.info, k, None) != getattr(profile2.info, k, None)
        else:
            assert getattr(profile1.info, k, None) == getattr(profile2.info, k, None)

    if "profile" not in expected_diff:
        assert profile1.profile.content == profile2.profile.content
    else:
        assert profile1.profile.content != profile2.profile.content


def compare_profile_content(profile1, profile2, expected_diff):
    all_keys = set(profile1.profile.data.keys()) | set(profile2.profile.data.keys())
    for k in all_keys:
        if k in expected_diff:
            assert profile1.profile.data.get(k) != profile2.profile.data.get(k)
        else:
            assert profile1.profile.data.get(k) == profile2.profile.data.get(k)


def test_help():
    result = runner.invoke(app, ["profile", "sync", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} profile sync" in result.stdout
    assert "Sync custom profiles with Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_dry_run(profiles_lrc):
    local, _, _ = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "sync", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created profile.*in Iru:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated profile.*in Iru:", result.stdout)) == 1
    assert len(re.findall(r"Would have created profile.*locally:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated profile.*locally:", result.stdout)) == 1
    assert "Would have deleted profile:" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no profiles have changed
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_force_push(profiles_lrc):
    pre_local, _, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == pre_local

    result = runner.invoke(app, ["profile", "sync", "--all", "--force-mode", "push"])
    assert result.exit_code == 0

    # Check output
    assert "Syncing 5 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru successfully with new Iru ID:", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 2
    assert len(re.findall(r"created in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"deleted.*successfully", result.stdout)) == 0
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+2\s+0", result.stdout)) == 1  # in push table
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 1  # in pull table
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert "Deleted" not in result.stdout

    post_local = Repository.load_path(model=CustomProfile)

    # Check that the created profile's id has been updated along with dependent fields
    old_created_profile = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_profile = post_local[str(old_created_profile.profile_path)]
    assert old_created_profile.id not in post_local
    assert new_created_profile.id in post_local
    compare_profile_object(
        old_created_profile,
        new_created_profile,
        {"id", "created_at", "updated_at", "sync_hash", "profile", "mdm_identifier"},
    )
    compare_profile_content(
        old_created_profile,
        new_created_profile,
        {"PayloadUUID", "PayloadIdentifier"},
    )

    # Check that the pushed profiles have only changed in expected ways. The changes in this case are
    # because the PayloadDisplayName is set to match the info file name field
    for change_type in {ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT}:
        updated_id = changes[change_type][0][0].id
        compare_profile_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash", "profile"})
        compare_profile_content(pre_local[updated_id], post_local[updated_id], {"PayloadDisplayName"})

    # Check that the pulled profiles match the remote
    for change_type in {ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE}:
        remote_profile = changes[change_type][0][1]
        post_local_profile = post_local[remote_profile.id]
        compare_profile_object(
            remote_profile,
            post_local_profile,
            {"sync_hash", "profile_path", "info_path"},
        )
        compare_profile_content(remote_profile, post_local_profile, {})


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_force_pull(profiles_lrc):
    pre_local, _, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == pre_local

    result = runner.invoke(app, ["profile", "sync", "--all", "--force-mode", "pull"])
    assert result.exit_code == 0

    # Check output
    assert "Syncing 5 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru successfully with new Iru ID:", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 1
    assert len(re.findall(r"created in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 2
    assert len(re.findall(r"deleted.*successfully", result.stdout)) == 0
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+2\s+0", result.stdout)) == 1  # in pull table
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 1  # in push table
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert "Deleted" not in result.stdout

    post_local = Repository.load_path(model=CustomProfile)

    # Check that the created profile's id has been updated along with dependent fields
    old_created_profile = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_profile = post_local[str(old_created_profile.profile_path)]
    assert old_created_profile.id not in post_local
    assert new_created_profile.id in post_local
    compare_profile_object(
        old_created_profile,
        new_created_profile,
        {"id", "created_at", "updated_at", "sync_hash", "profile", "mdm_identifier"},
    )
    compare_profile_content(
        old_created_profile,
        new_created_profile,
        {"PayloadUUID", "PayloadIdentifier"},
    )

    # Check that the pushed profiles have only changed in expected ways. The changes in this case are
    # because the PayloadDisplayName is set to match the info file name field
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_profile_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash", "profile"})
    compare_profile_content(pre_local[updated_id], post_local[updated_id], {"PayloadDisplayName"})

    # Check that the pulled profiles match the remote
    for change_type in {ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT}:
        remote_profile = changes[change_type][0][1]
        post_local_profile = post_local[remote_profile.id]
        compare_profile_object(
            remote_profile,
            post_local_profile,
            {"sync_hash", "profile_path", "info_path"},
        )
        compare_profile_content(remote_profile, post_local_profile, {})


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_by_id_and_path(profiles_lrc):
    pre_local, _, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == pre_local

    cmd_args = [
        "profile",
        "sync",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].profile_path.parent),
    ]
    for idx, change_type in enumerate(changes):
        if change_type in {ChangeType.CREATE_REMOTE, ChangeType.CREATE_LOCAL}:
            continue
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(random.choice(changes[change_type])[0].profile_path.parent)
        else:
            cmd_args.append("--id")
            cmd_args.append(random.choice(changes[change_type])[0].id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Syncing 4 changes with Iru..." in result.stdout
    assert len(re.findall(r"created in Iru successfully with new Iru ID:", result.stdout)) == 1
    assert len(re.findall(r"updated in Iru", result.stdout)) == 1
    assert len(re.findall(r"created in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"deleted in Iru successfully", result.stdout)) == 0
    assert "Sync operation complete!" in result.stdout
    assert "Pushed Item Summary" in result.stdout
    assert "Pulled Item Summary" in result.stdout
    assert len(re.findall(r"Created\s+1\s+0", result.stdout)) == 2
    assert len(re.findall(r"Updated\s+1\s+0", result.stdout)) == 2
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    post_local = Repository.load_path(model=CustomProfile)

    # Check that the created profile's id has been updated along with dependent fields
    old_created_profile = changes[ChangeType.CREATE_LOCAL][0][0]
    new_created_profile = post_local[str(old_created_profile.profile_path)]
    assert old_created_profile.id not in post_local
    assert new_created_profile.id in post_local
    compare_profile_object(
        old_created_profile,
        new_created_profile,
        {"id", "created_at", "updated_at", "sync_hash", "profile", "mdm_identifier"},
    )
    compare_profile_content(
        old_created_profile,
        new_created_profile,
        {"PayloadUUID", "PayloadIdentifier"},
    )

    # Check that the pushed profiles have only changed in expected ways. The changes in this case are
    # because the PayloadDisplayName is set to match the info file name field
    updated_id = changes[ChangeType.UPDATE_LOCAL][0][0].id
    compare_profile_object(pre_local[updated_id], post_local[updated_id], {"updated_at", "sync_hash", "profile"})
    compare_profile_content(pre_local[updated_id], post_local[updated_id], {"PayloadDisplayName"})

    # Check that the pulled profiles match the remote
    for change_type in {ChangeType.CREATE_REMOTE, ChangeType.UPDATE_REMOTE}:
        remote_profile = changes[change_type][0][1]
        post_local_profile = post_local[remote_profile.id]
        compare_profile_object(
            remote_profile,
            post_local_profile,
            {"sync_hash", "profile_path", "info_path"},
        )
        compare_profile_content(remote_profile, post_local_profile, {})

    # Check that the conflicting profile has not changed.
    conflicting_id = changes[ChangeType.CONFLICT][0][0].id
    assert pre_local[conflicting_id] == post_local[conflicting_id]


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["profile", "sync", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in" in result.stderr


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_invalid_path():
    missing_path = Path("profiles/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["profile", "sync", "--path", str(missing_path)])
    assert result.exit_code == 2
    assert "does not exist." in normalize_output(result.stderr)


@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [
        pytest.param(None, None, "info.plist", id="default-plist"),
        pytest.param(None, "yaml", "info.yaml", id="flag"),
        pytest.param("json", None, "info.json", id="env"),
        pytest.param("yaml", "plist", "info.plist", id="flag-over-env"),
    ],
)
@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_sync_writes_new_profile_in_configured_format(profiles_lrc, monkeypatch, env, flag, expected):
    # Sync's pull half creates a profile absent locally in the configured format.
    _, _, changes = profiles_lrc
    create_id = changes[ChangeType.CREATE_REMOTE][0][1].id
    if env is not None:
        monkeypatch.setenv(INFO_FORMAT_ENV, env)
    args = ["profile", "sync", "--id", str(create_id)]
    if flag is not None:
        args += ["--info-format", flag]

    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output

    repo = Repository.load_path(model=CustomProfile)
    assert repo[create_id].info.path.name == expected


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_reformat_converts_unchanged_profile_and_preserves_local_fields(profiles_lrc):
    _, _, changes = profiles_lrc
    member = changes[ChangeType.NONE][0][0]
    member.info.ensure_blueprints = [BlueprintAssignment(blueprint=str(uuid4()))]
    member.info.write()
    old_path = member.info_path
    # Pick a target that differs from the member's current format so the reformat happens.
    target = "yaml" if old_path.suffix == ".plist" else "plist"

    result = runner.invoke(app, ["profile", "sync", "--id", member.id, "--info-format", target, "--reformat"])
    assert result.exit_code == 0, result.output
    assert "Reformatted 1 info file to" in result.stdout

    reloaded = Repository.load_path(model=CustomProfile)[member.id]
    assert reloaded.info_path == old_path.with_name(f"info.{target}")
    assert not old_path.exists()
    assert reloaded.info.sync_hash == member.info.sync_hash
    assert reloaded.info.ensure_blueprints == member.info.ensure_blueprints


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_reformat_requires_info_format_flag():
    result = runner.invoke(app, ["profile", "sync", "--all", "--reformat"])
    assert result.exit_code == 2
    assert "--reformat requires --info-format" in result.stderr


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_reformat_dry_run_reports_without_writing(profiles_lrc):
    local, _, changes = profiles_lrc
    member = changes[ChangeType.NONE][0][0]
    # Pick a target that differs from the member's current format so the reformat happens.
    target = "yaml" if member.info_path.suffix == ".plist" else "plist"

    result = runner.invoke(
        app, ["profile", "sync", "--id", member.id, "--info-format", target, "--reformat", "--dry-run"]
    )
    assert result.exit_code == 0, result.output

    assert "Would have reformatted profile:" in result.stdout
    assert "already up to date" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout
    assert Repository.load_path(model=CustomProfile) == local
