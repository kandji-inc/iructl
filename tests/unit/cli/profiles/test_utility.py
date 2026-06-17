import itertools
import json
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import pytest
import requests
import typer

from iructl._cli.common import SyncResults
from iructl._cli.utility import (
    InvalidRemoteMember,
    filter_changes,
    filter_invalid_members,
    get_local_members,
    get_remote_members,
    load_members_by_id,
    load_members_by_path,
    record_invalid_members,
    reformat_members,
    save_report,
)
from iructl._constants import APP_NAME
from iructl._diff import ChangeType
from iructl.exceptions import InvalidProfileError
from iructl.repository import PROFILE_RUNS_ON_PARAMS, BlueprintAssignment, CustomProfile, InfoFormat, Repository


@pytest.mark.parametrize(
    ("pass_repo_path", "num_profiles", "add_invalid_id", "expectation", "log_message"),
    [
        pytest.param(False, 0, False, nullcontext(0), "", id="no_repo-no_ids"),
        pytest.param(
            False,
            1,
            False,
            pytest.raises(typer.Exit),
            "does not appear to be a",
            id="no_repo-one_id",
        ),
        pytest.param(True, 0, False, nullcontext(0), "", id="repo-no_ids"),
        pytest.param(True, 1, False, nullcontext(1), "", id="repo-one_id"),
        pytest.param(True, 2, False, nullcontext(2), "", id="repo-multiple_ids"),
        pytest.param(
            True, 2, True, pytest.raises(typer.BadParameter), "not found in local repository", id="repo-invalid_id"
        ),
    ],
)
def test_get_profiles_from_repo_by_id(
    caplog,
    profiles_repo: Path,
    profiles_repo_obj: Repository,
    pass_repo_path,
    num_profiles,
    add_invalid_id,
    expectation,
    log_message,
):
    # Get profile ids from repo
    profile_ids = list(itertools.islice(profiles_repo_obj, num_profiles))
    assert len(profile_ids) == num_profiles  # Sanity check for num_profiles

    if add_invalid_id:
        profile_ids.append("invalid_id")

    with expectation as expected_length:
        result = load_members_by_id(
            repo_path=profiles_repo if pass_repo_path else Path("/var/tmp"),
            member_type=CustomProfile,
            member_ids=profile_ids,
        )
        assert len(list(result)) == expected_length

    assert log_message in caplog.text


@pytest.mark.parametrize(
    ("num_profiles", "add_invalid_path", "expectation", "log_message"),
    [
        pytest.param(
            0,
            False,
            nullcontext(0),
            "",
            id="no_paths",
        ),
        pytest.param(
            1,
            False,
            nullcontext(1),
            "",
            id="one_paths",
        ),
        pytest.param(
            2,
            False,
            nullcontext(2),
            "",
            id="multiple_paths",
        ),
        pytest.param(
            0,
            True,
            pytest.raises(typer.Exit),
            "An error occurred while loading",
            id="one_invalid_path",
        ),
        pytest.param(
            1,
            True,
            pytest.raises(typer.Exit),
            "An error occurred while loading",
            id="one_path-one_invalid_path",
        ),
    ],
)
def test_load_profiles_by_path(
    caplog, profiles_repo_obj: Repository, num_profiles, add_invalid_path, expectation, log_message
):
    # Get profile from repo

    profile_paths = [
        profile.profile_path
        for profile in itertools.islice(profiles_repo_obj.values(), num_profiles + int(add_invalid_path))
    ]
    assert len(profile_paths) == num_profiles + int(add_invalid_path)  # Sanity check for num_profiles

    if add_invalid_path:
        with profile_paths[-1].open("wb") as profile:
            profile.write(b"{invalid profile}")

    with expectation as expected_length:
        result = load_members_by_path(member_type=CustomProfile, member_paths=profile_paths)
        assert len(list(result)) == expected_length

    assert log_message in caplog.text


