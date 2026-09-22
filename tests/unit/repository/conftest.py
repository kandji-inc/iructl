import pytest
from rich.table import Table

from tests.fixtures.apps import app_info_data_factory, custom_app_factory
from tests.fixtures.profiles import (
    custom_profile_factory,
    custom_profile_obj,
    mobileconfig_content_factory,
    mobileconfig_data_factory,
    profile_info_data_factory,
)
from tests.fixtures.scripts import custom_script_factory, script_content, script_info_data_factory


@pytest.fixture
def table_rows():
    """Return a helper extracting (label, value) pairs from a two-column detail Table.

    Reaches into Rich's private cell storage; Rich exposes no public row accessor.
    """

    def _rows(table: Table) -> list[tuple[str, object]]:
        labels, values = (column._cells for column in table.columns)
        return list(zip(labels, values, strict=True))

    return _rows
