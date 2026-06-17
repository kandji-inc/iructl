import json
import re

import pytest
from typer.testing import CliRunner

from iructl import app
from iructl._diff import ChangeType
from iructl.repository import BlueprintAssignment, Repository
from tests.fixtures.blueprints import (
    BLUEPRINT_A,
    BLUEPRINT_A_NAME,
    BLUEPRINT_NOT_FOUND_BODY,
    DUPLICATE_BODY,
    MISSING_BLUEPRINT,
    NODE_A,
)

runner = CliRunner()


def test_push_assigns_declared_blueprint_when_preview_on(
    member_case, patch_blueprints_assign, declare_blueprints, report_file
):
    member = member_case.changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A, node=NODE_A)])
    calls = patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, ["--preview", member_case.noun, "push", "--all"])

    assert result.exit_code == 0
    assert "Library Item changes" in result.stdout
    assert "Blueprint assignments" in result.stdout
    assert "Assignment Summary" in result.stdout
    assert BLUEPRINT_A[:8] in result.stdout
    assert NODE_A[:8] in result.stdout
    assert "assigned" in result.stdout
    assert len(calls) == 1
    assert calls[0]["blueprint"] == BLUEPRINT_A
    assert calls[0]["node"] == NODE_A

    report = json.loads(report_file.read_text())[0]
    assert report["status"] != "partial"
    outcomes = [outcome for entry in report["success"] for outcome in entry.get("blueprint_outcomes", [])]
    assert any(o["blueprint_id"] == BLUEPRINT_A and o["status"] == "assigned" for o in outcomes)


def test_push_reports_partial_when_a_blueprint_fails(
    member_case, patch_blueprints_assign, declare_blueprints, report_file, make_http_error
):
    member = member_case.changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(
        member,
        [BlueprintAssignment(blueprint=BLUEPRINT_A), BlueprintAssignment(blueprint=MISSING_BLUEPRINT)],
    )
    patch_blueprints_assign({BLUEPRINT_A: None, MISSING_BLUEPRINT: make_http_error(404, BLUEPRINT_NOT_FOUND_BODY)})

    result = runner.invoke(app, ["--preview", member_case.noun, "push", "--all"])

    assert result.exit_code == 1
    assert "assigned" in result.stdout
    assert "failed" in result.stdout
    assert "[404]" in result.stdout

    report = json.loads(report_file.read_text())[0]
    assert report["status"] == "partial"
    outcomes = [outcome for entry in report["partial"] for outcome in entry["blueprint_outcomes"]]
    assert {o["status"] for o in outcomes} == {"assigned", "failed"}


def test_push_records_skip_for_already_assigned_blueprint(
    member_case, patch_blueprints_assign, declare_blueprints, report_file, make_http_error
):
    member = member_case.changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A)])
    patch_blueprints_assign({BLUEPRINT_A: make_http_error(400, DUPLICATE_BODY)})

    result = runner.invoke(app, ["--preview", member_case.noun, "push", "--all"])

    assert result.exit_code == 0
    assert "Skipped Assignment Summary" in result.stdout
    assert "Already Assigned" in result.stdout

    report = json.loads(report_file.read_text())[0]
    outcomes = [outcome for entry in report["success"] for outcome in entry.get("blueprint_outcomes", [])]
    assert any(o["blueprint_id"] == BLUEPRINT_A and o["status"] == "skipped" for o in outcomes)


def test_push_warns_and_skips_assignment_when_preview_off(
    member_case, patch_blueprints_assign, declare_blueprints, report_file
):
    member = member_case.changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A)])
    calls = patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, [member_case.noun, "push", "--all"])

    assert result.exit_code == 0
    assert "Blueprint preview disabled" in result.stderr
    assert calls == []
    assert "Blueprint assignments" not in result.stdout

    report = json.loads(report_file.read_text())[0]
    assert report["status"] != "partial"
    assert all("blueprint_outcomes" not in entry for entry in report["success"])


@pytest.mark.usefixtures("report_file")
def test_push_does_not_warn_for_empty_ensure_blueprints_when_preview_off(
    member_case, patch_blueprints_assign, declare_blueprints
):
    member = member_case.changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [])
    calls = patch_blueprints_assign()

    result = runner.invoke(app, [member_case.noun, "push", "--all"])

    assert result.exit_code == 0
    assert "Blueprint preview disabled" not in result.stderr
    assert calls == []


def test_sync_reports_partial_on_push_half_when_preview_on(
    member_case, patch_blueprints_assign, declare_blueprints, report_file, make_http_error
):
    member = member_case.changes[ChangeType.UPDATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=MISSING_BLUEPRINT)])
    patch_blueprints_assign({MISSING_BLUEPRINT: make_http_error(404, BLUEPRINT_NOT_FOUND_BODY)})

    result = runner.invoke(app, ["--preview", member_case.noun, "sync", "--id", member.id])

    assert result.exit_code == 1
    assert "Pushed Item Summary" in result.stdout
    # Columns are Success | Partial | Failure; the content update succeeded but its blueprint failed.
    assert re.search(r"Updated\s+0\s+1\s+0", result.stdout)
    assert "Sync operation complete!" in result.stdout
    assert json.loads(report_file.read_text())[0]["status"] == "partial"


