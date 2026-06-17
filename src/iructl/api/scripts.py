from enum import StrEnum

from .payload import CustomScriptPayload, PayloadList
from .resource_base import ResourceBase

SHOW_IN_SELF_SERVICE_EXAMPLE = """Example:
  "show_in_self_service": true
  "self_service_category_id": "ae492437-c35f-46a3-bd0b-21188a69dfb1"
"""


class ExecutionFrequency(StrEnum):
    """An enumeration of possible execution frequencies for a script."""

    ONCE = "once"
    EVERY_15_MIN = "every_15_min"
    EVERY_DAY = "every_day"
    NO_ENFORCEMENT = "no_enforcement"


class CustomScriptsResource(ResourceBase):
    """An API client wrapper for interacting with the Custom Scripts endpoint.

    Attributes:
        client (ApiClient): An ApiClient object with an open Session

    Methods:
        list: Retrieve a list of all custom scripts
        get: Retrieve a single custom script by id
        create: Create a new custom script
        update: Update an existing custom script by id
        delete: Delete an existing custom script by id

    """

    _path = "/api/v1/library/custom-scripts"

    def list(self) -> PayloadList[CustomScriptPayload]:
        """Retrieve a list of all custom scripts.

        Returns:
            PayloadList: An object containing all combined results

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        all_results = PayloadList[CustomScriptPayload]()
        next_page = self._path
        while next_page:
            response = self.client.get(next_page)

            # Parse bytes content to CustomScriptPayloadList or raise ValidationError
            script_list = PayloadList[CustomScriptPayload].model_validate_json(response.content)

            all_results.count = script_list.count
            all_results.results.extend(script_list.results)

            next_page = script_list.next

        return all_results

    def get(self, id: str) -> CustomScriptPayload:
        """Retrieve details about a custom script.

        Args:
            id (str): The library item id of the script to retrieve

        Returns:
            CustomScriptPayload: A parsed object from the response

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        response = self.client.get(f"{self._path}/{id}")
        return CustomScriptPayload.model_validate_json(response.content)

    def create(
        self,
        name: str,
        script: str,
        remediation_script: str | None = None,
        active: bool = False,
        execution_frequency: ExecutionFrequency = ExecutionFrequency.ONCE,
        restart: bool = False,
        show_in_self_service: bool = False,
        self_service_category_id: str | None = None,
        self_service_recommended: bool = False,
    ) -> CustomScriptPayload:
        """Create custom scripts in Iru.

        Args:
            name (str): The name of the script
            script (str): The script content
            remediation_script (str): The remediation script content
            active (bool): Whether the script is active
            execution_frequency (ExecutionFrequency): The execution frequency of the script
            restart (bool): Whether the script should restart
            show_in_self_service (bool): Whether the script should show in self service
            self_service_category_id (str): The self service category id
            self_service_recommended (bool): Whether the script is recommended in self service

        Returns:
            CustomScriptPayload: A parsed object from the response

        Raises:
            ValueError: Raised when invalid parameters are passed
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        payload = {
            "name": name,
            "active": active,
            "script": script,
            "execution_frequency": str(execution_frequency),
            "restart": restart,
            "show_in_self_service": show_in_self_service,
        }
        if remediation_script is not None:
            payload["remediation_script"] = remediation_script

        if execution_frequency is ExecutionFrequency.NO_ENFORCEMENT and not show_in_self_service:
            raise ValueError(
                '"show_in_self_service" and "self_service_category_id" are required if execution_frequency is '
                f"NO_ENFORCEMENT. You can add the required keys to your script's info file.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
            )

        if show_in_self_service:
            if self_service_category_id is None:
                raise ValueError(
                    f"self_service_category_id is required if show_in_self_service is True.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
                )
            payload["self_service_category_id"] = self_service_category_id
            payload["self_service_recommended"] = self_service_recommended

        response = self.client.post(self._path, json=payload)

        return CustomScriptPayload.model_validate_json(response.content)

    def update(
        self,
        id: str,
        name: str | None = None,
        script: str | None = None,
        remediation_script: str | None = None,
        active: bool | None = None,
        execution_frequency: ExecutionFrequency | None = None,
        restart: bool = False,
        show_in_self_service: bool | None = None,
        self_service_category_id: str | None = None,
        self_service_recommended: bool = False,
    ) -> CustomScriptPayload:
        """Update custom scripts in Iru.

        Args:
            id (str): The id of the script to update
            name (str): The name of the script
            script (str): The script content
            remediation_script (str): The remediation script content
            active (bool): Whether the script is active
            execution_frequency (ExecutionFrequency): The execution frequency of the script
            restart (bool): Whether the script should restart
            show_in_self_service (bool): Whether the script should show in self service
            self_service_category_id (str): The self service category id
            self_service_recommended (bool): Whether the script is recommended in self service

        Returns:
            CustomScriptPayload: A parsed object from the response

        Raises:
            ValueError: Raised when invalid parameters are passed
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        payload = {
            "name": name,
            "active": active,
            "script": script,
            "remediation_script": remediation_script,
            "execution_frequency": None if execution_frequency is None else str(execution_frequency),
            "restart": restart,
            "show_in_self_service": show_in_self_service,
        }

        if execution_frequency is ExecutionFrequency.NO_ENFORCEMENT and not show_in_self_service:
            raise ValueError(
                '"show_in_self_service" and "self_service_category_id" are required if execution_frequency is '
                f"NO_ENFORCEMENT. You can add the required keys to your script's info file.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
            )

        if show_in_self_service:
            if self_service_category_id is None:
                raise ValueError(
                    f"self_service_category_id is required if show_in_self_service is True.\n\n{SHOW_IN_SELF_SERVICE_EXAMPLE}"
                )
            payload["self_service_category_id"] = self_service_category_id
            payload["self_service_recommended"] = self_service_recommended

        payload = {k: v for k, v in payload.items() if v is not None}
        response = self.client.patch(f"{self._path}/{id}", json=payload)

        return CustomScriptPayload.model_validate_json(response.content)

    def delete(self, id: str) -> None:
        """Delete specified custom script in Iru.

        Args:
            id (str): The id of the script to delete

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails

        """

        self.client.delete(f"{self._path}/{id}")
