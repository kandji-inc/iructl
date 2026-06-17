import requests
from pydantic import TypeAdapter

from .payload import BlueprintPayload, PayloadList
from .resource_base import ResourceBase

_assign_response_adapter = TypeAdapter(list[str])

# Plain-text 400 bodies that mean the (library item, blueprint, node) pair is already in the desired state.
_DUPLICATE_ASSIGNMENT_MARKERS = {
    "Library Item already exists in Assignment Node",
    "Assignment node can only have one of this Library Item type.",
}


def is_duplicate_assignment(response: requests.Response) -> bool:
    """Return True if a response reports an assignment that already exists.

    A duplicate assignment is an HTTP 400 whose plain-text body matches one of
    the known markers. It signals the (library item, blueprint, node) pair is
    already in the desired state, so callers treat it as a skip rather than an
    error.

    Args:
        response (requests.Response): The response to inspect.

    Returns:
        bool: True if the response reports an already-present assignment.

    """
    return response.status_code == 400 and any(marker in response.text for marker in _DUPLICATE_ASSIGNMENT_MARKERS)


class BlueprintsResource(ResourceBase):
    """An API client wrapper for interacting with the Blueprints endpoint.

    Attributes:
        client (ApiClient): An ApiClient object with an open Session

    Methods:
        assign: Assign a Library Item to a Blueprint
        list: Retrieve a list of all blueprints

    """

    _path = "/api/v1/blueprints"

    def assign(
        self,
        blueprint: str,
        *,
        library_item_id: str,
        node: str | None = None,
    ) -> list[str]:
        """Assign a Library Item to a Blueprint.

        Args:
            blueprint (str): UUID of the Blueprint to assign to.
            library_item_id (str): UUID of the Library Item to assign.
            node (str | None): UUID of the Assignment Map node to assign at.
                Omitting the node parameter assigns to the root node.

        Returns:
            list[str]: UUIDs of every Library Item currently assigned to
                the blueprint after the operation.

        Raises:
            ApiClientError: Raised if the ApiClient has not been opened.
            HTTPError: Raised when Iru returns a non-2xx response.
                Callers classify the body (e.g. duplicate-assignment 400)
                themselves.
            ConnectionError: Raised when the API connection fails.
            ValidationError: Raised when Iru's response body does not
                match `list[str]`.

        """
        body: dict[str, str] = {"library_item_id": library_item_id}
        if node:
            body["assignment_node_id"] = node
        response = self.client.post(
            f"{self._path}/{blueprint}/assign-library-item",
            json=body,
            anticipated_error=is_duplicate_assignment,
        )
        return _assign_response_adapter.validate_json(response.content)

    def list(self) -> PayloadList[BlueprintPayload]:
        """Retrieve a list of all blueprints.

        Returns:
            PayloadList: An object containing all combined results

        Raises:
            ApiClientError: Raised if a ApiClient has not been opened
            HTTPError: Raised when the HTTP request returns an unsuccessful status code
            ConnectionError: Raised when the API connection fails
            ValidationError: Raised when the response does not match the expected schema

        """

        all_results = PayloadList[BlueprintPayload]()
        next_page = self._path
        while next_page:
            response = self.client.get(next_page)

            blueprint_list = PayloadList[BlueprintPayload].model_validate_json(response.content)

            all_results.count = blueprint_list.count
            all_results.results.extend(blueprint_list.results)

            next_page = blueprint_list.next

        return all_results
