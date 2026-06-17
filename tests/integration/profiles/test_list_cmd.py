import json
import plistlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._diff import ChangeType
from iructl._utils import yaml

runner = CliRunner()


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="both"),
        pytest.param(["--local"], id="local"),
        pytest.param(["--remote"], id="remote"),
    ],
)
@pytest.mark.usefixtures("iructl_repo_cd")
def test_list_table(profiles_lrc, patch_profiles_endpoints, extra_args):
    _, _, changes = profiles_lrc

    result = runner.invoke(app, ["profile", "list", *extra_args])

    assert result.exit_code == 0

    # check that api is only called when not local only
    if "--local" in extra_args:
        assert all(v == 0 for k, v in patch_profiles_endpoints.items())
    else:
        assert all(v == 0 for k, v in patch_profiles_endpoints.items() if k != "list")
        assert patch_profiles_endpoints["list"] == 1

    for change_type in changes:
        for local_profile, remote_profile in changes[change_type]:
            if "--remote" not in extra_args and change_type != ChangeType.CREATE_REMOTE:
                assert local_profile is not None
                assert local_profile.id in result.stdout
            if "--local" not in extra_args and change_type != ChangeType.CREATE_LOCAL:
                assert remote_profile is not None
                assert remote_profile.id in result.stdout

        if any(arg in extra_args for arg in ["--local", "--remote"]):
            assert "New Remote Item" not in result.stdout
            assert "Updated Remote Item" not in result.stdout
            assert "New Local Item" not in result.stdout
            assert "Updated Local Item" not in result.stdout
            assert "No Pending Changes" not in result.stdout
            assert "Conflicting Changes" not in result.stdout
        else:
            assert "New Remote" in result.stdout
            assert "Updated Remote" in result.stdout
            assert "New Local" in result.stdout
            assert "Updated Local" in result.stdout
            assert "No Pending" in result.stdout
            assert "Conflicting" in result.stdout


@pytest.mark.parametrize(
    "only_arg",
    [
        pytest.param([], id="both"),
        pytest.param(["--local"], id="local"),
        pytest.param(["--remote"], id="remote"),
    ],
)
@pytest.mark.parametrize(
    ("format"),
    [
        pytest.param("yaml", id="yaml"),
        pytest.param("json", id="json"),
        pytest.param("plist", id="plist"),
    ],
)
@pytest.mark.usefixtures("iructl_repo_cd")
def test_list_format(profiles_lrc, patch_profiles_endpoints, format, only_arg):
    local, remote, changes = profiles_lrc
    outfile = Path("outfile")

    result = runner.invoke(app, ["profile", "list", "--output", str(outfile), "--format", format, *only_arg])

    assert result.exit_code == 0

    # check that api is only called when not local only
    if "--local" in only_arg:
        assert all(v == 0 for k, v in patch_profiles_endpoints.items())
    else:
        assert all(v == 0 for k, v in patch_profiles_endpoints.items() if k != "list")
        assert patch_profiles_endpoints["list"] == 1

    with outfile.open("rb") as f:
        match format:
            case "yaml":
                output = yaml.load(f)
            case "json":
                output = json.load(f)
            case "plist":
                output = plistlib.load(f)
            case _:
                raise ValueError("Invalid format")

    change_count = sum(len(v) for c, v in changes.items())
    assert len(output) == change_count - 1 if only_arg != [] else change_count

    for list_item in output:
        assert "id" in list_item
        local_profile = local.get(list_item["id"])
        remote_profile = remote.get(list_item["id"])

        if format == "plist" and (local_profile is None or only_arg == ["--remote"]):
            assert "local" not in list_item
        elif only_arg == ["--remote"]:
            assert list_item["local"] is None
        else:
            assert "local" in list_item

        if format == "plist" and (remote_profile is None or only_arg == ["--local"]):
            assert "remote" not in list_item
        elif only_arg == ["--local"]:
            assert list_item["remote"] is None
        else:
            assert "remote" in list_item

        if only_arg:
            assert "status" not in list_item
        else:
            assert "status" in list_item
            if list_item["status"] != ChangeType.CREATE_LOCAL:
                assert list_item["remote"] is not None
            if list_item["status"] != ChangeType.CREATE_REMOTE:
                assert list_item["local"] is not None