@pytest.mark.parametrize(
    ("pass_all", "num_paths", "num_ids", "num_duplicates", "expected_count"),
    [
        pytest.param(False, 0, 0, 0, 0, id="no_paths-no_ids"),
        pytest.param(False, 1, 0, 0, 1, id="one_path-no_ids"),
        pytest.param(False, 0, 1, 0, 1, id="no_paths-one_id"),
        pytest.param(False, 2, 1, 1, 2, id="remove_duplicates"),
        pytest.param(True, 0, 0, 0, 10, id="all"),
    ],
)
@pytest.mark.profile_count(10)
def test_get_local_profiles(
    profiles_repo: Path,
    profiles_repo_obj: Repository,
    pass_all,
    num_paths,
    num_ids,
    num_duplicates,
    expected_count,
):
    profiles_list = list(profiles_repo_obj.values())
    profile_paths = [profile.profile_path for profile in profiles_list][:num_paths]
    profile_ids = list(profiles_repo_obj)[num_paths - num_duplicates : num_ids]
    if len(profile_ids) != num_ids - num_duplicates or len(profile_paths) != num_paths:
        pytest.fail("Invalid test setup, unexpected number of profiles")

    result = get_local_members(
        repo=profiles_repo,
        member_type=CustomProfile,
        all_members=pass_all,
        member_paths=profile_paths,
        member_ids=profile_ids,
    )
    assert len(result) == expected_count

    for profile_path in profile_paths:
        assert result.get(str(profile_path)) is not None
    for profile_id in profile_ids:
        assert result.get(profile_id) is not None


@pytest.mark.parametrize(
    ("all_profiles", "num_ids", "add_missing", "expectation", "log_message"),
    [
        pytest.param(True, 0, False, nullcontext(9), "", id="all"),
        pytest.param(True, 2, False, nullcontext(9), "", id="all_with_ids"),
        pytest.param(False, 0, False, nullcontext(0), "", id="no_ids"),
        pytest.param(False, 2, False, nullcontext(2), "", id="with_ids"),
        pytest.param(False, 2, True, nullcontext(2), "", id="with_missing_ids"),
    ],
)
@pytest.mark.usefixtures("patch_profiles_endpoints", "profiles_lrc")
def test_get_remote_profiles(
    caplog,
    config,
    profiles_response,
    all_profiles,
    num_ids,
    add_missing,
    expectation,
    log_message,
):
    ids = [profile.id for profile in profiles_response.results[:num_ids]]
    if add_missing:
        ids.append(str(uuid4()))

    with expectation as expected_length:
        profiles, invalid = get_remote_members(
            config=config,
            member_type=CustomProfile,
            all_members=all_profiles,
            member_ids=ids,
            raise_on_missing=False,
        )
        assert len(profiles) == expected_length
        assert invalid == []

    assert log_message in caplog.text


def test_get_remote_profiles_runs_on_missing(monkeypatch, config, profiles_response):
    """Check that if the API returns a profile with no runs_on parameters set, they default to True"""
    profile = profiles_response.results[0]

    def fake_list(self):
        for param in PROFILE_RUNS_ON_PARAMS:
            setattr(profile, param, False)
        return profiles_response

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.list", fake_list)

    result_repo = get_remote_members(
        config=config,
        member_type=CustomProfile,
        member_ids=[profile.id],
    ).repo
    assert all(getattr(result_repo[profile.id].info, param) for param in PROFILE_RUNS_ON_PARAMS)


def test_get_remote_profiles_connection_error(monkeypatch, caplog, config):
    def fake_list(self):
        raise requests.exceptions.ConnectionError("Connection Error")

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.list", fake_list)
    with pytest.raises(typer.Exit):
        get_remote_members(
            config=config,
            member_type=CustomProfile,
            all_members=True,
        )

    assert "An error occurred while fetching: Connection Error" in caplog.text


def test_get_remote_profiles_skips_invalid_member(monkeypatch, caplog, config, profiles_response):
    """A member that fails conversion is excluded from the repo and reported as invalid."""
    target = profiles_response.results[0]
    real_from_api_payload = CustomProfile.from_api_payload

    def fake_list(self):
        return profiles_response

    def fake_from_api_payload(payload):
        if payload.id == target.id:
            raise InvalidProfileError("bad shape")
        return real_from_api_payload(payload)

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.list", fake_list)
    monkeypatch.setattr(CustomProfile, "from_api_payload", fake_from_api_payload)

    repo, invalid = get_remote_members(config=config, member_type=CustomProfile, all_members=True)

    assert target.id not in repo
    assert len(repo) == len(profiles_response.results) - 1
    assert invalid == [InvalidRemoteMember(id=target.id, name=target.name, error="bad shape")]
    assert f"Skipping invalid remote member '{target.name}' ({target.id}): bad shape" in caplog.text


