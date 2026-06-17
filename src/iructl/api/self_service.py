from pydantic import TypeAdapter

from .payload import SelfServiceCategoryPayload
from .resource_base import ResourceBase


class SelfServiceCategoriesResource(ResourceBase):
    """An API client wrapper for interacting with the Self Service Categories endpoint

    Attributes:
        client (ApiClient): An ApiClient object with an open Session

    Methods:
        list: Retrieve a list of all self service categories

    """

    _path: str = "/api/v1/self-service/categories"

    def list(self) -> list[SelfServiceCategoryPayload]:
        """Retrieve a list of all self service categories.

        Returns:
            list[SelfServiceCategoryPayload]: A list containing all result objects

        Raises:
            ApiClientError: An http error occurred in the API client
            HTTPError: Raised when the HTTP response status is unsuccessful
            ConnectionError: Raised when the connection to the API fails
            ValidationError: Raised when the response does not match the expected schema

        """

        response = self.client.get(self._path)
        return TypeAdapter(list[SelfServiceCategoryPayload]).validate_json(response.content)
