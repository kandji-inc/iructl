import random
from uuid import uuid4

import pytest

from iructl._constants import DEFAULT_SCRIPT_CATEGORY
from iructl.repository import SCRIPT_INFO_HASH_KEYS, ExecutionFrequency, ScriptInfoFile


@pytest.fixture
def script_info_file_obj_with_self_service(script_info_data_factory):
    return ScriptInfoFile.model_validate(script_info_data_factory(show_in_self_service=True))


@pytest.fixture
def script_info_file_obj_without_self_service(script_info_data_factory):
    return ScriptInfoFile.model_validate(script_info_data_factory(show_in_self_service=False))


class TestScriptInfoFile:
    def test_dump_fields(self, script_info_file_obj):
        dumped = script_info_file_obj.model_dump()
        assert set(dumped.keys()) == {
            "id",
            "name",
            "active",
            "created_at",
            "updated_at",
            "sync_hash",
            "ensure_blueprints",
            "execution_frequency",
            "restart",
            "show_in_self_service",
            "self_service_category_id",
            "self_service_recommended",
        }

    @pytest.mark.parametrize(("key"), SCRIPT_INFO_HASH_KEYS, ids=lambda key: f"modifying_{key}")
    def test_diff_hash(self, script_info_file_obj_with_self_service, key: str):
        """Test that the diff_hash property is correctly calculated."""
        info_obj = script_info_file_obj_with_self_service
        original_hash = info_obj.diff_hash
        original_value = getattr(info_obj, key)

        # Hash should always exist in a CustomProfile object
        assert original_hash is not None

        # Hash should change with a change in the value
        if isinstance(original_value, bool):
            setattr(info_obj, key, not original_value)
        elif isinstance(original_value, ExecutionFrequency):
            setattr(
                info_obj,
                key,
                random.choice(list(set(ExecutionFrequency) - {original_value})),
            )
        elif isinstance(original_value, str):
            setattr(info_obj, key, "New Value")
        else:
            pytest.fail(f"Unsupported type for key: {key}")
        assert info_obj.diff_hash != original_hash

        # Hash should be reverted when the value is set back to the original
        # The exception for show in self service is a result of the self service
        # options being cleared when the value is set to False
        if key != "show_in_self_service":
            setattr(info_obj, key, original_value)
            assert info_obj.diff_hash == original_hash

    def test_set_self_service_options(self, script_info_file_obj_without_self_service):
        """Test that the self service options are set when show_in_self_service is True."""
        info_obj: ScriptInfoFile = script_info_file_obj_without_self_service
        assert info_obj.execution_frequency != ExecutionFrequency.NO_ENFORCEMENT
        assert info_obj.show_in_self_service is False
        assert info_obj.self_service_category_id is None
        assert info_obj.self_service_recommended is None

        # Ensure default options are set when execution_frequency is NO_ENFORCEMENT
        info_obj_copy = info_obj.model_copy()
        info_obj_copy.execution_frequency = ExecutionFrequency.NO_ENFORCEMENT
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == DEFAULT_SCRIPT_CATEGORY
        assert info_obj_copy.self_service_recommended is False

        # Ensure default options are set when show_in_self_service is True
        info_obj_copy = info_obj.model_copy()
        info_obj_copy.show_in_self_service = True
        assert info_obj_copy.self_service_category_id == DEFAULT_SCRIPT_CATEGORY
        assert info_obj_copy.self_service_recommended is False

        # Ensure default options are set when self_service_recommended is True
        info_obj_copy = info_obj.model_copy()
        info_obj_copy.self_service_recommended = True
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == DEFAULT_SCRIPT_CATEGORY

        # Ensure default options are set when self_service_category_id is set to a category name
        info_obj_copy = info_obj.model_copy()
        info_obj_copy.self_service_category_id = "Custom Category"
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == "Custom Category"
        assert info_obj_copy.self_service_recommended is False

        # Ensure default options are set when self_service_category_id is set to a category ID
        info_obj_copy = info_obj.model_copy()
        category_id = str(uuid4())
        info_obj_copy.self_service_category_id = category_id
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == category_id
        assert info_obj_copy.self_service_recommended is False

        # Ensure default options are not set when no self service options are provided
        info_obj_copy = info_obj.model_copy()
        info_obj_copy.execution_frequency = ExecutionFrequency.ONCE
        assert info_obj_copy.show_in_self_service is False
        info_obj_copy.show_in_self_service = False
        assert info_obj_copy.show_in_self_service is False
        info_obj_copy.self_service_category_id = None
        assert info_obj_copy.show_in_self_service is False
        info_obj_copy.self_service_recommended = None
        assert info_obj_copy.show_in_self_service is False
