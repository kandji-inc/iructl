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
from iructl.api import CustomAppPayload, PayloadList
from iructl.repository import (
    SUFFIX_MAP,
    AppFile,
    AppInfoFile,
    CustomApp,
    InfoFormat,
    InstallEnforcement,
    InstallType,
    Repository,
    Script,
)

APP_SCRIPT_CONTENT = "#!/bin/zsh\necho 'app script'\n"

VALID_INFO_SUFFIXES = list(SUFFIX_MAP.keys())


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


def _remote_copy(app: CustomApp) -> CustomApp:
    return CustomApp(
        info=AppInfoFile.model_validate(app.info.model_dump(exclude={"sync_hash"})),
        audit=Script(content=app.audit.content) if app.audit else None,
        preinstall=Script(content=app.preinstall.content) if app.preinstall else None,
        postinstall=Script(content=app.postinstall.content) if app.postinstall else None,
    )


@pytest.fixture
def apps_lrc(
    iructl_repo: Path,
    apps_repo_obj: Repository[CustomApp],
    apps_remote: Repository[CustomApp],
) -> tuple[Repository[CustomApp], Repository[CustomApp], ChangesDict[CustomApp]]:
    """Prepare local and remote app repos with changes, plus on-disk installers.

    Unlike scripts/profiles, an app create push uploads the installer, so each local app gets a
    unique ``payloads/`` installer whose sha256 matches its info file.
    """

    local_repo = Repository(
        (app for app in itertools.islice(apps_repo_obj.values(), 10)),
        root=apps_repo_obj.root,
    )
    assert local_repo.root is not None

    # Payloads resolve at the repo root, not the apps dir.
    payload_dir = iructl_repo / "payloads"
    payload_dir.mkdir(exist_ok=True)
    for index, app in enumerate(local_repo.values()):
        content = f"installer-{index}".encode()
        name = f"installer-{index}.pkg"
        (payload_dir / name).write_bytes(content)
        app.info.file = AppFile(name=name, sha256=hashlib.sha256(content).hexdigest())
        app.sync_hash = app.diff_hash
        app.write()

    for app in local_repo.values():
        apps_remote[app.id] = _remote_copy(app)

    app_ids = set(local_repo.keys())
    changes: ChangesDict = {
        ChangeType.NONE: [],
        ChangeType.CREATE_REMOTE: [],
        ChangeType.UPDATE_REMOTE: [],
        ChangeType.CREATE_LOCAL: [],
        ChangeType.UPDATE_LOCAL: [],
        ChangeType.CONFLICT: [],
    }

    app_id = app_ids.pop()
    del apps_remote[app_id]
    changes[ChangeType.CREATE_LOCAL].append((local_repo[app_id], None))

    app_id = app_ids.pop()
    local_app = local_repo[app_id]
    local_app.info.name = "New Local Name"
    local_app.write()
    changes[ChangeType.UPDATE_LOCAL].append((local_repo[app_id], apps_remote[app_id]))

    app_id = app_ids.pop()
    local_app = local_repo[app_id]
    for script_path in (local_app.audit_path, local_app.preinstall_path, local_app.postinstall_path):
        script_path.unlink(missing_ok=True)
    local_app.info_path.unlink()
    local_app.info_path.parent.rmdir()
    if local_app.info.file:
        (payload_dir / local_app.info.file.name).unlink(missing_ok=True)
    del local_repo[app_id]
    changes[ChangeType.CREATE_REMOTE].append((None, apps_remote[app_id]))

    app_id = app_ids.pop()
    apps_remote[app_id].info.active = not apps_remote[app_id].info.active
    changes[ChangeType.UPDATE_REMOTE].append((local_repo[app_id], apps_remote[app_id]))

    app_id = app_ids.pop()
    local_app = local_repo[app_id]
    local_app.info.name = "New Local Name"
    local_app.write()
    apps_remote[app_id].info.name = "New Remote Name"
    changes[ChangeType.CONFLICT].append((local_repo[app_id], apps_remote[app_id]))

    changes[ChangeType.NONE] += [(local_repo[app_id], apps_remote[app_id]) for app_id in app_ids]

    return local_repo, apps_remote, changes


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
