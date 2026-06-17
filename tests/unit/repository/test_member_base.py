import pytest

from iructl.api import CustomScriptPayload, CustomScriptsResource
from iructl.exceptions import DuplicateScriptError, InvalidScriptError
from iructl.repository import BlueprintAssignment, CustomProfile
from iructl.repository.content import Mobileconfig, Script
from iructl.repository.info import ScriptInfoFile
from iructl.repository.member_base import ContentChild, MemberBase, MemberConfig, child_path
from tests.fixtures.profiles import profile_to_response


def _config_with(specs: tuple[ContentChild, ...]) -> MemberConfig:
    """Build a MemberConfig with arbitrary content specs for guard tests."""
    return MemberConfig(
        member_name="thing",
        resource_cls=CustomScriptsResource,
        content_specs=specs,
        invalid_error=InvalidScriptError,
        duplicate_error=DuplicateScriptError,
    )


class TestContentChildGuard:
    """__pydantic_init_subclass__ enforces that each content spec has a matching field and path property."""

    def test_consistent_declaration_is_accepted(self):
        """A field, spec, and path property that agree define the class without error."""

        class _Good(MemberBase[ScriptInfoFile, CustomScriptPayload]):
            directory_name = "things"
            _config = _config_with((ContentChild(attribute="audit", payload_field="script", content_cls=Script),))
            info: ScriptInfoFile
            audit: Script | None = None
            audit_path = child_path("audit")

        assert "audit" in _Good.model_fields

    def test_spec_without_field_raises(self):
        """A content spec with no matching model field is a loud class-definition error."""
        with pytest.raises(TypeError, match="no matching model field"):

            class _Bad(MemberBase[ScriptInfoFile, CustomScriptPayload]):
                directory_name = "things"
                _config = _config_with((ContentChild(attribute="audit", payload_field="script", content_cls=Script),))
                info: ScriptInfoFile

    def test_field_type_mismatch_raises(self):
        """A field whose type does not include the spec's content_cls is rejected."""
        with pytest.raises(TypeError, match="does not include its content spec"):

            class _Bad(MemberBase[ScriptInfoFile, CustomScriptPayload]):
                directory_name = "things"
                _config = _config_with(
                    (ContentChild(attribute="audit", payload_field="script", content_cls=Mobileconfig),)
                )
                info: ScriptInfoFile
                audit: Script | None = None
                audit_path = child_path("audit")

    def test_spec_without_path_property_raises(self):
        """A content spec with a field but no <attr>_path property is rejected."""
        with pytest.raises(TypeError, match="missing 'audit_path' property"):

            class _Bad(MemberBase[ScriptInfoFile, CustomScriptPayload]):
                directory_name = "things"
                _config = _config_with((ContentChild(attribute="audit", payload_field="script", content_cls=Script),))
                info: ScriptInfoFile
                audit: Script | None = None


class TestContentChildDerivation:
    """filename and glob default from the content type's naming convention; explicit values win."""

    def test_script_child_is_prefix_named(self):
        """A Script child is named after its attribute with the .zsh suffix and a prefix glob."""
        spec = ContentChild(attribute="audit", payload_field="script", content_cls=Script)
        assert spec.filename == "audit.zsh"
        assert spec.glob == "audit*"

    def test_mobileconfig_child_is_extension_named(self):
        """A Mobileconfig child matches by extension, so its glob ignores the attribute name."""
        spec = ContentChild(attribute="profile", payload_field="profile", content_cls=Mobileconfig)
        assert spec.filename == "profile.mobileconfig"
        assert spec.glob == "*.mobileconfig"


class TestSynced:
    """synced builds the post-sync local member: merge the remote (if any) then stamp sync_hash."""

    def test_merges_remote_and_stamps_sync_hash(self, custom_profile_obj):
        local = custom_profile_obj
        local.info.sync_hash = "stale"
        local.info.ensure_blueprints = [BlueprintAssignment(blueprint="11111111-1111-1111-1111-111111111111")]
        remote = CustomProfile.from_api_payload(profile_to_response(local))
        remote.info.name = "Renamed Remotely"

        synced = CustomProfile.synced(local, remote)

        assert synced.info.name == "Renamed Remotely"  # remote field merged in
        assert synced.info.ensure_blueprints == local.info.ensure_blueprints  # local-only preserved
        assert synced.sync_hash == synced.diff_hash  # stamped to current state
        assert synced.sync_hash != "stale"

    def test_no_existing_copies_remote_and_stamps_without_mutating_it(self, custom_profile_obj):
        remote = CustomProfile.from_api_payload(profile_to_response(custom_profile_obj))
        assert remote.sync_hash is None

        synced = CustomProfile.synced(None, remote)

        assert synced is not remote
        assert synced.diff_hash == remote.diff_hash  # same content
        assert synced.sync_hash == synced.diff_hash  # stamped
        assert remote.sync_hash is None  # remote left untouched
