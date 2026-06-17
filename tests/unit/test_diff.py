import pytest

from iructl._diff import ChangeType, three_way_diff


@pytest.mark.parametrize(
    ("base", "local", "remote", "expected"),
    [
        pytest.param(1, 1, 1, ChangeType.NONE, id="no_change"),
        pytest.param(1, 2, 2, ChangeType.NONE, id="no_change_out_of_sync"),
        pytest.param(1, None, 1, ChangeType.CREATE_REMOTE, id="create_remote"),
        pytest.param(1, None, 2, ChangeType.CREATE_REMOTE, id="create_remote_out_of_sync"),
        pytest.param(2, 2, 1, ChangeType.UPDATE_REMOTE, id="update_remote"),
        pytest.param(1, 1, None, ChangeType.CREATE_LOCAL, id="create_local"),
        pytest.param(1, 2, None, ChangeType.CREATE_LOCAL, id="create_local_out_of_sync"),
        pytest.param(1, 2, 1, ChangeType.UPDATE_LOCAL, id="update_local"),
        pytest.param(None, 1, 1, ChangeType.NONE, id="no_change_no_base"),
        pytest.param(1, 2, 3, ChangeType.CONFLICT, id="conflict_with_base"),
        pytest.param(None, 1, 2, ChangeType.CONFLICT, id="conflict_no_base"),
    ],
)
def test_three_way_diff(base, local, remote, expected):
    assert three_way_diff(base=base, local=local, remote=remote) == expected