def test_get_remote_profiles_skips_member_failing_validation(monkeypatch, caplog, config, profiles_response):
    """A pydantic ValidationError during conversion is caught and reported with field paths."""
    target = profiles_response.results[0]
    real_from_api_payload = CustomProfile.from_api_payload

    def fake_list(self):
        return profiles_response

    def fake_from_api_payload(payload):
        if payload.id == target.id:
            CustomProfile.model_validate({})  # raises ValidationError
        return real_from_api_payload(payload)

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.list", fake_list)
    monkeypatch.setattr(CustomProfile, "from_api_payload", fake_from_api_payload)

    repo, invalid = get_remote_members(config=config, member_type=CustomProfile, all_members=True)

    assert target.id not in repo
    assert len(invalid) == 1
    assert invalid[0].id == target.id
    assert "info: Field required" in invalid[0].error
    assert "Skipping invalid remote member" in caplog.text


def test_excluded_invalid_member_is_absent_from_change_classification(monkeypatch, config, profiles_response):
    """Excluding an invalid member's local counterpart keeps it out of every change bucket.

    Without exclusion the counterpart is classified as locally new (CREATE_LOCAL),
    which a push would turn into a duplicate create on the tenant.
    """
    target = profiles_response.results[0]
    local_repo = Repository[CustomProfile](
        CustomProfile.from_api_payload(payload) for payload in profiles_response.results
    )
    real_from_api_payload = CustomProfile.from_api_payload

    def fake_list(self):
        return profiles_response

    def fake_from_api_payload(payload):
        if payload.id == target.id:
            raise InvalidProfileError("bad shape")
        return real_from_api_payload(payload)

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.list", fake_list)
    monkeypatch.setattr(CustomProfile, "from_api_payload", fake_from_api_payload)

    remote_repo, invalid = get_remote_members(config=config, member_type=CustomProfile, all_members=True)

    naive_changes = filter_changes(local_repo=local_repo, remote_repo=remote_repo)
    created_locally = {local.id for local, _ in naive_changes[ChangeType.CREATE_LOCAL] if local is not None}
    assert target.id in created_locally  # the duplicate-create hazard exclusion exists to prevent

    local_repo, _ = filter_invalid_members(local_repo, invalid)

    changes = filter_changes(local_repo=local_repo, remote_repo=remote_repo)
    classified_ids = {
        member.id for entries in changes.values() for pair in entries for member in pair if member is not None
    }
    assert target.id not in classified_ids


def test_record_invalid_members_serializes_with_reason():
    """Synthesized failure entries report no object, carry the conversion error, and don't break the summary."""
    results = SyncResults[CustomProfile]()
    record_invalid_members(results, [InvalidRemoteMember(id="abc-123", name="Bad Profile", error="bad shape")])

    report = results.format_report()

    assert report["status"] == "failure"
    entry = report["failure"][0]
    assert entry["id"] == "abc-123"
    assert entry["object"] is None
    assert entry["reason"] == "bad shape"
    assert "abc-123" in results.format_summary()


def test_filter_changes(profiles_lrc):
    local_repo, remote_repo, expected_changes = profiles_lrc
    filtered_changes = filter_changes(local_repo, remote_repo)
    for change_type in ChangeType:
        result = sorted(filtered_changes[change_type], key=lambda x: x[0].id if x[0] is not None else "")
        expected = sorted(expected_changes[change_type], key=lambda x: x[0].id if x[0] is not None else "")
        assert len(result) == len(expected)
        assert result == expected


def _reload_profiles(repo: Repository[CustomProfile]) -> Repository[CustomProfile]:
    assert repo.root is not None
    return Repository.load_path(model=CustomProfile, path=repo.root)


