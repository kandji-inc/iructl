import contextlib
import hashlib
import io
import itertools
import json
import plistlib
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from ruamel.yaml import YAML

from iructl._diff import ChangesDict, ChangeType
from iructl._utils import content_suffixed_filename
from iructl.api import CustomAppPayload, PayloadList
from iructl.repository import (
    SUFFIX_MAP,
    AppInfoFile,
    CustomApp,
    InfoFormat,
    InstallEnforcement,
    InstallType,
    Repository,
    Script,
)
from iructl.repository.custom_app import SCRIPT_ATTRIBUTES

APP_SCRIPT_CONTENT = "#!/bin/zsh\necho 'app script'\n"

VALID_INFO_SUFFIXES = list(SUFFIX_MAP.keys())

# Deterministic installer bytes shared by the integration tests, with their sha256.
INSTALLER = b"installer-bytes-content"
INSTALLER_SHA = hashlib.sha256(INSTALLER).hexdigest()
INSTALLER_NAME = content_suffixed_filename("installer.pkg", INSTALLER_SHA)


def _random_sha256() -> str:
    return "".join(random.choices("0123456789abcdef", k=64))


@pytest.fixture
def app_info_data_factory():
    def factory(
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        install_type: InstallType | None = None,
        install_enforcement: InstallEnforcement | None = None,
        restart: bool | None = None,
        unzip_location: str | None = None,
        show_in_self_service: bool | None = None,
        self_service_category_id: str | None = None,
        self_service_recommended: bool | None = None,
        file_name: str | None = None,
        file_sha256: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict:
        random_updated_at = datetime.now(UTC) - timedelta(seconds=random.randint(0, 31_536_000))

        if install_enforcement is InstallEnforcement.NO_ENFORCEMENT and show_in_self_service is False:
            raise ValueError("Self service must be enabled if install_enforcement is no_enforcement")

        # Default install_type respects the unzip_location constraint:
        # zip requires unzip_location; non-zip rejects it.
        if install_type is None:
            if unzip_location is not None:
                install_type = InstallType.ZIP
            else:
                install_type = random.choice(list(set(InstallType) - {InstallType.ZIP}))
        if install_type is InstallType.ZIP and unzip_location is None:
            unzip_location = "/Applications"

        # If show_in_self_service is False, force install_enforcement off NO_ENFORCEMENT.
        if install_enforcement is None:
            if show_in_self_service is False:
                install_enforcement = random.choice(list(set(InstallEnforcement) - {InstallEnforcement.NO_ENFORCEMENT}))
            else:
                install_enforcement = random.choice(list(InstallEnforcement))

        result = {
            "id": id or str(uuid4()),
            "name": name or "Test App",
            "active": active if active is not None else random.choice([True, False]),
            "install_type": str(install_type),
            "install_enforcement": str(install_enforcement),
            "restart": restart if restart is not None else random.choice([True, False]),
            "updated_at": updated_at or random_updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "file": {
                "name": file_name or "Test App.pkg",
                "sha256": file_sha256 or _random_sha256(),
            },
        }

        result["created_at"] = created_at or random.choice(
            [random_updated_at, (random_updated_at - timedelta(seconds=random.randint(0, 31_536_000)))]
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        if unzip_location is not None:
            result["unzip_location"] = unzip_location

        # NO_ENFORCEMENT forces show_in_self_service True (validator).
        if install_enforcement is InstallEnforcement.NO_ENFORCEMENT:
            result["show_in_self_service"] = True
        else:
            result["show_in_self_service"] = (
                show_in_self_service if show_in_self_service is not None else random.choice([True, False])
            )

        if result["show_in_self_service"]:
            result["self_service_category_id"] = self_service_category_id or str(uuid4())
            result["self_service_recommended"] = (
                self_service_recommended if self_service_recommended is not None else random.choice([True, False])
            )

        return result

    return factory


@pytest.fixture
def app_info_content_factory(app_info_data_factory):
    def factory(format_type: InfoFormat, **kwargs) -> str:
        info_file_dict = app_info_data_factory(**kwargs)
        info_file_dict = {k: v for k, v in info_file_dict.items() if v is not None}

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
def app_info_file_obj(app_info_data_factory) -> AppInfoFile:
    return AppInfoFile.model_validate(app_info_data_factory())


@pytest.fixture(params=VALID_INFO_SUFFIXES, ids=VALID_INFO_SUFFIXES)
def app_info_file_obj_with_path(request, tmp_path, app_info_file_obj) -> AppInfoFile:
    app_info_file_obj.format = SUFFIX_MAP[request.param]
    app_info_file_obj.path = tmp_path / f"info{request.param}"
    return app_info_file_obj


@pytest.fixture(params=VALID_INFO_SUFFIXES, ids=VALID_INFO_SUFFIXES)
def app_info_file(request, tmp_path, app_info_content_factory) -> Path:
    """Return the path to a valid app info file on disk."""
    info_path = tmp_path / f"info{request.param}"
    info_path.write_text(app_info_content_factory(format_type=SUFFIX_MAP[request.param]))
    return info_path


@pytest.fixture
def custom_app_factory(app_info_data_factory):
    def factory(
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        install_type: InstallType | None = None,
        install_enforcement: InstallEnforcement | None = None,
        restart: bool | None = None,
        unzip_location: str | None = None,
        show_in_self_service: bool | None = None,
        self_service_category_id: str | None = None,
        self_service_recommended: bool | None = None,
        file_name: str | None = None,
        file_sha256: str | None = None,
        has_audit: bool | None = None,
        has_preinstall: bool | None = None,
        has_postinstall: bool | None = None,
    ) -> CustomApp:
        has_audit = random.choice([True, False]) if has_audit is None else has_audit
        has_preinstall = random.choice([True, False]) if has_preinstall is None else has_preinstall
        has_postinstall = random.choice([True, False]) if has_postinstall is None else has_postinstall

        # An audit script is only valid when continuously enforced.
        if has_audit:
            install_enforcement = InstallEnforcement.CONTINUOUSLY_ENFORCE

        info_file = AppInfoFile.model_validate(
            app_info_data_factory(
                id=id,
                name=name,
                active=active,
                install_type=install_type,
                install_enforcement=install_enforcement,
                restart=restart,
                unzip_location=unzip_location,
                show_in_self_service=show_in_self_service,
                self_service_category_id=self_service_category_id,
                self_service_recommended=self_service_recommended,
                file_name=file_name,
                file_sha256=file_sha256,
            )
        )

        scripts = {
            attribute: Script(content=APP_SCRIPT_CONTENT)
            for attribute, present in (
                ("audit", has_audit),
                ("preinstall", has_preinstall),
                ("postinstall", has_postinstall),
            )
            if present
        }
        return CustomApp(info=info_file, **scripts)

    return factory


@pytest.fixture(
    params=[(True, True, True), (False, False, False), (True, False, False), (False, True, True)],
    ids=["all_scripts", "no_scripts", "audit_only", "pre_post"],
)
def custom_app_obj(request, custom_app_factory) -> CustomApp:
    has_audit, has_preinstall, has_postinstall = request.param
    return custom_app_factory(has_audit=has_audit, has_preinstall=has_preinstall, has_postinstall=has_postinstall)


@pytest.fixture
def app_directory_factory(app_info_content_factory):
    def factory(
        path: str | Path,
        format_type: InfoFormat = InfoFormat.PLIST,
        *,
        has_audit: bool = True,
        has_preinstall: bool = True,
        has_postinstall: bool = True,
        **info_kwargs,
    ) -> dict[str, Path]:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        # An audit script is only valid when continuously enforced.
        if has_audit:
            info_kwargs["install_enforcement"] = InstallEnforcement.CONTINUOUSLY_ENFORCE

        info_path = path / f"info.{format_type.value}"
        info_path.write_text(app_info_content_factory(format_type, **info_kwargs))
        written = {"info": info_path}
        for attribute, present in (
            ("audit", has_audit),
            ("preinstall", has_preinstall),
            ("postinstall", has_postinstall),
        ):
            if present:
                script_path = path / f"{attribute}.zsh"
                script_path.write_text(APP_SCRIPT_CONTENT)
                written[attribute] = script_path
        return written

    return factory


@pytest.fixture
def app_directory(tmp_path, app_directory_factory) -> Path:
    return app_directory_factory(tmp_path)["info"].parent


@pytest.fixture
def apps_repo(request, iructl_repo, app_directory_factory) -> Path:
    """Create a repository directory with child app directories populated."""
    apps_repo_path = iructl_repo / "apps"
    apps_repo_path.mkdir(exist_ok=True)

    marker = request.node.get_closest_marker("app_count")
    count = 10 if marker is None else marker.args[0]
    for i in range(count):
        info_format = random.choice(list(InfoFormat))
        path = apps_repo_path / random.choice((".", "group1", "group2", "group1/group3")) / f"App {i:03}"
        app_directory_factory(path=path.resolve(), format_type=info_format)

    return apps_repo_path


@pytest.fixture
def apps_repo_obj(apps_repo: Path) -> Repository[CustomApp]:
    """Return a repository object for the apps repo."""
    return Repository.load_path(model=CustomApp, path=apps_repo)


def app_to_response(app: CustomApp) -> CustomAppPayload:
    """Build the API response payload for a CustomApp (test inverse of from_api_payload)."""
    data = app.info.model_dump(mode="json", exclude={"sync_hash", "file"})
    if data.get("updated_at") is None:
        data["updated_at"] = data["created_at"]
    name = app.info.file.payload_name
    return CustomAppPayload.model_validate(
        data
        | {
            "sha256": app.info.file.sha256,
            "file_key": f"tenants/1/library/custom_apps/{name}",
            "file_url": f"https://example.com/{name}",
            "file_size": 1234,
            "file_updated": data["updated_at"],
            "audit_script": app.audit.content if app.audit else "",
            "preinstall_script": app.preinstall.content if app.preinstall else "",
            "postinstall_script": app.postinstall.content if app.postinstall else "",
        }
    )


@pytest.fixture
def apps_remote() -> Repository[CustomApp]:
    """An in-memory remote repository that tests populate and the faked endpoints serve."""
    return Repository[CustomApp]()


@pytest.fixture
def patch_apps_endpoints(monkeypatch, apps_remote: Repository[CustomApp]) -> dict[str, int]:
    """Patch the custom-apps API endpoints against an in-memory remote repository.

    The faked create/update accept the installer ``file`` Path (as push_remote passes it) and
    derive its sha256 in place of a real S3 upload.
    """
    called = {"get": 0, "list": 0, "create": 0, "update": 0, "delete": 0}

    def _now() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _file_fields(file: Path, name: str | None = None) -> dict:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        key_name = name or file.name
        return {
            "sha256": digest,
            "file_key": f"tenants/1/library/custom_apps/{key_name}",
            "file_url": f"https://example.com/{key_name}",
            "file_size": file.stat().st_size,
            "file_updated": _now(),
        }

    def fake_get(self, id):
        called["get"] += 1
        if id in apps_remote:
            return app_to_response(apps_remote[id])
        raise requests.HTTPError("Not Found")

    def fake_list(self):
        called["list"] += 1
        results = [app_to_response(member) for member in apps_remote.values()]
        return PayloadList(count=len(results), results=results)

    def fake_create(self, **kwargs):
        called["create"] += 1
        file = kwargs.pop("file")
        file_name = kwargs.pop("file_name", None)
        payload = CustomAppPayload.model_validate(
            kwargs | {"id": str(uuid4()), "created_at": _now(), "updated_at": _now()} | _file_fields(file, file_name)
        )
        apps_remote[payload.id] = CustomApp.from_api_payload(payload)
        return payload

    def fake_update(self, id, **kwargs):
        called["update"] += 1
        base = app_to_response(apps_remote[id])
        file = kwargs.pop("file", None)
        file_name = kwargs.pop("file_name", None)
        update = dict(kwargs) | {"updated_at": _now()}
        if file is not None:
            update |= _file_fields(file, file_name)
        payload = base.model_copy(update=update)
        apps_remote[id] = CustomApp.from_api_payload(payload)
        return payload

    def fake_delete(self, id):
        called["delete"] += 1
        del apps_remote[id]

    monkeypatch.setattr("iructl.api.apps.CustomAppsResource.get", fake_get)
    monkeypatch.setattr("iructl.api.apps.CustomAppsResource.list", fake_list)
    monkeypatch.setattr("iructl.api.apps.CustomAppsResource.create", fake_create)
    monkeypatch.setattr("iructl.api.apps.CustomAppsResource.update", fake_update)
    monkeypatch.setattr("iructl.api.apps.CustomAppsResource.delete", fake_delete)
    return called


def make_local_app(repo: Path, factory, *, name: str = "My App") -> CustomApp:
    """Write a local custom-app member (no scripts) under <repo>/apps."""
    member = factory(
        name=name,
        file_name=INSTALLER_NAME,
        file_sha256=INSTALLER_SHA,
        install_type=InstallType.PACKAGE,
        install_enforcement=InstallEnforcement.INSTALL_ONCE,
        has_audit=False,
        has_preinstall=False,
        has_postinstall=False,
    )
    member.ensure_paths(repo / "apps")
    member.write()
    return member


def place_installer(repo: Path, content: bytes = INSTALLER, name: str = INSTALLER_NAME) -> Path:
    """Write installer bytes into <repo>/payloads, creating the directory if needed."""
    payloads = repo / "payloads"
    payloads.mkdir(exist_ok=True)
    target = payloads / name
    target.write_bytes(content)
    return target


def compare_app_object(app1: CustomApp, app2: CustomApp, expected_diff: set[str]) -> None:
    """Assert two custom apps differ exactly on the info fields named in expected_diff.

    The three optional scripts (audit/preinstall/postinstall) are compared by content;
    present-on-one-side-only counts as a difference for that attribute.
    """
    for k in set(app1.info.model_dump().keys()):
        if k in expected_diff:
            assert getattr(app1.info, k, None) != getattr(app2.info, k, None)
        else:
            assert getattr(app1.info, k, None) == getattr(app2.info, k, None)

    for attribute in SCRIPT_ATTRIBUTES:
        script1 = getattr(app1, attribute)
        script2 = getattr(app2, attribute)
        if script1 is None and script2 is None:
            assert attribute not in expected_diff
        elif script1 is None or script2 is None:
            assert attribute in expected_diff
        elif script1.content == script2.content:
            assert attribute not in expected_diff
        else:
            assert attribute in expected_diff


@pytest.fixture
def stub_installer_download(monkeypatch):
    """Patch the installer download to write INSTALLER bytes to its destination."""

    def _download(self, url, dest, *, expected_sha, file_size=None, on_progress=lambda _: None):
        dest.write_bytes(INSTALLER)

    monkeypatch.setattr("iructl.api.client.S3Client.download_file", _download)


@pytest.fixture
def unchanged_app(iructl_repo_cd, custom_app_factory, apps_remote) -> CustomApp:
    """A local app whose metadata matches Iru (ChangeType.NONE), with no local installer yet."""
    member = make_local_app(iructl_repo_cd, custom_app_factory)
    member.sync_hash = member.diff_hash
    member.write()
    apps_remote[member.id] = CustomApp.from_api_payload(app_to_response(member))
    (iructl_repo_cd / "payloads").mkdir(exist_ok=True)
    return member


@pytest.fixture
def apps_lrc(
    apps_repo_obj: Repository[CustomApp],
    apps_remote: Repository[CustomApp],
) -> tuple[Repository[CustomApp], Repository[CustomApp], ChangesDict[CustomApp]]:
    """Prepare local and remote repositories with one change in each ChangeType bucket.

    Mirrors scripts_lrc with one deliberate divergence: it populates the shared apps_remote
    fixture (which patch_apps_endpoints serves) instead of returning a private remote repo.
    Only the CREATE_LOCAL app gets a seeded installer in payloads/ so its create-push uploads;
    every other bucket is a metadata-only edit that leaves file.sha256 untouched and so never
    trips an installer upload.
    """
    # limit local to 10 apps
    local_repo = Repository(
        (app for app in itertools.islice(apps_repo_obj.values(), 10)),
        root=apps_repo_obj.root,
    )
    assert local_repo.root is not None
    # set sync hash on apps in local repo so they start in sync
    for app in local_repo.values():
        app.sync_hash = app.diff_hash
        app.write()

    # populate the shared remote with independent copies of every local app
    for app in local_repo.values():
        apps_remote[app.id] = CustomApp.from_api_payload(app_to_response(app))

    app_ids = set(local_repo.keys())
    changes: ChangesDict = {
        ChangeType.NONE: [],
        ChangeType.CREATE_REMOTE: [],
        ChangeType.UPDATE_REMOTE: [],
        ChangeType.CREATE_LOCAL: [],
        ChangeType.UPDATE_LOCAL: [],
        ChangeType.CONFLICT: [],
    }

    # mock local create change: drop from remote and seed an installer whose sha matches.
    app_id = app_ids.pop()
    create_local_app = local_repo[app_id]
    del apps_remote[app_id]
    create_local_app.info.file.name = "installer.pkg"
    create_local_app.info.file.sha256 = INSTALLER_SHA
    create_local_app.write()
    place_installer(local_repo.root.parent, name="installer.pkg")
    changes[ChangeType.CREATE_LOCAL].append((create_local_app, None))

    # mock local update change (metadata only)
    app_id = app_ids.pop()
    local_app = local_repo[app_id]
    local_app.info.name = "New Local Name"
    local_app.write()
    changes[ChangeType.UPDATE_LOCAL].append((local_repo[app_id], apps_remote[app_id]))

    # mock remote create change: remove the local member, keep it on the remote.
    app_id = app_ids.pop()
    member_dir = local_repo[app_id].info_path.parent
    for child in member_dir.iterdir():
        child.unlink()
    member_dir.rmdir()
    del local_repo[app_id]
    changes[ChangeType.CREATE_REMOTE].append((None, apps_remote[app_id]))

    # mock remote update change (metadata only)
    app_id = app_ids.pop()
    apps_remote[app_id].info.active = not apps_remote[app_id].info.active
    changes[ChangeType.UPDATE_REMOTE].append((local_repo[app_id], apps_remote[app_id]))

    # mock conflicting change (metadata changed on both sides)
    app_id = app_ids.pop()
    local_app = local_repo[app_id]
    local_app.info.name = "New Local Name"
    local_app.write()
    apps_remote[app_id].info.name = "New Remote Name"
    changes[ChangeType.CONFLICT].append((local_repo[app_id], apps_remote[app_id]))

    # mock no changes
    changes[ChangeType.NONE] += [(local_repo[app_id], apps_remote[app_id]) for app_id in app_ids]

    # Invariant the suites rely on: the returned local repo round-trips through disk.
    assert Repository.load_path(model=CustomApp, path=local_repo.root) == local_repo
    return local_repo, apps_remote, changes
