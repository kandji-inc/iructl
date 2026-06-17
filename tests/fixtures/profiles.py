import contextlib
import io
import itertools
import json
import plistlib
import random
import sys
import textwrap
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from ruamel.yaml import YAML

from iructl._diff import ChangesDict, ChangeType
from iructl.api import CustomProfilePayload, PayloadList
from iructl.repository import (
    CustomProfile,
    InfoFormat,
    Mobileconfig,
    ProfileInfoFile,
    Repository,
)


def profile_to_response(profile: CustomProfile) -> CustomProfilePayload:
    """Build the API response payload for a CustomProfile (test inverse of from_api_payload)."""
    data = profile.info.model_dump(mode="json", exclude={"sync_hash"})
    if data.get("updated_at") is None:
        data["updated_at"] = data["created_at"]
    return CustomProfilePayload.model_validate(data | {"profile": profile.profile.content})


# --- mobileconfig fixtures ---
@pytest.fixture
def mobileconfig_data_factory():
    """Return a function that generates the data necessary to create a profile using specified fields and random data as filler."""

    def random_profile_data(
        name: str | None = None,
        id: str | None = None,
        mdm_identifier: str | None = None,
        payload_id: str | None = None,
        payload_key: str = "TestKey",
        payload_value: str = "TestValue",
    ) -> dict[str, str | bool]:
        profile_id = id or str(uuid4())
        return {
            "id": profile_id,
            "name": name or profile_id,
            "mdm_identifier": mdm_identifier or f"com.kandji.profile.custom.{profile_id}",
            "payload_id": payload_id or str(uuid4()),
            "payload_key": payload_key,
            "payload_value": payload_value,
        }

    return random_profile_data


@pytest.fixture
def mobileconfig_data(mobileconfig_data_factory) -> dict[str, str | bool]:
    """Return a dictionary with random mobileconfig data."""
    return mobileconfig_data_factory()