class TestReformatMembers:
    @pytest.fixture
    def make_repo(self, iructl_repo, profile_directory_factory):
        """Build a profiles repo with one member per given info format and load it."""

        def _make(*formats: InfoFormat) -> Repository[CustomProfile]:
            profiles_path = iructl_repo / "profiles"
            for info_format in formats:
                profile_directory_factory(path=profiles_path / f"Profile {uuid4()}", info_format=info_format)
            return Repository.load_path(model=CustomProfile, path=profiles_path)

        return _make

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            pytest.param(InfoFormat.PLIST, InfoFormat.YAML, id="plist-to-yaml"),
            pytest.param(InfoFormat.YAML, InfoFormat.JSON, id="yaml-to-json"),
            pytest.param(InfoFormat.JSON, InfoFormat.PLIST, id="json-to-plist"),
        ],
    )
    def test_rewrites_info_file_and_removes_old_one(self, make_repo, source, target):
        repo = make_repo(source)
        member = next(iter(repo.values()))
        old_path = member.info_path

        reformatted = reformat_members(repo, target)

        assert [m.id for m in reformatted] == [member.id]
        assert not old_path.exists()
        reloaded = _reload_profiles(repo)[member.id]
        assert reloaded.info.path == old_path.with_name(f"info.{target}")
        assert reloaded.info.format is target
        assert reloaded.info.name == member.info.name

    def test_preserves_local_only_fields(self, make_repo):
        repo = make_repo(InfoFormat.PLIST)
        member = next(iter(repo.values()))
        member.info.sync_hash = member.diff_hash
        member.info.ensure_blueprints = [BlueprintAssignment(blueprint=str(uuid4()))]
        member.info.write()
        repo = _reload_profiles(repo)

        reformat_members(repo, InfoFormat.YAML)

        reloaded = _reload_profiles(repo)[member.id]
        assert reloaded.info.sync_hash == member.info.sync_hash
        assert reloaded.info.ensure_blueprints == member.info.ensure_blueprints

    @pytest.mark.parametrize(
        "suffix",
        [
            pytest.param(".yaml", id="same-format"),
            pytest.param(".yml", id="yml-alias"),
        ],
    )
    def test_skips_suffix_already_mapping_to_target(self, make_repo, suffix):
        repo = make_repo(InfoFormat.YAML)
        old_path = next(iter(repo.values())).info_path
        info_path = old_path.rename(old_path.with_suffix(suffix))
        repo = _reload_profiles(repo)

        assert reformat_members(repo, InfoFormat.YAML) == []
        assert info_path.exists()

    def test_failed_write_removes_partial_file_and_keeps_old_one(self, make_repo, monkeypatch):
        repo = make_repo(InfoFormat.PLIST)
        old_path = next(iter(repo.values())).info_path

        def partial_write(info):
            info.path.write_text("partial")
            raise OSError("disk full")

        monkeypatch.setattr("iructl.repository.info.InfoFile.write", partial_write)

        with pytest.raises(OSError, match="disk full"):
            reformat_members(repo, InfoFormat.YAML)

        assert old_path.exists()
        assert not old_path.with_name("info.yaml").exists()

    def test_dry_run_reports_without_writing(self, make_repo):
        repo = make_repo(InfoFormat.PLIST)
        member = next(iter(repo.values()))
        old_path = member.info_path

        reformatted = reformat_members(repo, InfoFormat.YAML, dry_run=True)

        assert [m.id for m in reformatted] == [member.id]
        assert old_path.exists()
        assert not old_path.with_name("info.yaml").exists()

    def test_excludes_given_ids(self, make_repo):
        repo = make_repo(InfoFormat.PLIST, InfoFormat.PLIST)
        excluded, kept = list(repo.values())

        reformatted = reformat_members(repo, InfoFormat.JSON, exclude_ids={excluded.id})

        assert [m.id for m in reformatted] == [kept.id]
        assert excluded.info_path.exists()
        assert not kept.info_path.with_name("info.plist").exists()


def test_save_report(profile_sync_results, tmp_path):
    """Test saving the sync report to a file."""
    report_path = tmp_path / f"{APP_NAME}_report.json"
    save_report(results=profile_sync_results, report_path=report_path)
    assert report_path.exists()
    original_size = report_path.stat().st_size
    with report_path.open("r") as f:
        data = json.load(f)
        assert len(data) == 1
        assert "success" in data[0]
        assert "failure" in data[0]
        assert len(data[0]["success"]) == len(profile_sync_results.success)
        assert len(data[0]["failure"]) == len(profile_sync_results.failure)

    save_report(results=profile_sync_results, report_path=report_path)
    assert original_size != report_path.stat().st_size
    with report_path.open("r") as f:
        data = json.load(f)
        assert len(data) == 2