@pytest.mark.parametrize(
    "preview_args",
    [pytest.param([], id="preview-off"), pytest.param(["--preview"], id="preview-on")],
)
@pytest.mark.usefixtures("report_file")
def test_pull_never_assigns_or_warns(member_case, patch_blueprints_assign, declare_blueprints, preview_args):
    member = member_case.changes[ChangeType.UPDATE_REMOTE][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A)])
    calls = patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, [*preview_args, member_case.noun, "pull", "--all"])

    assert result.exit_code == 0
    assert calls == []
    assert "Blueprint preview disabled" not in result.stderr
    assert "Blueprint assignments" not in result.stdout
    reloaded = Repository.load_path(model=member_case.model)[member.id]
    assert reloaded.info.ensure_blueprints == [BlueprintAssignment(blueprint=BLUEPRINT_A)]


def test_push_marks_unchanged_item_for_retarget_when_blueprint_fails(
    member_case, patch_blueprints_assign, declare_blueprints, report_file, make_http_error
):
    # A content-unchanged item with a declared blueprint is reconciled via the separate
    # _reconcile_unchanged path (SKIP action); a failed assignment there resolves to PARTIAL
    # and surfaces as "Needs re-target" rather than "Already up to date".
    member = member_case.changes[ChangeType.NONE][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=MISSING_BLUEPRINT)])
    patch_blueprints_assign({MISSING_BLUEPRINT: make_http_error(404, BLUEPRINT_NOT_FOUND_BODY)})

    result = runner.invoke(app, ["--preview", member_case.noun, "push", "--id", member.id])

    assert result.exit_code == 1
    assert re.search(r"Needs re-target\s+1", result.stdout)
    assert "failed" in result.stdout
    report = json.loads(report_file.read_text())[0]
    assert report["status"] == "partial"
    outcomes = [outcome for entry in report["partial"] for outcome in entry["blueprint_outcomes"]]
    assert any(o["blueprint_id"] == MISSING_BLUEPRINT and o["status"] == "failed" for o in outcomes)


# Preview gating and name resolution are type-agnostic; apps stands in for all member types here.
@pytest.mark.parametrize(
    ("cli_args", "env"),
    [
        pytest.param(["app", "push", "--all"], {"IRUCTL_PREVIEW": "1"}, id="env-var-enables"),
        pytest.param(["--preview", "app", "push", "--all"], {"IRUCTL_PREVIEW": "0"}, id="cli-flag-overrides-env"),
    ],
)
@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd", "report_file")
def test_preview_enabled_by_env_var_or_cli_flag(apps_lrc, patch_blueprints_assign, declare_blueprints, cli_args, env):
    _, _, changes = apps_lrc
    member = changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A)])
    calls = patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, cli_args, env=env)

    assert result.exit_code == 0
    assert "Blueprint assignments" in result.stdout
    assert len(calls) == 1


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_push_resolves_blueprint_name_to_id(
    apps_lrc, patch_blueprints_list, patch_blueprints_assign, declare_blueprints, report_file
):
    patch_blueprints_list({BLUEPRINT_A_NAME: BLUEPRINT_A})
    _, _, changes = apps_lrc
    member = changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A_NAME, node=NODE_A)])
    calls = patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, ["--preview", "app", "push", "--all"])

    assert result.exit_code == 0
    assert BLUEPRINT_A_NAME in result.stdout
    assert calls[0]["blueprint"] == BLUEPRINT_A

    report = json.loads(report_file.read_text())[0]
    assert report["status"] != "partial"
    outcomes = [outcome for entry in report["success"] for outcome in entry.get("blueprint_outcomes", [])]
    assert any(
        o["blueprint_id"] == BLUEPRINT_A and o["blueprint_name"] == BLUEPRINT_A_NAME and o["status"] == "assigned"
        for o in outcomes
    )


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_push_displays_blueprint_name_for_uuid_assignment(
    apps_lrc, patch_blueprints_list, patch_blueprints_assign, declare_blueprints, report_file
):
    patch_blueprints_list({BLUEPRINT_A_NAME: BLUEPRINT_A})
    _, _, changes = apps_lrc
    member = changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint=BLUEPRINT_A, node=NODE_A)])
    patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, ["--preview", "app", "push", "--all"])

    assert result.exit_code == 0
    assert BLUEPRINT_A_NAME in result.stdout

    report = json.loads(report_file.read_text())[0]
    outcomes = [outcome for entry in report["success"] for outcome in entry.get("blueprint_outcomes", [])]
    assert any(o["blueprint_id"] == BLUEPRINT_A and o["blueprint_name"] == BLUEPRINT_A_NAME for o in outcomes)


@pytest.mark.usefixtures("patch_apps_endpoints", "iructl_repo_cd")
def test_push_fails_for_unresolvable_blueprint_name(
    apps_lrc, patch_blueprints_list, patch_blueprints_assign, declare_blueprints, report_file
):
    patch_blueprints_list({})
    _, _, changes = apps_lrc
    member = changes[ChangeType.CREATE_LOCAL][0][0]
    declare_blueprints(member, [BlueprintAssignment(blueprint="Ghost Blueprint")])
    calls = patch_blueprints_assign({BLUEPRINT_A: None})

    result = runner.invoke(app, ["--preview", "app", "push", "--all"])

    assert result.exit_code == 1
    assert calls == []

    report = json.loads(report_file.read_text())[0]
    assert report["status"] == "partial"
    outcomes = [outcome for entry in report["partial"] for outcome in entry["blueprint_outcomes"]]
    assert any(
        o["status"] == "failed" and o["blueprint_id"] == "Ghost Blueprint" and "Unable to get ID" in o["error_message"]
        for o in outcomes
    )
