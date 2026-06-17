from uuid import uuid4

import pytest
from requests import HTTPError

from iructl.api import CustomScriptPayload
from iructl.repository import ExecutionFrequency


@pytest.mark.allow_http
@pytest.mark.script_count(1)
def test_update_live(scripts_resource, ensure_script_resources):
    script_id = ensure_script_resources[0]
    script_info = {
        "name": "Test Script 2",
        "script": 'echo "Goodbye, World!"',
    }
    script = scripts_resource.update(id=script_id, **script_info)
    assert isinstance(script, CustomScriptPayload)
    assert script.name == script_info["name"]
    assert script.script == script_info["script"]


@pytest.mark.parametrize(
    "script_info",
    [
        {
            "name": "Test Script",
            "execution_frequency": ExecutionFrequency.ONCE,
            "script": "echo 'Hello, World!'",
            "show_in_self_service": False,
        },
        {
            "name": "Test Script",
            "execution_frequency": ExecutionFrequency.NO_ENFORCEMENT,
            "script": "echo 'Hello, World!'",
            "show_in_self_service": True,
            "self_service_category_id": str(uuid4()),
            "self_service_recommended": False,
        },
        {
            "name": "Test Script",
            "execution_frequency": ExecutionFrequency.EVERY_DAY,
            "script": "echo 'Hello, World!'",
            "remediation_script": "echo 'Goodbye, World!'",
            "active": True,
            "restart": True,
        },
    ],
)
@pytest.mark.script_count(1)
def test_update_successful(patched_scripts_resource, remote_scripts, script_info):
    script_id = next(iter(remote_scripts.keys()))
    script = patched_scripts_resource.update(id=script_id, **script_info)
    assert isinstance(script, CustomScriptPayload)
    for key, value in script_info.items():
        assert getattr(script, key) == value


@pytest.mark.parametrize(
    ("script_info", "exception_msg"),
    [
        pytest.param(
            {
                "name": "Test Script",
                "execution_frequency": ExecutionFrequency.ONCE,
                "script": "echo 'Hello, World!'",
                "show_in_self_service": True,
            },
            "self_service_category_id is required",
            id="no_category_id",
        ),
        pytest.param(
            {
                "name": "Test Script",
                "execution_frequency": ExecutionFrequency.NO_ENFORCEMENT,
                "script": "echo 'Hello, World!'",
                "show_in_self_service": False,
            },
            '"show_in_self_service" and "self_service_category_id" are required',
            id="no_self_service",
        ),
    ],
)
def test_update_value_error(patched_scripts_resource, script_info, exception_msg):
    with pytest.raises(ValueError, match=exception_msg):
        patched_scripts_resource.create(**script_info)


def test_update_missing(patched_scripts_resource):
    with pytest.raises(HTTPError):
        patched_scripts_resource.update(id=str(uuid4()), name="Test Script 2")
