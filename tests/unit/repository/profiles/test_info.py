from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from iructl.exceptions import InvalidInfoFileError
from iructl.repository import (
    PROFILE_INFO_HASH_KEYS,
    PROFILE_RUNS_ON_PARAMS,
    SUFFIX_MAP,
    InfoFile,
    InfoFormat,
    ProfileInfoFile,
)

VALID_INFO_SUFFIXES = list(SUFFIX_MAP.keys())


@pytest.fixture
def profile_info_file_obj(profile_info_data_factory) -> ProfileInfoFile:
    return ProfileInfoFile.model_validate(profile_info_data_factory())


@pytest.fixture(params=VALID_INFO_SUFFIXES, ids=VALID_INFO_SUFFIXES)
def profile_info_file_obj_with_path(request, tmp_path, profile_info_file_obj) -> ProfileInfoFile:
    profile_info_file_obj.format = SUFFIX_MAP[request.param]
    profile_info_file_obj.path = tmp_path / f"info{request.param}"
    return profile_info_file_obj


@pytest.fixture(params=VALID_INFO_SUFFIXES, ids=VALID_INFO_SUFFIXES)
def profile_info_file(request, tmp_path, profile_info_content_factory) -> Path:
    """Return the path to a valid profile info file."""
    info_path = tmp_path / f"info{request.param}"
    info_path.write_text(profile_info_content_factory(format_type=SUFFIX_MAP[request.param]))
    return info_path


class TestInfoFile:
    def test_id_is_lower(self, profile_info_file_obj):
        """Test that the id field is always uppercase."""
        info = profile_info_file_obj.model_dump()
        info["id"] = profile_info_file_obj.id.upper()
        profile = profile_info_file_obj.model_validate(info)
        assert profile.id.islower()
        profile.id = profile.id.upper()
        assert profile.id.islower()

    @pytest.mark.parametrize("value", [None, Path("."), Path("test/path")])
    @pytest.mark.usefixtures("tmp_path_cd")
    def test_ensure_absolute_paths(self, value):
        if value is None:
            assert InfoFile.ensure_absolute_paths(value) is None
        else:
            resolved_path = InfoFile.ensure_absolute_paths(value)
            assert resolved_path is not None
            assert resolved_path.is_absolute()

    @pytest.mark.parametrize(
        ("value", "expectation"),
        [
            pytest.param(None, nullcontext(), id="none"),
            pytest.param(Path("info.plist"), nullcontext(), id="valid"),
            pytest.param(
                Path("invalid.plist"), pytest.raises(ValueError, match=r"Invalid info file name\."), id="invalid-stem"
            ),
            pytest.param(
                Path("info.invalid"), pytest.raises(ValueError, match=r"Invalid info file name\."), id="invalid-suffix"
            ),
        ],
    )
    def test_ensure_valid_file_name(self, value, expectation):
        if value is None:
            assert InfoFile.ensure_valid_file_name(value) is None
        else:
            with expectation:
                InfoFile.ensure_valid_file_name(v=value)

    def test_load_successful(self, profile_info_file):
        info_file = ProfileInfoFile.load(profile_info_file)
        assert isinstance(info_file, ProfileInfoFile)
        assert info_file.path == profile_info_file
        assert info_file.format == SUFFIX_MAP[profile_info_file.suffix]

    def test_load_invalid_suffix(self, tmp_path, profile_info_content_factory):
        """Test that the load method raises an error when the info file has an invalid suffix."""
        info_path = tmp_path / "info.invalid"
        info_path.write_text(profile_info_content_factory(format_type=InfoFormat.PLIST))
        with pytest.raises(InvalidInfoFileError, match="does not have a valid suffix"):
            ProfileInfoFile.load(info_path)

    @pytest.mark.parametrize("suffix", VALID_INFO_SUFFIXES)
    def test_load_invalid_content(self, tmp_path, suffix):
        """Test that the load method raises an error when the info file has invalid content."""
        info_path = tmp_path / f"info{suffix}"
        info_path.write_text(":\n:invalid content")
        with pytest.raises(InvalidInfoFileError, match=f"is not a valid {SUFFIX_MAP[suffix]} file"):
            ProfileInfoFile.load(info_path)

    def test_write_successful(self, profile_info_file_obj_with_path):
        assert isinstance(profile_info_file_obj_with_path.path, Path)

        assert not profile_info_file_obj_with_path.path.exists()
        profile_info_file_obj_with_path.write()
        assert profile_info_file_obj_with_path.path.exists()

        # Test update existing file
        content1 = profile_info_file_obj_with_path.path.read_text()
        profile_info_file_obj_with_path.active = not profile_info_file_obj_with_path.active
        profile_info_file_obj_with_path.write()
        content2 = profile_info_file_obj_with_path.path.read_text()

        assert content1 != content2

    def test_write_no_path(self, profile_info_file_obj):
        with pytest.raises(ValueError, match=r"The info file has no path set\."):
            profile_info_file_obj.write()


class TestProfileInfoFile:
    @pytest.mark.parametrize(
        "runs_on",
        [
            pytest.param(
                {
                    "runs_on_mac": False,
                    "runs_on_iphone": False,
                    "runs_on_ipad": False,
                    "runs_on_tv": False,
                    "runs_on_vision": False,
                },
                id="all_false",
            ),
            pytest.param({"runs_on_mac": False}, id="mac_false"),
        ],
    )
    def test_invalid_runs_on(self, profile_info_file_obj, runs_on: dict):
        """Creating a CustomProfile object with no runs_on_* fields should raise a ValueError."""
        info_data = profile_info_file_obj.model_dump()
        # remove all runs_on keys from dict and merge with runs_on
        info_data = {k: v for k, v in info_data.items() if not k.startswith("runs_on")} | runs_on
        with pytest.raises(ValidationError, match=r"Value error, At least one runs_on_\* property must be True\."):
            ProfileInfoFile.model_validate(info_data)

    def test_dump_fields(self, profile_info_file_obj):
        dumped = profile_info_file_obj.model_dump()
        assert set(dumped.keys()) == {
            "id",
            "name",
            "mdm_identifier",
            "active",
            "created_at",
            "updated_at",
            "sync_hash",
            "ensure_blueprints",
            *PROFILE_RUNS_ON_PARAMS,
        }

    @pytest.mark.parametrize(("key"), PROFILE_INFO_HASH_KEYS, ids=lambda key: f"modifying_{key}")
    def test_diff_hash(self, profile_info_file_obj, key: str):
        """Test that the diff_hash property is correctly calculated."""
        for param in PROFILE_RUNS_ON_PARAMS:
            setattr(profile_info_file_obj, param, True)
        original_hash = profile_info_file_obj.diff_hash
        original_value = getattr(profile_info_file_obj, key)

        # Hash should always exist in a CustomProfile object
        assert original_hash is not None

        # Hash should change with a change in the value
        if isinstance(original_value, bool):
            setattr(profile_info_file_obj, key, not original_value)
        elif isinstance(original_value, str):
            setattr(profile_info_file_obj, key, "New Value")
        else:
            pytest.fail(f"Unsupported type for key: {key}")
        assert profile_info_file_obj.diff_hash != original_hash

        # Hash should be reverted when the value is set back to the original
        setattr(profile_info_file_obj, key, original_value)
        assert profile_info_file_obj.diff_hash == original_hash
