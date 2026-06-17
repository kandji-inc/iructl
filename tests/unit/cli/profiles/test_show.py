import logging
import random
import re
from contextlib import nullcontext

import pytest
import typer

from iructl._cli.utility import get_member
from iructl.repository import CustomProfile


@pytest.mark.parametrize(
    ("profile_arg", "pass_remote", "expectation", "exit_code", "log_message"),
    [
        pytest.param(
            "valid_id",
            False,
            nullcontext(),
            None,
            "",
            id="valid-local-id",
        ),
        pytest.param(
            "valid_path",
            False,
            nullcontext(),
            None,
            "",
            id="valid-local-path",
        ),
        pytest.param(
            "valid_id",
            True,
            nullcontext(),
            None,
            "",
            id="valid-remote-id",
        ),
        pytest.param(
            "valid_path",
            True,
            nullcontext(),
            None,
            "",
            id="valid-remote-path",
        ),
        pytest.param(
            "abcdefg",
            False,
            pytest.raises(typer.BadParameter),
            2,
            r"is not a valid ID or existing path\. Please double-check the lookup value\.",
            id="not-id-or-path",
        ),
        pytest.param(
            "60829610-877C-472F-9659-F5D59D7E4B2B",
            False,
            pytest.raises(typer.BadParameter),
            2,
            r"Member with ID [\w\-]+ not found in local repository at",
            id="non-existent-local-id",
        ),
        pytest.param(
            "60829610-877C-472F-9659-F5D59D7E4B2B",
            True,
            pytest.raises(typer.BadParameter),
            2,
            r"Member with ID [\w\-]+ not found in Iru",
            id="non-existent-remote-id",
        ),
        pytest.param(
            "/tmp/path/to/profile",
            False,
            pytest.raises(typer.BadParameter),
            2,
            r"is not a valid ID or existing path\. Please double-check the lookup value\.",
            id="non-existent-path",
        ),
        pytest.param(
            "./profiles",
            False,
            pytest.raises(typer.BadParameter),
            2,
            r"Found \d+ items at path",
            id="multiple-profiles",
        ),
    ],
)
@pytest.mark.usefixtures("iructl_repo_cd", "patch_profiles_endpoints")
def test_get_profile(
    caplog,
    profiles_lrc,
    patch_profiles_endpoints,
    config,
    profile_arg,
    pass_remote,
    expectation,
    exit_code,
    log_message,
):
    caplog.set_level(logging.DEBUG)

    # replace valid_id and valid_path with actual values
    local, remote, _ = profiles_lrc
    repo = remote if pass_remote else local
    profile_id = profile_arg
    if profile_arg == "valid_id":
        profile_id = random.choice(list(repo.keys()))
        profile_arg = profile_id
    if profile_arg == "valid_path":
        profile_id = random.choice([k for k in repo if k in local])
        profile_arg = local[profile_id].profile_path

    result = None
    with expectation as ctx:
        result = get_member(
            config=config, member_type=CustomProfile, key=str(profile_arg), repo=local.root, remote=pass_remote
        )
    if ctx is None:
        # Tests for successful return

        # If remote was passed then get_profile should have called the get endpoint
        assert patch_profiles_endpoints["list"] == (1 if pass_remote else 0)

        # If function returned it should match the profile from the relevant repo
        if result is not None:
            assert result == repo[profile_id]
    else:
        # Tests for raised Exception
        assert ctx.value.exit_code == exit_code
        assert re.search(log_message, caplog.text) is not None
