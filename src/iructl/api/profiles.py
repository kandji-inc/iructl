from io import BufferedReader
from pathlib import Path

from .payload import CustomProfilePayload, PayloadList
from .resource_base import ResourceBase


class CustomProfilesResource(ResourceBase):
    """An API client wrapper for interacting with the Custom Profiles endpoint.

    Attributes:
        client (ApiClient): An ApiClient object with an open Session

    Methods:
        list: Retrieve a list of all custom profile(s)
        get: Retrieve a single custom profile by id
        create: Create a new custom profile
        update: Update an existing custom profile by id
        delete: Delete an existing custom profile by id

    """

    _path = "/api/v1/library/custom-profiles"

    def list(self) -> PayloadList[CustomProfilePayload]:
        """Retrieve a list of all custom profile(s).

        Returns:
            PayloadList: An object containing all combined results

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        all_results = PayloadList[CustomProfilePayload]()
        next_page = self._path
        while next_page:
            response = self.client.get(next_page)

            # Parse bytes content to CustomProfilePayloadList or raise ValidationError
            profile_list = PayloadList[CustomProfilePayload].model_validate_json(response.content)

            all_results.count = profile_list.count
            all_results.results.extend(profile_list.results)

            next_page = profile_list.next

        return all_results

    def get(self, id: str) -> CustomProfilePayload:
        """Retrieve details about a custom profile.

        Args:
            id (str): The library item id of the profile to retrieve

        Returns:
            CustomProfilePayload: A parsed object from the response

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        response = self.client.get(f"{self._path}/{id}")
        return CustomProfilePayload.model_validate_json(response.content)

    def create(
        self,
        name: str,
        file: Path | BufferedReader,
        active: bool = False,
        runs_on_mac: bool | None = None,
        runs_on_iphone: bool | None = None,
        runs_on_ipad: bool | None = None,
        runs_on_tv: bool | None = None,
        runs_on_vision: bool | None = None,
    ) -> CustomProfilePayload:
        """Create custom profiles in Iru.

        Args:
            name (str): The name for the new profile
            file (Path | BufferedReader): Path or open Buffer to the mobileconfig file to upload
            active (bool): Whether the profile is active or not
            runs_on_mac (bool): Whether the profile runs on macOS
            runs_on_iphone (bool): Whether the profile runs on iPhone
            runs_on_ipad (bool): Whether the profile runs on iPad
            runs_on_tv (bool): Whether the profile runs on Apple TV
            runs_on_vision (bool): Whether the profile runs on Apple TV

        Returns:
            CustomProfilePayload: A parsed object from the response

        Raises:
            ValueError: Raised when invalid parameters are passed
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        runs_on_args = {
            "runs_on_mac": runs_on_mac,
            "runs_on_iphone": runs_on_iphone,
            "runs_on_ipad": runs_on_ipad,
            "runs_on_tv": runs_on_tv,
            "runs_on_vision": runs_on_vision,
        }
        if not any(runs_on_args.values()):
            raise ValueError("At least one runs_on_* argument must be True.")

        close_file = False
        if isinstance(file, Path):
            if not file.is_file():
                raise FileNotFoundError(f"The file {file} does not exist or is not readable")
            file_obj = file.open("rb")
            file_name = file.name
            close_file = True
        elif isinstance(file, BufferedReader):
            file_obj = file
            file_name = f"{name}.mobileconfig"
        else:
            raise ValueError("Invalid file type provided. Must be a Path or BufferedReader object.")

        files = [("file", (file_name, file_obj, "application/octet-stream"))]
        payload = {"name": name, "active": active} | {k: v for k, v in runs_on_args.items() if v is not None}
        response = self.client.post(self._path, data=payload, files=files)

        if close_file:
            file_obj.close()

        return CustomProfilePayload.model_validate_json(response.content)

    def update(
        self,
        id: str,
        name: str | None = None,
        file: Path | BufferedReader | None = None,
        active: bool = False,
        runs_on_mac: bool | None = None,
        runs_on_iphone: bool | None = None,
        runs_on_ipad: bool | None = None,
        runs_on_tv: bool | None = None,
        runs_on_vision: bool | None = None,
    ) -> CustomProfilePayload:
        """Update specified custom profile in Iru with contents from provided .mobileconfig file in the specified directory.

        Args:
            id (str): The id of the profile to update
            name (str): The name for the profile
            file (Path | BufferedReader): Path or open Buffer to the mobileconfig file to upload
            active (bool): Whether the profile is active or not
            runs_on_mac (bool): Whether the profile runs on macOS
            runs_on_iphone (bool): Whether the profile runs on iPhone
            runs_on_ipad (bool): Whether the profile runs on iPad
            runs_on_tv (bool): Whether the profile runs on Apple TV
            runs_on_vision (bool): Whether the profile runs on Apple TV

        Returns:
            CustomProfilePayload: A parsed object from the response

        Raises:
            ValueError: Raised when invalid parameters are passed
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        payload = {
            k: v
            for k, v in {
                "name": name,
                "active": active,
                "runs_on_mac": runs_on_mac,
                "runs_on_iphone": runs_on_iphone,
                "runs_on_ipad": runs_on_ipad,
                "runs_on_tv": runs_on_tv,
                "runs_on_vision": runs_on_vision,
            }.items()
            if v is not None
        }

        close_file = False
        if file is not None:
            if isinstance(file, Path):
                if not file.is_file():
                    raise FileNotFoundError(f"The file {file} does not exist or is not readable")
                file_obj = file.open("rb")
                file_name = file.name
                close_file = True
            elif isinstance(file, BufferedReader):
                file_obj = file
                file_name = f"{name if name is not None else id}.mobileconfig"
            else:
                raise ValueError("Invalid file type provided. Must be a Path or BufferedReader object.")
            files = [("file", (file_name, file_obj, "application/octet-stream"))] if file else None
        else:
            files = None
            file_obj = None

        response = self.client.patch(f"{self._path}/{id}", data=payload, files=files)

        if close_file and file_obj is not None:
            file_obj.close()

        return CustomProfilePayload.model_validate_json(response.content)

    def delete(self, id: str) -> None:
        """Delete specified custom profile in Iru.

        Args:
            id (str): The id of the profile to delete

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails

        """

        self.client.delete(f"{self._path}/{id}")
