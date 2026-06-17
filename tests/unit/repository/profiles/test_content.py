import plistlib
from contextlib import nullcontext
from pathlib import Path

import pytest

from iructl.exceptions import InvalidProfileError
from iructl.repository import File, Mobileconfig


@pytest.fixture
def mobileconfig_obj(mobileconfig_content) -> Mobileconfig:
    """Return a Mobileconfig object with valid profile data."""
    return Mobileconfig(content=mobileconfig_content)


@pytest.fixture
def mobileconfig_obj_with_path(request, tmp_path, mobileconfig_obj) -> Mobileconfig:
    mobileconfig_obj.path = tmp_path / "profile.mobileconfig"
    return mobileconfig_obj


class TestFile:
    @pytest.mark.parametrize("value", [None, Path("."), Path("test/path")])
    @pytest.mark.usefixtures("tmp_path_cd")
    def test_ensure_absolute_paths(self, value):
        if value is None:
            assert File.ensure_absolute_paths(value) is None
        else:
            resolved_path = File.ensure_absolute_paths(value)
            assert resolved_path is not None
            assert resolved_path.is_absolute()

    def test_write_successful(self, mobileconfig_obj_with_path):
        assert isinstance(mobileconfig_obj_with_path.path, Path)

        assert not mobileconfig_obj_with_path.path.exists()
        mobileconfig_obj_with_path.write()
        assert mobileconfig_obj_with_path.path.exists()

    def test_diff_hash(self, mobileconfig_obj):
        """Test that the diff_hash property is correctly calculated."""
        original_hash = mobileconfig_obj.diff_hash
        original_content = mobileconfig_obj.content

        # Hash should always exist in a CustomProfile object
        assert original_hash is not None

        # Hash should change with a change in the value
        data = plistlib.loads(original_content.encode("utf-8"))
        data["PayloadDisplayName"] = "New Payload Display Name"
        mobileconfig_obj.content = plistlib.dumps(data, fmt=plistlib.FMT_XML).decode("utf-8")
        assert mobileconfig_obj.diff_hash != original_hash

        # Hash should be reverted when the value is set back to the original
        mobileconfig_obj.content = original_content
        assert mobileconfig_obj.diff_hash == original_hash


class TestMobileconfig:
    def test_dump_fields(self, mobileconfig_obj):
        dumped = mobileconfig_obj.model_dump()
        assert set(dumped.keys()) == {"content"}

    @pytest.mark.parametrize(
        ("value", "expectation"),
        [
            pytest.param(None, nullcontext(), id="none"),
            pytest.param(Path("profile.mobileconfig"), nullcontext(), id="default"),
            pytest.param(Path("A Neat Profile.mobileconfig"), nullcontext(), id="altered-stem"),
            pytest.param(
                Path("profile.invalid"),
                pytest.raises(ValueError, match=r"Invalid mobileconfig file extension:"),
                id="invalid-suffix",
            ),
        ],
    )
    def test_ensure_valid_extension(self, value, expectation):
        if value is None:
            assert Mobileconfig.ensure_valid_extension(value) is None
        else:
            with expectation:
                Mobileconfig.ensure_valid_extension(v=value)

    def test_data_property(self, mobileconfig_obj, mobileconfig_data):
        assert isinstance(mobileconfig_obj.data, dict)
        assert mobileconfig_obj.data["PayloadUUID"] == mobileconfig_data["id"]
        assert mobileconfig_obj.data["PayloadDisplayName"] == mobileconfig_data["name"]
        assert mobileconfig_obj.data["PayloadIdentifier"] == mobileconfig_data["mdm_identifier"]
        assert (
            mobileconfig_obj.data["PayloadContent"][0][mobileconfig_data["payload_key"]]
            == mobileconfig_data["payload_value"]
        )

    def test_load_successful(self, mobileconfig_file):
        file = Mobileconfig.load(mobileconfig_file)
        assert isinstance(file, Mobileconfig)
        if mobileconfig_file.read_bytes()[:8] == b"bplist00":
            file_content = plistlib.dumps(plistlib.loads(mobileconfig_file.read_bytes()), fmt=plistlib.FMT_XML).decode(
                "utf-8"
            )
        else:
            file_content = mobileconfig_file.read_text()
            assert file.content == file_content

    def test_load_invalid_profile(self, tmp_path):
        """Test that the load method raises an error when the profile is invalid."""
        profile_path = tmp_path / "profile.mobileconfig"
        profile_path.write_text("invalid content")
        with pytest.raises(InvalidProfileError, match="is in an invalid format"):
            Mobileconfig.load(profile_path)

    def test_write(self, mobileconfig_obj_with_path):
        assert isinstance(mobileconfig_obj_with_path.path, Path)

        assert not mobileconfig_obj_with_path.path.exists()
        mobileconfig_obj_with_path.write()
        assert mobileconfig_obj_with_path.path.exists()

        with mobileconfig_obj_with_path.path.open("r") as file:
            assert file.read() == mobileconfig_obj_with_path.content
