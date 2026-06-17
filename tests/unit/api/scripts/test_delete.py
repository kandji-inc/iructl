from uuid import uuid4

import pytest
from requests import HTTPError


@pytest.mark.allow_http
@pytest.mark.script_count(1)
def test_delete_live(scripts_resource, ensure_script_resources):
    script_id = ensure_script_resources[0]
    scripts_resource.delete(id=script_id)
    ensure_script_resources.pop(0)


@pytest.mark.script_count(1)
def test_delete_successful(patched_scripts_resource, remote_scripts):
    script_id = next(iter(remote_scripts.keys()))
    patched_scripts_resource.delete(id=script_id)
    assert patched_scripts_resource.called_counter["delete"] == 1
    assert script_id not in remote_scripts


def test_update_missing(patched_scripts_resource):
    with pytest.raises(HTTPError):
        patched_scripts_resource.delete(id=str(uuid4()))
