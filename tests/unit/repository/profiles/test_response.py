from iructl.api import CustomProfilePayload, CustomScriptPayload, PayloadList

profile_json = {
    "id": "id",
    "name": "name",
    "active": True,
    "profile": "profile",
    "mdm_identifier": "mdm_identifier",
    "runs_on_mac": True,
    "runs_on_iphone": True,
    "runs_on_ipad": True,
    "runs_on_tv": True,
    "runs_on_vision": True,
    "runs_on_android": True,
    "runs_on_windows": True,
    "created_at": "created_at",
    "updated_at": "updated_at",
}

script_json = {
    "id": "id",
    "name": "name",
    "active": True,
    "execution_frequency": "execution_frequency",
    "restart": True,
    "script": "script",
    "remediation_script": "remediation_script",
    "show_in_self_service": True,
    "self_service_category_id": "cc6d8638-4499-4cc5-b043-496e0e6ed06a",
    "self_service_recommended": False,
    "created_at": "created_at",
    "updated_at": "updated_at",
}


class TestCustomProfilePayload:
    def test_custom_profile_payload_roundtrip(self):
        assert CustomProfilePayload.model_validate(profile_json).model_dump() == profile_json

    def test_custom_profile_payload_tabs_to_spaces(self):
        assert CustomProfilePayload.tabs_to_spaces("\tprofile data") == "    profile data"


class TestCustomScriptPayload:
    def test_custom_script_response_roundtrip(self):
        assert CustomScriptPayload.model_validate(script_json).model_dump() == script_json


def test_custom_script_response_list_roundtrip():
    profile_json_list = {"count": 1, "next": "next", "previous": "previous", "results": [profile_json]}
    assert PayloadList[CustomProfilePayload].model_validate(profile_json_list).model_dump() == profile_json_list

    script_json_list = {"count": 1, "next": "next", "previous": "previous", "results": [script_json]}
    assert PayloadList[CustomScriptPayload].model_validate(script_json_list).model_dump() == script_json_list
