from uuid import uuid4

import pytest
from requests import HTTPError

from iructl.api import CustomScriptPayload


@pytest.mark.allow_http
@pytest.mark.script_count(1)
def test_get_live(scripts_resource, ensure_script_resources):
    script_id = next(iter(ensure_script_resources))
    script = scripts_resource.get(id=script_id)
    assert isinstance(script, CustomScriptPayload)
    assert script.id == script_id


@pytest.mark.script_count(1)
def test_get(patched_scripts_resource, remote_scripts):
    script_id = next(iter(remote_scripts))
    script = patched_scripts_resource.get(id=script_id)
    assert isinstance(script, CustomScriptPayload)
    assert script.id == script_id


@pytest.mark.script_count(0)
def test_get_missing(patched_scripts_resource):
    with pytest.raises(HTTPError):
        patched_scripts_resource.get(id=str(uuid4()))