@pytest.fixture
def mobileconfig_content_factory() -> Callable[..., str]:
    """Return a function that generates a mobileconfig file content with the given parameters."""

    def profile(
        *,
        id: str,
        name: str,
        mdm_identifier: str,
        payload_id: str,
        payload_key: str = "TestKey",
        payload_value: str = "TestValue",
    ) -> str:
        return textwrap.dedent(
            f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
                <key>PayloadDisplayName</key>
                <string>{name}</string>
                <key>PayloadIdentifier</key>
                <string>{mdm_identifier}</string>
                <key>PayloadType</key>
                <string>Configuration</string>
                <key>PayloadUUID</key>
                <string>{id}</string>
                <key>PayloadVersion</key>
                <integer>1</integer>
                <key>PayloadContent</key>
                <array>
                    <dict>
                        <key>PayloadDisplayName</key>
                        <string>Generated Test Payload</string>
                        <key>PayloadIdentifier</key>
                        <string>com.iru.syseng.test.{payload_id}</string>
                        <key>PayloadType</key>
                        <string>com.iru.syseng.test</string>
                        <key>PayloadUUID</key>
                        <string>{payload_id}</string>
                        <key>PayloadVersion</key>
                        <integer>1</integer>
                        <key>{payload_key}</key>
                        <string>{payload_value}</string>
                    </dict>
                </array>
            </dict>
            </plist>
            """
        )

    return profile


@pytest.fixture
def mobileconfig_content(mobileconfig_data, mobileconfig_content_factory) -> str:
    """Return the content of a mobileconfig file."""
    return mobileconfig_content_factory(**mobileconfig_data)


@pytest.fixture(params=[plistlib.FMT_XML, plistlib.FMT_BINARY], ids=["xml", "binary"])
def mobileconfig_file(request, tmp_path, mobileconfig_content) -> Path:
    """Return the path to a valid mobileconfig file."""
    plist_data = plistlib.loads(mobileconfig_content.encode("utf-8"))
    plist_path = tmp_path / "test.mobileconfig"
    with plist_path.open("wb") as file:
        plistlib.dump(plist_data, file, fmt=request.param)
    return plist_path


# --- profile info file fixtures ---
@pytest.fixture
def profile_info_data_factory():
    """Return a function that generates the data necessary to create a profile info file using specified fields and random data as filler."""

    def factory(
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        mdm_identifier: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        runs_on_mac: bool | None = None,
        runs_on_iphone: bool | None = None,
        runs_on_ipad: bool | None = None,
        runs_on_tv: bool | None = None,
        runs_on_vision: bool | None = None,
    ) -> dict[str, str | bool]:
        profile_id = id or str(uuid4())
        info = {
            "id": profile_id,
            "name": name or "Test Profile",
            "active": active or random.choice((False, True)),
            "mdm_identifier": mdm_identifier or f"com.kandji.profile.custom.{profile_id}",
            "created_at": created_at
            or (datetime.now(UTC) - timedelta(seconds=random.randint(0, 31_536_000))).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "updated_at": updated_at
            or (datetime.now(UTC) - timedelta(seconds=random.randint(0, 31_536_000))).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "runs_on_mac": runs_on_mac if runs_on_mac is not None else random.choice((False, True, None)),
            "runs_on_iphone": runs_on_iphone or random.choice((False, True, None)),
            "runs_on_ipad": runs_on_ipad if runs_on_ipad is not None else random.choice((False, True, None)),
            "runs_on_tv": runs_on_tv if runs_on_tv is not None else random.choice((False, True, None)),
            "runs_on_vision": runs_on_vision if runs_on_vision is not None else random.choice((False, True, None)),
        }

        # Ensure at least one runs_on_* is True
        if True not in {
            info["runs_on_mac"],
            info["runs_on_iphone"],
            info["runs_on_ipad"],
            info["runs_on_tv"],
            info["runs_on_vision"],
        }:
            info[random.choice(("runs_on_mac", "runs_on_iphone", "runs_on_ipad", "runs_on_tv", "runs_on_vision"))] = (
                True
            )

        # Remove None values and return
        return {k: str(v) if isinstance(v, Path) else v for k, v in info.items() if v is not None}

    return factory


@pytest.fixture
def profile_info_content_factory(profile_info_data_factory) -> Callable[..., str]:
    """Return a function that generates an info file content with the given parameters."""

    def factory(
        format_type: InfoFormat,
        *,
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        mdm_identifier: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        runs_on_mac: bool | None = None,
        runs_on_iphone: bool | None = None,
        runs_on_ipad: bool | None = None,
        runs_on_tv: bool | None = None,
        runs_on_vision: bool | None = None,
    ) -> str:
        info_file_dict = profile_info_data_factory(
            id,
            name,
            active,
            mdm_identifier,
            created_at,
            updated_at,
            runs_on_mac,
            runs_on_iphone,
            runs_on_ipad,
            runs_on_tv,
            runs_on_vision,
        )
        # remove keys with None values
        info_file_dict = {k: str(v) if isinstance(v, Path) else v for k, v in info_file_dict.items() if v is not None}

        match format_type:
            case InfoFormat.PLIST:
                return plistlib.dumps(info_file_dict, fmt=plistlib.FMT_XML).decode("utf-8").expandtabs(4)
            case InfoFormat.JSON:
                return json.dumps(info_file_dict, indent=2)
            case InfoFormat.YAML:
                yaml = YAML()
                yaml.indent(mapping=2, sequence=4, offset=2)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    yaml.dump(info_file_dict, sys.stdout)
                return output.getvalue()

    return factory


@pytest.fixture
def custom_profile_factory(
    profile_info_data_factory: Callable[..., dict[str, str | bool]],
    mobileconfig_data_factory: Callable[..., dict[str, str | bool]],
    mobileconfig_content_factory: Callable[..., str],
) -> Callable[..., CustomProfile]:
    """Return a function that creates a CustomProfile object with valid info and profile data."""

    def factory() -> CustomProfile:
        profile_data = mobileconfig_data_factory()
        profile_content = mobileconfig_content_factory(**profile_data)
        info_data = profile_info_data_factory(
            id=profile_data["id"], name=profile_data["name"], mdm_identifier=profile_data["mdm_identifier"]
        )
        return CustomProfile(
            info=ProfileInfoFile.model_validate(info_data),
            profile=Mobileconfig(content=profile_content),
        )

    return factory


@pytest.fixture
def custom_profile_obj(custom_profile_factory) -> CustomProfile:
    """Return a CustomProfile object with valid info and profile data."""
    return custom_profile_factory()


@pytest.fixture
def custom_profile_obj_with_paths(profiles_repo: Path, custom_profile_obj: CustomProfile) -> CustomProfile:
    """Return a CustomProfile object with valid info and profile data and paths set."""
    custom_profile_obj.ensure_paths(repo_path=profiles_repo)
    custom_profile_obj.write()
    return custom_profile_obj


@pytest.fixture
def profile_directory_factory(
    file_factory: Callable[..., None],
    mobileconfig_data_factory: Callable[..., dict[str, str | bool]],
    profile_info_data_factory: Callable[..., dict[str, str | bool]],
    mobileconfig_content_factory: Callable[..., str],
    profile_info_content_factory: Callable[..., str],
) -> Callable[..., tuple[Path, Path]]:
    """Return a function that creates a profile directory with a profile and info file."""

    def create_profile_directory(
        *,
        path: str | Path,
        info_format: InfoFormat = InfoFormat.PLIST,
    ) -> tuple[Path, Path]:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        mobileconfig_data = mobileconfig_data_factory()
        profile_path = path / f"{mobileconfig_data['name']}.mobileconfig"
        profile = mobileconfig_content_factory(**mobileconfig_data)
        file_factory(profile_path, profile)

        info_data = profile_info_data_factory(
            id=mobileconfig_data["id"],
            name=mobileconfig_data["name"],
            mdm_identifier=mobileconfig_data["mdm_identifier"],
        )
        info_path = path / f"info.{info_format}"
        info = profile_info_content_factory(info_format, **info_data)
        file_factory(info_path, info)

        return info_path.relative_to(path), profile_path.relative_to(path)

    return create_profile_directory


@pytest.fixture(params=[InfoFormat.PLIST, InfoFormat.JSON, InfoFormat.YAML], ids=["plist", "json", "yaml"])
def profile_directory(request, tmp_path, profile_directory_factory) -> Path:
    """Return the path to valid profile directory with a profile and info file."""
    info_path, _ = profile_directory_factory(path=tmp_path, info_format=request.param)
    return info_path.parent


@pytest.fixture
def profiles_repo(
    request: pytest.FixtureRequest, iructl_repo: Path, profile_directory_factory: Callable[..., tuple[Path, Path]]
) -> Path:
    """Create a repository directory with child profile directories populated."""

    profiles_repo_path = iructl_repo / "profiles"
    profiles_repo_path.mkdir(exist_ok=True)

    marker = request.node.get_closest_marker("profile_count")
    count = 10 if marker is None else marker.args[0]
    for _ in range(count):
        info_format = random.choice(list(InfoFormat))
        path = (
            profiles_repo_path / random.choice((".", "group1", "group2", "group1/group3")) / f"Profile {str(uuid4())}"
        )
        profile_directory_factory(path=path.resolve(), info_format=info_format)

    return profiles_repo_path


@pytest.fixture
def profiles_repo_obj(profiles_repo: Path) -> Repository[CustomProfile]:
    """Return a CustomProfilesRepo object with CustomProfiles loaded from a repository directory."""
    return Repository.load_path(model=CustomProfile, path=profiles_repo)


@pytest.fixture
def profiles_list(
    request: pytest.FixtureRequest,
    custom_profile_factory: Callable[..., CustomProfile],
) -> list[CustomProfile]:
    """Create a list of CustomProfile objects without a directory."""
    marker = request.node.get_closest_marker("profile_count")
    count = 10 if marker is None else marker.args[0]
    return [custom_profile_factory() for _ in range(count)]


@pytest.fixture
def profiles_repo_obj_without_paths(profiles_list: list[CustomProfile]) -> Repository[CustomProfile]:
    """Return a CustomProfilesRepo object with CustomProfiles without loaded directly."""
    return Repository(profiles_list)


@pytest.fixture
def profiles_lrc(
    profiles_repo_obj: Repository[CustomProfile],
) -> tuple[Repository[CustomProfile], Repository[CustomProfile], ChangesDict[CustomProfile]]:
    """Prepare local and remote repositories with changes."""

    # limit local to 10 profiles
    local_repo = Repository(
        (profile for profile in itertools.islice(profiles_repo_obj.values(), 10)),
        root=profiles_repo_obj.root,
    )
    assert local_repo.root is not None
    # set sync hash profiles in local repo
    for profile in local_repo.values():
        profile.sync_hash = profile.diff_hash
        profile.write()

    # create an independent remote repo copy
    remote_repo = Repository(
        CustomProfile(
            info=ProfileInfoFile.model_validate(profile.info.model_dump(exclude={"sync_hash"})),
            profile=Mobileconfig(content=profile.profile.content),
        )
        for profile in local_repo.values()
    )

    # get IDs list
    profile_ids = set(local_repo.keys())
    changes: ChangesDict = {
        ChangeType.NONE: [],
        ChangeType.CREATE_REMOTE: [],
        ChangeType.UPDATE_REMOTE: [],
        ChangeType.CREATE_LOCAL: [],
        ChangeType.UPDATE_LOCAL: [],
        ChangeType.CONFLICT: [],
    }

    # mock local create change
    profile_id = profile_ids.pop()
    del remote_repo[profile_id]
    changes[ChangeType.CREATE_LOCAL].append((local_repo[profile_id], None))

    # mock local update change
    profile_id = profile_ids.pop()
    local_profile = local_repo[profile_id]
    local_profile.info.name = "New Local Name"
    local_profile.write()
    changes[ChangeType.UPDATE_LOCAL].append((local_repo[profile_id], remote_repo[profile_id]))

    # mock remote create change
    profile_id = profile_ids.pop()
    local_profile = local_repo[profile_id]
    local_profile.profile_path.unlink()
    local_profile.info_path.unlink()
    local_profile.profile_path.parent.rmdir()
    del local_repo[profile_id]
    changes[ChangeType.CREATE_REMOTE].append((None, remote_repo[profile_id]))

    # mock remote update change
    profile_id = profile_ids.pop()
    remote_repo[profile_id].info.active = not remote_repo[profile_id].info.active
    changes[ChangeType.UPDATE_REMOTE].append((local_repo[profile_id], remote_repo[profile_id]))

    # mock conflicting change
    profile_id = profile_ids.pop()
    local_profile = local_repo[profile_id]
    local_profile.info.name = "New Local Name"
    local_profile.write()
    remote_repo[profile_id].info.name = "New Remote Name"
    changes[ChangeType.CONFLICT].append((local_repo[profile_id], remote_repo[profile_id]))

    # mock no changes
    changes[ChangeType.NONE] += [(local_repo[profile_id], remote_repo[profile_id]) for profile_id in profile_ids]

    return local_repo, remote_repo, changes


@pytest.fixture
def patch_profiles_endpoints(
    monkeypatch, profiles_lrc: tuple[Repository, Repository, ChangesDict], response_factory
) -> dict[str, int]:
    """Patch the profiles endpoints for testing."""
    _, remote, _ = profiles_lrc
    called_counter = {"get": 0, "list": 0, "create": 0, "update": 0, "delete": 0}

    def fake_get_profile(self, id):
        nonlocal called_counter
        called_counter["get"] += 1
        if id in remote:
            return profile_to_response(remote[id])
        raise requests.exceptions.HTTPError(
            "Not Found", response=response_factory(404, {"detail": "No MDMProfile matches the given query."})
        )

    def fake_list_profiles(self):
        nonlocal called_counter
        called_counter["list"] += 1
        results: list[CustomProfilePayload] = []
        for profile in remote.values():
            results.append(profile_to_response(profile))

        return PayloadList(
            count=len(results),
            results=results,
        )

    def fake_create_profile(
        self, name, file, active, runs_on_mac, runs_on_iphone, runs_on_ipad, runs_on_tv, runs_on_vision
    ):
        nonlocal called_counter
        called_counter["create"] += 1
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
        self,
        id,
        name=None,
        file=None,
        active=None,
        runs_on_mac=None,
        runs_on_iphone=None,
        runs_on_ipad=None,
        runs_on_tv=None,
        runs_on_vision=None,
    ):
        nonlocal called_counter
        called_counter["update"] += 1
        if id not in remote:
            raise requests.exceptions.HTTPError(
                "Not Found", response=response_factory(404, {"detail": "No MDMProfile matches the given query."})
            )
        profile = remote[id]
        assert isinstance(profile, CustomProfile)
        payload = {
            "id": id,
            "name": name if name is not None else profile.name,
            "active": active if active is not None else profile.info.active,
            "mdm_identifier": f"com.kandji.profile.custom.{id}",
            "runs_on_mac": runs_on_mac if runs_on_mac is not None else False,
            "runs_on_iphone": runs_on_iphone if runs_on_iphone is not None else False,
            "runs_on_ipad": runs_on_ipad if runs_on_ipad is not None else False,
            "runs_on_tv": runs_on_tv if runs_on_tv is not None else False,
            "runs_on_vision": runs_on_vision if runs_on_vision is not None else False,
            "created_at": profile.info.created_at,
            "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        if file is not None:
            with file.open("rb") as f:
                plist = plistlib.load(f, dict_type=OrderedDict)
        else:
            plist = plistlib.loads(profile.profile.content.encode("utf-8"), dict_type=OrderedDict)

        plist["PayloadUUID"] = payload["id"]
        plist["PayloadDisplayName"] = payload["name"]
        plist["PayloadIdentifier"] = f"com.kandji.profile.custom.{payload['id']}"

        payload["profile"] = plistlib.dumps(plist, fmt=plistlib.PlistFormat.FMT_XML, sort_keys=False).decode("utf-8")

        return CustomProfilePayload.model_validate(payload)

    def fake_delete_profile(self, id):
        nonlocal called_counter
        called_counter["delete"] += 1
        return

    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.get", fake_get_profile)
    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.list", fake_list_profiles)
    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.create", fake_create_profile)
    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.update", fake_update_profile)
    monkeypatch.setattr("iructl.api.profiles.CustomProfilesResource.delete", fake_delete_profile)

    return called_counter


@pytest.fixture
def profiles_response(
    profiles_lrc: tuple[Repository, Repository, ChangesDict],
) -> PayloadList:
    _, remote, _ = profiles_lrc

    response = [profile_to_response(profile) for profile in remote.values()]
    return PayloadList(count=len(response), results=response)
