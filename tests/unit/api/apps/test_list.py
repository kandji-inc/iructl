import pytest
from pydantic import ValidationError

from iructl.api import PayloadList


def test_successful_list(monkeypatch, response_factory, custom_apps_resource):
    def mock_get_request(self, url):
        json_data = {
            "count": 2,
            "next": None,
            "previous": None,
            "results": [
                {
                    "id": "03fd3564-1e29-433f-8e67-6f987f3d242d",
                    "name": "ms_company_portal",
                    "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/CompanyPortal_5.2310.5-Installer_0f04055b.pkg",
                    "install_type": "package",
                    "install_enforcement": "install_once",
                    "audit_script": "",
                    "unzip_location": "",
                    "active": True,
                    "restart": False,
                    "preinstall_script": "",
                    "postinstall_script": "",
                    "file_url": "shortlived S3 URL",
                    "file_size": 42375997,
                    "file_updated": "2024-01-20T20:07:48Z",
                    "created_at": "2024-01-20T20:07:53.612150Z",
                    "updated_at": "2024-02-26T22:31:53.632505Z",
                    "show_in_self_service": False,
                    "sha256": "extra-long-sha256-sum-for-testing",
                },
                {
                    "id": "23b8aae8-fbe5-4b83-aa0e-8af3b1d663fb",
                    "name": "Custom App New Name",
                    "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/Custom App New Name.dmg",
                    "install_type": "image",
                    "install_enforcement": "continuously_enforce",
                    "audit_script": "",
                    "unzip_location": "",
                    "active": False,
                    "restart": False,
                    "preinstall_script": "",
                    "postinstall_script": "",
                    "file_url": "shortlived S3 URL",
                    "file_size": 42375997,
                    "file_updated": "2024-01-20T20:07:48Z",
                    "created_at": "2024-01-20T20:07:53.612150Z",
                    "updated_at": "2024-02-26T22:31:53.632505Z",
                    "show_in_self_service": False,
                    "sha256": "another-long-sha256-sum-for-testing",
                },
            ],
        }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    response = custom_apps_resource.list()
    assert isinstance(response, PayloadList)
    assert response.results[0].id == "03fd3564-1e29-433f-8e67-6f987f3d242d"


def test_successful_list_with_pagination(monkeypatch, response_factory, custom_apps_resource):
    # Mock the first page
    def mock_get_request(self, url):
        if "offset=2" in url:
            json_data = {
                "count": 4,
                "next": None,  # No more pages
                "previous": "https://tenant.api.iru.com/api/v1/library/custom-apps?limit=2",
                "results": [
                    {
                        "id": "03fd3564-1e29-433f-8e67-6f987f3d242d",
                        "name": "ms_company_portal",
                        "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/CompanyPortal_5.2310.5-Installer_0f04055b.pkg",
                        "install_type": "package",
                        "install_enforcement": "install_once",
                        "audit_script": "",
                        "unzip_location": "",
                        "active": True,
                        "restart": False,
                        "preinstall_script": "",
                        "postinstall_script": "",
                        "file_url": "shortlived S3 URL",
                        "file_size": 42375997,
                        "file_updated": "2024-01-20T20:07:48Z",
                        "created_at": "2024-01-20T20:07:53.612150Z",
                        "updated_at": "2024-02-26T22:31:53.632505Z",
                        "show_in_self_service": False,
                        "sha256": "extra-long-sha256-sum-for-testing",
                    },
                    {
                        "id": "23b8aae8-fbe5-4b83-aa0e-8af3b1d663fb",
                        "name": "Custom App New Name",
                        "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/Custom App New Name.dmg",
                        "install_type": "image",
                        "install_enforcement": "continuously_enforce",
                        "audit_script": "",
                        "unzip_location": "",
                        "active": False,
                        "restart": False,
                        "preinstall_script": "",
                        "postinstall_script": "",
                        "file_url": "shortlived S3 URL",
                        "file_size": 42375997,
                        "file_updated": "2024-01-20T20:07:48Z",
                        "created_at": "2024-01-20T20:07:53.612150Z",
                        "updated_at": "2024-02-26T22:31:53.632505Z",
                        "show_in_self_service": False,
                        "sha256": "another-long-sha256-sum-for-testing",
                    },
                ],
            }
        else:  # Mocked first page response
            json_data = {
                "count": 4,
                "next": "https://tenant.api.iru.com/api/v1/library/custom-apps?limit=2&offset=2",
                "previous": None,
                "results": [
                    {
                        "id": "03fd3564-1e29-433f-8e67-6f987f3d242d",
                        "name": "ms_company_portal",
                        "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/CompanyPortal_5.2310.5-Installer_0f04055b.pkg",
                        "install_type": "package",
                        "install_enforcement": "install_once",
                        "audit_script": "",
                        "unzip_location": "",
                        "active": True,
                        "restart": False,
                        "preinstall_script": "",
                        "postinstall_script": "",
                        "file_url": "shortlived S3 URL",
                        "file_size": 42375997,
                        "file_updated": "2024-01-20T20:07:48Z",
                        "created_at": "2024-01-20T20:07:53.612150Z",
                        "updated_at": "2024-02-26T22:31:53.632505Z",
                        "show_in_self_service": False,
                        "sha256": "extra-long-sha256-sum-for-testing",
                    },
                    {
                        "id": "23b8aae8-fbe5-4b83-aa0e-8af3b1d663fb",
                        "name": "Custom App New Name",
                        "file_key": "companies/companies/28344fcc-579a-4d42-bcf7-cdbf97585066/library/custom_apps/Custom App New Name.dmg",
                        "install_type": "image",
                        "install_enforcement": "continuously_enforce",
                        "audit_script": "",
                        "unzip_location": "",
                        "active": False,
                        "restart": False,
                        "preinstall_script": "",
                        "postinstall_script": "",
                        "file_url": "shortlived S3 URL",
                        "file_size": 42375997,
                        "file_updated": "2024-01-20T20:07:48Z",
                        "created_at": "2024-01-20T20:07:53.612150Z",
                        "updated_at": "2024-02-26T22:31:53.632505Z",
                        "show_in_self_service": False,
                        "sha256": "another-long-sha256-sum-for-testing",
                    },
                ],
            }
        return response_factory(200, json_data)

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    response = custom_apps_resource.list()
    assert isinstance(response, PayloadList)
    # Assert that all results from both pages are included
    assert response.count == 4  # 2 apps on each page
    assert len(response.results) == response.count
    assert response.next is None
    assert response.previous is None


def test_list_json_response_error(monkeypatch, response_factory, custom_apps_resource):
    def mock_get_request(self, url):
        return response_factory(200, b"not a json response")

    monkeypatch.setattr("iructl.api.client.ApiClient.get", mock_get_request)
    with pytest.raises(ValidationError):
        custom_apps_resource.list()


@pytest.mark.allow_http
def test_successful_list_live(custom_apps_resource):
    response = custom_apps_resource.list()
    assert isinstance(response, PayloadList)
    assert response.next is None
    assert response.previous is None
    assert response.count > 0
    assert len(response.results) == response.count
