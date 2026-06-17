from itertools import chain


def test_api_imports():
    import iructl.api

    expected_imports = {
        "ApiClient",
        "ApiConfig",
        "ApiPayload",
        "BlueprintPayload",
        "BlueprintsResource",
        "CustomAppPayload",
        "CustomAppUploadPayload",
        "CustomAppsResource",
        "CustomProfilePayload",
        "CustomProfilesResource",
        "CustomScriptPayload",
        "CustomScriptsResource",
        "ExecutionFrequency",
        "InstallEnforcement",
        "InstallType",
        "PayloadList",
        "S3Client",
        "SelfServiceCategoriesResource",
        "SelfServiceCategoryPayload",
        "is_duplicate_assignment",
    }

    for import_name in expected_imports:
        assert hasattr(iructl.api, import_name)

    assert set(iructl.api.__all__) == expected_imports


def test_model_imports():
    import iructl.repository

    common_imports = {
        "ACCEPTED_INFO_EXTENSIONS",
        "BlueprintAssignment",
        "File",
        "InfoFile",
        "InfoFormat",
        "MemberBase",
        "PushOutcome",
        "Repository",
        "RepositoryDirectory",
        "SUFFIX_MAP",
    }

    profile_imports = {
        "CustomProfile",
        "Mobileconfig",
        "PROFILE_INFO_HASH_KEYS",
        "PROFILE_RUNS_ON_PARAMS",
        "ProfileInfoFile",
    }

    script_imports = {
        "CustomScript",
        "ExecutionFrequency",
        "SCRIPT_INFO_HASH_KEYS",
        "Script",
        "ScriptInfoFile",
    }

    app_imports = {
        "APP_INFO_HASH_KEYS",
        "AppFile",
        "AppInfoFile",
        "CustomApp",
        "InstallEnforcement",
        "InstallType",
    }

    for import_name in chain(common_imports, profile_imports, script_imports, app_imports):
        assert hasattr(iructl.repository, import_name)

    assert set(iructl.repository.__all__) == common_imports | profile_imports | script_imports | app_imports
