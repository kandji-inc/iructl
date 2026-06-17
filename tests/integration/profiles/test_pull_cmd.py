import random
import re
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_NAME, INFO_FORMAT_ENV, ROOT_MARKER
from iructl._diff import ChangeType
from iructl.repository import SUFFIX_MAP, BlueprintAssignment, CustomProfile, InfoFormat, Repository
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
    result = runner.invoke(app, ["profile", "pull", "--help"])
    assert result.exit_code == 0
    assert f"Usage: {APP_NAME} profile pull" in result.stdout
    assert "Pull remote custom profile changes from Iru." in result.stdout
    assert "Made with ❤ by Iru" in result.stdout


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_dry_run(profiles_lrc):
    local, _, _ = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "pull", "--all", "--dry-run"])
    assert result.exit_code == 0

    # Check output
    assert "Running in dry-run mode" in result.stdout
    assert len(re.findall(r"Would have created profile:", result.stdout)) == 1
    assert len(re.findall(r"Would have updated profile:", result.stdout)) == 1
    assert len(re.findall(r"Would have deleted profile:", result.stdout)) == 0
    assert "Would have deleted profile:" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout

    # Check no profiles have changed
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_clean(profiles_lrc):
    local, _, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "pull", "--all", "--clean"])
    assert result.exit_code == 0

    # Check output
    assert "Pulling 3 changes from Iru..." in result.stdout
    assert len(re.findall(r"created in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"deleted in local repo successfully", result.stdout)) == 1
    assert "Pull operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+1\s+0", result.stdout)
    assert re.search(r"Deleted\s+1\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Local only updates\s+1", result.stdout)
    assert re.search(r"Conflicting changes\s+1", result.stdout)

    repo = Repository.load_path(model=CustomProfile)

    # Check that the created profile's matches the remote
    remote_profile_from_changes = changes[ChangeType.CREATE_REMOTE][0][1]
    new_created_local_profile = repo[remote_profile_from_changes.id]
    compare_profile_object(remote_profile_from_changes, new_created_local_profile, {"sync_hash"})
    compare_profile_content(remote_profile_from_changes, new_created_local_profile, {})

    # Check that the updated profile has not changed.
    remote_profile = changes[ChangeType.UPDATE_REMOTE][0][1]
    compare_profile_object(remote_profile, repo[remote_profile.id], {"sync_hash"})
    compare_profile_content(remote_profile, repo[remote_profile.id], {})

    # Check that the deleted profile is still not in the repo
    deleted_id = changes[ChangeType.CREATE_LOCAL][0][0].id
    assert deleted_id not in repo


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_all_force(profiles_lrc):
    local, _, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    result = runner.invoke(app, ["profile", "pull", "--all", "--force"])
    assert result.exit_code == 0

    # Check output
    assert "Pulling 4 changes from Iru..." in result.stdout
    assert len(re.findall(r"created in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 3
    assert len(re.findall(r"deleted in local repo successfully", result.stdout)) == 0
    assert "Pull operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+3\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+5", result.stdout)
    assert re.search(r"Local only item\s+1", result.stdout)
    assert "Local only updates" not in result.stdout
    assert "Conflicting changes" not in result.stdout

    repo = Repository.load_path(model=CustomProfile)

    # Check that the created profile's matches the remote
    remote_profile_from_changes = changes[ChangeType.CREATE_REMOTE][0][1]
    new_created_local_profile = repo[remote_profile_from_changes.id]
    compare_profile_object(
        remote_profile_from_changes,
        new_created_local_profile,
        {"sync_hash", "profile_path", "info_path"},
    )
    compare_profile_content(remote_profile_from_changes, new_created_local_profile, {})

    # Check that the updated profile has not changed.
    for change_type in (ChangeType.UPDATE_REMOTE, ChangeType.CONFLICT):
        remote_profile = changes[change_type][0][1]
        compare_profile_object(remote_profile, repo[remote_profile.id], {"sync_hash"})
        compare_profile_content(remote_profile, repo[remote_profile.id], {})

    # check that the update_remote profile has been reverted to the local state
    remote_profile = changes[ChangeType.UPDATE_LOCAL][0][1]
    compare_profile_object(remote_profile, repo[remote_profile.id], {"sync_hash"})
    compare_profile_content(remote_profile, repo[remote_profile.id], {})


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_by_id_and_path(profiles_lrc):
    local, _, changes = profiles_lrc

    # Sanity check that local repo matches disk
    assert Repository.load_path(model=CustomProfile) == local

    cmd_args = [
        "profile",
        "pull",
        "--force",
        "--id",
        changes[ChangeType.CREATE_REMOTE][0][1].id,
        "--path",
        str(changes[ChangeType.CREATE_LOCAL][0][0].profile_path.parent),
    ]
    for idx, change_type in enumerate(
        {k for k in changes if k not in {ChangeType.CREATE_REMOTE, ChangeType.CREATE_LOCAL}}
    ):
        if change_type in {ChangeType.CREATE_REMOTE, ChangeType.CREATE_LOCAL}:
            continue
        if idx % 2 == 1:
            cmd_args.append("--path")
            cmd_args.append(str(random.choice(changes[change_type])[0].profile_path.parent))
        else:
            cmd_args.append("--id")
            cmd_args.append(random.choice(changes[change_type])[0].id)
    result = runner.invoke(app, cmd_args)
    assert result.exit_code == 0

    # Check output
    assert "Pulling 4 changes from Iru..." in result.stdout
    assert len(re.findall(r"created in local repo successfully", result.stdout)) == 1
    assert len(re.findall(r"updated in local repo", result.stdout)) == 3
    assert len(re.findall(r"deleted in local repo successfully", result.stdout)) == 0
    assert "Pull operation complete!" in result.stdout
    assert "Updated Item Summary" in result.stdout
    assert re.search(r"Created\s+1\s+0", result.stdout)
    assert re.search(r"Updated\s+3\s+0", result.stdout)
    assert "Skipped Item Summary" in result.stdout
    assert re.search(r"Already up to date\s+1", result.stdout)
    assert re.search(r"Local only item\s+1", result.stdout)
    assert "Updated on remote only" not in result.stdout

    repo = Repository.load_path(model=CustomProfile)

    # Check that the updated profile has not changed.
    for change_type in (ChangeType.UPDATE_LOCAL, ChangeType.CONFLICT, ChangeType.UPDATE_REMOTE):
        remote_profile = changes[change_type][0][1]
        compare_profile_object(remote_profile, repo[remote_profile.id], {"sync_hash"})
        compare_profile_content(remote_profile, repo[remote_profile.id], {})


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_invalid_id():
    random_id = str(uuid4())
    result = runner.invoke(app, ["profile", "pull", "--force", "--id", random_id])
    assert result.exit_code == 2
    assert "Repository member with ID" in result.stderr
    assert f"{random_id} not found in local or remote" in result.stderr


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_invalid_path():
    missing_path = Path("profiles/invalid")
    assert not missing_path.exists()

    result = runner.invoke(app, ["profile", "pull", "--force", "--path", str(missing_path)])
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
def test_pull_writes_new_profile_in_configured_format(profiles_lrc, monkeypatch, env, flag, expected):
    # A profile present in Iru but not yet on disk is created in the configured format.
    _, _, changes = profiles_lrc
    create_id = changes[ChangeType.CREATE_REMOTE][0][1].id
    if env is not None:
        monkeypatch.setenv(INFO_FORMAT_ENV, env)
    args = ["profile", "pull", "--id", str(create_id)]
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

    result = runner.invoke(app, ["profile", "pull", "--id", member.id, "--info-format", target, "--reformat"])
    assert result.exit_code == 0, result.output
    assert "Reformatted 1 info file to" in result.stdout
    assert "Nothing to do." not in result.stdout

    reloaded = Repository.load_path(model=CustomProfile)[member.id]
    assert reloaded.info_path == old_path.with_name(f"info.{target}")
    assert not old_path.exists()
    assert reloaded.info.sync_hash == member.info.sync_hash
    assert reloaded.info.ensure_blueprints == member.info.ensure_blueprints


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_reformat_applies_to_member_updated_in_same_run(profiles_lrc):
    # Guards that the post-pass serializes the merged member, not stale pre-pull data.
    _, _, changes = profiles_lrc
    local, remote = changes[ChangeType.UPDATE_REMOTE][0]
    old_path = local.info_path
    # Pick a target that differs from the member's current format so the reformat happens.
    target = "yaml" if old_path.suffix == ".plist" else "plist"

    result = runner.invoke(app, ["profile", "pull", "--id", local.id, "--info-format", target, "--reformat"])
    assert result.exit_code == 0, result.output

    reloaded = Repository.load_path(model=CustomProfile)[local.id]
    assert reloaded.info_path == old_path.with_name(f"info.{target}")
    assert not old_path.exists()
    compare_profile_object(remote, reloaded, {"sync_hash"})
    compare_profile_content(remote, reloaded, {})


@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc", "iructl_repo_cd")
def test_reformat_all_converts_every_info_file():
    result = runner.invoke(app, ["profile", "pull", "--all", "--info-format", "yaml", "--reformat"])
    assert result.exit_code == 0, result.output

    for member in Repository.load_path(model=CustomProfile).values():
        assert member.info_path.name == "info.yaml"


@pytest.mark.parametrize(
    ("env", "config"),
    [
        pytest.param(None, None, id="unset"),
        pytest.param("yaml", None, id="env"),
        pytest.param(None, "yaml", id="config"),
    ],
)
@pytest.mark.usefixtures("patch_profiles_endpoints")
def test_reformat_requires_info_format_flag(profiles_lrc, iructl_repo_cd, monkeypatch, env, config):
    # env- or config-sourced info_format must not satisfy --reformat; only the flag does.
    local, _, _ = profiles_lrc
    if env is not None:
        monkeypatch.setenv(INFO_FORMAT_ENV, env)
    if config is not None:
        (iructl_repo_cd / ROOT_MARKER).write_text(f"info_format: {config}\n")

    result = runner.invoke(app, ["profile", "pull", "--all", "--reformat"])

    assert result.exit_code == 2
    assert "--reformat requires --info-format" in result.stderr
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_reformat_dry_run_reports_without_writing(profiles_lrc):
    local, _, changes = profiles_lrc
    member = changes[ChangeType.NONE][0][0]
    # Pick a target that differs from the member's current format so the reformat happens.
    target = "yaml" if member.info_path.suffix == ".plist" else "plist"

    result = runner.invoke(
        app, ["profile", "pull", "--id", member.id, "--info-format", target, "--reformat", "--dry-run"]
    )
    assert result.exit_code == 0, result.output

    assert "Would have reformatted profile:" in result.stdout
    assert "already up to date" not in result.stdout
    assert "Dry run complete. No changes were made." in result.stdout
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_reformat_dry_run_excludes_member_pending_delete(profiles_lrc):
    local, _, changes = profiles_lrc
    deleted = changes[ChangeType.CREATE_LOCAL][0][0]
    # Pick a target the deleted member is not in, so only the exclusion keeps it out of the output.
    target = InfoFormat.YAML if deleted.info_path.suffix == ".plist" else InfoFormat.PLIST

    result = runner.invoke(
        app, ["profile", "pull", "--all", "--clean", "--info-format", target, "--reformat", "--dry-run"]
    )
    assert result.exit_code == 0, result.output

    normalized = normalize_output(result.stdout)
    assert f"Would have deleted profile: {deleted.name} ({deleted.id})" in normalized
    assert f"Would have reformatted profile: {deleted.name}" not in normalized
    expected = [m for m in local.values() if m.id != deleted.id and SUFFIX_MAP.get(m.info_path.suffix) is not target]
    assert normalized.count("Would have reformatted profile:") == len(expected)
    assert Repository.load_path(model=CustomProfile) == local


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_reformat_warns_when_interrupted(profiles_lrc, monkeypatch):
    _, _, changes = profiles_lrc
    member = changes[ChangeType.NONE][0][0]
    # Pick a target that differs from the member's current format so the reformat happens.
    target = "yaml" if member.info_path.suffix == ".plist" else "plist"

    def failing_write(info):
        raise OSError("disk full")

    monkeypatch.setattr("iructl.repository.info.InfoFile.write", failing_write)

    result = runner.invoke(app, ["profile", "pull", "--id", member.id, "--info-format", target, "--reformat"])

    assert result.exit_code != 0
    assert f"Reformat to {target} was interrupted" in normalize_output(result.stderr)


@pytest.mark.usefixtures("patch_profiles_endpoints", "iructl_repo_cd")
def test_pull_preserves_existing_profile_format(profiles_lrc):
    # Pulling an update with a different --info-format must not reformat the on-disk file.
    _, _, changes = profiles_lrc
    update_id = changes[ChangeType.UPDATE_REMOTE][0][0].id

    original_name = Repository.load_path(model=CustomProfile)[update_id].info.path.name
    # Pick a target that differs from the existing format so the assertion is meaningful.
    target = "yaml" if original_name == "info.plist" else "plist"

    result = runner.invoke(app, ["profile", "pull", "--id", str(update_id), "--info-format", target])
    assert result.exit_code == 0, result.output

    assert Repository.load_path(model=CustomProfile)[update_id].info.path.name == original_name
