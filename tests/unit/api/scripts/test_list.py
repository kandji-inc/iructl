import pytest

from iructl.api import CustomScriptPayload, PayloadList


@pytest.mark.allow_http
@pytest.mark.script_count(2)
def test_list_live(scripts_resource, ensure_script_resources):
    scripts = scripts_resource.list()
    assert isinstance(scripts, PayloadList)
    assert all(isinstance(script, CustomScriptPayload) for script in scripts.results)
    assert set(ensure_script_resources).issubset({script.id for script in scripts.results})


@pytest.mark.script_count(5)
def test_list(patched_scripts_resource):
    scripts = patched_scripts_resource.list()
    assert isinstance(scripts, PayloadList)
    assert all(isinstance(script, CustomScriptPayload) for script in scripts.results)
    assert scripts.count == len(scripts.results) == 5
    assert patched_scripts_resource.called_counter["list"] == 1


@pytest.mark.script_count(0)
def test_list_zero(patched_scripts_resource):
    scripts = patched_scripts_resource.list()
    assert isinstance(scripts, PayloadList)
    assert all(isinstance(script, CustomScriptPayload) for script in scripts.results)
    assert scripts.count == len(scripts.results) == 0
    assert patched_scripts_resource.called_counter["list"] == 1


@pytest.mark.script_count(17)
def test_list_with_pagination(patched_scripts_resource):
    scripts = patched_scripts_resource.list()
    assert isinstance(scripts, PayloadList)
    assert all(isinstance(script, CustomScriptPayload) for script in scripts.results)
    assert scripts.count == len(scripts.results) == 17
    assert patched_scripts_resource.called_counter["list"] == 4
