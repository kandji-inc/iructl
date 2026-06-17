import plistlib
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from iructl._constants import DEFAULT_APP_CATEGORY
from iructl.api.payload import CustomAppPayload
from iructl.repository import (
    APP_INFO_HASH_KEYS,
    SUFFIX_MAP,
    AppFile,
    AppInfoFile,
    InfoFormat,
    InstallEnforcement,
    InstallType,
)


@pytest.fixture
def app_info_file_obj_zip(app_info_data_factory) -> AppInfoFile:
    """An app with install_type=zip, self-service enabled, NOT NO_ENFORCEMENT — every
    hash key can be mutated independently without tripping a cascading validator."""
    return AppInfoFile.model_validate(
        app_info_data_factory(
            install_type=InstallType.ZIP,
            install_enforcement=InstallEnforcement.INSTALL_ONCE,
            unzip_location="/Applications",
            show_in_self_service=True,
        )
    )


@pytest.fixture
def app_info_file_obj_without_self_service(app_info_data_factory) -> AppInfoFile:
    return AppInfoFile.model_validate(
        app_info_data_factory(
            install_type=InstallType.PACKAGE,
            install_enforcement=InstallEnforcement.INSTALL_ONCE,
            show_in_self_service=False,
        )
    )


class TestAppFile:
    def test_requires_name_and_sha256(self):
        with pytest.raises(ValidationError):
            AppFile.model_validate({})
        with pytest.raises(ValidationError):
            AppFile.model_validate({"name": "MyApp.pkg"})
        with pytest.raises(ValidationError):
            AppFile.model_validate({"sha256": "deadbeef" * 8})

    def test_forbids_extra_fields(self):
        with pytest.raises(ValidationError):
            AppFile.model_validate({"name": "MyApp.pkg", "sha256": "x" * 64, "path": "/tmp"})


