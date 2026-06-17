import pytest

from iructl.api import CustomAppPayload


class TestCustomAppPayloadFileName:
    @pytest.mark.parametrize(
        ("file_key", "expected"),
        [
            # The basename is taken from the full file_key path.
            ("tenants/1/library/custom_apps/MyApp_d00561c3.pkg", "MyApp.pkg"),
            # Token appended before the extension is removed.
            ("MyApp_d00561c3.pkg", "MyApp.pkg"),
            # Underscores in the original name are preserved; only the final token is stripped.
            ("my_under_score_1584c902.pkg", "my_under_score.pkg"),
            # Token match is case-insensitive.
            ("MyApp_AB12CD34.dmg", "MyApp.dmg"),
            # A name without a trailing token is returned unchanged.
            ("MyApp.pkg", "MyApp.pkg"),
            # Only the last token is stripped (a double-suffixed key drops one level).
            ("MyApp_df5f1408_7737ec3a.zip", "MyApp_df5f1408.zip"),
        ],
    )
    def test_strips_upload_token_from_basename(self, file_key, expected):
        payload = CustomAppPayload.model_construct(file_key=file_key)
        assert payload.file_name == expected


def _package_payload(**overrides) -> dict:
    data = {
        "id": "03fd3564-1e29-433f-8e67-6f987f3d242d",
        "name": "ms_company_portal",
        "sha256": "random-sha-sum",
        "file_key": "tenants/1/library/custom_apps/CompanyPortal_0f04055b.pkg",
        "file_url": "https://example.com/file",
        "file_size": 1234,
        "file_updated": "2024-01-20T20:07:48Z",
        "install_type": "package",
        "install_enforcement": "install_once",
        "unzip_location": "",
        "restart": False,
        "audit_script": "",
        "preinstall_script": "",
        "postinstall_script": "",
        "active": True,
        "created_at": "2024-01-20T20:07:53.612150Z",
        "updated_at": "2024-02-26T22:31:53.632505Z",
    }
    return data | overrides


class TestCustomAppPayloadUnzipLocation:
    @pytest.mark.parametrize(
        ("unzip_location", "expected"),
        [
            # The API's empty-string sentinel for non-zip apps normalizes to None.
            ("", None),
            # A real unzip location is preserved unchanged.
            ("/var/tmp", "/var/tmp"),
        ],
    )
    def test_normalizes_blank_sentinel(self, unzip_location, expected):
        payload = CustomAppPayload.model_validate(_package_payload(unzip_location=unzip_location))
        assert payload.unzip_location == expected
