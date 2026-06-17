import contextlib
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
from iructl.api.payload import CustomScriptPayload, PayloadList
from iructl.repository import (
    SUFFIX_MAP,
    CustomScript,
    ExecutionFrequency,
    InfoFormat,
    Repository,
    Script,
    ScriptInfoFile,
)

VALID_INFO_SUFFIXES = list(SUFFIX_MAP.keys())


def script_to_response(script: CustomScript) -> CustomScriptPayload:
    """Build the API response payload for a CustomScript (test inverse of from_api_payload)."""
    data = script.info.model_dump(mode="json", exclude={"sync_hash"})
    if data.get("updated_at") is None:
        data["updated_at"] = data["created_at"]
    return CustomScriptPayload.model_validate(
        data
        | {
            "script": script.audit.content,
            "remediation_script": script.remediation.content if script.remediation is not None else "",
        }
    )


@pytest.fixture
def script_content() -> str:
    return "#!/bin/sh\necho 'Hello, World!'"


@pytest.fixture
def script_file(tmp_path, script_content) -> Path:
    script_file = tmp_path / "script.sh"
    script_file.write_text(script_content)
    return script_file


@pytest.fixture
def script_info_data_factory():
    def factory(
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        execution_frequency: ExecutionFrequency | None = None,
        restart: bool | None = None,
        show_in_self_service: bool | None = None,
        self_service_category_id: str | None = None,
        self_service_recommended: bool | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, str | bool]:
        random_id = str(uuid4())
        random_updated_at = datetime.now(UTC) - timedelta(seconds=random.randint(0, 31_536_000))

        if execution_frequency is ExecutionFrequency.NO_ENFORCEMENT and show_in_self_service is False:
            raise ValueError("Self service must be enabled if execution frequency is no_enforcement")

        result = {
            "id": id or random_id,
            "name": name or "Test Script",
            "active": active if active is not None else random.choice([True, False]),
            "restart": restart if restart is not None else random.choice([True, False]),
            "updated_at": updated_at or random_updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }

        # Randomly set created_at to be the same as updated_at or a random time before it
        result["created_at"] = created_at or random.choice(
            [random_updated_at, (random_updated_at - timedelta(seconds=random.randint(0, 31_536_000)))]
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        result["execution_frequency"] = str(
            execution_frequency
            or random.choice(
                list(set(ExecutionFrequency) - {ExecutionFrequency.NO_ENFORCEMENT})
                if show_in_self_service is False
                else list(ExecutionFrequency)
            )
        )

        # Self service is required if execution_frequency is no_enforcement
        if result["execution_frequency"] == "no_enforcement":
            result["show_in_self_service"] = True
        else:
            result["show_in_self_service"] = (
                show_in_self_service if show_in_self_service is not None else random.choice([True, False])
            )

        # Self service options are required if show_in_self_service is True
        if result["show_in_self_service"]:
            result["self_service_category_id"] = self_service_category_id or str(uuid4())
            result["self_service_recommended"] = (
                self_service_recommended if self_service_recommended is not None else random.choice([True, False])
            )

        return result

    return factory


@pytest.fixture
def script_info_content_factory(script_info_data_factory):
    def factory(
        format_type: InfoFormat,
        *,
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        execution_frequency: str | None = None,
        restart: bool | None = None,
        show_in_self_service: bool | None = None,
        self_service_category_id: str | None = None,
        self_service_recommended: bool | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> str:
        info_file_dict = script_info_data_factory(
            id=id,
            name=name,
            active=active,
            execution_frequency=execution_frequency,
            restart=restart,
            show_in_self_service=show_in_self_service,
            self_service_category_id=self_service_category_id,
            self_service_recommended=self_service_recommended,
            created_at=created_at,
            updated_at=updated_at,
        )
        # remove None values
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
def script_info_file_obj(script_info_data_factory) -> ScriptInfoFile:
    return ScriptInfoFile.model_validate(script_info_data_factory())


@pytest.fixture(params=VALID_INFO_SUFFIXES, ids=VALID_INFO_SUFFIXES)
def script_info_file_obj_with_path(request, tmp_path, script_info_file_obj) -> ScriptInfoFile:
    script_info_file_obj.format = SUFFIX_MAP[request.param]
    script_info_file_obj.path = tmp_path / f"info.{request.param}"
    return script_info_file_obj


@pytest.fixture(params=VALID_INFO_SUFFIXES, ids=VALID_INFO_SUFFIXES)
def script_info_file(request, tmp_path, script_info_content_factory):
    """Return the path to a valid script info file."""
    info_path = tmp_path / f"info{request.param}"
    info_path.write_text(script_info_content_factory(format_type=SUFFIX_MAP[request.param]))
    return info_path


@pytest.fixture
def custom_script_factory(
    script_info_data_factory,
    script_content,
):
    def factory(
        id: str | None = None,
        name: str | None = None,
        active: bool | None = None,
        execution_frequency: str | None = None,
        restart: bool | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        has_remediation: bool | None = None,
    ) -> CustomScript:
        info_file = ScriptInfoFile.model_validate(
            script_info_data_factory(id, name, active, execution_frequency, restart, created_at, updated_at)
        )

        if has_remediation is None:
            has_remediation = random.choice([True, False])

        # Randomly include a remediation script
        if has_remediation:
            return CustomScript(
                info=info_file,
                audit=Script(content=script_content),
                remediation=Script(content=script_content),
            )
        else:
            return CustomScript(
                info=info_file,
                audit=Script(content=script_content),
            )

    return factory


@pytest.fixture(params=[True, False], ids=["with_remediation", "without_remediation"])
def custom_script_obj(request, custom_script_factory) -> CustomScript:
    return custom_script_factory(has_remediation=request.param)


@pytest.fixture
def script_directory_factory(script_info_content_factory, script_content):
    def factory(
        path: str | Path,
        format_type: InfoFormat = InfoFormat.PLIST,
        has_remediation: bool = True,
    ) -> tuple[Path, Path, Path | None]:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        info_path = path / f"info.{format_type.value}"
        info_path.write_text(script_info_content_factory(format_type))
        audit_path = path / "audit.sh"
        audit_path.write_text(script_content)
        if has_remediation:
            remediation_path = path / "remediation.sh"
            remediation_path.write_text(script_content)
        else:
            remediation_path = None
        return info_path, audit_path, remediation_path

    return factory


@pytest.fixture
def script_directory(tmp_path, script_directory_factory) -> Path:
    return script_directory_factory(tmp_path)[0].parent


@pytest.fixture
def scripts_repo(request, iructl_repo, script_directory_factory) -> Path:
    """Create a repository directory with child script directories populated."""
    scripts_repo_path = iructl_repo / "scripts"
    scripts_repo_path.mkdir(exist_ok=True)

    marker = request.node.get_closest_marker("script_count")
    count = 10 if marker is None else marker.args[0]
    for i in range(count):
        info_format = random.choice(list(InfoFormat))
        path = scripts_repo_path / random.choice((".", "group1", "group2", "group1/group3")) / f"Script {i:03}"
        script_directory_factory(path=path.resolve(), format_type=info_format)

    return scripts_repo_path


@pytest.fixture
def scripts_repo_obj(scripts_repo: Path) -> Repository[CustomScript]:
    """Return a repository object for the scripts repo."""
    return Repository.load_path(model=CustomScript, path=scripts_repo)


@pytest.fixture
def scripts_lrc(
    scripts_repo_obj: Repository[CustomScript],
) -> tuple[Repository[CustomScript], Repository[CustomScript], ChangesDict[CustomScript]]:
    """Prepare local and remote repositories with changes."""

    # limit local to 10 scripts
    local_repo = Repository(
        (script for script in itertools.islice(scripts_repo_obj.values(), 10)),
        root=scripts_repo_obj.root,
    )
    assert local_repo.root is not None
    # set sync hash scripts in local repo
    for script in local_repo.values():
        script.sync_hash = script.diff_hash
        script.write()

    # create an independent remote repo copy
    remote_repo = Repository(
        CustomScript(
            info=ScriptInfoFile.model_validate(script.info.model_dump(exclude={"sync_hash"})),
            audit=Script(content=script.audit.content),
            remediation=Script(content=script.remediation.content) if script.remediation else None,
        )
        for script in local_repo.values()
    )

    # get IDs list
    script_ids = set(local_repo.keys())
    changes: ChangesDict = {
        ChangeType.NONE: [],
        ChangeType.CREATE_REMOTE: [],
        ChangeType.UPDATE_REMOTE: [],
        ChangeType.CREATE_LOCAL: [],
        ChangeType.UPDATE_LOCAL: [],
        ChangeType.CONFLICT: [],
    }

    # mock local create change
    script_id = script_ids.pop()
    del remote_repo[script_id]
    changes[ChangeType.CREATE_LOCAL].append((local_repo[script_id], None))

    # mock local update change
    script_id = script_ids.pop()
    local_script = local_repo[script_id]
    local_script.info.name = "New Local Name"
    local_script.write()
    changes[ChangeType.UPDATE_LOCAL].append((local_repo[script_id], remote_repo[script_id]))

    # mock remote create change
    script_id = script_ids.pop()
    local_script = local_repo[script_id]
    local_script.audit_path.unlink()
    local_script.remediation_path.unlink()
    local_script.info_path.unlink()
    local_script.info_path.parent.rmdir()
    del local_repo[script_id]
    changes[ChangeType.CREATE_REMOTE].append((None, remote_repo[script_id]))

    # mock remote update change
    script_id = script_ids.pop()
    remote_repo[script_id].info.active = not remote_repo[script_id].info.active
    changes[ChangeType.UPDATE_REMOTE].append((local_repo[script_id], remote_repo[script_id]))

    # mock conflicting change
    script_id = script_ids.pop()
    local_script = local_repo[script_id]
    local_script.info.name = "New Local Name"
    local_script.write()
    remote_repo[script_id].info.name = "New Remote Name"
    changes[ChangeType.CONFLICT].append((local_repo[script_id], remote_repo[script_id]))

    # mock no changes
    changes[ChangeType.NONE] += [(local_repo[script_id], remote_repo[script_id]) for script_id in script_ids]

    return local_repo, remote_repo, changes


@pytest.fixture
def patch_scripts_endpoints(
    monkeypatch,
    scripts_lrc: tuple[Repository[CustomScript], Repository[CustomScript], ChangesDict[CustomScript]],
    response_factory,
) -> dict[str, int]:
    """Patch the scripts endpoints for testing."""
    _, remote, _ = scripts_lrc
    called_counter = {"get": 0, "list": 0, "create": 0, "update": 0, "delete": 0}

    def fake_get_script(self, id):
        nonlocal called_counter
        called_counter["get"] += 1
        if id in remote:
            return script_to_response(remote[id])
        raise requests.exceptions.HTTPError(
            "Not Found", response=response_factory(404, {"detail": "No MDMScript matches the given query."})
        )

    def fake_list_scripts(self):
        nonlocal called_counter
        called_counter["list"] += 1
        results: list[CustomScriptPayload] = []
        for script in remote.values():
            results.append(script_to_response(script))

        return PayloadList(
            count=len(results),
            results=results,
        )

    def fake_create_script(self, **kwargs):
        nonlocal called_counter
        called_counter["create"] += 1
        create_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if "remediation_script" not in kwargs:
            kwargs["remediation_script"] = ""
        payload = CustomScriptPayload.model_validate(
            kwargs | {"id": str(uuid4()), "created_at": create_time, "updated_at": create_time}
        )
        remote[payload.id] = CustomScript.from_api_payload(payload)
        return payload

    def fake_update_script(self, id, **kwargs):
        nonlocal called_counter
        called_counter["update"] += 1
        try:
            payload = script_to_response(remote[id])
        except KeyError:
            raise requests.exceptions.HTTPError(
                "Not Found", response=response_factory(404, {"detail": "No CustomScript matches the given query."})
            )
        payload = payload.model_copy(
            update=kwargs | {"updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        )
        remote[id] = CustomScript.from_api_payload(payload)
        return payload

    def fake_delete_script(self, id):
        nonlocal called_counter
        called_counter["delete"] += 1
        del remote[id]
        return

    monkeypatch.setattr("iructl.api.scripts.CustomScriptsResource.get", fake_get_script)
    monkeypatch.setattr("iructl.api.scripts.CustomScriptsResource.list", fake_list_scripts)
    monkeypatch.setattr("iructl.api.scripts.CustomScriptsResource.create", fake_create_script)
    monkeypatch.setattr("iructl.api.scripts.CustomScriptsResource.update", fake_update_script)
    monkeypatch.setattr("iructl.api.scripts.CustomScriptsResource.delete", fake_delete_script)

    return called_counter


@pytest.fixture
def scripts_response(
    scripts_lrc: tuple[Repository[CustomScript], Repository[CustomScript], ChangesDict[CustomScript]],
) -> PayloadList:
    _, remote, _ = scripts_lrc
    response = [script_to_response(script) for script in remote.values()]
    return PayloadList(count=len(response), results=response)
