import json
from contextlib import chdir
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._constants import APP_BRANDING, ROOT_MARKER
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
)
from tests.fixtures.scripts import (
    patch_scripts_endpoints,
    script_content,
    script_directory_factory,
    script_info_content_factory,
    script_info_data_factory,
    scripts_lrc,
    scripts_repo,
    scripts_repo_obj,
)

runner = CliRunner()


@pytest.mark.usefixtures("tmp_path_cd", "patch_profiles_endpoints", "patch_scripts_endpoints")
@pytest.mark.parametrize(
    "relative_path",
    [
        Path(),
        Path("subdir"),
    ],
)
def test_quickstart(relative_path):
    """Test the quickstart workflow."""
    iructl_repo_path = relative_path / "test_repo"
    iructl_repo_path.parent.mkdir(parents=True, exist_ok=True)

    # Create a new repository
    result = runner.invoke(app, ["new", str(iructl_repo_path)])
    assert result.exit_code == 0
    assert f"Created a new {APP_BRANDING} repository at" in result.stdout

    assert (iructl_repo_path / "README.md").is_file()
    assert (iructl_repo_path / ROOT_MARKER).is_file()

    with chdir(iructl_repo_path):
        # Pull profiles and scripts from the repository
        for resource_cmd in ("profile", "script"):
            # Pull the resource
            result = runner.invoke(app, [resource_cmd, "pull", "--all"])
            assert result.exit_code == 0

            # List the resource
            result = runner.invoke(app, [resource_cmd, "list", "--format", "json"])
            assert result.exit_code == 0
            resource_list = json.loads(result.stdout)
            assert len(resource_list) == 9


@pytest.mark.usefixtures("iructl_repo_cd")
def test_create_delete():
    """Test the create and delete workflow."""
    for resource_cmd in ("profile", "script"):
        # Create the new resource
        result = runner.invoke(app, [resource_cmd, "new", "--name", f"new_{resource_cmd}", "--info-format", "json"])
        assert result.exit_code == 0
        resource_path = Path(f"{resource_cmd}s/new_{resource_cmd}")
        assert resource_path.is_dir()
        new_obj = json.loads((resource_path / "info.json").read_text())
        assert new_obj["name"] == f"new_{resource_cmd}"

        # Show the new resource
        result = runner.invoke(app, [resource_cmd, "show", str(resource_path), "--format", "json"])
        assert result.exit_code == 0
        show_out = json.loads(result.stdout)
        for key in new_obj.keys():
            assert new_obj[key] == show_out[key]

        # List the new resource
        result = runner.invoke(app, [resource_cmd, "list", "--local", "--format", "json"])
        assert result.exit_code == 0
        new_obj_list = json.loads(result.stdout)
        assert len(new_obj_list) == 1

        # Delete the new resource
        result = runner.invoke(app, [resource_cmd, "delete", "--local", "--force", "--path", str(resource_path)])
        assert result.exit_code == 0
        assert not resource_path.is_dir()
