from uuid import uuid4

import pytest
from pydantic import ValidationError

from iructl._console import OutputFormat
from iructl.repository import (
    SUFFIX_MAP,
    AppInfoFile,
    BlueprintAssignment,
    CustomProfile,
    ProfileInfoFile,
    ScriptInfoFile,
)


@pytest.fixture
def blueprint_uuid() -> str:
    return str(uuid4())


@pytest.fixture
def info_obj(request, profile_info_data_factory, script_info_data_factory, app_info_data_factory):
    cls, factory = {
        "profile": (ProfileInfoFile, profile_info_data_factory),
        "script": (ScriptInfoFile, script_info_data_factory),
        "app": (AppInfoFile, app_info_data_factory),
    }[request.param]
    return cls.model_validate(factory())


_NEW_VALUE_CASES = [
    pytest.param([], id="empty-list"),
    pytest.param([{"blueprint": "11111111-1111-1111-1111-111111111111"}], id="non-empty"),
]


class TestCoercion:
    def test_bare_uuid_string_coerces_to_assignment(self, profile_info_data_factory, blueprint_uuid):
        info = ProfileInfoFile.model_validate(profile_info_data_factory() | {"ensure_blueprints": [blueprint_uuid]})
        assert info.ensure_blueprints == [BlueprintAssignment(blueprint=blueprint_uuid)]

    def test_mixed_str_and_dict_entries_coexist(self, profile_info_data_factory):
        first, second, node = str(uuid4()), str(uuid4()), str(uuid4())
        info = ProfileInfoFile.model_validate(
            profile_info_data_factory() | {"ensure_blueprints": [first, {"blueprint": second, "node": node}]}
        )
        assert info.ensure_blueprints == [
            BlueprintAssignment(blueprint=first),
            BlueprintAssignment(blueprint=second, node=node),
        ]

    def test_blueprint_case_preserved(self, profile_info_data_factory):
        """blueprint may be a name (or a UUID), so its case is preserved as declared."""
        info = ProfileInfoFile.model_validate(profile_info_data_factory() | {"ensure_blueprints": ["Production Macs"]})
        assert info.ensure_blueprints == [BlueprintAssignment(blueprint="Production Macs")]

    def test_node_uuid_normalized_to_lowercase(self, profile_info_data_factory, blueprint_uuid):
        node = str(uuid4()).upper()
        info = ProfileInfoFile.model_validate(
            profile_info_data_factory() | {"ensure_blueprints": [{"blueprint": blueprint_uuid, "node": node}]}
        )
        assert info.ensure_blueprints == [BlueprintAssignment(blueprint=blueprint_uuid, node=node.lower())]

    def test_empty_string_node_stored_literally(self, profile_info_data_factory, blueprint_uuid):
        """node is a standard str | None field; an empty string is not coerced to None."""
        info = ProfileInfoFile.model_validate(
            profile_info_data_factory() | {"ensure_blueprints": [{"blueprint": blueprint_uuid, "node": ""}]}
        )
        assert info.ensure_blueprints == [BlueprintAssignment(blueprint=blueprint_uuid, node="")]


class TestValidation:
    def test_extra_keys_rejected(self, blueprint_uuid):
        with pytest.raises(ValidationError, match=r"Extra inputs are not permitted"):
            BlueprintAssignment.model_validate({"blueprint": blueprint_uuid, "node": None, "extra": "nope"})

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param("not-a-list", id="string"),
            pytest.param(42, id="integer"),
            pytest.param({"blueprint": "x"}, id="bare-dict"),
        ],
    )
    def test_non_list_value_rejected(self, profile_info_data_factory, bad_value):
        """ensure_blueprints must be a list (or None); other types fall through to Pydantic's type guard."""
        with pytest.raises(ValidationError, match=r"Input should be a valid list"):
            ProfileInfoFile.model_validate(profile_info_data_factory() | {"ensure_blueprints": bad_value})


class TestRoundTrip:
    @pytest.mark.parametrize(
        "ensure_blueprints_value",
        [
            pytest.param([], id="empty-list"),
            pytest.param(
                [
                    {"blueprint": "11111111-1111-1111-1111-111111111111"},
                    {
                        "blueprint": "22222222-2222-2222-2222-222222222222",
                        "node": "33333333-3333-3333-3333-333333333333",
                    },
                ],
                id="non-empty",
            ),
            pytest.param([{"blueprint": "Production Macs"}], id="name"),
        ],
    )
    @pytest.mark.parametrize(
        "suffix",
        [pytest.param(s, id=s) for s in SUFFIX_MAP.keys()],
    )
    @pytest.mark.parametrize("info_obj", ["profile", "script", "app"], indirect=True)
    def test_preserves_ensure_blueprints(self, info_obj, suffix, ensure_blueprints_value, tmp_path):
        info_obj.ensure_blueprints = ensure_blueprints_value
        info_obj.format = SUFFIX_MAP[suffix]
        info_path = tmp_path / f"info{suffix}"
        info_obj.path = info_path
        info_obj.write()

        loaded = type(info_obj).load(info_path)
        assert loaded.ensure_blueprints == info_obj.ensure_blueprints


class TestDiffHashSensitivity:
    @pytest.mark.parametrize("info_obj", ["profile", "script", "app"], indirect=True)
    def test_diff_hash_changes_when_in_scope_field_mutated(self, info_obj):
        """Positive control: confirms diff_hash is sensitive to fields in HASH_KEYS (e.g. ``name``)."""
        original = info_obj.diff_hash
        info_obj.name = f"{info_obj.name}-renamed"
        assert info_obj.diff_hash != original

    @pytest.mark.parametrize("new_value", _NEW_VALUE_CASES)
    @pytest.mark.parametrize("info_obj", ["profile", "script", "app"], indirect=True)
    def test_diff_hash_unchanged_when_ensure_blueprints_set(self, info_obj, new_value):
        original = info_obj.diff_hash
        info_obj.ensure_blueprints = new_value
        assert info_obj.diff_hash == original

    @pytest.mark.parametrize("new_value", _NEW_VALUE_CASES)
    @pytest.mark.parametrize("info_obj", ["profile", "script", "app"], indirect=True)
    def test_diff_hash_unchanged_when_ensure_blueprints_reset_to_none(self, info_obj, new_value):
        info_obj.ensure_blueprints = new_value
        original = info_obj.diff_hash
        info_obj.ensure_blueprints = None
        assert info_obj.diff_hash == original


class TestPrepareSyntaxDict:
    @pytest.fixture
    def profile_with_assignments(self, custom_profile_obj) -> CustomProfile:
        custom_profile_obj.info.ensure_blueprints = [
            BlueprintAssignment(blueprint="11111111-1111-1111-1111-111111111111")
        ]
        return custom_profile_obj

    def test_excluded_when_preview_off(self, profile_with_assignments):
        assert "ensure_blueprints" not in profile_with_assignments.prepare_syntax_dict()

    def test_included_when_preview_on(self, profile_with_assignments):
        output = profile_with_assignments.prepare_syntax_dict(preview_mode=True)
        assert output["ensure_blueprints"] == [{"blueprint": "11111111-1111-1111-1111-111111111111", "node": None}]

    def test_plist_dump_handles_node_none_under_preview(self, profile_with_assignments):
        """plistlib.dumps rejects nested None; the XML branch must strip node=None from each entry."""
        profile_with_assignments.format_plain_text(OutputFormat.PLIST, preview_mode=True)
