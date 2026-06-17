from uuid import uuid4

import pytest

from iructl.api import CustomScriptPayload
from iructl.repository import ExecutionFrequency


@pytest.mark.allow_http
@pytest.mark.script_count(0)
def test_create_live(scripts_resource, ensure_script_resources):
    script_info = {
        "name": "Test Script",
        "execution_frequency": ExecutionFrequency.ONCE,
        "script": "echo 'Hello, World!'",
        "show_in_self_service": False,
    }
    script = scripts_resource.create(**script_info)
    ensure_script_resources.append(script.id)
    assert isinstance(script, CustomScriptPayload)
    assert script.name == script_info["name"]
    assert script.execution_frequency == script_info["execution_frequency"]
    assert script.script == script_info["script"]
    assert script.show_in_self_service == script_info["show_in_self_service"]


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
def test_create_successful(patched_scripts_resource, script_info):
    script = patched_scripts_resource.create(**script_info)
    assert isinstance(script, CustomScriptPayload)
    for key, value in script_info.items():
        assert getattr(script, key) == value
    assert patched_scripts_resource.called_counter["create"] == 1


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
def test_create_value_error(patched_scripts_resource, script_info, exception_msg):
    with pytest.raises(ValueError, match=exception_msg):
        patched_scripts_resource.create(**script_info)
    assert patched_scripts_resource.called_counter["create"] == 0