class TestAppInfoFile:
    def test_dump_fields(self, app_info_file_obj):
        dumped = app_info_file_obj.model_dump()
        assert set(dumped.keys()) == {
            "id",
            "name",
            "active",
            "created_at",
            "updated_at",
            "sync_hash",
            "ensure_blueprints",
            "install_type",
            "install_enforcement",
            "restart",
            "unzip_location",
            "show_in_self_service",
            "self_service_category_id",
            "self_service_recommended",
            "file",
        }
        assert set(dumped["file"].keys()) == {"name", "sha256"}

    def test_created_at_none_until_pushed(self, app_info_data_factory):
        """A locally-authored app carries no created_at/updated_at until its first push.

        The value is no longer synthesized via a default_factory, so it is not written to
        disk and cannot regenerate a fresh "now" on each read (SYS-2789 finding F3).
        """
        data = app_info_data_factory()
        data.pop("created_at", None)
        data.pop("updated_at", None)
        info = AppInfoFile.model_validate(data)
        assert info.created_at is None
        assert info.updated_at is None
        assert "created_at" not in info.model_dump(exclude_unset=True, exclude_none=True)

    @pytest.mark.parametrize("key", APP_INFO_HASH_KEYS, ids=lambda key: f"modifying_{key}")
    def test_diff_hash(self, app_info_file_obj_zip, key: str):
        """Hash should change when any hash key changes, and revert when restored.

        Uses setattr (not full model_validate) for most keys to avoid re-triggering the
        mode=before update_self_service_options validator, matching the scripts test
        pattern. install_type requires special handling because its mode=after validator
        couples it to unzip_location.
        """
        info_obj = app_info_file_obj_zip
        original_hash = info_obj.diff_hash
        assert original_hash is not None

        if key == "install_type":
            new_data = info_obj.model_dump()
            new_data["install_type"] = "package" if info_obj.install_type is InstallType.ZIP else "zip"
            new_data["unzip_location"] = "/Applications" if new_data["install_type"] == "zip" else None
            mutated = AppInfoFile.model_validate(new_data)
            assert mutated.diff_hash != original_hash
            return

        # Walk dotted keys (e.g., "file.name") down to the parent object so setattr
        # mutates the leaf in place.
        target: Any = info_obj
        *parents, leaf = key.split(".")
        for parent_attr in parents:
            target = getattr(target, parent_attr)
        original_value = getattr(target, leaf)

        if isinstance(original_value, bool):
            setattr(target, leaf, not original_value)
        elif isinstance(original_value, InstallEnforcement):
            # Avoid NO_ENFORCEMENT — it triggers self-service auto-fill cascade.
            new_value = next(
                v for v in InstallEnforcement if v is not original_value and v is not InstallEnforcement.NO_ENFORCEMENT
            )
            setattr(target, leaf, new_value)
        elif isinstance(original_value, str):
            setattr(target, leaf, f"new-{original_value}")
        else:
            pytest.fail(f"Unsupported type for key {key}: {type(original_value).__name__}")
        assert info_obj.diff_hash != original_hash

        # show_in_self_service: validator force-fills it back to True on full
        # re-validation, so revert-via-setattr also doesn't restore the original hash
        # (the values setattr leaves behind diverge from the validator-cleaned baseline).
        if leaf != "show_in_self_service":
            setattr(target, leaf, original_value)
            assert info_obj.diff_hash == original_hash

    def test_diff_hash_ignores_file_name_change(self, app_info_file_obj_zip):
        """Renaming the installer must NOT change diff_hash.

        The upload endpoint rewrites the file name with a per-upload token, so file.name is
        unstable round-tripping through the API. Binary identity is file.sha256; file.name is
        local-only metadata excluded from the hash (renaming an uploaded binary is unsupported).
        """
        info_obj = app_info_file_obj_zip
        original_hash = info_obj.diff_hash

        info_obj.file.name = "RenamedApp.pkg"

        assert info_obj.diff_hash == original_hash

    def test_diff_hash_stable_across_formats(self, app_info_file_obj_zip, tmp_path):
        """Same content written and reloaded through plist/json/yaml produces the same hash."""
        hashes = []
        for suffix in SUFFIX_MAP:
            info_path = tmp_path / f"info{suffix}"
            app_info_file_obj_zip.format = SUFFIX_MAP[suffix]
            app_info_file_obj_zip.path = info_path
            app_info_file_obj_zip.write()
            reloaded = AppInfoFile.load(info_path)
            hashes.append(reloaded.diff_hash)

        assert len(set(hashes)) == 1, f"diff_hash differs across formats: {dict(zip(SUFFIX_MAP, hashes))}"

    def test_write_load_round_trip(self, app_info_file_obj_with_path):
        """Round-trip through write() and load() for each supported format."""
        info_obj = app_info_file_obj_with_path
        assert not info_obj.path.exists()
        info_obj.write()
        assert info_obj.path.exists()

        reloaded = AppInfoFile.load(info_obj.path)
        assert reloaded.diff_hash == info_obj.diff_hash
        assert reloaded.model_dump(exclude={"path", "format"}) == info_obj.model_dump(exclude={"path", "format"})

    def test_zip_requires_unzip_location(self, app_info_data_factory):
        data = app_info_data_factory(install_type=InstallType.ZIP, unzip_location="/Applications")
        del data["unzip_location"]
        with pytest.raises(ValidationError, match="unzip_location is required when install_type is 'zip'"):
            AppInfoFile.model_validate(data)

    @pytest.mark.parametrize("install_type", [InstallType.PACKAGE, InstallType.IMAGE])
    def test_non_zip_rejects_unzip_location(self, app_info_data_factory, install_type):
        data = app_info_data_factory(install_type=install_type)
        data["unzip_location"] = "/Applications"
        with pytest.raises(ValidationError, match="unzip_location is only valid when install_type is 'zip'"):
            AppInfoFile.model_validate(data)

    def test_zip_accepts_unzip_location(self, app_info_data_factory):
        data = app_info_data_factory(install_type=InstallType.ZIP, unzip_location="/Applications")
        info = AppInfoFile.model_validate(data)
        assert info.install_type is InstallType.ZIP
        assert info.unzip_location == "/Applications"

    def test_no_enforcement_triggers_self_service_autofill(self, app_info_file_obj_without_self_service):
        """install_enforcement=NO_ENFORCEMENT forces self-service on (mirrors live API rule,
        verified by tests/unit/api/apps/test_live_payload_shape.py::
        test_no_enforcement_requires_self_service)."""
        info_obj = app_info_file_obj_without_self_service
        assert info_obj.install_enforcement is not InstallEnforcement.NO_ENFORCEMENT
        assert info_obj.show_in_self_service is False

        info_obj_copy = info_obj.model_copy()
        info_obj_copy.install_enforcement = InstallEnforcement.NO_ENFORCEMENT

        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == DEFAULT_APP_CATEGORY
        assert info_obj_copy.self_service_recommended is False

    def test_set_self_service_options(self, app_info_file_obj_without_self_service):
        """Mirrors TestScriptInfoFile.test_set_self_service_options for app fields."""
        info_obj = app_info_file_obj_without_self_service
        assert info_obj.show_in_self_service is False
        assert info_obj.self_service_category_id is None
        assert info_obj.self_service_recommended is None

        info_obj_copy = info_obj.model_copy()
        info_obj_copy.show_in_self_service = True
        assert info_obj_copy.self_service_category_id == DEFAULT_APP_CATEGORY
        assert info_obj_copy.self_service_recommended is False

        info_obj_copy = info_obj.model_copy()
        info_obj_copy.self_service_recommended = True
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == DEFAULT_APP_CATEGORY

        info_obj_copy = info_obj.model_copy()
        info_obj_copy.self_service_category_id = "Custom Category"
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == "Custom Category"
        assert info_obj_copy.self_service_recommended is False

        info_obj_copy = info_obj.model_copy()
        category_id = str(uuid4())
        info_obj_copy.self_service_category_id = category_id
        assert info_obj_copy.show_in_self_service is True
        assert info_obj_copy.self_service_category_id == category_id

    def test_self_service_remains_off_when_no_hints_given(self, app_info_file_obj_without_self_service):
        info_obj = app_info_file_obj_without_self_service
        assert info_obj.show_in_self_service is False
        assert info_obj.self_service_category_id is None
        assert info_obj.self_service_recommended is None


class TestAppInfoFileFormats:
    def test_load_plist(self, app_info_content_factory, tmp_path):
        info_path = tmp_path / "info.plist"
        info_path.write_bytes(
            plistlib.dumps(plistlib.loads(app_info_content_factory(InfoFormat.PLIST).encode()), fmt=plistlib.FMT_XML)
        )
        info = AppInfoFile.load(info_path)
        assert info.path == info_path.resolve()
        assert info.format is InfoFormat.PLIST

    def test_load_json(self, app_info_content_factory, tmp_path):
        info_path = tmp_path / "info.json"
        info_path.write_text(app_info_content_factory(InfoFormat.JSON))
        info = AppInfoFile.load(info_path)
        assert info.format is InfoFormat.JSON

    @pytest.mark.parametrize("suffix", [".yaml", ".yml"])
    def test_load_yaml(self, app_info_content_factory, tmp_path, suffix):
        info_path = tmp_path / f"info{suffix}"
        info_path.write_text(app_info_content_factory(InfoFormat.YAML))
        info = AppInfoFile.load(info_path)
        assert info.format is InfoFormat.YAML
