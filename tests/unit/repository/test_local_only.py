import pytest

from iructl.api.payload import CustomAppPayload, CustomProfilePayload, CustomScriptPayload
from iructl.repository import BlueprintAssignment, CustomProfile
from iructl.repository.info import InfoFile
from tests.fixtures.profiles import profile_to_response

# A representative local value per InfoFile.LOCAL_ONLY field, keyed by field name.
_LOCAL_ONLY_SAMPLES = {
    "sync_hash": "a" * 64,
    "ensure_blueprints": [BlueprintAssignment(blueprint="11111111-1111-1111-1111-111111111111")],
}


class TestLocalOnlyContract:
    @pytest.mark.parametrize("payload_cls", [CustomProfilePayload, CustomScriptPayload, CustomAppPayload])
    def test_no_local_only_field_in_remote_payload(self, payload_cls):
        """A local-only field has no counterpart in the remote contract; one appearing in a
        payload would mean it round-trips from remote and was misclassified."""
        assert not (InfoFile.LOCAL_ONLY & set(payload_cls.model_fields))

    def test_samples_cover_local_only(self):
        """Tripwire: the merge cases enumerate exactly LOCAL_ONLY, so a field cannot be added
        to the set without a merge-preservation case below."""
        assert set(_LOCAL_ONLY_SAMPLES) == set(InfoFile.LOCAL_ONLY)


class TestMergePreservesLocalOnly:
    @pytest.mark.parametrize(
        ("field", "local_value"), [pytest.param(name, value, id=name) for name, value in _LOCAL_ONLY_SAMPLES.items()]
    )
    def test_remote_merge_does_not_wipe_local_only_field(self, custom_profile_obj, field, local_value):
        """A remote member built from an API payload carries no local-only state, so merging it
        must not overwrite the locally-held value."""
        setattr(custom_profile_obj.info, field, local_value)
        remote = CustomProfile.from_api_payload(profile_to_response(custom_profile_obj))
        assert getattr(remote.info, field) is None

        updated = custom_profile_obj.updated(remote)

        assert getattr(updated.info, field) == local_value
