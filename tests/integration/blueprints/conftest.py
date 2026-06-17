from dataclasses import dataclass

import pytest

from iructl._diff import ChangesDict
from iructl.repository import CustomApp, CustomProfile, CustomScript, MemberBase
from tests.fixtures.apps import (
    app_directory_factory,
    app_info_content_factory,
    app_info_data_factory,
    apps_lrc,
    apps_remote,
    apps_repo,
    apps_repo_obj,
    custom_app_factory,
    patch_apps_endpoints,
)
from tests.fixtures.blueprints import (
    declare_blueprints,
    make_http_error,
    patch_blueprints_assign,
    patch_blueprints_list,
    report_file,
)
from tests.fixtures.profiles import (
    mobileconfig_content,
    mobileconfig_content_factory,
    mobileconfig_data,
    mobileconfig_data_factory,
    mobileconfig_file,
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
    script_file,
    script_info_content_factory,
    script_info_data_factory,
    scripts_lrc,
    scripts_repo,
    scripts_repo_obj,
)


@dataclass(frozen=True)
class _MemberType:
    noun: str
    model: type[MemberBase]
    lrc_fixture: str
    endpoints_fixture: str


@dataclass(frozen=True)
class MemberCase:
    noun: str
    model: type[MemberBase]
    changes: ChangesDict[MemberBase]


_MEMBER_TYPES = [
    _MemberType("app", CustomApp, "apps_lrc", "patch_apps_endpoints"),
    _MemberType("profile", CustomProfile, "profiles_lrc", "patch_profiles_endpoints"),
    _MemberType("script", CustomScript, "scripts_lrc", "patch_scripts_endpoints"),
]


@pytest.fixture(params=_MEMBER_TYPES, ids=lambda m: m.noun)
def member_case(request: pytest.FixtureRequest) -> MemberCase:
    member_type = request.param
    request.getfixturevalue("iructl_repo_cd")
    request.getfixturevalue(member_type.endpoints_fixture)
    _, _, changes = request.getfixturevalue(member_type.lrc_fixture)
    return MemberCase(noun=member_type.noun, model=member_type.model, changes=changes)


__all__ = [
    "app_directory_factory",
    "app_info_content_factory",
    "app_info_data_factory",
    "apps_lrc",
    "apps_remote",
    "apps_repo",
    "apps_repo_obj",
    "custom_app_factory",
    "declare_blueprints",
    "make_http_error",
    "member_case",
    "mobileconfig_content",
    "mobileconfig_content_factory",
    "mobileconfig_data",
    "mobileconfig_data_factory",
    "mobileconfig_file",
    "patch_apps_endpoints",
    "patch_blueprints_assign",
    "patch_blueprints_list",
    "patch_profiles_endpoints",
    "patch_scripts_endpoints",
    "profile_directory_factory",
    "profile_info_content_factory",
    "profile_info_data_factory",
    "profiles_lrc",
    "profiles_repo",
    "profiles_repo_obj",
    "report_file",
    "script_content",
    "script_directory_factory",
    "script_file",
    "script_info_content_factory",
    "script_info_data_factory",
    "scripts_lrc",
    "scripts_repo",
    "scripts_repo_obj",
]
